"""启动真实但完全隔离的 HTTP 服务，复验访问控制和历史回放。"""
from pathlib import Path
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def serve(folder, port):
    import httpx
    from backend.app import config
    # 数据路径先替换再导入数据库模块；主站 .env 与 SQLite 从未打开。
    config.DATA_DIR = folder / "data"
    from backend.app import main
    config.ROOT = folder
    if os.name == "nt":
        import ctypes
        privilege = {"windows_elevated_admin_token": bool(ctypes.windll.shell32.IsUserAnAdmin())}
    else:
        privilege = {"effective_uid": os.geteuid()}
    (folder / "process_identity.json").write_text(json.dumps(privilege), encoding="utf-8")

    def forbidden(*args, **kwargs):
        raise RuntimeError("审计隔离服务禁止任何外部请求")

    httpx.HTTPTransport.handle_request = forbidden
    import uvicorn
    uvicorn.run(main.app, host="127.0.0.1", port=port, workers=1, log_level="warning")


def main():
    import httpx
    scratch = Path(tempfile.mkdtemp(prefix="audit-live-", dir=ROOT / ".tools"))
    password = secrets.token_urlsafe(24)
    (scratch / ".env").write_text("ADMIN_USERNAME=audit_admin\nADMIN_PASSWORD=" + password + "\n", encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = ROOT / "backend/scripts/audit_live_server_output.txt"
    result = {}
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, __file__, "--serve", str(scratch), str(port)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        response = client.get("/api/health")
                        break
                    except httpx.ConnectError:
                        if process.poll() is not None or time.monotonic() >= deadline:
                            raise RuntimeError("隔离 HTTP 服务未能启动，检查审计服务日志")
                        time.sleep(0.1)
                result["anonymous_health"] = response.status_code
                result["anonymous_home"] = client.get("/", follow_redirects=False).status_code
                result["login"] = client.post("/api/auth/login", json={"username": "audit_admin", "password": password}).status_code
                result["authenticated_health"] = client.get("/api/health").status_code
                result["authenticated_home"] = client.get("/").status_code
                result["empty_price_catalog_count"] = len(client.get("/api/prices/latest").json()["items"])
                result["source_status"] = client.get("/api/sources/status").status_code
                result["oversized_json_4MiB"] = client.post("/api/auth/login", content=b" " * (4 * 1024 * 1024) + json.dumps({"username": "audit_admin", "password": password}).encode(), headers={"Content-Type": "application/json"}).status_code
                result["register"] = client.post("/api/auth/register", json={"username": "audit_user", "password": password}).status_code
                client.post("/api/auth/login", json={"username": "audit_user", "password": password}).raise_for_status()
                result["ordinary_user_admin_gate"] = client.get("/api/admin/users").status_code
                result["oversized_page"] = client.get("/api/alerts/logs?page=9223372036854775808&page_size=100").status_code
                result["logout"] = client.post("/api/auth/logout").status_code
                result["after_logout"] = client.get("/api/health").status_code
            assert result == {"anonymous_health": 401, "anonymous_home": 303, "login": 200, "authenticated_health": 200, "authenticated_home": 200, "empty_price_catalog_count": 11, "source_status": 200, "oversized_json_4MiB": 200, "register": 201, "ordinary_user_admin_gate": 403, "oversized_page": 500, "logout": 200, "after_logout": 401}
        finally:
            process.terminate()
            process.wait(timeout=15)
    result["isolated_server_stopped"] = process.poll() is not None
    result["process_privilege"] = json.loads((scratch / "process_identity.json").read_text(encoding="utf-8"))
    print("AUDIT LIVE_HTTP " + json.dumps(result, ensure_ascii=False))

    # 将真实历史缓存复制到隔离目录，原回放脚本输出也只写隔离目录。
    import backend.scripts.replay_alerts as replay
    from backend.app import config
    source = ROOT / "data/stage3-history"
    target = scratch / "data/stage3-history"
    if (source / "manifest.json").is_file():
        shutil.copytree(source, target)
        replay.ROOT = scratch
        original_argv = sys.argv[:]
        try:
            sys.argv = ["replay_alerts.py"]
            replay.main()
        finally:
            sys.argv = original_argv
            config.ROOT = ROOT
        evidence = json.loads((scratch / "data/stage3-replay.json").read_text(encoding="utf-8"))
        destination = ROOT / "backend/scripts/audit_replay_result.json"
        destination.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        print("AUDIT REPLAY " + json.dumps({"status": evidence["status"], "mode": evidence["mode"], "isolated_messages": evidence["isolated_messages"], "checks": evidence["checks"]}, ensure_ascii=False))
    else:
        print("AUDIT REPLAY skipped: existing real history cache unavailable; no upstream downloads")
    # 隔离目录保留供复核，无须删除任何正式文件。
    print("AUDIT ISOLATED_ARTIFACTS " + str(scratch.relative_to(ROOT)))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        main()
