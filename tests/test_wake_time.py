from __future__ import annotations

from datetime import datetime, timezone

from yugang.protocol.wake import build_wake_context, record_wake


def test_wake_context_includes_the_current_local_time(store, cfg):
    now = datetime(2026, 10, 25, 12, 34, 56, tzinfo=timezone.utc)
    context = build_wake_context(store, cfg, query="", now=now)
    assert "现在：2026-10-25 20:34:56（Asia/Shanghai）" in context.prompt


def test_autonomous_wake_recalls_the_intention_left_before_sleep(store, cfg):
    memory = store.append_event("human", "message", "我上次说想核对 SELF 边界的证据")
    store.append_event(
        "system",
        "sleep",
        "睡去",
        meta={"next_intent": "核对 SELF 边界的证据"},
    )
    context = build_wake_context(store, cfg, query="", now=datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc))
    assert memory.id in {hit.event.id for hit in context.recalled}


def test_wake_context_shows_how_she_last_changed_herself(store, cfg):
    first = store.current_self_version()
    evidence = store.append_event("human", "message", "一条促成修订的材料")
    second = store.commit_self(first.content + "\n- 这是上一次改自己的内容。\n", "测试修订：加了一句", [evidence.id])
    context = build_wake_context(store, cfg, query="")
    assert "我上一次改自己" in context.prompt
    assert "测试修订：加了一句" in context.prompt
    assert second.id == context.self_history[0].id


def test_wake_context_says_she_has_not_changed_herself_yet(store, cfg):
    context = build_wake_context(store, cfg, query="")
    assert "还没有改过自己" in context.prompt


def test_wake_context_reports_body_readings_without_claiming_experience(store, cfg):
    context = build_wake_context(
        store,
        cfg,
        query="",
        body={
            "events": 157,
            "budget": {"budget_cny": 5.0, "spent_cny": 0.79, "remaining_cny": 4.21},
            "stage": "ok",
            "last_backup": {"created_at": "2026-10-25T04:15:00+00:00", "db": "yugang.db"},
            "next_alarm": {"due_at": "2026-10-25T09:00:00+00:00", "reason": "核对证据"},
            "channels": ["http", "email"],
            "paused": False,
        },
    )
    assert "我的身体状态（系统读数，不是体验）" in context.prompt
    assert "L1 记忆：157 条" in context.prompt
    assert "已用 ¥0.79 / ¥5.00，剩余 ¥4.21" in context.prompt
    assert "最近一次身体备份：2026-10-25T04:15:00+00:00" in context.prompt
    assert "下一次自己设的醒来：2026-10-25T09:00:00+00:00（核对证据）" in context.prompt
    assert "http、email" in context.prompt
    assert "操作员暂停：否" in context.prompt


def test_wake_context_shows_evidence_ids_for_l2_and_cards(store, cfg):
    evidence = store.append_event("human", "message", "一条可以引用的材料")
    judgment = store.add_judgment("这条判断有来源", "如果找不到来源就被推翻", [evidence.id], 0.5)
    card = store.add_awareness_card(
        "T5",
        "我注意到一件事",
        "这件事可能是一个模式",
        "如果下一次没有出现，就被推翻",
        [evidence.id],
        0.5,
    )
    context = build_wake_context(store, cfg, query="")
    assert evidence.id in context.prompt
    assert judgment.id in context.prompt
    assert card.id in context.prompt
