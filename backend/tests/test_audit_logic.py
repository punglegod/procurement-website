"""审计复现：仅使用 pytest 临时环境与 SQLite，不发送网络推送。"""

from datetime import datetime
from decimal import Decimal
import json
from threading import Event, Thread

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from sqlalchemy.orm import sessionmaker

from backend.app import alerts as alerts_module, auth, main
from backend.app.alerts import AlertService, RuleInput, evaluate, matching_points
from backend.app.config import COMMODITY_MAP, TIMEZONE
from backend.app.db import initialize_database, make_engine
from backend.app.models import AlertRule, PricePoint


AT = datetime(2026, 11, 12, 23, 59, 59, tzinfo=TIMEZONE)


class IsolatedSender:
    def __init__(self):
        self.messages = []

    def send(self, text, settings):
        self.messages.append(text)
        return "sent", "审计隔离通道；未发送真实消息", len(self.messages)


@pytest.fixture
def audit_factory(tmp_path, monkeypatch):
    engine = make_engine(f"sqlite:///{(tmp_path / 'audit.sqlite3').as_posix()}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    yield factory
    engine.dispose()


def make_service(factory, monkeypatch):
    sender = IsolatedSender()
    service = AlertService(factory, sender=sender,
                           settings_reader=lambda: {"TELEGRAM_BOT_TOKEN": "audit-isolated", "TELEGRAM_CHAT_ID": "audit-isolated"})
    monkeypatch.setattr(alerts_module, "now", lambda: AT)
    item = COMMODITY_MAP["alu_a00"]
    with factory() as session:
        session.add(PricePoint(code=item.code, price=Decimal("100"), unit=item.unit,
                               quote_basis=item.quote_basis, quote_date=AT.date(),
                               source=item.source, source_name="审计隔离报价",
                               source_url="https://example.invalid/audit", fetched_at=AT.replace(tzinfo=None)))
        session.commit()
    return service, sender


def test_audit_huge_page_is_rejected(audit_factory, monkeypatch):
    service, _ = make_service(audit_factory, monkeypatch)
    monkeypatch.setattr(main, "alerts", service)
    # 不进入 lifespan，防止启动与本复现无关的后台调度器。
    with TestClient(main.app, raise_server_exceptions=False) as client:
        assert client.post("/api/auth/login", json={"username": "test_admin", "password": "isolated-test-password"}).status_code == 200
        ordinary = client.get("/api/alerts/logs?page=999&page_size=100")
        huge = client.get("/api/alerts/logs?page=9223372036854775808&page_size=100")
        print("AUDIT_PAGINATION " + json.dumps({"ordinary_page": ordinary.status_code,
              "ordinary_items": ordinary.json()["items"], "huge_page": huge.status_code,
              "huge_body": huge.text}))
        assert ordinary.status_code == 200 and ordinary.json()["items"] == []
        assert huge.status_code == 422
    with pytest.raises(HTTPException) as error:
        service.logs(page=9223372036854775808, page_size=100, user_id=1)
    assert error.value.status_code == 422
    print("AUDIT_PAGINATION_EXCEPTION " + str(error.value))


def test_audit_rule_edit_between_evaluate_and_deliver(audit_factory, monkeypatch):
    service, sender = make_service(audit_factory, monkeypatch)
    saved = service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1"), user_id=1)
    evaluated, changed = Event(), Event()
    original_channels = service.channels
    paused = [False]

    def channels(user_id):
        if not paused[0]:
            paused[0] = True
            evaluated.set()
            assert changed.wait(10), "规则更新线程超时"
        return original_channels(user_id)

    monkeypatch.setattr(service, "channels", channels)
    output = {}

    def check():
        output.update(service.run(user_id=1))

    thread = Thread(target=check)
    thread.start()
    assert evaluated.wait(10), "提醒评估线程超时"
    service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1000"), saved["id"], user_id=1)
    changed.set()
    thread.join(10)
    assert not thread.is_alive()
    with audit_factory() as session:
        rule = session.get(AlertRule, saved["id"])
        current_hit, _ = evaluate(rule, matching_points(session, rule, AT.date()), AT.date())
        threshold = str(rule.threshold)
    print("AUDIT_RULE_EDIT_RACE " + json.dumps({"saved_threshold": threshold, "current_price": 100,
          "current_condition_matches": current_hit, "result": output["results"][0]["status"],
          "isolated_sends": len(sender.messages), "daily_quota_rows": service.logs(user_id=1)["total"]}, ensure_ascii=False))
    assert current_hit is False and sender.messages == []
    assert output["results"][0]["status"] == "skipped"
    assert service.logs(user_id=1)["total"] == 0


def test_audit_midnight_stops_old_batch(audit_factory, monkeypatch):
    service, sender = make_service(audit_factory, monkeypatch)
    service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="1"), user_id=1)
    second_rule = service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="2"), user_id=1)
    clock = [AT]
    monkeypatch.setattr(alerts_module, "now", lambda: clock[0])
    actual_dates = []

    def send(text, settings):
        actual_dates.append(clock[0].date().isoformat())
        sender.messages.append(text)
        clock[0] = datetime(2026, 11, 13, 0, 0, 1, tzinfo=TIMEZONE)
        return "sent", "审计隔离通道", len(sender.messages)

    monkeypatch.setattr(sender, "send", send)
    results = service.run(user_id=1)["results"]
    item = COMMODITY_MAP["alu_a00"]
    with audit_factory() as session:
        session.add(PricePoint(code=item.code, price=Decimal("101"), unit=item.unit,
                               quote_basis=item.quote_basis, quote_date=clock[0].date(),
                               source=item.source, source_name="审计隔离报价",
                               source_url="https://example.invalid/audit", fetched_at=clock[0].replace(tzinfo=None)))
        session.commit()
    next_results = service.run(user_id=1)["results"]
    attempts = [row for row in results + next_results if row["status"] == "sent"]
    print("AUDIT_MIDNIGHT " + json.dumps({"actual_send_dates": actual_dates,
          "recorded_send_days": [row["send_day"] for row in attempts],
          "old_batch_second_rule": results[1]["status"],
          "second_rule_actual_sends_on_nov13": sum(row["rule_id"] == second_rule["id"] and row["send_day"] == "2026-11-13" for row in attempts),
          "old_quote_sent_after_midnight": any("报价日期：2026-11-12" in message for message in sender.messages[1:])}, ensure_ascii=False))
    assert actual_dates == ["2026-11-12", "2026-11-13", "2026-11-13"]
    assert results[1]["status"] == "skipped"
    assert [row["send_day"] for row in attempts] == actual_dates
    assert not any("报价日期：2026-11-12" in message for message in sender.messages[1:])
