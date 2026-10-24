from __future__ import annotations

import time
from datetime import datetime, timezone

from yugang.gateway.service import GatewayService
from yugang.runtime.mock import MockRuntime


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_pause_buffers_messages_and_resume_answers_them(cfg, store):
    now = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    runtime = MockRuntime(responder=lambda messages, purpose: "恢复后我在。")
    service = GatewayService(cfg, store, runtime, now_fn=lambda: now)
    service.start()
    try:
        paused = service.pause(reason="测试暂停")
        assert paused["paused"] is True
        assert service.status()["paused"] is True

        reply = service.chat("暂停时也不许丢", timeout=2)
        assert reply is not None and "操作员暂停" in reply
        service.queue.join()

        # The message is visible in L1 but has not been answered or woken.
        assert service.window.is_awake() is False
        assert store.latest_event("wake") is None
        pending = store.recoverable_messages()
        assert any(m.content == "暂停时也不许丢" and m.status == "received" for m in pending)

        service.resume()
        assert service.status()["paused"] is False

        def recovered():
            row = store.conn.execute(
                "SELECT status, meta FROM messages WHERE direction = 'in' AND content = ?",
                ("暂停时也不许丢",),
            ).fetchone()
            return bool(row and row["status"] == "processed" and "恢复后我在。" in (row["meta"] or ""))

        assert _wait_for(recovered)
        assert store.latest_event("wake") is not None
    finally:
        service.stop()


def test_pause_does_not_fake_a_sleep(cfg, store):
    now = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    runtime = MockRuntime(responder=lambda messages, purpose: "我在。")
    service = GatewayService(cfg, store, runtime, now_fn=lambda: now)
    service.start()
    try:
        assert service.chat("醒着", timeout=2) == "我在。"
        assert service.window.is_awake() is True
        sleeps_before = [e for e in store.events(limit=100) if e.kind == "sleep"]
        service.pause(reason="维护")
        assert service.window.is_awake() is False
        sleeps_after = [e for e in store.events(limit=100) if e.kind == "sleep"]
        assert len(sleeps_after) == len(sleeps_before)
        assert any("这不是睡眠，没有睡去日记" in e.content for e in store.events(limit=30))
    finally:
        service.stop()
