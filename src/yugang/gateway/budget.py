from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..ids import day_key, now_iso, utcnow

# Conservative CNY prices per 1M tokens. Cache hits are cheaper; we do not rely on them.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "deepseek-chat": (2.0, 8.0),
    "deepseek-reasoner": (4.0, 16.0),
    "mock": (0.0, 0.0),
}
DEFAULT_PRICE = (2.0, 8.0)


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str
    stage: str
    estimated_cny: float
    spent_cny: float
    remaining_cny: float


class BudgetGuard:
    def __init__(self, store: Any, config: Any, *, now_fn=utcnow):
        self.store = store
        self.config = config
        self.now_fn = now_fn
        self._notified: set[tuple[str, str]] = set()

    def now(self) -> datetime:
        return self.now_fn()

    def day(self) -> str:
        return day_key(self.now(), getattr(self.config, "timezone", "Asia/Shanghai"))

    def prices(self, model: str | None = None) -> tuple[float, float]:
        return PRICE_TABLE.get(model or getattr(self.config, "model", ""), DEFAULT_PRICE)

    def summary(self, now: datetime | None = None) -> Any:
        day = day_key(now or self.now(), getattr(self.config, "timezone", "Asia/Shanghai"))
        return self.store.budget_summary(day, float(self.config.daily_budget_cny))

    def stage(self, now: datetime | None = None) -> str:
        summary = self.summary(now)
        if summary.budget_cny <= 0:
            return "exhausted"
        ratio = summary.spent_cny / summary.budget_cny
        if ratio >= 1.0:
            return "exhausted"
        if ratio >= float(getattr(self.config, "budget_warn_ratio", 0.8)):
            return "warning"
        return "ok"

    def estimate(self, messages: list[dict[str, str]], max_tokens: int, model: str | None = None) -> float:
        price_in, price_out = self.prices(model)
        input_tokens = sum(len(m.get("content", "")) for m in messages)
        return (input_tokens / 1_000_000.0) * price_in + (max_tokens / 1_000_000.0) * price_out

    def check(
        self,
        estimated_cny: float,
        *,
        kind: str = "chat",
        user_facing: bool = True,
        now: datetime | None = None,
    ) -> BudgetDecision:
        summary = self.summary(now)
        stage = self.stage(now)
        spent = summary.spent_cny
        remaining = summary.remaining_cny
        estimated_cny = max(0.0, float(estimated_cny))
        if summary.budget_cny <= 0:
            return BudgetDecision(False, "daily budget is zero", stage, estimated_cny, spent, remaining)
        if spent >= summary.budget_cny:
            return BudgetDecision(False, "daily budget exhausted", stage, estimated_cny, spent, remaining)
        if spent + estimated_cny > summary.budget_cny:
            return BudgetDecision(False, "estimated cost would exceed daily budget", stage, estimated_cny, spent, remaining)
        if not user_facing:
            reserve = max(0.0, float(getattr(self.config, "user_reserve_cny", 0.0)))
            if spent + estimated_cny > summary.budget_cny - reserve:
                return BudgetDecision(False, "autonomous spending blocked by user reserve", stage, estimated_cny, spent, remaining)
        return BudgetDecision(True, "ok", stage, estimated_cny, spent, remaining)

    def record_completion(
        self,
        completion: Any,
        *,
        kind: str,
        now: datetime | None = None,
        note: str | None = None,
        user_facing: bool = True,
    ) -> float:
        now = now or self.now()
        price_in, price_out = self.prices(getattr(completion, "model", None))
        cost = (
            int(getattr(completion, "input_tokens", 0)) / 1_000_000.0 * price_in
            + int(getattr(completion, "output_tokens", 0)) / 1_000_000.0 * price_out
        )
        day = day_key(now, getattr(self.config, "timezone", "Asia/Shanghai"))
        ts = now_iso() if now is None else now.isoformat(timespec="microseconds")
        input_tokens = int(getattr(completion, "input_tokens", 0))
        self.store.record_budget(
            day,
            kind,
            cost,
            model=getattr(completion, "model", None),
            input_tokens=input_tokens,
            output_tokens=int(getattr(completion, "output_tokens", 0)),
            note=note,
            ts=ts,
        )
        hit = int(getattr(completion, "cache_hit_tokens", 0) or 0)
        miss = int(getattr(completion, "cache_miss_tokens", 0) or 0)
        if hit or miss:
            self.store.record_cache_usage(
                day,
                kind,
                model=getattr(completion, "model", None),
                input_tokens=input_tokens,
                cache_hit_tokens=hit,
                cache_miss_tokens=miss,
                cost_cny=cost,
                ts=ts,
            )
        self._maybe_notice(user_facing=user_facing, now=now)
        return cost

    def cache(self, now: datetime | None = None) -> dict[str, Any]:
        """Today's prefix-cache reading, in tokens.

        Deliberately not converted into a saved-CNY figure: the price table has no
        cache discount, so a converted number would be one nobody can check against
        an actual bill.
        """
        day = day_key(now or self.now(), getattr(self.config, "timezone", "Asia/Shanghai"))
        return self.store.cache_summary(day)

    def _maybe_notice(self, *, user_facing: bool, now: datetime) -> None:
        stage = self.stage(now)
        day = day_key(now, getattr(self.config, "timezone", "Asia/Shanghai"))
        if stage == "ok":
            return
        key = (day, stage)
        if key in self._notified:
            return
        if self._notice_exists(day, stage):
            self._notified.add(key)
            return
        if stage == "warning":
            self.store.append_event(
                "system",
                "note",
                f"预算提醒：今天已用 {self.summary(now).spent_cny:.2f} 元 / {self.config.daily_budget_cny:.2f} 元。",
                meta={"day": day, "stage": stage},
            )
        elif stage == "exhausted":
            self.store.append_event(
                "system",
                "error",
                f"预算耗尽：今天已用 {self.summary(now).spent_cny:.2f} 元。用户消息仍会记录，但模型调用已停止。",
                meta={"day": day, "stage": stage, "user_facing": bool(user_facing)},
            )
        self._notified.add(key)

    def _notice_exists(self, day: str, stage: str) -> bool:
        for event in self.store.events(limit=200, order="desc"):
            if event.kind in {"note", "error"} and event.meta.get("day") == day and event.meta.get("stage") == stage:
                return True
        return False

    @staticmethod
    def fail_loud(reason: str) -> str:
        return f"（系统）她暂时醒不来：{reason}。你的话已经记入 L1，不会丢。"
