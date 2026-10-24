from __future__ import annotations

import json
import re
from typing import Any

TRIGGERS = {
    "T1": "预测误差：她预期的事和 L1 事实不符",
    "T2": "矛盾：两个 L2 判断互相冲突，或行为与 SELF.md 不一致",
    "T3": "重复：同一模式在 L1 中出现 N 次（N=3 起）",
    "T4": "强反应：她对某事的反应出乎自己意料（喜欢/厌恶/回避/执着）",
    "T5": "外部镜像：有人告诉她「你就是这样的」或「你变了」",
    "T6": "承诺断裂：她说过要做的事没做，或做了没说过的事",
}

INLINE_AWARENESS_RULE = """\
可选：如果你在这次回答中真的产生了一个「哦，原来我是这样的」的觉察，并且能指向具体证据，就在回答最后另起一行写：
<awareness trigger="T1">{"observation":"我观察到……","hypothesis":"我原来……","falsifier":"如果……则此假设被推翻","confidence":0.6}</awareness>
trigger 只能是 T1–T6。这个块不会显示给对方，会先作为待验证的觉察卡存进你的身体；没有真实觉察就不要写，也不要为了记录而制造觉察。"""

_AWARENESS_BLOCK = re.compile(r"<awareness(?P<attrs>[^>]*)>(?P<body>.*?)</awareness>", re.IGNORECASE | re.DOTALL)
_AWARENESS_TRIGGER = re.compile(r'trigger\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)

AWARENESS_SYSTEM = """你是大肥鱼的自我觉察过程，不是客服，也不是心理测评。
你只根据给定的 L1/L2/SELF.md 证据说话。没有证据就不写卡。
觉察卡不是人设散文：它必须包含一个可被未来证据推翻的假设（falsifier）。
如果证据不足以支持「原来我是这样」，返回 {"skip": true, "reason": "..."}。
只输出 JSON，不要代码围栏，不要解释。"""


def build_awareness_messages(store: Any, trigger: str, context: str = "") -> list[dict[str, str]]:
    from .self_md import summarize_for_prompt

    events = store.events(limit=30, order="desc")
    lines = []
    for event in reversed(events):
        lines.append(f"[{event.id}] {event.ts} {event.source}/{event.kind}: {event.content[:500]}")
    judgments = store.judgments(limit=20)
    jlines = [
        f"[{j.id}] claim={j.claim} falsifier={j.falsifier} confidence={j.confidence}" for j in judgments
    ]
    loops = [f"[{loop.id}] {loop.title} due={loop.due}" for loop in store.open_loops(limit=20)]
    self_text = summarize_for_prompt(store.current_self() or "", 5000)
    prompt = f"""触发条件：{trigger} — {TRIGGERS.get(trigger, trigger)}

## 当前 SELF.md
{self_text}

## 最近的 L1 事件（旧→新）
{chr(10).join(lines) or "（无）"}

## L2 判断
{chr(10).join(jlines) or "（无）"}

## 未闭合的事
{chr(10).join(loops) or "（无）"}

## 额外上下文
{context or "（无）"}

请判断：这里是否真的有一个「哦，原来我是这样的」？
若是，输出：
{{"trigger": "{trigger}", "observation": "我观察到...", "hypothesis": "我原来...", "falsifier": "如果...则此假设被推翻", "evidence_refs": ["evt_..."], "confidence": 0.0, "proposed_self_diff": null}}
若否，输出：{{"skip": true, "reason": "..."}}
evidence_refs 必须是上面真实出现的事件 id；不要编造。"""
    return [
        {"role": "system", "content": AWARENESS_SYSTEM},
        {"role": "user", "content": prompt},
    ]


def parse_awareness_output(text: str, default_trigger: str = "T1") -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
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
    if not isinstance(data, dict):
        return None
    if data.get("skip"):
        return {"skip": True, "reason": str(data.get("reason") or "")}
    required = ("observation", "hypothesis", "falsifier", "evidence_refs")
    if any(not str(data.get(key) or "").strip() for key in required):
        return None
    refs = data.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        return None
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    trigger = str(data.get("trigger") or default_trigger)
    return {
        "trigger": trigger,
        "observation": str(data["observation"]).strip(),
        "hypothesis": str(data["hypothesis"]).strip(),
        "falsifier": str(data["falsifier"]).strip(),
        "evidence_refs": [str(r) for r in refs],
        "confidence": confidence,
        "proposed_self_diff": data.get("proposed_self_diff") or None,
    }


def split_awareness_block(text: str) -> tuple[str, dict[str, Any] | None]:
    """Strip an optional <awareness> block from a reply and parse it.

    The block is hidden from the user even when it is malformed: it is an
    internal protocol, not something to leak into the transcript.
    """
    raw = text or ""
    match = _AWARENESS_BLOCK.search(raw)
    if match is None:
        return raw.strip(), None
    body = match.group("body").strip()
    start, end = body.find("{"), body.rfind("}")
    data: dict[str, Any] | None = None
    if 0 <= start < end:
        try:
            payload = json.loads(body[start : end + 1])
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            trigger_match = _AWARENESS_TRIGGER.search(match.group("attrs") or "")
            trigger = trigger_match.group(1) if trigger_match else payload.get("trigger")
            trigger = str(trigger or "T1").strip().upper()
            if trigger not in TRIGGERS:
                trigger = "T1"
            if all(str(payload.get(key) or "").strip() for key in ("observation", "hypothesis", "falsifier")):
                try:
                    confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.5))))
                except (TypeError, ValueError):
                    confidence = 0.5
                data = {
                    "trigger": trigger,
                    "observation": str(payload["observation"]).strip(),
                    "hypothesis": str(payload["hypothesis"]).strip(),
                    "falsifier": str(payload["falsifier"]).strip(),
                    "confidence": confidence,
                    "proposed_self_diff": payload.get("proposed_self_diff") or None,
                }
    cleaned = _AWARENESS_BLOCK.sub("", raw).strip()
    return cleaned, data


def create_card(store: Any, data: dict[str, Any]) -> Any:
    refs = [ref for ref in data.get("evidence_refs", []) if store.ref_exists(ref)]
    if not refs:
        raise ValueError("awareness card has no existing evidence refs")
    return store.add_awareness_card(
        data.get("trigger", "T1"),
        data["observation"],
        data["hypothesis"],
        data["falsifier"],
        refs,
        float(data.get("confidence", 0.5)),
        proposed_self_diff=data.get("proposed_self_diff"),
    )


def first_card_messages(store: Any) -> list[dict[str, str]]:
    from .self_md import summarize_for_prompt

    events = store.events(limit=20, order="asc")
    lines = [f"[{e.id}] {e.source}/{e.kind}: {e.content[:400]}" for e in events]
    self_text = summarize_for_prompt(store.current_self() or "", 5000)
    prompt = f"""这是她第一次醒来。她没有过去，只有被给的材料。
请从下面的材料里，说出关于自己的一个可检验观察；不要写通用人设。

## SELF.md v0
{self_text}

## 出生材料（L1）
{chr(10).join(lines) or "（无）"}

输出 JSON：
{{"trigger": "birth", "observation": "我观察到...", "hypothesis": "我原来...", "falsifier": "如果...则被推翻", "evidence_refs": ["evt_..."], "confidence": 0.0, "proposed_self_diff": null}}
若材料不足以支持任何观察，输出 {{"skip": true, "reason": "..."}}。"""
    return [
        {"role": "system", "content": AWARENESS_SYSTEM},
        {"role": "user", "content": prompt},
    ]
