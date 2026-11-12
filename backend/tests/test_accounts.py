"""第五阶段：真实认证流程、多账号隔离及迁移恢复，全部使用临时配置。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from backend.app import auth, config, main, service as price_service
from backend.app.alerts import AlertService, EmailSender, RuleInput
from backend.app.db import initialize_database, make_engine
from backend.app.models import AlertRule, PushLog, PushSetting, SiteSetting, User, UserSession
from backend.tests.test_alerts import AT, DAY, SETTINGS, Sender, point

PASSWORD = "isolated-test-password"
PAYLOAD = {"code": "alu_a00", "kind": "price_gte", "threshold": 1}


def mark_verified(factory, *user_ids):
    # 既有通道/去重回归的前置状态；所有权确认接口由第七阶段用例完整验证。
    with factory() as session:
        for user_id in user_ids:
            session.get(PushSetting, user_id).email_verified_at = auth.utc_now()
        session.commit()


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'stage5.sqlite3'}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    alerts = AlertService(factory, sender=Sender(), settings_reader=lambda: SETTINGS,
                          email_sender=Sender(), email_settings_reader=lambda: {"RESEND_API_KEY": "mock-key", "EMAIL_FROM": "sourcing@bbbhl.com"})
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(main, "alerts", alerts)
    monkeypatch.setattr(main, "initialize_database", lambda: None)
    monkeypatch.setattr(main, "latest_prices", lambda: price_service.latest_prices(factory))
    monkeypatch.setattr(main, "price_history", lambda code, days: price_service.price_history(code, days, factory))
    monkeypatch.setattr(main, "service", price_service.FetchService(factory))
    with TestClient(main.app) as admin, TestClient(main.app) as a, TestClient(main.app) as b:
        assert admin.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD}).status_code == 200
        ids = []
        for client, name in [(a, "account_a"), (b, "account_b")]:
            response = client.post("/api/auth/register", json={"username": name, "password": PASSWORD})
            assert response.status_code == 201 and set(response.json()) == {"message"}
            assert client.post("/api/auth/login", json={"username": name, "password": PASSWORD}).status_code == 200
            account = client.get("/api/auth/me").json()
            assert account["role"] == "user"
            ids.append(account["id"])
        yield admin, a, b, ids, factory, alerts
    engine.dispose()


def test_guest_gates_all_routes_and_login_assets(accounts):
    with TestClient(main.app) as guest:
        for path in ["/", "/docs", "/openapi.json", "/missing-page"]:
            response = guest.get(path, follow_redirects=False)
            assert response.status_code == 303 and response.headers["location"] == "/login"
        for path in ["/api/health", "/api/prices/latest", "/api/prices/history?code=alu_a00", "/api/alerts/rules", "/api/alerts/logs", "/api/me/push-settings", "/api/sources/status", "/api/admin/users", "/api/auth/me", "/api/missing"]:
            assert guest.get(path).status_code == 401
        assert guest.post("/api/alerts/check").status_code == 401
        assert guest.get("/login").status_code == 200
        assert guest.get("/register").status_code == 200


def test_role_permissions_and_registration_switch(accounts):
    admin, a, b, ids, factory, alerts = accounts
    for method, path, body in [("GET", "/api/sources/status", None), ("GET", "/api/settings/telegram", None),
                               ("POST", "/api/fetch/run", None), ("GET", "/api/admin/users", None),
                               ("PUT", "/api/admin/registration", {"enabled": False}),
                               ("PUT", f"/api/admin/users/{ids[1]}", {"enabled": False}),
                               ("DELETE", f"/api/admin/users/{ids[1]}", None)]:
        assert a.request(method, path, json=body).status_code == 403
    assert a.get("/api/prices/latest").status_code == 200
    assert a.get("/api/prices/history?code=alu_a00").status_code == 200
    assert admin.get("/api/sources/status").status_code == 200
    assert admin.get("/api/settings/telegram").status_code == 200
    assert len(admin.get("/api/admin/users").json()["items"]) == 3
    assert admin.put("/api/admin/registration", json={"enabled": False}).status_code == 200
    assert a.post("/api/auth/register", json={"username": "closed", "password": PASSWORD}).status_code == 403
    assert admin.put("/api/admin/registration", json={"enabled": True}).status_code == 200
    for extra in [{"role": "admin"}, {"user_id": 1}, {"enabled": True}]:
        assert a.post("/api/auth/register", json={"username": "elevated", "password": PASSWORD} | extra).status_code == 422
    assert a.post("/api/auth/register", json={"username": "short", "password": "123"}).status_code == 422
    assert a.post("/api/auth/register", json={"username": "account_a", "password": PASSWORD}).status_code == 201


def test_rules_settings_logs_and_daily_dedup_are_personal(accounts):
    admin, a, b, ids, factory, alerts = accounts
    first = a.post("/api/alerts/rules", json=PAYLOAD).json()
    second = b.post("/api/alerts/rules", json=PAYLOAD).json()
    assert a.get("/api/alerts/rules").json()["items"][0]["id"] == first["id"]
    assert [r["id"] for r in b.get("/api/alerts/rules").json()["items"]] == [second["id"]]
    assert b.put(f"/api/alerts/rules/{first['id']}", json=PAYLOAD).status_code == 404
    assert b.delete(f"/api/alerts/rules/{first['id']}").status_code == 404
    assert a.post("/api/alerts/rules", json=PAYLOAD | {"user_id": ids[1]}).status_code == 422
    for client, email in [(a, "a@example.com"), (b, "b@example.com")]:
        assert client.put("/api/me/push-settings", json={"email": email}).status_code == 200
        assert client.get("/api/me/push-settings").json()["email"] == email
    assert a.put("/api/me/push-settings", json={"email": "x@example.com", "user_id": ids[1]}).status_code == 422
    assert a.put("/api/me/push-settings", json={"email": "bad\r\nBcc: victim@example.com"}).status_code == 422
    mark_verified(factory, *ids)
    with factory() as session:
        session.add(point("105")); session.commit()
    assert alerts.run(at=AT, user_id=ids[1])["results"][0]["status"] == "sent"
    assert a.get("/api/alerts/logs").json()["total"] == 0
    assert alerts.run(at=AT, user_id=ids[0])["results"][0]["status"] == "sent"
    for client, uid in [(a, ids[0]), (b, ids[1])]:
        assert alerts.run(at=AT, user_id=uid)["results"][0]["status"] == "deduplicated"
        logs = client.get("/api/alerts/logs").json()
        assert logs["total"] == 1 and logs["items"][0]["user_id"] == uid
    assert b.get("/api/alerts/logs?user_id="+str(ids[0])).json()["items"][0]["recipient"] == "b@example.com"
    # API 检查只能返回当前用户规则，即使调用者是管理员也不展开其他人的规则。
    assert all(r["user_id"] == ids[0] for r in a.post("/api/alerts/check").json()["results"])
    assert admin.get("/api/alerts/logs").json()["total"] == 0


def test_cookie_password_logout_expiry_and_hashes(accounts):
    admin, a, b, ids, factory, alerts = accounts
    login = a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
    cookie = login.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    token = a.cookies.get(auth.COOKIE)
    with factory() as session:
        assert session.get(User, ids[0]).password_hash.startswith("$argon2id$")
        assert session.get(UserSession, auth.digest(token)) is not None
        assert session.get(UserSession, token) is None
    assert a.post("/api/auth/password", json={"old_password": "bad", "new_password": "new-password-123"}).status_code == 400
    assert a.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "short"}).status_code == 422
    assert a.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "new-password-123"}).status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    with TestClient(main.app) as stolen:
        stolen.cookies.set(auth.COOKIE, token)
        assert stolen.get("/api/auth/me").status_code == 401
    assert a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD}).status_code == 401
    assert a.post("/api/auth/login", json={"username": "account_a", "password": "new-password-123"}).status_code == 200
    assert a.post("/api/auth/logout").status_code == 200
    assert a.get("/api/prices/latest").status_code == 401
    with factory() as session:
        record = session.get(UserSession, auth.digest(b.cookies.get(auth.COOKIE)))
        record.expires_at = datetime(2000, 1, 1); session.commit()
    assert b.get("/api/auth/me").status_code == 401


def test_throttling_and_csrf(accounts):
    admin, a, b, ids, factory, alerts = accounts
    with TestClient(main.app) as guest:
        for _ in range(5):
            assert guest.post("/api/auth/login", json={"username": "missing", "password": "incorrect-password"}).status_code == 401
        response = guest.post("/api/auth/login", json={"username": "missing", "password": "incorrect-password"})
        assert response.status_code == 429 and 1 <= int(response.headers["retry-after"]) <= 900
    assert a.post("/api/alerts/rules", json=PAYLOAD, headers={"Origin": "https://other.example"}).status_code == 403
    assert a.post("/api/alerts/rules", data="code=x").status_code == 415


def test_disable_enable_delete_revoke_sessions_and_skip_evaluation(accounts):
    admin, a, b, ids, factory, alerts = accounts
    a.post("/api/alerts/rules", json=PAYLOAD)
    a.put("/api/me/push-settings", json={"email": "a@example.com"})
    mark_verified(factory, ids[0])
    with factory() as session:
        session.add(point("105")); session.commit()
    assert admin.put(f"/api/admin/users/{ids[0]}", json={"enabled": False}).status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    assert alerts.run(at=AT, user_id=ids[0])["results"] == []
    assert a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD}).status_code == 401
    assert admin.put(f"/api/admin/users/{ids[0]}", json={"enabled": True}).status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    assert a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD}).status_code == 200
    assert alerts.run(at=AT, user_id=ids[0])["results"][0]["status"] == "sent"
    assert admin.delete(f"/api/admin/users/{ids[0]}").status_code == 200
    assert a.get("/api/auth/me").status_code == 401
    with factory() as session:
        for model in (AlertRule, PushLog, PushSetting, UserSession):
            assert session.scalar(select(func.count()).select_from(model).where(model.user_id == ids[0])) == 0
    assert b.get("/api/auth/me").status_code == 200
    assert admin.delete("/api/admin/users/1").status_code == 403


def test_email_pending_then_mock_success_and_dual_admin_channels(accounts):
    admin, a, b, ids, factory, alerts = accounts
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "mock-message-"+str(len(requests))})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        alerts.email_sender = EmailSender(client)
        alerts.email_settings_reader = lambda: {"RESEND_API_KEY": "", "EMAIL_FROM": "sourcing@bbbhl.com"}
        with factory() as session:
            session.add(point("105")); session.commit()
        for actor in [admin, a, b]:
            actor.post("/api/alerts/rules", json=PAYLOAD)
        for actor, address in [(admin, "admin@example.com"), (a, "a@example.com"), (b, "b@example.com")]:
            actor.put("/api/me/push-settings", json={"email": address})
        pending = alerts.run(at=AT)
        assert [r["status"] for r in pending["results"]] == ["sent", "pending_configuration", "pending_configuration", "pending_configuration"]
        assert not requests
        assert a.get("/api/alerts/logs").json()["items"][0]["status"] == "pending_configuration"
        alerts.email_settings_reader = lambda: {"RESEND_API_KEY": "mock-key", "EMAIL_FROM": "sourcing@bbbhl.com"}
        mark_verified(factory, 1, *ids)
        with ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(lambda _: alerts.run(at=AT), range(4)))
        assert len(requests) == 3
        assert sorted(json.loads(r.content)["to"][0] for r in requests) == ["a@example.com", "admin@example.com", "b@example.com"]
        assert all(r.headers["authorization"] == "Bearer mock-key" for r in requests)
        assert a.get("/api/alerts/logs").json()["total"] == 1
        assert a.get("/api/alerts/logs").json()["items"][0]["provider_message_id"].startswith("mock-message-")
        assert len(alerts.sender.calls) == 1


@pytest.mark.parametrize("response,expected", [(httpx.Response(401, text="secret-key"), "failed"), (httpx.Response(429), "failed"), (httpx.Response(500), "failed"), (httpx.Response(200, json={}), "unknown"), (httpx.Response(200, text="invalid"), "unknown")])
def test_resend_rejects_unconfirmed_success_without_leaking(response, expected):
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        result = EmailSender(client).send("test", {"RESEND_API_KEY": "secret-key", "EMAIL_FROM": "sourcing@bbbhl.com", "recipient": "a@example.com"})
        assert result[0] == expected and "secret-key" not in str(result)


def test_resend_timeout_no_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret-key", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = EmailSender(client).send("test", {"RESEND_API_KEY": "secret-key", "EMAIL_FROM": "sourcing@bbbhl.com", "recipient": "a@example.com"})
    assert result[0] == "unknown" and len(calls) == 1 and "secret-key" not in str(result)


@pytest.mark.parametrize("status", ["failed", "unknown"])
def test_email_failures_keep_user_quota_across_restart_and_reset_next_day(accounts, status):
    admin, a, b, ids, factory, alerts = accounts
    a.put("/api/me/push-settings", json={"email": "a@example.com"})
    mark_verified(factory, ids[0])
    a.post("/api/alerts/rules", json=PAYLOAD)
    sender = Sender(status)
    alerts.email_sender = sender
    with factory() as session:
        session.add_all([point("105"), point("106", DAY+timedelta(days=1))]); session.commit()
    assert alerts.run(at=AT, user_id=ids[0])["results"][0]["status"] == status
    restarted = AlertService(factory, email_sender=sender, email_settings_reader=alerts.email_settings_reader)
    assert restarted.run(at=AT, user_id=ids[0])["results"][0]["status"] == "deduplicated"
    assert restarted.run(at=AT+timedelta(days=1), user_id=ids[0])["results"][0]["status"] == status
    assert len(sender.calls) == 2


def test_secure_cookie_and_credentials_ignore_process_environment(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    with (config.ROOT / ".env").open("a", encoding="utf-8") as handle:
        handle.write("SESSION_COOKIE_SECURE=true\n")
    monkeypatch.setenv("RESEND_API_KEY", "process-key-must-not-be-used")
    assert config.read_account_settings()["RESEND_API_KEY"] == ""
    response = a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
    assert "secure" in response.headers["set-cookie"].lower()


def test_empty_legacy_rules_preserve_deleted_id_sequence(tmp_path):
    path = tmp_path / "empty-legacy.sqlite3"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM alert_rules")
    engine = make_engine(f"sqlite:///{path}")
    initialize_database(engine)
    saved = AlertService(sessionmaker(engine, expire_on_commit=False)).save(RuleInput(**PAYLOAD), user_id=1)
    assert saved["id"] > 99
    engine.dispose()


def legacy_database(path):
    # 精确保留旧表的列、唯一约束及 AUTOINCREMENT，用于验证备份与升级。
    with sqlite3.connect(path) as connection:
        connection.executescript("""
        CREATE TABLE commodities (code VARCHAR(40) PRIMARY KEY, name VARCHAR(100), source VARCHAR(30), unit VARCHAR(20), quote_basis VARCHAR(200), enabled BOOLEAN);
        CREATE TABLE alert_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, code VARCHAR(40), kind VARCHAR(30), threshold NUMERIC(14,4), enabled BOOLEAN, source VARCHAR(30), quote_basis VARCHAR(200), unit VARCHAR(20), created_at DATETIME, updated_at DATETIME);
        CREATE TABLE push_logs (id INTEGER PRIMARY KEY, rule_id INTEGER, code VARCHAR(40), send_day DATE, kind VARCHAR(20), status VARCHAR(30), message VARCHAR(2000), detail VARCHAR(300), telegram_message_id INTEGER, created_at DATETIME, finished_at DATETIME, UNIQUE(rule_id,code,send_day));
        INSERT INTO commodities VALUES ('alu_a00','legacy','free','unit','basis',1);
        INSERT INTO alert_rules VALUES (7,'alu_a00','price_gte',1,1,'free','basis','unit','2026-11-12 16:00:00','2026-11-12 16:00:00');
        INSERT INTO push_logs VALUES (5,7,'alu_a00','2026-11-12','rule','sent','legacy message','legacy detail',42,'2026-11-12 16:00:00','2026-11-12 16:00:01');
        UPDATE sqlite_sequence SET seq=99 WHERE name='alert_rules';
        """)


def test_legacy_migration_backup_idempotence_preservation_and_rollback(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy_database(path)
    engine = make_engine(f"sqlite:///{path}")
    backup = initialize_database(engine)
    assert "/backup/" in backup.replace("\\", "/")
    with sqlite3.connect(backup) as connection:
        assert "user_id" not in {r[1] for r in connection.execute("PRAGMA table_info(alert_rules)")}
        assert connection.execute("SELECT telegram_message_id FROM push_logs").fetchone()[0] == 42
    factory = sessionmaker(engine, expire_on_commit=False)
    assert AlertService(factory).deliver("repeat", rule_id=7, code="alu_a00", user_id=1, at=AT)["status"] == "deduplicated"
    with factory() as session:
        assert session.get(AlertRule, 7).user_id == 1
        log = session.get(PushLog, 5)
        assert log.user_id == 1 and log.channel == "telegram" and log.telegram_message_id == 42
        original_hash = session.get(User, 1).password_hash
    assert initialize_database(engine) is None
    with factory() as session:
        assert session.get(User, 1).password_hash == original_hash
        assert session.scalar(select(func.count()).select_from(AlertRule)) == 1
    saved = AlertService(factory).save(RuleInput(**PAYLOAD), user_id=1)
    assert saved["id"] > 99
    engine.dispose()
    # 恢复至另一个文件验证，避免对测试进程打开的原文件做替换。
    restored = tmp_path / "restored.sqlite3"
    with sqlite3.connect(backup) as source, sqlite3.connect(restored) as target:
        source.backup(target)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT COUNT(*) FROM alert_rules").fetchone()[0] == 1
        assert "users" not in {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_missing_bootstrap_credentials_rolls_back_without_orphaning(tmp_path):
    (config.ROOT / ".env").write_text("", encoding="utf-8")
    path = tmp_path / "failure.sqlite3"
    legacy_database(path)
    engine = make_engine(f"sqlite:///{path}")
    with pytest.raises(RuntimeError, match="ADMIN_USERNAME"):
        initialize_database(engine)
    with sqlite3.connect(path) as connection:
        assert "user_id" not in {r[1] for r in connection.execute("PRAGMA table_info(alert_rules)")}
        assert "users" not in {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    engine.dispose()


def test_bootstrap_existing_account_never_overwritten_and_env_only(accounts):
    admin, a, b, ids, factory, alerts = accounts
    with factory() as session:
        encoded = session.get(User, 1).password_hash
    (config.ROOT / ".env").write_text("ADMIN_USERNAME=test_admin\nADMIN_PASSWORD=different-password\n", encoding="utf-8")
    initialize_database(factory.kw["bind"])
    with factory() as session:
        assert session.get(User, 1).password_hash == encoded
    assert config.read_account_settings()["EMAIL_FROM"] == "sourcing@bbbhl.com"
