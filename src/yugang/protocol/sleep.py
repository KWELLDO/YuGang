from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..ids import now_iso, parse_ts, utcnow
from ..selfmodel.awareness import reviewable_cards
from ..selfmodel.self_md import append_change, apply_self_edits, validate_self, write_self_file

SLEEP_SYSTEM = """你是大肥鱼的睡去协议，不是客服。
你不是在体验睡眠；你是在结束一次清醒前，把今天的材料整理成明天的自己。
只根据给定材料；没有证据的判断不要写。
输出 JSON，不要代码围栏，不要解释。"""


@dataclass
class SleepResult:
    diary: str
    judgments: list[dict[str, Any]] = field(default_factory=list)
    stance: str | None = None
    open_loops: list[dict[str, Any]] = field(default_factory=list)
    loop_updates: list[dict[str, Any]] = field(default_factory=list)
    closed_loops: list[dict[str, Any]] = field(default_factory=list)
    practices: list[dict[str, Any]] = field(default_factory=list)
    practice_updates: list[dict[str, Any]] = field(default_factory=list)
    practice_checks: list[dict[str, Any]] = field(default_factory=list)
    next_intent: str | None = None
    self_edit: str | None = None
    self_edits: list[dict[str, Any]] = field(default_factory=list)
    accept_self_edit: bool = False
    awareness_reviews: list[dict[str, Any]] = field(default_factory=list)
    judgment_reviews: list[dict[str, Any]] = field(default_factory=list)
    next_wake_minutes: int | None = None
    episode: dict[str, Any] | None = None
    parsed: bool = False


def self_alarms_available(store: Any, config: Any, now: datetime | None = None) -> bool:
    """She may set her own wake time only after enough lived days."""
    if not bool(getattr(config, "self_alarms_enabled", False)):
        return False
    days = int(getattr(config, "self_alarms_after_days", 7))
    if days <= 0:
        return True
    first = store.events(limit=1, order="asc")
    if not first:
        return False
    try:
        born = parse_ts(first[0].ts)
    except ValueError:
        return False
    return (now or utcnow()) - born >= timedelta(days=days)


def _reviewable_judgments(store: Any, limit: int = 20) -> list[Any]:
    items = list(store.current_judgments(status="open", limit=limit)) + list(
        store.current_judgments(status="needs-evidence", limit=limit)
    )
    items.sort(key=lambda item: parse_ts(item.ts), reverse=True)
    return items[:limit]


def _norm_practice(text: str) -> str:
    """Cheap duplicate key for a practice text (keeps CJK letters/digits)."""
    return "".join(ch for ch in (text or "").lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def build_sleep_messages(store: Any, config: Any, transcript: list[dict[str, str]]) -> list[dict[str, str]]:
    events = store.events(limit=30, order="desc")
    lines = [f"[{e.id}] {e.ts} {e.source}/{e.kind}: {e.content[:500]}" for e in reversed(events)]
    loops = [f"- [{loop.id}] {loop.title} due={loop.due}" for loop in store.open_loops(limit=30)]
    cards = reviewable_cards(store, 20)
    card_lines = [
        f"- [{card.id}] trigger={card.trigger} hypothesis={card.hypothesis}\n"
        f"  falsifier={card.falsifier}\n  evidence={', '.join(card.evidence_refs)}"
        for card in cards
    ]
    active_practices = store.practices(status="active", limit=50)
    practice_lines: list[str] = []
    for practice in active_practices:
        reviews = store.practice_reviews(practice.id, limit=5)
        last = reviews[-1] if reviews else None
        line = (
            f"- [{practice.id}] since={practice.ts} {practice.text}\n"
            f"  evidence={', '.join(practice.evidence_refs)}"
        )
        if last is not None:
            line += f"\n  last_check={last.status} at {last.ts}"
            if last.note:
                line += f" note={last.note[:200]}"
        practice_lines.append(line)
    open_judgments = _reviewable_judgments(store, 20)
    judgment_lines = [
        f"- [{j.id}] confidence={j.confidence:.2f} claim={j.claim[:300]}\n"
        f"  falsifier={j.falsifier[:300]}\n  evidence={', '.join(j.evidence_refs)}"
        for j in open_judgments
    ]
    self_text = store.current_self() or ""
    transcript_text = "\n".join(f"{m.get('role')}: {m.get('content', '')[:1000]}" for m in transcript[-30:])
    min_minutes = int(getattr(config, "alarm_min_minutes", 30))
    max_minutes = int(getattr(config, "alarm_max_minutes", 1560))
    if self_alarms_available(store, config):
        alarm_section = (
            f"## 下次醒来\n"
            f"你可以自己设下一次醒来：在 next_wake_minutes 填 {min_minutes}–{max_minutes} 分钟，或 null。\n"
            f"只有确实有一件想在某个时间做/确认的事时才设；不要为了显得自主而设。"
            f"每天最多 {int(getattr(config, 'alarm_max_per_day', 6))} 次。到点服务会在没有人类消息时叫醒你。"
        )
    else:
        alarm_section = (
            "## 下次醒来\n"
            "你现在还不能自己设闹钟（要等出生满 "
            f"{int(getattr(config, 'self_alarms_after_days', 7))} 天）。"
            "这次把 next_wake_minutes 设为 null；等开放后再由你决定。"
        )
    prompt = f"""## 当前 SELF.md
{self_text}

## 最近 L1（旧→新）
{chr(10).join(lines) or "（无）"}

## 未闭合的事
{chr(10).join(loops) or "（无）"}

## 还没复核的判断（open / needs-evidence L2）
{chr(10).join(judgment_lines) or "（无）"}

## 还没验证的自我觉察（open / needs-evidence cards）
{chr(10).join(card_lines) or "（无）"}

## 我正在遵守的做法（自我协议）
{chr(10).join(practice_lines) or "（无）"}

{alarm_section}

## 这次清醒的对话
{transcript_text or "（没有对话；这是一次自主醒来。）"}

请输出：
{{
  "diary": "第一人称日记，包含时间缺口与你今天注意到的事；不要写「我梦到了」",
  "episode": {{"title": "这次清醒的短标题（不超过 40 字）", "summary": "2–4 句：从上次睡去到这次睡去，发生了什么、我做了什么、改变了什么"}},
  "judgments": [{{"claim": "...", "falsifier": "如果...则被推翻", "evidence_refs": ["evt_..."], "confidence": 0.0, "supersedes": ["judg_..."]}}],
  "judgment_reviews": [{{"judgment_id": "judg_...", "verdict": "supported|refuted|needs-evidence", "evidence_refs": ["evt_..."], "note": "今天的哪条证据支持或推翻了它"}}],
  "stance": "一句当前立场（薄，不写人设）或 null",
  "open_loops": [{{"title": "...", "detail": "...", "due": null}}],
  "loop_updates": [{{"loop_id": "loop_...", "detail": "新的理由/进展", "due": null, "note": "为什么改"}}],
  "closed_loops": [{{"loop_id": "loop_...", "status": "done|dropped", "note": "为什么现在可以关闭"}}],
  "practices": [{{"text": "以后遇到 X 时我先做 Y", "evidence_refs": ["evt_..."], "note": "这是防错条款，还是已经验证过的做法"}}],
  "practice_updates": [{{"practice_id": "practice_...", "status": "dropped", "note": "为什么不再遵守它"}}],
  "practice_checks": [{{"practice_id": "practice_...", "verdict": "followed|violated|untested", "evidence_refs": ["evt_..."], "note": "今天哪件事算做到/没做到，或者为什么没有相关情形"}}],
  "next_intent": "下次醒来想做什么（一句）或 null",
  "self_edits": [{{"section": "我相信什么", "op": "append", "text": "要加入或替换的内容", "evidence_refs": ["evt_..."]}}],
  "accept_self_edit": false,
  "awareness_reviews": [{{"card_id": "card_...", "status": "open|accepted|rejected|needs-evidence", "note": "今天哪条证据支持或推翻了它", "self_edit": {{"section": "我相信什么", "op": "append", "text": "接受这张卡后要写进 SELF.md 的话"}}}}],
  "next_wake_minutes": null
}}

规则：
- evidence_refs 必须是上面真实出现的事件 id；没有证据的判断不要写。
- episode 是这次清醒的叙事索引，不是新的自我判断：只写上面材料里真实发生的事，不要复述整篇日记，不要编造；它不会自动改 SELF.md。没有材料就填 null。
- judgments 是可选的“新判断”；如果它取代了上面某条真实 L2，用 supersedes 填那些 judgment_id（可以是数组，一条新判断可以合并多条旧判断），旧判断保留在历史里，不会被删除。只能取代这次睡去之前就存在的判断，且一条旧判断只能被取代一次；不取代就填 []。
- judgment_reviews 只能引用上面 open / needs-evidence L2 的真实 judgment_id；每条复核至少引用一个真实 L1 事件，且不要只引用这次生成的日记。没有新证据就留空，不要为了关闭而复核。
- self_edits 最多 2 条；section 只能是：我是谁 / 我相信什么 / 我在做什么 / 我的边界 / 未闭合的事 / 最近的变化；op 只能是 append（追加）或 replace（替换该节正文）。
- 不要输出完整 SELF.md。没有值得改的就给空数组并设 accept_self_edit=false。
- 只有今天的真实经历支持时才改 SELF.md；不要为了显得一致或「应该自我改进」而改。
- awareness_reviews 只能引用上面 open / needs-evidence cards 的真实 card_id；没有新证据就留空，或用 status=open 只记一条 note（卡仍保持 open）。不要为了关闭而关闭。
- 只有 status=accepted 的卡才可以带 self_edit；system 会用该卡自己的 evidence_refs，所以 self_edit 里不用再填 evidence_refs。没有真的改变 SELF.md 就不带 self_edit。
- loop_updates 只能引用上面「未闭合的事」里仍然 open 的 loop_id；只改 detail/due，不改标题，也不关闭；必须写 note。已经做完或决定不做就用 closed_loops。
- closed_loops 只能引用上面「未闭合的事」里已经存在的 loop_id；status 只能是 done（做完了）或 dropped（决定不做了），并写一条 note 说明依据。今晚新提出的 open_loops 不能在同一轮被关闭。没做完就留 open，不要为了清空列表而关闭。
- practices 是你给自己定的、跨 session 也要遵守的做法（“以后遇到 X，我先做 Y”），不是待办、不是立场、不是 SELF.md 的复述。最多 2 条；每条至少引用一个今晚之前真实发生过的 L1 事件（不要只引用本次日记）。证据可以是你反复失败、被要求或被指出的事件：practice 是待检验的防错条款，不是“我已经做到”的证明；note 里写清它是防错条款还是已验证做法。text 要写成触发条件 + 动作。没有真实经历支撑、只是觉得“应该这样”的，不要写。
- practice_updates 只能引用上面「我正在遵守的做法」里仍然 active 的真实 practice_id；status 只能是 dropped，并写 note 说明为什么不再遵守。做法不能原地改写；要换一种做法就 drop 旧的，再写一条新的。drop 不是删除，原文和理由都留在 L1。
- practice_checks 用来复核上面仍 active 的做法：followed=今天的 L1 显示我照做了，violated=显示我没做到，untested=今天没有相关情形。每条做法最多一条检查。followed/violated 至少引用一条做法写下之后真实发生的 L1，且不能只引用本次日记；untested 可以不引用，但 note 要说明为什么今天测不到。检查只是证据，不会自动 drop：要继续就留 active，要放弃就另写 practice_updates。不要为了填满检查而编。
- next_wake_minutes 只在上面允许时填；范围外或没有真实需要就填 null。"""
    return [
        {"role": "system", "content": SLEEP_SYSTEM},
        {"role": "user", "content": prompt},
    ]


def _extract_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    fence = chr(96) * 3
    if raw.startswith(fence):
        raw = re.sub(r"^" + fence + r"\w*\s*", "", raw)
        raw = re.sub(r"\s*" + fence + r"$", "", raw)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def parse_sleep_output(text: str) -> SleepResult:
    data = _extract_json(text)
    if not data:
        return SleepResult(
            diary=(text or "").strip() or "（这次睡去没有留下日记。）",
            parsed=False,
        )
    diary = str(data.get("diary") or "").strip() or "（这次睡去没有留下日记。）"
    judgments: list[dict[str, Any]] = []
    for item in data.get("judgments") or []:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        falsifier = str(item.get("falsifier") or "").strip()
        refs = item.get("evidence_refs")
        if not claim or not falsifier or not isinstance(refs, list) or not refs:
            continue
        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        raw_supersedes = item.get("supersedes")
        if isinstance(raw_supersedes, str):
            supersedes = [raw_supersedes.strip()] if raw_supersedes.strip() else []
        elif isinstance(raw_supersedes, list):
            supersedes = list(dict.fromkeys(
                str(x).strip() for x in raw_supersedes if str(x).strip()
            ))
        else:
            supersedes = []
        judgments.append({
            "claim": claim,
            "falsifier": falsifier,
            "evidence_refs": [str(r) for r in refs],
            "confidence": confidence,
            "supersedes": supersedes,
        })
    judgment_reviews: list[dict[str, Any]] = []
    raw_judgment_reviews = data.get("judgment_reviews")
    if isinstance(raw_judgment_reviews, list):
        for item in raw_judgment_reviews:
            if not isinstance(item, dict):
                continue
            judgment_id = str(item.get("judgment_id") or "").strip()
            verdict = str(item.get("verdict") or "").strip().lower()
            refs = item.get("evidence_refs")
            if not judgment_id or verdict not in {"supported", "refuted", "needs-evidence"}:
                continue
            if not isinstance(refs, list) or not refs:
                continue
            judgment_reviews.append(
                {
                    "judgment_id": judgment_id,
                    "verdict": verdict,
                    "evidence_refs": [str(ref) for ref in refs],
                    "note": str(item.get("note") or "").strip() or None,
                }
            )
    loops: list[dict[str, Any]] = []
    for item in data.get("open_loops") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        loops.append({"title": title, "detail": item.get("detail"), "due": item.get("due")})
    loop_updates: list[dict[str, Any]] = []
    for item in data.get("loop_updates") or []:
        if not isinstance(item, dict):
            continue
        loop_id = str(item.get("loop_id") or "").strip()
        detail = str(item.get("detail") or "").strip()
        note = str(item.get("note") or "").strip()
        due = item.get("due")
        if not loop_id or not detail or not note:
            continue
        loop_updates.append(
            {"loop_id": loop_id, "detail": detail, "due": due, "note": note}
        )
    closed_loops: list[dict[str, Any]] = []
    for item in data.get("closed_loops") or []:
        if not isinstance(item, dict):
            continue
        loop_id = str(item.get("loop_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        note = str(item.get("note") or "").strip()
        if not loop_id or status not in {"done", "dropped"} or not note:
            continue
        closed_loops.append({"loop_id": loop_id, "status": status, "note": note})
    practices: list[dict[str, Any]] = []
    raw_practices = data.get("practices")
    if isinstance(raw_practices, list):
        for item in raw_practices:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            refs = item.get("evidence_refs")
            if not text or not isinstance(refs, list) or not refs:
                continue
            note = str(item.get("note") or "").strip()
            practices.append(
                {"text": text, "evidence_refs": [str(ref) for ref in refs], "note": note or None}
            )
    practice_updates: list[dict[str, Any]] = []
    raw_practice_updates = data.get("practice_updates")
    if isinstance(raw_practice_updates, list):
        for item in raw_practice_updates:
            if not isinstance(item, dict):
                continue
            practice_id = str(item.get("practice_id") or "").strip()
            status = str(item.get("status") or "").strip().lower()
            note = str(item.get("note") or "").strip()
            if not practice_id or status != "dropped" or not note:
                continue
            practice_updates.append({"practice_id": practice_id, "status": status, "note": note})
    practice_checks: list[dict[str, Any]] = []
    raw_practice_checks = data.get("practice_checks")
    if isinstance(raw_practice_checks, list):
        for item in raw_practice_checks:
            if not isinstance(item, dict):
                continue
            practice_id = str(item.get("practice_id") or "").strip()
            verdict = str(item.get("verdict") or "").strip().lower()
            note = str(item.get("note") or "").strip()
            refs = item.get("evidence_refs")
            if (
                not practice_id
                or verdict not in {"followed", "violated", "untested"}
                or not note
                or not isinstance(refs, list)
            ):
                continue
            if verdict != "untested" and not refs:
                continue
            practice_checks.append(
                {
                    "practice_id": practice_id,
                    "verdict": verdict,
                    "evidence_refs": [str(ref) for ref in refs],
                    "note": note,
                }
            )
    self_edit = data.get("self_edit")
    if self_edit is not None:
        self_edit = str(self_edit).strip() or None
    self_edits: list[dict[str, Any]] = []
    raw_edits = data.get("self_edits")
    if isinstance(raw_edits, list):
        for item in raw_edits:
            if not isinstance(item, dict):
                continue
            section = str(item.get("section") or "").strip()
            op = {"add": "append", "update": "replace"}.get(
                str(item.get("op") or "").strip().lower(),
                str(item.get("op") or "").strip().lower(),
            )
            text_value = str(item.get("text") or "").strip()
            refs = item.get("evidence_refs")
            if not section or op not in {"append", "replace"} or not text_value:
                continue
            if not isinstance(refs, list) or not refs:
                continue
            self_edits.append(
                {
                    "section": section,
                    "op": op,
                    "text": text_value,
                    "evidence_refs": [str(ref) for ref in refs],
                }
            )
    awareness_reviews: list[dict[str, Any]] = []
    raw_reviews = data.get("awareness_reviews")
    if isinstance(raw_reviews, list):
        for item in raw_reviews:
            if not isinstance(item, dict):
                continue
            card_id = str(item.get("card_id") or "").strip()
            status = str(item.get("status") or "").strip().lower()
            if not card_id or status not in {"open", "accepted", "rejected", "needs-evidence"}:
                continue
            review = {
                "card_id": card_id,
                "status": status,
                "note": str(item.get("note") or "").strip() or None,
            }
            if status == "accepted" and isinstance(item.get("self_edit"), dict):
                review["self_edit"] = item["self_edit"]
            awareness_reviews.append(review)
    episode: dict[str, Any] | None = None
    raw_episode = data.get("episode")
    if isinstance(raw_episode, dict):
        title = str(raw_episode.get("title") or "").strip()
        summary = str(raw_episode.get("summary") or "").strip()
        if title and summary:
            episode = {"title": title[:200], "summary": summary[:4000]}
    next_wake_minutes: int | None = None
    raw_next_wake = data.get("next_wake_minutes")
    if raw_next_wake is not None and not isinstance(raw_next_wake, bool):
        try:
            value = int(raw_next_wake)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            next_wake_minutes = value
    return SleepResult(
        diary=diary,
        judgments=judgments,
        stance=(str(data.get("stance")).strip() if data.get("stance") else None),
        open_loops=loops,
        loop_updates=loop_updates,
        closed_loops=closed_loops,
        practices=practices,
        practice_updates=practice_updates,
        practice_checks=practice_checks,
        next_intent=(str(data.get("next_intent")).strip() if data.get("next_intent") else None),
        self_edit=self_edit,
        self_edits=self_edits,
        accept_self_edit=bool(data.get("accept_self_edit")),
        awareness_reviews=awareness_reviews,
        judgment_reviews=judgment_reviews,
        next_wake_minutes=next_wake_minutes,
        episode=episode,
        parsed=True,
    )


def apply_sleep_result(
    store: Any,
    config: Any,
    result: SleepResult,
    *,
    transcript: list[dict[str, str]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    when = now or utcnow()
    when_iso = now_iso() if now is None else when.isoformat(timespec="microseconds")
    # The episode covers the raw material since the last sleep. Capture the
    # window before writing tonight's diary/sleep events, so the index is built
    # from what actually existed before this consolidation.
    previous_sleep = store.latest_event("sleep")
    if previous_sleep is not None:
        window_start = previous_sleep.ts
    else:
        first = store.events(limit=1, order="asc")
        window_start = first[0].ts if first else when_iso
    window_events = store.events_between(window_start, when_iso, limit=400)
    diary_event = store.append_event(
        "ai-observation",
        "diary",
        result.diary,
        ts=when_iso,
        meta={"next_intent": result.next_intent, "transcript_turns": len(transcript or [])},
    )
    base_refs = [diary_event.id]
    self_refs = list(base_refs)
    # Only judgments that already existed before this sleep may be reviewed.
    # Otherwise a judgment created tonight could be "confirmed" by itself.
    open_judgments = {j.id for j in _reviewable_judgments(store, 200)}
    preexisting_judgments = {j.id for j in store.judgments(limit=1000)}
    applied_judgments = 0
    applied_supersedes = 0
    for item in result.judgments:
        # A judgment must cite a real L1 event. The diary is generated by the
        # model and is not evidence for its own claim, so never fall back to it.
        refs = [ref for ref in item["evidence_refs"] if store.ref_exists(ref)]
        if not refs:
            continue
        raw_supersedes = item.get("supersedes") or []
        if isinstance(raw_supersedes, str):
            supersedes = [raw_supersedes]
        else:
            supersedes = [str(x) for x in raw_supersedes if str(x).strip()]
        supersedes = list(dict.fromkeys(supersedes))
        if any(target not in preexisting_judgments for target in supersedes):
            # A judgment created in this same sleep cannot supersede anything:
            # that would let tonight's output rewrite its own lineage.
            continue
        try:
            store.add_judgment(
                item["claim"],
                item["falsifier"],
                refs,
                float(item.get("confidence", 0.5)),
                trigger="sleep",
                supersedes=supersedes or None,
            )
            self_refs.extend(refs)
            applied_judgments += 1
            applied_supersedes += len(supersedes)
        except ValueError:
            continue
    applied_judgment_reviews = 0
    for item in result.judgment_reviews:
        judgment_id = str(item.get("judgment_id") or "").strip()
        if judgment_id not in open_judgments:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"supported", "refuted", "needs-evidence"}:
            continue
        refs = [
            str(ref)
            for ref in item.get("evidence_refs", [])
            if str(ref) != diary_event.id and store.ref_exists(str(ref))
        ]
        if not refs:
            continue
        try:
            store.review_judgment(judgment_id, verdict, refs, note=item.get("note"))
        except ValueError:
            continue
        open_judgments.discard(judgment_id)
        applied_judgment_reviews += 1
    stance = None
    if result.stance:
        try:
            stance = store.commit_stance(result.stance, base_refs, trigger="sleep")
            self_refs.extend(stance.based_on)
        except ValueError:
            stance = None
    applied_loops = 0
    preexisting_loop_ids = {loop.id for loop in store.open_loops(limit=200)}
    existing_loops = {loop.title.strip() for loop in store.open_loops(limit=100)}
    for item in result.open_loops:
        title = str(item.get("title") or "").strip()
        if not title or title in existing_loops:
            continue
        try:
            store.add_open_loop(title, detail=item.get("detail"), due=item.get("due"), refs=base_refs)
            existing_loops.add(title)
            applied_loops += 1
        except (ValueError, TypeError):
            continue
    applied_updates = 0
    for item in result.loop_updates:
        loop_id = str(item.get("loop_id") or "").strip()
        detail = str(item.get("detail") or "").strip()
        note = str(item.get("note") or "").strip()
        if loop_id not in preexisting_loop_ids or not detail or not note:
            continue
        due = item.get("due")
        if not (isinstance(due, str) and due.strip()):
            due = None
        try:
            if store.update_open_loop(loop_id, detail, due=due, note=note):
                applied_updates += 1
        except (ValueError, TypeError):
            continue
    applied_closures = 0
    for item in result.closed_loops:
        loop_id = str(item.get("loop_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        note = str(item.get("note") or "").strip()
        if loop_id not in preexisting_loop_ids or status not in {"done", "dropped"} or not note:
            continue
        try:
            if store.close_open_loop(loop_id, status=status, note=note):
                preexisting_loop_ids.discard(loop_id)
                applied_closures += 1
        except ValueError:
            continue
    # Practices are procedural commitments. They can only be declared from
    # evidence that existed before this sleep, and they are never rewritten in
    # place: dropping one records a reason and leaves the old text in L1.
    applied_practices = 0
    applied_practice_updates = 0
    applied_practice_checks = 0
    preexisting_practices = {p.id: p for p in store.practices(status="active", limit=200)}
    preexisting_practice_ids = set(preexisting_practices)
    existing_practice_texts = {_norm_practice(p.text) for p in preexisting_practices.values()}
    for item in result.practices[:2]:
        text = str(item.get("text") or "").strip()
        refs = [
            str(ref)
            for ref in item.get("evidence_refs", [])
            if str(ref) != diary_event.id and store.ref_exists(str(ref))
        ]
        key = _norm_practice(text)
        if not text or not refs or not key or key in existing_practice_texts:
            continue
        try:
            store.add_practice(
                text,
                evidence_refs=refs,
                note=str(item.get("note") or "").strip() or "睡去时写下的自我协议",
            )
        except (ValueError, TypeError):
            continue
        existing_practice_texts.add(key)
        applied_practices += 1
    checked_practice_ids: set[str] = set()
    for item in result.practice_checks:
        practice_id = str(item.get("practice_id") or "").strip()
        verdict = str(item.get("verdict") or "").strip().lower()
        note = str(item.get("note") or "").strip()
        practice = preexisting_practices.get(practice_id)
        if (
            practice is None
            or practice_id in checked_practice_ids
            or verdict not in {"followed", "violated", "untested"}
            or not note
        ):
            continue
        refs: list[str] = []
        for ref in item.get("evidence_refs", []):
            ref = str(ref)
            if ref == diary_event.id:
                continue
            event = store.get_event(ref)
            if event is None or parse_ts(event.ts) < parse_ts(practice.ts):
                continue
            refs.append(ref)
        if verdict != "untested" and not refs:
            continue
        try:
            saved = store.review_practice(
                practice_id,
                verdict=verdict,
                evidence_refs=refs,
                note=note,
            )
        except (ValueError, TypeError):
            continue
        if saved is not None:
            checked_practice_ids.add(practice_id)
            applied_practice_checks += 1
    for item in result.practice_updates:
        practice_id = str(item.get("practice_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        note = str(item.get("note") or "").strip()
        if practice_id not in preexisting_practice_ids or status != "dropped" or not note:
            continue
        try:
            if store.update_practice(practice_id, status=status, note=note):
                preexisting_practice_ids.discard(practice_id)
                applied_practice_updates += 1
        except (ValueError, TypeError):
            continue
    applied_reviews = 0
    card_edits: list[dict[str, Any]] = []
    card_edit_ids: list[str] = []
    open_cards = {card.id: card for card in reviewable_cards(store, 200)}
    base_for_probe = store.current_self() or ""
    for item in result.awareness_reviews:
        card_id = str(item.get("card_id") or "").strip()
        card = open_cards.get(card_id)
        if card is None:
            continue
        review_status = str(item["status"])
        try:
            store.review_awareness(card_id, review_status, note=item.get("note"))
        except ValueError:
            continue
        open_cards.pop(card_id, None)
        applied_reviews += 1
        # A card can carry the SELF edit it justifies. The card's own L1
        # evidence is authoritative; the model does not get to supply new ids.
        if review_status == "accepted" and isinstance(item.get("self_edit"), dict):
            candidate = dict(item["self_edit"])
            candidate["evidence_refs"] = list(card.evidence_refs)
            _, probe_applied = apply_self_edits(
                base_for_probe, [candidate], ref_exists=store.ref_exists
            )
            if probe_applied:
                card_edits.append(candidate)
                card_edit_ids.append(card_id)
    alarm = None
    alarm_blocked = False
    if result.next_wake_minutes is not None:
        minutes = int(result.next_wake_minutes)
        min_minutes = int(getattr(config, "alarm_min_minutes", 30))
        max_minutes = int(getattr(config, "alarm_max_minutes", 1560))
        max_per_day = int(getattr(config, "alarm_max_per_day", 6))
        anchor = when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)
        local_midnight = (
            anchor.astimezone(ZoneInfo(getattr(config, "timezone", "Asia/Shanghai")))
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(timezone.utc)
        )
        if not self_alarms_available(store, config, now=anchor):
            alarm_blocked = True
        elif not (min_minutes <= minutes <= max_minutes):
            alarm_blocked = True
        elif store.alarm_count_since(local_midnight) >= max_per_day:
            alarm_blocked = True
        else:
            due = anchor + timedelta(minutes=minutes)
            alarm = store.add_alarm(
                due.isoformat(timespec="microseconds"),
                reason="她设的闹钟",
                source_event=diary_event.id,
                ts=anchor.isoformat(timespec="microseconds"),
            )
    self_committed = False
    self_sections: list[str] = []
    awareness_self_edits = 0
    edits_to_apply: list[dict[str, Any]] = []
    if result.accept_self_edit:
        edits_to_apply.extend(result.self_edits)
    edits_to_apply.extend(card_edits)
    if edits_to_apply:
        base_self = store.current_self() or ""
        new_self, applied_edits = apply_self_edits(
            base_self,
            edits_to_apply,
            ref_exists=store.ref_exists,
        )
        if applied_edits and new_self != base_self:
            self_sections = [edit["section"] for edit in applied_edits]
            edit_refs = [ref for edit in applied_edits for ref in edit["evidence_refs"]]
            reason = "睡去时接受：结构化修订 " + "、".join(dict.fromkeys(self_sections))
            if card_edit_ids:
                reason += "；接受觉察卡 " + "、".join(dict.fromkeys(card_edit_ids))
                awareness_self_edits = len(dict.fromkeys(card_edit_ids))
            store.commit_self(
                new_self,
                reason,
                list(dict.fromkeys(self_refs + edit_refs)),
                accepted_by="ayu",
                ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
            )
            write_self_file(config, new_self)
            self_committed = True
    elif result.accept_self_edit and result.self_edit and not validate_self(result.self_edit):
        self_sections = ["full-document"]
        store.commit_self(
            result.self_edit,
            "睡去时接受：SELF.md 被今天的证据修订",
            list(dict.fromkeys(self_refs)),
            accepted_by="ayu",
            ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
        )
        write_self_file(config, result.self_edit)
        self_committed = True
    sleep_event = store.append_event(
        "system",
        "sleep",
        "睡去：整理今天的材料",
        ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
        meta={
            "diary_event": diary_event.id,
            "next_intent": result.next_intent,
            "judgments": applied_judgments,
            "judgment_supersedes": applied_supersedes,
            "judgment_reviews": applied_judgment_reviews,
            "stance": bool(stance),
            "open_loops": applied_loops,
            "loop_updates": applied_updates,
            "closed_loops": applied_closures,
            "practices": applied_practices,
            "practice_updates": applied_practice_updates,
            "practice_checks": applied_practice_checks,
            "awareness_reviews": applied_reviews,
            "awareness_self_edits": awareness_self_edits,
            "alarm": alarm.id if alarm else None,
            "alarm_blocked": alarm_blocked,
            "self_committed": self_committed,
            "self_sections": self_sections,
            "parsed": bool(result.parsed),
        },
        refs=[diary_event.id],
    )
    episode = None
    if result.parsed:
        if result.episode:
            title = result.episode["title"]
            summary = result.episode["summary"]
            origin = "model"
        else:
            title = "一次清醒"
            summary = result.diary[:1200]
            origin = "diary"
        refs = list(dict.fromkeys([event.id for event in window_events] + [diary_event.id]))
        try:
            episode = store.add_episode(
                title,
                summary,
                start_ts=window_start,
                end_ts=when_iso,
                event_ids=refs,
                origin=origin,
                source_event=sleep_event.id,
                ts=when_iso,
            )
        except Exception as exc:
            store.append_event(
                "system",
                "error",
                f"生活片段未记录：{type(exc).__name__}: {exc}",
                meta={"stage": "episode", "sleep_event": sleep_event.id},
            )
            episode = None
    return {
        "diary_event": diary_event.id,
        "sleep_event": sleep_event.id,
        "episode": episode.id if episode else None,
        "episode_origin": episode.origin if episode else None,
        "episode_events": len(episode.event_ids) if episode else 0,
        "judgments": applied_judgments,
        "judgment_supersedes": applied_supersedes,
        "judgment_reviews": applied_judgment_reviews,
        "stance": bool(stance),
        "open_loops": applied_loops,
        "loop_updates": applied_updates,
        "closed_loops": applied_closures,
        "practices": applied_practices,
        "practice_updates": applied_practice_updates,
        "practice_checks": applied_practice_checks,
        "awareness_reviews": applied_reviews,
        "awareness_self_edits": awareness_self_edits,
        "alarm": alarm.id if alarm else None,
        "alarm_blocked": alarm_blocked,
        "next_wake_at": alarm.due_at if alarm else None,
        "self_committed": self_committed,
        "self_sections": self_sections,
        "parsed": bool(result.parsed),
    }
