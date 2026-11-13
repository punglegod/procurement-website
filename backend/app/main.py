import logging
import asyncio
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED, EVENT_JOB_MAX_INSTANCES
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
import re
from .models import PushSetting

from .config import COMMODITIES, COMMODITY_MAP, FETCH_SLOTS, ROOT, TIMEZONE, read_public_origin
from .db import initialize_database
from .service import FetchBusy, FetchService, latest_prices, price_history, quote_options
from .quotes import choose
from .alerts import AlertService, RuleInput, channel_status
from .auth import router as auth_router, current_user, admin_user, resolve_user
from . import auth
from .security import BodyLimitMiddleware
from .email_verification import available_user, clear_challenge, request_verification, confirm_verification
from .source_settings import SourceSettings, Sci99Input, progress_enabled, warn_encryption_configuration
from .notifications import NotificationService, digest_enabled
from .rounds import RoundRunner, batch_dict
from .models import FetchBatch, SiteSetting
from sqlalchemy import select

logger = logging.getLogger(__name__)
service = FetchService()
alerts = AlertService()


def scheduled_alerts():
    try:
        alerts.run()
    except Exception:
        logger.error("提醒检查失败，请检查数据库；下个调度周期继续")


def round_runner():
    # fixture/运维可注入服务；通知必须使用相同数据库及对应的隔离通道。
    channels = alerts if alerts.session_factory is service.session_factory else AlertService(service.session_factory)
    return RoundRunner(service, NotificationService(channels))


def scheduled_fetch(slot):
    try:
        round_runner().run(slot)
    except FetchBusy:
        logger.info("已有抓取任务执行中，跳过本次定时任务")
        round_runner().alarm(f"morning:{alerts.clock().date()}" if slot == "09:00" else f"busy:{slot}:{alerts.clock().date()}", f"{slot}轮因已有任务未启动")
        if slot == "09:00":
            round_runner().safe(lambda: round_runner().notifications.daily_digest(abnormal=True))
    except Exception:
        round_runner().alarm(f"morning:{alerts.clock().date()}" if slot == "09:00" else f"failed:{slot}:{alerts.clock().date()}", f"{slot}轮整体异常，请核对抓取日志")
        if slot == "09:00":
            round_runner().safe(lambda: round_runner().notifications.daily_digest(abnormal=True))


def monitor_morning():
    round_runner().safe(lambda: round_runner().monitor_morning())


@asynccontextmanager
async def lifespan(app: FastAPI):
    read_public_origin()  # 配置错误在接流量前明确失败，不静默退回请求头。
    initialize_database()
    warn_encryption_configuration()
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    # 固定Asia/Shanghai四轮；保留15:30，日报由09:00轮完成事件触发。
    for hour, minute in FETCH_SLOTS:
        slot = f"{hour:02}:{minute:02}"
        scheduler.add_job(scheduled_fetch, CronTrigger(hour=hour, minute=minute, timezone=TIMEZONE), args=[slot],
                          id="prices_" + slot.replace(":", ""), max_instances=1, coalesce=True, misfire_grace_time=120)
    scheduler.add_job(scheduled_alerts, "interval", minutes=5, id="alerts", max_instances=1, coalesce=True)
    scheduler.add_job(monitor_morning, "interval", seconds=60, id="morning_monitor", max_instances=1, coalesce=True)
    loop = asyncio.get_running_loop()

    def scheduler_event(event):
        # 监听器只提交后台工作，不能在调度器线程等待外部通道。
        def handle():
            runner = round_runner()
            runner.alarm(f"morning:{runner.clock().date()}" if event.job_id == "prices_0900" else f"scheduler:{event.job_id}:{runner.clock().date()}", f"任务 {event.job_id} 未正常执行，请检查调度状态")
            runner.safe(lambda: runner.monitor_morning())
        loop.call_soon_threadsafe(lambda: loop.create_task(asyncio.to_thread(handle)))

    scheduler.add_listener(scheduler_event, EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES)
    try:
        scheduler.start()
    except Exception:
        await asyncio.to_thread(lambda: round_runner().alarm("scheduler_start:" + str(alerts.clock().date()), "调度器启动失败"))
        raise
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="采购行情网站", version="0.10.0", lifespan=lifespan)
app.include_router(auth_router)


@app.middleware("http")
async def protect_site(request: Request, call_next):
    path = request.url.path
    public = path in {"/login", "/register", "/api/auth/login", "/api/auth/register"} or path.startswith("/assets/")
    # 登录壳页与其脚本是认证入口；行情、文档、健康检查等其余接口均要求会话。
    if not public:
        user = resolve_user(request)
        if user is None:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "请先登录"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        request.state.user = user
    # Cookie SameSite 外再校验写请求来源，拒绝跨站请求及非 JSON 表单。
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        try:
            expected = read_public_origin() or f"{request.url.scheme}://{request.url.netloc}"
        except ValueError:
            return JSONResponse({"detail": "站点公开来源配置无效，请联系管理员"}, status_code=503)
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站写请求"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json" and request.headers.get("content-length", "0") != "0":
            return JSONResponse({"detail": "写请求须使用 JSON"}, status_code=415)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# 最外层先限制正文，避免大 JSON 先被其他中间件或 Pydantic 全量读取。
app.add_middleware(BodyLimitMiddleware)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/commodities")
def commodities(user=Depends(current_user)):
    prices = {item["code"]: item for item in latest_prices(service.session_factory, user_id=user.id)}
    return {"items": [vars(item) | {key: prices[item.code][key] for key in ("source", "unit", "quote_basis")} for item in COMMODITIES]}


@app.get("/api/prices/latest")
def latest(user=Depends(current_user)):
    return {"items": latest_prices(service.session_factory, user_id=user.id)}


@app.get("/api/prices/history")
def history(code: str, days: int = Query(30, ge=1, le=3650), user=Depends(current_user)):
    if code not in COMMODITY_MAP:
        raise HTTPException(status_code=404, detail="未知品种 code")
    return price_history(code, days, service.session_factory, user_id=user.id)


@app.get("/api/quotes")
def quotes(code: str, user=Depends(current_user)):
    if code not in COMMODITY_MAP:
        raise HTTPException(404, "未知品种 code")
    return quote_options(code, user.id, service.session_factory)


class SelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_id: int = Field(gt=0)


@app.put("/api/me/quote-selections/{code}")
def save_quote_selection(code: str, data: SelectionInput, user=Depends(current_user)):
    if code not in COMMODITY_MAP:
        raise HTTPException(404, "未知品种 code")
    with service.session_factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        available_user(session, user.id)
        choose(session, user.id, code, data.entry_id)
        session.commit()
    return quote_options(code, user.id, service.session_factory)


@app.post("/api/fetch/run")
def fetch_run(user=Depends(admin_user)):
    try:
        return round_runner().run()
    except FetchBusy:
        raise HTTPException(status_code=409, detail="抓取任务正在执行，请稍后查看来源状态") from None


@app.get("/api/sources/status")
def sources_status(user=Depends(admin_user)):
    return {"items": service.statuses()}


@app.get("/api/alerts/status")
def alert_status(user=Depends(current_user)):
    return alerts.status(user.id)


@app.get("/api/alerts/rules")
def alert_rules(user=Depends(current_user)):
    return alerts.rules(user_id=user.id)


@app.post("/api/alerts/rules", status_code=201)
def create_alert_rule(data: RuleInput, user=Depends(current_user)):
    return alerts.save(data, user_id=user.id)


@app.put("/api/alerts/rules/{rule_id}")
def update_alert_rule(rule_id: int, data: RuleInput, user=Depends(current_user)):
    result = alerts.save(data, rule_id, user_id=user.id)
    if result is None:
        raise HTTPException(404, "提醒规则不存在")
    return result


@app.delete("/api/alerts/rules/{rule_id}")
def delete_alert_rule(rule_id: int, user=Depends(current_user)):
    if not alerts.delete(rule_id, user_id=user.id):
        raise HTTPException(404, "提醒规则不存在")
    return {"deleted": rule_id}


@app.get("/api/alerts/logs")
def alert_logs(page: int = Query(1, ge=1, le=100000), page_size: int = Query(30, ge=1, le=100), user=Depends(current_user)):
    return alerts.logs(page, page_size, user_id=user.id)


@app.post("/api/alerts/test")
def test_alert(request: Request, user=Depends(current_user)):
    return alerts.test(user_id=user.id, client_ip=request.client.host if request.client else "unknown")


@app.post("/api/alerts/check")
def check_alerts(user=Depends(current_user)):
    return alerts.run(user_id=user.id)


@app.get("/api/settings/telegram")
def telegram_status(user=Depends(admin_user)):
    return channel_status()


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    daily_digest_enabled: bool | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip()
        if value and (len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~\-]+@[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?)+", value)):
            raise ValueError("邮箱格式无效")
        return value


@app.get("/api/me/push-settings")
def push_settings(user=Depends(current_user)):
    with auth.SessionLocal() as session:
        setting = session.get(PushSetting, user.id)
        return {"user_id": user.id, "email": setting.email if setting else "",
                "daily_digest_enabled": digest_enabled(user, setting),
                "email_verified": bool(setting and setting.email_verified_at), "channel": alerts.status(user.id)}


@app.put("/api/me/push-settings")
def save_push_settings(data: EmailInput, user=Depends(current_user)):
    with auth.SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        available_user(session, user.id)
        setting = session.get(PushSetting, user.id)
        if setting is None:
            setting = PushSetting(user_id=user.id, email=data.email)
            session.add(setting)
        elif setting.email != data.email:
            setting.email = data.email
            setting.email_verified_at = None
            clear_challenge(setting)
        if data.daily_digest_enabled is not None:
            setting.daily_digest_enabled = data.daily_digest_enabled
        session.commit()
    return push_settings(user)


class VerificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=r"^[0-9]{8}$")


@app.post("/api/me/email-verification/request")
def send_email_verification(request: Request, user=Depends(current_user)):
    return request_verification(alerts, user.id, request.client.host if request.client else "unknown")


@app.post("/api/me/email-verification/confirm")
def confirm_email_verification(data: VerificationInput, request: Request, user=Depends(current_user)):
    confirm_verification(alerts, user.id, request.client.host if request.client else "unknown", data.code)
    return push_settings(user)


@app.exception_handler(RequestValidationError)
async def safe_validation_error(request, error):
    if request.url.path.startswith("/api/admin/sources/sci99"):
        # 验证错误默认含input，凭据表单禁止将原始输入回显或写进异常日志。
        return JSONResponse({"detail": "卓创配置格式无效，请检查字段和长度"}, status_code=422)
    return await request_validation_exception_handler(request, error)


@app.get("/api/admin/sources/sci99")
def sci99_settings(user=Depends(admin_user)):
    return SourceSettings(service.session_factory).state()


@app.put("/api/admin/sources/sci99")
def save_sci99_settings(data: Sci99Input, user=Depends(admin_user)):
    return SourceSettings(service.session_factory).save(data)


@app.post("/api/admin/sources/sci99/test")
def test_sci99_settings(request: Request, user=Depends(admin_user)):
    return SourceSettings(service.session_factory).test_connection(user.id, request.client.host if request.client else "unknown")


@app.get("/api/admin/fetch-settings")
def fetch_settings(user=Depends(admin_user)):
    with service.session_factory() as session:
        return {"progress_enabled": progress_enabled(session)}


class FetchSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    progress_enabled: bool


@app.put("/api/admin/fetch-settings")
def save_fetch_settings(data: FetchSettingsInput, user=Depends(admin_user)):
    with service.session_factory() as session:
        session.merge(SiteSetting(key="fetch_progress_enabled", value="true" if data.progress_enabled else "false"))
        session.commit()
    return fetch_settings(user)


@app.get("/api/admin/fetch-runs")
def fetch_runs(user=Depends(admin_user)):
    with service.session_factory() as session:
        return {"items": [batch_dict(batch) for batch in session.scalars(select(FetchBatch).order_by(FetchBatch.id.desc()).limit(20))]}


# 哈希路由使详情页刷新仍请求根首页，API 路由优先于静态目录。
frontend_dist = ROOT / "frontend" / "dist"
if (frontend_dist / "index.html").is_file():
    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    def auth_page():
        return FileResponse(frontend_dist / "index.html")

    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
