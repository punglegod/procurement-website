from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app import service as service_module
from backend.app.adapters.common import AccessStopped, SourceError
from backend.app.adapters.free import FreeAdapter
from backend.app.adapters.sci99 import Sci99Adapter
from backend.app import config as config_module
from backend.app.config import COMMODITY_MAP, SOURCES
from backend.app.db import initialize_database, make_engine
from backend.app.models import PricePoint, SourceRun
from backend.app.service import FetchService, latest_prices


# 以下数字均为隔离测试用例，不作为真实行情，也不会写入项目 data 数据库。
FREE_HTML = '''<h1 class="tit">1月2日南海有色（灵通）铝锭价格</h1>
<div class="content_nav"><span class="time">2020-01-02 11:40:00</span></div>
<div class="marketcontent"><table><tr><td>单位：元/吨</td></tr>
<tr><td>品种</td><td>型号</td><td>价格区间</td><td>均价</td><td>涨跌</td></tr>
<tr><td rowspan="2">佛山地区（铝）</td><td>国产A00批售（送厂）</td><td>100-200</td><td>150</td><td>+10</td></tr>
<tr><td>国产A00批售（不含）</td><td>90-190</td><td>140</td><td>+10</td></tr>
</table></div>'''
URL = "https://market.cnal.com/nanhai/2020/01-02/test.shtml"
EMPTY_SETTINGS = {key: "" for key in ("SCI99_USER", "SCI99_PASS", "SCI99_COOKIE", "SCI99_ABS_URL", "SCI99_LOGIN_URL")}


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}")
    initialize_database(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


def test_free_parser_keeps_exact_basis_and_quote_date():
    point = FreeAdapter.parse(FREE_HTML, URL)
    assert point.price == Decimal("150")
    assert point.quote_date == date(2020, 1, 2)
    assert point.quote_basis == COMMODITY_MAP["alu_a00"].quote_basis
    assert point.source_url == URL


@pytest.mark.parametrize("html", [
    FREE_HTML.replace("国产A00批售（送厂）", "国产A00批售（不含）"),
    FREE_HTML.replace("<td>150</td>", "<td>151</td>"),
    FREE_HTML.replace("2020-01-02", "2099-01-02"),
    FREE_HTML.replace("2020-01-02", "2020-01-03"),
    FREE_HTML.replace("元/吨", "美元/吨"),
    FREE_HTML.replace("均价", "收盘价"),
])
def test_free_parser_rejects_drift_or_wrong_basis(html):
    with pytest.raises(SourceError):
        FreeAdapter.parse(html, URL)


def test_free_fetch_reads_index_and_article():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        if request.url.path == "/nanhai/":
            return httpx.Response(200, text=f'<a href="{URL}">1月2日南海有色（灵通）铝锭价格</a>')
        return httpx.Response(200, text=FREE_HTML)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        points = FreeAdapter(client=client, interval=0).fetch()
    assert len(points) == 1
    assert len(paths) == 2


def test_upsert_and_stale_status(factory, monkeypatch):
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: EMPTY_SETTINGS)
    service = FetchService(factory)
    service.store([FreeAdapter.parse(FREE_HTML, URL)], "free")
    service.store([FreeAdapter.parse(FREE_HTML, URL)], "free")
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PricePoint)) == 1
    rows = latest_prices(factory)
    assert len(rows) == 11
    assert rows[0]["stale"] is True
    assert rows[0]["price_point"]["quote_date"] == "2020-01-02"
    assert rows[1]["data_status"] == "pending_credentials"
    assert rows[2]["data_status"] == "pending_configuration"


def test_missing_credentials_never_calls_sci99_or_uses_quota(factory, monkeypatch):
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: EMPTY_SETTINGS)
    monkeypatch.setattr(FreeAdapter, "fetch", lambda _: [FreeAdapter.parse(FREE_HTML, URL)])

    def forbidden(_):
        pytest.fail("未配置凭据时不应请求卓创")

    monkeypatch.setattr(Sci99Adapter, "fetch", forbidden)
    results = FetchService(factory).run()["results"]
    assert results[1]["status"] == "pending_credentials"
    assert results[1]["outcome"] == "skipped"
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(SourceRun)) == 0


def test_daily_limit_persists_and_is_atomic(factory):
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: FetchService(factory).claim_sci99_day(), range(4)))
    assert claims.count(True) == 1
    assert FetchService(factory).claim_sci99_day() is False


def test_sci99_failure_isolated_and_not_retried_same_day(factory, monkeypatch):
    settings = EMPTY_SETTINGS | {"SCI99_COOKIE": "test=isolated"}
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: settings)
    monkeypatch.setattr(FreeAdapter, "fetch", lambda _: [FreeAdapter.parse(FREE_HTML, URL)])
    calls = []

    def blocked(_):
        calls.append(1)
        raise AccessStopped("触发验证码，已停止")

    monkeypatch.setattr(Sci99Adapter, "fetch", blocked)
    first = FetchService(factory).run()
    second = FetchService(factory).run()
    assert first["results"][0]["outcome"] == "success"
    assert first["results"][1]["status"] == "blocked"
    assert second["results"][1]["status"] == "daily_limit"
    assert second["sources"][1]["status"] == "blocked"
    assert len(calls) == 1


@pytest.mark.parametrize("status,text", [(403, ""), (429, ""), (200, "请完成验证"), (200, "访问过于频繁"), (503, "请完成验证")])
def test_sci99_stops_without_retry_on_access_control(status, text):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text=text)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings=EMPTY_SETTINGS)
        adapter.interval = 0
        with pytest.raises(AccessStopped):
            adapter.request(client, "GET", "https://ochem.sci99.com/product/abs", sci99=True)
    assert len(calls) == 1


def test_sci99_network_retry_is_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("isolated failure", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings=EMPTY_SETTINGS)
        adapter.interval = 0
        with pytest.raises(SourceError):
            adapter.request(client, "GET", "https://ochem.sci99.com/product/abs", sci99=True)
    assert len(calls) == 2


def test_sci99_does_not_treat_generic_product_page_as_price():
    with pytest.raises(SourceError):
        Sci99Adapter.parse("<h1>ABS</h1><p>报价请登录</p>", SOURCES["sci99"]["url"])


ABS_HTML = '''<table><tr><th>品种</th><th>市场</th><th>牌号</th><th>价格类型</th>
<th>均价</th><th>单位</th><th>报价日期</th></tr>
<tr><td>ABS</td><td>华东</td><td>0215A</td><td>现货</td><td>150</td><td>元/吨</td><td>2020-01-02</td></tr>
<tr><td>ABS</td><td>华南</td><td>0215A</td><td>现货</td><td>160</td><td>元/吨</td><td>2020-01-02</td></tr>
</table>'''


def test_sci99_cookie_fetch_parse_and_store_framework(factory):
    def handler(request):
        assert request.headers.get("cookie") == "test=isolated"
        return httpx.Response(200, text=ABS_HTML)

    settings = EMPTY_SETTINGS | {"SCI99_COOKIE": "test=isolated"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings=settings, client=client)
        adapter.interval = 0
        points = adapter.fetch()
    FetchService(factory).store(points, "sci99")
    with factory() as session:
        point = session.scalar(select(PricePoint))
        assert point.code == "abs"
        assert point.price == Decimal("150")
        assert point.quote_basis == COMMODITY_MAP["abs"].quote_basis


def test_sci99_plain_form_framework():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            assert b"username=isolated" in request.content
            assert b"password=test-only" in request.content
            return httpx.Response(200, text="<p>登录成功</p>")
        if request.url.host == "ucenter.sci99.com":
            return httpx.Response(200, text='''<form method="post" action="/login">
                <input name="username"><input name="password" type="password"></form>''')
        return httpx.Response(200, text=ABS_HTML)

    settings = EMPTY_SETTINGS | {"SCI99_USER": "isolated", "SCI99_PASS": "test-only"}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = Sci99Adapter(settings=settings, client=client)
        adapter.interval = 0
        assert adapter.fetch()[0].code == "abs"
    assert calls == ["GET", "POST", "GET"]


def test_credentials_only_read_root_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "ROOT", tmp_path)
    monkeypatch.setenv("SCI99_USER", "unexpected-env-user")
    monkeypatch.setenv("SCI99_PASS", "unexpected-env-pass")
    assert not config_module.sci99_configured(config_module.read_sci99_settings())
    (tmp_path / ".env").write_text("SCI99_COOKIE=literal=${TOKEN}\n", encoding="utf-8")
    assert config_module.read_sci99_settings()["SCI99_COOKIE"] == "literal=${TOKEN}"


@pytest.mark.parametrize("old,new,amount,percent", [
    ("120", "150", 30.0, 25.0),
    ("150", "120", -30.0, -20.0),
    ("150", "150", 0.0, 0.0),
    ("140", "150", 10.0, 7.14),
])
def test_latest_changes_compare_previous_quote_with_exact_basis(factory, monkeypatch, old, new, amount, percent):
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: EMPTY_SETTINGS)
    first = FreeAdapter.parse(FREE_HTML, URL)
    first.price = Decimal(old)
    FetchService(factory).store([first], "free")
    with factory() as session:
        # 无关口径即使日期更近，也不能参与涨跌对比。
        session.add(PricePoint(code="alu_a00", price=Decimal("999"), unit="元/吨",
                               quote_basis="隔离测试：其他口径", quote_date=date(2020, 1, 3),
                               source="free", source_name="隔离测试", source_url=URL, fetched_at=first.fetched_at))
        session.commit()
    second = FreeAdapter.parse(FREE_HTML, URL)
    second.price, second.quote_date = Decimal(new), date(2020, 1, 6)
    FetchService(factory).store([second], "free")
    row = latest_prices(factory)[0]
    assert row["change_amount"] == amount
    assert row["change_percent"] == percent
    assert row["previous_quote_date"] == "2020-01-02"


def test_latest_changes_are_null_without_comparison(factory, monkeypatch):
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: EMPTY_SETTINGS)
    row = latest_prices(factory)[0]
    assert row["change_amount"] is row["change_percent"] is row["previous_quote_date"] is None
    FetchService(factory).store([FreeAdapter.parse(FREE_HTML, URL)], "free")
    row = latest_prices(factory)[0]
    assert row["price_point"]["price"] == 150
    assert row["change_amount"] is row["change_percent"] is row["previous_quote_date"] is None
