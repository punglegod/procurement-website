import httpx
import pytest


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    # 单元测试只能使用 MockTransport 或 TestClient，避免意外请求真实行情/推送。
    def forbidden(*args, **kwargs):
        raise AssertionError("单元测试禁止真实网络请求")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
