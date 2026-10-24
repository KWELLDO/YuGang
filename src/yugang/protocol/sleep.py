from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..ids import now_iso, utcnow
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
    next_intent: str | None = None
    self_edit: str | None = None
    self_edits: list[dict[str, Any]] = field(default_factory=list)
    accept_self_edit: bool = False
    parsed: bool = False


def build_sleep_messages(store: Any, config: Any, transcript: list[dict[str, str]]) -> list[dict[str, str]]:
    events = store.events(limit=30, order="desc")
    lines = [f"[{e.id}] {e.ts} {e.source}/{e.kind}: {e.content[:500]}" for e in reversed(events)]
    loops = [f"- [{loop.id}] {loop.title} due={loop.due}" for loop in store.open_loops(limit=30)]
    self_text = store.current_self() or ""
    transcript_text = "\n".join(f"{m.get('role')}: {m.get('content', '')[:1000]}" for m in transcript[-30:])
    prompt = f"""## 当前 SELF.md
{self_text}

## 最近 L1（旧→新）
{chr(10).join(lines) or "（无）"}

## 未闭合的事
{chr(10).join(loops) or "（无）"}

## 这次清醒的对话
{transcript_text or "（没有对话；这是一次自主醒来。）"}

请输出：
{{
  "diary": "第一人称日记，包含时间缺口与你今天注意到的事；不要写「我梦到了」",
  "judgments": [{{"claim": "...", "falsifier": "如果...则被推翻", "evidence_refs": ["evt_..."], "confidence": 0.0}}],
  "stance": "一句当前立场（薄，不写人设）或 null",
  "open_loops": [{{"title": "...", "detail": "...", "due": null}}],
  "next_intent": "下次醒来想做什么（一句）或 null",
  "self_edits": [{{"section": "我相信什么", "op": "append", "text": "要加入或替换的内容", "evidence_refs": ["evt_..."]}}],
  "accept_self_edit": false
}}

规则：
- evidence_refs 必须是上面真实出现的事件 id；没有证据的判断不要写。
- self_edits 最多 2 条；section 只能是：我是谁 / 我相信什么 / 我在做什么 / 我的边界 / 未闭合的事 / 最近的变化；op 只能是 append（追加）或 replace（替换该节正文）。
- 不要输出完整 SELF.md。没有值得改的就给空数组并设 accept_self_edit=false。
- 只有今天的真实经历支持时才改 SELF.md；不要为了显得一致或「应该自我改进」而改。"""
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
        judgments.append({"claim": claim, "falsifier": falsifier, "evidence_refs": [str(r) for r in refs], "confidence": confidence})
    loops: list[dict[str, Any]] = []
    for item in data.get("open_loops") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        loops.append({"title": title, "detail": item.get("detail"), "due": item.get("due")})
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
            op = str(item.get("op") or "").strip().lower()
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
    return SleepResult(
        diary=diary,
        judgments=judgments,
        stance=(str(data.get("stance")).strip() if data.get("stance") else None),
        open_loops=loops,
        next_intent=(str(data.get("next_intent")).strip() if data.get("next_intent") else None),
        self_edit=self_edit,
        self_edits=self_edits,
        accept_self_edit=bool(data.get("accept_self_edit")),
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
    diary_event = store.append_event(
        "ai-observation",
        "diary",
        result.diary,
        ts=now_iso() if now is None else when.isoformat(timespec="microseconds"),
        meta={"next_intent": result.next_intent, "transcript_turns": len(transcript or [])},
    )
    base_refs = [diary_event.id]
    self_refs = list(base_refs)
    applied_judgments = 0
    for item in result.judgments:
        # A judgment must cite a real L1 event. The diary is generated by the
        # model and is not evidence for its own claim, so never fall back to it.
        refs = [ref for ref in item["evidence_refs"] if store.ref_exists(ref)]
        if not refs:
            continue
        try:
            store.add_judgment(
                item["claim"],
                item["falsifier"],
                refs,
                float(item.get("confidence", 0.5)),
                trigger="sleep",
            )
            self_refs.extend(refs)
            applied_judgments += 1
        except ValueError:
            continue
    stance = None
    if result.stance:
        try:
            stance = store.commit_stance(result.stance, base_refs, trigger="sleep")
            self_refs.extend(stance.based_on)
        except ValueError:
            stance = None
    applied_loops = 0
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
    self_committed = False
    self_sections: list[str] = []
    if result.accept_self_edit and result.self_edits:
        base_self = store.current_self() or ""
        new_self, applied_edits = apply_self_edits(
            base_self,
            result.self_edits,
            ref_exists=store.ref_exists,
        )
        if applied_edits and new_self != base_self:
            self_sections = [edit["section"] for edit in applied_edits]
            edit_refs = [ref for edit in applied_edits for ref in edit["evidence_refs"]]
            store.commit_self(
                new_self,
                "睡去时接受：结构化修订 " + "、".join(self_sections),
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
            "stance": bool(stance),
            "open_loops": applied_loops,
            "self_committed": self_committed,
            "self_sections": self_sections,
            "parsed": bool(result.parsed),
        },
        refs=[diary_event.id],
    )
    return {
        "diary_event": diary_event.id,
        "sleep_event": sleep_event.id,
        "judgments": applied_judgments,
        "stance": bool(stance),
        "open_loops": applied_loops,
        "self_committed": self_committed,
        "self_sections": self_sections,
        "parsed": bool(result.parsed),
    }
