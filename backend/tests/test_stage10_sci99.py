"""真实脱敏响应结构与明确标注的对抗变体；不连接上游、不读取真实配置。"""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest

from backend.app.adapters.common import SourceError
from backend.app.adapters.sci99 import ACCOUNT_URL, Sci99Adapter, Sci99Stopped
from backend.app.adapters.sci99_quotes import PRODUCT_PAGES, parse_table
from backend.app import config
from backend.app.service import FetchService
from backend.tests.test_accounts import accounts

FIXTURES=Path(__file__).parent/"fixtures/sci99"
def sample():return json.loads((FIXTURES/"price-center-sample.json").read_text(encoding="utf-8-sig"))["data"]
def parse(data,group="市场价格",code="abs",name="ABS"):
    return parse_table(data,code=code,product_name=name,group=group,url=PRODUCT_PAGES[code][0])


def test_public_product_contexts_confirm_all_ten_codes_without_reusing_abs_id():
    from backend.app.adapters.sci99_quotes import page_context
    records=json.loads((FIXTURES/"product-contexts.json").read_text(encoding="utf-8"))
    codes={"paper" if r["code"] in {"corrugated_paper","cardboard_paper"} else r["code"] for r in records}
    assert codes==set(PRODUCT_PAGES)
    for record in records:
        context,name=page_context(record["html"])
        assert context["navDictId"]==record["navDictId"] and context["navCategory"]==1 and name
        if record["code"]!="abs":assert context["navDictId"]!=12593


def test_real_table_all_24_rows_html_ranges_explicit_units_and_market_identity():
    data=sample();rows=parse(data)
    assert len(rows)==len(data["tableDataArray"])==24
    assert rows[0].low==1710 and rows[0].high==1730 and rows[0].price==1720
    assert rows[0].price_text=="1710-1730" and rows[0].unit=="美元/吨"
    assert "<span" not in rows[0].change_text and rows[0].change_text=="-10/-10"
    assert rows[2].unit=="元/吨"
    assert rows[3].price is None and rows[3].price_text=="-"
    assert len({r.market for r in rows})>1 and len({r.specification for r in rows})>1
    assert all(r.product_name=="ABS" and r.price_type=="市场价格" and str(r.quote_date)=="2026-11-13" for r in rows)


def test_missing_unit_mean_group_and_multi_market_variants_are_explicit():
    data=sample();data["tableDataArray"]=deepcopy(data["tableDataArray"][:2])
    for index,row in enumerate(data["tableDataArray"]):
        row["tips"]=[];row["MarketSampleName"]=["华北","华南"][index]
        row["Model"]=f"AA级高瓦{120+index*20}g";row["FactorySampleName"]="测试纸厂"
        row["Data"]="2980-3150" if index==0 else "3065"
    rows=parse(data,group="均价",code="paper",name="瓦楞纸")
    assert len(rows)==2 and all(r.unit=="元/吨" and r.price_type=="均价" for r in rows)
    assert rows[0].price==Decimal("3065") and rows[0].price_text=="2980-3150"
    assert rows[0].low==2980 and rows[1].low is None
    assert "AA级高瓦120g" in rows[0].specification


def test_hong_kong_without_explicit_unit_is_pending_never_assumed_yuan(accounts):
    data=sample();data["tableDataArray"]=data["tableDataArray"][:1]
    data["tableDataArray"][0]["tips"]=[]
    rows=parse(data)
    assert rows[0].unit=="待核定"
    international=deepcopy(data);international["tableDataArray"][0]["MarketSampleName"]="未注明地区"
    assert parse(international,group="国际")[0].unit=="待核定"
    FetchService(accounts[4]).store_sci99(rows,[{"code":"abs","status":"ok"}])
    client=accounts[1]; options=client.get("/api/quotes?code=abs").json()
    client.put("/api/me/quote-selections/abs",json={"entry_id":options["items"][0]["id"]})
    client.post("/api/alerts/rules",json={"code":"abs","kind":"price_gte","threshold":1})
    result=accounts[5].run(user_id=accounts[3][0])["results"][0]
    assert result["status"]=="skipped" and "单位待核定" in result["detail"]


def test_same_day_missing_quote_keeps_last_real_value_and_marks_missing(accounts):
    from backend.app.quotes import store_row, observations
    with accounts[4]() as session:
        valid=parse(sample())[0];entry,_=store_row(session,valid);session.commit()
        absent=deepcopy(valid);absent.price=None;absent.low=None;absent.high=None;absent.price_text="-"
        store_row(session,absent);session.commit()
        assert observations(session,entry)[-1].price==1720 and entry.missing
        assert entry.last_price_text=="-"


@pytest.mark.parametrize("mutation",["locked","login","unit_conflict","date_missing","negative","bad_range","unknown_field"])
def test_unknown_or_locked_fields_never_become_fake_prices(mutation):
    data=sample();data["tableDataArray"]=data["tableDataArray"][:1];row=data["tableDataArray"][0]
    if mutation=="locked":row["powerType"]=0
    if mutation=="login":row["loginStatus"]=0
    if mutation=="unit_conflict":row["tips"].append("单位：元/吨")
    if mutation=="date_missing":data["tableHeadArray"][3]["title"]="最新价格"
    if mutation=="negative":row["Data"]="-123"
    if mutation=="bad_range":row["Data"]="3150-2980"
    if mutation=="unknown_field":row["Data"]={"hidden":123}
    with pytest.raises(SourceError):parse(data)


def test_multiple_date_columns_keep_history_instead_of_only_latest():
    data=sample();data["tableDataArray"]=data["tableDataArray"][:1]
    data["tableHeadArray"].append({"field":"previous_test","title":"2026-11-12","isDual":False})
    data["tableDataArray"][0]["previous_test"]="1700"
    rows=parse(data)
    assert len(rows)==2 and [str(r.quote_date) for r in rows]==["2026-11-13","2026-11-12"]


def mock_source(*, broken=None, risk=None):
    # 测试页编号刻意采用独立测试数字；不声称是其他官网商品的真实ID。
    urls=[url for values in PRODUCT_PAGES.values() for url in values]
    contexts={url:900000+index for index,url in enumerate(urls)}
    calls=[]
    def handler(request):
        calls.append(request)
        url=str(request.url).split("?",1)[0]
        if url==ACCOUNT_URL:
            return httpx.Response(200,json={"code":200,"data":{"user_info_id":900001,"user_name":"redacted"}})
        if url in contexts:
            if risk and url==PRODUCT_PAGES[risk][0]:return httpx.Response(429,json={"message":"安全验证"})
            return httpx.Response(200,text=f"var navDictId = {contexts[url]}; var pageNavDictId = {contexts[url]}; var navCategory = 1; var pageNavDictName = '测试商品{contexts[url]}';")
        if request.url.path=="/visualPane/priceCenterPriceType":
            return httpx.Response(200,json={"code":200,"data":{"groups":[{"name":"市场价格"},{"name":"均价"},{"name":"企业出厂"}]}})
        if request.url.path=="/visualPane/priceCenterDataV3":
            body=json.loads(request.content)
            assert body["navDictId"] in contexts.values() and body["filterItems"]==[]
            assert "pageIndex" not in body and "pageSize" not in body
            assert set(body)=={"navDictId","navCategory","priceType","filterItems"}
            data=sample();data["tableDataArray"]=data["tableDataArray"][:1]
            if broken and body["navDictId"]==contexts[PRODUCT_PAGES[broken][0]]:data["tableDataArray"][0]["Data"]="字段结构变化，未确认价格"
            return httpx.Response(200,json={"code":200,"data":data})
        raise AssertionError("请求了未核定的接口")
    return handler,calls,contexts


def test_all_10_codes_13_product_pages_and_all_groups_use_own_page_id():
    handler,calls,contexts=mock_source()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={"SCI99_COOKIE":"test=isolated"},client=client);adapter.interval=0
        rows=adapter.fetch()
    assert {r.code for r in rows}==set(PRODUCT_PAGES) and len(rows)==13*3
    assert all(r["status"]=="ok" for r in adapter.quote_results)
    posts=[json.loads(r.content) for r in calls if r.method=="POST"]
    assert {p["navDictId"] for p in posts}==set(contexts.values())
    assert {r.price_type for r in rows}=={"市场价格","均价","企业出厂"}
    assert len([r for r in calls if str(r.url)==ACCOUNT_URL])==1


def test_product_paging_follows_declared_total_pages_and_page_size_only():
    from backend.app.adapters.sci99_quotes import group_pages
    bodies=[]
    def handler(request):
        body=json.loads(request.content);bodies.append(body)
        page=body.get("pageIndex",1)
        assert request.url.path=="/visualPane/priceCenterDataV3"
        if page==1:assert "pageSize" not in body
        else:assert body["pageSize"]==1
        data=sample();data["tableDataArray"]=deepcopy(data["tableDataArray"][page-1:page])
        data.update(pageIndex=page,pageSize=1,totalPage=2,totalCount=2)
        return httpx.Response(200,json={"code":200,"data":data})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={},client=client);adapter.interval=0
        pages=list(group_pages(adapter,client,"https://ochem.sci99.com",{"navDictId":12593,"navCategory":1},"市场价格"))
    assert len(pages)==2 and len(bodies)==2 and all(p["priceType"]=="市场价格" for p in bodies)


def test_commodity_parse_failure_isolated_but_risk_stops_all_remaining_sci_requests():
    handler,calls,_=mock_source(broken="pc")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={"SCI99_COOKIE":"test=isolated"},client=client);adapter.interval=0;rows=adapter.fetch()
    assert "pc" not in {r.code for r in rows} and "paper" in {r.code for r in rows}
    assert next(r for r in adapter.quote_results if r["code"]=="pc")["status"]=="failed"
    handler,calls,_=mock_source(risk="pc")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={"SCI99_COOKIE":"test=isolated"},client=client);adapter.interval=0;rows=adapter.fetch()
    assert {r.code for r in rows}=={"abs"} and adapter.auth_status=="access_blocked"
    assert calls[-1].url.path=="/product/pc" and sum(r.method=="POST" for r in calls)==3


def test_full_rows_store_into_uniform_catalog_without_changing_free_source(accounts):
    data=sample();rows=parse(data)
    changed,reports=FetchService(accounts[4]).store_sci99(rows,[{"code":"abs","status":"ok"}])
    assert changed==24 and next(r for r in reports if r["code"]=="abs")["count"]==24
    assert config.COMMODITY_MAP["abs"].source=="free_abs"
    options=accounts[1].get("/api/quotes?code=abs").json()
    assert len(options["items"])==24 and options["selected_entry_id"] in {e["id"] for e in options["items"]}
    assert all("<span" not in str(e) for e in options["items"])
