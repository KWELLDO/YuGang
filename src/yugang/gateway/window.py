from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..ids import utcnow


@dataclass
class WakeWindow:
    idle_seconds: int = 900
    max_seconds: int = 5400
    started_at: datetime | None = None
    last_activity: datetime | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    wake_count: int = 0

    def is_awake(self) -> bool:
        return self.started_at is not None

    def wake(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        if self.is_awake():
            self.touch(now)
            return False
        self.started_at = now
        self.last_activity = now
        self.transcript = []
        self.wake_count += 1
        return True

    def touch(self, now: datetime | None = None) -> None:
        if self.is_awake():
            self.last_activity = now or utcnow()

    def should_sleep(self, now: datetime | None = None) -> bool:
        if not self.is_awake():
            return False
        now = now or utcnow()
        idle_deadline = (self.last_activity or self.started_at) + timedelta(seconds=max(1, self.idle_seconds))
        hard_deadline = self.started_at + timedelta(seconds=max(1, self.max_seconds))
        return now >= idle_deadline or now >= hard_deadline

    def history(self, *, max_chars: int = 6000, max_turns: int = 12) -> list[dict[str, str]]:
        """Return the last few turns of this wake, bounded by size.

        The transcript is what makes a second message in the same wake a real
        continuation rather than a fresh retrieval over the same system prompt.
        Old turns are dropped from the front, never silently truncated mid-turn.
        """
        kept: list[dict[str, str]] = []
        total = 0
        for message in reversed(self.transcript[-max_turns:]):
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role not in {"user", "assistant"} or not content:
                continue
            if kept and total + len(content) > max_chars:
                break
            kept.append({"role": role, "content": content})
            total += len(content)
        return list(reversed(kept))

    def sleep(self) -> None:
        self.started_at = None
        self.last_activity = None

    def elapsed(self, now: datetime | None = None) -> float:
        if not self.is_awake():
            return 0.0
        return max(0.0, ((now or utcnow()) - self.started_at).total_seconds())

    def remaining(self, now: datetime | None = None) -> float:
        if not self.is_awake():
            return 0.0
        now = now or utcnow()
        idle = (self.last_activity or self.started_at) + timedelta(seconds=max(1, self.idle_seconds))
        hard = self.started_at + timedelta(seconds=max(1, self.max_seconds))
        return max(0.0, (min(idle, hard) - now).total_seconds())
