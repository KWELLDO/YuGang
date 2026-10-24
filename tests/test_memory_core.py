from __future__ import annotations

from datetime import datetime, timezone

import pytest

from yugang.config import DEFAULT_SELF_TEMPLATE
from yugang.protocol.sleep import SleepResult, apply_sleep_result
from yugang.selfmodel.awareness import create_card
from yugang.selfmodel.self_md import get_section, validate_self


def test_l1_hash_chain_and_tamper_detection(store):
    before = store.latest_event()
    assert before is not None
    first = store.append_event("human", "message", "你好，鱼缸")
    second = store.append_event("ai-observation", "observation", "我看到了")
    ok, bad = store.verify_chain()
    assert ok is True
    assert bad is None
    assert first.prev_hash == before.hash
    assert second.prev_hash == first.hash

    store.conn.execute("DROP TRIGGER events_no_update")
    store.conn.execute("UPDATE events SET content = ? WHERE id = ?", ("被改过", first.id))
    ok, bad = store.verify_chain()
    assert ok is False
    assert bad == first.id


def test_recall_mixed_chinese_terms_and_fts(store):
    target = store.append_event("human", "message", "今天在深海的视频拍完了，海水很冷")
    store.append_event("human", "message", "今天吃了一碗面")
    hits = store.recall("深海 视频", limit=5, log=False)
    assert hits
    assert hits[0].event.id == target.id

    fts_hits = store.recall("深海的视频", limit=5, log=False)
    assert fts_hits
    assert fts_hits[0].event.id == target.id


def test_recall_is_reinforced_by_future_confirmation(store):
    now = datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)
    evidence = store.append_event("human", "message", "鲸歌录音里有一段很低的低频")
    store.append_event("human", "message", "鲸歌录音的第二个版本更安静")
    judgment = store.add_judgment(
        "这段鲸歌录音值得保留",
        "如果后来的我听不出它与噪声的区别，则被推翻",
        [evidence.id],
        0.5,
        trigger="test",
    )
    before = {hit.event.id: hit for hit in store.recall("鲸歌录音", limit=5, now=now, log=False)}
    assert evidence.id in before
    base_score = before[evidence.id].score
    assert before[evidence.id].confirmations == 0

    store.review_judgment(judgment.id, "supported", [evidence.id], note="重新听后仍成立")
    after = {hit.event.id: hit for hit in store.recall("鲸歌录音", limit=5, now=now, log=False)}
    assert after[evidence.id].confirmations == 1
    assert after[evidence.id].score > base_score

    store.recall("鲸歌录音", limit=5, now=now, log=True)
    later = {hit.event.id: hit for hit in store.recall("鲸歌录音", limit=5, now=now, log=False)}
    assert later[evidence.id].recall_count >= 1
    assert later[evidence.id].score >= after[evidence.id].score


def test_judgment_requires_falsifier_and_keeps_history(store):
    evidence = store.append_event("human", "message", "她说她喜欢安静")
    with pytest.raises(ValueError):
        store.add_judgment("她喜欢安静", "  ", [evidence.id], 0.5)
    judgment = store.add_judgment(
        "她可能更偏好安静的工作方式",
        "如果她连续三次主动选择嘈杂环境，则此判断被削弱",
        [evidence.id],
        0.4,
        trigger="test",
    )
    assert store.judgment_status(judgment.id) == "open"
    store.review_judgment(judgment.id, "refuted", [evidence.id], note="后来去了吵闹的现场")
    assert store.judgment_status(judgment.id) == "refuted"
    revised = store.add_judgment(
        "她不是单纯偏好安静，而是需要可控的刺激",
        "如果她在不可控噪声中仍然稳定，则此判断被削弱",
        [evidence.id],
        0.6,
        supersedes=judgment.id,
    )
    assert len(store.judgments()) == 2
    assert revised.supersedes == judgment.id


def test_self_versions_are_append_only_with_diff(store):
    current = store.current_self()
    assert current
    revised = current.replace("## 最近的变化\n\n- （还没有。）", "## 最近的变化\n\n- 我开始把记忆当作证据，而不是背景。")
    assert revised != current
    version = store.commit_self(revised, "测试：一次自我修订", [], accepted_by="kwelldo")
    assert store.current_self() == revised
    history = store.self_history()
    assert len(history) == 2
    assert "SELF.md.previous" in version.diff
    assert "记忆当作证据" in version.diff
    diffs = [e for e in store.events(limit=20) if e.kind == "self_diff"]
    assert diffs


def test_awareness_card_requires_real_evidence(store):
    evidence = store.append_event("ai-observation", "observation", "我在被问身份时先读了记忆")
    with pytest.raises(ValueError):
        create_card(
            store,
            {
                "trigger": "T1",
                "observation": "我先读记忆",
                "hypothesis": "我依赖记忆回答身份",
                "falsifier": "如果我不读记忆也能答",
                "evidence_refs": ["evt_does_not_exist"],
                "confidence": 0.4,
            },
        )
    card = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "我先读记忆",
            "hypothesis": "我依赖记忆回答身份",
            "falsifier": "如果我不读记忆也能答",
            "evidence_refs": [evidence.id],
            "confidence": 0.4,
        },
    )
    assert card.evidence_refs == (evidence.id,)
    assert store.card_status(card.id) == "open"
    store.review_awareness(card.id, "needs-evidence", note="样本只有一次")
    assert store.card_status(card.id) == "needs-evidence"


def test_sleep_applies_judgment_stance_loop_and_self_edit(store, cfg):
    evidence = store.append_event("human", "message", "你昨天说会记得我")
    revised = DEFAULT_SELF_TEMPLATE.replace(
        "## 最近的变化\n\n- （还没有。）",
        "## 最近的变化\n\n- 我确认自己会把承诺写进未闭合清单。",
    )
    assert not validate_self(revised)
    result = SleepResult(
        diary="今天有人回来确认我还记得他。",
        judgments=[
            {
                "claim": "我会把承诺当作需要跟踪的事",
                "falsifier": "如果连续三次承诺都没有进入未闭合清单，则此判断被削弱",
                "evidence_refs": [evidence.id],
                "confidence": 0.5,
            }
        ],
        stance="我先记住，再谈感受。",
        open_loops=[{"title": "下次醒来确认这个承诺", "detail": None, "due": None}],
        next_intent="醒来先读 SELF.md。",
        self_edit=revised,
        accept_self_edit=True,
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[{"role": "user", "content": "你好"}])
    assert applied["judgments"] == 1
    assert applied["stance"] is True
    assert applied["open_loops"] == 1
    assert applied["self_committed"] is True
    assert store.current_self() == revised
    latest_self = store.self_history()[0]
    assert evidence.id in latest_self.evidence_refs
    assert any(loop.title == "下次醒来确认这个承诺" for loop in store.open_loops())


def test_invalid_self_edit_is_rejected(store, cfg):
    before = store.current_self()
    result = SleepResult(
        diary="今天没有足够证据改 SELF.md。",
        self_edit="# 我是谁\n\n- 没有章节格式",
        accept_self_edit=True,
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["self_committed"] is False
    assert store.current_self() == before


def test_sleep_drops_judgment_that_cites_only_fake_evidence(store, cfg):
    result = SleepResult(
        diary="今天没有真实证据，只有一个看起来像 id 的字符串。",
        judgments=[
            {
                "claim": "我会记得所有承诺",
                "falsifier": "如果下一次没有进入未闭合清单，则被推翻",
                "evidence_refs": ["evt_fake"],
                "confidence": 0.9,
            }
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["judgments"] == 0
    assert store.judgments() == []


def test_sleep_does_not_duplicate_an_open_loop(store, cfg):
    store.add_open_loop("检验 given 主张")
    result = SleepResult(
        diary="今天又想到同一件未闭合的事。",
        open_loops=[{"title": "检验 given 主张", "detail": "重复输出不应产生第二条"}, {"title": "新的一件事"}],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    titles = [loop.title for loop in store.open_loops()]
    assert applied["open_loops"] == 1
    assert titles.count("检验 given 主张") == 1
    assert "新的一件事" in titles


def test_duplicate_external_message_is_idempotent(store):
    first, first_event = store.receive_message("http", "重复消息", sender="kwelldo", external_id="same-1")
    count = store.event_count()
    second, second_event = store.receive_message("http", "重复消息", sender="kwelldo", external_id="same-1")
    assert second.id == first.id
    assert second_event.id == first_event.id
    assert store.event_count() == count


def test_structured_self_edits_are_auditable_and_require_real_evidence(store, cfg):
    evidence = store.append_event("human", "message", "你上次说会在醒来时先读 SELF.md")
    before = store.current_self()
    result = SleepResult(
        diary="今天有一次真实的核对，可以改两处很小的东西。",
        self_edits=[
            {
                "section": "我相信什么",
                "op": "append",
                "text": "我能在记录层面区分中断与睡去，这靠 L1，不靠体验连续。",
                "evidence_refs": [evidence.id],
            },
            {
                "section": "我在做什么",
                "op": "replace",
                "text": "- 正在核对 L1 与 SELF.md 的差异。",
                "evidence_refs": [evidence.id],
            },
            {
                "section": "我的边界",
                "op": "append",
                "text": "这条使用假证据，不应被应用。",
                "evidence_refs": ["evt_fake"],
            },
        ],
        accept_self_edit=True,
        parsed=True,
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    after = store.current_self()

    assert applied["self_committed"] is True
    assert applied["self_sections"] == ["我相信什么", "我在做什么"]
    assert "（还没有被我的经历检验过的判断。）" not in after
    assert "记录层面区分中断与睡去" in (get_section(after, "我相信什么") or "")
    assert "还在核对" not in before
    assert "正在核对 L1" in (get_section(after, "我在做什么") or "")
    assert "这条使用假证据" not in after
    assert "<!-- evidence: " + evidence.id in after
    latest = store.self_history()[0]
    assert evidence.id in latest.evidence_refs
