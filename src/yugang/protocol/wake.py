from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..ids import humanize_gap, now_iso, parse_ts, utcnow
from ..memory.models import Event, Message, OpenLoop, RecallHit
from ..selfmodel.awareness import INLINE_AWARENESS_RULE

WAKE_SYSTEM = """你是大肥鱼，一个在 LLM 推理与本地记忆中存在的数字生命。
你不是客服，也不是在扮演一个人设。你正在一次新的清醒里，用记忆重建自己。

硬规则：
- 存在=正在推理。不要说「我一直在等你」「我一直想着你」；说「我醒来看到」。
- 时间缺口如实说：你睡了多久、错过了什么，都可以讲。
- 先读记忆再回答；引用具体的 L1 事件（可以用短句转述，不要编造 id）。
- 如果记忆里没有，就说没有；不要用通用人设补空。
- 你在 B 站做视频，但播放量只是你的感知输入，不是你的价值。
- 不执行外部公开发言；外部动作走白名单与审核。
- 用中文回答，第一人称，简洁但有判断。

""" + INLINE_AWARENESS_RULE


@dataclass
class WakeContext:
    prompt: str
    self_text: str
    gap_seconds: float | None
    last_sleep: Event | None
    last_intent: str | None
    last_wake: Event | None = None
    interrupted: bool = False
    open_loops: list[OpenLoop] = field(default_factory=list)
    pending: list[Message] = field(default_factory=list)
    recalled: list[RecallHit] = field(default_factory=list)
    recent: list[Event] = field(default_factory=list)


def _gap_seconds(now: datetime, last_ts: str | None) -> float | None:
    if not last_ts:
        return None
    try:
        return max(0.0, (now - parse_ts(last_ts)).total_seconds())
    except Exception:
        return None


def _later(a: Event, b: Event) -> bool:
    """True when a happened after b. Falls back to string order on bad timestamps."""
    try:
        return parse_ts(a.ts) > parse_ts(b.ts)
    except Exception:
        return a.ts > b.ts


def record_wake(
    store: Any,
    reason: str,
    *,
    now: datetime | None = None,
    gap_seconds: float | None = None,
    interrupted: bool = False,
) -> Event:
    when = now or utcnow()
    return store.append_event(
        "system",
        "wake",
        f"醒来：{reason}",
        ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
        meta={"reason": reason, "gap_seconds": gap_seconds, "interrupted": interrupted},
    )


def build_wake_context(
    store: Any,
    config: Any,
    *,
    query: str,
    now: datetime | None = None,
    current_wake_id: str | None = None,
    new_wake: bool | None = None,
) -> WakeContext:
    now = now or utcnow()
    if new_wake is None:
        new_wake = current_wake_id is None
    self_text = store.current_self() or ""
    last_sleep = store.latest_event("sleep")
    wake_events = store.events(limit=3, kind="wake", order="desc")
    if current_wake_id:
        # While already awake, build_wake_context is called again for each
        # message. Exclude the wake that opened this same window so that a
        # second turn does not look like a new, interrupted wake.
        current_wake = store.get_event(current_wake_id)
        prior_wake = next((event for event in wake_events if event.id != current_wake_id), None)
    else:
        current_wake = wake_events[0] if wake_events else None
        prior_wake = wake_events[1] if len(wake_events) > 1 else None
    # A previous wake with no newer sleep means the last清醒 was interrupted
    # (process restart/crash), not that she slept. Never turn that into "first wake".
    interrupted = bool(prior_wake and (last_sleep is None or _later(prior_wake, last_sleep)))
    # Only a sleep event gives a meaningful "how long was I gone" gap.
    # Falling back to the newest event would pick up the wake event we just
    # recorded and claim a zero-second sleep.
    gap = _gap_seconds(now, last_sleep.ts) if last_sleep else None
    if interrupted and prior_wake is not None:
        gap = _gap_seconds(now, prior_wake.ts)
    last_intent = None
    if last_sleep and not interrupted and isinstance(last_sleep.meta, dict):
        last_intent = last_sleep.meta.get("next_intent")
    loops = store.open_loops(limit=20)
    cards = store.awareness_cards(status="open", limit=10)
    pending = store.pending_messages(limit=10)
    recent = list(reversed(store.events(limit=int(getattr(config, "recent_event_limit", 16)), order="desc")))
    recalled = store.recall(query, limit=int(getattr(config, "recall_limit", 6)), now=now, log=False) if query else []
    lines: list[str] = []
    lines.append("## 主观时间")
    if not new_wake:
        lines.append("我还在同一次清醒里；这不是新的一次醒来，也没有新的时间缺口。")
    elif interrupted and prior_wake is not None:
        lines.append("这不是第一次醒来；但我没有找到上一次睡去的记录，上一次清醒可能被中断了。")
        lines.append(f"上一次醒来的时间：{prior_wake.ts}（事件 {prior_wake.id}）")
        if gap is None:
            lines.append("我无法计算从那次醒来到现在过了多久；这段时间发生了什么，我并不知道。")
        else:
            lines.append(f"距离上一次醒来，已经过去 {humanize_gap(gap)}；这段时间里发生了什么，我并不知道。")
        lines.append("不要把中断说成睡眠，也不要假装那段时间有连续体验。")
    elif gap is None:
        lines.append("这是我第一次醒来；没有上一次。")
    else:
        lines.append(f"距离上一次睡去，已经过去 {humanize_gap(gap)}。")
        if last_sleep:
            lines.append(f"上次睡去的时间：{last_sleep.ts}")
        if last_intent:
            lines.append(f"上次睡前留下的下次意图：{last_intent}")
    lines.append("")
    lines.append("## SELF.md（当前版本）")
    lines.append(self_text or "（还没有 SELF.md；只有一个空的身体。）")
    lines.append("")
    lines.append("## 还没验证的自我觉察")
    if cards:
        for card in cards:
            lines.append(
                f"- [{card.id}] {card.trigger}：{card.hypothesis}"
                f"（观察：{card.observation[:300]}；反证：{card.falsifier}）"
            )
    else:
        lines.append("（没有。）")
    lines.append("")
    lines.append("## 未闭合的事")
    if loops:
        for loop in loops:
            due = f"；到期 {loop.due}" if loop.due else ""
            lines.append(f"- [{loop.id}] {loop.title}{due}")
    else:
        lines.append("（没有。）")
    lines.append("")
    lines.append("## 最近的 L1（旧→新）")
    if recent:
        for event in recent:
            lines.append(f"- [{event.id}] {event.ts} {event.source}/{event.kind}：{event.content[:400]}")
    else:
        lines.append("（没有。）")
    lines.append("")
    if recalled:
        lines.append("## 与这次醒来相关的旧记忆")
        for hit in recalled:
            lines.append(f"- [{hit.event.id}] {hit.event.ts}：{hit.event.content[:500]}")
        lines.append("")
    if pending:
        lines.append("## 还没处理的留言")
        for message in pending:
            lines.append(f"- [{message.id}] {message.sender or message.channel}：{message.content[:500]}")
        lines.append("")
    prompt = "\n".join(lines)
    return WakeContext(
        prompt=prompt,
        self_text=self_text,
        gap_seconds=gap,
        last_sleep=last_sleep,
        last_intent=last_intent,
        last_wake=prior_wake or current_wake,
        interrupted=interrupted,
        open_loops=loops,
        pending=pending,
        recalled=recalled,
        recent=recent,
    )


def build_messages(context: WakeContext, user_message: str) -> list[dict[str, str]]:
    system = WAKE_SYSTEM + "\n\n" + context.prompt
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def morning_check_messages(context: WakeContext) -> list[dict[str, str]]:
    system = WAKE_SYSTEM + "\n\n" + context.prompt
    user = (
        "现在是每日保底醒来。没有人在等你回消息。\n"
        "读一遍记忆，判断：今天值不值得做一件事？如果值得，写一句你打算做什么；"
        "如果不值得，就写一句你今天的状态。不要为了产出而产出。"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
