"""隔离验证触发边界、日额度、通道错误与规则接口，不向真实 Telegram 发消息。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.app import config, main, auth
from backend.app.alerts import AlertService, RuleInput, TelegramSender, evaluate
from backend.app.config import COMMODITY_MAP, TIMEZONE
from backend.app.db import initialize_database, make_engine
from backend.app.models import AlertRule, PricePoint, PushLog

DAY = date(2026, 11, 12)
AT = datetime(2026, 11, 12, 16, tzinfo=TIMEZONE)
SETTINGS = {"TELEGRAM_BOT_TOKEN": "123:isolated-test", "TELEGRAM_CHAT_ID": "isolated-chat"}


@pytest.fixture
def factory(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{(tmp_path/'alerts.sqlite3').as_posix()}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    yield factory
    engine.dispose()


def point(price, day=DAY, **kwargs):
    item = COMMODITY_MAP["alu_a00"]
    return PricePoint(code=item.code, price=Decimal(price), quote_date=day, source="free",
                      quote_basis=kwargs.get("quote_basis", item.quote_basis), unit=item.unit,
                      source_name="隔离测试", source_url="https://example.com/isolated", fetched_at=AT.replace(tzinfo=None))


def rule(kind="daily_change", threshold="5"):
    item = COMMODITY_MAP["alu_a00"]
    return AlertRule(code=item.code, kind=kind, threshold=Decimal(threshold), source=item.source,
                     quote_basis=item.quote_basis, unit=item.unit, enabled=True)


@pytest.mark.parametrize("kind,threshold,old,current,hit", [
    ("daily_change", "5", "100", "105", True),
    ("daily_change", "5", "100", "95", True),
    ("daily_change", "5", "100", "104.99", False),
    ("daily_change", "5", "100", "95.01", False),
    ("daily_change", "1", "1000", "1009.99", False),
    ("daily_change", "1", "100", "100", False),
    ("price_gte", "105", "100", "105", True),
    ("price_gte", "105", "100", "104.99", False),
    ("price_lte", "95", "100", "95", True),
    ("price_lte", "95", "100", "95.01", False),
])
def test_inclusive_boundaries_and_absolute_daily_change(kind, threshold, old, current, hit):
    assert evaluate(rule(kind, threshold), [point(current), point(old, DAY-timedelta(days=3))], DAY)[0] is hit


def test_empty_stale_missing_previous_and_basis_drift():
    assert not evaluate(rule(), [], DAY)[0]
    assert not evaluate(rule(), [point("200", DAY-timedelta(days=1)), point("100", DAY-timedelta(days=2))], DAY)[0]
    assert not evaluate(rule(), [point("200")], DAY)[0]
    changed = rule("price_gte", "1")
    changed.quote_basis = "旧口径"
    assert not evaluate(changed, [point("200")], DAY)[0]


class Sender:
    def __init__(self, status="sent"):
        self.calls = []
        self.status = status

    def send(self, text, settings):
        self.calls.append(text)
        return self.status, "隔离通道结果", 42 if self.status == "sent" else None


def seed(factory, sender=None, settings=None):
    service = AlertService(factory, sender=sender or Sender(), settings_reader=lambda: SETTINGS if settings is None else settings)
    saved = service.save(RuleInput(code="alu_a00", kind="daily_change", threshold="5"), user_id=1)
    with factory() as session:
        session.add_all([point("100", DAY-timedelta(days=3)), point("105"),
                         point("999", DAY-timedelta(days=1), quote_basis="其他口径不得对比")])
        session.commit()
        # v10默认口径随机；日额度回归显式选择原测试口径，其他条目继续隔离存在。
        from backend.app.quotes import backfill_prices, choose
        from backend.app.models import QuoteEntry
        backfill_prices(session)
        entry=session.scalar(select(QuoteEntry).where(QuoteEntry.specification==COMMODITY_MAP["alu_a00"].quote_basis))
        choose(session,1,"alu_a00",entry.id)
        session.commit()
    return service, saved


@pytest.mark.parametrize("status", ["sent", "failed", "unknown"])
def test_atomic_daily_dedup_persists_after_restart_and_edit(factory, status):
    sender = Sender(status)
    service, saved = seed(factory, sender)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outputs = list(pool.map(lambda _: service.run(at=AT, user_id=1)["results"][0]["status"], range(4)))
    assert outputs.count(status) == 1
    assert outputs.count("deduplicated") == 3
    service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1"), saved["id"], user_id=1)
    restarted = AlertService(factory, sender=sender, settings_reader=lambda: SETTINGS)
    assert restarted.run(at=AT, user_id=1)["results"][0]["status"] == "deduplicated"
    with factory() as session:
        session.add(point("106", DAY+timedelta(days=1)))
        session.commit()
    assert restarted.run(at=AT+timedelta(days=1), user_id=1)["results"][0]["status"] == status
    assert len(sender.calls) == 2
    assert service.delete(saved["id"], user_id=1)
    assert service.logs(user_id=1)["total"] == 2
    assert service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1"), user_id=1)["id"] > saved["id"]


def test_missing_config_does_not_consume_rule_quota_or_send(factory):
    sender = Sender()
    service, _ = seed(factory, sender, {})
    assert service.run(at=AT, user_id=1)["results"][0]["status"] == "pending_configuration"
    assert service.logs(user_id=1)["total"] == 1
    assert service.test(user_id=1)["status"] == "pending_configuration"
    assert not sender.calls
    service.settings_reader = lambda: SETTINGS
    assert service.run(at=AT, user_id=1)["results"][0]["status"] == "sent"


def test_disabled_rule_and_test_do_not_interfere_with_daily_quota(factory):
    service, saved = seed(factory)
    service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1", enabled=False), saved["id"], user_id=1)
    assert service.run(at=AT, user_id=1)["results"] == []
    assert service.test(user_id=1)["status"] == "sent"
    service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1"), saved["id"], user_id=1)
    assert service.run(at=AT, user_id=1)["results"][0]["status"] == "sent"


@pytest.mark.parametrize("response,expected", [
    (httpx.Response(200, json={"ok": True, "result": {"message_id": 17}}), "sent"),
    (httpx.Response(200, json={"ok": False, "description": "secret-data"}), "failed"),
    (httpx.Response(200, json={"ok": True, "result": {}}), "failed"),
    (httpx.Response(403, text="secret-data"), "failed"),
    (httpx.Response(429, text="secret-data"), "failed"),
    (httpx.Response(500, text="secret-data"), "failed"),
    (httpx.Response(200, text="invalid json"), "unknown"),
])
def test_telegram_only_reports_confirmed_success_without_leaking(response, expected, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        return response
    with caplog.at_level(logging.DEBUG), httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = TelegramSender(client).send("隔离测试", SETTINGS)
    assert result[0] == expected
    assert len(calls) == 1
    assert "secret-data" not in str(result)
    assert SETTINGS["TELEGRAM_BOT_TOKEN"] not in caplog.text
    assert SETTINGS["TELEGRAM_CHAT_ID"] not in caplog.text


def test_timeout_is_unknown_without_retry():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert TelegramSender(client).send("隔离测试", SETTINGS)[0] == "unknown"
    assert len(calls) == 1


def test_telegram_reads_only_root_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unexpected")
    assert not config.read_telegram_settings()["TELEGRAM_BOT_TOKEN"]
    (tmp_path/".env").write_text("TELEGRAM_BOT_TOKEN=literal${TOKEN}\nTELEGRAM_CHAT_ID=123\n", encoding="utf-8-sig")
    assert config.read_telegram_settings()["TELEGRAM_BOT_TOKEN"] == "literal${TOKEN}"


def test_api_crud_validation_logs_and_channel(factory, monkeypatch):
    service = AlertService(factory, sender=Sender(), settings_reader=lambda: {})
    monkeypatch.setattr(main, "alerts", service)
    monkeypatch.setattr(main, "initialize_database", lambda: None)
    monkeypatch.setattr(main, "channel_status", lambda: {"status": "pending_configuration"})
    # 接口验证始终使用隔离数据库，不写项目数据库。
    with TestClient(main.app) as client:
        assert client.post("/api/auth/login", json={"username": "test_admin", "password": "isolated-test-password"}).status_code == 200
        assert client.get("/api/alerts/status").json()["status"] == "pending_configuration"
        payload = {"code": "pe", "kind": "price_lte", "threshold": 9000}
        created = client.post("/api/alerts/rules", json=payload)
        assert created.status_code == 201
        rule_id = created.json()["id"]
        assert client.get("/api/alerts/rules").json()["items"][0]["data_message"] == "暂无同口径报价，等待数据"
        assert client.put(f"/api/alerts/rules/{rule_id}", json=payload | {"enabled": False}).json()["enabled"] is False
        for invalid in ({"code": "invalid"}, {"threshold": 0}, {"threshold": -1}, {"threshold": "NaN"}, {"threshold": "Infinity"}, {"kind": "invalid"}):
            assert client.post("/api/alerts/rules", json=payload | invalid).status_code == 422
        assert client.post("/api/alerts/test").json()["status"] == "pending_configuration"
        assert client.get("/api/alerts/logs").json()["total"] == 1
        assert client.get("/api/alerts/logs?page=0").status_code == 422
        assert client.get("/api/alerts/logs?page_size=101").status_code == 422
        assert client.post("/api/alerts/check").json()["results"] == []
        assert client.delete(f"/api/alerts/rules/{rule_id}").status_code == 200
        assert client.delete(f"/api/alerts/rules/{rule_id}").status_code == 404
        assert client.put(f"/api/alerts/rules/{rule_id}", json=payload).status_code == 404
