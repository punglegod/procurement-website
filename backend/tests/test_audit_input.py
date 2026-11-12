"""安全审计证据：输入、SQL、SSRF、路径与错误；仅用隔离测试配置。"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event

from backend.app import main
from backend.app.adapters.common import AccessStopped, HttpSource, SourceError, validate_url
from backend.tests.test_accounts import accounts, PASSWORD, PAYLOAD


def test_request_body_limit(accounts):
    admin, a, b, ids, factory, alerts = accounts
    # 四 MiB 空白仍是合法 JSON；验证是否在解析前限制正文，不做压力攻击。
    body = b" " * (4 * 1024 * 1024) + json.dumps({"username": "account_a", "password": PASSWORD}).encode()
    response = a.post("/api/auth/login", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    print("AUDIT BODY_LIMIT " + json.dumps({"request_bytes": len(body), "status": response.status_code, "expected_protective_status": 413}))


def test_injection_types_and_error_redaction(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    attacks = {
        "sql_username": a.post("/api/auth/login", json={"username": "x' OR 1=1--", "password": PASSWORD}).status_code,
        "sql_code": a.get("/api/prices/history", params={"code": "alu_a00' OR 1=1--"}).status_code,
        "sql_rule_id": a.put("/api/alerts/rules/1%20OR%201=1", json=PAYLOAD).status_code,
        "header_injection_email": a.put("/api/me/push-settings", json={"email": "a@example.com\r\nBcc:b@example.com"}).status_code,
        "invalid_decimal": a.post("/api/alerts/rules", json=PAYLOAD | {"threshold": "NaN"}).status_code,
        "array_body": a.post("/api/alerts/rules", json=[]).status_code,
        "plain_text_body": a.post("/api/alerts/rules", content=json.dumps(PAYLOAD), headers={"Content-Type": "text/plain"}).status_code,
        "malformed_json": a.post("/api/alerts/rules", content="{", headers={"Content-Type": "application/json"}).status_code,
        "cross_origin": a.post("/api/alerts/rules", json=PAYLOAD, headers={"Origin": "https://evil.invalid"}).status_code,
    }
    assert attacks == {"sql_username": 422, "sql_code": 404, "sql_rule_id": 422, "header_injection_email": 422, "invalid_decimal": 422, "array_body": 422, "plain_text_body": 415, "malformed_json": 422, "cross_origin": 403}
    assert a.get("/api/alerts/rules").status_code == 200
    assert any("?" in statement and parameters for statement, parameters in statements)
    assert not any("OR 1=1" in statement for statement, _ in statements)
    event.remove(engine, "before_cursor_execute", capture)
    print("AUDIT INPUT_VALIDATION " + json.dumps(attacks, sort_keys=True))

    def failed(*args, **kwargs):
        raise RuntimeError("AUDIT_INTERNAL_PATH C:/private/server/file.py")

    monkeypatch.setattr(alerts, "logs", failed)
    with TestClient(main.app, raise_server_exceptions=False) as client:
        client.cookies.update(a.cookies)
        response = client.get("/api/alerts/logs")
        assert response.status_code == 500 and response.text == "Internal Server Error"
        print("AUDIT ERROR_REDACTION " + json.dumps({"status": 500, "body": response.text, "stack_or_internal_path": False}))


def test_ssrf_redirect_and_traversal(accounts):
    admin, a, b, ids, factory, alerts = accounts
    bad_urls = [
        "http://market.cnal.com/nanhai/", "https://127.0.0.1/", "https://[::1]/", "https://169.254.169.254/",
        "file:///etc/passwd", "https://market.cnal.com.evil.invalid/", "https://market.cnal.com@127.0.0.1/",
        "https://evilmarket.cnal.com/", "https://market.cnal.com:8443/", "https://user:pass@market.cnal.com/",
    ]
    for target in bad_urls:
        with pytest.raises(SourceError):
            validate_url(target, ("market.cnal.com",))
    requests = []

    def redirect(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})

    with httpx.Client(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(AccessStopped):
            HttpSource(interval=0).request(client, "GET", "https://market.cnal.com/")
    assert requests == ["https://market.cnal.com/"]
    print("AUDIT SSRF " + json.dumps({"disallowed_urls_rejected": len(bad_urls), "redirect_requests": len(requests), "internal_target_requested": False}))

    paths = ["/assets/%2e%2e/%2e%2e/AGENTS.md", "/assets/..%2f..%2f..%2fbackend/app/auth.py", "/assets/..%5c..%5cAGENTS.md", "/%2e%2e/backend/app/auth.py", "/assets/%252e%252e/%252e%252e/AGENTS.md"]
    statuses = []
    for path in paths:
        response = a.get(path)
        statuses.append(response.status_code)
        assert response.status_code == 404
    print("AUDIT PATH_TRAVERSAL " + json.dumps({"attempts": len(paths), "statuses": statuses, "source_disclosed": False}))
