from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yugang.config import Config, QQConfig
from yugang.gateway.channels import ed25519
from yugang.gateway.channels.qq_official import (
    QQOfficialChannel,
    challenge_reply,
    extract_text,
    signature_ok,
    split_message,
)
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime

SECRET = "naOC0ocQE3shWLAfffVLB1rhYPG7"
OPENID = "openid-abc123"


class _FakeQQAPI(BaseHTTPRequestHandler):
    calls: list[dict] = []
    access_token = "tok-1"

    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") or "{}"
        payload = json.loads(raw)
        _FakeQQAPI.calls.append(
            {"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization")}
        )
        if self.path.endswith("/getAppAccessToken"):
            body = json.dumps({"access_token": _FakeQQAPI.access_token, "expires_in": "7200"})
        else:
            body = json.dumps({"id": "sent-1"})
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _fake_api() -> tuple[ThreadingHTTPServer, str]:
    _FakeQQAPI.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeQQAPI)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _headers(secret: str, body: bytes, timestamp: str = "1770000000") -> dict[str, str]:
    seed = ed25519.seed_from_secret(secret)
    signature = ed25519.sign(seed, timestamp.encode("utf-8") + body)
    return {
        "Content-Type": "application/json",
        "X-Signature-Ed25519": signature.hex(),
        "X-Signature-Timestamp": timestamp,
    }


def _post(
    port: int,
    event: dict,
    *,
    secret: str = SECRET,
    timestamp: str = "1770000000",
    unsigned: bool = False,
    path: str = "/qq/event",
) -> tuple[int, dict]:
    body = json.dumps(event, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"} if unsigned else _headers(secret, body, timestamp)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request("POST", path, body=body, headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8") or "{}")
    connection.close()
    return response.status, payload


def _message_event(openid: str = OPENID, text: str = "你好", event_id: str = "EVT-1") -> dict:
    return {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "id": event_id,
        "d": {
            "id": event_id,
            "content": text,
            "timestamp": "2026-10-26T01:00:00+08:00",
            "author": {"id": "bot", "user_openid": openid},
        },
    }


def _channel(tmp_path, monkeypatch, *, allowed: list[str] | None = None, api_base: str | None = None) -> tuple[QQOfficialChannel, MemoryStore]:
    monkeypatch.setenv("TEST_QQ_APP_SECRET", SECRET)
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.http.enabled = False
    cfg.model = "mock"
    cfg.qq = QQConfig(
        enabled=True,
        app_id="1903699681",
        app_secret_env="TEST_QQ_APP_SECRET",
        api_base=api_base or "http://127.0.0.1:1",
        webhook_port=0,
        allowed_openids=list(allowed or []),
    )
    store = MemoryStore(cfg.db_path)
    return QQOfficialChannel(cfg.qq), store


def test_signature_verification_accepts_the_platform_headers():
    body = b'{"op":0}'
    assert signature_ok(_headers(SECRET, body), body, SECRET) is True
    assert signature_ok(_headers(SECRET, body), body, "another-secret") is False
    assert signature_ok(_headers(SECRET, body), b'{"op":1}', SECRET) is False
    assert signature_ok({}, body, SECRET) is False


def test_signature_verification_refuses_without_a_secret():
    body = b'{"op":0}'
    headers = _headers(SECRET, body)
    assert signature_ok(headers, body, None) is False
    assert signature_ok(headers, body, "") is False


def test_challenge_reply_signs_event_ts_plus_plain_token():
    reply = challenge_reply(SECRET, "1770000000", "plain-token-1")
    assert reply["plain_token"] == "plain-token-1"
    seed = ed25519.seed_from_secret(SECRET)
    expected = ed25519.sign(seed, b"1770000000plain-token-1").hex()
    assert reply["signature"] == expected
    assert len(reply["signature"]) == 128


def test_extract_text_strips_mentions_and_split_message_respects_the_limit():
    assert extract_text({"content": "<@!1234> 你好 世界"}) == "你好 世界"
    assert extract_text({"content": "没有提及时原样保留"}) == "没有提及时原样保留"
    chunks = split_message("鱼" * 2500, 1000)
    assert [len(chunk) for chunk in chunks] == [1000, 1000, 500]
    assert "".join(chunks) == "鱼" * 2500


def test_the_channel_refuses_to_start_without_a_secret(tmp_path):
    cfg = QQConfig(enabled=True, app_id="1903699681", app_secret_env="TEST_QQ_MISSING_SECRET", webhook_port=0)
    channel = QQOfficialChannel(cfg)
    with pytest.raises(RuntimeError):
        channel.start(lambda incoming: None)


def test_an_unsigned_request_is_refused_with_403(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID])
    received: list = []
    channel.bind(type("S", (), {"store": store})())
    try:
        channel.start(received.append)
        status, body = _post(channel.address[1], _message_event(), unsigned=True)
        assert status == 403
        assert received == []
        assert channel.rejected_signatures == 1
    finally:
        channel.stop()
        store.close()


def test_the_url_registration_challenge_is_answered(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch)
    try:
        channel.start(lambda incoming: None)
        event = {"op": 13, "d": {"plain_token": "tok", "event_ts": "177"}}
        status, body = _post(channel.address[1], event, timestamp="177")
        assert status == 200
        assert body["plain_token"] == "tok"
        assert body["signature"] == challenge_reply(SECRET, "177", "tok")["signature"]
    finally:
        channel.stop()
        store.close()


def test_an_unknown_openid_is_written_to_l1_but_never_answered(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch, allowed=[])
    received: list = []
    channel.bind(type("S", (), {"store": store})())
    try:
        channel.start(received.append)
        status, body = _post(channel.address[1], _message_event(openid="stranger-openid", text="在吗"))
        assert status == 200
        assert body["accepted"] is False
        assert received == [], "an unlisted openid must not reach the model"
        noting = [event for event in store.events(limit=20) if event.kind == "note" and event.source == "system"]
        assert noting, "the openid must be recorded, otherwise it can never be allowed"
        assert noting[0].meta.get("openid") == "stranger-openid"
        assert noting[0].meta.get("untrusted") is True
        assert "stranger-openid" in channel.unknown_openids
    finally:
        channel.stop()
        store.close()


def test_an_allowed_openid_reaches_the_handler_with_its_msg_id(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID])
    received: list = []
    channel.bind(type("S", (), {"store": store})())
    try:
        channel.start(received.append)
        status, body = _post(channel.address[1], _message_event(text="<@!9> 你好"))
        assert status == 200 and body["accepted"] is True
        assert len(received) == 1
        incoming = received[0]
        assert incoming.channel == "qq"
        assert incoming.content == "你好"
        assert incoming.sender == OPENID
        assert incoming.meta["msg_id"] == "EVT-1"
        assert channel.received_count == 1
    finally:
        channel.stop()
        store.close()


def test_a_replayed_event_id_is_not_answered_twice(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID])
    received: list = []
    channel.bind(type("S", (), {"store": store})())
    try:
        channel.start(received.append)
        first = _post(channel.address[1], _message_event(event_id="EVT-9"))
        second = _post(channel.address[1], _message_event(event_id="EVT-9"))
        assert first[1]["accepted"] is True
        assert second[1].get("duplicate") == "EVT-9"
        assert len(received) == 1
    finally:
        channel.stop()
        store.close()


def test_group_and_unknown_events_are_ignored(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID])
    received: list = []
    channel.bind(type("S", (), {"store": store})())
    try:
        channel.start(received.append)
        group = {"op": 0, "t": "GROUP_AT_MESSAGE_CREATE", "d": {"id": "g1", "content": "hi"}}
        status, body = _post(channel.address[1], group)
        assert status == 200 and body["ignored"] == "GROUP_AT_MESSAGE_CREATE"
        status, body = _post(channel.address[1], {"op": 12, "d": {}})
        assert status == 200 and body["ok"] is True
        assert received == []
    finally:
        channel.stop()
        store.close()


def test_send_posts_a_passive_reply_with_the_access_token(tmp_path, monkeypatch):
    server, api_base = _fake_api()
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID], api_base=api_base)
    channel.token_url = f"{api_base}/app/getAppAccessToken"
    channel.max_message_chars = 1000
    try:
        assert channel.send(OPENID, "鱼" * 1200, meta={"msg_id": "EVT-1"}) is True
        sends = [call for call in _FakeQQAPI.calls if call["path"].startswith("/v2/users/")]
        assert len(sends) == 2
        assert all(call["path"] == f"/v2/users/{OPENID}/messages" for call in sends)
        assert all(call["authorization"] == "QQBot tok-1" for call in sends)
        assert all(call["payload"]["msg_type"] == 0 for call in sends)
        assert all(call["payload"]["msg_id"] == "EVT-1" for call in sends)
        assert "".join(call["payload"]["content"] for call in sends) == "鱼" * 1200
    finally:
        store.close()
        server.shutdown()
        server.server_close()


def test_a_full_round_trip_delivers_her_reply_to_the_openid(tmp_path, monkeypatch):
    server, api_base = _fake_api()
    monkeypatch.setenv("TEST_QQ_APP_SECRET", SECRET)
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.http.enabled = False
    cfg.model = "mock"
    cfg.qq = QQConfig(
        enabled=True,
        app_id="1903699681",
        app_secret_env="TEST_QQ_APP_SECRET",
        api_base=api_base,
        token_url=f"{api_base}/app/getAppAccessToken",
        webhook_port=0,
        allowed_openids=[OPENID],
    )
    store = MemoryStore(cfg.db_path)
    channel = QQOfficialChannel(cfg.qq)
    service = GatewayService(
        cfg,
        store,
        MockRuntime(
            responder=lambda messages, purpose: "收到：" + (messages[-1].get("content", "") if messages else "")
        ),
        channels=[channel],
    )
    service.start()
    try:
        status, body = _post(channel.address[1], _message_event(text="在吗"))
        assert status == 200 and body["accepted"] is True
        deadline = time.time() + 10
        while time.time() < deadline and not [c for c in _FakeQQAPI.calls if c["path"].startswith("/v2/users/")]:
            time.sleep(0.05)
        sends = [c for c in _FakeQQAPI.calls if c["path"].startswith("/v2/users/")]
        assert sends, "the reply never reached the QQ API"
        # Passive reply: without msg_id the platform may refuse it as proactive.
        assert sends[0]["payload"]["msg_id"] == "EVT-1"
        assert sends[0]["payload"]["content"].endswith("在吗")
        assert store.latest_event("wake") is not None
    finally:
        service.stop()
        store.close()
        server.shutdown()
        server.server_close()


def test_qq_config_loads_from_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "qq": {
                    "enabled": True,
                    "app_id": "1903699681",
                    "webhook_port": 3196,
                    "allowed_openids": [OPENID],
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(str(path))
    assert cfg.qq.enabled is True
    assert cfg.qq.app_id == "1903699681"
    assert cfg.qq.allowed_openids == [OPENID]
