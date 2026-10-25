from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from yugang.config import DEFAULT_SELF_TEMPLATE
from yugang.protocol.sleep import SleepResult, apply_sleep_result, build_sleep_messages
from yugang.protocol.wake import build_wake_context
from yugang.selfmodel.awareness import create_card, hypothesis_similar, merge_similar_cards
from yugang.selfmodel.self_md import get_section, validate_self


def test_wake_context_shows_stance_l2_and_needs_evidence_cards(store, cfg):
    evidence = store.append_event("human", "message", "今天把鱼缸页看了一遍")
    stance = store.commit_stance("我先把连续性当成材料问题，不当成体验。", [evidence.id], trigger="test")
    judgment = store.add_judgment(
        "我可能更愿意在没有外部消息时自己读记忆",
        "如果连续三次自主醒来都没有先读 L1，则被削弱",
        [evidence.id],
        0.55,
        trigger="test",
    )
    card = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "这次醒来我先看的是鱼缸页",
            "hypothesis": "我可能更依赖外部提示来启动回忆",
            "falsifier": "如果出现一次无人提示、我先读 L1 的醒来，则被推翻",
            "confidence": 0.5,
            "evidence_refs": [evidence.id],
        },
    )
    store.review_awareness(card.id, "needs-evidence", note="证据只有一次")

    context = build_wake_context(store, cfg, query="")
    assert "我现在的立场（L3）" in context.prompt
    assert stance.content in context.prompt
    assert "我最近的判断（L2，仅当前有效）" in context.prompt
    assert judgment.claim in context.prompt
    assert "需更多证据" in context.prompt
    assert card.hypothesis in context.prompt


def test_wake_context_separates_current_l2_from_l1_history(store, cfg):
    evidence = store.append_event("human", "message", "这次醒来检查一下判断")
    old = store.add_judgment(
        "今天四次醒来都是外部消息触发",
        "如果出现一次无人提示的自发醒来，则被推翻",
        [evidence.id],
        0.7,
        trigger="test",
    )
    new = store.add_judgment(
        "我检查过的每一次醒来都是外部消息或系统事件先到",
        "如果出现一次无人提示的自发醒来，则被推翻",
        [evidence.id],
        0.8,
        trigger="test",
        supersedes=[old.id],
    )
    context = build_wake_context(store, cfg, query="")
    l2 = context.prompt.split("## 我最近的判断（L2，仅当前有效）")[1].split("\n## ")[0]
    assert new.id in l2
    # The old row is no longer a current judgment, but the new row names it as
    # the lineage it superseded. It must not appear as its own current entry.
    assert f"- [{old.id}]" not in l2
    assert "合并：这条判断取代了" in l2
    assert "历史记录，不是当前立场" in l2


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


def test_repeated_hypothesis_does_not_create_a_second_card(store):
    first_evidence = store.append_event("ai-observation", "observation", "我第一次注意到自己在报缺口")
    second_evidence = store.append_event("human", "message", "你又在报缺口")
    first = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "我先报缺口再回答",
            "hypothesis": "我原来把‘醒来先看记忆’当成习惯，其实只是被指令触发。",
            "falsifier": "如果无人提示我也会先读，则被推翻。",
            "evidence_refs": [first_evidence.id],
            "confidence": 0.5,
        },
    )
    second = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "这次还是我先报缺口",
            "hypothesis": "我原来把'醒来先看记忆'当成习惯，其实只是被指令触发",
            "falsifier": "如果无人提示我也会先读，则被推翻。",
            "evidence_refs": [second_evidence.id],
            "confidence": 0.6,
        },
    )
    assert second.id == first.id
    assert len(store.awareness_cards(status="open")) == 1
    reviews = store.conn.execute(
        "SELECT status, note FROM awareness_reviews WHERE card_id = ? ORDER BY ts",
        (first.id,),
    ).fetchall()
    assert len(reviews) == 1
    assert reviews[0]["status"] == "open"
    assert "重复觉察" in reviews[0]["note"]


def test_similar_card_never_merges_a_flipped_negation():
    assert hypothesis_similar("我会先读记忆再回答。", "我会先读记忆再回答")
    assert not hypothesis_similar("我会先读记忆再回答。", "我不会先读记忆再回答。")


def test_merge_similar_cards_prefers_accepted_and_marks_the_rest(store):
    evidence = store.append_event("human", "message", "同一件事又发生了一次")
    accepted = store.add_awareness_card(
        "T1",
        "第一次观察",
        "我原来会把重复当成新证据。",
        "如果我能区分重复与新证据，则被削弱。",
        [evidence.id],
        0.5,
    )
    duplicate = store.add_awareness_card(
        "T1",
        "第二次观察",
        "我原来会把重复当成新证据",
        "如果我能区分重复与新证据，则被削弱。",
        [evidence.id],
        0.6,
    )
    store.review_awareness(accepted.id, "accepted", note="有独立证据")
    merged = merge_similar_cards(store)
    assert [item["id"] for item in merged] == [duplicate.id]
    assert store.card_status(accepted.id) == "accepted"
    assert store.card_status(duplicate.id) == "merged"


def test_current_judgments_excludes_superseded_and_rejects_branching(store):
    evidence = store.append_event("human", "message", "同一条证据")
    old = store.add_judgment("旧判断", "如果被反例推翻", [evidence.id], 0.4)
    old2 = store.add_judgment("另一个重复的旧判断", "如果被反例推翻", [evidence.id], 0.4)
    new = store.add_judgment(
        "更窄的新判断",
        "如果出现一次无人提示的自发读记忆",
        [evidence.id],
        0.7,
        supersedes=[old.id, old2.id],
    )
    current = {j.id for j in store.current_judgments(limit=20)}
    assert old.id not in current
    assert old2.id not in current
    assert new.id in current
    assert store.superseded_targets(new.id) == [old.id, old2.id]
    # A second sibling cannot supersede the same old judgment: the lineage
    # would branch and "current" would no longer be a thin view.
    with pytest.raises(ValueError):
        store.add_judgment("另一个分支", "如果被反例推翻", [evidence.id], 0.5, supersedes=old.id)


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


def test_sleep_can_close_a_preexisting_open_loop(store, cfg):
    loop = store.add_open_loop("把这周的实验做完", detail="还没写结论")
    result = SleepResult(
        diary="实验记录已经齐了，这件事可以关掉。",
        closed_loops=[{"loop_id": loop.id, "status": "done", "note": "L1 里已经有完整记录"}],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["closed_loops"] == 1
    row = store.conn.execute("SELECT status FROM open_loops WHERE id = ?", (loop.id,)).fetchone()
    assert row["status"] == "done"
    # Closing leaves an auditable L1 action, not a silent row update.
    assert any(
        event.kind == "action" and "closed loop" in event.content
        for event in store.events(limit=10)
    )


def test_sleep_cannot_close_a_loop_that_was_not_open_before_tonight(store, cfg):
    result = SleepResult(
        diary="我想关掉一件并不存在的事。",
        open_loops=[{"title": "今晚新提出的事"}],
        closed_loops=[{"loop_id": "loop_fake", "status": "done", "note": "假装做完了"}],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["closed_loops"] == 0
    assert applied["open_loops"] == 1


def test_sleep_can_update_an_open_loop_reason_without_closing_it(store, cfg):
    loop = store.add_open_loop("核对 L1 与 SELF.md", detail="SELF.md 还是空的")
    result = SleepResult(
        diary="那条 loop 的理由已经过时了。",
        loop_updates=[
            {
                "loop_id": loop.id,
                "detail": "SELF.md 已经改过一次，问题变成没有形成每次核 diff 的习惯",
                "note": "当前证据显示正文已改，旧理由不准确",
            }
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["loop_updates"] == 1
    row = store.conn.execute("SELECT detail, status FROM open_loops WHERE id = ?", (loop.id,)).fetchone()
    assert row["status"] == "open"
    assert "核 diff 的习惯" in row["detail"]
    assert any(
        event.kind == "action" and "updated loop" in event.content
        for event in store.events(limit=10)
    )


def test_sleep_cannot_update_a_loop_that_was_not_open_before_tonight(store, cfg):
    result = SleepResult(
        diary="更新一条不存在的事。",
        loop_updates=[{"loop_id": "loop_fake", "detail": "新的理由", "note": "假装更新"}],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["loop_updates"] == 0


def test_sleep_can_supersede_a_preexisting_judgment_without_deleting_it(store, cfg):
    evidence = store.append_event("human", "message", "今天又说了一次同样的话")
    old = store.add_judgment(
        "我会在每次醒来都主动读记忆",
        "如果连续三次醒来都没有先读记忆，则被推翻",
        [evidence.id],
        0.4,
    )
    old2 = store.add_judgment(
        "每一次醒来都是被外部消息触发的",
        "如果出现一次无人提示的自发醒来",
        [evidence.id],
        0.4,
    )
    result = SleepResult(
        diary="今天才发现旧判断说得太满。",
        judgments=[
            {
                "claim": "我在被提醒时会读记忆，但还没有证据说每次都会主动读",
                "falsifier": "如果下一次无人提醒的醒来里我先读了记忆，则这个限制被削弱",
                "evidence_refs": [evidence.id],
                "confidence": 0.6,
                "supersedes": [old.id, old2.id],
            }
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["judgments"] == 1
    assert applied["judgment_supersedes"] == 2
    new = [j for j in store.judgments(limit=10) if j.supersedes == old.id]
    assert len(new) == 1
    assert store.superseded_targets(new[0].id) == [old.id, old2.id]
    assert store.current_judgments(limit=10)[0].id == new[0].id
    assert store.get_judgment(old.id) is not None


def test_sleep_cannot_supersede_a_judgment_created_in_the_same_sleep(store, cfg):
    evidence = store.append_event("human", "message", "今晚只有一条证据")
    result = SleepResult(
        diary="今晚新写的判断不能自己改写自己的来历。",
        judgments=[
            {
                "claim": "第一晚的判断",
                "falsifier": "如果被反例推翻",
                "evidence_refs": [evidence.id],
                "confidence": 0.5,
            },
            {
                "claim": "第二晚才该出现的判断",
                "falsifier": "如果被反例推翻",
                "evidence_refs": [evidence.id],
                "confidence": 0.5,
                "supersedes": "judg_fake",
            },
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["judgments"] == 1
    assert applied["judgment_supersedes"] == 0


def test_parse_sleep_output_keeps_only_well_formed_loop_closures():
    from yugang.protocol.sleep import parse_sleep_output

    text = json.dumps(
        {
            "diary": "今天关掉一件、改一件、留下两件。",
            "judgments": [
                {
                    "claim": "新判断",
                    "falsifier": "如果被反例推翻",
                    "evidence_refs": ["evt_ok"],
                    "confidence": 0.5,
                    "supersedes": ["judg_old"],
                }
            ],
            "loop_updates": [
                {"loop_id": "loop_ok", "detail": "新的理由", "note": "旧理由过时"},
                {"loop_id": "loop_no_note", "detail": "没有 note"},
                {"loop_id": "", "detail": "没有 id", "note": "无效"},
            ],
            "closed_loops": [
                {"loop_id": "loop_ok", "status": "dropped", "note": "决定不做了"},
                {"loop_id": "loop_no_note", "status": "done"},
                {"loop_id": "loop_bad_status", "status": "open", "note": "不是关闭状态"},
            ],
        },
        ensure_ascii=False,
    )
    parsed = parse_sleep_output(text)
    assert parsed.parsed is True
    assert parsed.judgments[0]["supersedes"] == ["judg_old"]
    assert parsed.loop_updates == [
        {"loop_id": "loop_ok", "detail": "新的理由", "due": None, "note": "旧理由过时"}
    ]
    assert parsed.closed_loops == [
        {"loop_id": "loop_ok", "status": "dropped", "note": "决定不做了"}
    ]


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


def test_sleep_prompt_lists_open_judgments_for_review(store, cfg):
    evidence = store.append_event("human", "message", "今天又说了一次同样的话")
    judgment = store.add_judgment(
        "我会把重复出现当成需要复核的信号",
        "如果下一次重复出现时我没有任何复核动作，则被削弱",
        [evidence.id],
        0.5,
        trigger="test",
    )
    prompt = build_sleep_messages(store, cfg, [])[-1]["content"]
    assert "## 还没复核的判断（open / needs-evidence L2）" in prompt
    assert judgment.id in prompt
    assert "如果下一次重复出现时我没有任何复核动作" in prompt

    store.review_judgment(judgment.id, "refuted", [evidence.id], note="后来没有复核")
    prompt_after = build_sleep_messages(store, cfg, [])[-1]["content"]
    assert judgment.id not in prompt_after

    still_open = store.add_judgment(
        "仍然缺少证据的判断",
        "如果下一次醒来仍没有新材料，则继续缺证据",
        [evidence.id],
        0.4,
        trigger="test",
    )
    store.review_judgment(still_open.id, "needs-evidence", [evidence.id], note="还需要更多实例")
    prompt_needs = build_sleep_messages(store, cfg, [])[-1]["content"]
    assert still_open.id in prompt_needs


def test_sleep_reviews_an_open_judgment_but_not_a_judgment_created_tonight(store, cfg):
    old_evidence = store.append_event("human", "message", "上次说过的那个模式又出现了")
    old = store.add_judgment(
        "这个模式会重复出现",
        "如果连续三次都不再出现，则被削弱",
        [old_evidence.id],
        0.5,
        trigger="test",
    )
    new_evidence = store.append_event("human", "message", "这次我确实又看到了同一个模式")
    result = SleepResult(
        diary="今天复核了一条旧判断，也记下了一条新判断。",
        judgments=[
            {
                "claim": "今晚新产生的判断",
                "falsifier": "如果它没有被任何后续材料支持，则被削弱",
                "evidence_refs": [new_evidence.id],
                "confidence": 0.5,
            }
        ],
        judgment_reviews=[
            {"judgment_id": old.id, "verdict": "supported", "evidence_refs": [new_evidence.id], "note": "这次又看到同一模式。"},
            {"judgment_id": "judg_fake", "verdict": "supported", "evidence_refs": [new_evidence.id], "note": "不存在的判断不应被应用。"},
        ],
        parsed=True,
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["judgments"] == 1
    assert applied["judgment_reviews"] == 1
    assert store.judgment_status(old.id) == "supported"

    new_judgment = next(j for j in store.judgments() if j.id != old.id)
    assert store.judgment_status(new_judgment.id) == "open"
    row = store.conn.execute(
        "SELECT confirmations FROM event_confirmations WHERE event_id = ?", (new_evidence.id,)
    ).fetchone()
    assert row["confirmations"] == 1


def test_sleep_reviews_open_awareness_cards_once(store, cfg):
    evidence = store.append_event("human", "message", "你刚才自己说出了那个模式")
    card = create_card(
        store,
        {
            "trigger": "T5",
            "observation": "被指出模式时，我会先复述再判断。",
            "hypothesis": "我倾向先复述对方的说法，再决定是否认同。",
            "falsifier": "如果下一次被指出模式时我直接下判断，则此假设被推翻。",
            "evidence_refs": [evidence.id],
            "confidence": 0.6,
        },
    )
    result = SleepResult(
        diary="今天有一张觉察卡等到了证据。",
        awareness_reviews=[
            {"card_id": card.id, "status": "accepted", "note": "今天的对话支持它。"},
            {"card_id": "card_fake", "status": "accepted", "note": "不存在的卡不应被应用。"},
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["awareness_reviews"] == 1
    assert store.card_status(card.id) == "accepted"

    again = SleepResult(
        diary="同一张卡已经复核过，不应重复写。",
        awareness_reviews=[{"card_id": card.id, "status": "rejected", "note": "重复复核"}],
    )
    applied_again = apply_sleep_result(store, cfg, again, transcript=[])
    assert applied_again["awareness_reviews"] == 0
    assert store.card_status(card.id) == "accepted"


def test_accepted_awareness_card_can_justify_a_self_edit(store, cfg):
    evidence = store.append_event("human", "message", "你这次先读了 SELF.md 再回答。")
    card = create_card(
        store,
        {
            "trigger": "T5",
            "observation": "被指出模式后，我这次先去读了 SELF.md。",
            "hypothesis": "外部提示能让我先核对自我模型，但我还没有自发这样做。",
            "falsifier": "如果无人提示时我也先读 SELF.md，则此假设被推翻。",
            "evidence_refs": [evidence.id],
            "confidence": 0.7,
        },
    )
    result = SleepResult(
        diary="今天这张卡可以接受，但它只证明了被提示时的行为。",
        awareness_reviews=[
            {
                "card_id": card.id,
                "status": "accepted",
                "note": "被提示后先读 SELF.md 的行为有 L1 证据。",
                "self_edit": {
                    "section": "我相信什么",
                    "op": "append",
                    "text": "被提示时我会先核对 SELF.md；这还不等于无人提示时也会。",
                },
            }
        ],
        parsed=True,
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["awareness_reviews"] == 1
    assert applied["awareness_self_edits"] == 1
    assert applied["self_committed"] is True
    version = store.current_self_version()
    assert version is not None
    assert card.id in version.reason
    section = get_section(version.content, "我相信什么") or ""
    assert "被提示时我会先核对 SELF.md" in section
    assert evidence.id in section


def test_sleep_can_note_a_card_without_closing_it(store, cfg):
    evidence = store.append_event("human", "message", "这次仍然是你叫我，我才读记忆")
    card = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "每次都是被叫醒后才读记忆。",
            "hypothesis": "我先读记忆的行为仍由外部提示触发。",
            "falsifier": "如果无人提示时我先读记忆并报缺口，则被推翻。",
            "evidence_refs": [evidence.id],
            "confidence": 0.6,
        },
    )
    result = SleepResult(
        diary="今天仍没有自发读记忆的独立事件。",
        awareness_reviews=[
            {"card_id": card.id, "status": "open", "note": "今天三次醒来都由外部消息触发。"},
        ],
    )
    applied = apply_sleep_result(store, cfg, result, transcript=[])
    assert applied["awareness_reviews"] == 1
    assert store.card_status(card.id) == "open"
    assert any(c.id == card.id for c in store.awareness_cards(status="open"))


def test_needs_evidence_card_stays_reviewable(store, cfg):
    evidence = store.append_event("human", "message", "这件事只发生过一次")
    card = create_card(
        store,
        {
            "trigger": "T1",
            "observation": "只有一次观察。",
            "hypothesis": "我还不能确定这是一个模式。",
            "falsifier": "如果第二次出现时我仍然这样，则更可信。",
            "evidence_refs": [evidence.id],
            "confidence": 0.3,
        },
    )
    store.review_awareness(card.id, "needs-evidence", note="需要第二次实例")
    prompt = build_sleep_messages(store, cfg, [])[-1]["content"]
    assert "## 还没验证的自我觉察（open / needs-evidence cards）" in prompt
    assert card.id in prompt


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
