from __future__ import annotations

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
