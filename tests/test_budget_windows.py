from __future__ import annotations

from datetime import datetime, timedelta, timezone

from yugang.config import Config
from yugang.gateway.budget import BudgetGuard
from yugang.gateway.service import GatewayService
from yugang.gateway.window import WakeWindow
from yugang.ids import day_key
from yugang.memory.store import MemoryStore
from yugang.protocol.wake import build_wake_context, record_wake
from yugang.runtime.base import Completion
from yugang.runtime.mock import MockRuntime
from yugang.selfmodel.self_md import bootstrap_self


def test_wake_window_idle_and_hard_maximum():
    t0 = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    window = WakeWindow(idle_seconds=10, max_seconds=30)
    assert window.wake(t0) is True
    assert window.is_awake()
    assert window.wake(t0 + timedelta(seconds=5)) is False
    assert window.should_sleep(t0 + timedelta(seconds=14)) is False
    assert window.should_sleep(t0 + timedelta(seconds=15)) is True

    hard = WakeWindow(idle_seconds=10, max_seconds=30)
    hard.wake(t0)
    hard.touch(t0 + timedelta(seconds=25))
    assert hard.should_sleep(t0 + timedelta(seconds=30)) is True
    hard.sleep()
    assert not hard.is_awake()


def test_budget_user_can_spend_but_autonomy_keeps_reserve(store, cfg):
    cfg.daily_budget_cny = 1.0
    cfg.user_reserve_cny = 0.6
    now = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    store.record_budget(day_key(now), "chat", 0.5, model="deepseek-chat")
    guard = BudgetGuard(store, cfg, now_fn=lambda: now)
    assert guard.stage(now) == "ok"
    assert guard.check(0.4, kind="chat", user_facing=True, now=now).allowed is True
    blocked = guard.check(0.4, kind="autonomous", user_facing=False, now=now)
    assert blocked.allowed is not True
    assert "reserve" in blocked.reason


def test_budget_warning_and_exhaustion_are_loud(store, cfg):
    cfg.daily_budget_cny = 1.0
    now = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    guard = BudgetGuard(store, cfg, now_fn=lambda: now)
    completion = Completion(text="x", input_tokens=405_000, output_tokens=0, model="deepseek-chat")
    cost = guard.record_completion(completion, kind="chat", now=now)
    assert cost == 0.81
    assert guard.stage(now) == "warning"
    warnings = [e for e in store.events(limit=20) if e.kind == "note" and e.meta.get("stage") == "warning"]
    assert warnings

    completion2 = Completion(text="x", input_tokens=100_000, output_tokens=0, model="deepseek-chat")
    guard.record_completion(completion2, kind="chat", now=now)
    assert guard.stage(now) == "exhausted"
    assert guard.check(0.01, kind="chat", user_facing=True, now=now).allowed is not True
    assert "budget" in BudgetGuard.fail_loud("daily budget exhausted")


def test_first_wake_context_does_not_invent_a_zero_second_sleep(store, cfg):
    record_wake(store, "测试醒来")
    context = build_wake_context(store, cfg, query="")
    assert context.gap_seconds is None
    assert "第一次醒来" in context.prompt
    assert "距离上一次睡去" not in context.prompt


def test_later_wake_context_measures_from_last_sleep(store, cfg):
    sleep = store.append_event("system", "sleep", "睡去：整理材料")
    now = datetime.fromisoformat(sleep.ts) + timedelta(hours=3)
    record_wake(store, "后来醒来", now=now)
    context = build_wake_context(store, cfg, query="", now=now)
    assert context.gap_seconds is not None
    assert context.gap_seconds >= 3 * 3600 - 1
    assert "距离上一次睡去" in context.prompt


def test_wake_after_restart_without_sleep_is_an_interruption(store, cfg):
    t0 = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    first = record_wake(store, "第一次醒来", now=t0)
    t1 = t0 + timedelta(hours=2)
    record_wake(store, "服务重启后醒来", now=t1)
    context = build_wake_context(store, cfg, query="", now=t1)
    assert context.interrupted is True
    assert context.last_wake is not None and context.last_wake.id == first.id
    assert context.gap_seconds is not None and context.gap_seconds >= 7200 - 1
    assert "这是我第一次醒来" not in context.prompt
    assert "可能被中断" in context.prompt
    assert "距离上一次睡去" not in context.prompt


def test_wake_after_prior_wake_and_sleep_is_normal(store, cfg):
    t0 = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    record_wake(store, "第一次醒来", now=t0)
    t1 = t0 + timedelta(minutes=5)
    store.append_event("system", "sleep", "睡去：整理材料", ts=t1.isoformat(timespec="microseconds"))
    t2 = t1 + timedelta(hours=3)
    record_wake(store, "睡够后醒来", now=t2)
    context = build_wake_context(store, cfg, query="", now=t2)
    assert context.interrupted is False
    assert context.gap_seconds is not None and context.gap_seconds >= 3 * 3600 - 1
    assert "距离上一次睡去" in context.prompt


def test_second_message_in_same_window_is_a_continuation(cfg, store):
    t0 = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    runtime = MockRuntime(responder=lambda m, p: "嗯。")
    service = GatewayService(cfg, store, runtime, now_fn=lambda: t0)
    service.start()
    try:
        assert service.chat("第一句", timeout=1) == "嗯。"
        assert service.chat("第二句", timeout=1) == "嗯。"
    finally:
        service.stop()
    second_prompt = runtime.calls[1]["messages"][0]["content"]
    assert "同一次清醒" in second_prompt
    assert "第一次醒来" not in second_prompt
    assert len(store.events(kind="wake")) == 1


def test_daily_floor_uses_local_hour_not_utc(cfg, store):
    early = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)  # 08:30 in Shanghai
    early_service = GatewayService(cfg, store, MockRuntime(responder=lambda m, p: "我在。"), now_fn=lambda: early)
    early_service._maybe_daily_floor(early)
    assert store.latest_event("wake") is None

    late = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)  # 09:30 in Shanghai
    bootstrap_self(store, cfg)
    late_service = GatewayService(cfg, store, MockRuntime(responder=lambda m, p: "早上好。"), now_fn=lambda: late)
    late_service._maybe_daily_floor(late)
    wake = store.latest_event("wake")
    assert wake is not None
    assert day_key(datetime.fromisoformat(wake.ts), cfg.timezone) == "2026-10-25"
    assert late_service.window.is_awake() is False

