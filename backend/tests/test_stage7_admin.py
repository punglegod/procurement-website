"""本机维护只在隔离库验证，不改真实 .env、不改系统权限。"""
import sqlite3

import pytest
from sqlalchemy import select, func

from backend.app import auth, config
from backend.app.models import LoginThrottle, RateLimitBucket, SecurityEvent, User, UserSession
from backend.scripts.manage_admin import maintain
from backend.tests.test_accounts import accounts, PASSWORD


def test_offline_replace_preserves_id_role_revokes_sessions_and_backs_up(accounts):
    admin, a, b, ids, factory, alerts = accounts
    new_password = "isolated-maintenance-password"
    (config.ROOT / ".env").write_text("ADMIN_USERNAME=renamed_admin\nADMIN_PASSWORD=" + new_password + "\n", encoding="utf-8")
    result = maintain(factory.kw["bind"], "replace", "test_admin")
    with factory() as session:
        user = session.get(User, 1)
        assert user.username == "renamed_admin" and user.role == "admin"
        assert auth.verify_password(user.password_hash, new_password)
        assert session.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == 1)) == 0
        assert session.scalar(select(func.count()).select_from(User).where(User.role == "admin")) == 1
        assert session.scalar(select(SecurityEvent).where(SecurityEvent.kind == "admin_local_replace"))
    with sqlite3.connect(result["backup"]) as connection:
        assert connection.execute("SELECT username FROM users WHERE id=1").fetchone()[0] == "test_admin"
    assert admin.get("/api/auth/me").status_code == 401


def test_offline_replace_rejects_collision_without_promoting_user(accounts):
    admin, a, b, ids, factory, alerts = accounts
    (config.ROOT / ".env").write_text("ADMIN_USERNAME=account_a\nADMIN_PASSWORD=" + PASSWORD + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="其他账号"):
        maintain(factory.kw["bind"], "replace", "test_admin")
    with factory() as session:
        assert session.get(User, 1).username == "test_admin"
        assert session.get(User, ids[0]).role == "user"


def test_offline_unlock_only_one_admin_source_preserves_global_budget(accounts):
    admin, a, b, ids, factory, alerts = accounts
    from datetime import timedelta
    ips = ["198.51.100.1", "198.51.100.2"]
    keys = [auth.digest("pair:login:" + auth.digest("ip:" + ip) + ":test_admin") for ip in ips]
    with factory() as session:
        for key in keys:
            session.add(LoginThrottle(key=key, failures=5, reset_at=auth.utc_now() + timedelta(minutes=15)))
        before = session.scalar(select(func.count()).select_from(RateLimitBucket))
        session.commit()
    maintain(factory.kw["bind"], "unlock", "test_admin", ips[0])
    with factory() as session:
        assert session.get(LoginThrottle, keys[0]) is None
        assert session.get(LoginThrottle, keys[1]).failures == 5
        assert session.scalar(select(func.count()).select_from(RateLimitBucket)) == before
