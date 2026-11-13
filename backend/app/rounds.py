"""四轮免费源待办与16:30卓创轮；09:00结束仍触发日报。"""
from datetime import datetime, time, timedelta
import json
import logging
import threading
from time import monotonic

from sqlalchemy import select

from .config import COMMODITIES, ALL_FETCH_SLOTS, SCI99_FETCH_SLOT
from .fetch_policy import SCI99_CODES, selected_codes
from .models import FetchBatch
from .service import FetchBusy, run_lock

logger = logging.getLogger(__name__)
round_lock = threading.Lock()


def batch_dict(batch):
    return {"id": batch.id, "run_day": str(batch.run_day), "slot": batch.slot or "手动",
            "status": batch.status, "started_at": batch.started_at.isoformat() + "+08:00",
            "finished_at": batch.finished_at.isoformat() + "+08:00" if batch.finished_at else None,
            "summary": json.loads(batch.summary) if batch.summary else {}}


class RoundRunner:
    def __init__(self, fetcher, notifications):
        self.fetcher, self.notifications = fetcher, notifications
        self.factory, self.clock = fetcher.session_factory, notifications.clock

    def safe(self, action):
        try:
            return action()
        except Exception:
            logger.error("采集通知或日报处理异常，请核对运行与推送日志；下轮继续")

    def alarm(self, event, text):
        return self.safe(lambda: self.notifications.progress(event, "⚠️ 调度告警 · " + text))

    def run(self, slot=None, codes=None):
        scope = selected_codes(codes)
        if slot == f"{SCI99_FETCH_SLOT[0]:02}:{SCI99_FETCH_SLOT[1]:02}":
            scope = [code for code in scope if code in SCI99_CODES]
        if slot is not None and codes is not None:
            raise ValueError("自动轮不接受手动选品类")
        if not round_lock.acquire(blocking=False):
            raise FetchBusy()
        batch_id = None
        try:
            if run_lock.locked():
                raise FetchBusy()
            at = self.clock()
            if slot is not None and slot not in {f"{h:02}:{m:02}" for h, m in ALL_FETCH_SLOTS}:
                raise ValueError("未知抓取轮次")
            with self.factory() as session:
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                existing = session.scalar(select(FetchBatch).where(FetchBatch.run_day == at.date(), FetchBatch.slot == slot)) if slot else None
                if existing:
                    return {"status": "duplicate", "batch_id": existing.id, "results": []}
                batch = FetchBatch(run_day=at.date(), slot=slot, status="running", started_at=at.replace(tzinfo=None))
                session.add(batch)
                session.commit()
                batch_id = batch.id
            number = next((index+1 for index, (h, m) in enumerate(ALL_FETCH_SLOTS) if slot == f"{h:02}:{m:02}"), None)
            label = "卓创收盘轮 16:30" if slot == "16:30" else f"第{number}轮 {slot}" if number else f"手动 {at:%H:%M}"
            reference = f"运行 #{batch_id}"
            self.safe(lambda: self.notifications.progress(f"{batch_id}:start", f"🌅 抓取开始 · {label}，共 {len(scope)} 个品种（{reference}）"))
            started = monotonic()
            try:
                output = self.fetcher.run(slot=slot, **({"codes": codes} if codes is not None else {}))
                results = output["results"]
                status = "partial" if any(row["outcome"] == "failed" for row in results) else "complete"
            except Exception:
                output, results, status = {}, [], "failed"
                self.alarm(f"morning:{at.date()}" if slot == "09:00" else f"{batch_id}:error", f"{label}整体异常（{reference}），未完成品种见完成消息；后续轮次继续")
            outcomes, lines, skipped = [], [], 0
            for item in (item for item in COMMODITIES if item.code in scope):
                if "commodity_results" in output:
                    result = next((row for row in output["commodity_results"] if row["code"] == item.code), None)
                    outcome = (result or {}).get("outcome", "failed")
                    if outcome == "skipped":
                        skipped += 1
                    else:
                        outcomes.append(outcome == "success")
                    detail = (result or {}).get("message", "本轮未取得结果")
                    if outcome == "failed":
                        detail = "失败：" + detail
                    point = next(iter((result or {}).get("prices", [])), None)
                    if point:
                        detail += f" · {point['price']:g} {point['unit']}（报价日期{point['quote_date']}）"
                    lines.append(item.name + "：" + detail)
                    continue
                result = next((row for row in results if row["source"] == item.source), None)
                good = bool(result and result["outcome"] == "success")
                outcomes.append(good)
                if good:
                    point = next((p for p in result.get("prices", []) if p["code"] == item.code), None)
                    detail = "无更新" if not result.get("stored") else f"{point['price']:g} {point['unit']}" if point else "已更新"
                else:
                    detail = "失败：" + ((result or {}).get("message", "本轮未取得结果")[:160])
                lines.append(item.name + "：" + detail)
            sci = next((row for row in results if row["source"] == "sci99"), None)
            if sci and "commodity_results" not in output:
                lines.append("卓创资讯（另计）：" + sci.get("message", sci["status"]))
            elapsed = round(monotonic() - started, 1)
            summary = {"success": sum(outcomes), "failed": len(outcomes)-sum(outcomes), "skipped": skipped, "codes": scope, "seconds": elapsed,
                       "items": lines, "source_results": results}
            with self.factory() as session:
                batch = session.get(FetchBatch, batch_id)
                batch.status, batch.finished_at = status, self.clock().replace(tzinfo=None)
                batch.summary = json.dumps(summary, ensure_ascii=False)
                session.commit()
            complete = f"✅ 抓取完成 · {label}，成功 {sum(outcomes)} / 失败 {len(outcomes)-sum(outcomes)} / 跳过 {skipped}，耗时 {elapsed:g} 秒（{reference}）\n" + "\n".join(lines)
            self.safe(lambda: self.notifications.progress(f"{batch_id}:end", complete))
            if slot == "09:00" and self.clock().date() == at.date():
                self.safe(lambda: self.notifications.daily_digest(abnormal=status != "complete"))
            return output | {"status": status, "batch_id": batch_id, "summary": summary}
        finally:
            round_lock.release()

    def monitor_morning(self):
        at = self.clock()
        # 不是固定日报发送任务：每分钟看09点轮的持久状态；给启动/执行器2分钟宽限。
        if at.time().replace(tzinfo=None) < time(9, 2):
            return
        with self.factory() as session:
            batch = session.scalar(select(FetchBatch).where(FetchBatch.run_day == at.date(), FetchBatch.slot == "09:00"))
            if batch and batch.status == "running" and at.replace(tzinfo=None) - batch.started_at < timedelta(minutes=30):
                return
            abnormal = batch is None or batch.status != "complete"
            alarm_needed = batch is None or batch.status in {"running", "failed"}
        if alarm_needed:
            self.alarm(f"morning:{at.date()}", "09:00轮未运行、异常或超过30分钟未结束，按最近可用数据生成日报")
        self.safe(lambda: self.notifications.daily_digest(abnormal=abnormal))
