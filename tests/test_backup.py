from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from yugang.config import Config
from yugang.memory.store import MemoryStore
from yugang.selfmodel.self_md import bootstrap_self


def test_backup_is_consistent_and_readable(tmp_path: Path) -> None:
    cfg = Config.default(str(tmp_path / "body"))
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    event = store.append_event("human", "message", "备份之前的一句话", actor="kwelldo")
    store.close()

    store = MemoryStore(cfg.db_path)
    result = store.backup(tmp_path / "backups")
    assert result["integrity"] == "ok"
    assert Path(result["path"]).exists()
    assert result["events"] >= 3

    copy = MemoryStore(result["path"])
    assert copy.get_event(event.id) is not None
    assert copy.get_event(event.id).content == "备份之前的一句话"
    assert copy.verify_chain() == (True, None)
    copy.close()
    store.close()


def test_backup_manifest_and_self_copy(tmp_path: Path) -> None:
    import subprocess
    import sys

    cfg = Config.default(str(tmp_path / "body"))
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    bootstrap_self(store, cfg)
    store.close()

    env = dict(**__import__("os").environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "yugang", "--home", cfg.home, "backup", "--keep", "0"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] is True
    manifest = json.loads(Path(payload["manifest"]).read_text(encoding="utf-8"))
    assert manifest["chain_ok"] is True
    assert manifest["self_version"]
    assert Path(payload["self"]).read_text(encoding="utf-8").startswith("# SELF.md")
