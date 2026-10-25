from __future__ import annotations

import json
from pathlib import Path

from yugang.audit import audit_memory, memory_health
from yugang.ids import utcnow
from yugang.selfmodel.self_md import replace_section


def _write_backup_manifest(cfg, store, *, age_hours: float = 1.0) -> Path:
    from datetime import timedelta

    directory = Path(cfg.home) / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    created = (utcnow() - timedelta(hours=age_hours)).isoformat()
    path = directory / "yugang-test.manifest.json"
    (directory / "yugang-test.db").write_bytes(b"SQLite format 3\x00")
    path.write_text(
        json.dumps(
            {
                "created_at": created,
                "events": store.event_count(),
                "db_integrity": "ok",
                "chain_ok": True,
                "self_version": store.current_self_version().id if store.current_self_version() else None,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_audit_reports_a_healthy_body(store, cfg):
    event = store.append_event("human", "message", "我今天看见你了。", channel="http")
    store.add_judgment("我能读到这条消息。", "如果读不到，则被推翻。", [event.id], 0.8)
    store.add_practice("遇到引用旧记忆时先核对 L1。", evidence_refs=[event.id])
    store.add_open_loop("核对这条记忆", detail="等下一次醒来", refs=[event.id])
    store.add_episode(
        "一次短暂的清醒",
        "我读到一句话，并把它记了下来。",
        start_ts=event.ts,
        end_ts=utcnow().isoformat(),
        event_ids=[event.id],
        source_event=event.id,
    )
    _write_backup_manifest(cfg, store)

    report = audit_memory(store, cfg)
    assert report["ok"] is True
    assert report["events"]["chain_ok"] is True
    assert report["self"]["version"]
    assert report["judgments"]["current"] == 1
    assert report["practices"]["active"] == 1
    assert report["episodes"]["count"] == 1
    assert report["open_loops"]["open"] == 1
    assert report["backup"]["valid"] is True
    section = next(item for item in report["self"]["sections"] if item["name"] == "我相信什么")
    assert section["chars"] > 0


def test_audit_flags_a_manifest_whose_database_is_missing(store, cfg):
    path = _write_backup_manifest(cfg, store)
    (path.parent / "yugang-test.db").unlink()

    report = audit_memory(store, cfg)
    assert report["ok"] is False
    assert report["backup"]["db_exists"] is False
    assert "数据库文件不存在" in "\n".join(report["problems"])


def test_audit_flags_missing_and_redacted_self_evidence(store, cfg):
    event = store.append_event("human", "message", "这是一条以后会被删掉的证据。")
    current = store.current_self() or ""
    current = replace_section(
        current,
        "我相信什么",
        "- 我记得这句话。\n\n<!-- evidence: " + event.id + " -->",
    )
    store.commit_self(current, "引用一条即将被删除的证据", [event.id])
    store.redact_event(event.id, "用户要求删除")
    _write_backup_manifest(cfg, store)

    report = audit_memory(store, cfg)
    assert report["ok"] is False
    text = "\n".join(report["problems"])
    assert "已删除内容的证据" in text
    section = next(item for item in report["self"]["sections"] if item["name"] == "我相信什么")
    assert event.id in section["evidence"]["redacted"]


def test_audit_flags_a_broken_l1_chain_and_missing_judgment_evidence(store, cfg):
    event = store.append_event("human", "message", "一条正常消息。")
    store.conn.execute(
        "INSERT INTO judgments(id, ts, claim, falsifier, evidence_refs, confidence, trigger, supersedes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "judg_bad_evidence",
            utcnow().isoformat(),
            "一条引用了不存在证据的判断。",
            "如果证据不存在，则被推翻。",
            json.dumps(["evt_does_not_exist"]),
            0.7,
            "test",
            None,
            utcnow().isoformat(),
        ),
    )
    store.conn.execute(
        "INSERT INTO events(id, ts, source, kind, actor, channel, content, refs, meta, created_at, prev_hash, hash) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "evt_broken",
            utcnow().isoformat(),
            "system",
            "note",
            "test",
            None,
            "tampered",
            "[]",
            "{}",
            utcnow().isoformat(),
            "not-the-previous-hash",
            "not-the-right-hash",
        ),
    )
    _write_backup_manifest(cfg, store)

    report = audit_memory(store, cfg)
    assert report["ok"] is False
    assert report["events"]["chain_ok"] is False
    assert report["events"]["bad_event"] == "evt_broken"
    assert report["judgments"]["bad_evidence"]
    assert "哈希链断裂" in "\n".join(report["problems"])

def test_audit_warns_about_duplicate_current_judgments(store, cfg):
    event = store.append_event("human", "message", "两条措辞几乎相同的判断。")
    first = store.add_judgment(
        "这次醒来仍是被外部消息触发的。",
        "如果出现一次无人提示的自发醒来，则被推翻。",
        [event.id],
        0.7,
        trigger="test",
    )
    second = store.add_judgment(
        "这次醒来仍是被外部消息触发的。",
        "如果出现一次无人提示的自发醒来，则被推翻。",
        [event.id],
        0.7,
        trigger="test",
    )
    _write_backup_manifest(cfg, store)

    report = audit_memory(store, cfg)
    assert report["ok"] is True
    assert any("措辞高度相似" in item for item in report["warnings"])
    groups = report["judgments"]["duplicate_groups"]
    assert any(first.id in group and second.id in group for group in groups)


def test_memory_health_is_a_compact_projection(store, cfg):
    event = store.append_event("human", "message", "一次可体检的醒来。")
    store.add_judgment("我记得这条消息。", "如果查不到这条消息，则被推翻。", [event.id], 0.7)
    _write_backup_manifest(cfg, store)

    health = memory_health(store, cfg)
    assert health["healthy"] is True
    assert health["chain_ok"] is True
    assert health["bad_event"] is None
    assert health["events"] == store.event_count()
    assert health["judgments"] == 1
    assert health["problem_count"] == 0
    assert health["backup"]["db_exists"] is True
    assert health["backup"]["integrity"] == "ok"
    assert {
        "healthy",
        "events",
        "chain_ok",
        "self_version",
        "judgments",
        "open_cards",
        "active_practices",
        "episodes",
        "open_loops",
        "backup",
        "problems",
        "warnings",
        "problem_count",
        "warning_count",
    } <= set(health)
