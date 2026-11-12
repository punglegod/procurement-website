"""审计部署声明与实际健康命令，仅启动本地隔离 HTTP 服务。"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread

import yaml


ROOT = Path(__file__).resolve().parents[2]


class Handler(BaseHTTPRequestHandler):
    status = 401

    def do_GET(self):
        self.send_response(self.status)
        self.end_headers()

    def log_message(self, *args):
        pass


def main():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    server = HTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    command = web["healthcheck"]["test"]
    code = command[3].replace("127.0.0.1:8000", f"127.0.0.1:{server.server_port}")
    results = {}
    try:
        for status in (401, 200, 500):
            Handler.status = status
            result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
            results[str(status)] = result.returncode
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
    inline = web["build"]["dockerfile_inline"]
    result = {"docker_cli_available": bool(shutil.which("docker")),
              "inline_user_nonroot": "USER 10001:10001" in inline,
              "single_worker": '"--workers", "1"' in inline,
              "loopback_port": web["ports"],
              "data_directory_owned_by_runtime_user": "chown -R 10001:10001 /app/data" in inline,
              "env_read_only": next(m for m in web["volumes"] if isinstance(m, dict))["read_only"],
              "health_command_exit_by_mock_status": results,
              "image_build_or_container_start": "not_run_without_docker"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert results == {"401": 0, "200": 1, "500": 1}
    assert result["inline_user_nonroot"] and result["single_worker"] and result["env_read_only"]


if __name__ == "__main__":
    main()
