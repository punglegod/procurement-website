"""账号、持久化会话、登录节流及管理员权限。"""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import hashlib
import math
import re
import secrets
from threading import BoundedSemaphore
from time import sleep

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import read_account_settings
from .db import SessionLocal
from .models import AlertRule, LoginThrottle, PushLog, PushSetting, SecurityEvent, SiteSetting, User, UserSession
from .security import reserve_limits

hasher = PasswordHasher()
COOKIE = "procurement_session"
router = APIRouter()
PASSWORD_SLOTS = BoundedSemaphore(2)
REGISTER_IP_HOURLY = 5
REGISTER_GLOBAL_HOURLY = 50
REGISTER_IP_DAILY = 20
REGISTER_GLOBAL_DAILY = 200
AUTH_IP_PER_MINUTE = 30
AUTH_GLOBAL_PER_MINUTE = 120
AUTH_FAILURE_LIMIT = 5
AUTH_FAILURE_WINDOW = 900
AUTH_FAILURE_DELAYS = (0.25, 0.5, 1.0, 2.0, 4.0)


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def password_slot():
    # 不排队占用工作线程，也不允许并发 Argon2 将内存耗尽。
    if not PASSWORD_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "密码验证繁忙，请稍后重试", headers={"Retry-After": "1"})
    try:
        yield
    finally:
        PASSWORD_SLOTS.release()


def hash_password(password):
    with password_slot():
        return hasher.hash(password)


def verify_password(encoded, password):
    with password_slot():
        try:
            return hasher.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False


def validate_username(value):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{3,64}", value):
        raise ValueError("账号须为 3～64 位英文字母、数字、下划线或连字符")
    return value


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str = Field(min_length=8, max_length=128)
    _username = field_validator("username")(validate_username)


class PasswordInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def user_dict(user):
    return {key: getattr(user, key) for key in ("id", "username", "role", "enabled")}


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def resolve_user(request):
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 200:
        return None
    with SessionLocal() as session:
        record = session.get(UserSession, digest(token))
        if record and record.expires_at > utc_now():
            user = session.get(User, record.user_id)
            if user and user.enabled:
                return user
    return None


def current_user(request: Request):
    user = getattr(request.state, "user", None) or resolve_user(request)
    if user is None:
        raise HTTPException(401, "请先登录")
    return user


def admin_user(user=Depends(current_user)):
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可操作")
    return user


def create_session(session, response, user):
    token = secrets.token_urlsafe(32)
    session.execute(delete(UserSession).where(UserSession.expires_at <= utc_now()))
    session.add(UserSession(token_hash=digest(token), user_id=user.id, expires_at=utc_now()+timedelta(days=7)))
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=read_account_settings()["SESSION_COOKIE_SECURE"].lower() == "true", max_age=604800, path="/")


def client_key(request):
    # Uvicorn 仅在实际对端受信时用 XFF 重写 ASGI client；这里不自行采信任何转发头。
    # 公开来源配置只用于同源校验，不能替代代理信任和真实 IP 透传。
    return digest("ip:" + (request.client.host if request.client else "unknown"))


def record_security_event(session, kind, request, username, *, user_id=None, detail=""):
    session.add(SecurityEvent(created_at=utc_now(), kind=kind, user_id=user_id,
                              account_hash=digest(username), client_hash=client_key(request), detail=detail))


def log_security_event(kind, request, username, *, user_id=None, detail=""):
    # 被拒请求不能无限放大安全日志；日志额度不影响实际业务限流。
    with SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            reserve_limits(session, [("security_log_ip", client_key(request), 20, 60),
                                     ("security_log_global", "all", 200, 60)], at=utc_now())
        except HTTPException as error:
            if error.status_code == 429:
                return
            raise
        record_security_event(session, kind, request, username, user_id=user_id, detail=detail)
        session.commit()


def throttle(session, request, username, purpose="login"):
    # 新键只绑定来源和账号组合，忽略旧版全账号锁，陌生来源不能硬锁账号。
    key = digest("pair:" + purpose + ":" + client_key(request) + ":" + username)
    at = utc_now()
    row = session.get(LoginThrottle, key)
    if row and row.reset_at > at and row.failures >= AUTH_FAILURE_LIMIT:
        remaining = max(1, math.ceil((row.reset_at - at).total_seconds()))
        raise HTTPException(429, "此来源对此账号失败过多，请稍后重试", headers={"Retry-After": str(remaining)})
    if row is None:
        row = LoginThrottle(key=key, failures=0, reset_at=at+timedelta(seconds=AUTH_FAILURE_WINDOW))
        session.add(row)
    elif row.reset_at <= at:
        row.failures, row.reset_at = 0, at+timedelta(seconds=AUTH_FAILURE_WINDOW)
    # 在密码计算前预占失败/在途次数，防止并发错误请求绕过五次限制。
    row.failures += 1
    return row


def reserve_auth_attempt(request, username, purpose):
    try:
        with SessionLocal() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reserve_limits(session, [(purpose + "_ip", client_key(request), AUTH_IP_PER_MINUTE, 60),
                                     (purpose + "_global", "all", AUTH_GLOBAL_PER_MINUTE, 60)], at=utc_now())
            attempt = throttle(session, request, username, purpose)
            account = session.scalar(select(User).where(User.username == username))
            snapshot = (account.id, account.password_hash) if account else (None, DUMMY_HASH)
            key, failures = attempt.key, attempt.failures
            session.commit()
        return snapshot, key, failures
    except HTTPException as error:
        if error.status_code == 429:
            log_security_event(purpose + "_limited", request, username, detail="来源或账号组合限流")
        raise


def failed_authentication(request, username, purpose, failures, status, detail, user_id=None):
    log_security_event(purpose + "_failed", request, username, user_id=user_id, detail="凭据不匹配或账号不可用")
    # 哈希槽和数据库连接已释放，再做渐进等待。
    sleep(AUTH_FAILURE_DELAYS[min(failures - 1, len(AUTH_FAILURE_DELAYS) - 1)])
    raise HTTPException(status, detail)


@router.post("/api/auth/register", status_code=201)
def register(data: Credentials, request: Request):
    try:
        with SessionLocal() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            reserve_limits(session, [
                ("register_ip_hour", client_key(request), REGISTER_IP_HOURLY, 3600),
                ("register_global_hour", "all", REGISTER_GLOBAL_HOURLY, 3600),
                ("register_ip_day", client_key(request), REGISTER_IP_DAILY, 86400),
                ("register_global_day", "all", REGISTER_GLOBAL_DAILY, 86400),
            ], at=utc_now())
            session.commit()
    except HTTPException as error:
        if error.status_code == 429:
            log_security_event("register_limited", request, data.username, detail="注册来源或全站限流")
        raise
    with SessionLocal() as session:
        if session.get(SiteSetting, "registration_enabled").value != "true":
            raise HTTPException(403, "注册已关闭")
    # 已存在和新用户名均计算哈希，不用快速失败泄露存在性；哈希仍在写事务外。
    try:
        encoded = hash_password(data.password)
    except HTTPException:
        log_security_event("register_busy", request, data.username, detail="密码计算并发额度已满")
        raise
    with SessionLocal() as session:
        # 哈希期间管理员可能关注册，必须在最终写入事务里重新检查。
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if session.get(SiteSetting, "registration_enabled").value != "true":
            raise HTTPException(403, "注册已关闭")
        user = User(username=data.username, password_hash=encoded, role="user", enabled=True, created_at=utc_now())
        session.add(user)
        try:
            session.flush()
            record_security_event(session, "register_success", request, data.username, user_id=user.id)
            session.commit()
        except IntegrityError:
            session.rollback()
            log_security_event("register_existing", request, data.username, detail="注册请求未新建账号")
        # 不返回账号编号/角色，不建会话；客户端统一引导登录，绝不覆盖旧账号密码。
        return {"message": "注册请求已处理。请使用所填账号和密码登录；无法登录时请更换账号名或联系管理员。"}


@router.post("/api/auth/login")
def login(data: Credentials, request: Request, response: Response):
    (account_id, encoded), attempt_key, failures = reserve_auth_attempt(request, data.username, "login")
    try:
        valid = verify_password(encoded, data.password)
    except HTTPException:
        log_security_event("login_busy", request, data.username, detail="密码计算并发额度已满")
        raise
    if not valid or account_id is None:
        failed_authentication(request, data.username, "login", failures, 401, "账号或密码错误，或账号已禁用", account_id)
    with SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        user = session.get(User, account_id)
        # 校验期间的改密、禁用、删除立即生效，不能用旧快照新建会话。
        if user and user.enabled and user.password_hash == encoded:
            session.get(LoginThrottle, attempt_key).failures = 0
            session.execute(delete(UserSession).where(UserSession.token_hash == digest(request.cookies.get(COOKIE, ""))))
            create_session(session, response, user)
            record_security_event(session, "login_success", request, data.username, user_id=user.id)
            session.commit()
            return user_dict(user)
    failed_authentication(request, data.username, "login", failures, 401, "账号或密码错误，或账号已禁用", account_id)


DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


@router.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user_dict(user)


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, user=Depends(current_user)):
    with SessionLocal() as session:
        session.execute(delete(UserSession).where(UserSession.token_hash == digest(request.cookies.get(COOKIE, ""))))
        session.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"logged_out": True}


@router.post("/api/auth/password")
def password_change(data: PasswordInput, request: Request, response: Response, user=Depends(current_user)):
    (account_id, encoded), attempt_key, failures = reserve_auth_attempt(request, user.username, "password")
    try:
        valid = verify_password(encoded, data.old_password)
    except HTTPException:
        log_security_event("password_busy", request, user.username, user_id=user.id, detail="密码计算并发额度已满")
        raise
    if not valid or account_id != user.id:
        failed_authentication(request, user.username, "password", failures, 400, "原密码错误", user.id)
    try:
        replacement = hash_password(data.new_password)
    except HTTPException:
        log_security_event("password_busy", request, user.username, user_id=user.id, detail="密码计算并发额度已满")
        raise
    with SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        account = session.get(User, user.id)
        current_session = session.get(UserSession, digest(request.cookies.get(COOKIE, "")))
        if not account or not account.enabled or account.password_hash != encoded or not current_session or current_session.user_id != user.id or current_session.expires_at <= utc_now():
            record_security_event(session, "password_stale", request, user.username, user_id=user.id, detail="验证期间账号或会话已变更")
            session.commit()
            raise HTTPException(401, "账号或会话已变更，请重新登录")
        account.password_hash = replacement
        session.get(LoginThrottle, attempt_key).failures = 0
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
        record_security_event(session, "password_success", request, user.username, user_id=user.id)
        session.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"changed": True, "message": "密码已修改，请重新登录；全部旧会话已失效"}


@router.get("/api/admin/users")
def users(user=Depends(admin_user)):
    with SessionLocal() as session:
        return {"items": [user_dict(u) for u in session.scalars(select(User).order_by(User.id))], "registration_enabled": session.get(SiteSetting, "registration_enabled").value == "true"}


class EnabledInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


@router.put("/api/admin/registration")
def registration(data: EnabledInput, user=Depends(admin_user)):
    with SessionLocal() as session:
        session.get(SiteSetting, "registration_enabled").value = "true" if data.enabled else "false"
        session.commit()
    return {"registration_enabled": data.enabled}


def managed_user(session, user_id):
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(404, "账号不存在")
    if target.role == "admin":
        raise HTTPException(403, "管理员账号受保护，不可禁用或删除")
    return target


@router.put("/api/admin/users/{user_id}")
def set_enabled(user_id: int, data: EnabledInput, user=Depends(admin_user)):
    with SessionLocal() as session:
        target = managed_user(session, user_id)
        target.enabled = data.enabled
        if not data.enabled:
            session.execute(delete(UserSession).where(UserSession.user_id == user_id))
        session.commit()
        return user_dict(target)


@router.delete("/api/admin/users/{user_id}")
def delete_user(user_id: int, user=Depends(admin_user)):
    with SessionLocal() as session:
        target = managed_user(session, user_id)
        for model in (UserSession, AlertRule, PushLog, PushSetting):
            session.execute(delete(model).where(model.user_id == user_id))
        session.delete(target)
        session.commit()
    return {"deleted": user_id}
