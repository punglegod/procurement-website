"""第七阶段提醒一致性、邮箱验证门禁和持久配额回归；全部隔离发送。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.alerts import AlertService, RuleInput
from backend.app.config import COMMODITY_MAP, TIMEZONE
from backend.app.db import initialize_database, make_engine
from backend.app.models import AlertRule, PricePoint, PushLog, PushSetting, RateLimitBucket, User


AT = datetime(2026, 11, 12, 16, tzinfo=TIMEZONE)
MAIL = {"RESEND_API_KEY": "isolated-stage7-key", "EMAIL_FROM": "sender@example.invalid"}
TELEGRAM = {"TELEGRAM_BOT_TOKEN": "isolated-stage7-token", "TELEGRAM_CHAT_ID": "isolated-stage7-chat"}
RULE = RuleInput(code="alu_a00", kind="price_gte", threshold="1")


class Sender:
    def __init__(self):
        self.calls = []

    def send(self, text, settings):
        self.calls.append((text, dict(settings)))
        return "sent", "隔离发送结果", "isolated-message"


@pytest.fixture
def setup_alerts(tmp_path):
    engine = make_engine(f"sqlite:///{(tmp_path / 'stage7-alerts.sqlite3').as_posix()}")
    initialize_database(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    mail_sender, telegram_sender = Sender(), Sender()
    service = AlertService(factory, sender=telegram_sender, settings_reader=lambda: TELEGRAM,
                           email_sender=mail_sender, email_settings_reader=lambda: MAIL, clock=lambda: AT)
    with factory() as session:
        user = User(username="stage7_user", password_hash="isolated-unused-hash", role="user", enabled=True, created_at=AT.replace(tzinfo=None))
        session.add(user)
        session.flush()
        user_id = user.id
        session.add(PushSetting(user_id=user_id, email="recipient@example.invalid"))
        item = COMMODITY_MAP["alu_a00"]
        session.add(PricePoint(code=item.code, price=Decimal("100"), unit=item.unit, quote_basis=item.quote_basis,
                               quote_date=AT.date(), source=item.source, source_name="隔离测试报价",
                               source_url="https://example.invalid/quote", fetched_at=AT.replace(tzinfo=None)))
        session.commit()
    yield factory, service, user_id, mail_sender, telegram_sender
    engine.dispose()


def verify(factory, user_id):
    with factory() as session:
        session.get(PushSetting, user_id).email_verified_at = AT.replace(tzinfo=None)
        session.commit()


def test_unverified_mail_never_sends_and_verification_reuses_pending_row(setup_alerts):
    factory, service, user_id, sender, _ = setup_alerts
    service.save(RULE, user_id=user_id)
    for _ in range(3):
        result = service.run(user_id=user_id)["results"][0]
        assert result["status"] == "pending_verification"
    pending_id = result["id"]
    assert service.status(user_id)["status"] == "pending_verification"
    assert service.logs(user_id=user_id)["total"] == 1
    with pytest.raises(HTTPException) as error:
        service.test(user_id=user_id)
    assert error.value.status_code == 403 and sender.calls == []
    verify(factory, user_id)
    sent = service.run(user_id=user_id)["results"][0]
    assert sent["status"] == "sent" and sent["id"] == pending_id
    assert service.run(user_id=user_id)["results"][0]["status"] == "deduplicated"
    assert len(sender.calls) == 1 and service.logs(user_id=user_id)["total"] == 1


def test_missing_provider_then_verification_preserves_pending_slot(setup_alerts):
    factory, service, user_id, sender, _ = setup_alerts
    service.save(RULE, user_id=user_id)
    service.email_settings_reader = lambda: {}
    first = service.run(user_id=user_id)["results"][0]
    assert first["status"] == "pending_configuration"
    service.email_settings_reader = lambda: MAIL
    second = service.run(user_id=user_id)["results"][0]
    assert second["status"] == "pending_verification" and second["id"] == first["id"]
    verify(factory, user_id)
    third = service.run(user_id=user_id)["results"][0]
    assert third["status"] == "sent" and third["id"] == first["id"] and len(sender.calls) == 1


def test_admin_telegram_continues_while_mail_waits_for_verification(setup_alerts):
    factory, service, _, mail_sender, telegram_sender = setup_alerts
    with factory() as session:
        session.add(PushSetting(user_id=1, email="admin@example.invalid"))
        session.commit()
    results = service.test(user_id=1)["results"]
    assert [row["status"] for row in results] == ["sent", "pending_verification"]
    assert len(telegram_sender.calls) == 1 and not mail_sender.calls
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(RateLimitBucket)) == 0


def test_address_changed_after_channel_snapshot_is_rechecked(setup_alerts, monkeypatch):
    factory, service, user_id, sender, _ = setup_alerts
    verify(factory, user_id)
    original = service.channels

    def change_address(owner_id):
        snapshot = original(owner_id)
        with factory() as session:
            setting = session.get(PushSetting, owner_id)
            setting.email = "changed@example.invalid"
            setting.email_verified_at = None
            session.commit()
        return snapshot

    monkeypatch.setattr(service, "channels", change_address)
    with pytest.raises(HTTPException) as error:
        service.test(user_id=user_id, client_ip="198.51.100.7")
    assert error.value.status_code == 403 and not sender.calls
    assert service.logs(user_id=user_id)["total"] == 0


def test_concurrent_test_mail_reserves_recipient_quota_and_rules_remain_independent(setup_alerts):
    factory, service, user_id, sender, _ = setup_alerts
    verify(factory, user_id)

    def send(_):
        try:
            return service.test(user_id=user_id, client_ip="198.51.100.7")["status"]
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(send, range(4)))
    assert results.count("sent") == 3 and results.count(429) == 1
    assert len(sender.calls) == 3
    service.save(RULE, user_id=user_id)
    assert service.run(user_id=user_id)["results"][0]["status"] == "sent"
    assert len(sender.calls) == 4


def test_rule_create_limit_is_atomic_and_delete_or_edit_does_not_reset_it(setup_alerts):
    _, service, user_id, _, _ = setup_alerts

    def create(_):
        try:
            return service.save(RULE, user_id=user_id)["id"]
        except HTTPException as error:
            return str(error.status_code)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(create, range(12)))
    ids = [value for value in results if isinstance(value, int)]
    assert len(ids) == 10 and results.count("429") == 2
    edited = service.save(RuleInput(code="alu_a00", kind="price_lte", threshold="9"), ids[0], user_id=user_id)
    assert edited["kind"] == "price_lte"
    assert service.delete(ids[1], user_id=user_id)
    with pytest.raises(HTTPException) as error:
        service.save(RULE, user_id=user_id)
    assert error.value.status_code == 429


def test_total_rule_count_is_limited_to_twenty(setup_alerts):
    factory, service, user_id, _, _ = setup_alerts
    item = COMMODITY_MAP["alu_a00"]
    with factory() as session:
        for _ in range(20):
            session.add(AlertRule(user_id=user_id, code=item.code, kind="price_gte", threshold=1,
                                  enabled=True, source=item.source, quote_basis=item.quote_basis, unit=item.unit,
                                  created_at=AT.replace(tzinfo=None), updated_at=AT.replace(tzinfo=None)))
        session.commit()
    with pytest.raises(HTTPException) as error:
        service.save(RULE, user_id=user_id)
    assert error.value.status_code == 429 and "20" in error.value.detail


@pytest.mark.parametrize("change,expected", [("price", "skipped"), ("threshold", "sent"), ("commodity", "skipped")])
def test_delivery_rechecks_latest_price_rule_and_message(setup_alerts, monkeypatch, change, expected):
    factory, service, _, _, sender = setup_alerts
    rule = service.save(RULE, user_id=1)
    original = service.channels
    changed = [False]

    def change_between_checks(owner_id):
        if not changed[0]:
            changed[0] = True
            if change == "price":
                with factory() as session:
                    session.scalar(select(PricePoint)).price = Decimal("0.50")
                    session.commit()
            elif change == "threshold":
                service.save(RuleInput(code="alu_a00", kind="price_gte", threshold="50"), rule["id"], user_id=1)
            else:
                service.save(RuleInput(code="abs", kind="price_gte", threshold="1"), rule["id"], user_id=1)
        return original(owner_id)

    monkeypatch.setattr(service, "channels", change_between_checks)
    assert service.run(user_id=1)["results"][0]["status"] == expected
    if expected == "sent":
        assert "≥ 50.0000" in sender.calls[0][0] and len(sender.calls) == 1
    else:
        assert not sender.calls and service.logs(user_id=1)["total"] == 0


def test_explicit_replay_clock_and_beijing_date_are_preserved(setup_alerts):
    factory, service, _, _, sender = setup_alerts
    service.save(RULE, user_id=1)
    service.clock = lambda: AT + timedelta(days=100)
    replay = service.run(at=AT.astimezone(timezone.utc), user_id=1)["results"][0]
    assert replay["status"] == "sent" and replay["send_day"] == AT.date().isoformat()
    assert service.run(at=AT, user_id=1)["results"][0]["status"] == "deduplicated"
    assert len(sender.calls) == 1
    midnight = datetime(2026, 11, 13, 0, 0, 1, tzinfo=TIMEZONE)
    item = COMMODITY_MAP["alu_a00"]
    with factory() as session:
        session.add(PricePoint(code=item.code, price=Decimal("101"), unit=item.unit, quote_basis=item.quote_basis,
                               quote_date=midnight.date(), source=item.source, source_name="隔离测试报价",
                               source_url="https://example.invalid/quote", fetched_at=midnight.replace(tzinfo=None)))
        session.commit()
    new_service = AlertService(factory, sender=sender, settings_reader=lambda: TELEGRAM, clock=lambda: midnight.astimezone(timezone.utc))
    assert new_service.run(user_id=1)["results"][0]["send_day"] == "2026-11-13"
