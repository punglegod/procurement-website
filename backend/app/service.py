import logging
from importlib import import_module
import threading
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from .adapters.common import AccessStopped, SourceError, now
from .adapters.free import FreeAdapter
from .adapters.sci99 import Sci99Adapter
from .config import COMMODITIES, COMMODITY_MAP, SOURCES, read_sci99_settings, sci99_configured, latest_workday
from .db import SessionLocal
from .models import PricePoint, SourceRun, SourceState
from .source_settings import SourceSettings

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
        changed = 0
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
                    where=PricePoint.price != stmt.excluded.price,
                )
                changed += session.execute(stmt).rowcount
            session.commit()
        return changed

    def run(self, *, slot=None):
        if not run_lock.acquire(blocking=False):
            raise FetchBusy()
        results = []
        try:
            source_settings = SourceSettings(self.session_factory, legacy_reader=lambda: read_sci99_settings())
            for source in SOURCES:
                if source == "sci99":
                    configured = source_settings.state()
                    if configured["status"] != "enabled":
                        results.append({"source": source, "outcome": "skipped", "status": configured["status"], "message": configured["message"]})
                        continue
                    # 四轮中只有09:00允许卓创；手动与其余三轮也不消耗卓创额度。
                    if slot != "09:00":
                        results.append({"source": source, "outcome": "skipped", "status": "scheduled_only", "message": "卓创仅09:00轮执行"})
                        continue
                    if not self.claim_sci99_day():
                        results.append({"source": source, "outcome": "skipped", "status": "daily_limit", "message": "卓创今日已尝试，跳过；保留上次结果"})
                        continue
                try:
                    self.set_state(source, "running", "正在抓取")
                    if source == "free":
                        adapter = FreeAdapter()
                    elif source == "sci99":
                        settings, _, _ = source_settings.resolve()
                        adapter = Sci99Adapter(settings=settings)
                    else:
                        module, name = SOURCES[source]["adapter"].split(":")
                        adapter = getattr(import_module(f".adapters.{module}", package=__package__), name)()
                    points = adapter.fetch()
                    if source == "sci99" and any(COMMODITY_MAP[p.code].source != "sci99" for p in points):
                        raise SourceError("卓创报价口径尚未绑定到当前行情，未替换免费源")
                    changed = self.store(points, source)
                    self.set_state(source, "ok", f"正常：更新 {changed} 个价格点" if changed else "正常：无更新", success=True)
                    results.append({"source": source, "outcome": "success", "status": "ok", "stored": changed,
                                    "prices": [{"code": point.code, "price": float(point.price), "unit": point.unit} for point in points]})
                except Exception as error:
                    # 不输出 HTTP 异常原文、响应体、Cookie 或账号，避免凭据进入日志。
                    message = str(error) if isinstance(error, SourceError) else "内部处理失败，请检查数据库和解析器"
                    if source == "sci99":
                        message = ("卓创登录异常、验证码或风控，已停止；请人工检查官网" if isinstance(error, AccessStopped)
                                   else "卓创报价口径尚未绑定到当前行情，未替换免费源" if isinstance(error, SourceError) and str(error) == "卓创报价口径尚未绑定到当前行情，未替换免费源"
                                   else "卓创报价解析或处理未确认，请人工核定（未影响免费源）")
                    status = "blocked" if isinstance(error, AccessStopped) else "failed"
                    try:
                        self.set_state(source, status, message)
                    except Exception:
                        logger.error("来源状态写入失败，请检查数据库")
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
        configured = SourceSettings(self.session_factory, legacy_reader=lambda: read_sci99_settings()).state()
        with self.session_factory() as session:
            states = {state.source: state for state in session.scalars(select(SourceState))}
            run = session.scalar(select(SourceRun).where(SourceRun.source == "sci99", SourceRun.run_date == now().date()))
        result = []
        for source, config in SOURCES.items():
            state = states[source]
            status, message = state.status, state.message
            if source == "sci99" and configured["status"] != "enabled":
                status, message = configured["status"], configured["message"]
            elif source == "sci99" and state.status in {"never_run", "pending_credentials", "configured_disabled", "configuration_error"}:
                status, message = configured["status"], configured["message"]
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
                "stale": point is None or point.quote_date < latest_workday(now().date()),
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
