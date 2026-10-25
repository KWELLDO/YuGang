from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from conftest import make_service

from yugang.protocol.wake import build_messages, build_wake_context, morning_check_messages
from yugang.runtime.openai_compat import parse_usage

FIRST = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)


def _context(store, cfg, now):
    return build_wake_context(store, cfg, query="", now=now, body={"events": 3})


def test_the_system_message_never_carries_the_clock(store, cfg):
    a = build_messages(_context(store, cfg, FIRST), "在吗")
    b = build_messages(_context(store, cfg, FIRST + timedelta(hours=5)), "在吗")
    assert a[0]["content"] == b[0]["content"]
    assert "## 主观时间" not in a[0]["content"]
    assert "## SELF.md" in a[0]["content"]
    assert a[1]["content"] != b[1]["content"]


def test_the_volatile_readings_stay_in_the_turn_that_caused_them(store, cfg):
    context = _context(store, cfg, FIRST)
    messages = build_messages(context, "只有这一句")
    assert context.stable_prompt in messages[0]["content"]
    assert context.volatile_prompt in messages[1]["content"]
    assert "只有这一句" in messages[1]["content"]


def test_the_prompt_still_contains_every_section(store, cfg):
    context = _context(store, cfg, FIRST)
    for heading in ("## 主观时间", "## 我的身体状态", "## SELF.md", "## 最近的 L1"):
        assert heading in context.prompt


def test_morning_check_splits_the_same_way(store, cfg):
    context = _context(store, cfg, FIRST)
    messages = morning_check_messages(context, reason="每日保底")
    assert "## 主观时间" not in messages[0]["content"]
    assert "每日保底" in messages[1]["content"]


def test_parse_usage_reads_the_deepseek_cache_fields():
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 20,
        "prompt_cache_hit_tokens": 800,
        "prompt_cache_miss_tokens": 200,
    }
    assert parse_usage(usage, prompt_text="x", output_text="y") == (1000, 20, 800, 200)


def test_parse_usage_stays_unknown_when_the_provider_says_nothing():
    usage = {"prompt_tokens": 900, "completion_tokens": 10}
    assert parse_usage(usage, prompt_text="x", output_text="y") == (900, 10, 0, 0)


def test_parse_usage_accepts_an_openai_style_cached_tokens():
    usage = {"prompt_tokens": 900, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 512}}
    assert parse_usage(usage, prompt_text="x", output_text="y")[2:] == (512, 388)


def test_cache_tokens_are_recorded_and_summarised(store, cfg):
    service, _ = make_service(cfg, store)
    completion = SimpleNamespace(
        model="mock", input_tokens=1000, output_tokens=0, cache_hit_tokens=750, cache_miss_tokens=250
    )
    service.budget.record_completion(completion, kind="chat")
    summary = store.cache_summary(service.budget.summary().day)
    assert summary["reported"] is True
    assert summary["hit_ratio"] == 0.75
    assert summary["cache_hit_tokens"] == 750
    assert summary["cache_miss_tokens"] == 250
    assert summary["by_kind"]["chat"]["calls"] == 1


def test_status_reports_an_unknown_cache_reading_before_any_call(store, cfg):
    service, _ = make_service(cfg, store)
    reading = service.status()["cache"]
    assert reading["reported"] is False
    assert reading["hit_ratio"] is None
