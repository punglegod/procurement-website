"""路由、自动额度和手动选品类；全部临时库/mock，不连接上游或真实通知。"""
from datetime import datetime, timedelta
from decimal import Decimal
import importlib

from cryptography.fernet import Fernet
import pytest
from sqlalchemy import select, func

from backend.app import config, service as service_module, main
from backend.app.adapters.free import FreeAdapter
from backend.app.adapters.sci99 import Sci99Adapter, Sci99Stopped
from backend.app.models import PricePoint, SourceRun, QuoteObservation, PushLog, SiteSetting
from backend.app.quotes import QuoteRow, choose, store_row
from backend.app.fetch_policy import SCI99_CODES, captured_codes
from backend.app.service import FetchService
from backend.app.source_settings import SourceSettings, Sci99Input
from backend.app.rounds import RoundRunner
from backend.app.notifications import NotificationService, build_digest
from backend.tests.test_accounts import accounts

AT=datetime(2026,11,13,16,30,tzinfo=config.TIMEZONE)


@pytest.fixture
def routed(accounts,monkeypatch):
    factory=accounts[4];calls=[]
    monkeypatch.setattr(service_module,"now",lambda:AT)
    key=Fernet.generate_key().decode();monkeypatch.setattr(config,"read_settings_encryption_key",lambda:key)
    manager=SourceSettings(factory);manager.save(Sci99Input(enabled=True,cookie="test=isolated"))
    state={"sci_fail":set(),"free_fail":set(),"empty":False,"old_free":False,"old_sci":False,"blocked":False}
    def free(code):
        calls.append(("free",code))
        if code in state["free_fail"]:return []
        item=config.COMMODITY_MAP[code]
        return [PricePoint(code=code,price=Decimal("100"),unit=item.unit,quote_basis=item.quote_basis,
            quote_date=AT.date()-timedelta(days=1) if state["old_free"] else AT.date(),source=item.source,
            source_name="隔离免费源",source_url="https://example.invalid/quote",fetched_at=AT.replace(tzinfo=None))]
    monkeypatch.setattr(FreeAdapter,"fetch",lambda self:free("alu_a00"))
    for item in config.COMMODITIES:
        if item.code=="alu_a00":continue
        module,name=config.SOURCES[item.source]["adapter"].split(":")
        monkeypatch.setattr(getattr(importlib.import_module("backend.app.adapters."+module),name),"fetch",lambda self,code=item.code:free(code))
    def sci(self):
        codes=self.codes or list(SCI99_CODES);calls.append(("sci99",tuple(codes)))
        if state["blocked"]:raise Sci99Stopped("access_blocked")
        self.auth_status="cookie_valid"
        self.quote_results=[{"code":code,"status":"failed" if code in state["sci_fail"] else "ok","message":"隔离解析结果"} for code in codes]
        return [QuoteRow(code=code,product_name=config.COMMODITY_MAP[code].name,specification="隔离规格",market="华东",
            price_type="市场价格",price=None if state["empty"] else Decimal(100+sum(c[0]=="sci99" for c in calls)),
            price_text="-" if state["empty"] else str(100+sum(c[0]=="sci99" for c in calls)),
            quote_date=AT.date()-timedelta(days=1) if state["old_sci"] else AT.date(),unit="元/吨",source="sci99",
            source_name="卓创资讯",source_url="https://ochem.sci99.com/product/abs",fetched_at=AT.replace(tzinfo=None)) for code in codes if code not in state["sci_fail"]]
    monkeypatch.setattr(Sci99Adapter,"fetch",sci)
    accounts[5].clock=lambda:AT
    return accounts,FetchService(factory),manager,calls,state


def test_enabled_and_disabled_category_routing(routed):
    accounts,fetcher,manager,calls,_=routed
    result=fetcher.run(codes=["alu_a00","abs"])
    assert calls==[("free","alu_a00"),("sci99",("abs",))]
    assert [r["code"] for r in result["commodity_results"]]==["alu_a00","abs"]
    calls.clear();manager.save(Sci99Input(enabled=False))
    fetcher.run(codes=["alu_a00","abs"])
    assert calls==[("free","alu_a00"),("free","abs")]


def test_four_rounds_wait_for_sci_and_skip_already_captured_a00(routed):
    _,fetcher,_,calls,_=routed
    for slot in ("08:00","08:30","09:00","15:30"):
        output=fetcher.run(slot=slot)
        assert all(r["status"]=="scheduled_only" for r in output["commodity_results"] if r["code"] in SCI99_CODES)
    assert calls==[("free","alu_a00")]
    assert output["commodity_results"][0]["message"]=="今日已抓取"
    output=fetcher.run(slot="16:30")
    assert calls[-1]==("sci99",SCI99_CODES)
    assert len(output["commodity_results"])==10 and all(r["outcome"]=="success" for r in output["commodity_results"])


def test_auto_sci_failure_falls_back_same_round_only_failed_codes(routed):
    accounts,fetcher,_,calls,state=routed;state["sci_fail"]={"pc","paper"}
    output=fetcher.run(slot="16:30")
    assert [c for c in calls if c[0]=="free"]==[("free","pc"),("free","paper")]
    assert all(r["outcome"]=="success" for r in output["commodity_results"])
    assert {r["code"] for r in output["commodity_results"] if r.get("fallback")}=={"pc","paper"}
    with accounts[4]() as session:
        assert set(SCI99_CODES)<=captured_codes(session,AT.date())
        assert session.scalar(select(func.count()).select_from(SourceRun))==1
    calls.clear();fetcher.run(slot="16:30");assert calls==[]


def test_risk_stop_gets_same_round_free_fallback_for_all_selected(routed):
    _,fetcher,_,calls,state=routed;state["blocked"]=True
    output=fetcher.run(slot="16:30")
    assert sum(c[0]=="sci99" for c in calls)==1
    assert [c[1] for c in calls if c[0]=="free"]==list(SCI99_CODES)
    assert all(r.get("fallback") for r in output["commodity_results"])


def test_manual_selected_sci_unlimited_does_not_take_auto_quota_or_skip(routed):
    accounts,fetcher,_,calls,_=routed
    for _ in range(3):
        output=fetcher.run(codes=["pc","paper"])
        assert all(r["outcome"]=="success" for r in output["commodity_results"])
        assert {r["code"] for r in output["commodity_results"]}=={"pc","paper"}
    assert calls==[("sci99",("pc","paper"))]*3
    with accounts[4]() as session:
        assert session.scalar(select(func.count()).select_from(SourceRun))==0
        assert not captured_codes(session,AT.date())
        assert session.scalar(select(func.count()).select_from(QuoteObservation))==2
        assert all(p.price==103 for p in session.scalars(select(QuoteObservation)))
    fetcher.run(slot="16:30")
    assert calls[-1]==("sci99",SCI99_CODES)
    # 自动额度耗尽仍可手动刷新已抓品类。
    fetcher.run(codes=["pc"]);assert calls[-1]==("sci99",("pc",))


def test_disabled_sci_four_free_rounds_persist_skip_and_manual_ignores_it(routed):
    accounts,fetcher,manager,calls,_=routed;manager.save(Sci99Input(enabled=False))
    fetcher.run(slot="08:00");assert len(calls)==11
    calls.clear()
    for slot in ("08:30","09:00","15:30"):
        output=FetchService(accounts[4]).run(slot=slot)
        assert all(r["message"]=="今日已抓取" for r in output["commodity_results"])
    assert not calls
    fetcher.run(codes=["abs"]);assert calls==[("free","abs")]
    calls.clear();fetcher.run(slot="16:30");assert not calls


def test_missing_old_or_failed_prices_do_not_mark_auto_captured(routed):
    accounts,fetcher,manager,calls,state=routed
    state["empty"]=True;state["old_free"]=True
    result=fetcher.run(slot="16:30")
    assert all(r.get("fallback") for r in result["commodity_results"])
    with accounts[4]() as session:assert not captured_codes(session,AT.date())
    manager.save(Sci99Input(enabled=False));calls.clear();state["free_fail"]={"abs"}
    for slot in ("08:00","08:30"):fetcher.run(slot=slot)
    assert sum(c==("free","abs") for c in calls)==2
    with accounts[4]() as session:assert not captured_codes(session,AT.date())


def test_auto_day_resets_and_manual_prior_price_is_not_auto_proof(routed,monkeypatch):
    _,fetcher,manager,calls,_=routed;manager.save(Sci99Input(enabled=False))
    fetcher.run(codes=["abs"]);calls.clear();fetcher.run(slot="08:00")
    assert ("free","abs") in calls
    calls.clear();monkeypatch.setattr(service_module,"now",lambda:AT+timedelta(days=1))
    fetcher.run(slot="08:30");assert len(calls)==11


def test_admin_api_selection_validation_and_ordinary_403(routed):
    accounts,_,_,calls,_=routed;admin,ordinary=accounts[:2]
    assert ordinary.post("/api/fetch/run",json={"codes":["pc"]}).status_code==403
    for payload in [{"codes":[]},{"codes":["pc","pc"]},{"codes":["no-such-code"]},{"codes":["pc"],"slot":"16:30"}]:
        assert admin.post("/api/fetch/run",json=payload).status_code==422
    assert not calls
    response=admin.post("/api/fetch/run",json={"codes":["pc","paper"]})
    assert response.status_code==200 and {r["code"] for r in response.json()["commodity_results"]}=={"pc","paper"}
    assert calls==[("sci99",("pc","paper"))]


def test_progress_reports_manual_scope_and_today_skips_admin_only(routed):
    accounts,fetcher,manager,_,_=routed;alerts=accounts[5]
    runner=RoundRunner(fetcher,NotificationService(alerts))
    result=runner.run(codes=["pc","paper"])
    assert result["summary"]["codes"]==["pc","paper"] and len(result["summary"]["items"])==2
    assert "共 2 个品种" in alerts.sender.calls[0]
    assert accounts[1].get("/api/alerts/logs").json()["total"]==0
    manager.save(Sci99Input(enabled=False));runner.run("08:00");runner.run("08:30")
    assert "今日已抓取" in alerts.sender.calls[-1] and "跳过 11" in alerts.sender.calls[-1]


def test_five_jobs_exact_timezone_and_only_nine_triggers_digest(routed):
    accounts,fetcher,_,_,_=routed
    jobs={job.id:job for job in main.app.state.scheduler.get_jobs()}
    assert jobs["prices_1630"].args==("16:30",) and str(jobs["prices_1630"].trigger.timezone)=="Asia/Shanghai"
    assert str(jobs["prices_1630"].trigger)=="cron[hour='16', minute='30']"
    assert {f"prices_{h:02}{m:02}" for h,m in config.ALL_FETCH_SLOTS}<=set(jobs)
    runner=RoundRunner(fetcher,NotificationService(accounts[5]))
    runner.run("09:00");runner.run("16:30");runner.run(codes=["abs"])
    assert sum(text.startswith("采购行情日报") for text in accounts[5].sender.calls)==1


def test_digest_excludes_today_intraday_sci_but_keeps_yesterdays_close(routed):
    accounts,fetcher,_,_,_=routed
    fetcher.run(codes=["abs"])
    with accounts[4]() as session:
        from backend.app.models import QuoteEntry
        entry=session.scalar(select(QuoteEntry).where(QuoteEntry.code=="abs",QuoteEntry.source=="sci99"))
        older=QuoteRow(code="abs",product_name="ABS",specification="隔离规格",market="华东",price_type="市场价格",
            price=Decimal("90"),price_text="90",quote_date=AT.date()-timedelta(days=1),unit="元/吨",source="sci99",
            source_name="卓创资讯",source_url="https://ochem.sci99.com/product/abs",fetched_at=AT.replace(tzinfo=None))
        store_row(session,older);choose(session,1,"abs",entry.id);session.commit()
        _,text,_=build_digest(session,AT.date(),user_id=1)
        assert "：90 元/吨" in text and "报价日期：2026-11-12" in text and "：101 元/吨" not in text


def test_actual_sci_adapter_only_requests_selected_product_and_risk_stops_selection():
    import httpx
    from backend.tests.test_stage10_sci99 import mock_source
    handler,calls,_=mock_source()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={"SCI99_COOKIE":"test=isolated"},codes=["pc"],client=client);adapter.interval=0
        rows=adapter.fetch()
    assert len(rows)==3 and {r.code for r in rows}=={"pc"}
    assert [r.url.path for r in calls if "/product/" in r.url.path]==["/product/pc"]
    handler,calls,_=mock_source(risk="pc")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={"SCI99_COOKIE":"test=isolated"},codes=["pc","paper"],client=client);adapter.interval=0
        assert adapter.fetch()==[]
    assert len(calls)==2 and adapter.auth_status=="access_blocked"


def test_automatic_marker_and_price_share_rollback_transaction(routed):
    accounts,fetcher,_,_,_=routed
    item=config.COMMODITY_MAP["alu_a00"]
    good=PricePoint(code=item.code,price=Decimal("100"),unit=item.unit,quote_basis=item.quote_basis,quote_date=AT.date(),
                    source=item.source,source_name="隔离",source_url="https://example.invalid/",fetched_at=AT.replace(tzinfo=None))
    bad=PricePoint(code=item.code,price=Decimal("100"),unit="错误单位",quote_basis=item.quote_basis,quote_date=AT.date(),
                   source=item.source,source_name="隔离",source_url="https://example.invalid/",fetched_at=AT.replace(tzinfo=None))
    from backend.app.adapters.common import SourceError
    with pytest.raises(SourceError):fetcher.store([good,bad],item.source,automatic_day=AT.date())
    with accounts[4]() as session:
        assert not captured_codes(session,AT.date())
        assert session.scalar(select(func.count()).select_from(PricePoint))==0


def test_enabled_but_bad_key_keeps_route_and_falls_back_at_close(routed,monkeypatch):
    accounts,fetcher,manager,calls,_=routed
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:Fernet.generate_key().decode())
    assert manager.state()["status"]=="configuration_error" and manager.routing_enabled()
    fetcher.run(slot="09:00");assert calls==[("free","alu_a00")]
    calls.clear();output=fetcher.run(slot="16:30")
    assert all(r.get("fallback") for r in output["commodity_results"])
    assert [c[1] for c in calls]==list(SCI99_CODES)
    with accounts[4]() as session:assert session.scalar(select(func.count()).select_from(SourceRun))==0
