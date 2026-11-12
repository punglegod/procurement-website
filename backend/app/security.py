"""持久配额与正文边界；额度检查和领取必须由调用方放在同一个短写事务。"""
from datetime import datetime, timedelta, timezone
import hashlib
import math

from fastapi import HTTPException
from sqlalchemy import delete
from starlette.responses import JSONResponse

from .models import RateLimitBucket

MAX_BODY_BYTES = 16 * 1024
EMAIL_LIMITS = {
    "account": ((5, 600), (20, 86400)),
    "ip": ((10, 600), (100, 86400)),
    "recipient": ((3, 600), (10, 86400)),
    "global": ((50, 600), (500, 86400)),
}


def reserve_limits(session, specs, *, at=None):
    at = at or datetime.now(timezone.utc).replace(tzinfo=None)
    if at.tzinfo is not None:
        at = at.astimezone(timezone.utc).replace(tzinfo=None)
    rows = []
    retry = 0
    # 先完整检查再递增，避免某一个维度被拒绝却耗掉其他维度的额度。
    for scope, subject, maximum, seconds in specs:
        key = hashlib.sha256(f"{scope}\0{subject}\0{seconds}".encode()).hexdigest()
        row = session.get(RateLimitBucket, key)
        if row is not None and row.reset_at > at and row.count >= maximum:
            retry = max(retry, math.ceil((row.reset_at - at).total_seconds()))
        rows.append((row, key, seconds))
    if retry:
        raise HTTPException(429, "操作过于频繁，请稍后重试", headers={"Retry-After": str(max(1, retry))})
    # 持续清理过期来源桶，不保留历史邮箱/IP 的原文。
    session.execute(delete(RateLimitBucket).where(RateLimitBucket.reset_at <= at))
    for row, key, seconds in rows:
        if row is None or row.reset_at <= at:
            session.add(RateLimitBucket(key=key, count=1, reset_at=at + timedelta(seconds=seconds)))
        else:
            row.count += 1


def reserve_email_limits(session, user_id, client_ip, recipient, *, at=None):
    subjects = {"account": str(user_id), "ip": client_ip, "recipient": recipient.casefold(), "global": "all"}
    reserve_limits(session, [("email_" + dimension, subjects[dimension], maximum, seconds)
                            for dimension, windows in EMAIL_LIMITS.items() for maximum, seconds in windows], at=at)


class BodyLimitMiddleware:
    def __init__(self, app, maximum=MAX_BODY_BYTES):
        self.app = app
        self.maximum = maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # 在 JSON 解析、认证与端点处理之前读取受限流；不依赖 Content-Length。
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            size += len(message.get("body", b""))
            if size > self.maximum:
                response = JSONResponse({"detail": "请求正文超过 16 KiB 上限"}, status_code=413,
                                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
                return await response(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
