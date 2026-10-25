from __future__ import annotations

"""Recall must not carry a private conversation into a public answer."""

from yugang.memory.visibility import (
    is_private_channel,
    is_public_channel,
    visibility_clause,
)
from yugang.protocol.wake import points_to_past, build_wake_context


def test_channel_classification_is_explicit(  ):
    assert is_public_channel("public:bilibili")
    assert is_public_channel("bili")
    assert is_public_channel("public")
    assert not is_public_channel("email")
    assert not is_public_channel("onebot")
    assert not is_public_channel(None)
    assert is_private_channel("email")
    assert is_private_channel("wechat")
    assert not is_private_channel("bili:comment")
    assert not is_private_channel(None)


def test_visibility_clause_keeps_her_own_mind_and_drops_private_channels():
    clause, params = visibility_clause("e.channel")
    assert "e.channel IS NULL" in clause
    assert "public" in params


def test_private_message_is_not_recalled_into_a_public_answer(store, cfg):
    store.append_event("human", "message", "字幕错位这件事我私下跟你说过", channel="email", actor="kwelldo")
    store.append_event("system", "note", "字幕模板改好了，公开说过的", channel="public:bilibili")

    public = store.recall("字幕", public_only=True)
    everything = store.recall("字幕", public_only=False)

    public_text = " ".join(hit.event.content for hit in public)
    everything_text = " ".join(hit.event.content for hit in everything)
    assert "私下" not in public_text
    assert "私下" in everything_text
    assert "公开说过的" in public_text


def test_empty_query_recall_also_respects_public_scope(store, cfg):
    store.append_event("human", "message", "我邮箱里那封私信内容", channel="email")
    hits = store.recall("", public_only=True)
    assert all("私信内容" not in hit.event.content for hit in hits)


def test_a_greeting_does_not_pay_for_a_semantic_search(store, cfg):
    store.append_event("human", "message", "上次我们说好的字幕模板", channel="cli")
    assert points_to_past("在吗") is False
    assert points_to_past("你上次说的那个字幕模板呢") is True
    assert points_to_past("今天忙吗") is False

    greeting = build_wake_context(store, cfg, query="在吗", channel="cli")
    past = build_wake_context(store, cfg, query="你上次说的那个字幕模板呢", channel="cli")
    assert greeting.recalled == []
    assert past.recalled


def test_a_public_wake_cannot_see_a_private_passage(store, cfg):
    store.append_event("human", "message", "上次说的字幕错位，是我在私聊里告诉你的", channel="email")
    context = build_wake_context(store, cfg, query="你上次说的字幕错位", channel="public:bilibili")
    assert context.public_only is True
    assert all("私聊" not in hit.event.content for hit in context.recalled)
    public_text = "\n".join(hit.event.content for hit in context.recalled)
    assert "私聊" not in public_text
