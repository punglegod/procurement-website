"""管理员数据源配置；后台凭据整包认证加密，读接口只给状态和掩码。"""
import json
import logging

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from sqlalchemy import select

from . import config
from .adapters.common import now
from .models import SiteSetting, SourceState
from .security import reserve_limits

logger = logging.getLogger(__name__)
KEY = "sci99_credentials"
FIELDS = {"username": "SCI99_USER", "password": "SCI99_PASS", "cookie": "SCI99_COOKIE"}


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

    def state(self):
        with self.factory() as session:
            state = session.get(SourceState, "sci99")
            try:
                settings, enabled, backend = self.resolve(session)
                configured = config.sci99_configured(settings)
                status = "enabled" if configured and enabled else "configured_disabled" if configured else "pending_credentials"
                message = "已启用（仅09:00轮；当前保留采集框架，不替换免费口径）" if status == "enabled" else "已配置未启用" if configured else "未配置（待配置凭据）"
                username = settings.get("SCI99_USER", "")
                masks = {"username_mask": username[:2] + "••••" + username[-2:] if len(username) > 4 else "••••" if username else "",
                         "password_mask": "••••" if settings.get("SCI99_PASS") else "",
                         "cookie_mask": "••••" if settings.get("SCI99_COOKIE") else ""}
            except HTTPException:
                enabled, backend, configured = False, True, False
                status, message, masks = "configuration_error", "后台配置不可解密，请恢复密钥或清除后台凭据", {}
            return {"enabled": enabled, "configured": configured, "backend_configured": backend,
                    "key_ready": cipher() is not None, "status": status, "message": message, **masks,
                    "last_result": state.message if state else "尚未测试或抓取",
                    "last_result_status": state.status if state else "never_run",
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
            reserve_limits(session, [("sci99_test_user", str(user_id), 1, 900),
                                     ("sci99_test_ip", client_ip, 2, 900), ("sci99_test_global", "all", 4, 86400)])
            session.commit()
        try:
            (adapter_factory or Sci99Adapter)(settings=settings).test_connection()
            status, message = "ok", "登录可用性已确认，未批量抓取行情"
        except Exception as error:
            from .adapters.common import AccessStopped
            status = "blocked" if isinstance(error, AccessStopped) else "failed"
            message = "登录异常、验证码或风控，已停止；请人工检查官网" if status == "blocked" else "测试未确认成功，请检查配置及官网登录方式"
        with self.factory() as session:
            state = session.get(SourceState, "sci99")
            state.status, state.message, state.last_attempt_at = status, message, now().replace(tzinfo=None)
            session.commit()
        return {"status": status, "message": message}


def progress_enabled(session):
    setting = session.get(SiteSetting, "fetch_progress_enabled")
    return setting is None or setting.value == "true"
