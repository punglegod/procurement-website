"""第六阶段审计复现；全部使用临时数据库、配置和隔离发送器，不修改业务代码。"""

import json
from datetime import timedelta
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app import auth, config, main
from backend.app.adapters.common import now
from backend.app.db import initialize_database
from backend.app.models import User, UserSession
from backend.tests.test_accounts import accounts, PASSWORD, PAYLOAD, mark_verified
from backend.tests.test_alerts import point


def evidence(name, value):
    print("AUDIT " + name + " " + json.dumps(value, ensure_ascii=True, sort_keys=True))


def test_audit_ab_authorization_matrix(accounts):
    admin, a, b, ids, factory, alerts = accounts
    aid, bid = ids
    rule_a = a.post("/api/alerts/rules", json=PAYLOAD).json()["id"]
    rule_b = b.post("/api/alerts/rules", json=PAYLOAD).json()["id"]
    a.put("/api/me/push-settings", json={"email": "owner-a@example.com"})
    b.put("/api/me/push-settings", json={"email": "owner-b@example.com"})
    mark_verified(factory, aid, bid)
    with factory() as session:
        session.add(point("105", now().date()))
        session.commit()
    checks = {}
    checks["B_list_query_A"] = [r["id"] for r in b.get(f"/api/alerts/rules?user_id={aid}&id={rule_a}").json()["items"]]
    assert checks["B_list_query_A"] == [rule_b]
    checks["B_get_A_rule_id"] = b.get(f"/api/alerts/rules/{rule_a}").status_code
    assert checks["B_get_A_rule_id"] in (404, 405)
    checks["B_update_A_rule"] = b.put(f"/api/alerts/rules/{rule_a}", json=PAYLOAD).status_code
    checks["B_delete_A_rule"] = b.delete(f"/api/alerts/rules/{rule_a}").status_code
    assert checks["B_update_A_rule"] == checks["B_delete_A_rule"] == 404
    checks["B_create_for_A"] = b.post("/api/alerts/rules", json=PAYLOAD | {"user_id": aid}).status_code
    checks["B_update_settings_for_A"] = b.put("/api/me/push-settings", json={"email": "attacker@example.com", "user_id": aid}).status_code
    assert checks["B_create_for_A"] == checks["B_update_settings_for_A"] == 422
    assert b.get(f"/api/me/push-settings?user_id={aid}").json()["user_id"] == bid
    assert b.get(f"/api/alerts/status?user_id={aid}").json()["channels"].keys() == {"email"}
    check_b = b.post(f"/api/alerts/check?user_id={aid}", json={"user_id": aid, "rule_id": rule_a}).json()
    assert {r["user_id"] for r in check_b["results"]} == {bid}
    assert {r["rule_id"] for r in check_b["results"]} == {rule_b}
    checks["B_immediate_check_owner"] = bid
    assert a.get("/api/alerts/logs").json()["total"] == 0
    a.post("/api/alerts/check")
    log_a = a.get("/api/alerts/logs").json()["items"][0]["id"]
    logs_b = b.get(f"/api/alerts/logs?user_id={aid}&id={log_a}").json()["items"]
    assert {r["user_id"] for r in logs_b} == {bid}
    assert all(r["id"] != log_a for r in logs_b)
    for method in ("GET", "PUT", "DELETE"):
        status = b.request(method, f"/api/alerts/logs/{log_a}", json={} if method == "PUT" else None).status_code
        checks[f"B_{method}_A_log_id"] = status
        assert status in (404, 405)
    for method in ("GET", "PUT", "DELETE"):
        status = b.request(method, f"/api/me/push-settings/{aid}", json={} if method == "PUT" else None).status_code
        checks[f"B_{method}_A_setting_id"] = status
        assert status in (404, 405)
    tested = b.post(f"/api/alerts/test?user_id={aid}", json={"user_id": aid}).json()
    assert tested["user_id"] == bid and tested["recipient"] == "owner-b@example.com"
    assert a.get("/api/me/push-settings").json()["email"] == "owner-a@example.com"
    assert a.get("/api/alerts/rules").json()["items"][0]["id"] == rule_a
    checks["A_resources_unchanged"] = True
    # 回放仅有本地脚本，不存在可指定账号或资源 id 的 HTTP 回放路由。
    checks["replay_api"] = b.post("/api/alerts/replay", json={"user_id": aid}).status_code
    assert checks["replay_api"] in (404, 405)
    evidence("AB_MATRIX", checks)


def test_audit_global_and_guest_gates(accounts):
    admin, a, b, ids, factory, alerts = accounts
    global_routes = [("GET", "/api/sources/status", None), ("POST", "/api/fetch/run", None),
                     ("GET", "/api/settings/telegram", None), ("GET", "/api/admin/users", None),
                     ("PUT", f"/api/admin/users/{ids[0]}", {"enabled": False}),
                     ("DELETE", f"/api/admin/users/{ids[0]}", None),
                     ("PUT", "/api/admin/registration", {"enabled": False})]
    statuses = {method + " " + path: b.request(method, path, json=body).status_code for method, path, body in global_routes}
    assert set(statuses.values()) == {403}
    with TestClient(main.app) as guest:
        schema = admin.get("/openapi.json").json()
        protected = {}
        for path, methods in schema["paths"].items():
            if path in ("/api/auth/register", "/api/auth/login"):
                continue
            concrete = path.replace("{user_id}", str(ids[0])).replace("{rule_id}", "1")
            for method in methods:
                if method.upper() in {"GET", "POST", "PUT", "DELETE", "PATCH"}:
                    protected[method.upper() + " " + concrete] = guest.request(method, concrete).status_code
        assert set(protected.values()) == {401}
        evidence("GUEST_ALL_SCHEMA_API", protected)
    for extra in ({"role": "admin"}, {"enabled": True}, {"user_id": 1}):
        assert b.post("/api/auth/register", json={"username": "elevated", "password": PASSWORD} | extra).status_code == 422
    evidence("ORDINARY_GLOBAL_GATES", statuses)


def test_audit_sessions_and_cookie(accounts):
    admin, a, b, ids, factory, alerts = accounts
    login = a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
    token = a.cookies.get(auth.COOKIE)
    cookie = login.headers["set-cookie"].lower()
    with factory() as session:
        user = session.get(User, ids[0])
        assert user.password_hash.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
        assert session.get(UserSession, auth.digest(token))
        assert not session.get(UserSession, token)
    result = {"token_length": len(token), "stored_sha256_only": True, "argon2id_m65536_t3_p4": True,
              "httponly": "httponly" in cookie, "samesite_strict": "samesite=strict" in cookie,
              "max_age_604800": "max-age=604800" in cookie, "default_secure": "secure" in cookie}
    with TestClient(main.app) as stolen:
        stolen.cookies.set(auth.COOKIE, token)
        assert a.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "audit-new-password"}).status_code == 200
        result["old_token_after_password_change"] = stolen.get("/api/auth/me").status_code
        assert result["old_token_after_password_change"] == 401
    assert admin.put(f"/api/admin/users/{ids[1]}", json={"enabled": False}).status_code == 200
    result["old_token_after_disable"] = b.get("/api/auth/me").status_code
    assert result["old_token_after_disable"] == 401
    admin.put(f"/api/admin/users/{ids[1]}", json={"enabled": True})
    assert b.get("/api/auth/me").status_code == 401
    b.post("/api/auth/login", json={"username": "account_b", "password": PASSWORD})
    admin.delete(f"/api/admin/users/{ids[1]}")
    result["old_token_after_delete"] = b.get("/api/auth/me").status_code
    assert result["old_token_after_delete"] == 401
    evidence("SESSION", result)


def test_audit_unverified_email_unlimited_test_send(accounts):
    admin, a, b, ids, factory, alerts = accounts
    assert b.put("/api/me/push-settings", json={"email": "unverified-third-party@example.com"}).status_code == 200
    before = len(alerts.email_sender.calls)
    responses = [b.post("/api/alerts/test") for _ in range(20)]
    assert all(r.status_code == 403 for r in responses)
    sent = len(alerts.email_sender.calls) - before
    assert sent == 0
    mark_verified(factory, ids[1])
    limited = [b.post("/api/alerts/test") for _ in range(5)]
    assert [r.status_code for r in limited] == [200, 200, 200, 429, 429]
    evidence("UNVERIFIED_UNLIMITED_EMAIL", {"requests": 20, "http_403": len(responses), "mock_sends_before_verification": sent,
                                           "verified_send_statuses": [r.status_code for r in limited],
                                           "recipient_verified": False, "real_external_messages": 0})


def test_audit_session_rotation_logout_expiry_secure(accounts):
    admin, a, b, ids, factory, alerts = accounts
    original_token = a.cookies.get(auth.COOKIE)
    a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
    new_token = a.cookies.get(auth.COOKIE)
    assert original_token != new_token
    with TestClient(main.app) as copied:
        copied.cookies.set(auth.COOKIE, original_token)
        rotated_status = copied.get("/api/auth/me").status_code
        assert rotated_status == 401
        copied.cookies.set(auth.COOKIE, new_token)
        a.post("/api/auth/logout")
        logout_status = copied.get("/api/auth/me").status_code
        assert logout_status == 401
    with factory() as session:
        record = session.get(UserSession, auth.digest(b.cookies.get(auth.COOKIE)))
        record.expires_at = auth.utc_now() - timedelta(seconds=1)
        session.commit()
    expired_status = b.get("/api/auth/me").status_code
    assert expired_status == 401
    with (config.ROOT / ".env").open("a", encoding="utf-8") as handle:
        handle.write("SESSION_COOKIE_SECURE=true\n")
    response = a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
    assert "secure" in response.headers["set-cookie"].lower()
    evidence("SESSION_LIFECYCLE", {"old_cookie_after_rotation": rotated_status, "cookie_after_logout": logout_status,
                                   "expired_cookie": expired_status, "configured_secure": True})


def test_audit_registration_throttled_without_hash_write_lock(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    active_writer_locks = []
    original = auth.hash_password

    def checked_hash(value):
        # 哈希期间独立连接应能立即取得写事务。
        import sqlite3
        connection = sqlite3.connect(factory.kw["bind"].url.database, timeout=0)
        try:
            connection.execute("BEGIN IMMEDIATE")
            active_writer_locks.append(False)
            connection.rollback()
        except sqlite3.OperationalError as error:
            active_writer_locks.append("locked" in str(error))
        finally:
            connection.close()
        return original(value)

    monkeypatch.setattr(auth, "hash_password", checked_hash)
    started = perf_counter()
    with TestClient(main.app, client=("198.51.100.30", 12345)) as guest:
        codes = [guest.post("/api/auth/register", json={"username": f"unlimited_{i}", "password": PASSWORD}).status_code for i in range(12)]
    elapsed = perf_counter() - started
    assert codes == [201] * auth.REGISTER_IP_HOURLY + [429] * (12 - auth.REGISTER_IP_HOURLY)
    assert active_writer_locks == [False] * auth.REGISTER_IP_HOURLY
    evidence("REGISTRATION_LIMIT_FIXED", {"requests_same_ip": 12, "http_statuses": codes, "seconds": round(elapsed, 3),
                                          "argon2_inside_global_sqlite_write_lock": any(active_writer_locks)})


def test_audit_foreign_ip_cannot_lock_account(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    with TestClient(main.app, client=("198.51.100.10", 12345)) as attacker, TestClient(main.app, client=("203.0.113.20", 12345)) as legitimate:
        bad = [attacker.post("/api/auth/login", json={"username": "test_admin", "password": "incorrect-password"}).status_code for _ in range(5)]
        good = legitimate.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD})
        blocked = attacker.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD})
        assert bad == [401] * 5 and good.status_code == 200 and blocked.status_code == 429
        evidence("ACCOUNT_LOCKOUT_FIXED", {"attacker_failures": bad, "correct_password_other_ip": good.status_code,
                                           "attacker_source_still_limited": blocked.status_code})


def test_audit_admin_seed_on_later_start(accounts):
    admin, a, b, ids, factory, alerts = accounts
    (config.ROOT / ".env").write_text("ADMIN_USERNAME=replacement_admin\nADMIN_PASSWORD=isolated-replacement\n", encoding="utf-8")
    initialize_database(factory.kw["bind"])
    with factory() as session:
        names = list(session.scalars(select(User.username).where(User.role == "admin").order_by(User.id)))
    assert names == ["test_admin"]
    assert admin.delete("/api/admin/users/1").status_code == 403
    evidence("BOOTSTRAP_ON_RESTART", {"admin_names": names, "old_admin_still_protected": True})
