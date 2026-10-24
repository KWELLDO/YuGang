from __future__ import annotations

import http.client
import json
import time

import pytest

from yugang.config import Config
from yugang.gateway.channels.http import HttpChannel
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime


@pytest.fixture
def running(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    cfg.daily_budget_cny = 5.0
    store = MemoryStore(cfg.db_path)
    runtime = MockRuntime(responder=lambda messages, purpose: "echo: " + (messages[-1].get("content", "")[:70] if messages else ""))
    service = GatewayService(cfg, store, runtime)
    service.start()
    yield service, store, cfg
    service.stop()
    store.close()


def test_chat_wakes_records_reply_and_sleeps(running):
    service, store, _cfg = running
    reply = service.chat("你好，我是 kwelldo。", timeout=10)
    assert reply and "echo:" in reply
    assert service.window.is_awake() is True
    assert store.latest_event("wake") is not None
    replies = [e for e in store.events(limit=20) if e.kind == "reply"]
    assert replies
    applied = service.sleep_window(reason="测试结束")
    assert applied is not None
    assert applied["diary_event"]
    assert service.window.is_awake() is False
    assert store.latest_event("sleep") is not None


def test_budget_exhaustion_fails_loud_and_keeps_message(running):
    service, store, cfg = running
    cfg.daily_budget_cny = 0.000001
    store.record_budget(service.budget.day(), "chat", 0.000002, model="deepseek-chat")
    reply = service.chat("这条消息不能被静默吞掉", timeout=10)
    assert reply is not None
    assert "暂时醒不来" in reply
    pending = store.pending_messages()
    assert any(m.content == "这条消息不能被静默吞掉" and m.status == "failed" for m in pending)
    errors = [e for e in store.events(limit=20) if e.kind == "error"]
    assert errors


def test_model_failure_fails_loud(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)
    service = GatewayService(cfg, store, MockRuntime(fail=RuntimeError("boom")))
    service.start()
    try:
        reply = service.chat("模型会挂吗", timeout=10)
        assert reply is not None
        assert "模型调用失败" in reply
        failed = [m for m in store.pending_messages() if m.status == "failed"]
        assert failed
    finally:
        service.stop()
        store.close()


def test_http_channel_auth_health_and_synchronous_reply(running):
    service, _store, _cfg = running
    channel = HttpChannel("127.0.0.1", 0, token="s3cret")
    channel.bind(service)
    channel.start(service.handle_incoming)
    port = channel._server.server_address[1]
    try:
        assert _request(port, "GET", "/health")[0] == 200
        assert _request(port, "GET", "/status")[0] == 401
        assert _request(port, "POST", "/message", {"content": "你好"})[0] == 401

        status, payload = _request(
            port,
            "POST",
            "/message",
            {"content": "你好，HTTP", "id": "http-1"},
            token="s3cret",
        )
        assert status == 200
        assert payload["reply"] and "echo:" in payload["reply"]
        assert payload["message_id"]

        status2, payload2 = _request(
            port,
            "POST",
            "/message",
            {"content": "你好，HTTP", "id": "http-1"},
            token="s3cret",
        )
        assert status2 == 200
        assert payload2["message_id"] == payload["message_id"]
        assert payload2["reply"] == payload["reply"]

        status3, status_payload = _request(port, "GET", "/status", token="s3cret")
        assert status3 == 200
        assert status_payload["channels"] == []
    finally:
        channel.stop()


def _request(port, method, path, body=None, token=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers = {}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=data, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw.decode("utf-8"))

def test_fish_tank_page_requires_token_and_speaks_html(running):
    service, _store, _cfg = running
    channel = HttpChannel("127.0.0.1", 0, token="s3cret")
    channel.bind(service)
    channel.start(service.handle_incoming)
    port = channel._server.server_address[1]
    try:
        status, headers, body = _request_raw(port, "GET", "/")
        assert status == 401
        assert "text/html" in headers["Content-Type"]

        status, headers, body = _request_raw(port, "GET", "/?token=s3cret")
        assert status == 200
        assert "text/html" in headers["Content-Type"]
        assert "鱼缸" in body and "/message" in body

        status, headers, _body = _request_raw(
            port, "GET", "/", headers={"Authorization": "Bearer s3cret"}
        )
        assert status == 200
        assert "text/html" in headers["Content-Type"]
    finally:
        channel.stop()


def _request_raw(port, method, path, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    raw = response.read().decode("utf-8")
    response_headers = {k: v for k, v in response.getheaders()}
    connection.close()
    return response.status, response_headers, raw

def test_startup_recovers_message_left_processing(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)
    message, _event = store.receive_message(
        "http", "上次崩溃时没回的消息", sender="kwelldo", external_id="crash-1"
    )
    store.mark_message(message.id, "processing")

    service = GatewayService(cfg, store, MockRuntime(responder=lambda m, p: "echo: recovered"))
    service.start()
    try:
        deadline = time.time() + 8
        row = None
        while time.time() < deadline:
            row = store.message_by_external("http", "crash-1")
            if row is not None and row.status == "processed":
                break
            time.sleep(0.05)
        assert row is not None and row.status == "processed"
        assert "echo: recovered" in str(row.meta.get("reply"))
        copies = [
            e for e in store.events(limit=30)
            if e.kind == "message" and e.content == "上次崩溃时没回的消息"
        ]
        assert len(copies) == 1
    finally:
        service.stop()
        store.close()
