"""提醒规则、持久化日额度及 Telegram 通道。"""

import logging
import re
from decimal import Decimal
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .adapters.common import now
from .config import COMMODITY_MAP, TIMEZONE, read_telegram_settings, read_account_settings
from .db import SessionLocal
from .models import AlertRule, PricePoint, PushLog, PushSetting, User
from .security import reserve_email_limits, reserve_limits
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
                                     "subject": settings.get("subject", "采购行情 · 现货提醒"), "text": text,
                                     **({"html": settings["html"]} if settings.get("html") else {})})
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


def email_status(settings, recipient, verified_at=None):
    missing = [name for name in ("RESEND_API_KEY", "EMAIL_FROM") if not settings.get(name)]
    if not recipient:
        missing.append("个人邮箱")
    if missing:
        return {"status": "pending_configuration", "message": "待配置：" + "、".join(missing), "missing": missing}
    if not verified_at:
        return {"status": "pending_verification", "message": "请先验证个人邮箱", "missing": []}
    return {"status": "configured", "message": "已配置（发送结果请查看推送日志）", "missing": []}


class _SecretURLFilter(logging.Filter):
    def filter(self, record):
        # httpx/httpcore 的日志可能含完整 token URL；只屏蔽 Telegram 请求日志。
        message = record.getMessage()
        return "api.telegram.org" not in message and "sci99.com" not in message and not re.search(r"/bot[A-Za-z0-9:_-]+/", message)


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
                 email_sender=None, email_settings_reader=read_account_settings, clock=None):
        self.session_factory = session_factory
        self.sender = sender or TelegramSender()
        self.settings_reader = settings_reader
        self.email_sender = email_sender or EmailSender()
        self.email_settings_reader = email_settings_reader
        # 线上每次取实时北京时间；回放可显式注入时钟，旧 run(at=...) 也表示固定模拟时钟。
        self.clock = lambda: (clock or now)().astimezone(TIMEZONE)

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
                result["email"] = email_status(self.email_settings_reader(), email, setting.email_verified_at if setting else None)
            return result

    def status(self, user_id):
        channels = self.channels(user_id)
        configured = bool(channels) and all(c["status"] == "configured" for c in channels.values())
        status = "configured" if configured else "pending_verification" if any(c["status"] == "pending_verification" for c in channels.values()) else "pending_configuration"
        return {"status": status,
                "message": "；".join(f"{name}：{value['message']}" for name, value in channels.items()), "channels": channels}

    def rules(self, *, user_id):
        with self.session_factory() as session:
            day = self.clock().date()
            rows = []
            for rule in session.scalars(select(AlertRule).where(AlertRule.user_id == user_id).order_by(AlertRule.id)):
                hit, reason = evaluate(rule, matching_points(session, rule, day), day)
                rows.append(rule_dict(rule) | {"matched": hit, "data_message": reason})
            return {"items": rows, "channel": self.status(user_id)}

    def save(self, data, rule_id=None, *, user_id):
        with self.session_factory() as session:
            if rule_id is None:
                # 数量与创建额度在同一短写事务检查，避免并发绕过上限。
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                count = session.scalar(select(func.count()).select_from(AlertRule).where(AlertRule.user_id == user_id))
                if count >= 20:
                    raise HTTPException(429, "每个账号最多保留 20 条提醒规则")
                reserve_limits(session, [("rule_create", str(user_id), 10, 600), ("rule_create", str(user_id), 50, 86400)])
            rule = session.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.user_id == user_id)) if rule_id is not None else AlertRule(user_id=user_id, created_at=self.clock().replace(tzinfo=None))
            if rule is None:
                return None
            for key, value in data.model_dump().items():
                setattr(rule, key, value)
            item = COMMODITY_MAP[data.code]
            rule.source, rule.quote_basis, rule.unit = item.source, item.quote_basis, item.unit
            rule.updated_at = self.clock().replace(tzinfo=None)
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
        if not isinstance(page, int) or not 1 <= page <= 100000 or not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise HTTPException(422, "page 须为 1～100000，page_size 须为 1～100")
        with self.session_factory() as session:
            query = select(PushLog).where(PushLog.user_id == user_id)
            total = session.scalar(select(func.count()).select_from(PushLog).where(PushLog.user_id == user_id))
            rows = session.scalars(query.order_by(PushLog.id.desc()).offset((page-1)*page_size).limit(page_size))
            return {"items": [log_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    def deliver(self, text, *, user_id, channel="telegram", rule_id=None, code=None, at=None, expected_day=None, clock=None, client_ip="internal"):
        clock = clock or ((lambda: at.astimezone(TIMEZONE)) if at is not None else self.clock)
        with self.session_factory() as session:
            # 在同一写事务检查账号与归属，避免禁用或删除后的新发送。
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            at = clock()
            if expected_day is not None and at.date() != expected_day:
                return {"status": "skipped", "detail": "检查已跨北京时间自然日，停止旧轮；下轮重新检查"}
            user = session.get(User, user_id)
            if not user or not user.enabled:
                return {"status": "skipped", "detail": "账号不可用"}
            if rule_id is not None:
                rule = session.get(AlertRule, rule_id)
                if not rule or rule.user_id != user_id or not rule.enabled:
                    return {"status": "skipped", "detail": "规则不存在、已暂停或不属于当前账号"}
                code = rule.code
            setting = session.get(PushSetting, user_id)
            recipient = setting.email if setting else ""
            if channel == "telegram":
                if user.role != "admin":
                    return {"status": "skipped", "detail": "普通账号不使用 Telegram"}
                settings = self.settings_reader()
                state = channel_status(settings)
            else:
                settings = self.email_settings_reader() | {"recipient": recipient}
                verified_at = setting.email_verified_at if setting else None
                if rule_id is None and user.role == "user" and not verified_at:
                    raise HTTPException(403, "请先验证个人邮箱，再发送测试消息")
                state = email_status(settings, recipient, verified_at)
            previous = session.scalar(select(PushLog).where(PushLog.user_id == user_id,
                PushLog.rule_id == rule_id, PushLog.code == code, PushLog.send_day == at.date(), PushLog.channel == channel)) if rule_id else None
            if previous and previous.status not in {"pending_configuration", "pending_verification"}:
                return {"status": "deduplicated", "channel": channel, "detail": "该用户该规则该通道今天已有发送尝试，跳过"}
            if rule_id is not None:
                # 在领取额度的事务内重读报价、重算条件和文案，不信任先前评估快照。
                points = matching_points(session, rule, at.date())
                hit, reason = evaluate(rule, points, at.date())
                if not hit:
                    return {"status": "skipped", "detail": reason}
                point = points[0]
                text = (f"采购行情 · 现货提醒 #{rule.id}\n{COMMODITY_MAP[rule.code].name}\n{reason}\n"
                        f"现价：{point.price} {rule.unit}\n报价日期：{point.quote_date}\n口径：{rule.quote_basis}\n"
                        f"来源：{point.source_name}\n{point.source_url}")
            # 等写锁、读配置等耗时不能让旧日评估在新日领取额度。
            if clock().date() != at.date():
                return {"status": "skipped", "detail": "检查已跨北京时间自然日，停止旧轮；下轮重新检查"}
            if channel == "email" and rule_id is None and state["status"] == "configured":
                # 收件地址和混合邮件配额在同一事务读取、领取，改邮箱不能绕过限制。
                reserve_email_limits(session, user_id, client_ip, recipient)
            log = previous or PushLog(user_id=user_id, channel=channel, rule_id=rule_id, code=code,
                send_day=at.date(), kind="rule" if rule_id else "test", created_at=at.replace(tzinfo=None))
            log.recipient = recipient if channel == "email" else None
            log.message = text
            log.status = "sending" if state["status"] == "configured" else state["status"]
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
                if clock().date() != at.date():
                    status, detail, provider_id = "skipped", "检查已跨北京时间自然日，取消旧日报价发送；下轮重新检查", None
                else:
                    sender = self.sender if channel == "telegram" else self.email_sender
                    status, detail, provider_id = sender.send(text, settings)
            except Exception:
                status, detail, provider_id = "unknown", "通道异常，送达未知；本日不再发送该规则", None
            with self.session_factory() as session:
                log = session.get(PushLog, log_id)
                if log is None or log.user_id != user_id or log.channel != channel:
                    return {"status": "skipped", "detail": "账号及日志已删除"}
                log.status, log.detail = status, detail
                if channel == "telegram":
                    log.telegram_message_id = provider_id
                else:
                    log.provider_message_id = provider_id
                log.finished_at = clock().replace(tzinfo=None)
                session.commit()
                return log_dict(log)
        with self.session_factory() as session:
            log = session.get(PushLog, log_id)
            if log is None or log.user_id != user_id or log.channel != channel:
                return {"status": "skipped", "detail": "账号及日志已删除"}
            log.finished_at = clock().replace(tzinfo=None)
            session.commit()
            return log_dict(log)

    def test(self, *, user_id, client_ip="internal"):
        results = [self.deliver(f"采购行情 · 通道测试\n时间：{self.clock().isoformat(timespec='seconds')}\n这是一条测试消息，不代表行情规则触发。", user_id=user_id, channel=channel, client_ip=client_ip)
                   for channel in self.channels(user_id)]
        return results[0] if len(results) == 1 else {"results": results}

    def run(self, *, at=None, user_id=None):
        # 定时任务检查全部有效账号；个人 API 必须传入认证账号。
        clock = (lambda: at.astimezone(TIMEZONE)) if at is not None else self.clock
        day = clock().date()
        results = []
        with self.session_factory() as session:
            query = select(AlertRule.id, AlertRule.user_id).join(User, User.id == AlertRule.user_id).where(AlertRule.enabled.is_(True), User.enabled.is_(True))
            if user_id is not None:
                query = query.where(AlertRule.user_id == user_id)
            rules = session.execute(query.order_by(AlertRule.id)).all()
        for rule_id, owner_id in rules:
            if clock().date() != day:
                results.append({"rule_id": rule_id, "user_id": owner_id, "status": "skipped", "detail": "检查已跨北京时间自然日，停止旧轮；下轮重新检查"})
                break
            # 初筛仅用于返回跳过原因；短会话在发送前结束，最终条件由 deliver 再验。
            with self.session_factory() as session:
                rule = session.get(AlertRule, rule_id)
                if rule is None or not rule.enabled:
                    continue
                hit, reason = evaluate(rule, matching_points(session, rule, day), day)
            if not hit:
                results.append({"rule_id": rule_id, "user_id": owner_id, "status": "skipped", "detail": reason})
                continue
            for channel in self.channels(owner_id):
                result = self.deliver("", user_id=owner_id, channel=channel, rule_id=rule_id, expected_day=day, clock=clock)
                results.append({"rule_id": rule_id, "user_id": owner_id, "channel": channel} | result)
        return {"channel": self.status(user_id) if user_id else None, "results": results}
