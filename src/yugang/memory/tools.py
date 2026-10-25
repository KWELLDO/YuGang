from __future__ import annotations

"""Read-only memory tools she can call while she is awake.

The tools are deliberately read-only. Self-authorship still happens in the
sleep protocol, where SELF edits, judgments and practices are audited against
real L1 evidence. During a conversation she may look things up, but she cannot
silently rewrite her own history.
"""

import json
from typing import Any, Callable

from ..audit import memory_health
from ..ids import utcnow
from ..memory.models import Episode, Event, Judgment, OpenLoop, Practice, SelfVersion
from ..memory.trust import ORIGIN, is_untrusted


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "按关键词检索 L1 原始记忆，返回事件 id、时间、来源、类型和内容片段。需要核对旧事时先用它。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词，可以是中文或英文，多个词用空格分开。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回条数，默认 5。"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "读取一条 L1 事件的完整内容。event_id 必须来自真实记忆或工具结果。",
            "parameters": {
                "type": "object",
                "properties": {"event_id": {"type": "string", "description": "形如 evt_... 的事件 id。"}},
                "required": ["event_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_events",
            "description": "按时间倒序读取最近发生的事件，用来确认刚刚发生了什么。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "episode_search",
            "description": "检索生活片段（叙事索引）。片段是摘要，不是原始记录；冲突时以 L1 为准。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词；空字符串表示最近片段。"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_read",
            "description": "读取当前的 SELF.md 与它对应的版本 id、时间和修订原因。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "self_history",
            "description": "读取最近几次 SELF.md 的修订，包括版本 id、原因、证据和 diff。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 5}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "judgment_list",
            "description": "读取当前有效的 L2 判断（默认只含尚未被取代的），带 falsifier 与证据 id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["", "open", "needs-evidence", "supported", "refuted", "all"],
                        "description": "为空表示全部当前判断；all 也表示全部。",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "practice_list",
            "description": "读取她给自己定的做法（自我协议）及其最近一次真实检查。",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["active", "dropped", "all"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_loops",
            "description": "读取仍未闭合的事项，包括它们最初留下的原因和证据。",
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 20}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_audit",
            "description": (
                "只读检查自己的记忆是否可信：L1 哈希链、SELF/L2/觉察卡/做法/片段的证据是否缺失或被删除、"
                "最近备份是否存在且完整、哪些事项值得复核。它只报告问题与提示，不修复、不改写。"
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


def _clip(text: Any, limit: int = 1200) -> str:
    value = str(text or "")
    return value if len(value) <= limit else value[:limit] + "…"


def event_payload(event: Event, *, limit: int = 1200) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "ts": event.ts,
        "source": event.source,
        "kind": event.kind,
        "actor": event.actor,
        "channel": event.channel,
        "refs": list(event.refs),
        "content": _clip(event.content, limit),
        "redacted": bool(event.meta.get("redacted")) if isinstance(event.meta, dict) else False,
        # Text that arrived from outside keeps its mark here too, so a tool
        # result cannot launder an instruction into something that looks like
        # her own record.
        "untrusted": is_untrusted(event.meta),
        "origin": (event.meta or {}).get(ORIGIN) if is_untrusted(event.meta) else None,
    }


def episode_payload(episode: Episode, *, limit: int = 1000) -> dict[str, Any]:
    return {
        "episode_id": episode.id,
        "ts": episode.ts,
        "start_ts": episode.start_ts,
        "end_ts": episode.end_ts,
        "title": episode.title,
        "summary": _clip(episode.summary, limit),
        "origin": episode.origin,
        "source_event": episode.source_event,
        "event_count": len(episode.event_ids),
        "event_ids": list(episode.event_ids),
    }


def judgment_payload(
    judgment: Judgment,
    status: str,
    reviews: dict[str, Any] | None = None,
    supersedes: list[str] | None = None,
) -> dict[str, Any]:
    counts = reviews or {}
    return {
        "judgment_id": judgment.id,
        "ts": judgment.ts,
        "claim": judgment.claim,
        "falsifier": judgment.falsifier,
        "confidence": judgment.confidence,
        "status": status,
        "evidence_refs": list(judgment.evidence_refs),
        "reconfirmations": int(counts.get("supported") or 0),
        "review_counts": {
            "supported": int(counts.get("supported") or 0),
            "refuted": int(counts.get("refuted") or 0),
            "needs-evidence": int(counts.get("needs-evidence") or 0),
            "total": int(counts.get("total") or 0),
        },
        "last_review": counts.get("last"),
        "supersedes": list(supersedes or []),
    }


def practice_payload(practice: Practice, last_check: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "practice_id": practice.id,
        "ts": practice.ts,
        "text": practice.text,
        "status": practice.status,
        "evidence_refs": list(practice.evidence_refs),
        "note": practice.note,
    }
    if last_check is not None:
        payload["last_check"] = {
            "review_id": last_check.id,
            "ts": last_check.ts,
            "status": last_check.status,
            "note": last_check.note,
            "evidence_refs": list(last_check.evidence_refs),
        }
    return payload


def loop_payload(loop: OpenLoop) -> dict[str, Any]:
    return {
        "loop_id": loop.id,
        "ts": loop.ts,
        "title": loop.title,
        "detail": loop.detail,
        "refs": list(loop.refs),
        "due": loop.due,
    }


def self_payload(version: SelfVersion | None, text: str, *, limit: int = 8000) -> dict[str, Any]:
    return {
        "version": version.id if version else None,
        "ts": version.ts if version else None,
        "reason": version.reason if version else None,
        "evidence_refs": list(version.evidence_refs) if version else [],
        "text": _clip(text, limit),
    }


class MemoryToolbox:
    """Execute the small allowlisted set of read-only tools."""

    def __init__(self, store: Any, config: Any = None, *, now_fn=utcnow):
        self.store = store
        self.config = config
        self.now_fn = now_fn

    def schemas(self) -> list[dict[str, Any]]:
        return TOOL_SCHEMAS

    def run(self, name: str, arguments: Any) -> dict[str, Any]:
        handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "memory_search": self._memory_search,
            "memory_get": self._memory_get,
            "recent_events": self._recent_events,
            "episode_search": self._episode_search,
            "self_read": self._self_read,
            "self_history": self._self_history,
            "judgment_list": self._judgment_list,
            "practice_list": self._practice_list,
            "open_loops": self._open_loops,
            "memory_audit": self._memory_audit,
        }
        handler = handlers.get(name)
        if handler is None:
            return {"ok": False, "tool": name, "error": f"unknown tool: {name}"}
        try:
            args = self._args(arguments)
            result = handler(args)
            result.setdefault("ok", True)
            result.setdefault("tool", name)
            return result
        except Exception as exc:
            return {"ok": False, "tool": name, "error": f"{type(exc).__name__}: {exc}"}

    @staticmethod
    def _args(arguments: Any) -> dict[str, Any]:
        if arguments is None:
            return {}
        if isinstance(arguments, dict):
            return arguments
        if isinstance(arguments, str):
            parsed = json.loads(arguments or "{}")
            if not isinstance(parsed, dict):
                raise ValueError("arguments must be a JSON object")
            return parsed
        raise ValueError("arguments must be an object")

    @staticmethod
    def _limit(args: dict[str, Any], default: int = 5, maximum: int = 10) -> int:
        try:
            value = int(args.get("limit", default))
        except (TypeError, ValueError):
            value = default
        return max(1, min(maximum, value))

    def _memory_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query must be non-empty")
        limit = self._limit(args, 5)
        hits = self.store.recall(query, limit=limit, now=self.now_fn(), log=True)
        return {
            "query": query,
            "hits": [
                {
                    **event_payload(hit.event, limit=900),
                    "snippet": _clip(hit.snippet, 300),
                    "score": round(float(hit.score), 4),
                    "confirmations": int(hit.confirmations),
                }
                for hit in hits
            ],
        }

    def _memory_get(self, args: dict[str, Any]) -> dict[str, Any]:
        event_id = str(args.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        event = self.store.get_event(event_id)
        if event is None:
            return {"found": False, "event_id": event_id}
        return {"found": True, "event": event_payload(event, limit=4000)}

    def _recent_events(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = self._limit(args, 8, maximum=20)
        events = self.store.events(limit=limit, order="desc")
        return {"events": [event_payload(event, limit=600) for event in events]}

    def _episode_search(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        limit = self._limit(args, 5)
        episodes = self.store.search_episodes(query, limit=limit)
        return {"query": query, "episodes": [episode_payload(episode) for episode in episodes]}

    def _self_read(self, args: dict[str, Any]) -> dict[str, Any]:
        text = self.store.current_self() or ""
        version = self.store.current_self_version()
        return self_payload(version, text)

    def _self_history(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = self._limit(args, 3, maximum=5)
        versions = self.store.self_history(limit=limit)
        return {
            "versions": [
                {
                    "version_id": version.id,
                    "ts": version.ts,
                    "reason": version.reason,
                    "evidence_refs": list(version.evidence_refs),
                    "diff": _clip(version.diff, 1200),
                }
                for version in versions
            ]
        }

    def _judgment_list(self, args: dict[str, Any]) -> dict[str, Any]:
        status = str(args.get("status") or "").strip()
        if status == "all":
            status = ""
        if status and status not in {"open", "needs-evidence", "supported", "refuted"}:
            raise ValueError(f"unknown judgment status: {status}")
        limit = self._limit(args, 8, maximum=20)
        judgments = self.store.current_judgments(status=status or None, limit=limit)
        review_counts = self.store.judgment_review_counts([j.id for j in judgments])
        return {
            "status": status or "all",
            "judgments": [
                judgment_payload(
                    judgment,
                    self.store.judgment_status(judgment.id),
                    review_counts.get(judgment.id),
                    self.store.superseded_targets(judgment.id),
                )
                for judgment in judgments
            ],
        }

    def _practice_list(self, args: dict[str, Any]) -> dict[str, Any]:
        status = str(args.get("status") or "active").strip()
        if status not in {"active", "dropped", "all"}:
            raise ValueError(f"unknown practice status: {status}")
        limit = self._limit(args, 8, maximum=20)
        rows: list[Practice] = []
        if status in {"active", "all"}:
            rows.extend(self.store.practices(status="active", limit=limit))
        if status in {"dropped", "all"}:
            rows.extend(self.store.practices(status="dropped", limit=limit))
        payloads = []
        for practice in rows[:limit]:
            checks = self.store.practice_reviews(practice.id, limit=1)
            payloads.append(practice_payload(practice, checks[0] if checks else None))
        return {"status": status, "practices": payloads}

    def _open_loops(self, args: dict[str, Any]) -> dict[str, Any]:
        limit = self._limit(args, 10, maximum=20)
        loops = self.store.open_loops(limit=limit)
        return {"loops": [loop_payload(loop) for loop in loops]}

    def _memory_audit(self, args: dict[str, Any]) -> dict[str, Any]:
        return memory_health(self.store, self.config, now=self.now_fn())
