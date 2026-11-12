"""验收运行中的第三阶段 API；只创建并删除本脚本规则，记录一次真实通道测试。"""

import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/health").json() == {"status": "ok"}
        channel = client.get("/api/alerts/status").json()
        before = client.get("/api/alerts/rules").json()["items"]
        payload = {"code": "alu_a00", "kind": "price_gte", "threshold": 999999, "enabled": False}
        created = client.post("/api/alerts/rules", json=payload)
        assert created.status_code == 201
        rule_id = created.json()["id"]
        try:
            updated = client.put(f"/api/alerts/rules/{rule_id}", json=payload | {"threshold": 999998})
            assert updated.status_code == 200 and updated.json()["threshold"] == 999998
            rows = client.get("/api/alerts/rules").json()["items"]
            assert any(row["id"] == rule_id and not row["enabled"] for row in rows)
            assert client.post("/api/alerts/rules", json=payload | {"threshold": 0}).status_code == 422
        finally:
            assert client.delete(f"/api/alerts/rules/{rule_id}").status_code == 200
        assert [r["id"] for r in client.get("/api/alerts/rules").json()["items"]] == [r["id"] for r in before]
        test = client.post("/api/alerts/test")
        assert test.status_code == 200
        test = test.json()
        logs = client.get("/api/alerts/logs").json()
        assert logs["items"][0]["id"] == test["id"]
        if channel["status"] == "pending_configuration":
            assert test["status"] == "pending_configuration" and test["telegram_message_id"] is None
            verdict = "待配置；真实发送暂不验收，不计失败"
        else:
            assert test["status"] == "sent", f"真实通道测试未确认成功：{test['detail']}"
            verdict = "Telegram API 已接受测试消息；实际收信请在目标会话核对"
    output = {"base_url": args.base_url, "crud": "passed", "channel": channel, "test": test, "verdict": verdict}
    path = Path(__file__).resolve().parents[2] / "data/stage3-live.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
