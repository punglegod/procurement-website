"""第七阶段认证修复：限流持久化、哈希锁范围和并发撤销边界。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
from threading import Barrier, Event

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from backend.app import auth, main
from backend.app.models import LoginThrottle, SecurityEvent, User, UserSession
from backend.tests.test_accounts import accounts, PASSWORD


def client_at(address):
    return TestClient(main.app, client=(address, 40000))


def registration(client, username):
    return client.post("/api/auth/register", json={"username": username, "password": PASSWORD})


def test_registration_global_budget_blocks_rotating_sources_before_hash(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    # fixture 已成功注册两个账号，另两个额度留给不同来源。
    monkeypatch.setattr(auth, "REGISTER_GLOBAL_HOURLY", 4)
    calls = []
    original = auth.hash_password

    def counted(value):
        calls.append(True)
        return original(value)

    monkeypatch.setattr(auth, "hash_password", counted)
    statuses = []
    for index in range(3):
        with client_at(f"198.51.100.{index + 20}") as client:
            response = registration(client, f"rotation_{index}")
            statuses.append(response.status_code)
    assert statuses == [201, 201, 429] and len(calls) == 2
    assert 1 <= int(response.headers["retry-after"]) <= 3600


def test_registration_daily_budget_survives_new_clients_and_hour_rollover(accounts, monkeypatch):
    monkeypatch.setattr(auth, "REGISTER_IP_HOURLY", 2)
    monkeypatch.setattr(auth, "REGISTER_IP_DAILY", 3)
    at = auth.utc_now()
    monkeypatch.setattr(auth, "utc_now", lambda: at)
    with client_at("198.51.100.40") as client:
        assert registration(client, "daily_first").status_code == 201
        assert registration(client, "daily_second").status_code == 201
        assert registration(client, "daily_third").status_code == 429
    at += timedelta(hours=2)
    with client_at("198.51.100.40") as restarted:
        assert registration(restarted, "daily_third").status_code == 201
        response = registration(restarted, "daily_fourth")
        assert response.status_code == 429
        assert 1 <= int(response.headers["retry-after"]) <= 86400


def test_closing_registration_during_hash_prevents_insert_without_blocking_admin(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    entered, release = Event(), Event()
    original = auth.hash_password

    def delayed(value):
        entered.set()
        assert release.wait(5)
        return original(value)

    monkeypatch.setattr(auth, "hash_password", delayed)
    with client_at("198.51.100.50") as client, ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(registration, client, "closed_during_hash")
        assert entered.wait(5)
        try:
            assert admin.put("/api/admin/registration", json={"enabled": False}).status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 403
    with factory() as session:
        assert session.scalar(select(User).where(User.username == "closed_during_hash")) is None


def test_duplicate_concurrent_registration_remains_atomic(accounts, monkeypatch):
    barrier = Barrier(2)
    original = auth.hash_password

    def synchronized(value):
        barrier.wait(timeout=5)
        return original(value)

    monkeypatch.setattr(auth, "hash_password", synchronized)
    with client_at("198.51.100.51") as first, client_at("198.51.100.52") as second, ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda client: registration(client, "duplicate_race").status_code, [first, second]))
    assert outcomes == [201, 201]
    # 响应相同，唯一约束仍保证只创建一条账号记录。
    with accounts[4]() as session:
        assert session.scalar(select(func.count()).select_from(User).where(User.username == "duplicate_race")) == 1


def test_failed_login_pair_delay_and_foreign_source_recovery(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    delays = []
    monkeypatch.setattr(auth, "sleep", delays.append)
    with client_at("198.51.100.60") as attacker, client_at("203.0.113.60") as legitimate:
        for _ in range(5):
            assert attacker.post("/api/auth/login", json={"username": "test_admin", "password": "incorrect-password"}).status_code == 401
        assert delays == list(auth.AUTH_FAILURE_DELAYS)
        blocked = attacker.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD})
        assert blocked.status_code == 429 and 1 <= int(blocked.headers["retry-after"]) <= 900
        assert legitimate.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD}).status_code == 200
    with factory() as session:
        events = session.scalars(select(SecurityEvent).where(SecurityEvent.client_hash == auth.digest("ip:198.51.100.60"))).all()
        assert [row.kind for row in events].count("login_failed") == 5
        assert [row.kind for row in events].count("login_limited") == 1
        assert all(row.account_hash == auth.digest("test_admin") for row in events)
        assert all("incorrect-password" not in row.detail and "198.51.100.60" not in row.detail for row in events)


def test_parallel_failure_attempts_cannot_bypass_pair_quota(accounts, monkeypatch):
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    monkeypatch.setattr(auth, "verify_password", lambda *args: False)

    def attempt(index):
        with client_at("198.51.100.61") as client:
            return client.post("/api/auth/login", json={"username": "account_a", "password": "incorrect-password"}).status_code

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(attempt, range(10)))
    assert outcomes.count(401) == 5 and outcomes.count(429) == 5


def test_login_source_budget_is_not_reset_by_success(accounts, monkeypatch):
    monkeypatch.setattr(auth, "AUTH_IP_PER_MINUTE", 3)
    with client_at("198.51.100.62") as client:
        outcomes = [client.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD}).status_code for _ in range(4)]
    assert outcomes == [200, 200, 200, 429]


def test_hash_and_verification_global_slots_reject_excess_and_release(accounts, monkeypatch):
    entered = Barrier(3)
    release = Event()

    def delayed_hash(self, value):
        entered.wait(timeout=5)
        assert release.wait(5)
        return "isolated-hash"

    monkeypatch.setattr(type(auth.hasher), "hash", delayed_hash)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(auth.hash_password, "isolated-password") for _ in range(2)]
        entered.wait(timeout=5)
        try:
            with pytest.raises(HTTPException) as denied_hash:
                auth.hash_password("isolated-password")
            with pytest.raises(HTTPException) as denied_verify:
                auth.verify_password("unused", "isolated-password")
            assert denied_hash.value.status_code == denied_verify.value.status_code == 429
            assert denied_hash.value.headers["Retry-After"] == "1"
        finally:
            release.set()
        assert [job.result(timeout=5) for job in jobs] == ["isolated-hash", "isolated-hash"]
    assert auth.verify_password("invalid-hash", "isolated-password") is False
    with auth.password_slot():
        with auth.password_slot():
            pass


def test_login_and_password_hashes_never_hold_database_writer(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    observations = []

    def inspect_writer():
        with sqlite3.connect(factory.kw["bind"].url.database, timeout=0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            observations.append("writer_available")
            connection.rollback()

    original_verify, original_hash = auth.verify_password, auth.hash_password

    def inspect_verify(*args):
        inspect_writer()
        return original_verify(*args)

    def inspect_hash(*args):
        inspect_writer()
        return original_hash(*args)

    monkeypatch.setattr(auth, "verify_password", inspect_verify)
    monkeypatch.setattr(auth, "hash_password", inspect_hash)
    assert a.post("/api/auth/login", json={"username": "account_a", "password": PASSWORD}).status_code == 200
    assert a.post("/api/auth/password", json={"old_password": PASSWORD, "new_password": "isolated-changed-password"}).status_code == 200
    assert observations == ["writer_available"] * 3


@pytest.mark.parametrize("mutation", ["disable", "change_password", "delete"])
def test_login_rechecks_account_after_slow_verification(accounts, monkeypatch, mutation):
    admin, a, b, ids, factory, alerts = accounts
    entered, release = Event(), Event()
    original = auth.verify_password
    monkeypatch.setattr(auth, "sleep", lambda _: None)

    def delayed(*args):
        valid = original(*args)
        entered.set()
        assert release.wait(5)
        return valid

    monkeypatch.setattr(auth, "verify_password", delayed)
    with client_at("198.51.100.70") as client, ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(client.post, "/api/auth/login", json={"username": "account_a", "password": PASSWORD})
        assert entered.wait(5)
        try:
            if mutation == "disable":
                assert admin.put(f"/api/admin/users/{ids[0]}", json={"enabled": False}).status_code == 200
            elif mutation == "delete":
                assert admin.delete(f"/api/admin/users/{ids[0]}").status_code == 200
            else:
                with factory() as session:
                    session.get(User, ids[0]).password_hash = auth.hash_password("isolated-changed-password")
                    session.execute(delete(UserSession).where(UserSession.user_id == ids[0]))
                    session.commit()
        finally:
            release.set()
        response = result.result(timeout=5)
        assert response.status_code == 401 and auth.COOKIE not in response.cookies
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == ids[0])) == 0


def test_password_change_does_not_resurrect_revoked_session(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    entered, release = Event(), Event()
    original = auth.hash_password

    def delayed(value):
        replacement = original(value)
        entered.set()
        assert release.wait(5)
        return replacement

    monkeypatch.setattr(auth, "hash_password", delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(a.post, "/api/auth/password", json={"old_password": PASSWORD, "new_password": "isolated-changed-password"})
        assert entered.wait(5)
        try:
            assert a.post("/api/auth/logout").status_code == 200
        finally:
            release.set()
        assert pending.result(timeout=5).status_code == 401
    with factory() as session:
        assert auth.verify_password(session.get(User, ids[0]).password_hash, PASSWORD)


def test_old_global_throttle_keys_are_ignored_and_pair_resets_after_window(accounts, monkeypatch):
    admin, a, b, ids, factory, alerts = accounts
    at = auth.utc_now()
    monkeypatch.setattr(auth, "utc_now", lambda: at)
    monkeypatch.setattr(auth, "sleep", lambda _: None)
    with factory() as session:
        session.add(LoginThrottle(key=auth.digest("user:test_admin"), failures=5, reset_at=at+timedelta(minutes=15)))
        session.commit()
    with client_at("198.51.100.80") as client:
        assert client.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD}).status_code == 200
        for _ in range(5):
            assert client.post("/api/auth/login", json={"username": "test_admin", "password": "incorrect-password"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD}).status_code == 429
        at += timedelta(minutes=16)
        assert client.post("/api/auth/login", json={"username": "test_admin", "password": PASSWORD}).status_code == 200
