from __future__ import annotations

import difflib
import hashlib
import json
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from ..db import connect, init_db
from ..ids import day_key, iso, new_id, now_iso, parse_ts, utcnow
from .models import (
    Alarm,
    AwarenessCard,
    BudgetSummary,
    Episode,
    Event,
    Judgment,
    JudgmentReview,
    Message,
    OpenLoop,
    Practice,
    PracticeReview,
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
    "practice",
    "self_diff",
    "awareness",
    "note",
    "error",
    "recall",
}
GENESIS_HASH = "0" * 64
_EVENT_REF_RE = re.compile(r"\bevt_[A-Za-z0-9_]+")


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

    def redactions(self) -> dict[str, str]:
        """Read-time redaction overlay.

        L1 stays append-only: the original bytes are kept so the hash chain can
        still be verified. Normal reads and recall return the replacement text
        instead. Full erasure would require rebuilding the log and is not
        something this method pretends to do.
        """
        rows = self.conn.execute("SELECT target_event_id, replacement FROM redactions").fetchall()
        return {r["target_event_id"]: (r["replacement"] or "[已删除]") for r in rows}

    def _with_redactions(self, events: list[Event], mapping: dict[str, str] | None = None) -> list[Event]:
        mapping = self.redactions() if mapping is None else mapping
        if not mapping:
            return events
        out: list[Event] = []
        for event in events:
            replacement = mapping.get(event.id)
            if replacement is None:
                out.append(event)
            else:
                meta = dict(event.meta)
                meta["redacted"] = True
                out.append(replace(event, content=replacement, meta=meta))
        return out

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not row:
            return None
        return self._with_redactions([Event.from_row(row)])[0]

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
        return self._with_redactions([Event.from_row(r) for r in self.conn.execute(sql, args).fetchall()])

    def events_between(
        self, start_ts: str, end_ts: str, *, limit: int = 500, include_start: bool = True
    ) -> list[Event]:
        """Raw L1 material in a time window, oldest first.

        Pass include_start=False when the start timestamp is the end of the
        previous window: its sleep/diary events belong to that window, not the
        next one.
        """
        parse_ts(start_ts)
        parse_ts(end_ts)
        comparator = ">=" if include_start else ">"
        rows = self.conn.execute(
            f"SELECT * FROM events WHERE ts {comparator} ? AND ts <= ? "
            "ORDER BY ts ASC, rowid ASC LIMIT ?",
            (start_ts, end_ts, limit),
        ).fetchall()
        return self._with_redactions([Event.from_row(row) for row in rows])

    def first_event_after(self, ts: str, *, kind: str | None = None) -> Event | None:
        """The oldest event strictly after ts, optionally limited to one kind."""
        parse_ts(ts)
        if kind is None:
            row = self.conn.execute(
                "SELECT * FROM events WHERE ts > ? ORDER BY ts ASC, rowid ASC LIMIT 1", (ts,)
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM events WHERE ts > ? AND kind = ? ORDER BY ts ASC, rowid ASC LIMIT 1",
                (ts, kind),
            ).fetchone()
        return self._with_redactions([Event.from_row(row)])[0] if row else None

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

    def redact_event(
        self,
        event_id: str,
        reason: str,
        *,
        actor: str = "operator",
        replacement: str = "[已删除]",
    ) -> dict[str, Any]:
        """Hide one event's readable content without rewriting the hash chain."""
        if not reason.strip():
            raise ValueError("a redaction requires a reason")
        raw = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if not raw:
            raise ValueError(f"unknown event: {event_id}")
        if self.conn.execute("SELECT 1 FROM redactions WHERE target_event_id = ?", (event_id,)).fetchone():
            raise ValueError(f"event already redacted: {event_id}")
        note = self.append_event(
            "system",
            "action",
            f"操作员删除了事件 {event_id} 的可读内容：{reason}",
            actor=actor,
            refs=[event_id],
            meta={"stage": "redaction", "reason": reason, "replacement": replacement},
        )
        rid = new_id("redact")
        self.conn.execute(
            "INSERT INTO redactions(id, ts, target_event_id, reason, actor, replacement) VALUES (?, ?, ?, ?, ?, ?)",
            (rid, now_iso(), event_id, reason, actor, replacement),
        )
        return {"id": rid, "target_event_id": event_id, "note_event": note.id, "replacement": replacement}

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
        redacted = self.redactions()
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
                    if row["id"] in redacted:
                        continue
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
                if row["id"] in redacted:
                    continue
                ev = Event.from_row(row)
                hits.append(RecallHit(ev, 0.5, ev.content[:160]))
        elif not query:
            for ev in self.events(limit=max(limit * 4, 16)):
                if ev.id in redacted:
                    continue
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

    def confirm_citations(
        self,
        text: str,
        *,
        source_event: str,
        before_ts: str,
        limit: int = 20,
        exclude: Iterable[str] = (),
        ts: str | None = None,
    ) -> list[str]:
        """Count old L1 events she explicitly cites in a new reply.

        A citation is stronger than a recall but weaker than an L2 review: she
        actually used this material in her own answer. Only events that existed
        before the current waking window can be confirmed; citing the message
        that just arrived is not remembering.
        """
        if not text or not source_event:
            return []
        source = self.get_event(source_event)
        if source is None:
            raise ValueError(f"unknown source event: {source_event}")
        try:
            cutoff = parse_ts(before_ts)
        except Exception:
            cutoff = parse_ts(source.ts)
        when = ts or now_iso()
        excluded = set(exclude)
        candidates = list(dict.fromkeys(_EVENT_REF_RE.findall(text)))[:limit]
        confirmed: list[str] = []
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for ref in candidates:
                if ref == source_event or ref in excluded:
                    continue
                event = self.get_event(ref)
                if event is None or event.meta.get("redacted"):
                    continue
                try:
                    if parse_ts(event.ts) >= cutoff:
                        continue
                except Exception:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO citation_confirmations(id, event_id, source_event, ts, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (new_id("cite"), ref, source_event, when, now_iso()),
                )
                if cur.rowcount == 0:
                    continue
                self.conn.execute(
                    "INSERT INTO event_confirmations(event_id, confirmations, last_confirmed_at) VALUES (?, 1, ?) "
                    "ON CONFLICT(event_id) DO UPDATE SET confirmations = confirmations + 1, "
                    "last_confirmed_at = excluded.last_confirmed_at",
                    (ref, when),
                )
                confirmed.append(ref)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return confirmed

    def citation_count(self, event_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM citation_confirmations WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return int(row["n"])

    # -------------------------------------------------------------------- episodes

    def add_episode(
        self,
        title: str,
        summary: str,
        *,
        start_ts: str,
        end_ts: str,
        event_ids: Iterable[str],
        origin: str = "model",
        source_event: str | None = None,
        ts: str | None = None,
    ) -> Episode:
        """Store one narrative episode backed by real L1 events.

        Episodes are an index over raw material, not a replacement for it:
        they never edit or delete the events they cite, and the events stay
        the authoritative record.
        """
        title = (title or "").strip()
        summary = (summary or "").strip()
        if not title:
            raise ValueError("episode title must be non-empty")
        if not summary:
            raise ValueError("episode summary must be non-empty")
        parse_ts(start_ts)
        parse_ts(end_ts)
        if parse_ts(end_ts) < parse_ts(start_ts):
            raise ValueError("episode end_ts must not precede start_ts")
        if origin not in {"model", "diary", "manual"}:
            raise ValueError(f"unknown episode origin: {origin}")
        refs = list(dict.fromkeys(str(ref).strip() for ref in event_ids if str(ref).strip()))
        if not refs:
            raise ValueError("an episode needs at least one L1 evidence event")
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"episode cites an unknown event: {ref}")
        if source_event is not None and not self.ref_exists(source_event):
            raise ValueError(f"episode source event is unknown: {source_event}")
        eid = new_id("episode")
        when = ts or now_iso()
        self.conn.execute(
            "INSERT INTO episodes(id, ts, start_ts, end_ts, title, summary, event_ids, origin, source_event, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                eid,
                when,
                start_ts,
                end_ts,
                title,
                summary,
                canonical_json(refs),
                origin,
                source_event,
                now_iso(),
            ),
        )
        return self.get_episode(eid)

    def get_episode(self, episode_id: str) -> Episode:
        row = self.conn.execute("SELECT * FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown episode: {episode_id}")
        return Episode.from_row(row)

    def episodes(self, *, limit: int = 20) -> list[Episode]:
        rows = self.conn.execute(
            "SELECT * FROM episodes ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Episode.from_row(row) for row in rows]

    def episode_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) AS n FROM episodes").fetchone()["n"])

    def search_episodes(self, query: str, *, limit: int = 5) -> list[Episode]:
        """Case-insensitive substring search over episode titles and summaries.

        Episodes are few (at most a handful per day), so a bounded in-memory
        scan is enough and works for Chinese one- and two-character terms where
        the trigram FTS index cannot.
        """
        query = (query or "").strip()
        if not query:
            return self.episodes(limit=limit)
        terms = [term.lower() for term in query.split() if term]
        if not terms:
            return self.episodes(limit=limit)
        scored: list[tuple[int, Episode]] = []
        for episode in self.episodes(limit=1000):
            haystack = f"{episode.title}\n{episode.summary}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, episode))
        scored.sort(key=lambda pair: (pair[0], pair[1].ts), reverse=True)
        return [episode for _, episode in scored[:limit]]

    # --------------------------------------------------------------- L2 judgments

    def add_judgment(
        self,
        claim: str,
        falsifier: str,
        evidence_refs: Iterable[str],
        confidence: float,
        *,
        trigger: str | None = None,
        supersedes: str | Iterable[str] | None = None,
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
        if supersedes is None:
            targets: list[str] = []
        elif isinstance(supersedes, str):
            targets = [supersedes]
        else:
            targets = list(dict.fromkeys(str(t) for t in supersedes if str(t).strip()))
        for target in targets:
            if not self.get_judgment(target):
                raise ValueError(f"unknown supersedes judgment: {target}")
            # Keep the lineage linear: an already-superseded judgment cannot be
            # superseded again by a sibling, or the "current" set would branch.
            row = self.conn.execute(
                "SELECT 1 FROM judgment_supersessions WHERE old_id = ? LIMIT 1", (target,)
            ).fetchone()
            legacy = self.conn.execute(
                "SELECT 1 FROM judgments WHERE supersedes = ? LIMIT 1", (target,)
            ).fetchone()
            if row or legacy:
                raise ValueError(f"judgment already superseded: {target}")
        jid = new_id("judg")
        when = ts or now_iso()
        # Keep the first target in the legacy column for one-to-one readers;
        # every target is also recorded in judgment_supersessions.
        legacy_supersedes = targets[0] if targets else None
        self.conn.execute(
            "INSERT INTO judgments(id, ts, claim, falsifier, evidence_refs, confidence, trigger, supersedes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (jid, when, claim.strip(), falsifier.strip(), canonical_json(refs), float(confidence), trigger, legacy_supersedes, now_iso()),
        )
        for target in targets:
            self.conn.execute(
                "INSERT INTO judgment_supersessions(new_id, old_id, created_at) VALUES (?, ?, ?)",
                (jid, target, now_iso()),
            )
        row = self.conn.execute("SELECT * FROM judgments WHERE id = ?", (jid,)).fetchone()
        return Judgment.from_row(row)

    def superseded_targets(self, judgment_id: str) -> list[str]:
        """Old judgment ids that this new judgment replaces (possibly several)."""
        rows = self.conn.execute(
            "SELECT old_id FROM judgment_supersessions WHERE new_id = ? ORDER BY rowid",
            (judgment_id,),
        ).fetchall()
        return [r["old_id"] for r in rows]

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

    def judgment_review_counts(
        self, judgment_ids: Iterable[str]
    ) -> dict[str, dict[str, Any]]:
        """Count how often a future self accepted or rejected each judgment.

        The count is the design's "reconfirmed by a later self" signal: only an
        explicit `supported` review written during sleep counts. A recall or a
        citation is deliberately weaker and is tracked separately.
        """
        ids = [str(x) for x in dict.fromkeys(judgment_ids) if str(x).strip()]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        counts = {
            jid: {
                "supported": 0,
                "refuted": 0,
                "needs-evidence": 0,
                "total": 0,
                "last": None,
            }
            for jid in ids
        }
        rows = self.conn.execute(
            f"SELECT * FROM judgment_reviews WHERE judgment_id IN ({marks}) "
            "ORDER BY ts ASC, rowid ASC",
            tuple(ids),
        ).fetchall()
        for row in rows:
            bucket = counts.get(row["judgment_id"])
            if bucket is None:
                continue
            verdict = row["verdict"]
            if verdict in {"supported", "refuted", "needs-evidence"}:
                bucket[verdict] += 1
            bucket["total"] += 1
            bucket["last"] = {
                "id": row["id"],
                "ts": row["ts"],
                "verdict": verdict,
                "note": row["note"],
                "evidence_refs": _loads(row["evidence_refs"], []),
            }
        return counts

    def judgment_duplicate_groups(
        self,
        *,
        threshold: float = 0.72,
        limit: int = 100,
        max_groups: int = 10,
    ) -> list[list[Judgment]]:
        """Current judgments whose wording is close enough to be worth a look.

        This is a mechanical hint, never an automatic merge: only she can decide
        whether two claims mean the same thing. Superseded rows are excluded, so
        the report shrinks as she consolidates her own lineage.
        """
        cutoff = max(0.0, min(1.0, float(threshold)))
        judgments = self.current_judgments(limit=limit)

        def norm(text: str) -> str:
            return "".join(
                ch
                for ch in (text or "").lower()
                if ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
            )

        normalized = [norm(j.claim) for j in judgments]
        used: set[int] = set()
        groups: list[list[Judgment]] = []
        for i, judgment in enumerate(judgments):
            if i in used or not normalized[i]:
                continue
            group = [i]
            for k in range(i + 1, len(judgments)):
                if k in used or not normalized[k]:
                    continue
                ratio = difflib.SequenceMatcher(
                    None, normalized[i], normalized[k], autojunk=False
                ).ratio()
                if ratio >= cutoff:
                    group.append(k)
            if len(group) > 1:
                used.update(group)
                groups.append([judgments[index] for index in group])
                if len(groups) >= max(1, int(max_groups)):
                    break
        return groups

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

    def current_judgments(self, *, status: str | None = None, limit: int = 50) -> list[Judgment]:
        """Judgments that no later judgment has superseded.

        The full history stays in `judgments()`; this is the thin current view
        used by waking, sleep review and the fish tank page. Without it a
        superseded duplicate would keep appearing as a current belief forever.
        """
        sql = (
            "SELECT j.* FROM judgments j "
            "WHERE NOT EXISTS (SELECT 1 FROM judgment_supersessions s WHERE s.old_id = j.id) "
            "AND NOT EXISTS (SELECT 1 FROM judgments s2 WHERE s2.supersedes = j.id) "
            "AND COALESCE((SELECT r.verdict FROM judgment_reviews r "
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

    def restore_self(self, version_id: str, reason: str, *, actor: str = "operator") -> SelfVersion:
        """Restore an old SELF.md body as a *new* version, leaving history intact."""
        target = self.get_self_version(version_id)
        if target is None:
            raise ValueError(f"unknown self version: {version_id}")
        current = self.current_self_version()
        if current is not None and current.content == target.content:
            raise ValueError(f"self version is already current: {version_id}")
        if not reason.strip():
            raise ValueError("a self restore requires a reason")
        note = self.append_event(
            "system",
            "action",
            f"操作员把 SELF.md 恢复为 {version_id}：{reason}",
            actor=actor,
            refs=[version_id] + ([current.id] if current else []),
            meta={"stage": "self_restore", "target": version_id, "reason": reason},
        )
        refs = list(target.evidence_refs) + [note.id]
        return self.commit_self(
            target.content,
            f"回滚到 {version_id}：{reason}",
            refs,
            accepted_by=actor,
        )

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

    # ---------------------------------------------------------------- practices

    def add_practice(
        self,
        text: str,
        *,
        evidence_refs: Iterable[str],
        note: str | None = None,
        ts: str | None = None,
    ) -> Practice:
        """Record a self-binding rule she intends to follow across sessions.

        The rule itself is declared in L1 first (kind="practice"), then the
        read model row points at that declaration via source_event. Evidence
        refs must already exist; a practice cannot be justified by itself.
        """
        if not text.strip():
            raise ValueError("practice text must be non-empty")
        refs = list(evidence_refs)
        if not refs:
            raise ValueError("a practice requires evidence refs")
        for ref in refs:
            if not self.ref_exists(ref):
                raise ValueError(f"unknown practice evidence ref: {ref}")
        when = ts or now_iso()
        event = self.append_event(
            "ai-observation",
            "practice",
            text.strip(),
            meta={"note": note},
            refs=refs,
            ts=when,
        )
        pid = new_id("practice")
        self.conn.execute(
            "INSERT INTO practices(id, ts, text, status, evidence_refs, source_event, note, updated_at) "
            "VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
            (pid, when, text.strip(), canonical_json(refs), event.id, note, when),
        )
        row = self.conn.execute("SELECT * FROM practices WHERE id = ?", (pid,)).fetchone()
        return Practice.from_row(row)

    def get_practice(self, practice_id: str) -> Practice | None:
        row = self.conn.execute("SELECT * FROM practices WHERE id = ?", (practice_id,)).fetchone()
        return Practice.from_row(row) if row else None

    def practices(self, *, status: str = "active", limit: int = 50) -> list[Practice]:
        if status not in {"active", "dropped"}:
            raise ValueError(f"unknown practice status: {status}")
        rows = self.conn.execute(
            "SELECT * FROM practices WHERE status = ? ORDER BY ts ASC, rowid ASC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [Practice.from_row(row) for row in rows]

    def update_practice(
        self,
        practice_id: str,
        *,
        status: str = "dropped",
        note: str | None = None,
        ts: str | None = None,
    ) -> bool:
        """Drop an active practice, keeping its text and the reason in L1.

        Practices are deliberately not editable in place: a changed rule is a
        new practice with its own evidence. Otherwise an old commitment could
        be silently rewritten to match present behaviour.
        """
        if status != "dropped":
            raise ValueError("a practice can only be dropped, not reactivated or rewritten")
        row = self.conn.execute("SELECT * FROM practices WHERE id = ?", (practice_id,)).fetchone()
        if not row or row["status"] != "active":
            return False
        when = ts or now_iso()
        self.conn.execute(
            "UPDATE practices SET status = 'dropped', note = COALESCE(?, note), updated_at = ? WHERE id = ?",
            (note, when, practice_id),
        )
        self.conn.execute(
            "INSERT INTO practice_reviews(id, practice_id, ts, status, evidence_refs, note, created_at) "
            "VALUES (?, ?, ?, 'dropped', ?, ?, ?)",
            (new_id("preview"), practice_id, when, row["evidence_refs"], note, now_iso()),
        )
        self.append_event(
            "ai-observation",
            "action",
            f"dropped practice: {str(row['text'])[:120]}",
            meta={"practice_id": practice_id, "status": "dropped", "note": note},
            refs=_loads(row["evidence_refs"], []),
            ts=when,
        )
        return True

    def review_practice(
        self,
        practice_id: str,
        *,
        verdict: str,
        evidence_refs: Iterable[str] = (),
        note: str | None = None,
        ts: str | None = None,
    ) -> PracticeReview | None:
        """Record whether a practice was followed, violated, or not exercised.

        A check is evidence about the rule, not a rewrite of it: the practice
        stays active until she explicitly drops it. Refs must predate the
        practice's own timestamp, so a rule cannot be retroactively justified
        by events from after it was written.
        """
        if verdict not in {"followed", "violated", "untested"}:
            raise ValueError(f"unknown practice verdict: {verdict}")
        row = self.conn.execute("SELECT * FROM practices WHERE id = ?", (practice_id,)).fetchone()
        if not row or row["status"] != "active":
            return None
        refs = list(dict.fromkeys(str(ref) for ref in evidence_refs if str(ref).strip()))
        for ref in refs:
            event = self.get_event(ref)
            if event is None:
                raise ValueError(f"unknown practice check evidence ref: {ref}")
            if parse_ts(event.ts) < parse_ts(row["ts"]):
                raise ValueError(f"practice check evidence predates the practice: {ref}")
        if verdict != "untested" and not refs:
            raise ValueError(f"a {verdict} practice check requires evidence refs")
        when = ts or now_iso()
        review_id = new_id("preview")
        self.conn.execute(
            "INSERT INTO practice_reviews(id, practice_id, ts, status, evidence_refs, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (review_id, practice_id, when, verdict, canonical_json(refs), note, now_iso()),
        )
        source_ref = row["source_event"] or practice_id
        self.append_event(
            "ai-observation",
            "action",
            f"practice {verdict}: {str(row['text'])[:120]}",
            meta={"practice_id": practice_id, "status": verdict, "note": note},
            refs=refs or [source_ref],
            ts=when,
        )
        saved = self.conn.execute(
            "SELECT * FROM practice_reviews WHERE id = ?", (review_id,)
        ).fetchone()
        return PracticeReview.from_row(saved)

    def practice_reviews(self, practice_id: str, *, limit: int = 50) -> list[PracticeReview]:
        rows = self.conn.execute(
            "SELECT * FROM practice_reviews WHERE practice_id = ? ORDER BY ts ASC, rowid ASC LIMIT ?",
            (practice_id, limit),
        ).fetchall()
        return [PracticeReview.from_row(row) for row in rows]

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

    def update_open_loop(
        self,
        loop_id: str,
        detail: str,
        *,
        due: str | None = None,
        note: str | None = None,
    ) -> bool:
        """Update the reason/detail of an open loop, leaving an L1 action."""
        if not detail.strip():
            raise ValueError("loop detail must be non-empty")
        if due:
            parse_ts(due)
        row = self.conn.execute("SELECT * FROM open_loops WHERE id = ?", (loop_id,)).fetchone()
        if not row or row["status"] != "open":
            return False
        new_due = due if due is not None else row["due"]
        self.conn.execute(
            "UPDATE open_loops SET detail = ?, due = ?, updated_at = ? WHERE id = ?",
            (detail, new_due, now_iso(), loop_id),
        )
        self.append_event(
            "ai-observation",
            "action",
            f"updated loop: {row['title']}",
            meta={
                "loop_id": loop_id,
                "old_detail": row["detail"],
                "new_detail": detail,
                "note": note,
            },
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