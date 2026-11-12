"""密钥扫描命中二次复核：输出代码形态及分类标记，不输出凭据值。"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PATTERN = re.compile(r"(?i)(?:ADMIN_PASSWORD|SCI99_PASS|SCI99_COOKIE|TELEGRAM_BOT_TOKEN|RESEND_API_KEY|password\s*[:=]|token\s*[:=])\s*[:=]?\s*[\"']?[^\s\"'\r\n]{8,}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    report = json.loads((ROOT / "backend/scripts/audit_secrets_result.json").read_text(encoding="utf-8"))
    cache = {}
    records = {}
    for hit in report["pattern_hits_for_manual_review"]:
        location = hit["location"]
        if location not in cache:
            if location.startswith("worktree:"):
                raw = (ROOT / location.split(":", 1)[1]).read_bytes()
            else:
                blob = location.split(":", 2)[1]
                raw = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "cat-file", "blob", blob], cwd=ROOT)
            cache[location] = raw.decode("utf-8", errors="replace").split("\n")
        line = cache[location][hit["line"] - 1]
        # 特征只输出布尔值；字符串/赋值文本不输出，避免二次复制真实秘密。
        signature = hashlib.sha256(line.strip().encode()).hexdigest()[:12]
        if signature in records:
            records[signature]["occurrences"] += 1
            continue
        marker_words = [word for word in ("isolated", "test", "example", "placeholder", "fake", "dummy", "invalid", "replay", "smoke", "secret", "token_urlsafe", "getpass", "settings[", "v-model", "form.", "password_hash", "str", "Field", "dotenv", "assert", "\\n", "SECRET_") if word.lower() in line.lower()]
        safe = re.sub(r"(['\"])(?:\\.|(?!\1).)*?\1", "<quoted>", line)
        safe = re.sub(r"(?i)((?:password|token|cookie|api_key)[A-Za-z_]*\s*[:=])[^,;\s)}]+", r"\1<value>", safe)
        # 日志可包含嵌入补丁/JSON，多重转义后仍只输出形态，不输出整行。
        records[signature] = {"id": signature, "location": location, "line": hit["line"],
                              "occurrences": 1, "line_length": len(line), "markers": marker_words,
                              "shape": safe[:220] if len(line) < 1500 else "long_embedded_code_or_bundle"}
    supplement = re.compile(r"(?i)[\"']?(ADMIN_PASSWORD|SCI99_PASS|SCI99_COOKIE|TELEGRAM_BOT_TOKEN|RESEND_API_KEY|password|token|access_token|api_key|secret)[\"']?\s*[:=]\s*([\"'])((?:\\.|(?!\2)[^\r\n]){0,300})\2")
    candidates = {}
    objects = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "rev-list", "--objects", "--all", "--reflog"], cwd=ROOT).decode().splitlines()
    for entry in objects:
        object_id, _, name = entry.partition(" ")
        kind = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "cat-file", "-t", object_id], cwd=ROOT).decode().strip()
        if kind != "blob":
            continue
        label = f"git:{object_id[:12]}:{name}"
        if label not in cache:
            raw = subprocess.check_output(["git", "-c", f"safe.directory={ROOT.as_posix()}", "cat-file", "blob", object_id], cwd=ROOT)
            if b"\x00" not in raw:
                cache[label] = raw.decode("utf-8", errors="replace").split("\n")
    for label, lines in cache.items():
        content = "\n".join(lines)
        for match in supplement.finditer(content):
            value = match[3]
            fingerprint = hashlib.sha256(value.encode()).hexdigest()[:12]
            if fingerprint in candidates:
                candidates[fingerprint]["occurrences"] += 1
                continue
            line_no = content[:match.start()].count("\n") + 1
            code_line = lines[line_no - 1]
            safe_line = re.sub(r"(['\"])(?:\\.|(?!\1).)*?\1", "<quoted>", code_line)
            candidates[fingerprint] = {"id": fingerprint, "key": match[1], "length": len(value),
                "location": label, "line": line_no, "occurrences": 1,
                "markers": [word for word in ("isolated", "test", "literal", "mock", "dummy", "example", "invalid", "replay", "secret", "configured", "password", "NaN", "false", "missing") if word.lower() in value.lower()],
                "contains_interpolation": "${" in value or "{password}" in value,
                "shape": safe_line[:240] if len(code_line) < 1500 else "long_embedded_code_or_bundle"}
    for record in records.values():
        shape = record["shape"]
        if shape.strip().lstrip("+") in {"TELEGRAM_BOT_TOKEN=", "RESEND_API_KEY="}:
            category = "empty_template"
        elif shape == "long_embedded_code_or_bundle" or record["id"] in {"435a2d5999c1", "18e0d857661d"}:
            category = "public_sdk_or_compiled_frontend"
        elif any(word in shape for word in ("write_text", "assert ", "test_sci99_", "PASSWORD =", "expect(")):
            category = "isolated_tests_or_smoke_configuration"
        elif any(word in shape for word in ("settings[", "request.cookies", "secrets.token_urlsafe", "a.cookies", "getpass(", "form.css_first", "$loginBody", "password.value", "oldPassword.value")):
            category = "runtime_variable_or_generation"
        elif "raise RuntimeError" in shape or "const pending" in shape:
            category = "configuration_error_or_placeholder_message"
        else:
            category = "documentation_or_instructions"
        record["review_category"] = category
    category_counts = Counter()
    for record in records.values():
        category_counts[record["review_category"]] += record["occurrences"]
    output = {"hits": len(report["pattern_hits_for_manual_review"]), "unique_lines": len(records),
              "reviewed_hit_categories": dict(category_counts),
              "records": list(records.values()), "supplemental_quoted_candidates": list(candidates.values())}
    path = ROOT / "backend/scripts/audit_secrets_review_shapes.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
