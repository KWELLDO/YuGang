from __future__ import annotations

from datetime import datetime, timezone

from yugang.protocol.wake import build_wake_context, record_wake


def test_wake_context_includes_the_current_local_time(store, cfg):
    now = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    context = build_wake_context(store, cfg, query="", now=now)
    assert "现在：2026-10-25 20:34:56（Asia/Shanghai）" in context.prompt


def test_autonomous_wake_recalls_the_intention_left_before_sleep(store, cfg):
    memory = store.append_event("human", "message", "我上次说想核对 SELF 边界的证据")
    store.append_event(
        "system",
        "sleep",
        "睡去",
        meta={"next_intent": "核对 SELF 边界的证据"},
    )
    context = build_wake_context(store, cfg, query="", now=datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc))
    assert memory.id in {hit.event.id for hit in context.recalled}
