"""真实公开响应片段回归与失败变异；只写临时数据库，不发送真实请求。"""

from decimal import Decimal
from importlib import import_module
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app import service as service_module
from backend.app import main as main_module
from backend.app.adapters.common import AccessStopped, SourceError, check_access
from backend.app.adapters.free import FreeAdapter
from backend.app.adapters.sci99 import Sci99Adapter
from backend.app.config import COMMODITY_MAP, SOURCES
from backend.app.db import initialize_database, make_engine
from backend.app.models import PricePoint, SourceRun
from backend.app.service import FetchService, latest_prices

EVIDENCE = Path(__file__).parent / "evidence"
CODES = [c for c in COMMODITY_MAP if c != "alu_a00"]


def adapter(code):
    module, name = SOURCES[COMMODITY_MAP[code].source]["adapter"].split(":")
    return getattr(import_module(f"backend.app.adapters.{module}"), name)()


def html(code):
    filename = "yjgmall" if code in ("dmc", "silicone_rubber") else code
    return (EVIDENCE / f"{filename}.html").read_text(encoding="utf-8")


def record(code):
    return json.loads((EVIDENCE / f"{code}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", CODES)
def test_real_public_fragments_preserve_price_basis_and_metadata(code):
    item = record(code)
    point = adapter(code).parse(html(code), item["source_url"])
    assert point.price == Decimal(str(item["price"]))
    # 纸张片段采于 11月10日，次日真实 fetch 证据单独记录；均为同一供应商同一规格。
    assert point.quote_date.isoformat() == ("2026-11-10" if code == "paper" else item["quote_date"])
    assert point.source == COMMODITY_MAP[code].source
    assert point.unit == "元/吨"
    assert point.quote_basis == item["quote_basis"]
    assert point.source_name and point.source_url and point.fetched_at


@pytest.mark.parametrize("code,old,new", [
    ("abs", "华南ABS市场", "华东ABS市场"),
    ("abs", "0215A/中石油吉化（揭阳）", "0215A/其他厂商"),
    ("pc", "WY-111BR/山东利华益维远", "WY-111BR/其他厂商"),
    ("pe", "LLDPE/DFDA 7042/中石化海南", "HDPE/DFDA 7042/中石化海南"),
    ("pp", "T30S/福建中景石化", "T30S/其他厂商"),
    ("abs", "元/吨", "美元/吨"),
    ("abs", "型号/制造商", "期货合约"),
    ("abs", "11月11日", "11月12日"),
    ("pmma", '"slug": "pmma"', '"slug": "pet"'),
    ("pmma", '"spot":', '"futures":'),
    ("pmma", "数据截至 2026-11-09", "数据截至 2026-11-10"),
    ("lithium_carbonate", "ID01551919", "ID01720085"),
    ("lithium_carbonate", "Li2CO3≥99.5%", "Li2CO3≥99.2%"),
    ("lithium_carbonate", "89200", "89***"),
    ("lithium_carbonate", "88300", "90300"),
    ("lithium_carbonate", "品位", "基准合约"),
    ("dmc", "DMC：13800", "裂解料DMC：13800"),
    ("dmc", "价格统计时间：11月11日", "价格统计时间不详"),
    ("silicone_rubber", "普通生胶：14800", "沉淀混炼胶：14800"),
    ("bopet", "12μ印刷基膜", "6μ镀铝基膜"),
    ("bopet", "江浙", "山东"),
    ("bopet", "10400-10600", "10700-10600"),
    ("paper", "140;", "120;"),
    ("paper", "上海兆美纸业有限公司", "其他纸厂"),
    ("paper", "2900元/吨", "2900美元/吨"),
])
def test_reject_wrong_spec_units_date_hidden_or_futures(code, old, new):
    original = html(code)
    assert old in original
    changed = original.replace(old, new)
    with pytest.raises(SourceError):
        adapter(code).parse(changed, record(code)["source_url"])


@pytest.mark.parametrize("code", CODES)
def test_reject_future_quote_dates(code):
    changed = html(code).replace("2026", "2099")
    with pytest.raises(SourceError):
        adapter(code).parse(changed, record(code)["source_url"])


@pytest.mark.parametrize("code", CODES)
def test_reject_empty_or_login_page(code):
    with pytest.raises(SourceError):
        adapter(code).parse("<h1>请登录后查看</h1>", record(code)["source_url"])


def test_all_free_sources_isolated_sci99_dormant_and_upsert(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{tmp_path / 'stage4.sqlite3'}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(service_module, "read_sci99_settings", lambda: {"SCI99_COOKIE": "test-only"})

    def forbidden(_):
        pytest.fail("休眠卓创不得执行")

    def blocked(_):
        raise AccessStopped("隔离测试：访问受限")

    monkeypatch.setattr(Sci99Adapter, "fetch", forbidden)
    monkeypatch.setattr(FreeAdapter, "fetch", blocked)
    for code in CODES:
        cls = type(adapter(code))
        monkeypatch.setattr(cls, "fetch", lambda self, code=code: [self.parse(html(code), record(code)["source_url"])])
    service = FetchService(factory)
    try:
        first, second = service.run(), service.run()
        assert first["results"][0]["status"] == "blocked"
        assert first["results"][1]["status"] == "configured_disabled"
        assert sum(r["outcome"] == "success" for r in first["results"]) == 10
        assert sum(r["outcome"] == "success" for r in second["results"]) == 10
        with factory() as session:
            assert session.scalar(select(func.count()).select_from(PricePoint)) == 10
            assert session.scalar(select(func.count()).select_from(SourceRun)) == 0
        rows = latest_prices(factory)
        assert sum(r["price_point"] is not None for r in rows) == 10
        assert all(r["change_amount"] is None for r in rows)
        # 一个新源失败后仍保留其真实旧报价，其他品种继续成功。
        monkeypatch.setattr(type(adapter("abs")), "fetch", blocked)
        third = service.run()
        assert next(r for r in third["results"] if r["source"] == "free_abs")["status"] == "blocked"
        assert latest_prices(factory)[1]["price_point"]["price"] == record("abs")["price"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("content", ["正在进行安全检查", "<script>document.cookie='HW_CHECK=x'</script>", "访问过于频繁"])
def test_free_sources_stop_on_security_checks(content):
    with pytest.raises(AccessStopped):
        check_access(httpx.Response(200, text=content))


def test_scheduler_single_daily_job_and_fetch_busy_route(tmp_path, monkeypatch):
    from backend.app import auth
    engine = make_engine(f"sqlite:///{tmp_path / 'api.sqlite3'}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(main_module, "initialize_database", lambda: None)
    monkeypatch.setattr(main_module, "service", FetchService(factory))
    try:
        with TestClient(main_module.app) as client:
            assert client.post("/api/auth/login", json={"username": "test_admin", "password": "isolated-test-password"}).status_code == 200
            jobs = {j.id: j for j in main_module.app.state.scheduler.get_jobs()}
            assert set(jobs) == {"prices_0800", "prices_0830", "prices_0900", "prices_1530", "alerts", "morning_monitor"}
            assert str(jobs["prices_1530"].trigger) == "cron[hour='15', minute='30']"
            assert str(jobs["prices_1530"].trigger.timezone) == "Asia/Shanghai"
            assert jobs["alerts"].trigger.interval.total_seconds() == 300
            assert all(j.max_instances == 1 for j in jobs.values())
            assert service_module.run_lock.acquire(blocking=False)
            try:
                assert client.post("/api/fetch/run").status_code == 409
            finally:
                service_module.run_lock.release()
            assert client.get("/api/health").json() == {"status": "ok"}
    finally:
        engine.dispose()
