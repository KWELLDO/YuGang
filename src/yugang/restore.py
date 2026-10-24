from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ids import now_iso
from .memory.store import MemoryStore


class BackupError(RuntimeError):
    """A backup is missing, inconsistent, or unsafe to restore."""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BackupSource:
    db: Path
    self_md: Path | None
    manifest: Path | None
    manifest_data: dict[str, Any] | None


def resolve_backup(source: str | Path) -> BackupSource:
    """Resolve a backup db, a manifest, or a backup directory to its files."""
    path = Path(source).expanduser()
    if path.is_dir():
        candidates = sorted(
            (candidate for candidate in path.glob("*.db") if candidate.is_file()),
            key=lambda candidate: candidate.stat().st_mtime,
        )
        if not candidates:
            raise BackupError(f"no .db backup in {path}")
        path = candidates[-1]
    elif path.name.endswith(".manifest.json"):
        stem = path.name[: -len(".manifest.json")]
        path = path.with_name(f"{stem}.db")
    elif path.name.endswith(".SELF.md"):
        path = path.with_name(path.name[: -len(".SELF.md")] + ".db")

    if not path.exists() or not path.is_file():
        raise BackupError(f"backup db not found: {path}")

    stem = path.stem
    self_md = path.with_name(f"{stem}.SELF.md")
    manifest = path.with_name(f"{stem}.manifest.json")
    manifest_data: dict[str, Any] | None = None
    if manifest.exists():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupError(f"cannot read backup manifest {manifest}: {exc}") from exc
    return BackupSource(
        db=path,
        self_md=self_md if self_md.exists() else None,
        manifest=manifest if manifest.exists() else None,
        manifest_data=manifest_data,
    )


def verify_backup(source: str | Path) -> dict[str, Any]:
    """Check integrity, hash, event count, and the L1 chain without touching the source."""
    backup = resolve_backup(source)
    problems: list[str] = []
    db_sha256 = _sha256_file(backup.db)

    try:
        con = sqlite3.connect(f"file:{backup.db}?mode=ro", uri=True)
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            integrity = str(row[0]) if row else "unknown"
            events = int(con.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            con.close()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"cannot open backup database {backup.db}: {exc}") from exc

    if integrity != "ok":
        problems.append(f"integrity_check={integrity}")

    # Open a disposable copy: MemoryStore may create WAL/shm files while it
    # verifies the chain, and the backup itself must stay untouched.
    with tempfile.TemporaryDirectory(prefix="yugang-verify-") as tmp:
        copy = Path(tmp) / "backup.db"
        shutil.copy2(backup.db, copy)
        store = MemoryStore(copy)
        try:
            chain_ok, bad_event = store.verify_chain()
            current = store.current_self_version()
            copy_events = store.event_count()
        finally:
            store.close()
    if copy_events != events:
        problems.append(f"event count changed while reading: {events} -> {copy_events}")
    if not chain_ok:
        problems.append(f"L1 chain broken at {bad_event}")

    manifest = backup.manifest_data
    if manifest:
        if manifest.get("db_sha256") and manifest["db_sha256"] != db_sha256:
            problems.append("db_sha256 does not match manifest")
        if manifest.get("events") is not None and int(manifest["events"]) != events:
            problems.append(f"events={events} does not match manifest {manifest['events']}")
        if manifest.get("db_integrity") and manifest["db_integrity"] != integrity:
            problems.append(f"integrity={integrity} does not match manifest {manifest['db_integrity']}")
        if backup.self_md is not None and manifest.get("self_md_sha256"):
            if _sha256_file(backup.self_md) != manifest["self_md_sha256"]:
                problems.append("SELF.md sha256 does not match manifest")

    return {
        "ok": not problems,
        "problems": problems,
        "db": str(backup.db),
        "self_md": str(backup.self_md) if backup.self_md else None,
        "manifest": str(backup.manifest) if backup.manifest else None,
        "db_sha256": db_sha256,
        "integrity": integrity,
        "events": events,
        "chain_ok": chain_ok,
        "chain_bad_event": bad_event,
        "self_version": current.id if current else None,
    }


def _atomic_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.restore-{os.getpid()}")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(mode)
    os.replace(tmp, path)


def _remove_sidecars(db_path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists():
            sidecar.unlink()


def restore_backup(
    config: Any,
    source: str | Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Restore a verified backup into config's body.

    The gateway must be stopped first. Without force, an existing target db is
    never overwritten. With force, the current body is backed up first and the
    restore is rolled back if the restored chain does not verify.
    """
    verified = verify_backup(source)
    if not verified["ok"]:
        raise BackupError("backup did not verify: " + "; ".join(verified["problems"]))

    target_db = Path(config.db_path).expanduser()
    target_self = Path(config.self_path).expanduser()
    target_db.parent.mkdir(parents=True, exist_ok=True)
    target_self.parent.mkdir(parents=True, exist_ok=True)
    existed = target_db.exists()
    if dry_run:
        return {**verified, "dry_run": True, "target_db": str(target_db), "target_self": str(target_self)}
    if existed and not force:
        raise FileExistsError(f"target body already exists: {target_db} (use --force to replace it)")

    previous_self_file = target_self.read_text(encoding="utf-8") if target_self.exists() else None
    pre_backup: str | None = None
    if existed:
        current = MemoryStore(target_db)
        try:
            snapshot = current.backup(Path(config.home) / "backups")
            pre_path = Path(snapshot["path"])
            version = current.current_self_version()
            if version is not None:
                pre_self = pre_path.with_name(f"{pre_path.stem}.SELF.md")
                pre_self.write_text(version.content, encoding="utf-8")
                pre_self.chmod(0o600)
            pre_backup = str(pre_path)
        finally:
            current.close()

    backup = resolve_backup(verified["db"])
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    tmp_db = target_db.with_name(f".{target_db.name}.restore-{stamp}-{os.getpid()}")
    shutil.copy2(backup.db, tmp_db)
    tmp_db.chmod(0o600)
    if tmp_db.stat().st_size == 0:
        tmp_db.unlink(missing_ok=True)
        raise BackupError("backup database is empty")

    try:
        _remove_sidecars(target_db)
        os.replace(tmp_db, target_db)
        store = MemoryStore(target_db)
        try:
            chain_ok, bad_event = store.verify_chain()
            if not chain_ok:
                raise BackupError(f"restored L1 chain broken at {bad_event}")
            version = store.current_self_version()
            if version is not None:
                content = version.content
                self_origin = "database"
            elif backup.self_md is not None:
                content = backup.self_md.read_text(encoding="utf-8")
                self_origin = "backup_file"
            elif previous_self_file is not None:
                content = previous_self_file
                self_origin = "previous_file"
            else:
                content = None
                self_origin = "missing"
            if content is not None:
                _atomic_write(target_self, content)
            restored_events = store.event_count()
            note = store.append_event(
                "system",
                "note",
                (
                    f"操作员从备份恢复身体：{backup.db.name}（备份时 {verified['events']} 条事件，"
                    f"SELF {verified['self_version'] or '无'}）；恢复前身体已备份到 {pre_backup or '无'}。"
                ),
                meta={
                    "restore": True,
                    "source_db": str(backup.db),
                    "source_manifest": str(backup.manifest) if backup.manifest else None,
                    "source_events": verified["events"],
                    "pre_restore_backup": pre_backup,
                    "self_origin": self_origin,
                },
            )
            chain_after, bad_after = store.verify_chain()
            if not chain_after:
                raise BackupError(f"L1 chain broken after recording the restore at {bad_after}")
            events_after = store.event_count()
            current_after = store.current_self_version()
        finally:
            store.close()
    except Exception:
        # Leave a usable body if anything failed after the swap.
        if pre_backup is not None:
            shutil.copy2(pre_backup, target_db)
        elif not existed:
            target_db.unlink(missing_ok=True)
        _remove_sidecars(target_db)
        if previous_self_file is not None:
            _atomic_write(target_self, previous_self_file)
        elif not existed:
            target_self.unlink(missing_ok=True)
        raise

    return {
        **verified,
        "restored": True,
        "target_db": str(target_db),
        "target_self": str(target_self) if content is not None else None,
        "self_origin": self_origin,
        "pre_restore_backup": pre_backup,
        "restore_note": note.id,
        "events_before_note": restored_events,
        "events_after_note": events_after,
        "self_version_after": current_after.id if current_after else None,
        "restored_at": now_iso(),
    }
