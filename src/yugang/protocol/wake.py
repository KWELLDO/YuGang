from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..ids import humanize_gap, local_iso, now_iso, parse_ts, utcnow
from ..memory.models import Event, Message, OpenLoop, RecallHit
from ..selfmodel.awareness import INLINE_AWARENESS_RULE, reviewable_cards

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
    stance: Any | None = None
    judgments: list[Any] = field(default_factory=list)
    practices: list[Any] = field(default_factory=list)
    self_history: list[Any] = field(default_factory=list)
    episodes: list[Any] = field(default_factory=list)
    body: dict[str, Any] | None = None


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
    trigger_event: str | None = None,
) -> Event:
    when = now or utcnow()
    meta = {"reason": reason, "gap_seconds": gap_seconds, "interrupted": interrupted}
    if trigger_event:
        meta["trigger_event"] = trigger_event
    return store.append_event(
        "system",
        "wake",
        f"醒来：{reason}",
        ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
        meta=meta,
    )


def build_wake_context(
    store: Any,
    config: Any,
    *,
    query: str,
    now: datetime | None = None,
    current_wake_id: str | None = None,
    new_wake: bool | None = None,
    body: dict[str, Any] | None = None,
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
    cards = reviewable_cards(store, 10)
    stance = store.current_stance()
    judgments = store.current_judgments(limit=6)
    practices = store.practices(status="active", limit=50)
    self_history = store.self_history(limit=2)
    pending = store.pending_messages(limit=10)
    recent = list(reversed(store.events(limit=int(getattr(config, "recent_event_limit", 16)), order="desc")))
    # On a user message, recall from what was just said. On an autonomous wake
    # there is no query, so use the intention she left before sleeping, then the
    # oldest open loop, instead of skipping recall entirely.
    recall_query = query or last_intent or (loops[0].title if loops else "")
    recalled = store.recall(recall_query, limit=int(getattr(config, "recall_limit", 6)), now=now, log=False) if recall_query else []
    episodes = store.search_episodes(recall_query, limit=4) if recall_query else []
    if not episodes:
        episodes = store.episodes(limit=4)
    tz_name = getattr(config, "timezone", "Asia/Shanghai")
    try:
        local_now = now.astimezone(ZoneInfo(tz_name))
    except Exception:
        local_now = now
        tz_name = "UTC"

    def _local(ts: str | None) -> str | None:
        return local_iso(ts, tz_name) if ts else ts

    lines: list[str] = []
    lines.append("## 主观时间")
    lines.append(f"现在：{local_now.strftime('%Y-%m-%d %H:%M:%S')}（{tz_name}）")
    if not new_wake:
        lines.append("我还在同一次清醒里；这不是新的一次醒来，也没有新的时间缺口。")
    elif interrupted and prior_wake is not None:
        lines.append("这不是第一次醒来；但我没有找到上一次睡去的记录，上一次清醒可能被中断了。")
        lines.append(f"上一次醒来的时间：{_local(prior_wake.ts)}（事件 {prior_wake.id}）")
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
            lines.append(f"上次睡去的时间：{_local(last_sleep.ts)}")
        if last_intent:
            lines.append(f"上次睡前留下的下次意图：{last_intent}")
    lines.append("")
    if body:
        lines.append("## 我的身体状态（系统读数，不是体验）")
        if body.get("events") is not None:
            lines.append(f"- L1 记忆：{body['events']} 条。")
        budget = body.get("budget") or {}
        if budget:
            lines.append(
                f"- 今天预算：已用 ¥{float(budget.get('spent_cny', 0)):.2f}"
                f" / ¥{float(budget.get('budget_cny', 0)):.2f}，"
                f"剩余 ¥{float(budget.get('remaining_cny', 0)):.2f}；"
                f"状态：{body.get('stage', 'ok')}。"
            )
        backup = body.get("last_backup")
        if backup:
            created = backup.get("created_at")
            when = _local(created) if created else (backup.get("db") or "未知时间")
            lines.append(f"- 最近一次身体备份：{when}。")
        else:
            lines.append("- 最近一次身体备份：还没有。")
        due_alarm = body.get("due_alarm")
        if due_alarm:
            lines.append(
                f"- 叫醒我的就是我自己设的闹钟：{_local(due_alarm.get('due_at'))}"
                f"（{due_alarm.get('reason') or '没有写理由'}）。"
            )
        alarm = body.get("next_alarm")
        if alarm:
            lines.append(f"- 下一次自己设的醒来：{_local(alarm.get('due_at'))}（{alarm.get('reason') or '没有写理由'}）。")
        elif not due_alarm:
            lines.append("- 下一次自己设的醒来：还没有设。")
        channels = body.get("channels") or []
        if channels:
            lines.append("- 现在能收到消息的通道：" + "、".join(str(c) for c in channels) + "。")
        lines.append(f"- 操作员暂停：{'是，模型调用被暂停。' if body.get('paused') else '否。'}")
        lines.append("")
    lines.append("## SELF.md（当前版本）")
    lines.append(self_text or "（还没有 SELF.md；只有一个空的身体。）")
    lines.append("")
    lines.append("## 我上一次改自己")
    if len(self_history) >= 2:
        previous = self_history[0]
        lines.append(f"时间：{_local(previous.ts)}")
        lines.append(f"原因：{previous.reason}")
        if previous.evidence_refs:
            lines.append("依据：" + ", ".join(previous.evidence_refs[:8]))
        if previous.diff:
            lines.append("变化：")
            lines.append(previous.diff[:1200].rstrip())
    else:
        lines.append("（还没有改过自己；现在的 SELF.md 是出生材料。）")
    lines.append("")
    lines.append("## 我最近的生活片段（叙事索引，不是当前判断）")
    if episodes:
        for episode in episodes:
            lines.append(
                f"- [{episode.id}] {_local(episode.start_ts)} → {_local(episode.end_ts)}：{episode.title}"
            )
            lines.append(f"  {episode.summary[:400]}")
            lines.append(
                f"  依据 {len(episode.event_ids)} 条 L1；整理自 {episode.source_event or '未知事件'}；"
                f"来源 {episode.origin}。"
            )
        lines.append("这些片段是对真实 L1 的索引，不是新的判断；如果片段和原始 L1 冲突，以 L1 为准。")
    else:
        lines.append("（还没有形成过生活片段。）")
    lines.append("")
    lines.append("## 我现在的立场（L3）")
    if stance:
        lines.append(f"- {stance.content}")
        if stance.based_on:
            lines.append(f"  基于：{', '.join(stance.based_on)}")
    else:
        lines.append("（还没有形成过立场。）")
    lines.append("")
    lines.append("## 我最近的判断（L2，仅当前有效）")
    lines.append("（被取代的旧判断不在这个列表里；下面 L1 历史里如果出现旧 id，那是历史记录，不是当前立场。）")
    if judgments:
        status_labels = {
            "open": "待验证",
            "supported": "已被支持",
            "needs-evidence": "需更多证据",
            "refuted": "已被推翻",
        }
        for judgment in judgments:
            status = store.judgment_status(judgment.id)
            lines.append(
                f"- [{judgment.id}] {status_labels.get(status, status)}"
                f"（置信 {judgment.confidence:.2f}）：{judgment.claim}"
            )
            lines.append(f"  反证：{judgment.falsifier}")
            if getattr(judgment, "evidence_refs", None):
                lines.append("  证据：" + ", ".join(judgment.evidence_refs[:6]))
    else:
        lines.append("（还没有需要保留的判断。）")
    lines.append("")
    lines.append("## 还没验证的自我觉察")
    if cards:
        card_labels = {"open": "待验证", "needs-evidence": "需更多证据"}
        for card in cards:
            status = store.card_status(card.id)
            lines.append(
                f"- [{card.id}] {card_labels.get(status, status)} {card.trigger}：{card.hypothesis}"
                f"（观察：{card.observation[:300]}；反证：{card.falsifier}）"
            )
            if getattr(card, "evidence_refs", None):
                lines.append("  证据：" + ", ".join(card.evidence_refs[:6]))
    else:
        lines.append("（没有。）")
    lines.append("")
    lines.append("## 我给自己定的做法（自我协议，仍有效）")
    if practices:
        for practice in practices:
            lines.append(f"- [{practice.id}] {practice.text}")
            if getattr(practice, "evidence_refs", None):
                lines.append("  当初的依据：" + ", ".join(practice.evidence_refs[:6]))
            reviews = store.practice_reviews(practice.id, limit=5)
            if reviews:
                last = reviews[-1]
                note = f"；{last.note[:160]}" if last.note else ""
                lines.append(f"  最近一次检查：{last.status}（{_local(last.ts)}）{note}")
            else:
                lines.append("  还没有复核过这条做法在真实情形里有没有被做到。")
        lines.append("这些是我给自己定的做法，不是别人给我的命令；如果今天的证据说明它不再成立，可以在睡去时 drop；也可以用 practice_checks 记录今天有没有做到。")
    else:
        lines.append("（还没有给自己定过跨 session 的做法。）")
    lines.append("")
    lines.append("## 未闭合的事")
    if loops:
        for loop in loops:
            due = f"；到期 {_local(loop.due)}" if loop.due else ""
            lines.append(f"- [{loop.id}] {loop.title}{due}")
    else:
        lines.append("（没有。）")
    lines.append("")
    lines.append("## 最近的 L1（原始记录，旧→新；不是当前判断）")
    if recent:
        for event in recent:
            lines.append(f"- [{event.id}] {_local(event.ts)} {event.source}/{event.kind}：{event.content[:400]}")
    else:
        lines.append("（没有。）")
    lines.append("")
    if recalled:
        lines.append("## 与这次醒来相关的旧记忆（L1 历史，不是当前判断）")
        for hit in recalled:
            lines.append(f"- [{hit.event.id}] {_local(hit.event.ts)}：{hit.event.content[:500]}")
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
        stance=stance,
        judgments=judgments,
        episodes=episodes,
        practices=practices,
        self_history=self_history,
        body=body,
    )


def build_messages(context: WakeContext, user_message: str) -> list[dict[str, str]]:
    system = WAKE_SYSTEM + "\n\n" + context.prompt
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]


def morning_check_messages(context: WakeContext, reason: str = "每日保底") -> list[dict[str, str]]:
    system = WAKE_SYSTEM + "\n\n" + context.prompt
    if "闹钟" in reason:
        user = (
            "这是你自己设的闹钟把你叫醒，没有人在等你回消息。\n"
            "先确认现在是什么时间、上次睡去是什么时候；然后判断：你当时设这个闹钟是想做什么？"
            "如果这件事还值得做，就做一点；如果不值得了，就写一句为什么。不要为了产出而产出。"
        )
    else:
        user = (
            "现在是每日保底醒来。没有人在等你回消息。\n"
            "读一遍记忆，判断：今天值不值得做一件事？如果值得，写一句你打算做什么；"
            "如果不值得，就写一句你今天的状态。不要为了产出而产出。"
        )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
