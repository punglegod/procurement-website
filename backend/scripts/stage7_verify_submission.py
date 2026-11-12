"""提交前只读核对：敏感配置仅在内存精确匹配，输出绝不包含配置值。"""
import json
from pathlib import Path
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def git(*arguments):
    return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", *arguments], cwd=ROOT)


def main():
    paths = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").decode().splitlines()
    values = dotenv_values(ROOT / ".env", interpolate=False)
    names = ("SCI99_USER", "SCI99_PASS", "SCI99_COOKIE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ADMIN_USERNAME", "ADMIN_PASSWORD", "RESEND_API_KEY")
    sensitive = {key: value for key in names if (value := values.get(key)) and len(value) >= 6}
    hits = []
    for path in paths:
        content = git("show", ":" + path).decode("utf-8", errors="replace")
        for key, value in sensitive.items():
            if value in content:
                hits.append({"path": path, "key": key})
    prohibited = [path for path in paths if path in {".env", "PROMPTS.md", "backend/app/config.py"}
                  or path.startswith(("data/", "backend/app/adapters/", "run_", "stage1-prompt", "stage2-prompt", "stage3-prompt", "stage4-prompt", "stage5-prompt", "stage7-prompt"))]
    result = {"staged_files_scanned": len(paths), "configured_secret_exact_matches": hits,
              "out_of_scope_staged_paths": prohibited,
              "scope": "staged text only; exact matching of nonempty configured values >=6 characters; no secret values emitted"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert paths and not hits and not prohibited


if __name__ == "__main__":
    main()
