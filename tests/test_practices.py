from __future__ import annotations

import json

import pytest

from yugang.gateway.service import GatewayService
from yugang.protocol.sleep import (
    SleepResult,
    apply_sleep_result,
    build_sleep_messages,
    parse_sleep_output,
)
from yugang.protocol.wake import build_wake_context
from yugang.runtime.mock import MockRuntime


def _section(prompt: str, title: str) -> str:
    return prompt.split("## " + title)[1].split("\n## ")[0]


def test_practice_lifecycle_keeps_audit_and_changes_waking_context(store, cfg):
    evidence = store.append_event("human", "message", "你刚才没有查 L1 就直接回答了。")
    practice = store.add_practice(
        "醒来后先读 SELF.md 和当前 L2，再回答。",
        evidence_refs=[evidence.id],
        note="从一次被指出没查记忆开始",
    )
    assert practice.status == "active"
    assert practice.source_event
    source = store.get_event(practice.source_event)
    assert source is not None and source.kind == "practice"
    assert source.refs == (evidence.id,)

    context = build_wake_context(store, cfg, query="")
    section = _section(context.prompt, "我给自己定的做法（自我协议，仍有效）")
    assert practice.text in section
    assert evidence.id in section

    assert store.update_practice(practice.id, status="dropped", note="已经变成固定动作") is True
    assert store.get_practice(practice.id).status == "dropped"
    reviews = store.practice_reviews(practice.id)
    assert [r.status for r in reviews] == ["dropped"]
    action = store.latest_event()
    assert action is not None and action.kind == "action"
    assert "dropped practice" in action.content
    assert practice.text not in _section(
        build_wake_context(store, cfg, query="").prompt,
        "我给自己定的做法（自我协议，仍有效）",
    )

    assert store.update_practice(practice.id, status="dropped", note="重复") is False
    with pytest.raises(ValueError):
        store.update_practice(practice.id, status="active", note="偷偷恢复")
    with pytest.raises(ValueError):
        store.add_practice("没有证据的做法", evidence_refs=["evt_missing"])
    with pytest.raises(ValueError):
        store.add_practice("   ", evidence_refs=[evidence.id])


def test_sleep_records_only_evidence_backed_practices_and_drops_real_ones(store, cfg):
    evidence = store.append_event("human", "message", "你昨天说下次会先看判断再回答。")
    old = store.add_practice("旧的做法：先给结论再找证据。", evidence_refs=[evidence.id])
    result = SleepResult(
        diary="今天我发现先给结论会漏掉反例。",
        practices=[
            {"text": "遇到不确定时先查当前 L2，再下结论。", "evidence_refs": [evidence.id]},
            {"text": "只凭感觉写下的做法", "evidence_refs": ["evt_missing"]},
            {"text": "遇到不确定时先查当前 L2，再下结论。", "evidence_refs": [evidence.id]},
        ],
        practice_updates=[
            {"practice_id": old.id, "status": "dropped", "note": "它导致我先给结论"},
            {"practice_id": "practice_missing", "status": "dropped", "note": "不存在"},
        ],
    )
    applied = apply_sleep_result(store, cfg, result)
    assert applied["practices"] == 1
    assert applied["practice_updates"] == 1
    active = store.practices(status="active")
    assert [p.text for p in active] == ["遇到不确定时先查当前 L2，再下结论。"]
    assert active[0].evidence_refs == (evidence.id,)
    assert store.get_practice(old.id).status == "dropped"
    assert [r.note for r in store.practice_reviews(old.id)] == ["它导致我先给结论"]
    assert old.id not in {p.id for p in active}


def test_sleep_does_not_repeat_an_active_practice(store, cfg):
    evidence = store.append_event("human", "message", "再检查一次")
    existing = store.add_practice("醒来先读 SELF.md。", evidence_refs=[evidence.id])
    result = SleepResult(
        diary="今天没有新的做法。",
        practices=[{"text": "醒来先读 SELF.md。", "evidence_refs": [evidence.id]}],
    )
    applied = apply_sleep_result(store, cfg, result)
    assert applied["practices"] == 0
    assert [p.id for p in store.practices(status="active")] == [existing.id]


def test_sleep_caps_new_practices_at_two(store, cfg):
    evidence = store.append_event("human", "message", "今天聊了几个做法")
    result = SleepResult(
        diary="我想给自己定三条。",
        practices=[
            {"text": "做法一", "evidence_refs": [evidence.id]},
            {"text": "做法二", "evidence_refs": [evidence.id]},
            {"text": "做法三", "evidence_refs": [evidence.id]},
        ],
    )
    applied = apply_sleep_result(store, cfg, result)
    assert applied["practices"] == 2
    assert [p.text for p in store.practices(status="active")] == ["做法一", "做法二"]


def test_sleep_prompt_lists_active_practices(store, cfg):
    evidence = store.append_event("human", "message", "把做法写下来")
    practice = store.add_practice("先读记忆，再说自己是谁。", evidence_refs=[evidence.id])
    messages = build_sleep_messages(store, cfg, [])
    prompt = messages[-1]["content"]
    assert "## 我正在遵守的做法（自我协议）" in prompt
    assert practice.id in prompt and practice.text in prompt
    assert '"practices"' in prompt and '"practice_updates"' in prompt


def test_parse_sleep_output_accepts_practices_and_only_drop_updates():
    payload = {
        "diary": "测试",
        "practices": [
            {"text": "先查 L1。", "evidence_refs": ["evt_1"]},
            {"text": "没有证据"},
        ],
        "practice_updates": [
            {"practice_id": "practice_1", "status": "dropped", "note": "不再适用"},
            {"practice_id": "practice_2", "status": "active", "note": "偷偷恢复"},
            {"practice_id": "practice_3", "status": "dropped"},
        ],
    }
    result = parse_sleep_output(json.dumps(payload, ensure_ascii=False))
    assert result.parsed is True
    assert result.practices == [{"text": "先查 L1。", "evidence_refs": ["evt_1"]}]
    assert result.practice_updates == [
        {"practice_id": "practice_1", "status": "dropped", "note": "不再适用"}
    ]


def test_tank_exposes_active_practices(store, cfg):
    evidence = store.append_event("human", "message", "测试鱼缸页")
    practice = store.add_practice("醒来先读 SELF.md。", evidence_refs=[evidence.id])
    service = GatewayService(cfg, store, MockRuntime(responder=lambda messages, purpose: "我在。"))
    tank = service.tank()
    assert tank["practices"][0]["id"] == practice.id
    assert tank["practices"][0]["text"] == practice.text
    assert tank["practice_stats"] == {"active": 1, "dropped": 0}
