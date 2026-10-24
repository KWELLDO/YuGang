from __future__ import annotations

import json

from yugang.gateway.channels.base import NullChannel
from yugang.gateway.service import GatewayService
from yugang.runtime.mock import MockRuntime


class EmailLike(NullChannel):
    name = "email"


def _service(cfg, store, channel):
    return GatewayService(cfg, store, MockRuntime(responder=lambda messages, purpose: "嗯。"), channels=[channel])


def test_broadcast_uses_notify_to_but_only_allowlisted_people(cfg, store):
    channel = EmailLike()
    cfg.email.allow_senders = ["owner@example.com"]
    cfg.email.notify_to = ["ops@example.com"]
    service = _service(cfg, store, channel)
    service._broadcast("她自己醒来了。", meta={"kind": "autonomous"})
    assert channel.sent[0][0] == "ops@example.com"
    assert channel.sent[0][1] == "她自己醒来了。"


def test_broadcast_falls_back_to_allow_senders(cfg, store):
    channel = EmailLike()
    cfg.email.allow_senders = ["owner@example.com"]
    cfg.email.notify_to = []
    service = _service(cfg, store, channel)
    service._broadcast("她自己醒来了。", meta={"kind": "autonomous"})
    assert channel.sent[0][0] == "owner@example.com"


def test_broadcast_records_a_missing_recipient_instead_of_dropping_it(cfg, store):
    channel = EmailLike()
    cfg.email.allow_senders = []
    cfg.email.notify_to = []
    service = _service(cfg, store, channel)
    service._broadcast("她自己醒来了。", meta={"kind": "autonomous"})
    assert not channel.sent
    notes = [event for event in store.events(kind="note") if "没有可送达的通知目标" in event.content]
    assert notes


def test_autonomous_wake_strips_awareness_and_records_it_with_real_refs(cfg, store):
    channel = EmailLike()
    cfg.email.allow_senders = ["owner@example.com"]
    replies = [
        '我先说时间缺口。\n<awareness trigger="T5">'
        '{"observation":"我醒来先读的是当前视图。","hypothesis":"我会把当前视图当成历史证据。",'
        '"falsifier":"如果我能先回 L1 核对再下结论，这条就站不住。","confidence":0.6}'
        "</awareness>",
        json.dumps({"diary": "这次自主醒来只读到当前视图，没有回 L1。"}, ensure_ascii=False),
    ]
    service = GatewayService(
        cfg,
        store,
        MockRuntime(replies=replies),
        channels=[channel],
    )

    text = service.autonomous_wake("测试闹钟")

    assert text is not None
    assert "<awareness" not in text
    assert text == "我先说时间缺口。"
    assert channel.sent[0][1] == text
    observations = [
        event for event in store.events(kind="observation")
        if event.meta.get("autonomous")
    ]
    assert observations and "<awareness" not in observations[0].content
    cards = store.awareness_cards(status="open", limit=10)
    assert len(cards) == 1
    assert cards[0].trigger == "T5"
    assert len(cards[0].evidence_refs) == 2
    assert all(store.ref_exists(ref) for ref in cards[0].evidence_refs)
    assert observations[0].id in cards[0].evidence_refs

class FlakyChannel(NullChannel):
    name = "email"

    def __init__(self):
        super().__init__()
        self.healthy = None

    def health(self):
        return {"name": self.name, "healthy": self.healthy, "consecutive_failures": 1 if self.healthy is False else 0}


def test_health_exposes_channel_state_and_logs_only_transitions(cfg, store):
    channel = FlakyChannel()
    service = _service(cfg, store, channel)
    assert service.health()["channels"]["email"]["healthy"] is None
    assert service.health()["degraded"] is False

    service._watch_channels()
    channel.healthy = False
    service._watch_channels()
    service._watch_channels()
    errors = [event for event in store.events(kind="error") if "通道不健康" in event.content]
    assert len(errors) == 1
    assert service.health()["degraded"] is True

    channel.healthy = True
    service._watch_channels()
    service._watch_channels()
    notes = [event for event in store.events(kind="note") if "通道恢复" in event.content]
    assert len(notes) == 1
    assert service.health()["degraded"] is False
