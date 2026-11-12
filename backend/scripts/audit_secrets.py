"""只读扫描工作树及所有 Git 历史；仅输出位置与分类，绝不输出凭据值。"""
from pathlib import Path
import json
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values


def git(*args):
    return subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", *args], cwd=ROOT)


def main():
    values = dotenv_values(ROOT / ".env", interpolate=False)
    names = ("SCI99_USER", "SCI99_PASS", "SCI99_COOKIE", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "ADMIN_USERNAME", "ADMIN_PASSWORD", "RESEND_API_KEY")
    secrets = {key: value for key in names if (value := values.get(key)) and len(value) >= 6}
    patterns = {
        "telegram_token_shape": re.compile(r"(?<![A-Za-z0-9])\d{7,12}:[A-Za-z0-9_-]{30,50}"),
        "resend_key_shape": re.compile(r"\bre_[A-Za-z0-9]{20,}\b"),
        "openai_key_shape": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{30,}\b"),
        "github_key_shape": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "credential_assignment_review": re.compile(r"(?i)(?:ADMIN_PASSWORD|SCI99_PASS|SCI99_COOKIE|TELEGRAM_BOT_TOKEN|RESEND_API_KEY|password\s*[:=]|token\s*[:=])\s*[:=]?\s*[\"']?[^\s\"'\r\n]{8,}"),
    }
    hits = []
    exact = []

    def scan(label, raw):
        if b"\x00" in raw:
            return False
        content = raw.decode("utf-8", errors="replace")
        for key, secret in secrets.items():
            start = content.find(secret)
            if start >= 0:
                exact.append({"location": label, "line": content[:start].count("\n") + 1, "key": key})
        for kind, pattern in patterns.items():
            for match in pattern.finditer(content):
                hits.append({"location": label, "line": content[:match.start()].count("\n") + 1, "kind": kind})
        return True

    skipped = {".git", ".venv", "node_modules", ".tools", "__pycache__", ".pytest_cache"}
    import os
    working = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [name for name in dirs if name not in skipped and not name.startswith(".audit-")]
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(ROOT).as_posix()
            if rel == ".env" or name.startswith("audit_") or name.startswith("test_audit_") or name == "AUDIT_REPORT.md":
                continue
            if path.suffix.lower() in {".sqlite3", ".db", ".pyc", ".png", ".zip"} or name.endswith(("-wal", "-shm")):
                continue
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            try:
                working += scan("worktree:" + rel, path.read_bytes())
            except PermissionError:
                hits.append({"location": rel, "kind": "unreadable"})

    objects = git("rev-list", "--objects", "--all", "--reflog").decode().splitlines()
    blob_count = 0
    commit_count = 0
    for entry in objects:
        object_id, _, name = entry.partition(" ")
        kind = git("cat-file", "-t", object_id).decode().strip()
        if kind == "blob":
            blob_count += 1
            scan(f"git:{object_id[:12]}:{name}", git("cat-file", "blob", object_id))
        elif kind == "commit":
            commit_count += 1
            scan(f"commit:{object_id[:12]}", git("cat-file", "commit", object_id))
    result = {
        "scope": "all refs + reflogs, unique blobs and commit messages; worktree source/config/examples/logs/data text; excludes actual .env, dependencies, binary SQLite, audit-generated files",
        "head": git("rev-parse", "HEAD").decode().strip(),
        "commits_scanned": commit_count, "git_blobs_scanned": blob_count, "worktree_text_files_scanned": working,
        "configured_keys": [key for key in names if values.get(key)],
        "exact_matches": exact, "pattern_hits_for_manual_review": hits,
        "tracked_env_paths": git("ls-files", ".env", ".env.*").decode().splitlines(),
    }
    output = ROOT / "backend/scripts/audit_secrets_result.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "pattern_hits_for_manual_review"}, ensure_ascii=False, indent=2))
    print("pattern_hit_count=", len(hits), "; values suppressed; review metadata in", output.relative_to(ROOT))


if __name__ == "__main__":
    main()
