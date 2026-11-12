import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Query, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, field_validator
import re
from .models import PushSetting

from .config import COMMODITIES, COMMODITY_MAP, ROOT, TIMEZONE
from .db import initialize_database
from .service import FetchBusy, FetchService, latest_prices, price_history
from .alerts import AlertService, RuleInput, channel_status
from .auth import router as auth_router, current_user, admin_user, resolve_user
from . import auth

logger = logging.getLogger(__name__)
service = FetchService()
alerts = AlertService()


def scheduled_alerts():
    try:
        alerts.run()
    except Exception:
        logger.error("提醒检查失败，请检查数据库；下个调度周期继续")


def scheduled_fetch():
    try:
        service.run()
    except FetchBusy:
        logger.info("已有抓取任务执行中，跳过本次定时任务")


@asynccontextmanager
async def lifespan(app: FastAPI):
    initialize_database()
    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(scheduled_fetch, CronTrigger(hour=15, minute=30, timezone=TIMEZONE),
                      id="daily_prices", max_instances=1, coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(scheduled_alerts, "interval", minutes=5, id="alerts", max_instances=1, coalesce=True)
    scheduler.start()
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="采购行情网站", version="0.5.0", lifespan=lifespan)
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
        expected = f"{request.url.scheme}://{request.url.netloc}"
        if (origin and origin != expected) or request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "拒绝跨站写请求"}, status_code=403)
        if request.headers.get("content-type", "").split(";")[0] != "application/json" and request.headers.get("content-length", "0") != "0":
            return JSONResponse({"detail": "写请求须使用 JSON"}, status_code=415)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/commodities")
def commodities():
    return {"items": [vars(item) for item in COMMODITIES]}


@app.get("/api/prices/latest")
def latest():
    return {"items": latest_prices()}


@app.get("/api/prices/history")
def history(code: str, days: int = Query(30, ge=1, le=3650)):
    if code not in COMMODITY_MAP:
        raise HTTPException(status_code=404, detail="未知品种 code")
    return price_history(code, days)


@app.post("/api/fetch/run")
def fetch_run(user=Depends(admin_user)):
    try:
        return service.run()
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
def alert_logs(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100), user=Depends(current_user)):
    return alerts.logs(page, page_size, user_id=user.id)


@app.post("/api/alerts/test")
def test_alert(user=Depends(current_user)):
    return alerts.test(user_id=user.id)


@app.post("/api/alerts/check")
def check_alerts(user=Depends(current_user)):
    return alerts.run(user_id=user.id)


@app.get("/api/settings/telegram")
def telegram_status(user=Depends(admin_user)):
    return channel_status()


class EmailInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str

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
        return {"user_id": user.id, "email": setting.email if setting else "", "channel": alerts.status(user.id)}


@app.put("/api/me/push-settings")
def save_push_settings(data: EmailInput, user=Depends(current_user)):
    with auth.SessionLocal() as session:
        session.merge(PushSetting(user_id=user.id, email=data.email))
        session.commit()
    return push_settings(user)


# 哈希路由使详情页刷新仍请求根首页，API 路由优先于静态目录。
frontend_dist = ROOT / "frontend" / "dist"
if (frontend_dist / "index.html").is_file():
    @app.get("/login", include_in_schema=False)
    @app.get("/register", include_in_schema=False)
    def auth_page():
        return FileResponse(frontend_dist / "index.html")

    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
