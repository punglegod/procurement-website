"""真实接口验证邮箱归属、一次性挑战、四维配额、并发和旧库安全迁移。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from backend.app import config, email_verification, main, security
from backend.app.db import initialize_database, make_engine
from backend.app.models import PushLog, PushSetting, RateLimitBucket
from backend.tests.test_accounts import accounts, mark_verified


def request_code(client, sender):
    response = client.post("/api/me/email-verification/request", json={})
    assert response.status_code == 200 and response.json()["status"] == "sent"
    text = sender.calls[-1]
    code = re.search(r"验证码：(\d{8})", text)[1]
    assert code not in response.text
    return code


def test_email_verification_lifecycle_and_secret_redaction(accounts):
    admin, a, b, ids, factory, alerts = accounts
    saved = a.put("/api/me/push-settings", json={"email": "verified@example.com"}).json()
    assert saved["email_verified"] is False and saved["channel"]["status"] == "pending_verification"
    assert a.post("/api/alerts/test").status_code == 403
    code = request_code(a, alerts.email_sender)
    with factory() as session:
        setting = session.get(PushSetting, ids[0])
        assert setting.verification_hash != code and len(setting.verification_hash) == 64
        assert setting.verification_expires_at > email_verification.utc_now()
        logs = list(session.scalars(select(PushLog)))
        assert all(code not in log.message and code not in log.detail for log in logs)
    assert code not in a.get("/api/alerts/logs").text
    confirmed = a.post("/api/me/email-verification/confirm", json={"code": code})
    assert confirmed.status_code == 200 and confirmed.json()["email_verified"] is True
    assert a.post("/api/me/email-verification/confirm", json={"code": code}).status_code == 400
    assert a.post("/api/alerts/test").json()["status"] == "sent"
    # 验证与测试共享收件地址额度；一封验证+两封测试以后再次测试被拒。
    assert a.post("/api/alerts/test").status_code == 200
    blocked = a.post("/api/alerts/test")
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    unchanged = a.put("/api/me/push-settings", json={"email": "verified@example.com"})
    assert unchanged.json()["email_verified"] is True
    changed = a.put("/api/me/push-settings", json={"email": "changed@example.com"})
    assert changed.json()["email_verified"] is False
    assert a.post("/api/alerts/test").status_code == 403
    assert a.post("/api/me/email-verification/confirm", json={"code": code}).status_code == 400
    assert len(alerts.email_sender.calls) == 3


def test_email_challenge_owner_resend_expiry_and_attempts(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    for client in (a, b):
        client.put("/api/me/push-settings", json={"email": "shared@example.com"})
    a_code = request_code(a, alerts.email_sender)
    b_code = request_code(b, alerts.email_sender)
    assert b.post("/api/me/email-verification/confirm", json={"code": a_code}).status_code == 400
    a_new = request_code(a, alerts.email_sender)
    assert a.post("/api/me/email-verification/confirm", json={"code": a_code}).status_code == 400
    assert a.post("/api/me/email-verification/confirm", json={"code": a_new}).json()["email_verified"] is True
    bad = "00000000" if b_code != "00000000" else "11111111"
    assert [b.post("/api/me/email-verification/confirm", json={"code": bad}).status_code for _ in range(4)] == [400, 400, 400, 429]
    assert b.post("/api/me/email-verification/confirm", json={"code": b_code}).status_code == 400
    # 地址变更撤销所有旧挑战；新地址获得的挑战过期后也不能使用。
    b.put("/api/me/push-settings", json={"email": "expiry@example.com"})
    expiring = request_code(b, alerts.email_sender)
    future = email_verification.utc_now() + timedelta(seconds=901)
    monkeypatch.setattr(email_verification, "utc_now", lambda: future)
    assert b.post("/api/me/email-verification/confirm", json={"code": expiring}).status_code == 400
    assert b.get("/api/me/push-settings").json()["email_verified"] is False


@pytest.mark.parametrize("dimension", ["account", "ip", "recipient", "global"])
def test_each_email_limit_dimension_cannot_be_rotated_away(accounts, monkeypatch, dimension):
    admin, a, b, ids, factory, alerts = accounts
    monkeypatch.setattr(security, "EMAIL_LIMITS", {key: ((2 if key == dimension else 100, 600),) for key in security.EMAIL_LIMITS})
    actors = [a, b, admin]
    statuses = []
    for index in range(3):
        actor = a if dimension == "account" else actors[index]
        address = "recipient@example.com" if dimension == "recipient" else f"limit-{index}@example.com"
        actor.put("/api/me/push-settings", json={"email": address})
        ip = "198.51.100.1" if dimension == "ip" else f"198.51.100.{index+1}"
        with TestClient(main.app, client=(ip, 40000)) as client:
            client.cookies.update(actor.cookies)
            statuses.append(client.post("/api/me/email-verification/request", json={}).status_code)
    assert statuses == [200, 200, 429]
    assert len(alerts.email_sender.calls) == 2
    print("STAGE7_EMAIL_LIMIT", dimension, statuses)


def test_verification_limit_atomic_and_persistent(accounts):
    admin, a, b, ids, factory, alerts = accounts
    a.put("/api/me/push-settings", json={"email": "race@example.com"})
    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(lambda _: a.post("/api/me/email-verification/request", json={}).status_code, range(6)))
    assert sorted(statuses) == [200, 200, 200, 429, 429, 429]
    assert len(alerts.email_sender.calls) == 3
    # 使用新服务对象和新连接仍受持久配额约束。
    from backend.app.alerts import AlertService
    replacement = AlertService(factory, email_sender=alerts.email_sender, email_settings_reader=alerts.email_settings_reader)
    with pytest.raises(Exception) as captured:
        email_verification.request_verification(replacement, ids[0], "changed-ip")
    assert captured.value.status_code == 429


def test_configuration_and_admin_telegram_remain_honest(accounts):
    admin, a, b, ids, factory, alerts = accounts
    alerts.email_settings_reader = lambda: {"RESEND_API_KEY": "", "EMAIL_FROM": "sourcing@bbbhl.com"}
    a.put("/api/me/push-settings", json={"email": "pending@example.com"})
    response = a.post("/api/me/email-verification/request", json={})
    assert response.status_code == 200 and response.json()["status"] == "pending_configuration"
    assert not alerts.email_sender.calls
    with factory() as session:
        assert session.get(PushSetting, ids[0]).verification_hash is None
    admin.put("/api/me/push-settings", json={"email": "admin@example.com"})
    result = admin.post("/api/alerts/test").json()["results"]
    assert result[0]["channel"] == "telegram" and result[0]["status"] == "sent"
    assert result[1]["status"] == "pending_configuration"
    assert len(alerts.sender.calls) == 1 and not alerts.email_sender.calls


def test_stage5_upgrade_backup_unverifies_old_addresses_and_is_idempotent(accounts, tmp_path, caplog):
    admin, a, b, ids, factory, alerts = accounts
    path = tmp_path / "stage5-upgrade.sqlite3"
    with sqlite3.connect(factory.kw["bind"].url.database) as source, sqlite3.connect(path) as target:
        source.backup(target)
    # 只在隔离副本还原第五阶段的邮箱表结构。
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE push_settings")
        connection.execute("CREATE TABLE push_settings (user_id INTEGER PRIMARY KEY REFERENCES users(id), email VARCHAR(254) NOT NULL)")
        connection.execute("INSERT INTO push_settings VALUES (?,?)", (ids[0], "legacy@example.com"))
        connection.execute("DROP TABLE rate_limit_buckets")
    engine = make_engine(f"sqlite:///{path}")
    backup = initialize_database(engine)
    assert "pre-stage7" in backup
    with sqlite3.connect(backup) as connection:
        assert "email_verified_at" not in {row[1] for row in connection.execute("PRAGMA table_info(push_settings)")}
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT email,email_verified_at,verification_attempts FROM push_settings").fetchone() == ("legacy@example.com", None, 0)
        assert connection.execute("SELECT value FROM site_settings WHERE key='schema_version'").fetchone()[0] == "7"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert initialize_database(engine) is None
    assert "test_admin" in caplog.text and "跳过" in caplog.text
    engine.dispose()


def test_stage7_alter_failure_rolls_back_schema_and_preserves_backup(accounts, tmp_path):
    admin, a, b, ids, factory, alerts = accounts
    path = tmp_path / "stage7-failed-upgrade.sqlite3"
    with sqlite3.connect(factory.kw["bind"].url.database) as source, sqlite3.connect(path) as target:
        source.backup(target)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE push_settings")
        connection.execute("CREATE TABLE push_settings (user_id INTEGER PRIMARY KEY REFERENCES users(id), email VARCHAR(254) NOT NULL)")
        connection.execute("INSERT INTO push_settings VALUES (?,?)", (ids[0], "preserved@example.com"))
        connection.execute("DROP TABLE rate_limit_buckets")
        connection.execute("UPDATE site_settings SET value='5' WHERE key='schema_version'")
    engine = make_engine(f"sqlite:///{path}")

    def fail_second_alter(connection, cursor, statement, parameters, context, many):
        if statement.startswith("ALTER TABLE push_settings ADD COLUMN verification_hash"):
            raise RuntimeError("isolated migration failure")

    event.listen(engine, "before_cursor_execute", fail_second_alter)
    with pytest.raises(RuntimeError, match="isolated migration"):
        initialize_database(engine)
    event.remove(engine, "before_cursor_execute", fail_second_alter)
    with sqlite3.connect(path) as connection:
        assert {row[1] for row in connection.execute("PRAGMA table_info(push_settings)")} == {"user_id", "email"}
        assert connection.execute("SELECT email FROM push_settings").fetchone()[0] == "preserved@example.com"
        assert connection.execute("SELECT value FROM site_settings WHERE key='schema_version'").fetchone()[0] == "5"
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='rate_limit_buckets'").fetchone() is None
    assert len(list((tmp_path / "backup").glob("stage7-failed-upgrade-pre-stage7-*.sqlite3"))) == 1
    engine.dispose()
