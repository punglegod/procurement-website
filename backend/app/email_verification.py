"""邮箱所有权确认：验证码只存摘要、绑定账号与当前地址，限次且一次性消费。"""
from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from fastapi import HTTPException

from .models import PushLog, PushSetting, User
from .config import TIMEZONE
from .security import reserve_email_limits, reserve_limits

VERIFICATION_SECONDS = 900
VERIFICATION_ATTEMPTS = 5


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def code_digest(user_id, email, salt, code):
    return hashlib.sha256(f"{user_id}\0{email}\0{salt}\0{code}".encode()).hexdigest()


def available_user(session, user_id):
    user = session.get(User, user_id)
    if not user or not user.enabled:
        raise HTTPException(401, "账号不可用，请重新登录")
    return user


def clear_challenge(setting):
    setting.verification_hash = setting.verification_salt = setting.verification_expires_at = None
    setting.verification_attempts = 0


def request_verification(service, user_id, client_ip):
    settings = service.email_settings_reader()
    at = utc_now()
    with service.session_factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        available_user(session, user_id)
        setting = session.get(PushSetting, user_id)
        if setting is None or not setting.email:
            raise HTTPException(400, "请先保存个人邮箱")
        if setting.email_verified_at is not None:
            raise HTTPException(400, "当前邮箱已经验证")
        reserve_email_limits(session, user_id, client_ip, setting.email, at=at)
        if not settings.get("RESEND_API_KEY") or not settings.get("EMAIL_FROM"):
            session.commit()
            return {"status": "pending_configuration", "message": "邮件服务待配置，请联系站长"}
        code = f"{secrets.randbelow(100_000_000):08d}"
        salt = secrets.token_hex(16)
        setting.verification_hash = code_digest(user_id, setting.email, salt, code)
        setting.verification_salt = salt
        setting.verification_expires_at = at + timedelta(seconds=VERIFICATION_SECONDS)
        setting.verification_attempts = 0
        recipient = setting.email
        local_at = at.replace(tzinfo=timezone.utc).astimezone(TIMEZONE)
        log = PushLog(user_id=user_id, channel="email", recipient=recipient, kind="verification",
                      send_day=local_at.date(), created_at=local_at.replace(tzinfo=None), status="sending",
                      message="邮箱所有权验证（验证码不记录）", detail="发送处理中，结果未确认")
        session.add(log)
        session.commit()
        log_id = log.id
    # 不在写事务内调用外部服务；代码只进入收件邮件，不进入响应或任何日志。
    text = f"采购行情 · 邮箱验证\n验证码：{code}\n15 分钟内有效，仅限当前登录账号。请勿转发给他人。\n如果不是本人操作，请忽略此邮件。"
    try:
        status, detail, provider_id = service.email_sender.send(text, settings | {"recipient": recipient, "subject": "采购行情 · 邮箱验证"})
    except Exception:
        status, detail, provider_id = "unknown", "邮件通道异常，是否送达未知，请检查邮箱", None
    with service.session_factory() as session:
        log = session.get(PushLog, log_id)
        if log is not None and log.user_id == user_id and log.channel == "email" and log.kind == "verification":
            log.status, log.detail, log.provider_message_id = status, detail, provider_id
            log.finished_at = datetime.now(TIMEZONE).replace(tzinfo=None)
            session.commit()
    return {"status": status, "message": detail}


def confirm_verification(service, user_id, client_ip, code):
    at = utc_now()
    with service.session_factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        available_user(session, user_id)
        reserve_limits(session, [("email_confirm_user", str(user_id), 10, 900),
                                 ("email_confirm_ip", client_ip, 30, 900)], at=at)
        setting = session.get(PushSetting, user_id)
        if (setting is None or not setting.verification_hash or setting.verification_expires_at <= at):
            if setting is not None:
                clear_challenge(setting)
            session.commit()
            raise HTTPException(400, "验证码无效或已过期，请重新发送")
        expected = code_digest(user_id, setting.email, setting.verification_salt, code)
        if not secrets.compare_digest(setting.verification_hash, expected):
            setting.verification_attempts += 1
            exhausted = setting.verification_attempts >= VERIFICATION_ATTEMPTS
            if exhausted:
                clear_challenge(setting)
            session.commit()
            raise HTTPException(429 if exhausted else 400, "验证次数过多，请重新发送验证码" if exhausted else "验证码错误",
                                headers={"Retry-After": "900"} if exhausted else None)
        setting.email_verified_at = at
        clear_challenge(setting)
        session.commit()
