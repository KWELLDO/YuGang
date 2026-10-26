from __future__ import annotations

import json

import pytest

from yugang.config import Config
from yugang.gateway.service import GatewayService
from yugang.ids import utcnow
from yugang.memory.store import MemoryStore
from yugang.memory.tools import MemoryToolbox
from yugang.runtime.mock import MockRuntime


@pytest.fixture
def env(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    store = MemoryStore(cfg.db_path)
    yield cfg, store
    store.close()


def call(name: str, arguments: dict | None = None, call_id: str = "call_1") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(arguments or {}, ensure_ascii=False),
        },
    }


def _service(cfg, store, runtime):
    service = GatewayService(cfg, store, runtime)
    service.start()
    return service


def test_chat_can_look_up_an_old_event_and_the_reply_records_the_tool(env):
    cfg, store = env
    old = store.append_event("human", "message", "青苔-739 是上次约定的暗号。")
    runtime = MockRuntime(
        replies=["我查到了：青苔-739 是我们上次约定的暗号。"],
        tool_rounds=[[call("memory_get", {"event_id": old.id})]],
    )
    service = _service(cfg, store, runtime)
    try:
        reply = service.chat("你还记得青苔-739 吗", timeout=15)
        assert reply is not None and "青苔-739" in reply
        # One tool round, then the final answer.
        assert len(runtime.calls) == 2
        assert runtime.calls[0]["tool_count"] >= 9
        tool_messages = [m for m in runtime.calls[1]["messages"] if m.get("role") == "tool"]
        assert tool_messages
        assert old.id in tool_messages[0]["content"]
        assert tool_messages[0]["tool_call_id"] == "call_1"
        reply_event = next(e for e in store.events(limit=50) if e.kind == "reply")
        assert reply_event.meta["tools"] == [{"name": "memory_get", "ok": True}]
        prompt = runtime.calls[0]["messages"][0]["content"]
        assert "关于只读记忆工具" in prompt
    finally:
        service.stop()


def test_tool_round_limit_fails_loud_instead_of_looping(env):
    cfg, store = env
    cfg.max_tool_rounds = 1
    runtime = MockRuntime(
        replies=["不应该到这里"],
        tool_rounds=[
            [call("recent_events", {"limit": 2}, "call_a")],
            [call("recent_events", {"limit": 2}, "call_b")],
        ],
    )
    service = _service(cfg, store, runtime)
    try:
        reply = service.chat("一直查下去", timeout=15)
        assert reply is not None
        assert "工具调用超过 1 轮" in reply
        assert len(runtime.calls) == 2
        failed = [m for m in store.pending_messages() if m.status == "failed"]
        assert failed
        errors = [e for e in store.events(limit=30) if e.kind == "error"]
        assert errors
    finally:
        service.stop()


def test_unknown_tool_is_returned_to_the_model_without_crashing(env):
    cfg, store = env
    runtime = MockRuntime(
        replies=["我没有这个工具，查不到。"],
        tool_rounds=[[call("drop_everything", {})]],
    )
    service = _service(cfg, store, runtime)
    try:
        reply = service.chat("试试不存在的工具", timeout=15)
        assert reply == "我没有这个工具，查不到。"
        tool_messages = [m for m in runtime.calls[1]["messages"] if m.get("role") == "tool"]
        assert tool_messages
        assert "unknown tool: drop_everything" in tool_messages[0]["content"]
        reply_event = next(e for e in store.events(limit=50) if e.kind == "reply")
        assert reply_event.meta["tools"][0]["name"] == "drop_everything"
        assert reply_event.meta["tools"][0]["ok"] is False
    finally:
        service.stop()


def test_memory_tools_can_be_disabled(env):
    cfg, store = env
    cfg.memory_tools_enabled = False
    runtime = MockRuntime(replies=["没有工具也能回答。"])
    service = _service(cfg, store, runtime)
    try:
        reply = service.chat("查一下", timeout=15)
        assert reply == "没有工具也能回答。"
        assert len(runtime.calls) == 1
        assert runtime.calls[0]["tool_count"] == 0
        assert "关于只读记忆工具" not in runtime.calls[0]["messages"][0]["content"]
    finally:
        service.stop()


def test_every_declared_tool_runs_against_real_memory(env):
    cfg, store = env
    event = store.append_event("human", "message", "深海视频的素材在 vault 里。")
    store.add_episode(
        "测试片段",
        "一次测试醒来。",
        start_ts=event.ts,
        end_ts=event.ts,
        event_ids=[event.id],
        source_event=event.id,
    )
    judgment = store.add_judgment("我能查到旧素材。", "如果 memory_search 查不到则被推翻。", [event.id], 0.6)
    practice = store.add_practice("回答前先查 L1。", evidence_refs=[event.id])
    loop = store.add_open_loop("测试未闭合事项", refs=[event.id])
    toolbox = MemoryToolbox(store, cfg)
    names = {schema["function"]["name"] for schema in toolbox.schemas()}
    assert names == {
        "memory_search",
        "memory_get",
        "recent_events",
        "episode_search",
        "self_read",
        "self_history",
        "judgment_list",
        "practice_list",
        "open_loops",
        "memory_audit",
    }
    cases = {
        "memory_search": {"query": "深海"},
        "memory_get": {"event_id": event.id},
        "recent_events": {"limit": 3},
        "episode_search": {"query": "测试"},
        "self_read": {},
        "self_history": {"limit": 2},
        "judgment_list": {"status": "all"},
        "practice_list": {"status": "active"},
        "open_loops": {"limit": 3},
        "memory_audit": {},
    }
    for name, args in cases.items():
        result = toolbox.run(name, args)
        assert result["ok"] is True, (name, result)
        assert result["tool"] == name
    assert practice.id
    assert loop.id


def test_a_broken_memory_audit_does_not_block_waking(env, monkeypatch):
    cfg, store = env
    service = GatewayService(cfg, store, MockRuntime(replies=[]))

    def boom(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr("yugang.gateway.service.memory_health", boom)
    first = service._memory_health_state(utcnow())
    second = service._memory_health_state(utcnow())
    assert first is second
    assert first["healthy"] is False
    assert "RuntimeError: disk gone" in first["audit_error"]
    assert first["problems"]
