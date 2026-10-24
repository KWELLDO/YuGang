from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from conftest import make_service
import json

from yugang.protocol.sleep import SleepResult, apply_sleep_result, parse_sleep_output
from yugang.protocol.wake import build_wake_context


def test_episode_is_append_only_and_searchable(store):
    evidence = store.append_event("human", "message", "第一次在云端说话。")
    episode = store.add_episode(
        "第一次云上对话",
        "她在云上醒来，确认自己能读到 SELF.md 和 L1。",
        start_ts=evidence.ts,
        end_ts=evidence.ts,
        event_ids=[evidence.id],
        origin="model",
    )
    assert episode.id.startswith("episode_")
    assert episode.event_ids == (evidence.id,)
    assert store.episode_count() == 1
    assert store.search_episodes("云上")[0].id == episode.id
    assert store.search_episodes("SELF")[0].id == episode.id
    assert store.search_episodes("不存在的词") == []

    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE episodes SET title = 'rewritten' WHERE id = ?", (episode.id,))
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("DELETE FROM episodes WHERE id = ?", (episode.id,))
    with pytest.raises(ValueError):
        store.add_episode("没有证据", "空片段", start_ts=episode.ts, end_ts=episode.ts, event_ids=[])


def test_parse_sleep_output_reads_an_episode():
    parsed = parse_sleep_output(
        json.dumps(
            {
                "diary": "我检查了记忆。",
                "episode": {"title": "检查记忆", "summary": "她核对了 L1 和当前视图。"},
            },
            ensure_ascii=False,
        )
    )
    assert parsed.parsed is True
    assert parsed.episode == {"title": "检查记忆", "summary": "她核对了 L1 和当前视图。"}
    fallback = parse_sleep_output(json.dumps({"diary": "只是日记", "episode": "不是对象"}))
    assert fallback.episode is None


def test_sleep_creates_an_episode_backed_by_real_window_events(store, cfg):
    evidence = store.append_event("human", "message", "你还记得上一次睡去前在做什么吗？")
    now = datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc)
    applied = apply_sleep_result(
        store,
        cfg,
        SleepResult(
            diary="我醒来后核对了 SELF.md 和 L1；这次对话很短。",
            episode={
                "title": "核对记忆的一次清醒",
                "summary": "她先读 SELF.md，再回到 L1 找上一次睡去前的意图。",
            },
            parsed=True,
        ),
        now=now,
    )
    assert applied["episode"]
    assert applied["episode_origin"] == "model"
    episode = store.get_episode(applied["episode"])
    assert episode.title == "核对记忆的一次清醒"
    assert episode.source_event == applied["sleep_event"]
    assert evidence.id in episode.event_ids
    assert applied["diary_event"] in episode.event_ids
    assert episode.event_ids[-1] == applied["diary_event"]


def test_episode_does_not_reabsorb_the_previous_sleep_boundary(store, cfg):
    first = apply_sleep_result(
        store,
        cfg,
        SleepResult(diary="第一次睡去，整理材料。", parsed=True),
        now=datetime(2026, 10, 25, 7, 0, tzinfo=timezone.utc),
    )
    prompt = store.append_event(
        "human",
        "message",
        "叫醒她的那条消息。",
        ts=datetime(2026, 10, 25, 7, 0, 59, tzinfo=timezone.utc).isoformat(),
    )
    wake = store.append_event(
        "system",
        "wake",
        "被新消息叫醒。",
        ts=datetime(2026, 10, 25, 7, 1, tzinfo=timezone.utc).isoformat(),
        meta={"trigger_event": prompt.id},
    )
    evidence = store.append_event(
        "human",
        "message",
        "这是第一次睡去之后的新消息。",
        ts=datetime(2026, 10, 25, 7, 2, tzinfo=timezone.utc).isoformat(),
    )
    second = apply_sleep_result(
        store,
        cfg,
        SleepResult(diary="第二次睡去，只整理这条消息。", parsed=True),
        now=datetime(2026, 10, 25, 7, 5, tzinfo=timezone.utc),
    )
    episode = store.get_episode(second["episode"])
    assert episode.start_ts == prompt.ts
    assert prompt.id in episode.event_ids
    assert wake.id in episode.event_ids
    assert evidence.id in episode.event_ids
    assert second["diary_event"] in episode.event_ids
    # The previous window's sleep and diary are consolidation of that window,
    # not material that happened during this one.
    assert first["sleep_event"] not in episode.event_ids
    assert first["diary_event"] not in episode.event_ids


def test_sleep_creates_a_diary_index_when_the_model_omits_an_episode(store, cfg):
    applied = apply_sleep_result(
        store,
        cfg,
        SleepResult(diary="今天只发生了一件事：我检查了记忆链。", parsed=True),
        now=datetime(2026, 10, 25, 7, 1, tzinfo=timezone.utc),
    )
    episode = store.get_episode(applied["episode"])
    assert episode.origin == "diary"
    assert episode.title == "一次清醒"
    assert "我检查了记忆链" in episode.summary


def test_wake_context_and_tank_expose_recent_episodes(store, cfg):
    evidence = store.append_event("human", "message", "今天先核对记忆。")
    episode = store.add_episode(
        "核对记忆",
        "她先从 L1 找原始事件，再决定要不要改 SELF.md。",
        start_ts=evidence.ts,
        end_ts=evidence.ts,
        event_ids=[evidence.id],
    )
    context = build_wake_context(store, cfg, query="核对")
    assert [item.id for item in context.episodes] == [episode.id]
    assert "我最近的生活片段" in context.prompt
    assert "核对记忆" in context.prompt
    assert "依据 1 条 L1" in context.prompt

    service, _ = make_service(cfg, store)
    tank = service.tank()
    assert tank["episode_count"] == 1
    assert tank["episodes"][0]["title"] == "核对记忆"
    assert tank["episodes"][0]["event_count"] == 1
