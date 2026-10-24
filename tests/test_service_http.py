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


def test_delivery_failure_is_recorded_not_swallowed(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)

    class FailingChannel:
        name = "email"

        def start(self, on_message):
            pass

        def stop(self):
            pass

        def send(self, recipient, content, *, meta=None):
            return False

    service = GatewayService(
        cfg, store, MockRuntime(responder=lambda messages, purpose: "回信内容"), channels=[FailingChannel()]
    )
    service.start()
    try:
        reply = service.chat("这封邮件会投递失败", channel="email", timeout=10)
        assert reply == "回信内容"
        service.queue.join()
        errors = [e for e in store.events(limit=30) if e.kind == "error" and "回复投递失败" in e.content]
        assert errors
        assert "channel.send returned False" in errors[0].content
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
        health_status, health_body = _request(port, "GET", "/health")
        assert health_status == 200
        assert health_body["ok"] is True
        assert health_body["worker_alive"] is True
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

def test_operator_can_end_a_wake_window_through_the_http_sleep_endpoint(running):
    service, store, _cfg = running
    channel = HttpChannel("127.0.0.1", 0, token="s3cret")
    channel.bind(service)
    channel.start(service.handle_incoming)
    port = channel._server.server_address[1]
    try:
        status, _payload = _request(port, "POST", "/sleep", {}, token="s3cret")
        assert status == 409
        service.chat("先醒来", timeout=10)
        assert service.window.is_awake() is True
        status, payload = _request(port, "POST", "/sleep", {"reason": "test"}, token="s3cret")
        assert status == 200
        assert payload["ok"] is True
        assert payload["sleep"]["diary_event"]
        assert service.window.is_awake() is False
        assert store.latest_event("sleep") is not None
    finally:
        channel.stop()


def test_operator_can_pause_and_resume_through_http(running):
    service, store, _cfg = running
    channel = HttpChannel("127.0.0.1", 0, token="s3cret")
    channel.bind(service)
    channel.start(service.handle_incoming)
    port = channel._server.server_address[1]
    try:
        assert _request(port, "POST", "/pause", {"reason": "maintenance"})[0] == 401
        status, payload = _request(port, "POST", "/pause", {"reason": "maintenance"}, token="s3cret")
        assert status == 200 and payload["paused"] is True

        status, status_payload = _request(port, "GET", "/status", token="s3cret")
        assert status == 200
        assert status_payload["paused"] is True
        assert status_payload["pause"]["reason"] == "maintenance"

        # Sleep is a model-backed operation, so it is unavailable while paused.
        assert _request(port, "POST", "/sleep", {}, token="s3cret")[0] == 409

        status, reply = _request(
            port,
            "POST",
            "/message",
            {"content": "暂停时发的", "id": "paused-1"},
            token="s3cret",
        )
        assert status == 200
        assert "操作员暂停" in reply["reply"]
        assert service.window.is_awake() is False

        status, resumed = _request(port, "POST", "/resume", {}, token="s3cret")
        assert status == 200 and resumed["paused"] is False
        status, status_payload = _request(port, "GET", "/status", token="s3cret")
        assert status == 200 and status_payload["paused"] is False

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            row = store.conn.execute(
                "SELECT status, meta FROM messages WHERE direction = 'in' AND content = ?",
                ("暂停时发的",),
            ).fetchone()
            if row and row["status"] == "processed" and "echo:" in (row["meta"] or ""):
                break
            time.sleep(0.05)
        assert row is not None and row["status"] == "processed"
    finally:
        channel.stop()


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

def test_sleep_retries_once_when_output_is_truncated(tmp_path):
    from yugang.runtime.mock import ScriptedRuntime

    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)
    runtime = ScriptedRuntime(
        [
            "收到。",
            '{"diary": "被截断的整理',
            json.dumps(
                {
                    "diary": "重试后形成了一段完整的日记。",
                    "judgments": [
                        {
                            "claim": "我会在整理失败后重试一次。",
                            "falsifier": "如果重试也没有发生，则被推翻。",
                            "evidence_refs": ["PLACEHOLDER"],
                            "confidence": 0.5,
                        }
                    ],
                    "open_loops": [],
                    "next_intent": None,
                    "self_edits": [],
                    "accept_self_edit": False,
                },
                ensure_ascii=False,
            ),
        ]
    )
    service = GatewayService(cfg, store, runtime)
    service.start()
    try:
        service.chat("今天说一句话", timeout=10)
        evidence = next(e for e in store.events(limit=20) if e.kind == "message")
        runtime._replies[-1] = runtime._replies[-1].replace("PLACEHOLDER", evidence.id)
        applied = service.sleep_window(reason="测试截断重试")
        assert applied is not None
        assert applied["parsed"] is True
        assert applied["judgments"] == 1
        parse_errors = [e for e in store.events(limit=30) if e.meta.get("stage") == "sleep_parse"]
        assert parse_errors
        assert len(runtime.calls) == 3
    finally:
        service.stop()
        store.close()


def test_sleep_fails_loudly_and_never_stores_raw_json_as_diary(tmp_path):
    from yugang.runtime.mock import ScriptedRuntime

    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)
    service = GatewayService(cfg, store, ScriptedRuntime(["收到。", '{"diary": "截断', "仍然不是 JSON"]))
    service.start()
    try:
        service.chat("这次整理会失败", timeout=10)
        applied = service.sleep_window(reason="测试失败")
        assert applied is not None
        assert applied["parsed"] is False
        diary = store.latest_event("diary")
        assert diary is not None
        assert "没有形成可解析的整理" in diary.content
        assert not diary.content.lstrip().startswith("{")
    finally:
        service.stop()
        store.close()


def test_health_is_not_ok_when_the_worker_was_never_started(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    store = MemoryStore(cfg.db_path)
    service = GatewayService(cfg, store, MockRuntime())
    channel = HttpChannel("127.0.0.1", 0, token=None)
    channel.bind(service)
    channel.start(service.handle_incoming)
    port = channel._server.server_address[1]
    try:
        status, body = _request(port, "GET", "/health")
        assert status == 503
        assert body["ok"] is False
        assert body["worker_alive"] is False
    finally:
        channel.stop()
        store.close()
