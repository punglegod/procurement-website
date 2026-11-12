"""第八阶段：代理链、固定公开来源、防枚举和安全 Cookie，全部使用隔离配置。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from backend.app import auth, config, main
from backend.app.models import SecurityEvent, User
from backend.tests.test_accounts import accounts, PASSWORD
from backend.tests.test_stage7_start import startup

DOMAIN = "https://market.example.invalid"
PROXY = "192.0.2.10"


def attempt(client, origin):
    return client.post("/api/auth/login", headers={"Origin": origin, "Host": "market.example.invalid",
                       "X-Forwarded-Proto": "https", "X-Forwarded-For": "198.51.100.40"},
                       json={"username": "missing_account", "password": PASSWORD})


def test_https_proxy_uses_trusted_scheme_and_real_source(accounts, monkeypatch):
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    wrapped = ProxyHeadersMiddleware(main.app, trusted_hosts=[PROXY])
    with TestClient(wrapped, client=(PROXY, 40000)) as client:
        legitimate = attempt(client, DOMAIN)
        assert legitimate.status_code == 401  # 已走正常凭据校验，而非同源误拒。
        assert attempt(client, "https://outside.example.invalid").status_code == 403
    with accounts[4]() as session:
        event = session.scalar(select(SecurityEvent).where(SecurityEvent.kind == "login_failed"))
        assert event.client_hash == auth.digest("ip:198.51.100.40")
    print("STAGE8_TRUSTED_PROXY legal_origin=401 external_origin=403 actual_client_hash=True")


def test_public_origin_prevents_incident_even_without_proxy_scheme(accounts, monkeypatch):
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    # 同一请求：代理未受信时原回退逻辑会误拒；固定公开来源后不再依赖内部 scheme。
    wrapped = ProxyHeadersMiddleware(main.app, trusted_hosts=["192.0.2.99"])
    with TestClient(wrapped, client=(PROXY, 40000)) as client:
        assert attempt(client, DOMAIN).status_code == 403
        monkeypatch.setenv("PUBLIC_BASE_URL", DOMAIN)
        assert attempt(client, DOMAIN).status_code == 401
        assert attempt(client, "https://outside.example.invalid").status_code == 403
        assert attempt(client, "http://market.example.invalid").status_code == 403
        assert attempt(client, "null").status_code == 403
    print("STAGE8_INCIDENT before=403 configured_public_origin=401 external_origin=403")


@pytest.mark.parametrize("trust_proxy,configured_origin", [(True, False), (True, True), (False, True)])
def test_real_login_succeeds_with_secure_cookie_behind_https_entry(accounts, monkeypatch, trust_proxy, configured_origin):
    (config.ROOT / ".env").write_text("SESSION_COOKIE_SECURE=true\n", encoding="utf-8")
    if configured_origin:
        monkeypatch.setenv("PUBLIC_BASE_URL", DOMAIN)
    wrapped = ProxyHeadersMiddleware(main.app, trusted_hosts=[PROXY if trust_proxy else "192.0.2.99"])
    with TestClient(wrapped, base_url="http://market.example.invalid", client=(PROXY, 40000)) as client:
        response = client.post("/api/auth/login", headers={"Origin": DOMAIN, "X-Forwarded-Proto": "https",
                               "X-Forwarded-For": "198.51.100.42"},
                               json={"username": "account_a", "password": PASSWORD})
        assert response.status_code == 200 and response.json()["username"] == "account_a"
        cookie = response.headers["set-cookie"].lower()
        assert "secure" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    print(f"STAGE8_LOGIN trusted_proxy={trust_proxy} public_origin={configured_origin} status=200 secure_cookie=True")


def test_configured_origin_ignores_host_and_forwarded_origin_forgery(accounts, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", DOMAIN)
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    with TestClient(main.app, client=("198.51.100.41", 40000)) as client:
        body = {"username": "missing_account", "password": PASSWORD}
        assert client.post("/api/auth/login", json=body, headers={"Host": "attacker.invalid", "Origin": DOMAIN}).status_code == 401
        assert client.post("/api/auth/login", json=body, headers={"Host": "attacker.invalid", "Origin": "http://attacker.invalid", "X-Forwarded-Proto": "http"}).status_code == 403
        assert client.post("/api/auth/login", json=body, headers={"Origin": DOMAIN, "Sec-Fetch-Site": "cross-site"}).status_code == 403


def test_trusted_proxy_ip_buckets_are_separate_and_untrusted_headers_are_ignored(accounts, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_IP_PER_MINUTE", 2)
    wrapped = ProxyHeadersMiddleware(main.app, trusted_hosts=[PROXY])
    body = {"username": "account_a", "password": PASSWORD}
    with TestClient(wrapped, client=(PROXY, 40000)) as client:
        first = {"X-Forwarded-For": "198.51.100.51"}
        second = {"X-Forwarded-For": "198.51.100.52"}
        assert [client.post("/api/auth/login", json=body, headers=first).status_code for _ in range(3)] == [200, 200, 429]
        assert client.post("/api/auth/login", json=body, headers=second).status_code == 200
    with TestClient(wrapped, client=("192.0.2.20", 40000)) as direct:
        statuses = [direct.post("/api/auth/login", json=body, headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code for n in range(3)]
        assert statuses == [200, 200, 429]
    print("STAGE8_IP trusted_different_visitors_independent=True untrusted_header_rotation_blocked=True")


def test_registration_batch_has_identical_responses_and_preserves_existing_accounts(accounts, monkeypatch):
    hashed = []
    original = auth.hash_password
    monkeypatch.setattr(auth, "hash_password", lambda value: (hashed.append(value), original(value))[1])
    responses = []
    names = ["account_a", "brand_new_one", "account_b", "brand_new_two"]
    with TestClient(main.app, client=("198.51.100.70", 40000)) as client:
        for name in names:
            response = client.post("/api/auth/register", json={"username": name, "password": "probe-password-only"})
            responses.append((response.status_code, response.json(), response.headers.get("set-cookie"), response.headers.get("location")))
        assert all(response == responses[0] for response in responses) and responses[0][0] == 201
        assert set(responses[0][1]) == {"message"} and responses[0][2:] == (None, None)
    assert len(hashed) == 4
    with accounts[4]() as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.username.in_(names))) == 4
        old = session.scalar(select(User).where(User.username == "account_a"))
        assert auth.verify_password(old.password_hash, PASSWORD)
    managed = accounts[0].get("/api/admin/users").json()["items"]
    assert set(names) <= {row["username"] for row in managed}
    print("STAGE8_ENUM probes=4 identical_status_body_cookie_location=True all_hashes_computed=True")


def test_missing_origin_and_fetch_metadata_remains_accepted_risk(accounts, monkeypatch):
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    with TestClient(main.app, client=("198.51.100.72", 40000)) as client:
        assert client.post("/api/auth/login", json={"username": "missing_account", "password": PASSWORD}).status_code == 401


@pytest.mark.parametrize("value", ["ftp://market.invalid", "https://market.invalid/path", "https://u:p@market.invalid", "https://market.invalid?x=1", "https://market.invalid#x", "https://market.invalid:bad", "https://", "https://market.invalid\\bad"])
def test_invalid_public_origin_fails_closed(value, monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", value)
    with pytest.raises(ValueError, match="PUBLIC_BASE_URL"):
        config.read_public_origin()


def test_public_origin_file_environment_and_default_ports(monkeypatch):
    # ROOT 由 autouse fixture 指向临时目录，绝不读取项目真实 .env。
    (config.ROOT / ".env").write_text("PUBLIC_BASE_URL=https://market.example.invalid:443/\n", encoding="utf-8")
    assert config.read_public_origin() == DOMAIN
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8010")
    assert config.read_public_origin() == "http://localhost:8010"
    monkeypatch.setenv("PUBLIC_BASE_URL", "")
    assert config.read_public_origin() is None


def test_secure_cookie_default_and_local_http_opt_out(accounts):
    (config.ROOT / ".env").write_text("", encoding="utf-8")
    assert config.read_account_settings()["SESSION_COOKIE_SECURE"] == "true"
    with TestClient(main.app, base_url="https://testserver") as client:
        response = client.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD})
        assert response.status_code == 200 and "secure" in response.headers["set-cookie"].lower()
        assert client.get("/api/auth/me").status_code == 200
    (config.ROOT / ".env").write_text("SESSION_COOKIE_SECURE=false\n", encoding="utf-8")
    assert config.read_account_settings()["SESSION_COOKIE_SECURE"] == "false"


def test_https_start_without_proxy_warns_only(startup, monkeypatch, caplog):
    monkeypatch.setenv("PUBLIC_BASE_URL", DOMAIN)
    invoke, calls = startup
    assert invoke([]) == 0 and calls[0][1]["proxy_headers"] is False
    assert "安全配置警告" in caplog.text and "真实客户端 IP" in caplog.text
