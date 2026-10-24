from __future__ import annotations

import difflib
import hashlib
import json
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..db import connect, init_db
from ..ids import day_key, iso, new_id, now_iso, parse_ts, utcnow
from .models import (
    Alarm,
    AwarenessCard,
    BudgetSummary,
    Event,
    Judgment,
    JudgmentReview,
    Message,
    OpenLoop,
    RecallHit,
    SelfVersion,
    Stance,
)

SOURCES = {"human", "ai-observation", "ai-interpretation", "system", "external"}
KINDS = {
    "message",
    "observation",
    "action",
    "outcome",
    "diary",
    "wake",
    "sleep",
    "reply",
    "commitment",
    "self_diff",
    "awareness",
    "note",
    "error",
    "recall",
}
GENESIS_HASH = "0" * 64


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def event_hash_input(data: dict[str, Any], prev_hash: str) -> str:
    body = {
        "id": data["id"],
        "ts": data["ts"],
        "source": data["source"],
        "kind": data["kind"],
        "content": data["content"],
        "actor": data["actor"],
        "channel": data["channel"],
        "refs": data["refs"],
        "meta": data["meta"],
        "created_at": data["created_at"],
    }
    return prev_hash + canonical_json(body)


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


class MemoryStore:
    """SQLite-backed memory core.

    L1 events are append-only and hash-chained. L2 judgments and their reviews are
    append-only. SELF.md versions are append-only. Operational state (messages, open
    loops) is mutable, but changes leave an L1 event where it matters.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(Path(db_path).expanduser())
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        init_db(self.conn)

    @property
    def conn(self) -> sqlite3.Connection:
        """One SQLite connection per thread.

        The gateway has an HTTP intake thread and a single worker thread; sharing
        one connection lets their transactions collide ("cannot start a
        transaction within a transaction"). WAL plus a per-thread connection
        keeps each thread's transaction boundary private.
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.db_path)
            self._local.conn = conn
            with self._connections_lock:
                self._connections.append(conn)
        return conn

    def close(self) -> None:
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            conn.close()
        self._local.conn = None

    # ---------------------------------------------------------------- L1 events

    def append_event(
        self,
        source: str,
        kind: str,
        content: str,
        *,
        actor: str | None = None,
        channel: str | None = None,
        refs: Iterable[str] | None = None,
        meta: dict[str, Any] | None = None,
        ts: str | None = None,
        event_id: str | None = None,
    ) -> Event:
        if source not in SOURCES:
            raise ValueError(f"unknown source: {source}")
        if kind not in KINDS:
            raise ValueError(f"unknown kind: {kind}")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("event content must be non-empty")
        data = {
            "id": event_id or new_id("evt"),
            "ts": ts or now_iso(),
            "source": source,
            "kind": kind,
            "content": content,
            "actor": actor,
            "channel": channel,
            "refs": list(refs or []),
            "meta": dict(meta or {}),
            "created_at": now_iso(),
        }
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            row = self.conn.execute("SELECT hash FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
            prev_hash = row["hash"] if row else GENESIS_HASH
            digest = hashlib.sha256(event_hash_input(data, prev_hash).encode("utf-8")).hexdigest()
            self.conn.execute(
                "INSERT INTO events(id, ts, source, kind, content, actor, channel, refs, meta, prev_hash, hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    data["id"],
                    data["ts"],
                    data["source"],
                    data["kind"],
                    data["content"],
                    data["actor"],
                    data["channel"],
                    canonical_json(data["refs"]),
                    canonical_json(data["meta"]),
                    prev_hash,
                    digest,
                    data["created_at"],
                ),
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        return self.get_event(data["id"])  # type: ignore[return-value]

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return Event.from_row(row) if row else None

    def events(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        order: str = "desc",
        kind: str | None = None,
        source: str | None = None,
        since: str | None = None,
    ) -> list[Event]:
        direction = "DESC" if order.lower() == "desc" else "ASC"
        where = []
        args: list[Any] = []
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if source:
            where.append("source = ?")
            args.append(source)
        if since:
            where.append("ts >= ?")
            args.append(since)
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY ts {direction}, rowid {direction} LIMIT ? OFFSET ?"
        args.extend([limit, offset])
        return [Event.from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def latest_event(self, kind: str | None = None) -> Event | None:
        rows = self.events(limit=1, kind=kind)
        return rows[0] if rows else None

    def event_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"])

    def last_event_ts(self) -> str | None:
        row = self.conn.execute("SELECT ts FROM events ORDER BY rowid DESC LIMIT 1").fetchone()
        return row["ts"] if row else None

    def backup(self, destination: str | Path) -> dict[str, Any]:
        """Make a consistent SQLite snapshot and verify it before returning."""
        target_dir = Path(destination).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o700)
        stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
        target = target_dir / f"yugang-{stamp}.db"
        backup_conn = sqlite3.connect(target)
        try:
            self.conn.backup(backup_conn)
            backup_conn.execute("PRAGMA journal_mode=DELETE")
            backup_conn.commit()
        finally:
            backup_conn.close()
        target.chmod(0o600)
        check = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        try:
            integrity = str(check.execute("PRAGMA integrity_check").fetchone()[0])
            events = int(check.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        finally:
            check.close()
        return {
            "path": str(target),
            "bytes": target.stat().st_size,
            "integrity": integrity,
            "events": events,
        }

    def verify_chain(self) -> tuple[bool, str | None]:
        prev = GENESIS_HASH
        for row in self.conn.execute("SELECT * FROM events ORDER BY rowid ASC"):
            data = {
                "id": row["id"],
                "ts": row["ts"],
                "source": row["source"],
                "kind": row["kind"],
                "content": row["content"],
                "actor": row["actor"],
                "channel": row["channel"],
                "refs": _loads(row["refs"], []),
                "meta": _loads(row["meta"], {}),
                "created_at": row["created_at"],
            }
            expected = hashlib.sha256(event_hash_input(data, prev).encode("utf-8")).hexdigest()
            if row["prev_hash"] != prev or row["hash"] != expected:
                return False, row["id"]
            prev = row["hash"]
        return True, None

    # ------------------------------------------------------------------- recall

    def recall(
        self,
        query: str,
        *,
        limit: int = 8,
        now: datetime | None = None,
        source: str | None = None,
        kind: str | None = None,
        log: bool = True,
    ) -> list[RecallHit]:
        now = now or utcnow()
        query = (query or "").strip()
        hits: list[RecallHit] = []
        tokenizer = self.conn.execute("SELECT value FROM meta WHERE key='fts_tokenizer'").fetchone()
        terms = [t for t in query.split() if t]
        tokenizer_name = tokenizer["value"] if tokenizer else "none"
        use_fts = bool(query) and tokenizer_name != "none"
        if use_fts:
            # A trigram index cannot answer Chinese one- or two-character terms, and a
            # quoted multi-word phrase would require the words to be adjacent. Search
            # terms independently and fall back to LIKE when FTS finds nothing.
            usable = [t for t in terms if len(t) >= 3] if tokenizer_name == "trigram" else terms
            if usable:
                match = " OR ".join('"' + t.replace('"', '""') + '"' for t in usable)
                sql = (
                    "SELECT e.*, bm25(events_fts) AS rank, "
                    "snippet(events_fts, 0, '[', ']', '…', 16) AS snip "
                    "FROM events_fts JOIN events e ON e.rowid = events_fts.rowid "
                    "WHERE events_fts MATCH ?"
                )
                args: list[Any] = [match]
                if source:
                    sql += " AND e.source = ?"
                    args.append(source)
                if kind:
                    sql += " AND e.kind = ?"
                    args.append(kind)
                sql += " ORDER BY rank LIMIT ?"
                args.append(max(limit * 4, 16))
                try:
                    rows = self.conn.execute(sql, args).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                for row in rows:
                    rank = row["rank"] if row["rank"] is not None else 0.0
                    rank_score = 1.0 / (1.0 + abs(float(rank)))
                    hits.append(RecallHit(Event.from_row(row), rank_score, row["snip"] or ""))
        if not hits and query:
            sql = "SELECT * FROM events WHERE (" + " OR ".join("content LIKE ?" for _ in terms) + ")"
            args = [f"%{t}%" for t in terms]
            if source:
                sql += " AND source = ?"
                args.append(source)
            if kind:
                sql += " AND kind = ?"
                args.append(kind)
            sql += " ORDER BY ts DESC LIMIT ?"
            args.append(max(limit * 4, 16))
            rows = self.conn.execute(sql, args).fetchall()
            for row in rows:
                ev = Event.from_row(row)
                hits.append(RecallHit(ev, 0.5, ev.content[:160]))
        elif not query:
            for ev in self.events(limit=max(limit * 4, 16)):
                hits.append(RecallHit(ev, 0.5, ev.content[:160]))
        # Recency + provenance + future-self confirmation weighting.
        # An event that a later judgment was explicitly supported with is not just
        # "old material": it has been re-read and re-confirmed. Likewise, events
        # that were recalled before are slightly easier to reach again.
        candidate_ids = {hit.event.id for hit in hits}
        confirmations: dict[str, int] = {}
        recall_counts: dict[str, int] = {}
        if candidate_ids:
            marks = ",".join("?" for _ in candidate_ids)
            for row in self.conn.execute(
                f"SELECT event_id, confirmations FROM event_confirmations WHERE event_id IN ({marks})",
                tuple(candidate_ids),
            ).fetchall():
                confirmations[row["event_id"]] = int(row["confirmations"])
            for row in self.conn.execute(
                f"SELECT event_id, recall_count FROM recall_stats WHERE event_id IN ({marks})",
                tuple(candidate_ids),
            ).fetchall():
                recall_counts[row["event_id"]] = int(row["recall_count"])
        source_weight = {
            "human": 1.15,
            "ai-observation": 1.0,
            "ai-interpretation": 0.85,
            "system": 0.7,
            "external": 0.9,
        }
        kind_weight = {
            "diary": 1.1,
            "message": 1.1,
            "self_diff": 1.15,
            "awareness": 1.1,
            "reply": 0.9,
            "wake": 0.6,
            "sleep": 0.6,
            "recall": 0.4,
        }
        weighted: list[RecallHit] = []
        for hit in hits:
            try:
                age_days = max(0.0, (now - parse_ts(hit.event.ts)).total_seconds() / 86400)
            except Exception:
                age_days = 0.0
            recency = 1.0 / (1.0 + age_days / 30.0)
            score = 0.6 * hit.score + 0.3 * recency + 0.1 * source_weight.get(hit.event.source, 1.0)
            score *= kind_weight.get(hit.event.kind, 1.0)
            confirmed = confirmations.get(hit.event.id, 0)
            recalled = recall_counts.get(hit.event.id, 0)
            score *= 1.0 + min(0.30, 0.05 * confirmed) + min(0.10, 0.01 * recalled)
            weighted.append(RecallHit(hit.event, score, hit.snippet, confirmed, recalled))
        weighted.sort(key=lambda h: (h.score, h.event.ts), reverse=True)
        weighted = weighted[:limit]
        if log:
            self.record_recall(query, [h.event.id for h in weighted])
        return weighted

    def record_recall(self, query: str, event_ids: list[str], context: str | None = None) -> None:
        when = now_iso()
        self.conn.execute(
            "INSERT INTO recall_log(id, ts, query, event_ids, context) VALUES (?, ?, ?, ?, ?)",
            (new_id("recall"), when, query, canonical_json(event_ids), context),
        )
        for event_id in dict.fromkeys(event_ids):
            self.conn.execute(
                "INSERT INTO recall_stats(event_id, recall_count, last_recalled_at) VALUES (?, 1, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET recall_count = recall_count + 1, "
                "last_recalled_at = excluded.last_recalled_at",
                (event_id, when),
            )

    # --------------------------------------------------------------- L2 judgments

    def add_judgment(
        self,
        claim: str,
        falsifier: str,
        evidence_refs: Iterable[str],
        confidence: float,
        *,
        trigger: str | None = None,
        supersedes: str | None = None,
        ts: str | None = None,
    ) -> Judgment:
        if not claim.strip():
            raise ValueError("claim must be non-empty")
        if not falsifier.strip():
            raise ValueError("a judgment requires a falsifier")
        refs = list(evidence_refs)
        if not refs:
            raise ValueError("a judgment requires evidence refs")
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown evidence ref: {ref}")
        if supersedes and not self.get_judgment(supersedes):
            raise ValueError(f"unknown supersedes judgment: {supersedes}")
        jid = new_id("judg")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO judgments(id, ts, claim, falsifier, evidence_refs, confidence, trigger, supersedes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (jid, when, claim.strip(), falsifier.strip(), canonical_json(refs), float(confidence), trigger, supersedes, now_iso()),
        )
        row = self.conn.execute("SELECT * FROM judgments WHERE id = ?", (jid,)).fetchone()
        return Judgment.from_row(row)

    def get_judgment(self, judgment_id: str) -> Judgment | None:
        row = self.conn.execute("SELECT * FROM judgments WHERE id = ?", (judgment_id,)).fetchone()
        return Judgment.from_row(row) if row else None

    def review_judgment(
        self,
        judgment_id: str,
        verdict: str,
        evidence_refs: Iterable[str],
        *,
        note: str | None = None,
        ts: str | None = None,
    ) -> JudgmentReview:
        if verdict not in {"supported", "refuted", "needs-evidence"}:
            raise ValueError(f"unknown verdict: {verdict}")
        if not self.get_judgment(judgment_id):
            raise ValueError(f"unknown judgment: {judgment_id}")
        refs = list(evidence_refs)
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown evidence ref: {ref}")
        rid = new_id("jrev")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO judgment_reviews(id, judgment_id, ts, verdict, evidence_refs, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rid, judgment_id, when, verdict, canonical_json(refs), note, now_iso()),
        )
        if verdict == "supported":
            # The future self re-read these events and used them to support a
            # judgment. That is the only automatic "reconfirmation" signal we
            # trust; a mere recall is weaker and tracked separately.
            for ref in dict.fromkeys(refs):
                self.conn.execute(
                    "INSERT INTO event_confirmations(event_id, confirmations, last_confirmed_at) VALUES (?, 1, ?) "
                    "ON CONFLICT(event_id) DO UPDATE SET confirmations = confirmations + 1, "
                    "last_confirmed_at = excluded.last_confirmed_at",
                    (ref, when),
                )
        row = self.conn.execute("SELECT * FROM judgment_reviews WHERE id = ?", (rid,)).fetchone()
        return JudgmentReview(
            id=row["id"],
            judgment_id=row["judgment_id"],
            ts=row["ts"],
            verdict=row["verdict"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            note=row["note"],
            created_at=row["created_at"],
        )

    def judgment_status(self, judgment_id: str) -> str:
        row = self.conn.execute(
            "SELECT verdict FROM judgment_reviews WHERE judgment_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (judgment_id,),
        ).fetchone()
        return row["verdict"] if row else "open"

    def judgments(self, *, status: str | None = None, limit: int = 50) -> list[Judgment]:
        sql = (
            "SELECT j.* FROM judgments j "
            "WHERE COALESCE((SELECT r.verdict FROM judgment_reviews r "
            "WHERE r.judgment_id = j.id ORDER BY r.ts DESC, r.rowid DESC LIMIT 1), 'open') "
        )
        args: list[Any] = []
        if status:
            sql += "= ?"
            args.append(status)
        else:
            sql += "IS NOT NULL"
        sql += " ORDER BY j.ts DESC LIMIT ?"
        args.append(limit)
        return [Judgment.from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    # --------------------------------------------------------------------- L3

    def commit_stance(
        self,
        content: str,
        based_on: Iterable[str],
        *,
        trigger: str | None = None,
        ts: str | None = None,
    ) -> Stance:
        if not content.strip():
            raise ValueError("stance must be non-empty")
        refs = list(based_on)
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown stance ref: {ref}")
        sid = new_id("stance")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO stances(id, ts, content, based_on, trigger, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (sid, when, content, canonical_json(refs), trigger, now_iso()),
        )
        return Stance(sid, when, content, tuple(refs), trigger, now_iso())

    def current_stance(self) -> Stance | None:
        row = self.conn.execute("SELECT * FROM stances ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        if not row:
            return None
        return Stance(
            id=row["id"],
            ts=row["ts"],
            content=row["content"],
            based_on=tuple(_loads(row["based_on"], [])),
            trigger=row["trigger"],
            created_at=row["created_at"],
        )

    # ------------------------------------------------------------- SELF.md

    def commit_self(
        self,
        content: str,
        reason: str,
        evidence_refs: Iterable[str],
        *,
        accepted_by: str = "ayu",
        ts: str | None = None,
    ) -> SelfVersion:
        if not content.strip():
            raise ValueError("SELF.md content must be non-empty")
        refs = list(evidence_refs)
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown self evidence ref: {ref}")
        previous = self.current_self() or ""
        diff = "".join(
            difflib.unified_diff(
                previous.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile="SELF.md.previous",
                tofile="SELF.md.new",
            )
        )
        vid = new_id("self")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO self_versions(id, ts, content, diff, reason, evidence_refs, accepted_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (vid, when, content, diff, reason, canonical_json(refs), accepted_by, now_iso()),
        )
        self.append_event(
            "system",
            "self_diff",
            reason,
            meta={"self_version_id": vid, "accepted_by": accepted_by, "diff": diff},
            refs=refs,
            ts=when,
        )
        return self.get_self_version(vid)  # type: ignore[return-value]

    def get_self_version(self, version_id: str) -> SelfVersion | None:
        row = self.conn.execute("SELECT * FROM self_versions WHERE id = ?", (version_id,)).fetchone()
        return SelfVersion.from_row(row) if row else None

    def current_self_version(self) -> SelfVersion | None:
        row = self.conn.execute("SELECT * FROM self_versions ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        return SelfVersion.from_row(row) if row else None

    def current_self(self) -> str | None:
        version = self.current_self_version()
        return version.content if version else None

    def self_history(self, limit: int = 20) -> list[SelfVersion]:
        rows = self.conn.execute(
            "SELECT * FROM self_versions ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [SelfVersion.from_row(r) for r in rows]

    # ------------------------------------------------------- awareness cards

    def add_awareness_card(
        self,
        trigger: str,
        observation: str,
        hypothesis: str,
        falsifier: str,
        evidence_refs: Iterable[str],
        confidence: float,
        *,
        proposed_self_diff: str | None = None,
        ts: str | None = None,
    ) -> AwarenessCard:
        if not falsifier.strip():
            raise ValueError("an awareness card requires a falsifier")
        refs = list(evidence_refs)
        if not refs:
            raise ValueError("an awareness card requires evidence refs")
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown awareness ref: {ref}")
        cid = new_id("card")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO awareness_cards(id, ts, trigger, observation, hypothesis, falsifier, "
            "evidence_refs, confidence, proposed_self_diff, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (cid, when, trigger, observation, hypothesis, falsifier, canonical_json(refs), float(confidence),
             proposed_self_diff, now_iso()),
        )
        row = self.conn.execute("SELECT * FROM awareness_cards WHERE id = ?", (cid,)).fetchone()
        return AwarenessCard.from_row(row)

    def review_awareness(self, card_id: str, status: str, *, note: str | None = None, ts: str | None = None) -> None:
        if status not in {"open", "accepted", "rejected", "needs-evidence", "merged"}:
            raise ValueError(f"unknown card status: {status}")
        if not self.conn.execute("SELECT 1 FROM awareness_cards WHERE id = ?", (card_id,)).fetchone():
            raise ValueError(f"unknown card: {card_id}")
        self.conn.execute(
            "INSERT INTO awareness_reviews(id, card_id, ts, status, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id("arev"), card_id, ts or now_iso(), status, note, now_iso()),
        )

    def awareness_cards(self, *, status: str | None = None, limit: int = 50) -> list[AwarenessCard]:
        sql = (
            "SELECT c.* FROM awareness_cards c WHERE COALESCE((SELECT r.status FROM awareness_reviews r "
            "WHERE r.card_id = c.id ORDER BY r.ts DESC, r.rowid DESC LIMIT 1), 'open') "
        )
        args: list[Any] = []
        if status:
            sql += "= ?"
            args.append(status)
        else:
            sql += "IS NOT NULL"
        sql += " ORDER BY c.ts DESC LIMIT ?"
        args.append(limit)
        return [AwarenessCard.from_row(r) for r in self.conn.execute(sql, args).fetchall()]

    def card_status(self, card_id: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM awareness_reviews WHERE card_id = ? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (card_id,),
        ).fetchone()
        return row["status"] if row else "open"

    # -------------------------------------------------------------------- alarms

    def add_alarm(
        self,
        due_at: str,
        *,
        reason: str | None = None,
        source_event: str | None = None,
        ts: str | None = None,
    ) -> Alarm:
        parse_ts(due_at)
        aid = new_id("alarm")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO alarms(id, ts, due_at, reason, status, source_event, fired_event, note, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL, ?)",
            (aid, when, due_at, reason, source_event, now_iso()),
        )
        return self.get_alarm(aid)

    def get_alarm(self, alarm_id: str) -> Alarm:
        row = self.conn.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown alarm: {alarm_id}")
        return Alarm.from_row(row)

    def due_alarms(self, *, now: datetime | None = None, limit: int = 10) -> list[Alarm]:
        stamp = (now or utcnow()).isoformat(timespec="microseconds")
        rows = self.conn.execute(
            "SELECT * FROM alarms WHERE status = 'pending' AND due_at <= ? ORDER BY due_at ASC LIMIT ?",
            (stamp, limit),
        ).fetchall()
        return [Alarm.from_row(row) for row in rows]

    def pending_alarms(self, *, limit: int = 10) -> list[Alarm]:
        rows = self.conn.execute(
            "SELECT * FROM alarms WHERE status = 'pending' ORDER BY due_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Alarm.from_row(row) for row in rows]

    def alarm_count_since(self, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM alarms WHERE ts >= ?",
            (since.isoformat(timespec="microseconds"),),
        ).fetchone()
        return int(row["n"])

    def mark_alarm(
        self,
        alarm_id: str,
        status: str,
        *,
        fired_event: str | None = None,
        note: str | None = None,
    ) -> None:
        if status not in {"fired", "missed", "cancelled"}:
            raise ValueError(f"unknown alarm status: {status}")
        self.conn.execute(
            "UPDATE alarms SET status = ?, fired_event = ?, note = ?, updated_at = ? WHERE id = ?",
            (status, fired_event, note, now_iso(), alarm_id),
        )

    # --------------------------------------------------------------- open loops

    def add_open_loop(
        self,
        title: str,
        *,
        detail: str | None = None,
        due: str | None = None,
        refs: Iterable[str] | None = None,
    ) -> OpenLoop:
        if not title.strip():
            raise ValueError("open loop title must be non-empty")
        if due:
            parse_ts(due)
        event = self.append_event(
            "ai-observation",
            "commitment",
            title,
            meta={"due": due, "detail": detail},
            refs=list(refs or []),
        )
        lid = new_id("loop")
        when = now_iso()
        self.conn.execute(
            "INSERT INTO open_loops(id, ts, title, detail, status, due, refs, updated_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?, ?)",
            (lid, when, title, detail, due, canonical_json([event.id]), when),
        )
        return self.get_open_loop(lid)  # type: ignore[return-value]

    def get_open_loop(self, loop_id: str) -> OpenLoop | None:
        row = self.conn.execute("SELECT * FROM open_loops WHERE id = ?", (loop_id,)).fetchone()
        if not row:
            return None
        return OpenLoop(
            id=row["id"],
            ts=row["ts"],
            title=row["title"],
            detail=row["detail"],
            status=row["status"],
            due=row["due"],
            refs=tuple(_loads(row["refs"], [])),
            updated_at=row["updated_at"],
        )

    def open_loops(self, *, status: str = "open", limit: int = 100) -> list[OpenLoop]:
        rows = self.conn.execute(
            "SELECT * FROM open_loops WHERE status = ? ORDER BY COALESCE(due, ts) ASC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [
            OpenLoop(
                id=r["id"],
                ts=r["ts"],
                title=r["title"],
                detail=r["detail"],
                status=r["status"],
                due=r["due"],
                refs=tuple(_loads(r["refs"], [])),
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    def due_open_loops(self, now: datetime | None = None) -> list[OpenLoop]:
        now = now or utcnow()
        due = []
        for loop in self.open_loops(limit=500):
            if not loop.due:
                continue
            try:
                if parse_ts(loop.due) <= now:
                    due.append(loop)
            except Exception:
                continue
        return due

    def close_open_loop(self, loop_id: str, *, status: str = "done", note: str | None = None) -> bool:
        if status not in {"done", "dropped"}:
            raise ValueError(f"unknown loop status: {status}")
        row = self.conn.execute("SELECT * FROM open_loops WHERE id = ?", (loop_id,)).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE open_loops SET status = ?, updated_at = ? WHERE id = ?",
            (status, now_iso(), loop_id),
        )
        self.append_event(
            "ai-observation",
            "action",
            f"closed loop: {row['title']} ({status})",
            meta={"loop_id": loop_id, "status": status, "note": note},
            refs=_loads(row["refs"], []),
        )
        return True

    # ----------------------------------------------------------------- messages

    def record_message(
        self,
        channel: str,
        content: str,
        *,
        direction: str = "in",
        sender: str | None = None,
        external_id: str | None = None,
        status: str = "received",
        meta: dict[str, Any] | None = None,
    ) -> Message:
        if direction not in {"in", "out"}:
            raise ValueError("direction must be in or out")
        mid = new_id("msg")
        when = now_iso()
        try:
            self.conn.execute(
                "INSERT INTO messages(id, channel, external_id, direction, sender, content, status, created_at, meta) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (mid, channel, external_id, direction, sender, content, status, when, canonical_json(meta or {})),
            )
        except sqlite3.IntegrityError:
            existing = self.message_by_external(channel, external_id)
            if existing:
                return existing
            raise
        row = self.conn.execute("SELECT * FROM messages WHERE id = ?", (mid,)).fetchone()
        return Message.from_row(row)

    def receive_message(
        self,
        channel: str,
        content: str,
        *,
        sender: str | None = None,
        external_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> tuple[Message, Event]:
        if external_id:
            existing = self.message_by_external(channel, external_id)
            if existing:
                event_id = existing.meta.get("event_id") if isinstance(existing.meta, dict) else None
                event = self.get_event(event_id) if event_id else None
                if event is None:
                    event = self.append_event(
                        "system", "note", "duplicate channel message ignored",
                        meta={"channel": channel, "external_id": external_id},
                    )
                return existing, event
        event = self.append_event(
            "human",
            "message",
            content,
            actor=sender,
            channel=channel,
            meta={"external_id": external_id, **(meta or {})},
        )
        message = self.record_message(
            channel,
            content,
            sender=sender,
            external_id=external_id,
            meta={"event_id": event.id, **(meta or {})},
        )
        return message, event

    def message_by_external(self, channel: str, external_id: str | None) -> Message | None:
        if not external_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM messages WHERE channel = ? AND external_id = ?", (channel, external_id)
        ).fetchone()
        return Message.from_row(row) if row else None

    def mark_message(self, message_id: str, status: str, *, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE messages SET status = ?, processed_at = ?, error = ? WHERE id = ?",
            (status, now_iso(), error, message_id),
        )

    def update_message_meta(self, message_id: str, patch: dict[str, Any]) -> None:
        row = self.conn.execute("SELECT meta FROM messages WHERE id = ?", (message_id,)).fetchone()
        if row is None:
            return
        meta = _loads(row["meta"], {})
        if not isinstance(meta, dict):
            meta = {}
        meta.update(patch)
        self.conn.execute("UPDATE messages SET meta = ? WHERE id = ?", (json.dumps(meta, ensure_ascii=False), message_id))

    def pending_messages(self, limit: int = 20) -> list[Message]:
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND status IN ('received', 'failed') "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Message.from_row(r) for r in rows]

    def recoverable_messages(self, limit: int = 50) -> list[Message]:
        """Inbound messages that were never answered before the process died.

        received means it was stored but not picked up; processing means the
        worker died mid-turn. Both are safe to retry because a successful turn
        marks the row processed before anything else observes it.
        """
        rows = self.conn.execute(
            "SELECT * FROM messages WHERE direction = 'in' AND status IN ('received', 'processing') "
            "ORDER BY created_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [Message.from_row(r) for r in rows]

    # ------------------------------------------------------------------- budget

    def record_budget(
        self,
        day: str,
        kind: str,
        cost_cny: float,
        *,
        model: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        note: str | None = None,
        ts: str | None = None,
    ) -> None:
        if cost_cny < 0:
            raise ValueError("cost must be non-negative")
        self.conn.execute(
            "INSERT INTO budget_ledger(id, ts, day, kind, model, input_tokens, output_tokens, cost_cny, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id("budget"), ts or now_iso(), day, kind, model, int(input_tokens), int(output_tokens), float(cost_cny), note),
        )

    def budget_spent(self, day: str) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost_cny), 0.0) AS total FROM budget_ledger WHERE day = ?", (day,)
        ).fetchone()
        return float(row["total"])

    def budget_by_kind(self, day: str) -> dict[str, float]:
        rows = self.conn.execute(
            "SELECT kind, SUM(cost_cny) AS total FROM budget_ledger WHERE day = ? GROUP BY kind", (day,)
        ).fetchall()
        return {r["kind"]: float(r["total"]) for r in rows}

    def budget_summary(self, day: str, budget_cny: float) -> BudgetSummary:
        spent = self.budget_spent(day)
        return BudgetSummary(
            day=day,
            budget_cny=budget_cny,
            spent_cny=spent,
            remaining_cny=max(0.0, budget_cny - spent),
            by_kind=self.budget_by_kind(day),
        )

    # -------------------------------------------------------------------- misc

    def ref_exists(self, ref: str) -> bool:
        for table in ("events", "judgments", "awareness_cards", "self_versions"):
            if self.conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (ref,)).fetchone():
                return True
        return False

    def stats(self) -> dict[str, Any]:
        def count(table: str, where: str = "") -> int:
            row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table} {where}").fetchone()
            return int(row["n"])

        return {
            "events": count("events"),
            "judgments": count("judgments"),
            "awareness_cards": count("awareness_cards"),
            "open_loops": count("open_loops", "WHERE status = 'open'"),
            "messages": count("messages"),
            "self_versions": count("self_versions"),
            "last_event_ts": self.last_event_ts(),
            "db_path": self.db_path,
        }
