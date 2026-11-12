"""逐品种真实采集、入库回读并追加续跑记录；不触发推送。"""

import argparse
from importlib import import_module
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.adapters.common import now
from backend.app.config import COMMODITY_MAP, ROOT, SOURCES
from backend.app.db import initialize_database
from backend.app.service import FetchService, latest_prices, serialize_price


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("code", choices=list(COMMODITY_MAP))
    args = parser.parse_args()
    item = COMMODITY_MAP[args.code]
    config = SOURCES[item.source]
    module, name = config["adapter"].split(":")
    adapter = getattr(import_module(f"backend.app.adapters.{module}"), name)()
    evidence = ROOT / "data/stage4-evidence"
    evidence.mkdir(exist_ok=True)

    def capture(response):
        response.read()
        # 只缓存当前公开源响应，便于复核；不缓存或请求卓创、Telegram。
        import hashlib
        key = hashlib.sha256(str(response.request.url).encode()).hexdigest()[:16]
        (evidence / f"{args.code}-{key}.html").write_bytes(response.content)

    with adapter.open_client() as client:
        client.event_hooks["response"].append(capture)
        adapter.client = client
        points = adapter.fetch()
    initialize_database()
    service = FetchService()
    service.store(points, item.source)
    service.set_state(item.source, "ok", f"真实验证：入库 {len(points)} 个价格点", success=True)
    row = next(r for r in latest_prices() if r["code"] == item.code)
    point = serialize_price(points[0])
    assert row["price_point"]["price"] == point["price"]
    (evidence / f"{item.code}.json").write_text(json.dumps(point, ensure_ascii=False, indent=2), encoding="utf-8")
    # 元数据可提交；不把本地数据库或上游整篇文章加入仓库。
    record = ROOT / f"backend/tests/evidence/{item.code}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(point, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (ROOT / "STAGE4_PROGRESS.md").open("a", encoding="utf-8") as out:
        out.write(f"\n## {item.code} — {now().isoformat(timespec='seconds')}\n\n"
                  f"- 状态：已验证真实 fetch → SQLite 入库 → latest 回读；不发送推送。\n"
                  f"- 来源：{point['source_name']}，{point['source_url']}\n"
                  f"- 固定口径：{point['quote_basis']}。\n"
                  f"- 示例价格：{point['price']} {point['unit']}；报价日期：{point['quote_date']}；抓取时间：{point['fetched_at']}。\n"
                  f"- 证据：`backend/tests/evidence/{item.code}.json`；提交结果见 STAGE4_REPORT.md。\n")
    print(json.dumps(point, ensure_ascii=False))


if __name__ == "__main__":
    main()
