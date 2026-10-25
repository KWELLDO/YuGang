from __future__ import annotations

from conftest import make_service

from yugang.memory.tools import MemoryToolbox
from yugang.protocol.sleep import build_sleep_messages
from yugang.protocol.wake import build_wake_context


def _judgment(store, evidence, claim, *, confidence=0.7):
    return store.add_judgment(
        claim,
        "如果出现反例，则被推翻",
        [evidence.id],
        confidence,
        trigger="test",
    )


def test_review_counts_count_only_supported_and_appear_on_wake(store, cfg):
    evidence = store.append_event("human", "message", "今天检查一条旧判断")
    judgment = _judgment(store, evidence, "我会在醒来时先读 SELF.md")
    store.review_judgment(judgment.id, "supported", [evidence.id], note="第一次确认")
    store.review_judgment(judgment.id, "supported", [evidence.id], note="第二次确认")
    store.review_judgment(judgment.id, "refuted", [evidence.id], note="后来出现反例")
    store.review_judgment(judgment.id, "needs-evidence", [evidence.id], note="证据不够")

    counts = store.judgment_review_counts([judgment.id])[judgment.id]
    assert counts["supported"] == 2
    assert counts["refuted"] == 1
    assert counts["needs-evidence"] == 1
    assert counts["total"] == 4
    assert counts["last"]["verdict"] == "needs-evidence"

    context = build_wake_context(store, cfg, query="")
    assert "未来自己确认过 2 次" in context.prompt
    assert "不代表客观真相" in context.prompt


def test_duplicate_groups_are_hints_and_skip_superseded_rows(store):
    evidence = store.append_event("human", "message", "记录几次重复的判断")
    first = _judgment(store, evidence, "这次醒来仍是被外部消息触发的。")
    second = _judgment(store, evidence, "这次醒来仍是被外部消息触发的。")
    different = _judgment(store, evidence, "邮件通道已经能收到消息。")

    groups = store.judgment_duplicate_groups(threshold=0.9)
    ids = {judgment.id for group in groups for judgment in group}
    assert first.id in ids and second.id in ids
    assert different.id not in ids

    merged = _judgment(store, evidence, "这些醒来都是外部消息触发的。")
    # The helper cannot supersede after creation; build the lineage directly the
    # same way sleep does.
    store.conn.execute(
        "INSERT INTO judgment_supersessions(new_id, old_id, created_at) VALUES (?, ?, ?)",
        (merged.id, first.id, merged.created_at),
    )
    current = {j.id for j in store.current_judgments(limit=100)}
    assert first.id not in current
    groups = store.judgment_duplicate_groups(threshold=0.9)
    assert first.id not in {judgment.id for group in groups for judgment in group}


def test_sleep_prompt_shows_reconfirmations_and_duplicate_hint(store, cfg):
    evidence = store.append_event("human", "message", "睡前看两条重复判断")
    first = _judgment(store, evidence, "这次醒来仍是被外部消息触发的。")
    _judgment(store, evidence, "这次醒来仍是被外部消息触发的。")
    store.review_judgment(first.id, "supported", [evidence.id], note="这次确认它")
    # A later review that still needs evidence keeps the judgment reviewable
    # while preserving the earlier explicit reconfirmation.
    store.review_judgment(first.id, "needs-evidence", [evidence.id], note="还需要更多证据")

    messages = build_sleep_messages(store, cfg, [])
    prompt = messages[-1]["content"]
    assert f"[{first.id}]" in prompt
    assert "reconfirmed=1" in prompt
    assert "last_review=needs-evidence" in prompt
    assert "## 可能重复的当前判断" in prompt
    assert "如果你今晚只是想再次确认它" in prompt


def test_judgment_list_tool_reports_reconfirmations(store, cfg):
    evidence = store.append_event("human", "message", "工具读取确认次数")
    judgment = _judgment(store, evidence, "我能区分记录和体验")
    store.review_judgment(judgment.id, "supported", [evidence.id], note="确认")

    toolbox = MemoryToolbox(store, cfg)
    result = toolbox.run("judgment_list", {"limit": 5})
    row = next(item for item in result["judgments"] if item["judgment_id"] == judgment.id)
    assert row["reconfirmations"] == 1
    assert row["review_counts"]["supported"] == 1
    assert row["last_review"]["verdict"] == "supported"


def test_tank_exposes_reconfirmations_for_the_page(store, cfg):
    evidence = store.append_event("human", "message", "鱼缸页显示确认次数")
    judgment = _judgment(store, evidence, "我会把当前状态当成历史证据")
    store.review_judgment(judgment.id, "supported", [evidence.id], note="确认")
    service, _ = make_service(cfg, store)
    tank = service.tank()
    row = next(item for item in tank["judgments"] if item["id"] == judgment.id)
    assert row["reconfirmations"] == 1
    assert row["reviews"]["supported"] == 1
