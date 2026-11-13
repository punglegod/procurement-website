"""条目身份、日线与个人口径；所有写入由调用者的短事务提交。"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import secrets

from fastapi import HTTPException
from sqlalchemy import select, delete
from sqlalchemy.dialects.sqlite import insert

from .adapters.common import SourceError, now
from .config import COMMODITY_MAP
from .models import PricePoint, QuoteEntry, QuoteObservation, UserQuoteSelection, User, UserDailyDigest, PushLog


@dataclass
class QuoteRow:
    code: str
    product_name: str
    specification: str
    market: str
    price_type: str
    price: Decimal | None
    quote_date: date
    source: str
    source_name: str
    source_url: str
    fetched_at: datetime
    unit: str = "元/吨"
    low: Decimal | None = None
    high: Decimal | None = None
    price_text: str = ""
    change_text: str = ""


def plain_number(value):
    return format(value, "f").rstrip("0").rstrip(".") if "." in format(value, "f") else str(value)


def legacy_row(point):
    return QuoteRow(code=point.code, product_name=COMMODITY_MAP[point.code].name,
                    specification=point.quote_basis, market="", price_type="现货参考价",
                    price=point.price, quote_date=point.quote_date, source=point.source,
                    source_name=point.source_name, source_url=point.source_url,
                    fetched_at=point.fetched_at, unit=point.unit, price_text=plain_number(point.price))


def store_row(session, row, *, present=True):
    if row.code not in COMMODITY_MAP or not row.product_name or not row.price_type:
        raise SourceError("报价条目身份不完整")
    if (row.price is not None and (not row.price.is_finite() or not 0 < row.price < Decimal("10000000000000"))
            or (row.low is None) != (row.high is None)
            or row.low is not None and (not row.low.is_finite() or not row.high.is_finite()
                or row.low <= 0 or row.high < row.low or row.price != (row.low + row.high) / 2)
            or row.price is None and row.price_text not in {"-", "--", "—", "暂无报价", ""}):
        raise SourceError("报价数值或区间无效")
    identity = {key: getattr(row, key).strip() for key in ("code", "product_name", "specification", "market", "price_type")}
    entry = session.scalar(select(QuoteEntry).filter_by(**identity))
    if entry is None:
        entry = QuoteEntry(**identity, source=row.source, source_name=row.source_name,
                           missing=False, last_seen_at=row.fetched_at)
        session.add(entry)
        session.flush()
    elif entry.source != row.source:
        raise SourceError("不同来源的同名条目冲突，拒绝混用历史")
    if row.fetched_at > entry.last_seen_at:
        entry.last_seen_at = row.fetched_at
    if present:
        entry.last_seen_at = row.fetched_at
    if entry.last_quote_date is None or row.quote_date >= entry.last_quote_date:
        entry.last_quote_date, entry.last_price_text = row.quote_date, row.price_text or (plain_number(row.price) if row.price is not None else "—")
        entry.last_unit, entry.last_change_text = row.unit or "元/吨", row.change_text
        if present:
            entry.missing = row.price is None
    values = {key: getattr(row, key) for key in ("price", "low", "high", "change_text", "quote_date", "source_url", "fetched_at")}
    values.update(entry_id=entry.id, price_text=row.price_text or (plain_number(row.price) if row.price is not None else "—"), unit=row.unit or "元/吨")
    statement = insert(QuoteObservation).values(**values)
    # 同日报价未变化不刷新抓取时间；条目last_seen_at独立记录本轮确实存在。
    changed = session.execute(statement.on_conflict_do_update(
        index_elements=["entry_id", "quote_date"],
        set_={k: v for k, v in values.items() if k not in {"entry_id", "quote_date"}},
        where=((QuoteObservation.price.is_distinct_from(statement.excluded.price))
              | QuoteObservation.low.is_distinct_from(statement.excluded.low)
              | QuoteObservation.high.is_distinct_from(statement.excluded.high)
              | (QuoteObservation.unit != statement.excluded.unit)
              | (QuoteObservation.price_text != statement.excluded.price_text)
              | (QuoteObservation.change_text != statement.excluded.change_text))
              & (statement.excluded.price.is_not(None) | QuoteObservation.price.is_(None)))).rowcount
    return entry, changed


def backfill_prices(session, code=None):
    mirrored = select(QuoteObservation.id).join(QuoteEntry).where(
        QuoteEntry.code == PricePoint.code, QuoteEntry.source == PricePoint.source,
        QuoteEntry.specification == PricePoint.quote_basis, QuoteEntry.market == "",
        QuoteEntry.price_type == "现货参考价", QuoteObservation.quote_date == PricePoint.quote_date,
        QuoteObservation.price == PricePoint.price, QuoteObservation.unit == PricePoint.unit)
    # 兼容旧回放/旧写入方；正常双写后不逐日重复读取整段历史。
    query = select(PricePoint).where(~mirrored.exists()).order_by(PricePoint.id)
    if code:
        query = query.where(PricePoint.code == code)
    for point in session.scalars(query):
        store_row(session, legacy_row(point), present=False)


def label(entry):
    if entry.source != "sci99":
        return entry.specification
    return " · ".join(part for part in (COMMODITY_MAP[entry.code].name, entry.product_name,
                                       entry.specification, entry.market, entry.price_type) if part)


def observations(session, entry, day=None, since=None):
    query = select(QuoteObservation).where(QuoteObservation.entry_id == entry.id, QuoteObservation.price.is_not(None))
    if day:
        query = query.where(QuoteObservation.quote_date <= day)
    if since:
        query = query.where(QuoteObservation.quote_date >= since)
    rows = list(session.scalars(query.order_by(QuoteObservation.quote_date)))
    # 比较与走势只使用最新值相同单位，绝不把美元和人民币混成一条涨跌线。
    return [p for p in rows if p.unit == rows[-1].unit] if rows else []


def selected_entry(session, user_id, code, *, sync=True):
    if sync:
        backfill_prices(session, code)
    chosen = session.get(UserQuoteSelection, (user_id, code))
    if chosen:
        return session.get(QuoteEntry, chosen.entry_id)
    user = session.get(User, user_id)
    if not user or not user.enabled:
        return None
    candidates = list(session.scalars(select(QuoteEntry).where(QuoteEntry.code == code).order_by(QuoteEntry.id)))
    candidates = [e for e in candidates if session.scalar(select(QuoteObservation.id).where(QuoteObservation.entry_id == e.id).limit(1))]
    if not candidates:
        return None
    entry = secrets.choice([e for e in candidates if not e.missing] or candidates)
    # 唯一主键保证并发首次读取只分配一次，后到者必须读回已落盘的选择。
    session.execute(insert(UserQuoteSelection).values(user_id=user_id, code=code, entry_id=entry.id,
                    selected_at=now().replace(tzinfo=None)).on_conflict_do_nothing())
    chosen = session.get(UserQuoteSelection, (user_id, code))
    return session.get(QuoteEntry, chosen.entry_id)


def observation_dict(point, entry):
    return {"code": entry.code, "entry_id": entry.id, "price": float(point.price),
            "low": float(point.low) if point.low is not None else None,
            "high": float(point.high) if point.high is not None else None,
            "price_text": point.price_text, "change_text": point.change_text,
            "unit": point.unit, "quote_basis": label(entry), "quote_date": str(point.quote_date),
            "unit_pending": point.unit == "待核定",
            "source": entry.source, "source_name": entry.source_name, "source_url": point.source_url,
            "fetched_at": point.fetched_at.isoformat(timespec="seconds") + "+08:00"}


def entry_dict(session, entry):
    rows = observations(session, entry)
    return {key: getattr(entry, key) for key in ("id", "code", "product_name", "specification", "market", "price_type", "source", "source_name", "missing")} | {
        "quote_basis": label(entry), "last_seen_at": entry.last_seen_at.isoformat() + "+08:00",
        "reported_point": {"price": None, "price_text": entry.last_price_text, "quote_date": str(entry.last_quote_date),
                           "unit": entry.last_unit, "change_text": entry.last_change_text} if entry.missing and entry.last_price_text in {"-", "--", "—", "暂无报价", ""} else None,
        "price_point": observation_dict(rows[-1], entry) if rows else None}


def choose(session, user_id, code, entry_id):
    entry = session.get(QuoteEntry, entry_id)
    if not entry or entry.code != code:
        raise HTTPException(404, "该品种不存在此报价条目")
    if not session.scalar(select(QuoteObservation.id).where(QuoteObservation.entry_id == entry_id).limit(1)):
        raise HTTPException(422, "该条目尚无可用报价")
    previous = session.get(UserQuoteSelection, (user_id, code))
    if previous is None or previous.entry_id != entry_id:
        attempted = session.scalar(select(PushLog.id).where(PushLog.user_id == user_id,
            PushLog.kind == "digest", PushLog.send_day == now().date(),
            PushLog.status.not_in(["pending_configuration", "pending_verification"])).limit(1))
        # 尚无实际发送尝试的日报可按新选择重建；已发送的当日快照和额度保持不变。
        if attempted is None:
            session.execute(delete(UserDailyDigest).where(UserDailyDigest.user_id == user_id, UserDailyDigest.send_day == now().date()))
    session.merge(UserQuoteSelection(user_id=user_id, code=code, entry_id=entry_id,
                                    selected_at=now().replace(tzinfo=None)))
    return entry
