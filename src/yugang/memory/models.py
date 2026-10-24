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
