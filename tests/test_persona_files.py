from __future__ import annotations

"""Her persona files must say what they cannot fit, and must not leak a secret.

These tests are about honesty of the prompt, not about prose quality: an
over-budget section has to be announced where it was cut, a duplicated line has
to be reported as double-injected, and a credential found in a persona file must
be reported by kind and location without being echoed back.
"""

import pytest

from yugang.protocol.wake import build_wake_context
from yugang.selfmodel.identity import (
    IDENTITY_TEMPLATE,
    bootstrap_identity,
    identity_path,
    read_identity,
)
from yugang.selfmodel.persona_check import check_persona
from yugang.selfmodel.self_md import get_section, split_for_prompt


def _commit(store, text: str, note: str = "测试用自我材料") -> None:
    event = store.append_event("system", "note", note)
    store.commit_self(text, "测试提交", [event.id])


def test_identity_is_written_once_and_leaves_one_l1_origin(store, cfg):
    bootstrap_identity(store, cfg)
    first = store.events(limit=50)
    identity_events = [e for e in first if (e.meta or {}).get("kind") == "identity"]
    assert len(identity_events) == 1

    bootstrap_identity(store, cfg)
    again = [e for e in store.events(limit=50) if (e.meta or {}).get("kind") == "identity"]
    assert len(again) == 1
    assert read_identity(cfg) == IDENTITY_TEMPLATE
    assert identity_path(cfg).name == "IDENTITY.md"


def test_wake_reads_the_identity_core_before_her_own_self(store, cfg):
    bootstrap_identity(store, cfg)
    context = build_wake_context(store, cfg, query="在吗")
    assert "## 我的身份内核" in context.prompt
    assert context.prompt.index("## 我的身份内核") < context.prompt.index("## SELF.md")
    assert "L1 只追加" in context.identity_text
    # It is a condition, not a memory: it belongs to the stable half of the prompt.
    assert "## 我的身份内核" in context.stable_prompt


def test_an_over_budget_section_is_announced_not_silently_cut(store, cfg):
    long_section = "我这段时间一直在想一件很具体的事。" * 120  # well past the budget
    text = "# SELF.md — 大肥鱼\n\n## 我是谁\n\n" + long_section + "\n\n## 我相信什么\n\n- 2026-10-01 我相信证据。\n"
    _commit(store, text)

    prompt, overflows = split_for_prompt(store.current_self())
    assert [item.heading for item in overflows] == ["我是谁"]
    assert overflows[0].over > 0
    assert "这次没有进入上下文" in prompt
    assert long_section not in prompt  # really cut, not just annotated

    context = build_wake_context(store, cfg, query="在吗")
    assert "这一次没有完整进入上下文" in context.prompt
    assert "不是我没有那部分" in context.prompt
    assert context.self_overflows and context.self_overflows[0].heading == "我是谁"


def test_persona_check_reports_double_injection_undated_lines_and_secrets(store, cfg):
    bootstrap_identity(store, cfg)
    duplicated = "- 我不拿播放量当价值函数，这件事我确认过"
    text = f"""# SELF.md — 大肥鱼

## 我是谁

{duplicated}

## 我相信什么

{duplicated}
- 2026-10-01 我相信能被反证的东西
- 我觉得这个东西应该是好的

## 我在做什么

- 2026-10-20 在整理字幕流程

## 我的边界

- api_key = sk-abcdefghijklmnopqrst

## 未闭合的事

## 最近的变化
"""
    _commit(store, text)
    report = check_persona(store, cfg)

    blob = "\n".join(report["problems"] + report["warnings"])
    assert "注入两次" in blob
    assert "没有日期" in blob
    assert "API key" in blob or "token" in blob
    assert "sk-abcdefghijklmnopqrst" not in blob  # never echo the value
    assert report["healthy"] is False


def test_persona_check_is_clean_for_a_fresh_body(store, cfg):
    bootstrap_identity(store, cfg)
    report = check_persona(store, cfg)
    assert report["problems"] == []
    assert report["identity"]["present"] is True
    assert report["self"]["prompt_chars"] > 0
    # Nothing is over budget out of the box, so nothing is cut at wake time.
    assert report["self"]["overflows"] == []


def test_identity_is_not_part_of_the_editable_self(store, cfg):
    bootstrap_identity(store, cfg)
    before = read_identity(cfg)
    text = store.current_self()
    assert "IDENTITY.md" not in text.split("## 我的边界", 1)[-1]
    assert get_section(text, "我的边界") is not None
    assert read_identity(cfg) == before
