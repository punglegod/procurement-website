"""直接驱动分块 ASGI 流，确保正文限制不依赖 Content-Length。"""
import asyncio
import pytest

from backend.app.security import BodyLimitMiddleware, MAX_BODY_BYTES


@pytest.mark.parametrize("headers", [[], [(b"transfer-encoding", b"chunked")], [(b"content-length", b"1")]])
def test_counted_stream_stops_before_app_even_with_missing_or_false_length(headers):
    calls, output, reads = [], [], []
    chunks = [{"type": "http.request", "body": b"x" * 8192, "more_body": True} for _ in range(3)]
    chunks.append({"type": "http.request", "body": b"never read", "more_body": False})

    async def receive():
        reads.append(True)
        return chunks.pop(0)

    async def send(message):
        output.append(message)

    async def downstream(scope, receive, send):
        calls.append(True)

    asyncio.run(BodyLimitMiddleware(downstream)({"type": "http", "method": "POST", "headers": headers}, receive, send))
    assert not calls and output[0]["status"] == 413 and len(reads) == 3


def test_exact_body_boundary_replayed_without_loss():
    body = b"x" * MAX_BODY_BYTES
    received = []
    chunks = [{"type": "http.request", "body": body[:3000], "more_body": True},
              {"type": "http.request", "body": body[3000:], "more_body": False}]

    async def receive():
        return chunks.pop(0)

    async def downstream(scope, receive, send):
        received.append(await receive())

    asyncio.run(BodyLimitMiddleware(downstream)({"type": "http"}, receive, None))
    assert received == [{"type": "http.request", "body": body, "more_body": False}]
