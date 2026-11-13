"""规则之外的日报和管理员进度；发送前持久领取额度，网络始终在事务外。"""
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from html import escape

from sqlalchemy import select

from .alerts import channel_status, email_status, log_dict
from .config import COMMODITIES, latest_workday
from .models import DailyDigest, UserDailyDigest, NotificationClaim, PricePoint, PushLog, PushSetting, User
from .quotes import selected_entry, observations, label
from .source_settings import progress_enabled


def digest_enabled(user, setting):
    return setting.daily_digest_enabled if setting and setting.daily_digest_enabled is not None else user.role == "admin"


def amount(value, suffix="", signed=False):
    if value is None:
        return "暂无对比数据"
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:+.2f}{suffix}" if signed else f"{rounded:.2f}{suffix}"


def build_digest(session, day, abnormal=False, user_id=None):
    title = f"采购行情日报 · {day.isoformat()}"
    blocks = []
    if abnormal:
        blocks.append("09:00 轮抓取异常，本报为最近可用数据")
    for item in COMMODITIES:
        entry = selected_entry(session, user_id, item.code) if user_id is not None else None
        # 09:00日报的卓创日度收盘价仅取已结束报价日，排除早间手动抓到的当日盘中值。
        closing_day = day - timedelta(days=1) if entry and entry.source == "sci99" else day
        points = observations(session, entry, day=closing_day) if entry else [] if user_id is not None else list(session.scalars(select(PricePoint).where(
            PricePoint.code == item.code, PricePoint.source == item.source,
            PricePoint.quote_basis == item.quote_basis, PricePoint.unit == item.unit,
            PricePoint.quote_date <= day).order_by(PricePoint.quote_date)))
        point = points[-1] if points else None
        previous = points[-2] if len(points) > 1 else None
        if point is None:
            basis, unit = (label(entry), entry.last_unit) if entry else (item.quote_basis, item.unit)
            missing = "【所选口径本轮未更新】" if entry and entry.missing else ""
            blocks.append(f"{item.name}{missing}：暂无报价（无可用数据）\n单位：{unit}；口径：{basis}；报价日期：无\n涨跌：暂无对比数据；近7日：历史数据不足；近30日最高/最低：无数据")
            continue
        if point.unit == "待核定":
            blocks.append(f"{item.name}【单位待核定】：{point.price_text}\n口径：{label(entry)}；报价日期：{point.quote_date}\n未默认元/吨，涨跌与走势暂不计算；请站长核定单位")
            continue
        change = point.price - previous.price if previous else None
        percent = change / previous.price * 100 if previous and previous.price > 0 else None
        direction = "上涨 ↑" if change is not None and change > 0 else "下跌 ↓" if change is not None and change < 0 else "持平 =" if change == 0 else "暂无对比数据"
        seven = [p for p in points if p.quote_date >= day - timedelta(days=6)]
        thirty = [p for p in points if p.quote_date >= day - timedelta(days=29)]
        trend = "历史数据不足"
        if len(seven) >= 2 and points[0].quote_date <= day - timedelta(days=6):
            first, last = seven[0].price, seven[-1].price
            movement = (last - first) / first * 100
            swing = (max(p.price for p in seven) - min(p.price for p in seven)) / first * 100
            description = ("震荡" if swing >= 1 else "小幅") + ("上行" if movement > 0 else "下行") if movement else "区间震荡" if swing else "持平"
            trend = f"{amount(movement, '%', True)}，近7日{description}（{len(seven)}个可用报价日）"
        bounds = f"{amount(max(p.price for p in thirty))}/{amount(min(p.price for p in thirty))} {point.unit}（{len(thirty)}条报价）" if thirty else "无数据"
        stale = "【过期】" if point.quote_date < latest_workday(day) else ""
        missing = "【所选口径本轮未更新】" if entry and entry.missing else ""
        display = getattr(point, "price_text", None) or amount(point.price)
        basis = label(entry) if entry else point.quote_basis
        blocks.append(f"{item.name}{stale}{missing}：{display} {point.unit}\n口径：{basis}；报价日期：{point.quote_date}\n较上一报价日：{direction} {amount(change, ' '+point.unit, True)} / {amount(percent, '%', True)}\n近7日：{trend}；近30日最高/最低：{bounds}")
    text = title + "\n\n" + "\n\n".join(blocks)
    html = "<!doctype html><html><body><h1>" + escape(title) + "</h1>" + "".join("<p>" + escape(block).replace("\n", "<br>") + "</p>" for block in blocks) + "</body></html>"
    return title, text, html


def telegram_parts(text, title):
    # 按UTF-16预算拆分，包含表情也低于Telegram 4096限制；不使用Markdown。
    parts, current, size = [], "", 0
    for character in text:
        units = len(character.encode("utf-16-le")) // 2
        if size + units > 3500:
            parts.append(current)
            current, size = "", 0
        current += character
        size += units
    if current:
        parts.append(current)
    return [part if index == 0 else f"{title}（续 {index+1}）\n{part}" for index, part in enumerate(parts)]


class NotificationService:
    def __init__(self, alerts):
        self.alerts = alerts
        self.factory = alerts.session_factory
        self.clock = alerts.clock

    def send(self, *, user_id, channel, kind, event, title, text, html=None, day=None):
        at = self.clock()
        day = day or at.date()
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            user = session.get(User, user_id)
            if not user or not user.enabled or (channel == "telegram" and user.role != "admin"):
                return {"status": "skipped"}
            setting = session.get(PushSetting, user_id)
            if kind == "digest" and (not digest_enabled(user, setting) or self.clock().date() != day):
                return {"status": "skipped"}
            if kind == "fetch_progress" and (user.role != "admin" or not progress_enabled(session)):
                return {"status": "skipped"}
            key = f"{user_id}:{channel}:{kind}:{event}"
            claim = session.get(NotificationClaim, key)
            previous = session.get(PushLog, claim.log_id) if claim else None
            if claim and (not previous or previous.user_id != user_id or previous.channel != channel or previous.kind != kind or previous.status not in {"pending_configuration", "pending_verification"}):
                return {"status": "deduplicated"}
            if channel == "telegram":
                settings = self.alerts.settings_reader()
                state = channel_status(settings)
            else:
                recipient = setting.email if setting else ""
                settings = self.alerts.email_settings_reader() | {"recipient": recipient, "subject": title, "html": html or ""}
                state = email_status(settings, recipient, setting.email_verified_at if setting else None)
            if kind == "digest":
                # 改选可能发生在生成日报与领取发送额度之间；短事务内读取最新个人快照。
                personal = session.get(UserDailyDigest, (user_id, day))
                if personal is None:
                    marker = session.get(DailyDigest, day)
                    abnormal = bool(marker and "09:00 轮抓取异常" in marker.message)
                    title, text, html = build_digest(session, day, abnormal, user_id=user_id)
                    personal = UserDailyDigest(user_id=user_id, send_day=day, title=title, message=text, html=html)
                    session.add(personal)
                title, text, html = personal.title, personal.message, personal.html
                if channel == "email":
                    settings |= {"subject": title, "html": html}
            log = previous or PushLog(user_id=user_id, channel=channel, kind=kind, send_day=day,
                                      created_at=at.replace(tzinfo=None))
            log.message, log.recipient = text, settings.get("recipient")
            log.status = "sending" if state["status"] == "configured" else state["status"]
            log.detail, log.finished_at = state["message"], None
            session.add(log)
            session.flush()
            if not claim:
                session.add(NotificationClaim(key=key, user_id=user_id, log_id=log.id))
            session.commit()
            log_id, should_send = log.id, log.status == "sending"
        if not should_send:
            return {"status": state["status"]}
        try:
            if kind == "digest" and self.clock().date() != day:
                status, detail, provider_id = "skipped", "已跨北京时间自然日，不发送旧日报", None
            elif channel == "telegram":
                parts = telegram_parts(text, title)
                for index, part in enumerate(parts):
                    status, detail, provider_id = self.alerts.sender.send(part, settings)
                    if status not in {"sent", "replayed"}:
                        if index:
                            status, detail = "unknown", "部分分段已接受，整体送达未知；不自动重发"
                        break
            else:
                status, detail, provider_id = self.alerts.email_sender.send(text, settings)
        except Exception:
            status, detail, provider_id = "unknown", "通道异常，送达未知；不自动重发", None
        with self.factory() as session:
            log = session.get(PushLog, log_id)
            if not log or log.user_id != user_id or log.kind != kind or log.channel != channel:
                return {"status": "skipped"}
            log.status, log.detail = status, detail
            log.finished_at = self.clock().replace(tzinfo=None)
            if channel == "telegram":
                log.telegram_message_id = provider_id
            else:
                log.provider_message_id = provider_id
            session.commit()
            return log_dict(log)

    def daily_digest(self, abnormal=False):
        at = self.clock()
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            digest = session.get(DailyDigest, at.date())
            if digest is None:
                title, text, html = build_digest(session, at.date(), abnormal)
                digest = DailyDigest(send_day=at.date(), created_at=at.replace(tzinfo=None), title=title, message=text, html=html)
                session.add(digest)
            session.commit()
            # 旧全站快照只保留调度异常与日标记；每个账号另存自己口径的不可变日报。
            abnormal = "09:00 轮抓取异常" in digest.message
            owners = [(u.id, u.role) for u in session.scalars(select(User).where(User.enabled.is_(True)))]
        results = []
        for user_id, role in owners:
            with self.factory() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                setting = session.get(PushSetting, user_id)
                user = session.get(User, user_id)
                if not user or not user.enabled or not digest_enabled(user, setting):
                    continue
                has_email = bool(setting and setting.email)
                personal = session.get(UserDailyDigest, (user_id, at.date()))
                if personal is None:
                    title, text, html = build_digest(session, at.date(), abnormal, user_id=user_id)
                    personal = UserDailyDigest(user_id=user_id, send_day=at.date(), title=title, message=text, html=html)
                    session.add(personal)
                payload = {"title": personal.title, "text": personal.message, "html": personal.html, "day": personal.send_day}
                session.commit()
            for channel in (["telegram"] if role == "admin" else []) + (["email"] if role != "admin" or has_email else []):
                results.append(self.send(user_id=user_id, channel=channel, kind="digest", event=str(at.date()), **payload))
        return results

    def progress(self, event, text):
        with self.factory() as session:
            ids = list(session.scalars(select(User.id).where(User.enabled.is_(True), User.role == "admin")))
        return [self.send(user_id=user_id, channel="telegram", kind="fetch_progress", event=event,
                          title="抓取进度", text=text) for user_id in ids]
