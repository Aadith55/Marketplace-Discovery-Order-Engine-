"""Small ASGI request-body limit for the demo API."""
from __future__ import annotations
import json
from typing import Any, Awaitable, Callable
ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]

class RequestSizeLimitMiddleware:
    """Reject oversized HTTP request bodies before JSON parsing/buffering by FastAPI."""
    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes
    async def _respond(self, send, status: int, detail: str, http_version: str = "1.1") -> None:
        body = json.dumps({"detail": detail}).encode("utf-8")
        headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode("ascii"))]
        if http_version not in {"2", "3"}:
            headers.append((b"connection", b"close"))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})
    async def _too_large(self, send, http_version: str = "1.1") -> None:
        await self._respond(send, 413, f"Request body exceeds the {self.max_bytes}-byte limit", http_version)
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        http_version = scope.get("http_version", "1.1")
        content_length = None
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    content_length = int(value)
                except ValueError:
                    await self._respond(send, 400, "Invalid Content-Length header", http_version)
                    return
                break
        if content_length is not None and content_length < 0:
            await self._respond(send, 400, "Invalid Content-Length header", http_version)
            return
        if content_length is not None and content_length > self.max_bytes:
            await self._too_large(send, http_version)
            return
        chunks: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                await self._too_large(send, http_version)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        chunks.clear()
        sent = False
        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()
        await self.app(scope, replay_receive, send)
