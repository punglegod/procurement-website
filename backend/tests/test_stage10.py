"""全量条目和个人选择回归；数字仅为隔离测试数据，上游全部mock。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
import sqlite3

import pytest
from sqlalchemy import select, func
from sqlalchemy.orm import sessionmaker

from backend.app import config
from backend.app.db import initialize_database, make_engine
from backend.app.models import QuoteEntry, QuoteObservation, UserQuoteSelection, PricePoint, UserDailyDigest, PushLog
from backend.app.quotes import QuoteRow, store_row, choose, selected_entry, observations
from backend.app.service import FetchService, latest_prices, price_history
from backend.app.notifications import NotificationService
from backend.tests.test_accounts import accounts, mark_verified

AT = datetime(2026, 11, 13, 9, 0, tzinfo=config.TIMEZONE)


def row(market="华南", *, price="3065", day=AT.date(), unit="", low="2980", high="3150", specification="AA级高瓦120g"):
    return QuoteRow(code="paper", product_name="瓦楞纸", specification=specification, market=market,
                    price_type="市场价格", price=Decimal(price), low=Decimal(low) if low else None,
                    high=Decimal(high) if high else None, price_text=f"{low}-{high}" if low else price,
                    quote_date=day, unit=unit, source="sci99", source_name="卓创资讯",
                    source_url="https://paper.sci99.com/product/corrugated_paper", fetched_at=AT.replace(tzinfo=None))


def seed(factory):
    with factory() as session:
        first, _ = store_row(session, row())
        second, _ = store_row(session, row("华北", price="3500", low=None, high=None, specification="AA级高瓦140g"))
        ids = first.id, second.id
        session.commit()
        return ids


def closing_prices(factory):
    # 09:00日报只读上一已结束报价日，今天的手动盘中价格不能充作收盘价。
    with factory() as session:
        store_row(session,row(day=AT.date()-timedelta(days=1)))
        store_row(session,row("华北",price="3500",low=None,high=None,specification="AA级高瓦140g",day=AT.date()-timedelta(days=1)))
        session.commit()


def test_rows_identity_ranges_units_and_history_preserved(accounts):
    factory=accounts[4]; first, second=seed(factory)
    with factory() as session:
        store_row(session, row(day=AT.date()-timedelta(days=1), price="3000", low="2900", high="3100"))
        entry, changed=store_row(session, row())
        session.commit()
        assert entry.id==first and changed==0
        points=observations(session, entry)
        assert len(points)==2 and points[-1].unit=="元/吨"
        assert points[-1].price==Decimal("3065") and points[-1].low==2980 and points[-1].high==3150
        assert points[-1].price_text=="2980-3150"
        assert session.scalar(select(func.count()).select_from(QuoteEntry))==2


def test_default_random_assignment_persists_concurrent_reads_and_restart(accounts):
    factory=accounts[4]; ids=seed(factory); user=accounts[3][0]
    def read(_): return next(r for r in latest_prices(factory,user_id=user) if r["code"]=="paper")["selected_entry_id"]
    with ThreadPoolExecutor(max_workers=4) as pool: picked=list(pool.map(read,range(8)))
    assert len(set(picked))==1 and picked[0] in ids
    with factory() as session:
        assert session.get(UserQuoteSelection,(user,"paper")).entry_id==picked[0]
    assert read(None)==picked[0]


def test_user_switch_ab_isolation_history_and_no_owner_override(accounts):
    admin,a,b,users,factory,_=accounts; first,second=seed(factory)
    assert a.put("/api/me/quote-selections/paper",json={"entry_id":first}).status_code==200
    assert b.put("/api/me/quote-selections/paper",json={"entry_id":second}).status_code==200
    assert a.put("/api/me/quote-selections/paper",json={"entry_id":second,"user_id":users[1]}).status_code==422
    for client,selected,price in [(a,first,3065),(b,second,3500)]:
        latest=next(r for r in client.get("/api/prices/latest").json()["items"] if r["code"]=="paper")
        assert latest["selected_entry_id"]==selected and latest["price_point"]["price"]==price
        history=client.get("/api/prices/history?code=paper").json()
        assert history["selected_entry_id"]==selected and all(p["entry_id"]==selected for p in history["items"])
        options=client.get("/api/quotes?code=paper").json()
        assert options["selected_entry_id"]==selected and "user_id" not in str(options)
    assert a.put("/api/me/quote-selections/abs",json={"entry_id":first}).status_code==404
    assert a.put(f"/api/users/{users[1]}/quote-selections/paper",json={"entry_id":first}).status_code in {404,405}


def test_selected_missing_never_reassigns_or_triggers_rule(accounts):
    _,a,b,users,factory,alerts=accounts; first,second=seed(factory)
    alerts.clock=lambda:AT
    a.put("/api/me/quote-selections/paper",json={"entry_id":first})
    rule=a.post("/api/alerts/rules",json={"code":"paper","kind":"price_gte","threshold":1}).json()
    with factory() as session:
        session.get(QuoteEntry,first).missing=True; session.commit()
    latest=next(r for r in a.get("/api/prices/latest").json()["items"] if r["code"]=="paper")
    assert latest["selection_missing"] and latest["selected_entry_id"]==first and latest["price_point"]["price_text"]=="2980-3150"
    assert "所选口径本轮未更新" in latest["message"]
    result=alerts.run(user_id=users[0])
    assert result["results"][0]["status"]=="skipped"
    with factory() as session: assert not session.scalar(select(PushLog).where(PushLog.rule_id==rule["id"]))


def test_rules_recheck_current_user_selection_before_claim(accounts):
    _,a,b,users,factory,alerts=accounts; first,second=seed(factory); alerts.clock=lambda:AT
    for client in (a,b):client.put("/api/me/push-settings",json={"email":"sample@example.invalid"})
    mark_verified(factory,*users)
    a.put("/api/me/quote-selections/paper",json={"entry_id":first})
    saved=a.post("/api/alerts/rules",json={"code":"paper","kind":"price_gte","threshold":3400}).json()
    assert alerts.run(user_id=users[0])["results"][0]["status"]=="skipped"
    a.put("/api/me/quote-selections/paper",json={"entry_id":second})
    result=alerts.run(user_id=users[0])["results"][0]
    assert result["status"]=="sent" and "华北" in result["message"] and "3500" in result["message"]
    assert alerts.run(user_id=users[0])["results"][0]["status"]=="deduplicated"


def test_daily_digest_uses_each_users_selection_and_range_text(accounts):
    admin,a,b,users,factory,alerts=accounts; first,second=seed(factory); closing_prices(factory); alerts.clock=lambda:AT
    for client,entry in [(a,first),(b,second),(admin,first)]:
        client.put("/api/me/quote-selections/paper",json={"entry_id":entry})
        client.put("/api/me/push-settings",json={"email":"sample@example.invalid","daily_digest_enabled":True})
    mark_verified(factory,1,*users)
    notifier=NotificationService(alerts); notifier.daily_digest()
    with factory() as session:
        one=session.get(UserDailyDigest,(users[0],AT.date())).message
        two=session.get(UserDailyDigest,(users[1],AT.date())).message
        assert "2980-3150 元/吨" in one and "华南" in one and "华北" not in one
        paper_two=two.split(config.COMMODITY_MAP["paper"].name,1)[1]
        assert "3500 元/吨" in paper_two and "华北" in paper_two and "华南" not in paper_two
        count=session.scalar(select(func.count()).select_from(PushLog))
    a.put("/api/me/quote-selections/paper",json={"entry_id":second}); notifier.daily_digest()
    with factory() as session: assert session.scalar(select(func.count()).select_from(PushLog))==count


def test_unsent_digest_rebuilds_after_choice_but_attempted_digest_stays_frozen(accounts):
    _,a,_,users,factory,alerts=accounts; first,second=seed(factory);closing_prices(factory);alerts.clock=lambda:AT
    a.put("/api/me/push-settings",json={"email":"sample@example.invalid","daily_digest_enabled":True})
    a.put("/api/me/quote-selections/paper",json={"entry_id":first})
    notifier=NotificationService(alerts);notifier.daily_digest()
    with factory() as session:assert "AA级高瓦120g" in session.get(UserDailyDigest,(users[0],AT.date())).message
    a.put("/api/me/quote-selections/paper",json={"entry_id":second})
    mark_verified(factory,users[0]);notifier.daily_digest()
    with factory() as session:
        message=session.get(UserDailyDigest,(users[0],AT.date())).message
        assert "AA级高瓦140g" in message and "AA级高瓦120g" not in message
    a.put("/api/me/quote-selections/paper",json={"entry_id":first});notifier.daily_digest()
    with factory() as session:assert session.get(UserDailyDigest,(users[0],AT.date())).message==message


def test_digest_selection_changed_before_claim_uses_new_choice(accounts,monkeypatch):
    _,a,_,users,factory,alerts=accounts;first,second=seed(factory);closing_prices(factory);alerts.clock=lambda:AT
    a.put("/api/me/push-settings",json={"email":"sample@example.invalid","daily_digest_enabled":True})
    mark_verified(factory,users[0]);a.put("/api/me/quote-selections/paper",json={"entry_id":first})
    notifier=NotificationService(alerts);original=notifier.send
    def before_claim(**kwargs):
        if kwargs["user_id"]==users[0]:
            with factory() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                choose(session,users[0],"paper",second);session.commit()
        return original(**kwargs)
    monkeypatch.setattr(notifier,"send",before_claim);notifier.daily_digest()
    with factory() as session:
        log=session.scalar(select(PushLog).where(PushLog.user_id==users[0],PushLog.kind=="digest"))
        assert log.status=="sent" and "AA级高瓦140g" in log.message and "AA级高瓦120g" not in log.message


def test_v10_backup_backfill_preserves_price_points_and_is_idempotent(accounts,tmp_path):
    factory=accounts[4]; path=tmp_path/"upgrade.sqlite3"
    item=config.COMMODITIES[0]
    with factory() as session:
        session.add(PricePoint(code=item.code,price=Decimal("100"),unit=item.unit,quote_basis=item.quote_basis,
            quote_date=AT.date(),source=item.source,source_name="脱敏来源",source_url="https://example.invalid/",fetched_at=AT.replace(tzinfo=None)))
        session.commit()
    with sqlite3.connect(factory.kw["bind"].url.database) as source,sqlite3.connect(path) as target:source.backup(target)
    with sqlite3.connect(path) as conn:
        for table in ["user_daily_digests","user_quote_selections","quote_observations","quote_entries","quote_imports"]:conn.execute("DROP TABLE "+table)
        conn.execute("UPDATE site_settings SET value='9' WHERE key='schema_version'")
    engine=make_engine(f"sqlite:///{path}"); backup=initialize_database(engine)
    assert "pre-stage10" in backup
    with sqlite3.connect(backup) as conn:
        assert conn.execute("SELECT count(*) FROM price_points").fetchone()[0]==1
        assert "quote_entries" not in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM price_points").fetchone()[0]==1
        assert conn.execute("SELECT count(*) FROM quote_observations").fetchone()[0]==1
        assert conn.execute("SELECT value FROM site_settings WHERE key='schema_version'").fetchone()[0]=="10"
        assert conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    assert initialize_database(engine) is None
    engine.dispose()


def test_units_never_mix_in_history_or_comparison(accounts):
    factory=accounts[4]; first,_=seed(factory)
    with factory() as session:
        store_row(session,row(day=AT.date()-timedelta(days=1),unit="美元/吨"))
        choose(session,accounts[3][0],"paper",first);session.commit()
    history=price_history("paper",30,factory,user_id=accounts[3][0])
    assert len(history["items"])==1 and history["items"][0]["unit"]=="元/吨"
    latest=next(r for r in latest_prices(factory,user_id=accounts[3][0]) if r["code"]=="paper")
    assert latest["change_amount"] is None


def test_migrated_retired_source_is_selectable_and_does_not_500(accounts):
    item=config.COMMODITY_MAP["paper"];factory=accounts[4];client=accounts[1]
    with factory() as session:
        session.add(PricePoint(code="paper",price=Decimal("100"),unit=item.unit,quote_basis="旧来源历史口径",
            quote_date=AT.date(),source="retired_source",source_name="旧来源",source_url="https://example.invalid/old",fetched_at=AT.replace(tzinfo=None)))
        session.commit()
    options=client.get("/api/quotes?code=paper").json()
    assert client.put("/api/me/quote-selections/paper",json={"entry_id":options["items"][0]["id"]}).status_code==200
    response=client.get("/api/prices/latest")
    assert response.status_code==200
    latest=next(r for r in response.json()["items"] if r["code"]=="paper")
    assert latest["source"]=="retired_source" and latest["price_point"]["price"]==100


def test_import_counts_drop_missing_and_per_commodity_transaction(accounts):
    factory=accounts[4]; fetcher=FetchService(factory)
    rows=[row(market=f"测试市场{i}") for i in range(5)]
    success=[{"code":"paper","status":"ok"}]
    changed,reports=fetcher.store_sci99(rows,success)
    report=next(r for r in reports if r["code"]=="paper")
    assert changed==5 and report["count"]==5 and report["status"]=="ok"
    changed,reports=fetcher.store_sci99(rows[:1],success)
    report=next(r for r in reports if r["code"]=="paper")
    assert changed==0 and report["status"]=="failed" and "骤降" in report["message"]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(QuoteObservation))==5
        assert all(e.missing for e in session.scalars(select(QuoteEntry)))
    changed,reports=fetcher.store_sci99(rows[:3],success)
    assert changed==0 and next(r for r in reports if r["code"]=="paper")["status"]=="ok"
    with factory() as session:
        entries=list(session.scalars(select(QuoteEntry).order_by(QuoteEntry.id)))
        assert [e.missing for e in entries]==[False,False,False,True,True]


def test_range_midpoint_precision_survives_database(accounts):
    with accounts[4]() as session:
        entry,_=store_row(session,row(low="1.0001",high="1.0002",price="1.00015"));session.commit()
        assert observations(session,entry)[0].price==Decimal("1.00015")


def test_duplicate_identity_dates_fail_whole_commodity_without_partial_write(accounts):
    rows=[row(),row(price="3100",low="3000",high="3200")]
    changed,reports=FetchService(accounts[4]).store_sci99(rows,[{"code":"paper","status":"ok"}])
    assert changed==0 and "重复" in next(r for r in reports if r["code"]=="paper")["message"]
    with accounts[4]() as session:assert session.scalar(select(func.count()).select_from(QuoteObservation))==0


def test_disabled_user_deleted_preferences_cascade_and_quote_catalog_stays(accounts):
    from backend.app.models import User
    factory=accounts[4]; first,_=seed(factory); owner=accounts[3][0]
    with factory() as session:
        choose(session,owner,"paper",first);session.commit()
    assert accounts[0].delete(f"/api/admin/users/{owner}").status_code==200
    with factory() as session:
        assert session.get(UserQuoteSelection,(owner,"paper")) is None
        assert session.get(QuoteEntry,first) is not None


def test_selection_change_between_initial_evaluation_and_claim_is_rechecked(accounts,monkeypatch):
    _,a,_,users,factory,alerts=accounts; first,second=seed(factory); alerts.clock=lambda:AT
    a.put("/api/me/push-settings",json={"email":"sample@example.invalid"});mark_verified(factory,users[0])
    a.put("/api/me/quote-selections/paper",json={"entry_id":second})
    a.post("/api/alerts/rules",json={"code":"paper","kind":"price_gte","threshold":3400})
    original=alerts.deliver
    def switch(*args,**kwargs):
        with factory() as session:
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            choose(session,users[0],"paper",first);session.commit()
        return original(*args,**kwargs)
    monkeypatch.setattr(alerts,"deliver",switch)
    result=alerts.run(user_id=users[0])["results"][0]
    assert result["status"]=="skipped"
    with factory() as session:assert not session.scalar(select(PushLog).where(PushLog.kind=="rule"))


def test_observed_frontend_page_context_uses_each_pages_own_id():
    from backend.app.adapters.sci99_quotes import page_context
    from backend.app.adapters.common import SourceError
    html="var pageNavDictId = 12573; var navDictId = 12573; var navCategory = 1; var pageNavDictName = '瓦楞纸';"
    context,name=page_context(html)
    assert context=={"navDictId":12573,"navCategory":1} and name=="瓦楞纸"
    with pytest.raises(SourceError):page_context(html.replace("navDictId = 12573","navDictId = 12593"))


def test_observed_multi_product_pagination_params_and_complete_count():
    import httpx
    from backend.app.adapters.sci99 import Sci99Adapter
    from backend.app.adapters.sci99_quotes import group_pages
    calls=[]
    def handler(request):
        import json
        body=json.loads(request.content);calls.append(body)
        assert body["pageSize"]==20 and "navCategory" not in body and body["filterItems"]==[]
        rows=[{"test":i} for i in range(20)] if body["pageIndex"]==1 else [{"test":20}]
        return httpx.Response(200,json={"code":200,"data":{"tableHeadArray":[{"field":"test","title":"测试字段"}],"tableDataArray":rows,"totalCount":21}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter=Sci99Adapter(settings={},client=client);adapter.interval=0
        pages=list(group_pages(adapter,client,"https://paper.sci99.com",{"navDictId":12573,"navCategory":1},"市场价格",paged=True))
    assert len(calls)==2 and [c["pageIndex"] for c in calls]==[1,2]
    assert sum(len(p["tableDataArray"]) for p in pages)==21


@pytest.mark.parametrize("paged",[False,True])
def test_incomplete_or_repeated_pages_are_never_silently_accepted(paged):
    import httpx
    from backend.app.adapters.sci99 import Sci99Adapter
    from backend.app.adapters.sci99_quotes import group_pages
    from backend.app.adapters.common import SourceError
    data={"code":200,"data":{"tableHeadArray":[],"tableDataArray":[{"test":i} for i in range(20)],"totalCount":40}}
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
        adapter=Sci99Adapter(settings={},client=client);adapter.interval=0
        with pytest.raises(SourceError):list(group_pages(adapter,client,"https://paper.sci99.com",{"navDictId":12573},"均价",paged=paged))


@pytest.mark.parametrize("low,high,price",[("3150","2980","3065"),("0","10","5"),("2980","3150","3000")])
def test_invalid_ranges_never_commit(accounts,low,high,price):
    from backend.app.adapters.common import SourceError
    with accounts[4]() as session,pytest.raises(SourceError):store_row(session,row(low=low,high=high,price=price))
