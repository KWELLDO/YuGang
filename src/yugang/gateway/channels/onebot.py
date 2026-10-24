from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.parse import urlsplit

from .base import IncomingMessage


_CQ_CODE = re.compile(r"\[CQ:[^\]]+\]")
_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


def split_message(text: str, limit: int = 1500) -> list[str]:
    """Split a reply into QQ-sized chunks without losing characters."""
    limit = max(100, int(limit))
    text = str(text or "")
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def extract_onebot_text(event: dict[str, Any]) -> str:
    """Turn a OneBot v11 message array (or raw string) into plain text."""
    message = event.get("message")
    raw = event.get("raw_message")
    if isinstance(message, list):
        parts: list[str] = []
        for segment in message:
            if not isinstance(segment, dict):
                continue
            kind = str(segment.get("type") or "")
            data = segment.get("data") if isinstance(segment.get("data"), dict) else {}
            if kind == "text":
                parts.append(str(data.get("text") or ""))
            elif kind == "image":
                parts.append("[图片]")
            elif kind == "face":
                parts.append("[表情]")
            elif kind == "at":
                parts.append("@" + str(data.get("qq") or ""))
            elif kind == "reply":
                continue
            else:
                parts.append(f"[{kind}]")
        text = "".join(parts)
    elif isinstance(message, str):
        text = message
    elif isinstance(raw, str):
        text = raw
    else:
        text = ""
    text = _CQ_CODE.sub("", text).strip()
    return text[:8000]


def _header(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "")
    except Exception:
        return ""


def _constant_time_equal(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return hmac.compare_digest(left, right)


def verify_webhook_secret(
    headers: Any,
    body: bytes,
    remote_addr: str,
    secret: str | None,
) -> bool:
    """Fail closed: loopback without a secret, or a valid secret from anywhere."""
    if not secret:
        return remote_addr in _LOOPBACK
    explicit = _header(headers, "X-YuGang-Secret")
    if _constant_time_equal(explicit, secret):
        return True
    authorization = _header(headers, "Authorization")
    if _constant_time_equal(authorization, f"Bearer {secret}"):
        return True
    signature = _header(headers, "X-Signature")
    if signature:
        value = signature.split("=", 1)[1] if "=" in signature else signature
        for algo in ("sha1", "sha256"):
            digest = hmac.new(secret.encode("utf-8"), body, getattr(hashlib, algo)).hexdigest()
            if _constant_time_equal(value, digest):
                return True
    return False


class _OneBotServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _OneBotHandler(BaseHTTPRequestHandler):
    server_version = "YuGangOneBot/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def channel(self) -> "OneBotChannel":
        return self.server.channel  # type: ignore[attr-defined]

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _remote(self) -> str:
        try:
            return str(self.client_address[0])
        except Exception:
            return ""

    def do_POST(self) -> None:
        if urlsplit(self.path).path not in ("/onebot/event", "/onebot"):
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "invalid content length"})
            return
        if length <= 0 or length > 1_000_000:
            self._json(413, {"error": "event too large"})
            return
        body = self.rfile.read(length)
        if not verify_webhook_secret(self.headers, body, self._remote(), self.channel.webhook_secret):
            self._json(403, {"error": "forbidden"})
            return
        try:
            event = json.loads(body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"error": "invalid JSON"})
            return
        if not isinstance(event, dict):
            self._json(400, {"error": "event must be an object"})
            return
        accepted, reason = self.channel.accept_event(event)
        self._json(200, {"ok": True, "accepted": accepted, "reason": reason})


class OneBotChannel:
    """OneBot v11 private-message bridge, intended for a local NapCat process.

    The bridge is deliberately narrow: private messages only, an explicit user
    allowlist, a loopback webhook by default, and an optional shared secret.
    It never accepts group messages or messages from an empty allowlist.
    """

    name = "onebot"

    def __init__(self, config: Any):
        self.config = config
        self.webhook_host = str(getattr(config, "webhook_host", "127.0.0.1"))
        self.webhook_port = int(getattr(config, "webhook_port", 3197))
        self.api_base = str(getattr(config, "api_base", "http://127.0.0.1:3000")).rstrip("/")
        self.webhook_secret = _env_secret(
            getattr(config, "webhook_secret_env", "YUGANG_ONEBOT_WEBHOOK_SECRET")
        )
        self.access_token = _env_secret(getattr(config, "access_token_env", "YUGANG_ONEBOT_TOKEN"))
        self.allowed_user_ids = {
            str(user).strip() for user in (getattr(config, "allowed_user_ids", None) or []) if str(user).strip()
        }
        self.max_message_chars = int(getattr(config, "max_message_chars", 1500))
        self.request_timeout_seconds = float(getattr(config, "request_timeout_seconds", 30))
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self.service: Any = None
        self._server: _OneBotServer | None = None
        self._thread: threading.Thread | None = None
        self._port = self.webhook_port
        self.last_rejected_reason: str | None = None
        self.received_count = 0

    def bind(self, service: Any) -> None:
        self.service = service

    def start(self, on_message: Callable[[IncomingMessage], Any]) -> None:
        if not self.webhook_secret and self.webhook_host not in _LOOPBACK:
            raise RuntimeError("onebot webhook needs a secret unless bound to loopback")
        self.on_message = on_message
        server = _OneBotServer((self.webhook_host, self.webhook_port), _OneBotHandler)
        server.channel = self  # type: ignore[attr-defined]
        self._server = server
        self._port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, name="yugang-onebot", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def address(self) -> tuple[str, int]:
        return (self.webhook_host, self._port)

    def accept_event(self, event: dict[str, Any]) -> tuple[bool, str]:
        if str(event.get("post_type") or "") != "message":
            return False, "not_a_message"
        if str(event.get("message_type") or "") != "private":
            return False, "not_private"
        user_id = str(event.get("user_id") or "").strip()
        self_id = str(event.get("self_id") or "").strip()
        if not user_id or (self_id and user_id == self_id):
            return False, "self_message"
        if not self.allowed_user_ids:
            self.last_rejected_reason = "allowlist_empty"
            return False, "allowlist_empty"
        if user_id not in self.allowed_user_ids:
            self.last_rejected_reason = "user_not_allowed"
            return False, "user_not_allowed"
        content = extract_onebot_text(event)
        if not content:
            return False, "empty_message"
        message_id = str(event.get("message_id") or "").strip()
        external_id = f"onebot:{message_id}" if message_id else f"onebot:{event.get('time')}:{user_id}"
        incoming = IncomingMessage(
            channel="onebot",
            content=content,
            sender=user_id,
            external_id=external_id,
            meta={
                "message_id": message_id,
                "user_id": user_id,
                "self_id": self_id,
                "onebot_time": event.get("time"),
            },
            wait_for_reply=False,
        )
        if self.on_message is None:
            return False, "channel_not_started"
        try:
            self.on_message(incoming)
        except Exception:
            return False, "handler_error"
        self.received_count += 1
        return True, "accepted"

    def _api(self, action: str, payload: dict[str, Any]) -> bool:
        url = f"{self.api_base}/{action.lstrip('/')}"
        headers = {"Content-Type": "application/json"}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urlrequest.Request(url, data=body, headers=headers, method="POST")
        try:
            with urlrequest.urlopen(request, timeout=self.request_timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except Exception:
            return False
        if not raw.strip():
            return True
        try:
            data = json.loads(raw)
        except ValueError:
            return False
        if not isinstance(data, dict):
            return True
        status = str(data.get("status") or "").lower()
        retcode = data.get("retcode")
        if status and status != "ok":
            return False
        if retcode not in (None, 0):
            return False
        return True

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        if not recipient:
            return False
        try:
            user_id = int(str(recipient))
        except (TypeError, ValueError):
            return False
        chunks = split_message(content, self.max_message_chars)
        if not chunks:
            return False
        for chunk in chunks:
            payload = {
                "user_id": user_id,
                "message": [{"type": "text", "data": {"text": chunk}}],
                "auto_escape": False,
            }
            if not self._api("send_private_msg", payload):
                return False
        return True


def _env_secret(name: str) -> str | None:
    import os

    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None
