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


def test_inline_awareness_is_recorded_before_reply_and_hidden(cfg, store):
    t0 = datetime(2026, 10, 25, 2, 0, tzinfo=timezone.utc)
    block = (
        "你先说我变了，我停了一下。\n"
        '<awareness trigger="T5">'
        '{"observation":"他说我变了","hypothesis":"我确实会随着被记得的事改变",'
        '"falsifier":"如果三次同类反馈都没有带来行为差异，则此假设被推翻","confidence":0.6}'
        "</awareness>"
    )
    runtime = MockRuntime(replies=[block])
    service = GatewayService(cfg, store, runtime, now_fn=lambda: t0)
    service.start()
    try:
        assert service.chat("你变了", timeout=1) == "你先说我变了，我停了一下。"
    finally:
        service.stop()
    cards = store.awareness_cards()
    assert len(cards) == 1
    card = cards[0]
    assert card.trigger == "T5"
    assert card.hypothesis == "我确实会随着被记得的事改变"
    message_events = store.events(kind="message")
    assert any(event.id in card.evidence_refs for event in message_events)
    assert "<awareness" not in (store.events(kind="reply")[-1].content)
    context = build_wake_context(store, cfg, query="")
    assert "还没验证的自我觉察" in context.prompt
    assert "我确实会随着被记得的事改变" in context.prompt


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



def test_self_alarm_is_created_after_grace_and_clamped_by_range(store, cfg):
    from yugang.protocol.sleep import SleepResult, apply_sleep_result

    t0 = datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc)
    cfg.self_alarms_enabled = True
    cfg.self_alarms_after_days = 0
    cfg.alarm_min_minutes = 30
    cfg.alarm_max_minutes = 120
    result = SleepResult(diary="我设一个闹钟。", next_wake_minutes=45, parsed=True)
    applied = apply_sleep_result(store, cfg, result, now=t0)
    assert applied["alarm"]
    alarm = store.get_alarm(applied["alarm"])
    assert alarm.status == "pending"
    assert alarm.due_at.startswith("2026-10-25T03:45:00")
    assert applied["next_wake_at"] == alarm.due_at

    too_early = SleepResult(diary="太早了。", next_wake_minutes=5, parsed=True)
    blocked = apply_sleep_result(store, cfg, too_early, now=t0 + timedelta(minutes=1))
    assert blocked["alarm"] is None
    assert blocked["alarm_blocked"] is True


def test_self_alarm_is_blocked_during_the_first_week(store, cfg):
    from yugang.protocol.sleep import SleepResult, apply_sleep_result

    cfg.self_alarms_enabled = True
    cfg.self_alarms_after_days = 7
    result = SleepResult(diary="我想设闹钟。", next_wake_minutes=60, parsed=True)
    applied = apply_sleep_result(store, cfg, result, now=datetime(2026, 10, 25, 3, 0, tzinfo=timezone.utc))
    assert applied["alarm"] is None
    assert applied["alarm_blocked"] is True


def test_due_alarm_wakes_her_and_is_marked_fired(store, cfg):
    from yugang.runtime.mock import ScriptedRuntime

    t0 = datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    alarm = store.add_alarm(
        (t0 - timedelta(minutes=1)).isoformat(timespec="microseconds"),
        reason="她设的闹钟",
        ts=(t0 - timedelta(hours=1)).isoformat(timespec="microseconds"),
    )
    sleep_json = (
        '{"diary":"闹钟响过，我处理了那件事。","judgments":[],"stance":null,'
        '"open_loops":[],"next_intent":null,"self_edits":[],"accept_self_edit":false,'
        '"awareness_reviews":[],"next_wake_minutes":null}'
    )
    runtime = ScriptedRuntime(["我醒来处理了那件事。", sleep_json])
    service = GatewayService(cfg, store, runtime, now_fn=lambda: t0)
    assert service._maybe_fire_alarm(t0) is True
    fired = store.get_alarm(alarm.id)
    assert fired.status == "fired"
    wakes = [e for e in store.events(limit=20) if e.kind == "wake"]
    assert any(alarm.id in (e.content or "") for e in wakes)
    assert any(e.kind == "observation" and "那件事" in e.content for e in store.events(limit=20))

