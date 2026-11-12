"""账号、持久化会话、登录节流及管理员权限。"""

from datetime import datetime, timedelta, timezone
import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .config import read_account_settings
from .db import SessionLocal
from .models import AlertRule, LoginThrottle, PushLog, PushSetting, SiteSetting, User, UserSession

hasher = PasswordHasher()
COOKIE = "procurement_session"
router = APIRouter()


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password):
    return hasher.hash(password)


def verify_password(encoded, password):
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


def throttle(session, request, username):
    # 不信任 X-Forwarded-For；同时按真实客户端地址和账号持久节流。
    keys = [digest("ip:" + (request.client.host if request.client else "unknown")), digest("user:"+username)]
    rows = []
    for key in keys:
        row = session.get(LoginThrottle, key)
        if row and row.reset_at > utc_now() and row.failures >= 5:
            raise HTTPException(429, "失败次数过多，请 15 分钟后重试", headers={"Retry-After": "900"})
        if row is None:
            row = LoginThrottle(key=key, failures=0, reset_at=utc_now()+timedelta(minutes=15))
            session.add(row)
        elif row.reset_at <= utc_now():
            row.failures, row.reset_at = 0, utc_now()+timedelta(minutes=15)
        rows.append(row)
    return rows


@router.post("/api/auth/register", status_code=201)
def register(data: Credentials):
    with SessionLocal() as session:
        # 与管理员关闭注册串行，避免并发越过注册开关。
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        if session.get(SiteSetting, "registration_enabled").value != "true":
            raise HTTPException(403, "注册已关闭")
        user = User(username=data.username, password_hash=hash_password(data.password), role="user", enabled=True, created_at=utc_now())
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            raise HTTPException(409, "账号已存在") from None
        return user_dict(user)


@router.post("/api/auth/login")
def login(data: Credentials, request: Request, response: Response):
    with SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        attempts = throttle(session, request, data.username)
        user = session.scalar(select(User).where(User.username == data.username))
        valid = verify_password(user.password_hash, data.password) if user else verify_password(DUMMY_HASH, data.password)
        if not valid or not user or not user.enabled:
            for row in attempts:
                row.failures += 1
            session.commit()
            raise HTTPException(401, "账号或密码错误，或账号已禁用")
        for row in attempts:
            row.failures = 0
        # 登录轮换 Cookie，对当前浏览器的旧会话进行撤销。
        session.execute(delete(UserSession).where(UserSession.token_hash == digest(request.cookies.get(COOKIE, ""))))
        create_session(session, response, user)
        session.commit()
        return user_dict(user)


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
    with SessionLocal() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        attempts = throttle(session, request, user.username)
        account = session.get(User, user.id)
        if not verify_password(account.password_hash, data.old_password):
            for row in attempts:
                row.failures += 1
            session.commit()
            raise HTTPException(400, "原密码错误")
        account.password_hash = hash_password(data.new_password)
        session.execute(delete(UserSession).where(UserSession.user_id == user.id))
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
