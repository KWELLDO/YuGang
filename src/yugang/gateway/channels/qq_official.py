"""QQ official bot platform (bot.q.qq.com) over a public webhook.

This is not the OneBot bridge. The platform signs every callback with Ed25519
using the bot secret and addresses a person by **openid**, never by QQ number;
there is no QQ-number-to-openid lookup, so the only way to learn an openid is to
receive one. That asymmetry shapes this whole file:

* An openid that is not in `allowed_openids` gets no reply and no model call.
  Its message is written to L1 as an untrusted note together with the openid,
  because knowing the openid is the one thing needed to allow that person later.
* The webhook refuses to start without a secret. An unauthenticated public
  endpoint that answers as her would be worse than no IM channel at all.
* The platform retries anything it cannot deliver inside a few seconds, so the
  handler verifies, enqueues, and answers 200 without waiting for a model call.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib import request as urlrequest
from urllib.parse import urlsplit

from . import ed25519
from .base import IncomingMessage

_LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}
_OP_DISPATCH = 0
_OP_CHALLENGE = 13
_SEEN_LIMIT = 500


def _header(headers: Any, name: str) -> str:
    try:
        return str(headers.get(name) or "")
    except Exception:
        return ""


_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_HEADING = re.compile(r"(?m)^[ \t]{0,3}#{1,6}[ \t]*")
_BULLET = re.compile(r"(?m)^([ \t]*)[-*+][ \t]+")
_QUOTE = re.compile(r"(?m)^[ \t]{0,3}>[ \t]?")
_FENCE = re.compile(r"(?m)^[ \t]*```[^\n]*$")
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def plain_text(text: str) -> str:
    """Turn Markdown into what a person would have typed.

    QQ's plain-text message type shows the raw bytes: a reply that says
    **四层，从下往上：** reaches the reader as a row of asterisks, which reads
    like a machine leaking its source. The prompt asks her not to format; this
    is the net that catches the cases where she does it anyway.
    """
    text = str(text or "")
    text = _FENCE.sub("", text)
    text = _LINK.sub(lambda m: f"{m.group(1)}（{m.group(2)}）", text)
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", text)
    text = _ITALIC.sub(lambda m: m.group(1), text)
    text = _INLINE_CODE.sub(lambda m: m.group(1), text)
    text = _HEADING.sub("", text)
    text = _BULLET.sub(lambda m: m.group(1) + "· ", text)
    text = _QUOTE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_message(text: str, limit: int = 1000) -> list[str]:
    """Split at a paragraph, then a line, then a sentence - never mid-word if we can help it.

    The platform caps one message at a few thousand characters. Cutting at a fixed
    offset turns a long answer into fragments that start mid-sentence, which is
    exactly how a broken client looks.
    """
    limit = max(100, int(limit))
    text = str(text or "")
    if not text:
        return []
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = len(window)
        for separator in ("\n\n", "\n", "。", "！", "？", "；"):
            index = window.rfind(separator)
            if index >= limit // 2:
                cut = index + len(separator)
                break
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


def signature_ok(headers: Any, body: bytes, secret: str | None) -> bool:
    """Verify X-Signature-Ed25519 over timestamp+body, or refuse.

    The platform signs `X-Signature-Timestamp + body` with the seed derived from
    the bot secret; a missing secret means every request is refused.
    """
    if not secret:
        return False
    signature = _header(headers, "X-Signature-Ed25519").strip()
    timestamp = _header(headers, "X-Signature-Timestamp").strip()
    if not signature or not timestamp:
        return False
    try:
        raw = bytes.fromhex(signature)
    except ValueError:
        return False
    if len(raw) != 64 or raw[63] & 224:
        return False
    try:
        seed = ed25519.seed_from_secret(secret)
        public = ed25519.public_key(seed)
    except ValueError:
        return False
    return ed25519.verify(public, timestamp.encode("utf-8") + body, raw)


def challenge_reply(secret: str, event_ts: str, plain_token: str) -> dict[str, str]:
    """Answer the platform's URL-registration challenge (op 13)."""
    seed = ed25519.seed_from_secret(secret)
    signature = ed25519.sign(seed, f"{event_ts}{plain_token}".encode("utf-8"))
    return {"plain_token": plain_token, "signature": signature.hex()}


def extract_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("content") or "")
    # Mentions arrive inline as <@!123456>; she is the one being addressed.
    while True:
        start = text.find("<@")
        if start < 0:
            break
        end = text.find(">", start)
        if end < 0:
            text = text[:start]
            break
        text = text[:start] + text[end + 1 :]
    return text.strip()[:8000]


class _QQServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _QQHandler(BaseHTTPRequestHandler):
    server_version = "YuGangQQ/0.1"

    def version_string(self) -> str:
        # The stock implementation answers with "YuGangQQ/0.1 Python/3.13.5", and this
        # handler is the only part of her that answers on the public internet.
        return self.server_version

    def log_message(self, format: str, *args: Any) -> None:
        return

    @property
    def channel(self) -> "QQOfficialChannel":
        return self.server.channel  # type: ignore[attr-defined]

    def _json(self, status: int, data: dict[str, Any], *, head_only: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if status == 405:
            self.send_header("Allow", "POST")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        # Someone will always open the callback in a browser; the stock http.server
        # answer to that is an HTML 501 page that advertises the method handling and
        # says nothing about what this address is actually for.
        self._json(
            405,
            {
                "error": "method not allowed",
                "hint": "这个地址只接收带签名的 POST（QQ 平台回调），用浏览器打开它不会发生任何事",
            },
        )

    def do_HEAD(self) -> None:  # noqa: N802 - http.server API
        self._json(405, {"error": "method not allowed", "hint": "POST only"}, head_only=True)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        if urlsplit(self.path).path not in ("/qq/event", "/qq"):
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
        if not signature_ok(self.headers, body, self.channel.app_secret):
            self.channel.note_rejection()
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
        status, payload = self.channel.accept_event(event)
        self._json(status, payload)


class QQOfficialChannel:
    """Webhook bridge for the official QQ bot platform."""

    name = "qq"

    def __init__(self, config: Any):
        self.config = config
        self.app_id = str(getattr(config, "app_id", "") or "")
        self.webhook_host = str(getattr(config, "webhook_host", "127.0.0.1"))
        self.webhook_port = int(getattr(config, "webhook_port", 3196))
        self.api_base = str(getattr(config, "api_base", "https://api.sgroup.qq.com")).rstrip("/")
        self.token_url = str(getattr(config, "token_url", "https://bots.qq.com/app/getAppAccessToken"))
        self.max_message_chars = int(getattr(config, "max_message_chars", 1000))
        self.request_timeout_seconds = float(getattr(config, "request_timeout_seconds", 30))
        self.allowed_openids = {
            str(item).strip() for item in (getattr(config, "allowed_openids", None) or []) if str(item).strip()
        }
        self.app_secret: str | None = None
        self.on_message: Callable[[IncomingMessage], Any] | None = None
        self.service: Any = None
        self._server: _QQServer | None = None
        self._thread: threading.Thread | None = None
        self._port = self.webhook_port
        self._seen: deque[str] = deque(maxlen=_SEEN_LIMIT)
        self._seen_set: set[str] = set()
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        self.received_count = 0
        self.rejected_signatures = 0
        self.unknown_openids: list[str] = []
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    def bind(self, service: Any) -> None:
        self.service = service

    def start(self, on_message: Callable[[IncomingMessage], Any]) -> None:
        self.app_secret = self._secret()
        if not self.app_secret:
            raise RuntimeError("qq webhook needs the bot secret (YUGANG_QQ_APP_SECRET)")
        if not self.app_id:
            raise RuntimeError("qq webhook needs app_id")
        self.on_message = on_message
        server = _QQServer((self.webhook_host, self.webhook_port), _QQHandler)
        server.channel = self  # type: ignore[attr-defined]
        self._server = server
        self._port = int(server.server_address[1])
        self._thread = threading.Thread(target=server.serve_forever, name="yugang-qq", daemon=True)
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

    def health(self) -> dict[str, Any]:
        return {
            "healthy": self._thread is not None and self._thread.is_alive(),
            "received": self.received_count,
            "rejected_signatures": self.rejected_signatures,
            "allowlist": sorted(self.allowed_openids),
            "unknown_openids": self.unknown_openids[-5:],
            "last_error": self.last_error,
        }

    def _secret(self) -> str | None:
        getter = getattr(self.config, "qq_app_secret", None)
        if callable(getter):
            return getter()
        import os

        env = str(getattr(self.config, "app_secret_env", "YUGANG_QQ_APP_SECRET"))
        return os.environ.get(env)

    def note_rejection(self) -> None:
        self.rejected_signatures += 1
        self.last_error = "签名验证失败"

    # ---------------------------------------------------------------- inbound
    def accept_event(self, event: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Answer the platform. Returns (http status, body)."""
        op = event.get("op")
        data = event.get("d") if isinstance(event.get("d"), dict) else {}
        if op == _OP_CHALLENGE:
            plain_token = str(data.get("plain_token") or "")
            event_ts = str(data.get("event_ts") or "")
            if not plain_token or not event_ts or not self.app_secret:
                return 400, {"error": "bad challenge"}
            return 200, challenge_reply(self.app_secret, event_ts, plain_token)
        if op != _OP_DISPATCH:
            return 200, {"ok": True, "ignored": f"op {op}"}
        kind = str(event.get("t") or "")
        if kind != "C2C_MESSAGE_CREATE":
            # Group messages, DMs with other bots, guild traffic: not her channel.
            return 200, {"ok": True, "ignored": kind or "unknown"}
        event_id = str(data.get("id") or "")
        if event_id and event_id in self._seen_set:
            return 200, {"ok": True, "duplicate": event_id}
        author = data.get("author") if isinstance(data.get("author"), dict) else {}
        openid = str(author.get("user_openid") or author.get("id") or "").strip()
        content = extract_text(data)
        if event_id:
            self._remember(event_id)
        if not openid or not content:
            return 200, {"ok": True, "ignored": "empty"}
        if openid not in self.allowed_openids:
            self._record_unknown(openid, content, event_id)
            return 200, {"ok": True, "accepted": False, "reason": "openid_not_allowed"}
        if self.on_message is None:
            return 200, {"ok": True, "accepted": False, "reason": "channel_not_started"}
        incoming = IncomingMessage(
            channel="qq",
            content=content,
            sender=openid,
            external_id=f"qq:{event_id}" if event_id else None,
            meta={"openid": openid, "msg_id": event_id, "qq_timestamp": data.get("timestamp")},
            wait_for_reply=False,
        )
        try:
            self.on_message(incoming)
        except Exception as exc:  # noqa: BLE001 - the platform must still get a 200
            self.last_error = f"{type(exc).__name__}: {exc}"
            return 200, {"ok": False, "error": "handler_error"}
        self.received_count += 1
        return 200, {"ok": True, "accepted": True}

    def _remember(self, event_id: str) -> None:
        if event_id in self._seen_set:
            return
        if len(self._seen) == self._seen.maxlen:
            dropped = self._seen[0]
            self._seen_set.discard(dropped)
        self._seen.append(event_id)
        self._seen_set.add(event_id)

    def _record_unknown(self, openid: str, content: str, event_id: str) -> None:
        """Write the openid down instead of answering a stranger.

        Not replying is the fail-closed choice; writing it is what lets the
        operator allow this person afterwards, since the platform offers no
        lookup from a QQ number to an openid.
        """
        if openid not in self.unknown_openids:
            self.unknown_openids.append(openid)
        store = getattr(self.service, "store", None)
        if store is None:
            return
        try:
            store.append_event(
                "system",
                "note",
                f"收到一个未授权的 QQ openid 的消息，没有回复：{content[:200]}",
                meta={
                    "untrusted": True,
                    "origin": "qq",
                    "openid": openid,
                    "qq_event_id": event_id,
                    "note": "把这个 openid 写进 qq.allowed_openids 之后，她才会看到并回复。",
                },
            )
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"{type(exc).__name__}: {exc}"

    # --------------------------------------------------------------- outbound
    def _access_token(self) -> str | None:
        with self._token_lock:
            if self._token and time.monotonic() < self._token_expires_at:
                return self._token
            secret = self.app_secret or self._secret()
            if not secret or not self.app_id:
                return None
            payload = json.dumps({"appId": self.app_id, "clientSecret": secret}).encode("utf-8")
            request = urlrequest.Request(
                self.token_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(request, timeout=self.request_timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8") or "{}")
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"取 access_token 失败：{type(exc).__name__}: {exc}"
                return None
            token = str(data.get("access_token") or "")
            if not token:
                self.last_error = f"access_token 为空：{data}"
                return None
            try:
                expires_in = int(data.get("expires_in") or 7200)
            except (TypeError, ValueError):
                expires_in = 7200
            self._token = token
            # Refresh a minute early: a token that expires mid-send is a lost reply.
            self._token_expires_at = time.monotonic() + max(60, expires_in - 60)
            return token

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        openid = str(recipient or "").strip()
        if not openid:
            return False
        chunks = split_message(plain_text(content), self.max_message_chars)
        if not chunks:
            return False
        token = self._access_token()
        if not token:
            return False
        msg_id = str((meta or {}).get("msg_id") or "").strip()
        ok = True
        for chunk in chunks:
            payload: dict[str, Any] = {"content": chunk, "msg_type": 0}
            if msg_id:
                # Passive replies reference the inbound message; the platform
                # counts a reply without msg_id as a proactive message.
                payload["msg_id"] = msg_id
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request = urlrequest.Request(
                f"{self.api_base}/v2/users/{openid}/messages",
                data=body,
                headers={"Content-Type": "application/json", "Authorization": f"QQBot {token}"},
                method="POST",
            )
            try:
                with urlrequest.urlopen(request, timeout=self.request_timeout_seconds) as response:
                    response.read()
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"发送失败：{type(exc).__name__}: {exc}"
                ok = False
                break
        return ok
