from __future__ import annotations

import json
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .base import IncomingMessage


class _Handler(BaseHTTPRequestHandler):
    server_version = "YuGangGateway/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the gateway quiet; errors still go through send_json.
        return

    @property
    def channel(self) -> "HttpChannel":
        return self.server.channel  # type: ignore[attr-defined]

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        token = self.channel.token
        if not token:
            return True
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {token}"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "awake": self.channel.service.status().get("awake", False)})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        if self.path == "/status":
            self._json(200, self.channel.service.status())
            return
        if self.path == "/tank":
            self._json(200, self.channel.service.tank())
            return
        if self.path == "/replies":
            self._json(200, {"replies": list(self.channel.replies)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/message":
            self._json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw or "{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return
        content = str(payload.get("content") or "").strip()
        if not content:
            self._json(400, {"error": "content is required"})
            return
        if len(content) > 8000:
            self._json(413, {"error": "content too long"})
            return
        incoming = IncomingMessage(
            channel="http",
            content=content,
            sender=str(payload.get("sender") or "web")[:200],
            external_id=str(payload.get("id"))[:200] if payload.get("id") else None,
            wait_for_reply=True,
        )
        handled = self.channel.on_message(incoming)
        if handled is not None:
            incoming.message_id = getattr(handled, "id", None)
        ok = incoming.wait()
        self._json(
            200 if ok and not incoming.error else 503,
            {
                "message_id": incoming.message_id,
                "reply": incoming.reply,
                "error": incoming.error,
                "timed_out": not ok,
            },
        )


class HttpChannel:
    name = "http"

    def __init__(self, host: str, port: int, *, token: str | None = None):
        self.host = host
        self.port = port
        self.token = token
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self.service: Any = None
        self.replies: deque[dict[str, Any]] = deque(maxlen=50)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def bind(self, service: Any) -> None:
        self.service = service

    def start(self, on_message: Callable[[IncomingMessage], Any]) -> None:
        self.on_message = on_message
        server = ThreadingHTTPServer((self.host, self.port), _Handler)
        server.daemon_threads = True
        server.channel = self  # type: ignore[attr-defined]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, name="yugang-http", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        self.replies.append({"to": recipient, "content": content, "meta": meta or {}})
        return True

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)
