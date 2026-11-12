import logging
import threading
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from .adapters.common import AccessStopped, SourceError, now
from .adapters.free import FreeAdapter
from .adapters.sci99 import Sci99Adapter
from .config import COMMODITIES, COMMODITY_MAP, SOURCES, read_sci99_settings, sci99_configured
from .db import SessionLocal
from .models import PricePoint, SourceRun, SourceState

logger = logging.getLogger(__name__)
run_lock = threading.Lock()


class FetchBusy(Exception):
    pass


class FetchService:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory

    def set_state(self, source, status, message, *, success=False):
        with self.session_factory() as session:
            state = session.get(SourceState, source)
            state.status, state.message = status, message
            state.last_attempt_at = now().replace(tzinfo=None)
            if success:
                state.last_success_at = state.last_attempt_at
            session.commit()

    def claim_sci99_day(self):
        # 唯一约束在网络请求前落盘；失败、重启及并发均不能重置当天额度。
        with self.session_factory() as session:
            session.add(SourceRun(source="sci99", run_date=now().date(), started_at=now().replace(tzinfo=None)))
            try:
                session.commit()
                return True
            except IntegrityError:
                session.rollback()
                return False

    def store(self, points, source):
        if not points:
            raise SourceError("来源未返回价格点")
        with self.session_factory() as session:
            for point in points:
                config = COMMODITY_MAP.get(point.code)
                if (not config or not config.enabled or config.source != source or point.source != source
                        or config.quote_basis != point.quote_basis or config.unit != point.unit):
                    raise SourceError("价格点与配置口径不一致，拒绝入库")
                values = {column.name: getattr(point, column.name) for column in PricePoint.__table__.columns if column.name != "id"}
                stmt = insert(PricePoint).values(**values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["code", "source", "quote_basis", "quote_date"],
                    set_={key: value for key, value in values.items() if key not in ("code", "source", "quote_basis", "quote_date")},
                )
                session.execute(stmt)
            session.commit()

    def run(self):
        if not run_lock.acquire(blocking=False):
            raise FetchBusy()
        results = []
        try:
            settings = read_sci99_settings()
            for source in SOURCES:
                if source == "sci99":
                    if not sci99_configured(settings):
                        results.append({"source": source, "outcome": "skipped", "status": "pending_credentials", "message": "待配置凭据"})
                        continue
                    if not self.claim_sci99_day():
                        results.append({"source": source, "outcome": "skipped", "status": "daily_limit", "message": "卓创今日已尝试，跳过；保留上次结果"})
                        continue
                self.set_state(source, "running", "正在抓取")
                try:
                    adapter = FreeAdapter() if source == "free" else Sci99Adapter(settings=settings)
                    points = adapter.fetch()
                    self.store(points, source)
                    self.set_state(source, "ok", f"正常：入库 {len(points)} 个价格点", success=True)
                    results.append({"source": source, "outcome": "success", "status": "ok", "stored": len(points)})
                except Exception as error:
                    # 不输出 HTTP 异常原文、响应体、Cookie 或账号，避免凭据进入日志。
                    message = str(error) if isinstance(error, SourceError) else "内部处理失败，请检查数据库和解析器"
                    status = "blocked" if isinstance(error, AccessStopped) else "failed"
                    self.set_state(source, status, message)
                    logger.warning("source=%s status=%s reason=%s", source, status, message)
                    results.append({"source": source, "outcome": "failed", "status": status, "message": message})
            # 入库完成后独立检查提醒；通道故障不改变抓取成功状态。
            try:
                from .alerts import AlertService
                alert_result = AlertService(self.session_factory).run()
            except Exception:
                logger.error("行情入库完成，但提醒检查失败，请查看推送服务")
                alert_result = {"status": "failed", "message": "提醒检查失败"}
            return {"results": results, "sources": self.statuses(), "alerts": alert_result}
        finally:
            run_lock.release()

    def statuses(self):
        settings = read_sci99_settings()
        with self.session_factory() as session:
            states = {state.source: state for state in session.scalars(select(SourceState))}
            run = session.scalar(select(SourceRun).where(SourceRun.source == "sci99", SourceRun.run_date == now().date()))
        result = []
        for source, config in SOURCES.items():
            state = states[source]
            status, message = state.status, state.message
            if source == "sci99" and not sci99_configured(settings):
                status, message = "pending_credentials", "待配置凭据"
            elif status == "running" and not run_lock.locked():
                status, message = "interrupted", "上次抓取被中断；卓创当天额度不会重置"
            result.append({
                "source": source, "name": config["name"], "status": status, "message": message,
                "last_attempt_at": timestamp(state.last_attempt_at), "last_success_at": timestamp(state.last_success_at),
                "daily_limit_reached": bool(run) if source == "sci99" else False,
                "enabled_codes": [item.code for item in COMMODITIES if item.source == source and item.enabled],
                "pending_codes": [item.code for item in COMMODITIES if item.source == source and not item.enabled],
            })
        return result


def timestamp(value):
    return value.isoformat(timespec="seconds") + "+08:00" if value else None


def serialize_price(point):
    return {
        "code": point.code, "price": float(point.price), "unit": point.unit,
        "quote_basis": point.quote_basis, "quote_date": point.quote_date.isoformat(),
        "source": point.source, "source_name": point.source_name, "source_url": point.source_url,
        "fetched_at": timestamp(point.fetched_at),
    }


def latest_prices(session_factory=SessionLocal):
    source_states = {item["source"]: item for item in FetchService(session_factory).statuses()}
    result = []
    with session_factory() as session:
        for item in COMMODITIES:
            points = session.scalars(select(PricePoint).where(
                PricePoint.code == item.code, PricePoint.quote_basis == item.quote_basis,
                PricePoint.source == item.source, PricePoint.unit == item.unit,
            ).order_by(PricePoint.quote_date.desc()).limit(2)).all()
            point = points[0] if points else None
            previous = points[1] if len(points) > 1 else None
            # 仅比较相同来源、口径及单位的相邻报价日；缺少对比时不以零充数。
            change = point.price - previous.price if previous else None
            percent = ((change / previous.price * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                       if previous and previous.price > 0 else None)
            status = source_states[item.source]
            data_status = "pending_configuration" if not item.enabled else status["status"]
            message = "本阶段仅配置目录，具体报价规格和解析待核定" if not item.enabled else status["message"]
            result.append({
                "code": item.code, "name": item.name, "source": item.source, "quote_basis": item.quote_basis,
                "unit": item.unit, "data_status": data_status, "message": message,
                "stale": point is None or point.quote_date < now().date(),
                "price_point": serialize_price(point) if point else None,
                "change_amount": float(change) if change is not None else None,
                "change_percent": float(percent) if percent is not None else None,
                "previous_quote_date": previous.quote_date.isoformat() if previous else None,
            })
    return result


def price_history(code, days, session_factory=SessionLocal):
    item = COMMODITY_MAP[code]
    since = now().date() - timedelta(days=days - 1)
    with session_factory() as session:
        points = session.scalars(select(PricePoint).where(
            PricePoint.code == code, PricePoint.source == item.source,
            PricePoint.quote_basis == item.quote_basis, PricePoint.unit == item.unit,
            PricePoint.quote_date >= since,
        ).order_by(PricePoint.quote_date)).all()
        return {"code": code, "days": days, "quote_basis": item.quote_basis, "items": [serialize_price(p) for p in points]}
