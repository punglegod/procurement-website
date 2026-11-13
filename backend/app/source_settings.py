"""管理员数据源配置；后台凭据整包认证加密，读接口只给状态和掩码。"""
import json
import logging
import hashlib
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import select

from . import config
from .adapters.common import now
from .adapters.sci99 import STATE_MESSAGES, Sci99Stopped
from .models import SiteSetting, SourceState, QuoteImport
from .security import reserve_limits

logger = logging.getLogger(__name__)
KEY = "sci99_credentials"
AUTH_STATE_SETTING = "sci99_verification"
FIELDS = {"username": "SCI99_USER", "password": "SCI99_PASS", "cookie": "SCI99_COOKIE"}


def _revision(session):
    # 绑定密文版本及根配置文件版本；只存摘要，不保存/返回明文凭据。
    row = session.get(SiteSetting, KEY)
    path = config.ROOT / ".env"
    stamp = (path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else None
    return hashlib.sha256(json.dumps([row.value if row else None, stamp]).encode()).hexdigest()


def _auth_state(session):
    row = session.get(SiteSetting, AUTH_STATE_SETTING)
    try:
        value = json.loads(row.value) if row else {}
        if value.get("revision") == _revision(session) and value.get("status") in STATE_MESSAGES:
            # 时间只能来自本机，不传回存储中任意字符串。
            checked = datetime.fromisoformat(value["checked_at"]).isoformat()
            status = value["status"]
            return status, checked
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
    return None, None


class Sci99Input(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    username: SecretStr | None = None
    password: SecretStr | None = None
    cookie: SecretStr | None = None
    clear_credentials: bool = False

    @field_validator("username", "password", "cookie")
    @classmethod
    def bounded(cls, value, info):
        if value is not None and len(value.get_secret_value()) > (8192 if info.field_name == "cookie" else 254):
            raise ValueError("凭据字段超过长度限制")
        return value


def cipher():
    try:
        value = config.read_settings_encryption_key()
        return Fernet(value.encode()) if value else None
    except (ValueError, UnicodeError):
        return None


def warn_encryption_configuration():
    if cipher() is None:
        logger.warning("安全配置警告：SETTINGS_ENCRYPTION_KEY 未配置或无效，禁止保存后台卓创凭据；不会自动生成或写入 .env")


class SourceSettings:
    def __init__(self, factory, legacy_reader=None):
        self.factory = factory
        self.legacy_reader = legacy_reader or config.read_sci99_settings

    def resolve(self, session=None):
        if session is None:
            with self.factory() as owned:
                return self.resolve(owned)
        legacy = {key: "" for key in (*FIELDS.values(), "SCI99_ABS_URL", "SCI99_LOGIN_URL")} | self.legacy_reader()
        row = session.get(SiteSetting, KEY)
        enabled = session.get(SiteSetting, "sci99_enabled")
        enabled = enabled.value == "true" if enabled else config.SOURCES["sci99"].get("enabled", False)
        if row:
            crypt = cipher()
            try:
                if crypt is None:
                    raise InvalidToken
                stored = json.loads(crypt.decrypt(row.value.encode()).decode())
                legacy.update({key: stored.get(key, "") for key in FIELDS.values()})
            except (InvalidToken, ValueError, TypeError, AttributeError, UnicodeError):
                # 后台配置存在却不可解密时不能悄悄改用另一套 .env 账号。
                raise HTTPException(503, "后台卓创配置不可解密，请恢复正确密钥或清除后台凭据") from None
        return legacy, bool(enabled), bool(row)

    def resolve_for_verification(self):
        with self.factory() as session:
            settings, _, _ = self.resolve(session)
            return settings, _revision(session)

    def record_auth_result(self, status, revision):
        if status not in STATE_MESSAGES or status in {"quote_pending", "pending_credentials"}:
            return
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if revision == _revision(session):
                session.merge(SiteSetting(key=AUTH_STATE_SETTING, value=json.dumps({
                    "status": status, "checked_at": now().isoformat(timespec="seconds"), "revision": revision})))
                session.commit()

    def state(self):
        with self.factory() as session:
            state = session.get(SourceState, "sci99")
            auth_status, checked_at = _auth_state(session)
            try:
                settings, enabled, backend = self.resolve(session)
                configured = config.sci99_configured(settings)
                status = "enabled" if configured and enabled else "configured_disabled" if configured else "pending_credentials"
                message = "已启用（仅09:00轮全量抓取；每账号自选口径）" if status == "enabled" else "已配置未启用" if configured else "未配置（待配置凭据）"
                mode = "cookie" if settings.get("SCI99_COOKIE") else "password" if configured else "none"
                if not configured:
                    auth_status, checked_at = "pending_credentials", None
                elif auth_status is None:
                    auth_status = "cookie_unconfirmed" if mode == "cookie" else "login_unconfirmed"
                username = settings.get("SCI99_USER", "")
                masks = {"username_mask": username[:2] + "••••" + username[-2:] if len(username) > 4 else "••••" if username else "",
                         "password_mask": "••••" if settings.get("SCI99_PASS") else "",
                         "cookie_mask": "••••" if settings.get("SCI99_COOKIE") else ""}
            except HTTPException:
                enabled, backend, configured = False, True, False
                status, message, masks = "configuration_error", "后台配置不可解密，请恢复密钥或清除后台凭据", {}
                mode, auth_status, checked_at = "none", "pending_credentials", None
            auth_message = STATE_MESSAGES[auth_status]
            message += "；" + auth_message
            latest_import = session.scalar(select(QuoteImport).where(QuoteImport.source == "sci99").order_by(QuoteImport.id.desc()).limit(1))
            quote_status, quote_message = "quote_pending", STATE_MESSAGES["quote_pending"]
            if latest_import:
                imports = list(session.scalars(select(QuoteImport).where(QuoteImport.source == "sci99", QuoteImport.fetched_at == latest_import.fetched_at).order_by(QuoteImport.id)))
                passed = sum(row.status == "ok" for row in imports)
                quote_status = "ok" if passed == len(imports) == 10 else "partial" if passed else "failed"
                quote_message = f"最近一次全量采集：成功{passed}/10品种；" + "；".join(f"{row.code} {row.count}条（{row.status}）" for row in imports)
            return {"enabled": enabled, "configured": configured, "backend_configured": backend,
                    "key_ready": cipher() is not None, "status": status, "message": message, **masks,
                    "last_result": state.message if state else "尚未测试或抓取",
                    "last_result_status": state.status if state else "never_run",
                    "auth_mode": mode, "auth_status": auth_status, "auth_message": auth_message,
                    "auth_checked_at": checked_at,
                    "verified_at": checked_at if auth_status in {"cookie_valid", "password_valid"} else None,
                    "quote_status": quote_status, "quote_message": quote_message,
                    "last_attempt_at": state.last_attempt_at.isoformat() + "+08:00" if state and state.last_attempt_at else None}

    def save(self, data):
        values = {target: (getattr(data, field).get_secret_value() if field == "password" else getattr(data, field).get_secret_value().strip()) for field, target in FIELDS.items()
                  if getattr(data, field) is not None}
        crypt = cipher()
        if values and crypt is None:
            raise HTTPException(503, "未配置有效 SETTINGS_ENCRYPTION_KEY，禁止保存凭据")
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if data.clear_credentials:
                row = session.get(SiteSetting, KEY)
                if row:
                    session.delete(row)
                    session.flush()
            if values:
                current, _, _ = self.resolve(session)
                stored = {key: current.get(key, "") for key in FIELDS.values()} | values
                session.merge(SiteSetting(key=KEY, value=crypt.encrypt(json.dumps(stored).encode()).decode()))
                session.flush()
            if values or data.clear_credentials:
                row = session.get(SiteSetting, AUTH_STATE_SETTING)
                if row:
                    session.delete(row)
                state = session.get(SourceState, "sci99")
                if state:
                    state.status, state.message = "never_run", "凭据已变更，请重新测试连接"
            if data.enabled:
                current, _, _ = self.resolve(session)
                if not config.sci99_configured(current):
                    raise HTTPException(400, "请先配置账号与密码或 Cookie，再启用")
            session.merge(SiteSetting(key="sci99_enabled", value="true" if data.enabled else "false"))
            session.commit()
        return self.state()

    def test_connection(self, user_id, client_ip, adapter_factory=None):
        from .adapters.sci99 import Sci99Adapter
        settings, _, _ = self.resolve()
        if not config.sci99_configured(settings):
            return {"status": "pending_credentials", "message": "未配置，未执行网络请求"}
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            settings, _, _ = self.resolve(session)
            if not config.sci99_configured(settings):
                return {"status": "pending_credentials", "message": "未配置，未执行网络请求"}
            revision = _revision(session)
            reserve_limits(session, [("sci99_test_user", str(user_id), 1, 900),
                                     ("sci99_test_ip", client_ip, 2, 900), ("sci99_test_global", "all", 4, 86400)])
            session.commit()
        try:
            status = (adapter_factory or Sci99Adapter)(settings=settings).test_connection()
            if status not in {"cookie_valid", "password_valid"}:
                status = "cookie_unconfirmed" if settings.get("SCI99_COOKIE") else "login_unconfirmed"
            message = STATE_MESSAGES[status]
        except Exception as error:
            from .adapters.common import AccessStopped
            status = error.status if isinstance(error, Sci99Stopped) else "blocked" if isinstance(error, AccessStopped) else "cookie_unconfirmed" if settings.get("SCI99_COOKIE") else "login_unconfirmed"
            message = STATE_MESSAGES.get(status, "登录结果未确认，已停止；请人工检查官网")
        with self.factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            if revision != _revision(session):
                return {"status": "configuration_changed", "message": "验证期间凭据已变更，请重新测试当前配置"}
            state = session.get(SourceState, "sci99")
            state.status, state.message, state.last_attempt_at = status, message, now().replace(tzinfo=None)
            checked_at = now().isoformat(timespec="seconds")
            auth_status = status if status in STATE_MESSAGES else "cookie_unconfirmed" if settings.get("SCI99_COOKIE") else "login_unconfirmed"
            session.merge(SiteSetting(key=AUTH_STATE_SETTING, value=json.dumps({"status": auth_status, "checked_at": checked_at, "revision": revision})))
            session.commit()
        return {"status": status, "message": message, "checked_at": checked_at,
                "verified_at": checked_at if status in {"cookie_valid", "password_valid"} else None}


def progress_enabled(session):
    setting = session.get(SiteSetting, "fetch_progress_enabled")
    return setting is None or setting.value == "true"
