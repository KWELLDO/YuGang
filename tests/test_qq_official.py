from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from yugang.config import Config, QQConfig
from yugang.gateway.channels import ed25519, qq_official
from yugang.gateway.channels.qq_official import (
    QQOfficialChannel,
    challenge_reply,
    extract_text,
    group_paragraphs,
    plain_text,
    signature_ok,
    split_message,
)
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime

SECRET = "naOC0ocQE3shWLAfffVLB1rhYPG7"
OPENID = "openid-abc123"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """Record the pauses between bubbles instead of actually waiting."""
    pauses: list[int] = []
    monkeypatch.setattr(qq_official, "_pause_between_messages", lambda chars: pauses.append(chars))
    return pauses


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


def _request(
    port: int, method: str = "GET", path: str = "/qq/event"
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connection.request(method, path)
    response = connection.getresponse()
    headers = {key.lower(): value for key, value in response.getheaders()}
    raw = response.read()
    connection.close()
    return response.status, headers, raw


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


def test_a_browser_get_gets_405_json_instead_of_a_501_page(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch)
    try:
        channel.start(lambda incoming: None)
        status, headers, raw = _request(channel.address[1])
        assert status == 405, "opening the callback in a browser is a wrong method, not a server error"
        assert headers["allow"] == "POST"
        assert headers["content-type"].startswith("application/json")
        body = json.loads(raw.decode("utf-8"))
        assert body["error"] == "method not allowed"
        assert "POST" in body["hint"] or "只接收" in body["hint"]
        assert "501" not in raw.decode("utf-8")
    finally:
        channel.stop()
        store.close()


def test_a_head_answers_405_without_a_body(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch)
    try:
        channel.start(lambda incoming: None)
        status, headers, raw = _request(channel.address[1], method="HEAD")
        assert status == 405
        assert raw == b""
        assert int(headers["content-length"]) > 0
    finally:
        channel.stop()
        store.close()


def test_the_only_public_endpoint_does_not_advertise_the_interpreter(tmp_path, monkeypatch):
    channel, store = _channel(tmp_path, monkeypatch)
    try:
        channel.start(lambda incoming: None)
        for method in ("GET", "HEAD"):
            _, headers, _ = _request(channel.address[1], method=method)
            assert headers["server"] == "YuGangQQ/0.1"
            assert "python" not in headers["server"].lower()
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


def test_a_reply_written_in_paragraphs_arrives_as_separate_messages(tmp_path, monkeypatch, _no_waiting):
    server, api_base = _fake_api()
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID], api_base=api_base)
    channel.token_url = f"{api_base}/app/getAppAccessToken"
    try:
        ok = channel.send(
            OPENID,
            "还行，刚醒，脑子有点糊\n\n你上次说的那个锁，还没等到你确认\n\n先放着也行",
            meta={"msg_id": "EVT-1"},
        )
        sends = [call for call in _FakeQQAPI.calls if call["path"].startswith("/v2/users/")]
        assert ok is True
        assert [call["payload"]["content"] for call in sends] == [
            "还行，刚醒，脑子有点糊",
            "你上次说的那个锁，还没等到你确认",
            "先放着也行",
        ]
        # Three thoughts, three bubbles, with a beat between them and none before
        # the first - a pause before the opener is just latency.
        assert len(_no_waiting) == 2
        # The platform tells replies to one message apart by (msg_id, msg_seq).
        # Left unset every bubble is seq 1, so the second one is rejected as a
        # duplicate and only the opening line reaches him.
        assert [call["payload"]["msg_seq"] for call in sends] == [1, 2, 3]
    finally:
        store.close()
        server.shutdown()
        server.server_close()


def test_a_burst_of_paragraphs_is_folded_into_three_bubbles(tmp_path, monkeypatch, _no_waiting):
    server, api_base = _fake_api()
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID], api_base=api_base)
    channel.token_url = f"{api_base}/app/getAppAccessToken"
    try:
        text = "\n\n".join(f"第{index}件事" for index in range(6))
        assert channel.send(OPENID, text, meta={"msg_id": "EVT-1"}) is True
        sends = [call for call in _FakeQQAPI.calls if call["path"].startswith("/v2/users/")]
        assert len(sends) == 3
        assert sends[-1]["payload"]["content"].count("\n\n") == 3
        # Four is the platform ceiling per inbound message; three is ours.
        assert [call["payload"]["msg_seq"] for call in sends] == [1, 2, 3]
    finally:
        store.close()
        server.shutdown()
        server.server_close()


def test_a_rejected_send_keeps_what_the_platform_actually_said(tmp_path, monkeypatch, _no_waiting):
    class _Reject(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path.endswith("/getAppAccessToken"):
                data = json.dumps({"access_token": "tok-1", "expires_in": "7200"}).encode("utf-8")
                self.send_response(200)
            else:
                data = json.dumps({"code": 40054005, "message": "msg_seq 重复"}).encode("utf-8")
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Reject)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    api_base = f"http://127.0.0.1:{server.server_address[1]}"
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID], api_base=api_base)
    channel.token_url = f"{api_base}/getAppAccessToken"
    try:
        assert channel.send(OPENID, "在吗", meta={"msg_id": "EVT-1"}) is False
        # "HTTP Error 400: Bad Request" is where the last investigation stalled:
        # the platform's own error code was sitting in the body, unread.
        assert "400" in channel.last_error
        assert "40054005" in channel.last_error
        assert "msg_seq" in channel.last_error
    finally:
        store.close()
        server.shutdown()
        server.server_close()


def test_group_paragraphs_cuts_a_single_long_paragraph_at_a_sentence():
    assert group_paragraphs("一句话。" * 400, limit=200) != ["一句话。" * 400]
    parts = group_paragraphs("一段\n\n二段\n\n三段", limit=1000)
    assert parts == ["一段", "二段", "三段"]


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
        assert all(call["payload"]["msg_id"] == "EVT-1" for call in sends)
        assert "".join(call["payload"]["content"] for call in sends).endswith("在吗")
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

def test_her_reply_reaches_qq_as_plain_text(tmp_path, monkeypatch):
    server, api_base = _fake_api()
    channel, store = _channel(tmp_path, monkeypatch, allowed=[OPENID], api_base=api_base)
    channel.token_url = f"{api_base}/app/getAppAccessToken"
    try:
        assert channel.send(OPENID, "**四层，从下往上：**\n\n- 一\n- 二\n\n# 标题\n看 `x`", meta={"msg_id": "EVT-1"}) is True
        sends = [call for call in _FakeQQAPI.calls if call["path"].startswith("/v2/users/")]
        content = "".join(call["payload"]["content"] for call in sends)
        assert "**" not in content
        assert "\n- " not in content
        assert "#" not in content
        assert "`" not in content
        assert "四层，从下往上：" in content
        assert "· 一" in content
    finally:
        store.close()
        server.shutdown()
        server.server_close()


def test_split_message_cuts_at_a_boundary_not_in_the_middle_of_a_word():
    text = "甲" * 750 + "\n\n" + "乙" * 400
    chunks = split_message(text, 1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks) == text
    # The paragraph break is inside the limit and past the halfway mark, so it wins.
    assert chunks[0].endswith("\n\n")


def test_plain_text_strips_what_a_person_would_not_type():
    raw = "**判断**\n\n- 一\n- 二\n\n## 小标题\n`code` 和 [链接](https://a.b)\n> 引用"
    text = plain_text(raw)
    assert "**" not in text and "## " not in text
    assert "· 一" in text and "· 二" in text
    assert "判断" in text and "小标题" in text
    assert "code" in text and "链接（https://a.b）" in text
