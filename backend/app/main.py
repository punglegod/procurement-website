import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from .config import COMMODITIES, COMMODITY_MAP, ROOT, TIMEZONE
from .db import initialize_database
from .service import FetchBusy, FetchService, latest_prices, price_history
from .alerts import AlertService, RuleInput, channel_status

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


app = FastAPI(title="采购行情网站", version="0.3.0", lifespan=lifespan)


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
def fetch_run():
    try:
        return service.run()
    except FetchBusy:
        raise HTTPException(status_code=409, detail="抓取任务正在执行，请稍后查看来源状态") from None


@app.get("/api/sources/status")
def sources_status():
    return {"items": service.statuses()}


@app.get("/api/alerts/status")
def alert_status():
    return channel_status()


@app.get("/api/alerts/rules")
def alert_rules():
    return alerts.rules()


@app.post("/api/alerts/rules", status_code=201)
def create_alert_rule(data: RuleInput):
    return alerts.save(data)


@app.put("/api/alerts/rules/{rule_id}")
def update_alert_rule(rule_id: int, data: RuleInput):
    result = alerts.save(data, rule_id)
    if result is None:
        raise HTTPException(404, "提醒规则不存在")
    return result


@app.delete("/api/alerts/rules/{rule_id}")
def delete_alert_rule(rule_id: int):
    if not alerts.delete(rule_id):
        raise HTTPException(404, "提醒规则不存在")
    return {"deleted": rule_id}


@app.get("/api/alerts/logs")
def alert_logs(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100)):
    return alerts.logs(page, page_size)


@app.post("/api/alerts/test")
def test_alert():
    return alerts.test()


@app.post("/api/alerts/check")
def check_alerts():
    return alerts.run()


# 哈希路由使详情页刷新仍请求根首页，API 路由优先于静态目录。
frontend_dist = ROOT / "frontend" / "dist"
if (frontend_dist / "index.html").is_file():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
