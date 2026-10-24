from __future__ import annotations

import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from yugang.config import Config, OneBotConfig
from yugang.gateway.channels.onebot import (
    OneBotChannel,
    extract_onebot_text,
    split_message,
    verify_webhook_secret,
)
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime


class _FakeOneBot(BaseHTTPRequestHandler):
    calls: list[dict] = []
    token: str | None = None
    retcode = 0

    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        _FakeOneBot.calls.append(
            {
                "path": self.path,
                "payload": payload,
                "authorization": self.headers.get("Authorization"),
            }
        )
        body = json.dumps({"status": "ok" if _FakeOneBot.retcode == 0 else "failed", "retcode": _FakeOneBot.retcode}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _fake_api() -> tuple[ThreadingHTTPServer, str]:
    _FakeOneBot.calls = []
    _FakeOneBot.retcode = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOneBot)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _post(port: int, body: dict, secret: str | None = None, path: str = "/onebot/event") -> tuple[int, dict]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if secret is not None:
        headers["X-YuGang-Secret"] = secret
    connection.request("POST", path, body=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers)
    response = connection.getresponse()
    payload = json.loads(response.read().decode("utf-8") or "{}")
    connection.close()
    return response.status, payload


def _event(user_id: str = "42", text: str = "你好", message_id: int = 1001) -> dict:
    return {
        "post_type": "message",
        "message_type": "private",
        "self_id": "999",
        "user_id": user_id,
        "message_id": message_id,
        "time": 1770000000,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
    }


def test_extract_onebot_text_handles_segments_and_cq_codes():
    event = {
        "message": [
            {"type": "text", "data": {"text": "你好"}},
            {"type": "image", "data": {}},
            {"type": "face", "data": {}},
            {"type": "text", "data": {"text": " 世界"}},
        ]
    }
    assert extract_onebot_text(event) == "你好[图片][表情] 世界"
    assert extract_onebot_text({"message": "a[CQ:face,id=1]b"}) == "ab"


def test_split_message_keeps_every_character():
    text = "鱼" * 4001
    chunks = split_message(text, 1500)
    assert [len(chunk) for chunk in chunks] == [1500, 1500, 1001]
    assert "".join(chunks) == text


def test_verify_webhook_secret_fails_closed_without_a_secret():
    assert verify_webhook_secret({}, b"{}", "127.0.0.1", None) is True
    assert verify_webhook_secret({}, b"{}", "10.0.0.1", None) is False
    assert verify_webhook_secret({"X-YuGang-Secret": "right"}, b"{}", "10.0.0.1", "right") is True
    assert verify_webhook_secret({"X-YuGang-Secret": "wrong"}, b"{}", "10.0.0.1", "right") is False


def test_onebot_webhook_rejects_bad_secret_and_unauthorized_users(monkeypatch):
    monkeypatch.setenv("TEST_ONEBOT_WEBHOOK_SECRET", "s3cret")
    cfg = OneBotConfig(
        enabled=True,
        webhook_port=0,
        webhook_secret_env="TEST_ONEBOT_WEBHOOK_SECRET",
        allowed_user_ids=["42"],
    )
    channel = OneBotChannel(cfg)
    received = []
    channel.start(received.append)
    port = channel.address[1]
    try:
        status, body = _post(port, _event(), secret="wrong")
        assert status == 403 and body["error"] == "forbidden"
        status, body = _post(port, _event(user_id="99"), secret="s3cret")
        assert status == 200 and body["accepted"] is False and body["reason"] == "user_not_allowed"
        status, body = _post(port, _event(), secret="s3cret")
        assert status == 200 and body["accepted"] is True and body["reason"] == "accepted"
        assert received and received[0].channel == "onebot"
        assert received[0].sender == "42"
        assert received[0].content == "你好"
        assert received[0].external_id == "onebot:1001"
    finally:
        channel.stop()


def test_onebot_webhook_refuses_group_and_self_messages(monkeypatch):
    monkeypatch.setenv("TEST_ONEBOT_WEBHOOK_SECRET", "s3cret")
    cfg = OneBotConfig(enabled=True, webhook_port=0, webhook_secret_env="TEST_ONEBOT_WEBHOOK_SECRET", allowed_user_ids=["42"])
    channel = OneBotChannel(cfg)
    assert channel.accept_event({**_event(), "message_type": "group"}) == (False, "not_private")
    assert channel.accept_event({**_event(), "user_id": "999", "self_id": "999"}) == (False, "self_message")


def test_onebot_send_posts_auth_and_splits_long_text(monkeypatch):
    monkeypatch.setenv("TEST_ONEBOT_API_TOKEN", "api-token")
    server, api_base = _fake_api()
    cfg = OneBotConfig(
        enabled=True,
        api_base=api_base,
        access_token_env="TEST_ONEBOT_API_TOKEN",
        allowed_user_ids=["42"],
        max_message_chars=1500,
    )
    channel = OneBotChannel(cfg)
    try:
        assert channel.send("42", "鱼" * 3200) is True
        assert len(_FakeOneBot.calls) == 3
        assert all(call["path"] == "/send_private_msg" for call in _FakeOneBot.calls)
        assert all(call["authorization"] == "Bearer api-token" for call in _FakeOneBot.calls)
        assert all(call["payload"]["user_id"] == 42 for call in _FakeOneBot.calls)
        assert "".join(call["payload"]["message"][0]["data"]["text"] for call in _FakeOneBot.calls) == "鱼" * 3200
        _FakeOneBot.retcode = 100
        assert channel.send("42", "失败") is False
    finally:
        server.shutdown()
        server.server_close()


def test_onebot_round_trip_through_the_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_ONEBOT_WEBHOOK_SECRET", "s3cret")
    server, api_base = _fake_api()
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.http.enabled = False
    cfg.model = "mock"
    cfg.onebot = OneBotConfig(
        enabled=True,
        api_base=api_base,
        webhook_port=0,
        webhook_secret_env="TEST_ONEBOT_WEBHOOK_SECRET",
        allowed_user_ids=["42"],
        max_message_chars=1500,
    )
    store = MemoryStore(cfg.db_path)
    channel = OneBotChannel(cfg.onebot)
    service = GatewayService(
        cfg,
        store,
        MockRuntime(responder=lambda messages, purpose: "收到：" + (messages[-1].get("content", "") if messages else "")),
        channels=[channel],
    )
    service.start()
    try:
        port = channel.address[1]
        status, body = _post(port, _event(text="在吗"), secret="s3cret")
        assert status == 200 and body["accepted"] is True
        deadline = time.time() + 10
        while time.time() < deadline and not _FakeOneBot.calls:
            time.sleep(0.05)
        assert _FakeOneBot.calls, "the gateway never called the OneBot API"
        assert _FakeOneBot.calls[0]["payload"]["message"][0]["data"]["text"] == "收到：在吗"
        assert store.latest_event("wake") is not None
        assert any(event.channel == "onebot" for event in store.events(limit=20))
    finally:
        service.stop()
        store.close()
        server.shutdown()
        server.server_close()


def test_onebot_config_loads_from_json(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "onebot": {
                    "enabled": True,
                    "api_base": "http://127.0.0.1:3000",
                    "webhook_port": 3197,
                    "allowed_user_ids": ["42"],
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(str(path))
    assert cfg.onebot.enabled is True
    assert cfg.onebot.webhook_port == 3197
    assert cfg.onebot.allowed_user_ids == ["42"]