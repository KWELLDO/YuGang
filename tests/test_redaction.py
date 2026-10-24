from __future__ import annotations

import pytest


def test_redaction_hides_content_without_rewriting_the_chain(store):
    secret = store.append_event("human", "message", "我的手机号是 13800000000，别记下来", actor="kwelldo")
    keep = store.append_event("human", "message", "这条普通消息应该还在")

    info = store.redact_event(secret.id, "用户要求删除个人信息")
    assert info["target_event_id"] == secret.id

    got = store.get_event(secret.id)
    assert got is not None
    assert got.content == "[已删除]"
    assert got.meta["redacted"] is True
    assert store.get_event(keep.id).content == "这条普通消息应该还在"

    # The raw bytes stay for chain verification; normal reads do not expose them.
    raw = store.conn.execute("SELECT content FROM events WHERE id = ?", (secret.id,)).fetchone()
    assert "13800000000" in raw["content"]
    assert store.verify_chain() == (True, None)

    # Redacted material must not come back through recall, even if FTS indexed it.
    hits = store.recall("13800000000", log=False)
    assert all(hit.event.id != secret.id for hit in hits)
    assert all("13800000000" not in hit.event.content for hit in hits)

    note = store.get_event(info["note_event"])
    assert note is not None
    assert secret.id in note.refs
    assert "可读内容" in note.content

    with pytest.raises(ValueError):
        store.redact_event(secret.id, "重复删除")
    with pytest.raises(ValueError):
        store.redact_event("evt_missing", "不存在")


def test_redaction_requires_a_reason(store):
    event = store.append_event("human", "message", "一条普通消息")
    with pytest.raises(ValueError):
        store.redact_event(event.id, "   ")
