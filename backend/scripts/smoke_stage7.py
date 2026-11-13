"""第七阶段隔离真实 HTTP 验收；仅本机通信，不读取正式凭据或连接真实推送。"""

from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(os.environ.get("PROCUREMENT_SMOKE_OUTPUT", str(ROOT / "backend/scripts")))
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def forbid_external(*args, **kwargs):
    raise RuntimeError("第七阶段自测禁止外部网络和真实推送")


def serve(folder, port):
    import httpx
    from backend.app import config
    # 数据模块首次导入之前改路径；保留 main 的正式 dist 挂载路径。
    config.DATA_DIR = folder / "data"
    from backend.app import main
    config.ROOT = folder
    httpx.HTTPTransport.handle_request = forbid_external
    httpx.AsyncHTTPTransport.handle_async_request = forbid_external
    write_lock = threading.Lock()

    class CapturedEmail:
        def send(self, text, settings):
            # 仅父子测试进程可读的本地文件；不新增任何取码 HTTP API。
            with write_lock:
                with (folder / "captured_mail.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({"recipient": settings["recipient"], "subject": settings.get("subject", "采购行情 · 现货提醒"), "text": text}, ensure_ascii=False) + "\n")
            return "sent", "隔离邮件发送器已接收，未发送真实邮件", "stage7-isolated-provider-id"

    class ForbiddenTelegram:
        def send(self, *args, **kwargs):
            raise AssertionError("HTTP 自测不应发送 Telegram")

    main.alerts.email_sender = CapturedEmail()
    main.alerts.sender = ForbiddenTelegram()
    identity = {"windows_elevated_admin_token": bool(__import__("ctypes").windll.shell32.IsUserAnAdmin())} if os.name == "nt" else {"effective_uid": os.geteuid()}
    (folder / "process_identity.json").write_text(json.dumps(identity), encoding="utf-8")
    import uvicorn
    # 使用真实 socket 源 IP；测试进程不接受代理头冒充来源。
    uvicorn.run(main.app, host="127.0.0.1", port=port, workers=1, log_level="warning", proxy_headers=False)


def read_mail(folder):
    path = folder / "captured_mail.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def run_http(folder, port, password):
    import httpx
    checks = []
    base = f"http://127.0.0.1:{port}"

    def expect(name, response, status):
        checks.append({"check": name, "status": response.status_code, "expected": status})
        assert response.status_code == status, f"{name}: expected {status}, received {response.status_code}"
        return response

    def client(ip=None):
        transport = httpx.HTTPTransport(local_address=ip, trust_env=False) if ip else None
        return httpx.Client(base_url=base, transport=transport, trust_env=False, timeout=15)

    with client() as admin, client() as personal, client() as other:
        expect("anonymous_health", admin.get("/api/health"), 401)
        expect("anonymous_home", admin.get("/", follow_redirects=False), 303)
        shell = expect("anonymous_login_shell", admin.get("/login"), 200)
        asset = re.search(r'src="(/assets/[^\"]+)"', shell.text)
        assert asset, "正式 dist 壳页缺少脚本资源"
        expect("anonymous_login_asset", admin.get(asset[1]), 200)
        expect("admin_login", admin.post("/api/auth/login", json={"username": "stage7_admin", "password": password}), 200)
        expect("authenticated_home", admin.get("/"), 200)
        prices = expect("authenticated_prices", admin.get("/api/prices/latest"), 200).json()["items"]
        assert len(prices) == 11 and all(row["price_point"] is None for row in prices)
        expect("admin_sources", admin.get("/api/sources/status"), 200)
        sci = expect("admin_sci99_classified_state", admin.get("/api/admin/sources/sci99"), 200).json()
        assert sci["auth_status"] == "pending_credentials" and sci["quote_status"] == "quote_pending"
        assert sci["verified_at"] is None and sci["enabled"] is False
        test = expect("admin_sci99_unconfigured_no_network", admin.post("/api/admin/sources/sci99/test"), 200).json()
        assert test["status"] == "pending_credentials"
        for actor, name in [(personal, "stage7_user"), (other, "stage7_other")]:
            registered = expect(name + "_register", actor.post("/api/auth/register", json={"username": name, "password": password}), 201)
            assert set(registered.json()) == {"message"}
            expect(name + "_login", actor.post("/api/auth/login", json={"username": name, "password": password}), 200)
        for path in ("/api/sources/status", "/api/settings/telegram", "/api/admin/users"):
            expect("ordinary_denied:" + path, personal.get(path), 403)
        expect("ordinary_fetch_denied", personal.post("/api/fetch/run"), 403)
        expect("ordinary_sci99_state_denied", personal.get("/api/admin/sources/sci99"), 403)
        expect("ordinary_sci99_save_denied", personal.put("/api/admin/sources/sci99", json={"enabled": False}), 403)
        expect("ordinary_sci99_test_denied", personal.post("/api/admin/sources/sci99/test"), 403)
        address = "stage7-recipient@example.invalid"
        settings = expect("save_unverified_email", personal.put("/api/me/push-settings", json={"email": address}), 200)
        assert settings.json()["email_verified"] is False
        expect("test_before_verification", personal.post("/api/alerts/test"), 403)
        assert read_mail(folder) == []
        requested = expect("request_verification", personal.post("/api/me/email-verification/request"), 200)
        assert requested.json()["status"] == "sent"
        letters = read_mail(folder)
        assert len(letters) == 1 and letters[0]["subject"] == "采购行情 · 邮箱验证"
        match = re.search(r"验证码：(\d{8})", letters[0]["text"])
        assert match, "隔离邮件缺少八位验证码"
        code = match[1]
        assert code not in requested.text
        confirmed = expect("confirm_verification", personal.post("/api/me/email-verification/confirm", json={"code": code}), 200)
        assert confirmed.json()["email_verified"] is True
        expect("verification_code_one_use", personal.post("/api/me/email-verification/confirm", json={"code": code}), 400)
        for number in (1, 2):
            sent = expect(f"verified_test_{number}", personal.post("/api/alerts/test"), 200)
            assert sent.json()["status"] == "sent"
        limited = expect("shared_recipient_fourth_mail_limited", personal.post("/api/alerts/test"), 429)
        assert int(limited.headers["retry-after"]) > 0 and len(read_mail(folder)) == 3
        expect("second_account_same_recipient_save", other.put("/api/me/push-settings", json={"email": address}), 200)
        expect("recipient_limit_shared_across_accounts", other.post("/api/me/email-verification/request"), 429)
        assert len(read_mail(folder)) == 3
        changed = expect("change_email", personal.put("/api/me/push-settings", json={"email": "stage7-changed@example.invalid"}), 200)
        assert changed.json()["email_verified"] is False
        expect("changed_email_test_denied", personal.post("/api/alerts/test"), 403)
        expect("changed_email_old_code_denied", personal.post("/api/me/email-verification/confirm", json={"code": code}), 400)

        def chunks():
            yield b" " * 8192
            yield b" " * 8192
            yield b" " * 8192
            yield b"{}"

        oversized = expect("chunked_body_over_16KiB", personal.post("/api/auth/login", content=chunks(), headers={"Content-Type": "application/json"}), 413)
        assert oversized.request.headers.get("transfer-encoding") == "chunked"
        assert "content-length" not in oversized.request.headers
        expect("oversized_page", personal.get("/api/alerts/logs?page=9223372036854775808&page_size=100"), 422)
        expect("logout", personal.post("/api/auth/logout"), 200)
        expect("after_logout", personal.get("/api/health"), 401)

    # 使用真实回环源 IP，确认失败来源不会硬锁另一个来源的正确登录。
    with client("127.0.0.2") as attacker, client("127.0.0.3") as legitimate:
        for index in range(5):
            expect(f"source_A_wrong_login_{index + 1}", attacker.post("/api/auth/login", json={"username": "stage7_admin", "password": "stage7-incorrect-password"}), 401)
        expect("source_A_sixth_login_limited", attacker.post("/api/auth/login", json={"username": "stage7_admin", "password": password}), 429)
        for number, forged in enumerate(({"X-Forwarded-For": "198.51.100.21"},
                {"X-Forwarded-For": "198.51.100.22, 127.0.0.1", "X-Real-IP": "198.51.100.22", "Forwarded": "for=198.51.100.22;proto=https"}), 1):
            expect(f"forged_proxy_headers_login_{number}_still_limited", attacker.post("/api/auth/login", json={"username": "stage7_admin", "password": password}, headers=forged), 429)
        expect("source_B_correct_login_unaffected", legitimate.post("/api/auth/login", json={"username": "stage7_admin", "password": password}), 200)
        expect("source_A_still_limited", attacker.post("/api/auth/login", json={"username": "stage7_admin", "password": password}), 429)
    with client("127.0.0.4") as limited_source, client("127.0.0.5") as fresh_source:
        for index in range(5):
            expect(f"source_C_register_{index + 1}", limited_source.post("/api/auth/register", json={"username": f"stage7_quota_{index}", "password": password}), 201)
        response = expect("source_C_sixth_registration_limited", limited_source.post("/api/auth/register", json={"username": "stage7_quota_5", "password": password}), 429)
        assert int(response.headers["retry-after"]) > 0
        for number, forged in enumerate(({"X-Forwarded-For": "198.51.100.21"},
                {"X-Forwarded-For": "198.51.100.22, 127.0.0.1", "X-Real-IP": "198.51.100.22", "Forwarded": "for=198.51.100.22;proto=https"}), 1):
            expect(f"forged_proxy_headers_register_{number}_still_limited", limited_source.post("/api/auth/register", json={"username": f"stage7_forged_{number}", "password": password}, headers=forged), 429)
        expect("source_D_registration_unaffected", fresh_source.post("/api/auth/register", json={"username": "stage7_fresh_source", "password": password}), 201)
    return {"checks": checks, "checks_passed": len(checks), "empty_catalog_count": len(prices),
            "isolated_email_count": len(read_mail(folder)), "real_email_or_telegram_sent": False,
            "chunked_transfer_confirmed": True, "real_loopback_client_ips_tested": ["127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5"]}


def replay_history(folder):
    import httpx
    from backend.app import config
    config.DATA_DIR = folder / "data"
    from backend.scripts import replay_alerts as replay
    source = ROOT / "data/stage3-history"
    if not (source / "manifest.json").exists():
        return {"status": "skipped", "reason": "真实历史缓存缺失；未下载上游数据"}
    shutil.copytree(source, folder / "data/stage3-history")
    replay.ROOT = folder
    original_argv, original_root = sys.argv[:], config.ROOT
    config.ROOT = folder
    original_transport = httpx.HTTPTransport.handle_request
    httpx.HTTPTransport.handle_request = forbid_external
    try:
        sys.argv = ["replay_alerts.py"]
        with redirect_stdout(io.StringIO()):
            replay.main()
    finally:
        sys.argv, config.ROOT = original_argv, original_root
        httpx.HTTPTransport.handle_request = original_transport
    evidence = json.loads((folder / "data/stage3-replay.json").read_text(encoding="utf-8"))
    (OUTPUT / "stage7_replay.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    return {name: evidence[name] for name in ("status", "mode", "isolated_messages", "checks")}


def digest_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    import httpx
    OUTPUT.mkdir(parents=True, exist_ok=True)
    tools_folder = ROOT / ".tools"
    tools_folder.mkdir(exist_ok=True)
    old_replay = ROOT / "data/stage3-replay.json"
    old_digest = digest_file(old_replay)
    result = {"status": "failed", "mode": "isolated_real_http_and_cached_replay"}
    with tempfile.TemporaryDirectory(prefix="stage7-live-", dir=tools_folder) as temporary:
        folder = Path(temporary).resolve()
        assert folder.parent == tools_folder.resolve() and folder.name.startswith("stage7-live-")
        password = secrets.token_urlsafe(24)
        (folder / ".env").write_text("SESSION_COOKIE_SECURE=false\nADMIN_USERNAME=stage7_admin\nADMIN_PASSWORD=" + password + "\nRESEND_API_KEY=stage7-isolated-mail-placeholder\nEMAIL_FROM=sender@example.invalid\n", encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        process = None
        try:
            with (OUTPUT / "stage7_live_server.txt").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, __file__, "--serve", str(folder), str(port)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
                try:
                    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=3) as probe:
                        deadline = time.monotonic() + 25
                        while True:
                            try:
                                if probe.get("/api/health").status_code == 401:
                                    break
                            except httpx.ConnectError:
                                pass
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise RuntimeError("隔离服务启动失败，参见 stage7_live_server.txt")
                            time.sleep(0.1)
                    result["http"] = run_http(folder, port, password)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            result["isolated_server_stopped"] = process.poll() is not None
            result["process_privilege"] = json.loads((folder / "process_identity.json").read_text(encoding="utf-8"))
            with closing(sqlite3.connect(folder / "data/procurement.sqlite3")) as db:
                result["security_event_counts"] = dict(db.execute("SELECT kind,COUNT(*) FROM security_events GROUP BY kind"))
                seen = {row[0] for row in db.execute("SELECT DISTINCT client_hash FROM security_events")}
                wanted = {hashlib.sha256(("ip:" + ip).encode()).hexdigest() for ip in ("127.0.0.2", "127.0.0.3", "127.0.0.4", "127.0.0.5")}
                result["socket_source_ips_confirmed_in_security_log"] = wanted <= seen
                assert result["socket_source_ips_confirmed_in_security_log"]
                forged = {hashlib.sha256(("ip:" + ip).encode()).hexdigest() for ip in ("198.51.100.21", "198.51.100.22")}
                result["forged_proxy_ips_absent_from_security_log"] = not (forged & seen)
                assert result["forged_proxy_ips_absent_from_security_log"]
            result["replay"] = replay_history(folder)
            assert result["replay"]["status"] == "passed"
            result["old_replay_evidence_unchanged"] = digest_file(old_replay) == old_digest
            assert result["old_replay_evidence_unchanged"]
            result["status"] = "passed"
        except Exception as error:
            result["failure"] = {"type": type(error).__name__, "message": str(error)}
            raise
        finally:
            if process is not None:
                result["isolated_server_stopped"] = process.poll() is not None
            (OUTPUT / "stage7_live.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            (OUTPUT / "stage7_live.txt").write_text("STAGE7 LIVE_HTTP\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("STAGE7 LIVE_HTTP " + json.dumps(result, ensure_ascii=False))
    print("证据：" + "、".join(str(OUTPUT / name) for name in ("stage7_live.json", "stage7_live.txt", "stage7_replay.json")))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        main()
