"""卓创真实接口结构回归；全部使用脱敏样例和隔离数据库。"""
import json
import secrets
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, func

from backend.app import config
from backend.app.adapters.sci99 import ACCOUNT_URL, Sci99Adapter, Sci99Stopped
from backend.app.models import SourceRun, SiteSetting
from backend.app.source_settings import SourceSettings, Sci99Input, AUTH_STATE_SETTING
from backend.tests.test_accounts import accounts

FIXTURES = Path(__file__).parent / "fixtures/sci99"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def run_response(response, *, fetch=False):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["cookie"] == "test=isolated"
        return response

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings={"SCI99_COOKIE": "test=isolated"}, client=client)
        adapter.interval = 0
        try:
            result = adapter.fetch() if fetch else adapter.test_connection()
        except Sci99Stopped as error:
            result = error.status
    return result, calls


def test_cookie_uses_observed_get_api_once_and_never_html_or_quotes():
    result, calls = run_response(httpx.Response(200, json=fixture("user-info-valid.json")))
    assert result == "cookie_valid"
    assert len(calls) == 1 and str(calls[0].url) == ACCOUNT_URL and calls[0].method == "GET"


@pytest.mark.parametrize("status", [200, 401])
def test_observed_anonymous_code_is_invalid(status):
    result, calls = run_response(httpx.Response(status, json=fixture("user-info-unauthenticated.json")))
    assert result == "cookie_invalid" and len(calls) == 1


@pytest.mark.parametrize("payload", [
    None, [], "logged in", {}, {"code": 200}, {"code": 0, "data": {"user_info_id": 1, "user_name": "sample"}},
    {"code": True, "data": {"user_info_id": 1, "user_name": "sample"}},
    {"code": 200, "data": {"user_info_id": True, "user_name": "sample"}},
    {"code": 200, "data": {"user_info_id": 0, "user_name": "sample"}},
    {"code": 200, "data": {"user_info_id": 1, "user_name": " "}},
    {"code": 200, "data": {"user_info_id": "123", "user_name": "sample"}},
    {"code": 200, "success": False, "data": {"user_info_id": 1, "user_name": "sample"}},
    fixture("user-info-unexpected.json"),
])
def test_unknown_json_never_guesses_valid(payload):
    result, calls = run_response(httpx.Response(200, json=payload))
    assert result == "cookie_unconfirmed" and len(calls) == 1


@pytest.mark.parametrize("status,kwargs,expected", [
    (302, {"headers": {"Location": "https://ucenter.sci99.com/#/login"}}, "cookie_unconfirmed"),
    (200, {"text": "<p>退出登录 登录成功</p>"}, "cookie_unconfirmed"),
    (200, {"text": (FIXTURES / "ucenter-spa.html").read_text(encoding="utf-8")}, "cookie_unconfirmed"),
    (200, {"content": '{"code":200,broken', "headers": {"Content-Type": "application/json"}}, "cookie_unconfirmed"),
    (200, {"content": '{"code":4001,"code":200,"data":{"user_info_id":1,"user_name":"x"}}', "headers": {"Content-Type": "application/json"}}, "cookie_unconfirmed"),
    (401, {"json": {"message": "unknown"}}, "cookie_unconfirmed"),
    (403, {"json": {"code": 403, "message": "forbidden"}}, "cookie_unconfirmed"),
    (429, {"text": ""}, "access_blocked"),
    (503, {"text": "请完成验证"}, "access_blocked"),
    (500, {"json": {"message": "请输入验证码"}}, "access_blocked"),
    (200, {"json": {"code": 200, "message": "安全验证", "data": {"user_info_id": 1, "user_name": "x"}}}, "access_blocked"),
    (200, {"json": {"captchaRequired": True}}, "access_blocked"),
    (200, {"text": "<p>安全验证</p>", "headers": {"Content-Type": "application/json"}}, "access_blocked"),
    (403, {"text": "<div>请输入验证码</div>"}, "access_blocked"),
    (200, {"json": {"code": 200, "message": "请重新登录", "data": {"user_info_id": 1, "user_name": "x"}}}, "cookie_invalid"),
])
def test_redirect_nonjson_unknown_and_risk_stop_once(status, kwargs, expected):
    result, calls = run_response(httpx.Response(status, **kwargs))
    assert result == expected and len(calls) == 1


def test_password_spa_stops_without_post_or_js():
    calls = []
    from backend.app.source_settings import FIELDS
    settings = {FIELDS["username"]: "isolated", FIELDS["password"]: secrets.token_urlsafe(24), "SCI99_COOKIE": ""}
    with httpx.Client(transport=httpx.MockTransport(lambda r: (calls.append(r), httpx.Response(200, text=(FIXTURES / "ucenter-spa.html").read_text(encoding="utf-8")))[1])) as client:
        adapter = Sci99Adapter(settings=settings, client=client); adapter.interval = 0
        with pytest.raises(Sci99Stopped) as stopped:
            adapter.test_connection()
    assert stopped.value.status == "password_js_required"
    assert len(calls) == 1 and calls[0].method == "GET"


def test_quote_dynamic_shell_keeps_cookie_separate_and_does_not_guess():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == ACCOUNT_URL:
            return httpx.Response(200, json=fixture("user-info-valid.json"))
        return httpx.Response(200, text='<div id="app"></div><script>loadPriceApi()</script><article>ABS价格区间100-200</article>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings={"SCI99_COOKIE": "test=isolated"}, client=client); adapter.interval = 0
        with pytest.raises(Sci99Stopped) as stopped: adapter.fetch()
        assert adapter.auth_status == "cookie_valid"
    assert stopped.value.status == "quote_pending" and len(calls) == 2


def test_cookie_network_failure_is_unconfirmed_with_bounded_get_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("never expose this response or secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings={"SCI99_COOKIE": "test=isolated"}, client=client); adapter.interval = 0
        with pytest.raises(Sci99Stopped) as stopped: adapter.test_connection()
    assert stopped.value.status == "cookie_unconfirmed" and len(calls) == 2


@pytest.mark.parametrize("status", ["cookie_valid", "cookie_invalid", "cookie_unconfirmed", "access_blocked", "password_js_required"])
def test_admin_states_timestamp_source_visibility_and_redaction(accounts, monkeypatch, caplog, status):
    admin, ordinary = accounts[:2]; factory = accounts[4]
    key = Fernet.generate_key().decode(); monkeypatch.setattr(config, "read_settings_encryption_key", lambda: key)
    hidden = secrets.token_urlsafe(30)
    manager = SourceSettings(factory)
    manager.save(Sci99Input(enabled=False, cookie="sample=" + hidden))
    class Adapter:
        def __init__(self, **kwargs): pass
        def test_connection(self):
            if status == "cookie_valid": return status
            raise Sci99Stopped(status)
    result = manager.test_connection(1, "192.0.2.10", adapter_factory=Adapter)
    state = admin.get("/api/admin/sources/sci99").json()
    source = next(x for x in admin.get("/api/sources/status").json()["items"] if x["source"] == "sci99")
    assert result["status"] == state["auth_status"] == source["auth_status"] == status
    assert bool(state["verified_at"]) == (status == "cookie_valid")
    assert state["auth_checked_at"] and state["quote_status"] == source["quote_status"] == "quote_pending"
    assert not state["enabled"] and "报价页动态加载待核定" in source["message"]
    assert hidden not in json.dumps([result, state, source]) + caplog.text
    for path in ("/api/admin/sources/sci99", "/api/sources/status"):
        assert ordinary.get(path).status_code == 403
    assert ordinary.post("/api/admin/sources/sci99/test").status_code == 403
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SourceRun)) == 0


def test_credential_change_invalidates_validation_and_race_cannot_mark_new_cookie(accounts, monkeypatch):
    factory = accounts[4]; key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "read_settings_encryption_key", lambda: key)
    manager = SourceSettings(factory)
    manager.save(Sci99Input(enabled=False, cookie="test=isolated"))
    class Changed:
        def __init__(self, **kwargs): pass
        def test_connection(self):
            manager.save(Sci99Input(enabled=False, cookie="test=changed"))
            return "cookie_valid"
    assert manager.test_connection(1, "192.0.2.20", adapter_factory=Changed)["status"] == "configuration_changed"
    assert manager.state()["auth_status"] == "cookie_unconfirmed"
    _, revision = manager.resolve_for_verification()
    manager.record_auth_result("cookie_valid", revision)
    assert manager.state()["verified_at"]
    manager.save(Sci99Input(enabled=False))
    assert manager.state()["auth_status"] == "cookie_valid"
    manager.save(Sci99Input(enabled=False, cookie="test=new"))
    assert manager.state()["auth_status"] == "cookie_unconfirmed" and not manager.state()["verified_at"]
    manager.record_auth_result("cookie_valid", revision)
    assert manager.state()["auth_status"] == "cookie_unconfirmed"
    manager.save(Sci99Input(enabled=False, clear_credentials=True))
    assert manager.state()["auth_status"] == "pending_credentials"


def test_auth_record_contains_no_profile_or_credentials(accounts, monkeypatch):
    factory = accounts[4]; manager = SourceSettings(factory, legacy_reader=lambda: {"SCI99_COOKIE": "sample=private"})
    _, revision = manager.resolve_for_verification()
    manager.record_auth_result("cookie_valid", revision)
    with factory() as session:
        value = json.loads(session.get(SiteSetting, AUTH_STATE_SETTING).value)
    assert set(value) == {"revision", "status", "checked_at"} and "private" not in json.dumps(value)


def test_unclassified_failure_revokes_previous_valid_result(accounts):
    from backend.app.adapters.common import AccessStopped
    manager = SourceSettings(accounts[4], legacy_reader=lambda: {"SCI99_COOKIE": "sample=private"})
    _, revision = manager.resolve_for_verification()
    manager.record_auth_result("cookie_valid", revision)
    class Unknown:
        def __init__(self, **kwargs): pass
        def test_connection(self): raise AccessStopped("private response must not escape")
    result = manager.test_connection(1, "192.0.2.30", adapter_factory=Unknown)
    assert result["status"] == "blocked"
    assert manager.state()["auth_status"] == "cookie_unconfirmed" and not manager.state()["verified_at"]
    assert "private" not in result["message"]


@pytest.mark.parametrize("persist_failure", [False, True])
def test_fetch_stop_remembers_cookie_validity_without_switching_free_source(accounts, monkeypatch, persist_failure):
    from backend.app import service, config
    from backend.app.adapters.free import FreeAdapter
    from backend.app.service import FetchService
    from backend.app.source_settings import FIELDS
    import importlib
    factory = accounts[4]
    settings = {key: "" for key in FIELDS.values()} | {"SCI99_COOKIE": "sample=private"}
    monkeypatch.setattr(service, "read_sci99_settings", lambda: settings)
    monkeypatch.setitem(config.SOURCES["sci99"], "enabled", True)
    monkeypatch.setattr(FreeAdapter, "fetch", lambda self: [])
    for source in config.SOURCES.values():
        if source.get("adapter"):
            module, name = source["adapter"].split(":")
            monkeypatch.setattr(getattr(importlib.import_module("backend.app.adapters." + module), name), "fetch", lambda self: [])
    def pending(self):
        self.auth_status = "cookie_valid"
        raise Sci99Stopped("quote_pending")
    monkeypatch.setattr(Sci99Adapter, "fetch", pending)
    if persist_failure:
        def failed_record(*args): raise RuntimeError("private database error")
        monkeypatch.setattr(SourceSettings, "record_auth_result", failed_record)
    result = FetchService(factory).run(slot="09:00")
    row = next(x for x in result["sources"] if x["source"] == "sci99")
    assert row["status"] == row["quote_status"] == "quote_pending"
    if persist_failure:
        assert row["auth_status"] == "cookie_unconfirmed" and not row["verified_at"]
        # 免费 mock 没有报价应如实失败；仍须走到最后一个来源，不能被卓创写入错误中断。
        assert len(result["results"]) == 12 and result["results"][-1]["source"] == "free_paper"
    else:
        assert row["auth_status"] == "cookie_valid" and row["verified_at"] and "验证时间" in row["message"]
    assert config.COMMODITY_MAP["abs"].source == "free_abs" and row["daily_limit_reached"]
