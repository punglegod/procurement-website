"""运行中全站验收：只读行情和历史，临时暂停规则 CRUD，不发真实推送。"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    results = {}
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=30) as client:
        def get(path, **kwargs):
            response = client.get(path, **kwargs)
            response.raise_for_status()
            return response.json()

        assert get("/api/health") == {"status": "ok"}
        page = client.get("/")
        assert page.status_code == 200 and 'id="app"' in page.text
        assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
        for asset in assets:
            assert client.get(asset).status_code == 200
        assert client.get("/favicon.svg").status_code == 200
        results["static"] = {"root": 200, "assets": assets, "favicon": 200}
        catalog = get("/api/commodities")["items"]
        assert len(catalog) == 11 and all(c["enabled"] and c["source"] != "sci99" for c in catalog)
        rows = get("/api/prices/latest")["items"]
        assert len(rows) == 11
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        history_counts = {}
        for row in rows:
            point = row["price_point"]
            assert point and point["price"] > 0, row["code"]
            assert point["code"] == row["code"] and point["quote_basis"] == row["quote_basis"]
            assert point["source"] == row["source"] and point["unit"] == row["unit"]
            assert point["source_name"] and point["source_url"].startswith("https://") and point["fetched_at"].endswith("+08:00")
            assert row["stale"] == (point["quote_date"] < today)
            history_counts[row["code"]] = {}
            for days in (7, 30, 90):
                history = get("/api/prices/history", params={"code": row["code"], "days": days})["items"]
                assert any(p["quote_date"] == point["quote_date"] and p["price"] == point["price"] for p in history)
                assert [p["quote_date"] for p in history] == sorted(p["quote_date"] for p in history)
                assert all(p["quote_basis"] == point["quote_basis"] and p["source"] == point["source"] for p in history)
                history_counts[row["code"]][str(days)] = len(history)
        results["prices"] = rows
        results["history_counts"] = history_counts
        sources = get("/api/sources/status")["items"]
        sci99 = next(s for s in sources if s["source"] == "sci99")
        assert sci99["status"] == "pending_credentials" and not sci99["enabled_codes"]
        results["sources"] = sources
        before = get("/api/alerts/rules")["items"]
        checked = []
        for commodity in catalog:
            payload = {"code": commodity["code"], "kind": "price_gte", "threshold": 999999, "enabled": False}
            response = client.post("/api/alerts/rules", json=payload)
            assert response.status_code == 201
            rule_id = response.json()["id"]
            try:
                saved = client.put(f"/api/alerts/rules/{rule_id}", json=payload | {"threshold": 999998})
                assert saved.status_code == 200 and saved.json()["threshold"] == 999998
                assert saved.json()["quote_basis"] == commodity["quote_basis"]
                assert not saved.json()["enabled"]
                assert client.post("/api/alerts/rules", json=payload | {"threshold": 0}).status_code == 422
            finally:
                assert client.delete(f"/api/alerts/rules/{rule_id}").status_code == 200
            checked.append(commodity["code"])
        assert [r["id"] for r in get("/api/alerts/rules")["items"]] == [r["id"] for r in before]
        results["alerts"] = {"paused_crud_codes": checked, "channel": get("/api/alerts/status"), "existing_rules_unchanged": True,
                             "log_page": get("/api/alerts/logs", params={"page": 1, "page_size": 1})["page"],
                             "real_send": "not_requested_stage3_already_accepted"}
        for path, status in [("/api/prices/history?code=missing&days=30", 404),
                             ("/api/prices/history?code=abs&days=0", 422),
                             ("/api/prices/history?code=abs&days=3651", 422),
                             ("/api/alerts/logs?page=0", 422), ("/api/missing", 404), ("/assets/missing.js", 404)]:
            assert client.get(path).status_code == status
        results["invalid_parameters"] = "passed"
    target = Path(__file__).resolve().parents[2] / "data/stage4-live.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("PASS: 单服务首页/构建资源/favicon；11品种真实价格与元数据；33组历史查询；过期判断")
    print("PASS: 卓创休眠；11品种暂停规则CRUD/校验/清理；通道与日志读取；404/422")
    print(f"真实发送沿用第三阶段已验收结论，本次未发送；完整结果：{target}")


if __name__ == "__main__":
    main()
