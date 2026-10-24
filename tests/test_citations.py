from __future__ import annotations

import re
from datetime import datetime, timezone

from yugang.config import Config
from yugang.gateway.service import GatewayService
from yugang.memory.store import MemoryStore
from yugang.runtime.mock import MockRuntime
from yugang.selfmodel.self_md import bootstrap_self


def _event_confirmations(store: MemoryStore, event_id: str) -> int:
    row = store.conn.execute(
        "SELECT confirmations FROM event_confirmations WHERE event_id = ?", (event_id,)
    ).fetchone()
    return int(row["confirmations"]) if row else 0


def test_citations_confirm_only_old_real_events(store):
    old = store.append_event(
        "human", "message", "昨天她说过会先核对证据。", ts="2026-10-25T00:00:00+00:00"
    )
    current = store.append_event(
        "human", "message", "今天再问一次。", ts="2026-10-25T01:00:00+00:00"
    )
    reply = store.append_event(
        "ai-observation",
        "reply",
        f"我记得 {old.id}。也看到你现在的消息 {current.id}，还有不存在的 evt_20990101T000000000000_ffffff。",
        ts="2026-10-25T01:00:05+00:00",
    )

    assert (
        store.confirm_citations(
            reply.content,
            source_event=reply.id,
            before_ts=current.ts,
            exclude=[old.id],
        )
        == []
    )
    confirmed = store.confirm_citations(
        reply.content, source_event=reply.id, before_ts=current.ts
    )

    assert confirmed == [old.id]
    assert store.citation_count(old.id) == 1
    assert store.citation_count(current.id) == 0
    assert _event_confirmations(store, old.id) == 1
    assert _event_confirmations(store, current.id) == 0
    # The same reply cannot inflate the counter twice, and duplicate mentions
    # in one reply are one confirmation.
    assert store.confirm_citations(reply.content, source_event=reply.id, before_ts=current.ts) == []
    assert store.citation_count(old.id) == 1


def test_a_reply_that_cites_an_old_event_confirms_it(cfg):
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    old = store.append_event(
        "human", "message", "记住：证据要能追溯到 L1。", ts="2026-10-24T20:00:00+00:00"
    )
    runtime = MockRuntime(responder=lambda messages, purpose: f"我记得这条：{old.id}。")
    service = GatewayService(cfg, store, runtime)
    service.start()
    try:
        reply = service.chat("你还记得证据规则吗？", timeout=10)
        assert reply and old.id in reply
        reply_event = store.latest_event("reply")
        assert reply_event is not None
        assert store.citation_count(old.id) == 1
        assert _event_confirmations(store, old.id) == 1
        row = store.conn.execute(
            "SELECT event_id, source_event FROM citation_confirmations WHERE event_id = ?",
            (old.id,),
        ).fetchone()
        assert row is not None
        assert row["source_event"] == reply_event.id
        message_event = store.latest_event("message")
        assert message_event is not None and message_event.id != old.id
        assert store.citation_count(message_event.id) == 0
    finally:
        service.stop()
        store.close()


def test_the_message_that_wakes_her_is_not_a_confirmed_memory(cfg):
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)

    def responder(messages, purpose):
        prompt = "\n".join(m.get("content", "") for m in messages)
        ids = re.findall(r"\[(evt_[A-Za-z0-9_]+)\] [^\n]*human/message", prompt)
        cited = ids[-1] if ids else "evt_00000000T000000000000_000000"
        return f"你刚刚这条 {cited} 是刚发生的，不是我记得的旧事。"

    runtime = MockRuntime(responder=responder)
    service = GatewayService(cfg, store, runtime)
    service.start()
    try:
        reply = service.chat("刚才那句不算你记得吧？", timeout=10)
        assert reply
        message_event = store.latest_event("message")
        assert message_event is not None
        assert message_event.id in reply
        assert store.citation_count(message_event.id) == 0
        assert _event_confirmations(store, message_event.id) == 0
    finally:
        service.stop()
        store.close()
