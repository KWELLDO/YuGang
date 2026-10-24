from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from yugang.config import Config
from yugang.memory.store import MemoryStore
from yugang.selfmodel.self_md import bootstrap_self


def _run(home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "yugang", "--home", str(home), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _backup(home: Path) -> dict:
    proc = _run(home, "backup", "--keep", "0")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    return {
        "db": payload["db"]["path"],
        "manifest": payload["manifest"],
        "self": payload["self"],
    }


def test_restore_round_trip_into_a_fresh_body(tmp_path: Path) -> None:
    cfg = Config.default(str(tmp_path / "body"))
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    before = store.append_event("human", "message", "备份之前的一句话", actor="kwelldo")
    self_text = store.current_self()
    store.close()

    saved = _backup(cfg.home)
    manifest = json.loads(Path(saved["manifest"]).read_text(encoding="utf-8"))

    store = MemoryStore(cfg.db_path)
    store.append_event("human", "message", "备份之后才发生的事", actor="kwelldo")
    store.close()

    target = Config.default(str(tmp_path / "restored"))
    proc = _run(Path(target.home), "restore", "--from", saved["db"])
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout)
    assert result["ok"] is True
    assert result["restored"] is True
    assert result["events_before_note"] == manifest["events"]
    assert result["events_after_note"] == manifest["events"] + 1
    assert result["chain_ok"] is True
    assert result["restore_note"]

    restored = MemoryStore(target.db_path)
    try:
        assert restored.get_event(before.id) is not None
        contents = [event.content for event in restored.events(limit=200)]
        assert "备份之前的一句话" in contents
        assert "备份之后才发生的事" not in contents
        assert restored.verify_chain() == (True, None)
        assert any("操作员从备份恢复身体" in text for text in contents)
    finally:
        restored.close()
    assert Path(target.self_path).read_text(encoding="utf-8") == self_text


def test_restore_refuses_to_overwrite_and_can_force(tmp_path: Path) -> None:
    cfg = Config.default(str(tmp_path / "body"))
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    store.append_event("human", "message", "原始身体的一句话", actor="kwelldo")
    store.close()
    saved = _backup(cfg.home)
    manifest = json.loads(Path(saved["manifest"]).read_text(encoding="utf-8"))

    target = Config.default(str(tmp_path / "restored"))
    assert _run(Path(target.home), "restore", "--from", saved["db"]).returncode == 0
    store = MemoryStore(target.db_path)
    base_events = store.event_count()
    store.append_event("human", "message", "恢复之后的新事件", actor="kwelldo")
    store.close()

    dry = _run(Path(target.home), "restore", "--from", saved["db"], "--dry-run")
    assert dry.returncode == 0, dry.stderr
    assert json.loads(dry.stdout)["dry_run"] is True
    store = MemoryStore(target.db_path)
    assert store.event_count() == base_events + 1
    store.close()

    refused = _run(Path(target.home), "restore", "--from", saved["db"])
    assert refused.returncode == 1
    assert "already exists" in refused.stderr
    store = MemoryStore(target.db_path)
    assert store.event_count() == base_events + 1
    store.close()

    forced = _run(Path(target.home), "restore", "--from", saved["db"], "--force")
    assert forced.returncode == 0, forced.stderr
    result = json.loads(forced.stdout)
    assert result["pre_restore_backup"]
    assert Path(result["pre_restore_backup"]).exists()
    assert result["events_before_note"] == manifest["events"]
    assert result["events_after_note"] == manifest["events"] + 1
    store = MemoryStore(target.db_path)
    contents = [event.content for event in store.events(limit=200)]
    assert "恢复之后的新事件" not in contents
    assert store.verify_chain() == (True, None)
    store.close()


def test_restore_rejects_a_truncated_backup_without_touching_target(tmp_path: Path) -> None:
    cfg = Config.default(str(tmp_path / "body"))
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    store.close()
    saved = _backup(cfg.home)
    bad = Path(saved["db"]).with_name("truncated.db")
    data = Path(saved["db"]).read_bytes()
    bad.write_bytes(data[: max(128, len(data) // 2)])

    target = Config.default(str(tmp_path / "restored"))
    proc = _run(Path(target.home), "restore", "--from", str(bad))
    assert proc.returncode == 1
    assert not Path(target.db_path).exists()
