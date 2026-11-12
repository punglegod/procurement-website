import argparse
import json
from pathlib import Path

import httpx
from smoke_auth import add_auth_arguments, login


def main():
    parser = argparse.ArgumentParser(description="第一阶段真实服务验收；加 --fetch 才触发网络抓取")
    add_auth_arguments(parser)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()
    results = {}
    with httpx.Client(base_url=args.base_url, trust_env=False, timeout=180) as client:
        login(client, args)
        def get(path):
            response = client.get(path)
            response.raise_for_status()
            return response.json()

        results["health"] = get("/api/health")
        assert results["health"] == {"status": "ok"}
        catalog = get("/api/commodities")["items"]
        assert len(catalog) == 11
        assert all(item["enabled"] and item["source"] != "sci99" for item in catalog)
        results["commodity_count"] = len(catalog)
        if args.fetch:
            response = client.post("/api/fetch/run")
            response.raise_for_status()
            results["fetch"] = response.json()["results"]
            assert results["fetch"][0]["outcome"] == "success", results["fetch"]
        latest = get("/api/prices/latest")["items"]
        aluminum = next(item for item in latest if item["code"] == "alu_a00")
        assert aluminum["price_point"], "A00 尚无真实行情"
        assert aluminum["price_point"]["price"] > 0
        results["alu_a00"] = aluminum
        history = get("/api/prices/history?code=alu_a00&days=30")
        assert any(point["quote_date"] == aluminum["price_point"]["quote_date"] for point in history["items"])
        results["history_count"] = len(history["items"])
        results["source_status"] = get("/api/sources/status")["items"]
        assert client.get("/api/prices/history?code=missing&days=30").status_code == 404
        assert client.get("/api/prices/history?code=alu_a00&days=0").status_code == 422
        results["invalid_parameters"] = {"unknown_code": 404, "days_zero": 422}
    output = Path(args.output or ("data/selftest.json" if args.fetch else "data/selftest-readonly.json"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print("PASS: 第一阶段服务验收")


if __name__ == "__main__":
    main()
