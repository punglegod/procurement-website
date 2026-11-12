"""真实历史回放：仅写临时 SQLite，使用明确标记的隔离通道，不发送 Telegram。"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time
from decimal import Decimal, ROUND_DOWN
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from selectolax.lexbor import LexborHTMLParser
from sqlalchemy.orm import sessionmaker

from backend.app.adapters.common import normalized, validate_url
from backend.app.adapters.free import FreeAdapter
from backend.app.alerts import AlertService, RuleInput
from backend.app.config import TIMEZONE
from backend.app.db import initialize_database, make_engine
from backend.app.service import FetchService, serialize_price


class ReplaySender:
    def __init__(self):
        self.messages = []

    def send(self, text, settings):
        self.messages.append(text)
        return "replayed", "隔离历史回放，未调用 Telegram", None


def historical_points(download=False):
    folder = ROOT / "data/stage3-history"
    folder.mkdir(exist_ok=True)
    manifest = folder / "manifest.json"
    if download:
        adapter = FreeAdapter()
        with adapter.open_client() as client:
            response = adapter.request(client, "GET", adapter.index_url)
            tree = LexborHTMLParser(response.text)
            urls = set()
            for link in tree.css("a[href]"):
                if re.fullmatch(r"\d{1,2}月\d{1,2}日南海有色（灵通）铝锭价格", normalized(link.text())):
                    urls.add(validate_url(urljoin(adapter.index_url, link.attributes["href"]), ("market.cnal.com",)))
            urls = sorted(urls, reverse=True)[:3]
            assert len(urls) == 3, "至少需要三个真实报价日，无法取得时停止，不造数据"
            records = []
            for url in urls:
                response = adapter.request(client, "GET", url)
                point = adapter.parse(response.text, url)
                filename = f"{point.quote_date.isoformat()}.html"
                (folder/filename).write_text(response.text, encoding="utf-8")
                records.append({"file": filename, "url": url})
            manifest.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    if not manifest.is_file():
        raise RuntimeError("无真实历史缓存，请先使用 --download-history")
    records = json.loads(manifest.read_text(encoding="utf-8"))
    points = [FreeAdapter.parse((folder/row["file"]).read_text(encoding="utf-8"), row["url"]) for row in records]
    return sorted(points, key=lambda p: p.quote_date)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-history", action="store_true", help="克制下载最近三个真实公开报价日，后续复用缓存")
    args = parser.parse_args()
    points = historical_points(args.download_history)
    final, previous = points[-1], points[-2]
    change = abs((final.price-previous.price)/previous.price*100)
    assert change >= Decimal("0.0001"), "最后两日报价无变化，不能据此宣称验证了涨跌幅触发"
    threshold = min(Decimal("0.01"), change.quantize(Decimal("0.0001"), rounding=ROUND_DOWN))
    with TemporaryDirectory(prefix="alerts-replay-") as temporary:
        engine = make_engine(f"sqlite:///{Path(temporary).as_posix()}/replay.sqlite3")
        initialize_database(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        FetchService(factory).store(points, "free")
        sender = ReplaySender()
        settings = {"TELEGRAM_BOT_TOKEN": "isolated-replay", "TELEGRAM_CHAT_ID": "isolated-replay"}
        service = AlertService(factory, sender=sender, settings_reader=lambda: settings)
        conditions = [("daily_change", threshold), ("price_gte", final.price), ("price_lte", final.price),
                      ("daily_change", change.quantize(Decimal("0.0001"))+1),
                      ("price_gte", max(p.price for p in points)+1), ("price_lte", min(p.price for p in points)-1)]
        rules = [service.save(RuleInput(code="alu_a00", kind=kind, threshold=value)) for kind, value in conditions]
        trace = []
        for p in points:
            at = datetime.combine(p.quote_date, time(16), tzinfo=TIMEZONE)
            first = service.run(at=at)
            second = AlertService(factory, sender=sender, settings_reader=lambda: settings).run(at=at)
            for a, b in zip(first["results"], second["results"]):
                assert b["status"] == ("deduplicated" if a["status"] == "replayed" else "skipped")
            trace.append({"day": str(p.quote_date), "first": first["results"], "restart_repeat": second["results"]})
        assert [r["status"] for r in trace[-1]["first"]] == ["replayed"]*3 + ["skipped"]*3
        assert "缺少上一报价日" in trace[0]["first"][0]["detail"]
        at = datetime.combine(final.quote_date, time(16), tzinfo=TIMEZONE)
        with ThreadPoolExecutor(max_workers=4) as pool:
            concurrent = list(pool.map(lambda _: service.run(at=at), range(4)))
        assert all(r["status"] == "deduplicated" for batch in concurrent for r in batch["results"][:3])
        # 同一规则改阈值后，既有额度仍保留。
        service.save(RuleInput(code="alu_a00", kind="price_gte", threshold=final.price), rules[0]["id"])
        assert service.run(at=at)["results"][0]["status"] == "deduplicated"
        output = {"status": "passed", "mode": "isolated_replay_no_telegram", "points": [serialize_price(p) for p in points],
                  "daily_change_absolute_percent": str(change), "rules": rules, "trace": trace,
                  "concurrent_repeats": 4, "isolated_messages": len(sender.messages),
                  "logs": service.logs(page_size=100), "checks": ["涨跌幅与两种到价条件触发（到价含等号）", "未达到阈值不触发", "首日缺前价跳过", "跨日额度重置", "进程重建后同日去重", "四路并发去重", "修改规则不重置额度"]}
        engine.dispose()
    path = ROOT / "data/stage3-replay.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: output[k] for k in ("status", "mode", "points", "daily_change_absolute_percent", "checks", "isolated_messages")}, ensure_ascii=False, indent=2))
    print(f"证据：{path}")


if __name__ == "__main__":
    main()
