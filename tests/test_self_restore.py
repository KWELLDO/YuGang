from __future__ import annotations

from pathlib import Path

import pytest

from yugang.cli import main


def test_self_restore_creates_a_new_version_without_deleting_history(store):
    first = store.current_self_version()
    assert first is not None
    evidence = store.append_event("human", "message", "这次修订有真实材料")

    second = store.commit_self(
        first.content + "\n- 测试修订。\n",
        "测试修订",
        [evidence.id],
    )
    assert store.current_self_version().id == second.id

    restored = store.restore_self(first.id, "那次修订证据不足")
    assert restored.content == first.content
    assert "回滚到" in restored.reason
    assert restored.id not in (first.id, second.id)
    assert store.current_self_version().id == restored.id
    assert len(store.self_history(limit=10)) == 3
    assert store.get_self_version(second.id) is not None
    assert store.verify_chain() == (True, None)

    note = store.latest_event("action")
    assert note is not None
    assert first.id in note.refs
    assert "恢复" in note.content

    with pytest.raises(ValueError):
        store.restore_self(first.id, "已经恢复过了")
    with pytest.raises(ValueError):
        store.restore_self("self_missing", "不存在")
    with pytest.raises(ValueError):
        store.restore_self(second.id, "  ")


def test_self_restore_cli_writes_the_self_file(store, cfg):
    first = store.current_self_version()
    evidence = store.append_event("human", "message", "一条用于修订的材料")
    store.commit_self(first.content + "\n- 临时修订。\n", "临时修订", [evidence.id])
    assert main(["--home", cfg.home, "self-restore", first.id, "--reason", "CLI 测试"]) == 0
    assert Path(cfg.self_path).read_text(encoding="utf-8") == first.content
