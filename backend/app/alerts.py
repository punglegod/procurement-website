"""提醒规则、持久化日额度及 Telegram 通道。"""

import logging
import re
from decimal import Decimal
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .adapters.common import now
from .config import COMMODITY_MAP, read_telegram_settings, read_account_settings
from .db import SessionLocal
from .models import AlertRule, PricePoint, PushLog, PushSetting, User
from .service import timestamp

logger = logging.getLogger(__name__)


class RuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    kind: Literal["daily_change", "price_gte", "price_lte"]
    threshold: Decimal = Field(gt=0, max_digits=14, decimal_places=4)
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def known_code(cls, value):
        if value not in COMMODITY_MAP:
            raise ValueError("未知品种 code")
        return value


def channel_status(settings=None):
    settings = read_telegram_settings() if settings is None else settings
    missing = [key for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID") if not settings.get(key)]
    return {"status": "pending_configuration" if missing else "configured",
            "message": "待配置：" + "、".join(missing) if missing else "已配置（发送结果请查看推送日志）",
            "missing": missing}


def rule_dict(rule):
    return {"id": rule.id, "user_id": rule.user_id, "code": rule.code, "name": COMMODITY_MAP[rule.code].name,
            "kind": rule.kind, "threshold": float(rule.threshold), "enabled": rule.enabled,
            "source": rule.source, "quote_basis": rule.quote_basis, "unit": rule.unit,
            "created_at": timestamp(rule.created_at), "updated_at": timestamp(rule.updated_at)}


def log_dict(log):
    return {key: getattr(log, key) for key in ("id", "user_id", "channel", "recipient", "provider_message_id", "rule_id", "code", "kind", "status", "message", "detail", "telegram_message_id")} | {
        "send_day": log.send_day.isoformat(), "created_at": timestamp(log.created_at),
        "finished_at": timestamp(log.finished_at)}


class TelegramSender:
    def __init__(self, client=None):
        self.client = client

    def send(self, text, settings):
        # httpx 的 INFO 请求日志包含 token 所在 URL，由下方过滤器屏蔽。
        # 禁止记录异常原文或 Telegram description，二者都可能带回敏感配置。
        token = settings["TELEGRAM_BOT_TOKEN"]
        if not token or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_-" for c in token):
            return "failed", "Bot token 格式无效，请检查 .env", None
        request = httpx.Request("POST", f"https://api.telegram.org/bot{token}/sendMessage",
                               json={"chat_id": settings["TELEGRAM_CHAT_ID"], "text": text})
        try:
            if self.client:
                response = self.client.send(request, follow_redirects=False)
            else:
                with httpx.Client(timeout=15, follow_redirects=False) as client:
                    response = client.send(request)
            if response.status_code != 200:
                return "failed", f"Telegram HTTP {response.status_code}；本次不自动重试", None
            payload = response.json()
            result = payload.get("result")
            if payload.get("ok") is True and isinstance(result, dict) and type(result.get("message_id")) is int:
                return "sent", "Telegram 已接受消息", result["message_id"]
            return "failed", "Telegram 未确认成功，请检查 token、chat_id 和 Bot 会话权限", None
        except httpx.TimeoutException:
            return "unknown", "发送超时，是否送达未知；为防重复，本日不再发送该规则", None
        except httpx.RequestError:
            return "unknown", "网络异常，是否送达未知；为防重复，本日不再发送该规则", None
        except (ValueError, TypeError, AttributeError):
            return "unknown", "Telegram 响应格式异常，是否送达未知；本日不再发送该规则", None


class EmailSender:
    def __init__(self, client=None):
        self.client = client

    def send(self, text, settings):
        request = httpx.Request("POST", "https://api.resend.com/emails",
                               headers={"Authorization": "Bearer " + settings["RESEND_API_KEY"]},
                               json={"from": settings["EMAIL_FROM"], "to": [settings["recipient"]],
                                     "subject": "采购行情 · 现货提醒", "text": text})
        try:
            if self.client:
                response = self.client.send(request, follow_redirects=False)
            else:
                with httpx.Client(timeout=15, follow_redirects=False) as client:
                    response = client.send(request)
            if response.status_code not in (200, 201):
                return "failed", f"Resend HTTP {response.status_code}；本次不自动重试", None
            payload = response.json()
            message_id = payload.get("id")
            if isinstance(message_id, str) and 0 < len(message_id) <= 200:
                return "sent", "Resend 已接受邮件，请到邮箱核对收信", message_id
            return "unknown", "Resend 未返回有效消息编号，是否送达未知", None
        except (httpx.RequestError, ValueError, TypeError, AttributeError):
            return "unknown", "邮件网络或响应异常，是否送达未知；本日不重试", None


def email_status(settings, recipient):
    missing = [name for name in ("RESEND_API_KEY", "EMAIL_FROM") if not settings.get(name)]
    if not recipient:
        missing.append("个人邮箱")
    return {"status": "pending_configuration" if missing else "configured",
            "message": "待配置：" + "、".join(missing) if missing else "已配置（发送结果请查看推送日志）",
            "missing": missing}


class _SecretURLFilter(logging.Filter):
    def filter(self, record):
        # httpx/httpcore 的日志可能含完整 token URL；只屏蔽 Telegram 请求日志。
        message = record.getMessage()
        return "api.telegram.org" not in message and not re.search(r"/bot[A-Za-z0-9:_-]+/", message)


for _name in ("httpx", "httpcore.connection", "httpcore.http11", "httpcore.proxy", "httpcore.http2"):
    logging.getLogger(_name).addFilter(_SecretURLFilter())


def matching_points(session, rule, day):
    return session.scalars(select(PricePoint).where(
        PricePoint.code == rule.code, PricePoint.source == rule.source,
        PricePoint.quote_basis == rule.quote_basis, PricePoint.unit == rule.unit,
        PricePoint.quote_date <= day,
    ).order_by(PricePoint.quote_date.desc()).limit(2)).all()


def evaluate(rule, points, day):
    item = COMMODITY_MAP[rule.code]
    if (rule.source, rule.quote_basis, rule.unit) != (item.source, item.quote_basis, item.unit):
        return False, "报价口径配置已变更，请编辑并保存规则重新确认"
    if not points:
        return False, "暂无同口径报价，等待数据"
    current = points[0]
    if current.quote_date != day:
        return False, "报价已过期，跳过"
    if rule.kind == "daily_change":
        if len(points) < 2:
            return False, "缺少上一报价日，无法计算日涨跌幅"
        change = (current.price - points[1].price) / points[1].price * 100
        hit = abs(change) >= rule.threshold
        reason = f"日涨跌幅 {change:+.4f}%（绝对值 ≥ {rule.threshold}%）"
    else:
        hit = current.price >= rule.threshold if rule.kind == "price_gte" else current.price <= rule.threshold
        reason = f"价格 {current.price} {rule.unit}（{'≥' if rule.kind == 'price_gte' else '≤'} {rule.threshold}）"
    return bool(hit), reason


class AlertService:
    def __init__(self, session_factory=SessionLocal, sender=None, settings_reader=read_telegram_settings,
                 email_sender=None, email_settings_reader=read_account_settings):
        self.session_factory = session_factory
        self.sender = sender or TelegramSender()
        self.settings_reader = settings_reader
        self.email_sender = email_sender or EmailSender()
        self.email_settings_reader = email_settings_reader

    def channels(self, user_id):
        with self.session_factory() as session:
            user = session.get(User, user_id)
            if not user or not user.enabled:
                return {}
            setting = session.get(PushSetting, user_id)
            email = setting.email if setting else ""
            result = {}
            if user.role == "admin":
                result["telegram"] = channel_status(self.settings_reader())
            if user.role == "user" or email:
                result["email"] = email_status(self.email_settings_reader(), email)
            return result

    def status(self, user_id):
        channels = self.channels(user_id)
        configured = bool(channels) and all(c["status"] == "configured" for c in channels.values())
        return {"status": "configured" if configured else "pending_configuration",
                "message": "?".join(f"{name}?{value['message']}" for name, value in channels.items()), "channels": channels}

    def rules(self, *, user_id):
        with self.session_factory() as session:
            day = now().date()
            rows = []
            for rule in session.scalars(select(AlertRule).where(AlertRule.user_id == user_id).order_by(AlertRule.id)):
                hit, reason = evaluate(rule, matching_points(session, rule, day), day)
                rows.append(rule_dict(rule) | {"matched": hit, "data_message": reason})
            return {"items": rows, "channel": self.status(user_id)}

    def save(self, data, rule_id=None, *, user_id):
        with self.session_factory() as session:
            rule = session.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)) if rule_id is not None else AlertRule(user_id=user_id, created_at=now().replace(tzinfo=None))
            if rule is None:
                return None
            for key, value in data.model_dump().items():
                setattr(rule, key, value)
            item = COMMODITY_MAP[data.code]
            rule.source, rule.quote_basis, rule.unit = item.source, item.quote_basis, item.unit
            rule.updated_at = now().replace(tzinfo=None)
            session.add(rule)
            session.commit()
            return rule_dict(rule)

    def delete(self, rule_id, *, user_id):
        with self.session_factory() as session:
            rule = session.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id))
            if rule is None:
                return False
            session.delete(rule)
            session.commit()
            return True

    def logs(self, page=1, page_size=30, *, user_id):
        with self.session_factory() as session:
            query = select(PushLog).where(PushLog.user_id == user_id)
            total = session.scalar(select(func.count()).select_from(PushLog).where(PushLog.user_id == user_id))
            rows = session.scalars(query.order_by(PushLog.id.desc()).offset((page-1)*page_size).limit(page_size))
            return {"items": [log_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def deliver(self, text, *, user_id, channel="telegram", rule_id=None, code=None, at=None):
        at = at or now()
        with self.session_factory() as session:
            # 在同一写事务检查账号与归属，避免禁用或删除后的新发送。
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            user = session.get(User, user_id)
            if not user or not user.enabled:
                return {"status": "skipped", "detail": "账号不可用"}
            if rule_id is not None:
                rule = session.get(AlertRule, rule_id)
                if not rule or rule.user_id != user_id or not rule.enabled:
                    return {"status": "skipped", "detail": "规则不存在、已暂停或不属于当前账号"}
            setting = session.get(PushSetting, user_id)
            recipient = setting.email if setting else ""
            if channel == "telegram":
                if user.role != "admin":
                    return {"status": "skipped", "detail": "普通账号不使用 Telegram"}
                settings = self.settings_reader()
                state = channel_status(settings)
            else:
                settings = self.email_settings_reader() | {"recipient": recipient}
                state = email_status(settings, recipient)
            previous = session.scalar(select(PushLog).where(PushLog.user_id == user_id,
                PushLog.rule_id == rule_id, PushLog.code == code, PushLog.send_day == at.date(), PushLog.channel == channel)) if rule_id else None
            if previous and previous.status != "pending_configuration":
                return {"status": "deduplicated", "channel": channel, "detail": "该用户该规则该通道今天已有发送尝试，跳过"}
            log = previous or PushLog(user_id=user_id, channel=channel, rule_id=rule_id, code=code,
                send_day=at.date(), kind="rule" if rule_id else "test", created_at=at.replace(tzinfo=None))
            log.recipient = recipient if channel == "email" else None
            log.message = text
            log.status = "sending" if state["status"] == "configured" else "pending_configuration"
            log.detail = "发送处理中；中断后送达未知，本日不重试" if log.status == "sending" else state["message"]
            log.finished_at = None
            session.add(log)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return {"status": "deduplicated", "channel": channel, "detail": "今日额度已被其他检查领取"}
            log_id, should_send = log.id, log.status == "sending"
        # 网络请求在额度落盘后进行，避免长时间持有数据库写锁。
        if should_send:
            try:
                sender = self.sender if channel == "telegram" else self.email_sender
                status, detail, provider_id = sender.send(text, settings)
            except Exception:
                status, detail, provider_id = "unknown", "通道异常，送达未知；本日不再发送该规则", None
            with self.session_factory() as session:
                log = session.get(PushLog, log_id)
                if log is None:
                    return {"status": "skipped", "detail": "账号及日志已删除"}
                log.status, log.detail = status, detail
                if channel == "telegram":
                    log.telegram_message_id = provider_id
                else:
                    log.provider_message_id = provider_id
                log.finished_at = now().replace(tzinfo=None)
                session.commit()
                return log_dict(log)
        with self.session_factory() as session:
            log = session.get(PushLog, log_id)
            log.finished_at = now().replace(tzinfo=None)
            session.commit()
            return log_dict(log)

    def test(self, *, user_id):
        results = [self.deliver(f"采购行情 · 通道测试\n时间：{now().isoformat(timespec='seconds')}\n这是一条测试消息，不代表行情规则触发。", user_id=user_id, channel=channel)
                   for channel in self.channels(user_id)]
        return results[0] if len(results) == 1 else {"results": results}

    def run(self, *, at=None, user_id=None):
        # 定时任务检查全部有效账号；个人 API 必须传入认证账号。
        at = at or now()
        results = []
        with self.session_factory() as session:
            query = select(AlertRule).join(User, User.id == AlertRule.user_id).where(AlertRule.enabled.is_(True), User.enabled.is_(True))
            if user_id is not None:
                query = query.where(AlertRule.user_id == user_id)
            rules = session.scalars(query.order_by(AlertRule.id)).all()
            for rule in rules:
                points = matching_points(session, rule, at.date())
                hit, reason = evaluate(rule, points, at.date())
                if not hit:
                    results.append({"rule_id": rule.id, "user_id": rule.user_id, "status": "skipped", "detail": reason})
                    continue
                point = points[0]
                text = (f"采购行情 · 现货提醒 #{rule.id}\n{COMMODITY_MAP[rule.code].name}\n{reason}\n"
                        f"现价：{point.price} {rule.unit}\n报价日期：{point.quote_date}\n口径：{rule.quote_basis}\n"
                        f"来源：{point.source_name}\n{point.source_url}")
                for channel in self.channels(rule.user_id):
                    result = self.deliver(text, user_id=rule.user_id, channel=channel, rule_id=rule.id, code=rule.code, at=at)
                    results.append({"rule_id": rule.id, "user_id": rule.user_id, "channel": channel} | result)
        return {"channel": self.status(user_id) if user_id else None, "results": results}
