"""账号删除期间复用日志编号时，旧在途发送不能读写新账号日志。"""

from datetime import datetime

import pytest
from sqlalchemy import select

from backend.app.models import PushLog, PushSetting
from backend.tests.test_accounts import accounts


@pytest.mark.parametrize("path", ["alerts", "verification"])
def test_send_completion_rechecks_owner_when_deleted_log_id_is_reused(accounts, path):
    admin, a, b, ids, factory, alerts = accounts
    aid, bid = ids
    assert a.put("/api/me/push-settings", json={"email": "a@example.com"}).status_code == 200
    if path == "alerts":
        with factory() as session:
            session.get(PushSetting, aid).email_verified_at = datetime.now()
            session.commit()
    reused_ids = []

    class ReplacingSender:
        def send(self, text, settings):
            # 模拟网络等待期间管理员删除 A，然后 B 创建了复用同一编号的日志。
            with factory() as session:
                original_id = session.scalar(select(PushLog.id).where(PushLog.user_id == aid))
            assert admin.delete(f"/api/admin/users/{aid}").status_code == 200
            with factory() as session:
                replacement = PushLog(user_id=bid, channel="email", recipient="b-private@example.com",
                                      kind="verification" if path == "verification" else "test",
                                      send_day=datetime.now().date(), created_at=datetime.now(), status="unknown",
                                      message="B_PRIVATE_MESSAGE", detail="B_PRIVATE_DETAIL")
                session.add(replacement)
                session.commit()
                assert replacement.id == original_id
                reused_ids.append(original_id)
            return "sent", "A_SEND_RESULT", "A_PROVIDER_ID"

    alerts.email_sender = ReplacingSender()
    endpoint = "/api/alerts/test" if path == "alerts" else "/api/me/email-verification/request"
    response = a.post(endpoint)
    assert response.status_code == 200 and reused_ids
    assert "B_PRIVATE" not in response.text and "b-private@example.com" not in response.text
    if path == "alerts":
        assert response.json()["status"] == "skipped"
    with factory() as session:
        other = session.get(PushLog, reused_ids[0])
        assert other.user_id == bid and other.status == "unknown"
        assert other.detail == "B_PRIVATE_DETAIL" and other.provider_message_id is None
        assert other.finished_at is None
    print(f"STAGE7_LOG_OWNER path={path} reused_id=True other_owner_unchanged=True other_data_returned=False")
