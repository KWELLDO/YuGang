from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


@dataclass(frozen=True)
class Event:
    id: str
    ts: str
    source: str
    kind: str
    content: str
    actor: str | None
    channel: str | None
    refs: tuple[str, ...]
    meta: dict[str, Any]
    prev_hash: str
    hash: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Event":
        return cls(
            id=row["id"],
            ts=row["ts"],
            source=row["source"],
            kind=row["kind"],
            content=row["content"],
            actor=row["actor"],
            channel=row["channel"],
            refs=tuple(_loads(row["refs"], [])),
            meta=_loads(row["meta"], {}),
            prev_hash=row["prev_hash"],
            hash=row["hash"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Judgment:
    id: str
    ts: str
    claim: str
    falsifier: str
    evidence_refs: tuple[str, ...]
    confidence: float
    trigger: str | None
    supersedes: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Judgment":
        return cls(
            id=row["id"],
            ts=row["ts"],
            claim=row["claim"],
            falsifier=row["falsifier"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            confidence=float(row["confidence"]),
            trigger=row["trigger"],
            supersedes=row["supersedes"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class JudgmentReview:
    id: str
    judgment_id: str
    ts: str
    verdict: str
    evidence_refs: tuple[str, ...]
    note: str | None
    created_at: str


@dataclass(frozen=True)
class Stance:
    id: str
    ts: str
    content: str
    based_on: tuple[str, ...]
    trigger: str | None
    created_at: str


@dataclass(frozen=True)
class SelfVersion:
    id: str
    ts: str
    content: str
    diff: str | None
    reason: str
    evidence_refs: tuple[str, ...]
    accepted_by: str
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "SelfVersion":
        return cls(
            id=row["id"],
            ts=row["ts"],
            content=row["content"],
            diff=row["diff"],
            reason=row["reason"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            accepted_by=row["accepted_by"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class AwarenessCard:
    id: str
    ts: str
    trigger: str
    observation: str
    hypothesis: str
    falsifier: str
    evidence_refs: tuple[str, ...]
    confidence: float
    proposed_self_diff: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "AwarenessCard":
        return cls(
            id=row["id"],
            ts=row["ts"],
            trigger=row["trigger"],
            observation=row["observation"],
            hypothesis=row["hypothesis"],
            falsifier=row["falsifier"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            confidence=float(row["confidence"]),
            proposed_self_diff=row["proposed_self_diff"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Alarm:
    id: str
    ts: str
    due_at: str
    reason: str | None
    status: str
    source_event: str | None
    fired_event: str | None
    note: str | None
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Alarm":
        return cls(
            id=row["id"],
            ts=row["ts"],
            due_at=row["due_at"],
            reason=row["reason"],
            status=row["status"],
            source_event=row["source_event"],
            fired_event=row["fired_event"],
            note=row["note"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class OpenLoop:
    id: str
    ts: str
    title: str
    detail: str | None
    status: str
    due: str | None
    refs: tuple[str, ...]
    updated_at: str


@dataclass(frozen=True)
class Practice:
    id: str
    ts: str
    text: str
    status: str
    evidence_refs: tuple[str, ...]
    source_event: str | None
    note: str | None
    updated_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Practice":
        return cls(
            id=row["id"],
            ts=row["ts"],
            text=row["text"],
            status=row["status"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            source_event=row["source_event"],
            note=row["note"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class PracticeReview:
    id: str
    practice_id: str
    ts: str
    status: str
    evidence_refs: tuple[str, ...]
    note: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "PracticeReview":
        return cls(
            id=row["id"],
            practice_id=row["practice_id"],
            ts=row["ts"],
            status=row["status"],
            evidence_refs=tuple(_loads(row["evidence_refs"], [])),
            note=row["note"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class Message:
    id: str
    channel: str
    external_id: str | None
    direction: str
    sender: str | None
    content: str
    status: str
    created_at: str
    processed_at: str | None
    error: str | None
    meta: dict[str, Any]

    @classmethod
    def from_row(cls, row: Any) -> "Message":
        return cls(
            id=row["id"],
            channel=row["channel"],
            external_id=row["external_id"],
            direction=row["direction"],
            sender=row["sender"],
            content=row["content"],
            status=row["status"],
            created_at=row["created_at"],
            processed_at=row["processed_at"],
            error=row["error"],
            meta=_loads(row["meta"], {}),
        )


@dataclass(frozen=True)
class Person:
    """Someone she knows. Identity metadata, not a memory: who, not what happened."""

    id: str
    name: str
    relation: str
    note: str
    first_seen_ts: str
    last_seen_ts: str
    handles: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Episode:
    id: str
    ts: str
    start_ts: str
    end_ts: str
    title: str
    summary: str
    event_ids: tuple[str, ...]
    origin: str
    source_event: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: Any) -> "Episode":
        return cls(
            id=row["id"],
            ts=row["ts"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            title=row["title"],
            summary=row["summary"],
            event_ids=tuple(_loads(row["event_ids"], [])),
            origin=row["origin"],
            source_event=row["source_event"],
            created_at=row["created_at"],
        )


@dataclass(frozen=True)
class RecallHit:
    event: Event
    score: float
    snippet: str
    confirmations: int = 0
    recall_count: int = 0


@dataclass(frozen=True)
class BudgetSummary:
    day: str
    budget_cny: float
    spent_cny: float
    remaining_cny: float
    by_kind: dict[str, float] = field(default_factory=dict)
