import httpx
import pytest


@pytest.fixture(autouse=True)
def isolated_account_env(tmp_path, monkeypatch):
    from backend.app import config
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    # 所有旧测试也必须使用临时凭据，不能读取正式 .env。
    (tmp_path / ".env").write_text("SESSION_COOKIE_SECURE=false\nADMIN_USERNAME=test_admin\nADMIN_PASSWORD=isolated-test-password\n", encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    # 单元测试只能使用 MockTransport 或 TestClient，避免意外请求真实行情/推送。
    def forbidden(*args, **kwargs):
        raise AssertionError("单元测试禁止真实网络请求")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
