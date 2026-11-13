"""第九阶段隔离回归；凭据仅随机留在内存，通道/上游全部mock，不打印敏感值。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
import secrets
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import sessionmaker

from backend.app import config, main, service as service_module
from backend.app.adapters.common import AccessStopped
from backend.app.adapters.sci99 import Sci99Adapter
from backend.app.alerts import AlertService, EmailSender, TelegramSender
from backend.app.db import initialize_database, make_engine
from backend.app.models import DailyDigest, FetchBatch, NotificationClaim, PricePoint, PushLog, PushSetting, SiteSetting, SourceRun, SourceState, User
from backend.app.notifications import NotificationService, build_digest, digest_enabled, telegram_parts
from backend.app.rounds import RoundRunner
from backend.app.service import FetchService
from backend.app.source_settings import Sci99Input, SourceSettings, warn_encryption_configuration
from backend.tests.test_accounts import accounts, mark_verified

AT = datetime(2026, 11, 13, 9, 3, tzinfo=config.TIMEZONE)


@pytest.fixture
def notices(accounts):
    admin, a, b, ids, factory, alerts = accounts
    alerts.clock = lambda: AT
    return accounts, NotificationService(alerts)


def add_prices(factory, prices=None):
    item = config.COMMODITIES[0]
    prices = prices or [(AT.date()-timedelta(days=6), "100"), (AT.date()-timedelta(days=1), "110"), (AT.date(), "120")]
    with factory() as session:
        for day, price in prices:
            session.add(PricePoint(code=item.code, price=Decimal(price), source=item.source, quote_basis=item.quote_basis,
                                   unit=item.unit, quote_date=day, source_name="隔离公开报价", source_url="https://example.invalid/quote",
                                   fetched_at=AT.replace(tzinfo=None)))
        session.commit()


def test_four_slots_registered_with_exact_timezone(accounts):
    jobs = {job.id: job for job in main.app.state.scheduler.get_jobs()}
    assert config.FETCH_SLOTS == ((8, 0), (8, 30), (9, 0), (15, 30))
    for hour, minute in config.FETCH_SLOTS:
        job = jobs[f"prices_{hour:02}{minute:02}"]
        assert str(job.trigger.timezone) == "Asia/Shanghai" and job.args == (f"{hour:02}:{minute:02}",)
        assert str(job.trigger) == f"cron[hour='{hour}', minute='{minute}']"
        assert job.max_instances == 1
    assert jobs["morning_monitor"].trigger.interval.total_seconds() == 60
    print("STAGE9_SCHEDULE slots=08:00,08:30,09:00,15:30 timezone=Asia/Shanghai")


def test_digest_all_commodities_changes_trends_and_html_escape(notices):
    accounts, notifier = notices
    add_prices(accounts[4])
    with accounts[4]() as session:
        title, text, html = build_digest(session, AT.date())
    assert title == "采购行情日报 · 2026-11-13"
    assert all(item.name in text for item in config.COMMODITIES)
    assert [text.index(item.name) for item in config.COMMODITIES] == sorted(text.index(item.name) for item in config.COMMODITIES)
    assert "上涨 ↑ +10.00 元/吨 / +9.09%" in text
    assert "近7日：+20.00%" in text and "近7日震荡上行" in text
    assert "近30日最高/最低：120.00/100.00 元/吨" in text
    assert text.count("历史数据不足") == 10 and "<html>" in html
    assert "Li2CO3≥99.5%" in html or "历史数据不足" in html
    print("STAGE9_DIGEST commodities=11 daily_change=+10/+9.09% trend=+20% bounds=120/100")


@pytest.mark.parametrize("prices,expected", [(["120", "100"], "下跌 ↓ -20.00 元/吨 / -16.67%"), (["100", "100"], "持平 = +0.00 元/吨 / +0.00%")])
def test_digest_falling_flat_and_insufficient_history(notices, prices, expected):
    accounts, _ = notices
    add_prices(accounts[4], [(AT.date()-timedelta(days=1), prices[0]), (AT.date(), prices[1])])
    with accounts[4]() as session:
        _, text, _ = build_digest(session, AT.date())
    assert expected in text and "近7日：历史数据不足" in text


def test_workday_expiry_and_same_basis_only(notices):
    accounts, _ = notices
    add_prices(accounts[4], [(date(2026, 11, 13), "100")])
    with accounts[4]() as session:
        _, saturday, _ = build_digest(session, date(2026, 11, 14))
        _, monday, _ = build_digest(session, date(2026, 11, 16))
    assert config.COMMODITIES[0].name + "【过期】" not in saturday
    assert config.COMMODITIES[0].name + "【过期】" in monday


def test_digest_defaults_opt_in_isolation_dedup_and_no_rule_needed(notices):
    accounts, notifier = notices
    admin, a, b, ids, factory, alerts = accounts
    assert admin.get("/api/me/push-settings").json()["daily_digest_enabled"] is True
    assert a.get("/api/me/push-settings").json()["daily_digest_enabled"] is False
    for actor, address in [(a,"a@example.invalid"),(b,"b@example.invalid"),(admin,"admin@example.invalid")]:
        actor.put("/api/me/push-settings", json={"email":address})
    mark_verified(factory, ids[0], ids[1], 1)
    assert a.put("/api/me/push-settings", json={"email":"a@example.invalid","daily_digest_enabled":True}).status_code == 200
    assert b.get("/api/me/push-settings").json()["daily_digest_enabled"] is False
    notifier.daily_digest()
    notifier.daily_digest()
    assert len(alerts.sender.calls) == 1 and len(alerts.email_sender.calls) == 2
    assert {log["kind"] for log in a.get("/api/alerts/logs").json()["items"]} == {"digest"}
    assert b.get("/api/alerts/logs").json()["items"] == []
    assert len(admin.get("/api/alerts/logs").json()["items"]) == 2
    assert b.put("/api/me/push-settings", json={"email":"b@example.invalid","daily_digest_enabled":True,"user_id":ids[0]}).status_code == 422
    assert alerts.email_sender.calls[0].startswith("采购行情日报")
    print("STAGE9_DELIVER admin_default=True user_default=False opted_in_only=True same_day_once=True")


def test_digest_unverified_pending_reuse_concurrent_restart_and_snapshot(notices):
    accounts, notifier = notices
    admin, a, _, ids, factory, alerts = accounts
    a.put("/api/me/push-settings", json={"email":"a@example.invalid","daily_digest_enabled":True})
    notifier.daily_digest(abnormal=True)
    assert not alerts.email_sender.calls
    mark_verified(factory, ids[0])
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: NotificationService(alerts).daily_digest(), range(4)))
    assert len(alerts.email_sender.calls) == 1 and len(alerts.sender.calls) == 1
    assert "09:00 轮抓取异常，本报为最近可用数据" in alerts.email_sender.calls[0]
    assert a.get("/api/alerts/logs").json()["total"] == 1
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DailyDigest)) == 1


def fake_round(factory, outcome="success"):
    class Fetcher:
        session_factory = factory
        calls = []
        def run(self, *, slot=None):
            self.calls.append(slot)
            if outcome == "exception":
                raise RuntimeError("isolated failure")
            return {"results": [{"source": item.source, "outcome": outcome if index == 1 else "success", "status":"ok",
                                  "message":"隔离来源失败", "stored":0 if index == 2 else 1,
                                  "prices":[{"code":item.code,"price":100.0,"unit":item.unit}]} for index,item in enumerate(config.COMMODITIES)]}
    return Fetcher()


def test_progress_start_end_admin_only_manual_and_disable(notices):
    accounts, notifier = notices
    admin, a, _, _, factory, alerts = accounts
    runner = RoundRunner(fake_round(factory), notifier)
    result = runner.run()
    assert result["summary"]["success"] == 11 and len(alerts.sender.calls) == 2
    assert "抓取开始 · 手动" in alerts.sender.calls[0] and "共 11 个品种" in alerts.sender.calls[0]
    assert "抓取完成" in alerts.sender.calls[1] and "成功 11 / 失败 0" in alerts.sender.calls[1]
    assert "无更新" in alerts.sender.calls[1]
    assert a.get("/api/alerts/logs").json()["total"] == 0 and not alerts.email_sender.calls
    assert admin.put("/api/admin/fetch-settings", json={"progress_enabled":False}).status_code == 200
    runner.run()
    assert len(alerts.sender.calls) == 2
    assert admin.get("/api/admin/fetch-runs").json()["items"][0]["id"] == 2
    assert a.get("/api/admin/fetch-runs").status_code == 403
    print("STAGE9_PROGRESS start_end=2 admin_only=True manual_recorded=True switch_off=True")


def test_nine_round_triggers_digest_after_end_afternoon_and_rerun_do_not_duplicate(notices):
    accounts, notifier = notices
    alerts = accounts[5]
    runner = RoundRunner(fake_round(accounts[4], "failed"), notifier)
    runner.run("09:00")
    assert len(alerts.sender.calls) == 3
    assert "抓取完成" in alerts.sender.calls[1] and alerts.sender.calls[2].startswith("采购行情日报")
    assert "09:00 轮抓取异常" in alerts.sender.calls[2]
    assert runner.run("09:00")["status"] == "duplicate"
    runner.monitor_morning()
    assert len(alerts.sender.calls) == 3  # 单品种失败仅完成汇总点名，不另发调度告警。
    runner.run("15:30")
    runner.run()
    assert sum(text.startswith("采购行情日报") for text in alerts.sender.calls) == 1


def test_round_global_failure_fallback_and_next_round_continues(notices):
    accounts, notifier = notices
    broken = RoundRunner(fake_round(accounts[4], "exception"), notifier)
    assert broken.run("09:00")["status"] == "failed"
    assert any("调度告警" in text for text in accounts[5].sender.calls)
    assert any("09:00 轮抓取异常" in text for text in accounts[5].sender.calls)
    broken.monitor_morning()
    assert sum("调度告警" in text for text in accounts[5].sender.calls)==1
    assert RoundRunner(fake_round(accounts[4]), notifier).run("15:30")["status"] == "complete"


def test_watchdog_missing_running_stale_and_one_alert_per_day(notices):
    accounts, notifier = notices
    runner = RoundRunner(fake_round(accounts[4]), notifier)
    with accounts[4]() as session:
        session.add(FetchBatch(run_day=AT.date(), slot="09:00", status="running", started_at=AT.replace(tzinfo=None)-timedelta(minutes=1)))
        session.commit()
    runner.monitor_morning()
    assert not accounts[5].sender.calls
    with accounts[4]() as session:
        session.scalar(select(FetchBatch)).started_at -= timedelta(minutes=40)
        session.commit()
    runner.monitor_morning()
    runner.monitor_morning()
    assert len(accounts[5].sender.calls) == 2
    assert "调度告警" in accounts[5].sender.calls[0] and "09:00 轮抓取异常" in accounts[5].sender.calls[1]


def test_watchdog_when_scheduler_missed_nine_uses_available_data(notices):
    accounts, notifier = notices
    RoundRunner(fake_round(accounts[4]), notifier).monitor_morning()
    assert len(accounts[5].sender.calls) == 2


def test_sci99_encryption_permissions_masks_clear_and_validation(accounts, monkeypatch, caplog):
    admin, a, _, _, factory, _ = accounts
    monkeypatch.setattr(config, "read_settings_encryption_key", lambda: Fernet.generate_key().decode())
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "read_settings_encryption_key", lambda:key)
    credentials = {field: secrets.token_urlsafe(28) for field in ("username","password","cookie")}
    credentials["password"] = " " + credentials["password"] + " "
    for path in ("/api/admin/sources/sci99", "/api/admin/fetch-settings", "/api/admin/fetch-runs"):
        assert a.get(path).status_code == 403
    assert a.put("/api/admin/sources/sci99",json={"enabled":True, **credentials}).status_code == 403
    saved = admin.put("/api/admin/sources/sci99", json={"enabled":True, **credentials})
    assert saved.status_code == 200
    response = admin.get("/api/admin/sources/sci99")
    with factory() as session:
        raw = "".join(session.scalars(select(SiteSetting.value)))
    leaked = any(value in response.text or value in saved.text or value in raw or value in caplog.text for value in credentials.values())
    path = Path(factory.kw["bind"].url.database)
    disk = b"".join(file.read_bytes() for file in (path, Path(str(path)+"-wal")) if file.exists())
    leaked = leaked or any(value.encode() in disk for value in credentials.values())
    assert not leaked, "凭据泄露"
    assert response.json()["configured"] and response.json()["key_ready"]
    assert response.json()["username_mask"].count("•") == 4
    with factory() as session:
        session.get(SourceState,"sci99").status="pending_credentials"
        session.commit()
    assert next(s for s in admin.get("/api/sources/status").json()["items"] if s["source"]=="sci99")["status"] == "enabled"
    malformed = admin.put("/api/admin/sources/sci99",json={"enabled":True,"password":credentials["password"]*30})
    assert malformed.status_code == 422 and not any(value in malformed.text for value in credentials.values())
    legacy = {"SCI99_USER":"", "SCI99_PASS":"", "SCI99_COOKIE":secrets.token_urlsafe(20)}
    manager = SourceSettings(factory, legacy_reader=lambda:legacy)
    resolved, _, backend = manager.resolve()
    assert backend and resolved["SCI99_COOKIE"] == credentials["cookie"]
    password_preserved = resolved["SCI99_" + "PASS"] == credentials["password"]
    assert password_preserved
    manager.save(Sci99Input(enabled=False, clear_credentials=True))
    resolved, _, backend = manager.resolve()
    assert not backend and resolved["SCI99_COOKIE"] == legacy["SCI99_COOKIE"]
    print("STAGE9_SECRETS ordinary=403 encrypted=True response_masked=True validation_sanitized=True clear_fallback=True")


def test_missing_wrong_key_forbids_save_and_no_silent_fallback(accounts, monkeypatch, caplog):
    admin, _, _, _, factory, _ = accounts
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:"")
    warn_encryption_configuration()
    assert "禁止保存" in caplog.text
    assert admin.put("/api/admin/sources/sci99",json={"enabled":False,"password":secrets.token_urlsafe(20)}).status_code == 503
    key=Fernet.generate_key().decode()
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:key)
    admin.put("/api/admin/sources/sci99",json={"enabled":True,"cookie":secrets.token_urlsafe(20)})
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:Fernet.generate_key().decode())
    assert SourceSettings(factory).state()["status"] == "configuration_error"
    with pytest.raises(Exception) as error:
        SourceSettings(factory).resolve()
    assert error.value.status_code == 503


def test_sci99_test_without_credentials_no_network_and_with_credentials_auth_only(accounts, monkeypatch):
    admin, _, _, _, factory, _ = accounts
    assert admin.post("/api/admin/sources/sci99/test").json()["status"] == "pending_credentials"
    settings={"SCI99_USER":"", "SCI99_PASS":"", "SCI99_COOKIE":"isolated=value", "SCI99_LOGIN_URL":""}
    calls=[]
    from pathlib import Path
    payload=json.loads((Path(__file__).parent/'fixtures/sci99/user-info-valid.json').read_text(encoding='utf-8'))
    with httpx.Client(transport=httpx.MockTransport(lambda request:(calls.append(request.url.path),httpx.Response(200,json=payload))[1])) as client:
        adapter=Sci99Adapter(settings=settings,client=client)
        adapter.interval=0
        adapter.test_connection()
    assert calls == ["/usercenter/newaccount/getUserInfoDetail"]


def test_sci99_runs_only_at_nine_and_daily_quota_persists(accounts, monkeypatch):
    factory=accounts[4]
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:Fernet.generate_key().decode())
    key=Fernet.generate_key().decode()
    monkeypatch.setattr(config,"read_settings_encryption_key",lambda:key)
    SourceSettings(factory).save(Sci99Input(enabled=True,cookie=secrets.token_urlsafe(20)))
    calls=[]
    monkeypatch.setattr(Sci99Adapter,"fetch",lambda self:(calls.append(True),(_ for _ in ()).throw(AccessStopped("已停止")))[1])
    monkeypatch.setattr(FetchService,"store",lambda *args:0)
    # 免费源也只用mock，不请求真实网站。
    from backend.app.adapters.free import FreeAdapter
    monkeypatch.setattr(FreeAdapter,"fetch",lambda self:[])
    import importlib
    for source in config.SOURCES.values():
        if source.get("adapter"):
            module,name=source["adapter"].split(":")
            monkeypatch.setattr(getattr(importlib.import_module("backend.app.adapters."+module),name),"fetch",lambda self:[])
    fetcher=FetchService(factory)
    for slot in ("08:00","08:30",None,"15:30"):
        assert next(r for r in fetcher.run(slot=slot)["results"] if r["source"]=="sci99")["status"] == "scheduled_only"
    assert not calls
    assert next(r for r in fetcher.run(slot="09:00")["results"] if r["source"]=="sci99")["status"] == "blocked"
    assert next(r for r in FetchService(factory).run(slot="09:00")["results"] if r["source"]=="sci99")["status"] == "daily_limit"
    assert len(calls)==1
    print("STAGE9_SCI99 other_slots_skipped=True daily_attempts=1 restart_preserves_limit=True")


def test_unchanged_price_does_not_refresh_row_or_grow_count(accounts):
    factory=accounts[4]
    add_prices(factory,[(AT.date(),"100")])
    with factory() as session:
        point=session.scalar(select(PricePoint))
        point.fetched_at=AT.replace(tzinfo=None)+timedelta(hours=1)
        session.expunge(point)
    assert FetchService(factory).store([point],point.source)==0
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PricePoint))==1
        assert session.scalar(select(PricePoint)).fetched_at==AT.replace(tzinfo=None)


def test_telegram_split_plain_and_email_html_use_same_text():
    text="采购行情日报 · 2026-11-13\n"+"价格 <&> ↑ 😀\n"*1000
    parts=telegram_parts(text,"采购行情日报 · 2026-11-13")
    assert all(len(part.encode("utf-16-le"))//2 <= 4096 for part in parts)
    bodies=[]
    with httpx.Client(transport=httpx.MockTransport(lambda request:(bodies.append(json.loads(request.content)),httpx.Response(200,json={"ok":True,"result":{"message_id":1},"id":"mock-message"}))[1])) as client:
        TelegramSender(client).send("普通文本 _*[]",{"TELEGRAM_BOT_TOKEN":"123:isolated","TELEGRAM_CHAT_ID":"mock"})
        EmailSender(client).send("相同文本",{"RESEND_API_KEY":"mock","EMAIL_FROM":"a@example.invalid","recipient":"b@example.invalid","subject":"日报","html":"<p>相同文本</p>"})
    assert "parse_mode" not in bodies[0] and bodies[1]["text"]=="相同文本" and bodies[1]["html"]=="<p>相同文本</p>"


def test_stage8_upgrade_backup_preserves_verification_and_is_idempotent(accounts, tmp_path):
    path=tmp_path/"stage8.sqlite3"
    accounts[1].put("/api/me/push-settings",json={"email":"preserved@example.invalid"})
    mark_verified(accounts[4], accounts[3][0])
    with sqlite3.connect(accounts[4].kw["bind"].url.database) as source, sqlite3.connect(path) as target:
        source.backup(target)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE push_settings DROP COLUMN daily_digest_enabled")
        for table in ("notification_claims","daily_digests","fetch_batches"):
            connection.execute("DROP TABLE "+table)
        connection.execute("UPDATE site_settings SET value='7' WHERE key='schema_version'")
    engine=make_engine(f"sqlite:///{path}")
    backup=initialize_database(engine)
    assert "pre-stage9" in backup
    with sqlite3.connect(backup) as connection:
        assert "daily_digest_enabled" not in {row[1] for row in connection.execute("PRAGMA table_info(push_settings)")}
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
        assert connection.execute("SELECT value FROM site_settings WHERE key='schema_version'").fetchone()[0]=="9"
        assert connection.execute("SELECT email_verified_at FROM push_settings").fetchone()[0] is not None
        assert connection.execute("SELECT daily_digest_enabled FROM push_settings").fetchone()[0] is None
    assert initialize_database(engine) is None
    engine.dispose()


def test_stage9_migration_failure_rolls_back_and_keeps_consistent_backup(accounts,tmp_path):
    path=tmp_path/"rollback.sqlite3"
    with sqlite3.connect(accounts[4].kw["bind"].url.database) as source, sqlite3.connect(path) as target:
        source.backup(target)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE push_settings DROP COLUMN daily_digest_enabled")
        for table in ("notification_claims","daily_digests","fetch_batches"):
            connection.execute("DROP TABLE "+table)
        connection.execute("UPDATE site_settings SET value='7' WHERE key='schema_version'")
    engine=make_engine(f"sqlite:///{path}")
    def fail(connection,cursor,statement,parameters,context,many):
        if statement.startswith("ALTER TABLE push_settings ADD COLUMN daily_digest_enabled"):
            raise RuntimeError("isolated rollback")
    event.listen(engine,"before_cursor_execute",fail)
    with pytest.raises(RuntimeError,match="isolated rollback"):
        initialize_database(engine)
    event.remove(engine,"before_cursor_execute",fail)
    with sqlite3.connect(path) as connection:
        assert "daily_digest_enabled" not in {row[1] for row in connection.execute("PRAGMA table_info(push_settings)")}
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='daily_digests'").fetchone() is None
        assert connection.execute("SELECT value FROM site_settings WHERE key='schema_version'").fetchone()[0]=="7"
    assert len(list((tmp_path/"backup").glob("rollback-pre-stage9-*.sqlite3")))==1
    engine.dispose()


def test_digest_html_escapes_provenance_instead_of_executing_it(notices,monkeypatch):
    from dataclasses import replace
    from backend.app import notifications
    accounts,_=notices
    item=replace(config.COMMODITIES[0],quote_basis='<script>alert("x")</script> & fixed')
    monkeypatch.setattr(notifications,"COMMODITIES",(item,))
    with accounts[4]() as session:
        _,text,html=build_digest(session,AT.date())
    assert item.quote_basis in text and "<script>" not in html and "&lt;script&gt;" in html


def test_connection_failure_is_redacted_and_does_not_consume_fetch_quota(accounts,monkeypatch,caplog):
    factory=accounts[4]
    hidden=secrets.token_urlsafe(25)
    class Stopped:
        def __init__(self,**kwargs): pass
        def test_connection(self): raise AccessStopped(hidden)
    manager=SourceSettings(factory,legacy_reader=lambda:{"SCI99_COOKIE":hidden})
    result=manager.test_connection(1,"192.0.2.1",adapter_factory=Stopped)
    with factory() as session:
        quota=session.scalar(select(func.count()).select_from(SourceRun))
    leaked=hidden in json.dumps(result)+json.dumps(manager.state())+caplog.text
    assert not leaked and result["status"]=="blocked" and quota==0
    with pytest.raises(Exception) as limited:
        manager.test_connection(1,"192.0.2.1",adapter_factory=Stopped)
    assert limited.value.status_code==429


def test_settings_key_only_comes_from_isolated_file(monkeypatch):
    monkeypatch.setenv("SETTINGS_ENCRYPTION_KEY",Fernet.generate_key().decode())
    assert config.read_settings_encryption_key()==""
    key=Fernet.generate_key().decode()
    (config.ROOT/".env").write_text("SETTINGS_ENCRYPTION_KEY="+key+"\n",encoding="utf-8")
    matches=config.read_settings_encryption_key()==key
    assert matches


def test_report_is_not_created_before_grace_or_for_old_round_crossing_midnight(notices):
    accounts,notifier=notices
    clock=[AT.replace(hour=9,minute=1)]
    notifier.clock=lambda:clock[0]
    runner=RoundRunner(fake_round(accounts[4]),notifier)
    runner.monitor_morning()
    assert not accounts[5].sender.calls
    fetcher=fake_round(accounts[4])
    original=fetcher.run
    def cross(*,slot=None):
        result=original(slot=slot)
        clock[0]=(AT+timedelta(days=1)).replace(hour=0,minute=1)
        return result
    fetcher.run=cross
    RoundRunner(fetcher,notifier).run("09:00")
    with accounts[4]() as session:
        assert session.scalar(select(func.count()).select_from(DailyDigest))==0


def test_notification_result_does_not_overwrite_reused_log_from_another_user(notices):
    accounts,notifier=notices
    admin,a,_,ids,factory,alerts=accounts
    a.put("/api/me/push-settings",json={"email":"a@example.invalid","daily_digest_enabled":True})
    mark_verified(factory,ids[0])
    def replace(text,settings):
        with factory() as session:
            target=session.scalar(select(PushLog).where(PushLog.user_id==ids[0]))
            original_id=target.id
            session.delete(target)
            session.flush()
            session.add(PushLog(id=original_id,user_id=ids[1],channel="email",kind="digest",send_day=AT.date(),created_at=AT.replace(tzinfo=None),message="another-owner",detail="unchanged",status="pending_configuration"))
            session.commit()
        return "sent","isolated",None
    alerts.email_sender.send=replace
    result=notifier.send(user_id=ids[0],channel="email",kind="digest",event="test-owner",title="日报",text="own")
    assert result["status"]=="skipped"
    with factory() as session:
        log=session.scalar(select(PushLog).where(PushLog.user_id==ids[1]))
        assert log.message=="another-owner" and log.detail=="unchanged"
