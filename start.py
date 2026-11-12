"""构建前端并以单进程启动全站。"""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
FRONTEND = ROOT / "frontend"


def build_frontend():
    node = shutil.which("node")
    # 兼容本机已有的 Codex Node，普通环境优先使用 PATH。
    runtime = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
    if not node and (runtime / "bin/node.exe").is_file():
        node = str(runtime / "bin/node.exe")
    if not node:
        raise RuntimeError("请安装 Node.js 20.19+ 或 22.12+，或使用 --skip-build 运行已有构建。")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    npm_cli = Path(node).parent / "node_modules/npm/bin/npm-cli.js"
    if not npm_cli.is_file():
        npm_cli = runtime / "node_modules/npm/bin/npm-cli.js"
    if not npm_cli.is_file():
        npm_cli = ROOT / ".tools/npm/package/bin/npm-cli.js"
    command = [node, str(npm_cli)] if npm_cli.is_file() else [npm] if npm else None
    if not command:
        raise RuntimeError("未找到 npm，请安装完整的 Node.js 发行版。")
    env = os.environ.copy()
    env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
    lock_hash = hashlib.sha256((FRONTEND / "package-lock.json").read_bytes()).hexdigest()
    marker = FRONTEND / "node_modules/.procurement-lock.sha256"
    if not marker.is_file() or marker.read_text(encoding="utf-8") != lock_hash:
        subprocess.run([*command, "ci", "--no-fund", "--no-audit"], cwd=FRONTEND, env=env, check=True)
        marker.write_text(lock_hash, encoding="utf-8")
    subprocess.run([*command, "run", "build"], cwd=FRONTEND, env=env, check=True)


def main():
    parser = argparse.ArgumentParser(description="采购行情全站：构建 Vue 前端，启动 FastAPI 单服务")
    parser.add_argument("--skip-build", action="store_true", help="复用 frontend/dist，无需 Node.js")
    parser.add_argument("--port", type=int, default=8000, help="本地监听端口，默认 8000")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口应在 1～65535 之间")
    try:
        if not args.skip_build:
            build_frontend()
        if not (FRONTEND / "dist/index.html").is_file():
            raise RuntimeError("缺少 frontend/dist/index.html，请先运行不带 --skip-build 的启动命令。")
        import uvicorn
        os.chdir(ROOT)
        print(f"全站地址：http://127.0.0.1:{args.port}", flush=True)
        uvicorn.run("backend.app.main:app", host="127.0.0.1", port=args.port, workers=1)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
