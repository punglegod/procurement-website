import logging
from importlib import import_module
import threading
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from .adapters.common import AccessStopped, SourceError, now
from .adapters.free import FreeAdapter
from .adapters.sci99 import Sci99Adapter, Sci99Stopped
from .config import COMMODITIES, COMMODITY_MAP, SOURCES, read_sci99_settings, sci99_configured, latest_workday
from .db import SessionLocal
from .models import PricePoint, SourceRun, SourceState, QuoteEntry, QuoteImport
from .quotes import QuoteRow, store_row, legacy_row, selected_entry, observations, observation_dict, label, entry_dict
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
                store_row(session, legacy_row(point))
            session.commit()
        return changed

    def store_sci99(self, points, outcomes):
        from .adapters.sci99_quotes import PRODUCT_PAGES
        reports, changed = [], 0
        imported_at = now().replace(tzinfo=None)
        for code in PRODUCT_PAGES:
            rows = [p for p in points if isinstance(p, QuoteRow) and p.code == code]
            outcome = next((o for o in outcomes if o["code"] == code), {"status": "failed"})
            count = len({(r.product_name, r.specification, r.market, r.price_type) for r in rows})
            status, message = outcome["status"], outcome.get("message", "未取得完整报价")
            try:
                with self.session_factory() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    previous = session.scalar(select(QuoteImport).where(QuoteImport.code == code,
                        QuoteImport.source == "sci99", QuoteImport.status == "ok").order_by(QuoteImport.id.desc()).limit(1))
                    if status != "ok" or not rows:
                        raise SourceError("本品种未取得完整报价，保留最后数据")
                    keys = [(r.product_name, r.specification, r.market, r.price_type, r.quote_date) for r in rows]
                    if len(set(keys)) != len(keys):
                        raise SourceError("同条目同日报价重复，拒绝覆盖或截断清单")
                    if previous and previous.count >= 4 and count < previous.count / 2:
                        raise SourceError("条目数较上次成功骤降超过50%，拒绝部分入库并告警")
                    session.execute(update(QuoteEntry).where(QuoteEntry.code == code, QuoteEntry.source == "sci99").values(missing=True))
                    stored = 0
                    for row in rows:
                        if row.source != "sci99":
                            raise SourceError("来源不匹配")
                        _, delta = store_row(session, row)
                        stored += delta
                    message = f"{count}条目，{len(rows)}条日线，更新{stored}条，无报价{sum(r.price is None for r in rows)}条，单位待核{sum(r.unit == '待核定' for r in rows)}条"
                    session.add(QuoteImport(code=code, source="sci99", count=count, status="ok", message=message,
                                            fetched_at=imported_at))
                    session.commit()
                    changed += stored
            except Exception as error:
                status = "blocked" if status == "blocked" else "failed"
                message = str(error) if isinstance(error, SourceError) else "本品种报价入库失败；保留最后数据"
                with self.session_factory() as session:
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                    session.execute(update(QuoteEntry).where(QuoteEntry.code == code, QuoteEntry.source == "sci99").values(missing=True))
                    session.add(QuoteImport(code=code, source="sci99", count=count, status=status, message=message,
                                            fetched_at=imported_at))
                    session.commit()
                logger.warning("sci99 code=%s status=%s count=%d reason=%s", code, status, count, message)
            reports.append({"code": code, "status": status, "count": count, "message": message})
        return changed, reports

    def run(self, *, slot=None):
        if not run_lock.acquire(blocking=False):
            raise FetchBusy()
        results = []
        try:
            source_settings = SourceSettings(self.session_factory, legacy_reader=lambda: read_sci99_settings())
            for source in SOURCES:
                sci_auth_revision = None
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
                        settings, sci_auth_revision = source_settings.resolve_for_verification()
                        adapter = Sci99Adapter(settings=settings)
                    else:
                        module, name = SOURCES[source]["adapter"].split(":")
                        adapter = getattr(import_module(f".adapters.{module}", package=__package__), name)()
                    points = adapter.fetch()
                    if source == "sci99":
                        source_settings.record_auth_result(getattr(adapter, "auth_status", None), sci_auth_revision)
                        changed, reports = self.store_sci99(points, getattr(adapter, "quote_results", []))
                        failed = any(r["status"] != "ok" for r in reports)
                        status = "partial" if failed else "ok"
                        message = "；".join(f"{COMMODITY_MAP[r['code']].name}：" + ("失败：" if r["status"] != "ok" else "") + r["message"] for r in reports)
                        self.set_state(source, status, "全量报价：" + message[:280], success=not failed)
                        results.append({"source": source, "outcome": "failed" if failed else "success", "status": status,
                                        "stored": changed, "message": message, "commodities": reports})
                        continue
                    changed = self.store(points, source)
                    self.set_state(source, "ok", f"正常：更新 {changed} 个价格点" if changed else "正常：无更新", success=True)
                    results.append({"source": source, "outcome": "success", "status": "ok", "stored": changed,
                                    "prices": [{"code": point.code, "price": float(point.price), "unit": point.unit} for point in points]})
                except Exception as error:
                    # 本轮该来源未取得完整清单，所选旧条目继续保留，但不得冒充本轮更新。
                    with self.session_factory() as session:
                        session.execute(update(QuoteEntry).where(QuoteEntry.source == source).values(missing=True))
                        session.commit()
                    # 不输出 HTTP 异常原文、响应体、Cookie 或账号，避免凭据进入日志。
                    message = str(error) if isinstance(error, SourceError) else "内部处理失败，请检查数据库和解析器"
                    if source == "sci99":
                        message = ("卓创登录异常、验证码或风控，已停止；请人工检查官网" if isinstance(error, AccessStopped)
                                   else "卓创报价口径尚未绑定到当前行情，未替换免费源" if isinstance(error, SourceError) and str(error) == "卓创报价口径尚未绑定到当前行情，未替换免费源"
                                   else "卓创报价解析或处理未确认，请人工核定（未影响免费源）")
                    status = "blocked" if isinstance(error, AccessStopped) else "failed"
                    if source == "sci99" and isinstance(error, Sci99Stopped):
                        status, message = error.status, str(error)
                        auth_status = status if status != "quote_pending" else getattr(adapter, "auth_status", None)
                        if sci_auth_revision is not None:
                            try:
                                source_settings.record_auth_result(auth_status, sci_auth_revision)
                            except Exception:
                                logger.error("卓创登录验证状态写入失败，请检查数据库")
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
            if source == "sci99":
                verified = "验证时间：" + configured["verified_at"] if configured["verified_at"] else ""
                for detail in (configured["auth_message"], verified, configured["quote_message"]):
                    if detail and detail not in message:
                        message += "；" + detail
            result.append({
                "source": source, "name": config["name"], "status": status, "message": message,
                "last_attempt_at": timestamp(state.last_attempt_at), "last_success_at": timestamp(state.last_success_at),
                "daily_limit_reached": bool(run) if source == "sci99" else False,
                "enabled_codes": [item.code for item in COMMODITIES if item.code != "alu_a00"] if source == "sci99" and configured["enabled"] else [item.code for item in COMMODITIES if item.source == source and item.enabled],
                "pending_codes": [item.code for item in COMMODITIES if item.source == source and not item.enabled],
                **({key: configured[key] for key in ("auth_status", "auth_message", "auth_checked_at", "verified_at", "quote_status", "quote_message")} if source == "sci99" else {}),
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


def latest_prices(session_factory=SessionLocal, user_id=None):
    if user_id is not None:
        return selected_prices(session_factory, user_id)
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


def price_history(code, days, session_factory=SessionLocal, user_id=None):
    item = COMMODITY_MAP[code]
    since = now().date() - timedelta(days=days - 1)
    with session_factory() as session:
        if user_id is not None:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            entry = selected_entry(session, user_id, code)
            points = observations(session, entry, since=since) if entry else []
            result = {"code": code, "days": days, "selected_entry_id": entry.id if entry else None,
                      "quote_basis": label(entry) if entry else item.quote_basis,
                      "selection_missing": bool(entry and entry.missing),
                      "items": [observation_dict(p, entry) for p in points]}
            session.commit()
            return result
        points = session.scalars(select(PricePoint).where(
            PricePoint.code == code, PricePoint.source == item.source,
            PricePoint.quote_basis == item.quote_basis, PricePoint.unit == item.unit,
            PricePoint.quote_date >= since,
        ).order_by(PricePoint.quote_date)).all()
        return {"code": code, "days": days, "quote_basis": item.quote_basis, "items": [serialize_price(p) for p in points]}


def selected_prices(factory, user_id):
    states = {s["source"]: s for s in FetchService(factory).statuses()}
    result = []
    with factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for item in COMMODITIES:
            entry = selected_entry(session, user_id, item.code)
            points = observations(session, entry) if entry else []
            point = points[-1] if points else None
            previous = points[-2] if len(points) > 1 else None
            if point and point.unit == "待核定":
                previous = None
            change = point.price - previous.price if previous else None
            percent = (change / previous.price * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if previous else None
            state = states.get(entry.source if entry else item.source, {"status": "unknown", "message": "来源已停用，保留历史报价"})
            missing = bool(entry and entry.missing)
            result.append({"code": item.code, "name": item.name, "source": entry.source if entry else item.source,
                "unit": point.unit if point else entry.last_unit if entry else item.unit, "quote_basis": label(entry) if entry else item.quote_basis,
                "selected_entry_id": entry.id if entry else None, "selection_missing": missing,
                "data_status": state["status"], "message": "所选口径本轮未更新" if missing else state["message"],
                "stale": point is None or point.quote_date < latest_workday(now().date()),
                "price_point": observation_dict(point, entry) if point else None,
                "change_amount": float(change) if change is not None else None,
                "change_percent": float(percent) if percent is not None else None,
                "previous_quote_date": str(previous.quote_date) if previous else None})
        session.commit()
    return result


def quote_options(code, user_id, factory=SessionLocal):
    with factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        entry = selected_entry(session, user_id, code)
        result = {"code": code, "selected_entry_id": entry.id if entry else None,
                  "items": [entry_dict(session, e) for e in session.scalars(select(QuoteEntry).where(QuoteEntry.code == code).order_by(QuoteEntry.id))]}
        session.commit()
        return result
