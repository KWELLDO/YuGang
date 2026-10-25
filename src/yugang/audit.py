from __future__ import annotations

"""Read-only health audit for her memory and self-model.

The audit never repairs or rewrites anything. It reports what a future reader
would have trouble trusting: a broken L1 chain, evidence ids that no longer
resolve, a judgment without a falsifier, an episode pointing at missing events,
or a backup that is too old to be a real fallback.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .ids import parse_ts, utcnow
from .selfmodel.persona_check import check_persona
from .selfmodel.self_md import CANONICAL_SECTIONS, get_section, section_evidence


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return max(0.0, (now - parse_ts(ts)).total_seconds() / 3600.0)
    except (TypeError, ValueError):
        return None


def _event_status(store: Any, ref: str) -> str:
    """Return ok / missing / redacted for one L1 event id."""
    row = store.conn.execute("SELECT id FROM events WHERE id = ?", (ref,)).fetchone()
    if row is None:
        return "missing"
    redacted = store.conn.execute(
        "SELECT 1 FROM redactions WHERE target_event_id = ?", (ref,)
    ).fetchone()
    return "redacted" if redacted else "ok"


def _ref_status(store: Any, ref: str) -> str:
    if ref.startswith("evt_"):
        return _event_status(store, ref)
    return "ok" if store.ref_exists(ref) else "missing"


def _check_refs(store: Any, refs: list[str]) -> dict[str, list[str]]:
    missing: list[str] = []
    redacted: list[str] = []
    for ref in refs:
        status = _ref_status(store, ref)
        if status == "missing":
            missing.append(ref)
        elif status == "redacted":
            redacted.append(ref)
    return {"checked": len(refs), "missing": missing, "redacted": redacted}


def _latest_backup(config: Any, now: datetime) -> dict[str, Any] | None:
    directory = Path(getattr(config, "home", "") or ".").expanduser() / "backups"
    if not directory.is_dir():
        return None
    manifests = sorted(directory.glob("*.manifest.json"), key=lambda p: p.stat().st_mtime)
    if not manifests:
        return None
    path = manifests[-1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"path": str(path), "valid": False, "error": f"{type(exc).__name__}: {exc}"}
    created = data.get("created_at")
    db_path = path.with_name(path.name[: -len(".manifest.json")] + ".db")
    try:
        db_bytes = db_path.stat().st_size
    except OSError:
        db_bytes = None
    return {
        "path": str(path),
        "valid": True,
        "db_path": str(db_path),
        "db_exists": db_path.is_file(),
        "db_bytes": db_bytes,
        "created_at": created,
        "age_hours": _age_hours(created, now),
        "events": data.get("events"),
        "integrity": data.get("db_integrity"),
        "chain_ok": data.get("chain_ok"),
        "self_version": data.get("self_version"),
    }


def audit_memory(store: Any, config: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Build a read-only health report. Never writes to L1 or the DB."""
    now = now or utcnow()
    problems: list[str] = []
    warnings: list[str] = []

    chain_ok, bad_event = store.verify_chain()
    first = store.events(limit=1, order="asc")
    last = store.events(limit=1, order="desc")
    events = {
        "count": store.event_count(),
        "chain_ok": chain_ok,
        "bad_event": bad_event,
        "first_ts": first[0].ts if first else None,
        "last_ts": last[0].ts if last else None,
    }
    if not chain_ok:
        problems.append(f"L1 哈希链断裂：{bad_event}")

    current_self = store.current_self_version()
    self_sections: list[dict[str, Any]] = []
    if current_self is None:
        problems.append("没有 SELF.md 版本")
    else:
        for name in CANONICAL_SECTIONS:
            text = get_section(current_self.content, name) or ""
            refs = section_evidence(current_self.content, name)
            status = _check_refs(store, refs)
            if status["missing"]:
                problems.append(f"SELF「{name}」引用了不存在的证据：{', '.join(status['missing'])}")
            if status["redacted"]:
                problems.append(f"SELF「{name}」引用了已删除内容的证据：{', '.join(status['redacted'])}")
            self_sections.append(
                {
                    "name": name,
                    "chars": len(text),
                    "has_evidence": bool(refs),
                    "evidence": status,
                }
            )
        version_refs = _check_refs(store, list(current_self.evidence_refs))
        if version_refs["missing"]:
            problems.append(f"SELF 版本引用了不存在的证据：{', '.join(version_refs['missing'])}")
        if version_refs["redacted"]:
            problems.append(f"SELF 版本引用了已删除内容的证据：{', '.join(version_refs['redacted'])}")
    self_report = {
        "version": current_self.id if current_self else None,
        "ts": current_self.ts if current_self else None,
        "reason": current_self.reason if current_self else None,
        "sections": self_sections,
        "evidence": _check_refs(store, list(current_self.evidence_refs)) if current_self else None,
    }

    current_judgments = store.current_judgments(limit=1000)
    bad_falsifiers: list[str] = []
    bad_evidence: list[dict[str, Any]] = []
    for judgment in current_judgments:
        if not str(judgment.falsifier or "").strip():
            bad_falsifiers.append(judgment.id)
        status = _check_refs(store, list(judgment.evidence_refs))
        if status["missing"] or status["redacted"]:
            bad_evidence.append({"judgment_id": judgment.id, **status})
    if bad_falsifiers:
        problems.append(f"{len(bad_falsifiers)} 条当前 L2 没有 falsifier：{', '.join(bad_falsifiers)}")
    if bad_evidence:
        problems.append(f"{len(bad_evidence)} 条当前 L2 的证据缺失或已删除")
    duplicate_groups = store.judgment_duplicate_groups(threshold=0.72, limit=1000)
    if duplicate_groups:
        warnings.append(
            f"{len(duplicate_groups)} 组当前 L2 的措辞高度相似；可能是同一条判断被反复写入，"
            "也可能分属不同情形，需要她自己判断是否用 supersedes 合并。"
        )
    judgments = {
        "current": len(current_judgments),
        "bad_falsifiers": bad_falsifiers,
        "bad_evidence": bad_evidence,
        "duplicate_groups": [
            [judgment.id for judgment in group] for group in duplicate_groups
        ],
    }

    cards = store.awareness_cards(status="open", limit=1000) + store.awareness_cards(
        status="needs-evidence", limit=1000
    )
    bad_cards: list[dict[str, Any]] = []
    for card in cards:
        status = _check_refs(store, list(card.evidence_refs))
        if status["missing"] or status["redacted"]:
            bad_cards.append({"card_id": card.id, **status})
    if bad_cards:
        problems.append(f"{len(bad_cards)} 张未关闭觉察卡的证据缺失或已删除")
    awareness = {
        "open_or_needs_evidence": len(cards),
        "bad_evidence": bad_cards,
    }

    active_practices = store.practices(status="active", limit=1000)
    unchecked: list[str] = []
    bad_practices: list[dict[str, Any]] = []
    for practice in active_practices:
        checks = store.practice_reviews(practice.id, limit=1)
        if not checks:
            unchecked.append(practice.id)
        status = _check_refs(store, list(practice.evidence_refs))
        if status["missing"] or status["redacted"]:
            bad_practices.append({"practice_id": practice.id, **status})
    if bad_practices:
        problems.append(f"{len(bad_practices)} 条有效做法的原始证据缺失或已删除")
    old_unchecked = [
        practice.id
        for practice in active_practices
        if practice.id in unchecked and (_age_hours(practice.ts, now) or 0) > 24 * 7
    ]
    if old_unchecked:
        warnings.append(f"{len(old_unchecked)} 条做法超过 7 天没有复核：{', '.join(old_unchecked)}")
    practices = {
        "active": len(active_practices),
        "never_checked": unchecked,
        "old_unchecked": old_unchecked,
        "bad_evidence": bad_practices,
    }

    episodes = store.episodes(limit=1000)
    bad_episodes: list[dict[str, Any]] = []
    for episode in episodes:
        missing = [event_id for event_id in episode.event_ids if _event_status(store, event_id) != "ok"]
        if missing:
            bad_episodes.append({"episode_id": episode.id, "missing": missing})
    if bad_episodes:
        problems.append(f"{len(bad_episodes)} 条生活片段引用了缺失或已删除的事件")
    episode_report = {
        "count": len(episodes),
        "latest": episodes[0].id if episodes else None,
        "bad_evidence": bad_episodes,
    }

    loops = store.open_loops(limit=1000)
    stale_loops = [
        loop.id for loop in loops if (_age_hours(loop.ts, now) or 0) > 24 * 14
    ]
    bad_loops: list[dict[str, Any]] = []
    for loop in loops:
        status = _check_refs(store, list(loop.refs))
        if status["missing"] or status["redacted"]:
            bad_loops.append({"loop_id": loop.id, **status})
    if bad_loops:
        problems.append(f"{len(bad_loops)} 条未闭合事项引用了缺失或已删除的证据")
    if stale_loops:
        warnings.append(f"{len(stale_loops)} 条未闭合事项超过 14 天没有动过")
    loops_report = {"open": len(loops), "stale": stale_loops, "bad_evidence": bad_loops}

    backup = _latest_backup(config, now)
    if backup is None:
        problems.append("没有找到任何备份 manifest")
    elif not backup.get("valid"):
        problems.append(f"最新备份的 manifest 无法读取：{backup.get('error')}")
    else:
        if not backup.get("db_exists"):
            problems.append(f"最新备份的数据库文件不存在：{backup.get('db_path')}")
        elif not backup.get("db_bytes"):
            problems.append(f"最新备份的数据库文件是空的：{backup.get('db_path')}")
        if backup.get("integrity") != "ok":
            problems.append(f"最新备份的数据库完整性不是 ok：{backup.get('integrity')}")
        if backup.get("chain_ok") is not True:
            problems.append("最新备份的哈希链不是 ok")
        age = backup.get("age_hours")
        if age is None:
            warnings.append("最新备份没有可解析的 created_at")
        elif age > 48:
            warnings.append(f"最新备份已经 {age:.1f} 小时没有更新")

    citation_count = int(
        store.conn.execute("SELECT COUNT(*) AS n FROM citation_confirmations").fetchone()["n"]
    )
    orphan_citations = int(
        store.conn.execute(
            "SELECT COUNT(*) AS n FROM citation_confirmations c "
            "LEFT JOIN events e ON e.id = c.event_id WHERE e.id IS NULL"
        ).fetchone()["n"]
    )
    if orphan_citations:
        problems.append(f"{orphan_citations} 条引用确认指向不存在的事件")

    # Persona accounting: what part of her self-model actually reaches the prompt,
    # what is written twice, what has no date, and whether a credential leaked
    # into the files that get pasted into every request.
    persona = check_persona(store, config)
    problems.extend(persona["problems"])
    warnings.extend(persona["warnings"])

    report = {
        "ok": not problems,
        "generated_at": now.isoformat(),
        "events": events,
        "self": self_report,
        "judgments": judgments,
        "awareness": awareness,
        "practices": practices,
        "episodes": episode_report,
        "open_loops": loops_report,
        "citations": {"count": citation_count, "orphans": orphan_citations},
        "persona": persona,
        "backup": backup,
        "problems": problems,
        "warnings": warnings,
    }
    return report


def memory_health(store: Any, config: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """A compact, read-only health line for waking and for the audit tool.

    `audit_memory` is deliberately thorough. This projection keeps the few
    facts she can act on without swallowing the full report: is the L1 chain
    intact, how old is the newest backup, and what problems/warnings exist.
    It never repairs anything.
    """
    report = audit_memory(store, config, now=now)
    backup = report.get("backup") or {}
    problems = [str(item) for item in report.get("problems") or []]
    warnings = [str(item) for item in report.get("warnings") or []]
    return {
        "healthy": bool(report.get("ok")),
        "events": int((report.get("events") or {}).get("count") or 0),
        "chain_ok": bool((report.get("events") or {}).get("chain_ok")),
        "bad_event": (report.get("events") or {}).get("bad_event"),
        "self_version": (report.get("self") or {}).get("version"),
        "judgments": int((report.get("judgments") or {}).get("current") or 0),
        "open_cards": int((report.get("awareness") or {}).get("open_or_needs_evidence") or 0),
        "active_practices": int((report.get("practices") or {}).get("active") or 0),
        "episodes": int((report.get("episodes") or {}).get("count") or 0),
        "open_loops": int((report.get("open_loops") or {}).get("open") or 0),
        "backup": {
            "created_at": backup.get("created_at"),
            "age_hours": backup.get("age_hours"),
            "events": backup.get("events"),
            "integrity": backup.get("integrity"),
            "chain_ok": backup.get("chain_ok"),
            "db_exists": backup.get("db_exists"),
        },
        "problems": problems[:20],
        "warnings": warnings[:10],
        "problem_count": len(problems),
        "warning_count": len(warnings),
    }
