"""启动隔离第五阶段服务，迁移真实旧库副本并通过 HTTP 验收；不读取正式凭据。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def snapshot(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


def serve(folder, port):
    from sqlalchemy.orm import sessionmaker
    import httpx
    import uvicorn
    from backend.app import main, auth, config
    from backend.app.db import make_engine, initialize_database
    from backend.app.alerts import AlertService, EmailSender, TelegramSender
    from backend.app.service import FetchService, latest_prices, price_history
    # main 先导入以保留正式前端资源路径；随后将所有凭据与数据库访问切到隔离目录。
    config.ROOT = folder
    engine = make_engine(f"sqlite:///{(folder / 'procurement.sqlite3').as_posix()}")
    factory = sessionmaker(engine, expire_on_commit=False)
    auth.SessionLocal = factory
    main.initialize_database = lambda: initialize_database(engine)
    main.service = FetchService(factory)
    main.latest_prices = lambda: latest_prices(factory)
    main.price_history = lambda code, days: price_history(code, days, factory)
    def mock(request):
        if request.url.host == "api.resend.com":
            return httpx.Response(200, json={"id": "mock-" + secrets.token_hex(8)})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 123456}})
    client = httpx.Client(transport=httpx.MockTransport(mock))
    main.alerts = AlertService(factory, sender=TelegramSender(client), email_sender=EmailSender(client))
    # 验收不请求真实源；手动入口只验证权限，定时任务不发起采集或自动评估。
    main.service.run = lambda: {"status": "isolated_fetch_control_mock"}
    main.scheduled_fetch = lambda: None
    main.scheduled_alerts = lambda: None
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.serve:
        return serve(args.serve, args.port)
    import httpx
    from datetime import datetime, timedelta
    from backend.app.config import TIMEZONE
    source = ROOT / "data/procurement.sqlite3"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    folder = ROOT / "data/stage5-validation" / stamp
    folder.mkdir(parents=True)
    backup = ROOT / "data/backup" / f"procurement-pre-stage5-{stamp}.sqlite3"
    snapshot(source, backup)
    isolated = folder / "procurement.sqlite3"
    snapshot(backup, isolated)
    # 正式 .env 只取字节摘要用于未改动断言，不解析或输出其内容。
    env_path = ROOT / ".env"
    env_digest = hashlib.sha256(env_path.read_bytes()).digest()
    username, password = "stage5_admin", secrets.token_urlsafe(24)
    temporary_env = folder / ".env"
    temporary_env.write_text(f"ADMIN_USERNAME={username}\nADMIN_PASSWORD={password}\nEMAIL_FROM=sourcing@bbbhl.com\n", encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    log = (folder / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(folder), "--port", str(port)], cwd=ROOT, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    checks = []
    def check(name, condition, detail=None):
        if not condition:
            raise AssertionError(name)
        checks.append({"check": name, "result": "passed", "detail": detail})
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, trust_env=False, timeout=20) as guest, httpx.Client(base_url=base, trust_env=False, timeout=20) as admin, httpx.Client(base_url=base, trust_env=False, timeout=20) as a, httpx.Client(base_url=base, trust_env=False, timeout=20) as b:
            for attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError("隔离服务启动失败，检查 " + str(folder / "server.log"))
                try:
                    if guest.get("/api/health").status_code == 401:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError("服务启动超时")
            check("anonymous_home_redirect", guest.get("/").status_code == 303)
            check("anonymous_prices_401", guest.get("/api/prices/latest").status_code == 401)
            check("login_page_200", guest.get("/login").status_code == 200)
            login = admin.post("/api/auth/login", json={"username": username, "password": password})
            check("admin_login_httponly", login.status_code == 200 and "HttpOnly" in login.headers["set-cookie"])
            check("admin_prices_11", len(admin.get("/api/prices/latest").json()["items"]) == 11)
            for actor, name in [(a, "stage5_a"), (b, "stage5_b")]:
                registered = actor.post("/api/auth/register", json={"username": name, "password": password})
                check("register_"+name, registered.status_code == 201 and registered.json()["role"] == "user")
                check("login_"+name, actor.post("/api/auth/login", json={"username": name, "password": password}).status_code == 200)
            aid, bid = a.get("/api/auth/me").json()["id"], b.get("/api/auth/me").json()["id"]
            for path in ["/api/sources/status", "/api/settings/telegram", "/api/admin/users"]:
                check("user_403_"+path, a.get(path).status_code == 403)
                check("admin_200_"+path, admin.get(path).status_code == 200)
            check("user_fetch_403", a.post("/api/fetch/run").status_code == 403)
            check("admin_fetch_control_mock_200", admin.post("/api/fetch/run").status_code == 200)
            payload = {"code": "alu_a00", "kind": "price_gte", "threshold": 1}
            ar = a.post("/api/alerts/rules", json=payload).json()
            br = b.post("/api/alerts/rules", json=payload).json()
            check("a_b_rules_isolated", [r["id"] for r in b.get("/api/alerts/rules").json()["items"]] == [br["id"]])
            check("b_cannot_edit_a_404", b.put(f"/api/alerts/rules/{ar['id']}", json=payload).status_code == 404)
            check("b_cannot_delete_a_404", b.delete(f"/api/alerts/rules/{ar['id']}").status_code == 404)
            a.put("/api/me/push-settings", json={"email": "a@example.com"})
            b.put("/api/me/push-settings", json={"email": "b@example.com"})
            # 仅在隔离库将真实铝锭点移动到今天，验证逻辑；报告明确标为测试时间变换。
            day = datetime.now(TIMEZONE).date()
            with sqlite3.connect(isolated) as connection:
                row = connection.execute("SELECT id FROM price_points WHERE code='alu_a00' ORDER BY quote_date DESC LIMIT 1").fetchone()
                if not row:
                    raise RuntimeError("真实旧库无铝锭价格，不能完成规则验收")
                connection.execute("UPDATE price_points SET quote_date=? WHERE id=?", (str(day), row[0]))
            pending = a.post("/api/alerts/check").json()["results"]
            check("missing_key_pending_log", pending[0]["status"] == "pending_configuration" and a.get("/api/alerts/logs").json()["items"][0]["status"] == "pending_configuration")
            temporary_env.write_text(f"ADMIN_USERNAME={username}\nADMIN_PASSWORD={password}\nRESEND_API_KEY=mock-key-never-sent\nEMAIL_FROM=sourcing@bbbhl.com\n", encoding="utf-8")
            before_a = a.get("/api/alerts/logs").json()["total"]
            sent_b = b.post("/api/alerts/check").json()["results"][0]
            check("b_sent_to_own_email_mock", sent_b["status"] == "sent" and sent_b["recipient"] == "b@example.com")
            check("b_does_not_consume_a_quota", a.get("/api/alerts/logs").json()["total"] == before_a)
            sent_a = a.post("/api/alerts/check").json()["results"][0]
            check("a_sent_after_pending_mock", sent_a["status"] == "sent" and sent_a["recipient"] == "a@example.com")
            check("a_same_day_dedup", a.post("/api/alerts/check").json()["results"][0]["status"] == "deduplicated")
            check("b_logs_isolated", all(r["user_id"] == bid for r in b.get("/api/alerts/logs").json()["items"]))
            check("registration_close", admin.put("/api/admin/registration", json={"enabled": False}).status_code == 200 and guest.post("/api/auth/register", json={"username": "closed_user", "password": password}).status_code == 403)
            check("registration_open", admin.put("/api/admin/registration", json={"enabled": True}).status_code == 200)
            check("disable_revokes_session", admin.put(f"/api/admin/users/{bid}", json={"enabled": False}).status_code == 200 and b.get("/api/auth/me").status_code == 401)
            check("enable_account", admin.put(f"/api/admin/users/{bid}", json={"enabled": True}).status_code == 200)
            check("relogin_after_enable", b.post("/api/auth/login", json={"username": "stage5_b", "password": password}).status_code == 200)
            check("delete_revokes_session", admin.delete(f"/api/admin/users/{bid}").status_code == 200 and b.get("/api/auth/me").status_code == 401)
            check("change_password_revokes_session", a.post("/api/auth/password", json={"old_password": password, "new_password": secrets.token_urlsafe(24)}).status_code == 200 and a.get("/api/auth/me").status_code == 401)
            check("logout_revokes_session", admin.post("/api/auth/logout").status_code == 200 and admin.get("/api/auth/me").status_code == 401)
        with sqlite3.connect(backup) as old, sqlite3.connect(isolated) as migrated:
            old_rules = old.execute("SELECT * FROM alert_rules ORDER BY id").fetchall()
            columns = [r[1] for r in old.execute("PRAGMA table_info(alert_rules)")]
            selected = ",".join(columns)
            new_rules = migrated.execute(f"SELECT {selected} FROM alert_rules WHERE user_id=1 ORDER BY id").fetchall()
            check("all_legacy_admin_rules_unchanged", old_rules == new_rules, {"rules": len(old_rules)})
            old_logs = old.execute("SELECT * FROM push_logs ORDER BY id").fetchall()
            log_columns = ",".join(r[1] for r in old.execute("PRAGMA table_info(push_logs)"))
            new_logs = migrated.execute(f"SELECT {log_columns} FROM push_logs WHERE user_id=1 ORDER BY id").fetchall()
            check("all_legacy_telegram_logs_unchanged", old_logs == new_logs, {"logs": len(old_logs)})
            check("legacy_logs_admin_telegram", migrated.execute("SELECT COUNT(*) FROM push_logs WHERE user_id=1 AND channel!='telegram'").fetchone()[0] == 0)
            migration_backup = migrated.execute("SELECT value FROM site_settings WHERE key='stage5_backup'").fetchone()[0]
        restored = folder / "rollback-check.sqlite3"
        snapshot(Path(migration_backup), restored)
        with sqlite3.connect(restored) as connection:
            check("rollback_integrity", connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
            check("rollback_old_schema", "user_id" not in {r[1] for r in connection.execute("PRAGMA table_info(alert_rules)")})
            check("rollback_old_rules", connection.execute("SELECT * FROM alert_rules ORDER BY id").fetchall() == old_rules)
        check("formal_env_unchanged", hashlib.sha256(env_path.read_bytes()).digest() == env_digest)
        with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as production:
            check("formal_db_still_stage4", "user_id" not in {r[1] for r in production.execute("PRAGMA table_info(alert_rules)")})
        output = {"status": "passed", "checks_passed": len(checks), "production_backup": str(backup), "migration_backup": migration_backup, "isolated_database": str(isolated), "checks": checks,
                  "mode": "real_http_isolated_database_mock_delivery", "test_price_note": "仅隔离副本将真实铝锭价格点的日期变换为今天；正式行情与凭据未修改", "actual_messages_sent": 0}
        (ROOT / "data/stage5-live.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
        log.close()
        temporary_env.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
