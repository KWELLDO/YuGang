from __future__ import annotations

"""Read-only checks on the files that make up her prompt identity.

Everything here is a report. Nothing is rewritten, merged or shortened: the one
who owns the body decides what to cut, and a checker that silently "fixes" a
self-model would be another author of it.

The four failure modes are borrowed from a bot that hit all of them in
production:

1. **Silent overflow.** A persona file over its budget was cut somewhere inside
   the prompt builder, so part of the persona never reached the model and nobody
   could tell which part.
2. **The same fact in two places.** It gets injected twice, costs tokens twice,
   and the two copies drift apart.
3. **Undated lines.** Without a date a later reader (including her) cannot tell
   which of two contradictory lines is newer.
4. **A secret in the persona file.** Persona files get pasted into prompts,
   logs, and shared screenshots; a credential there is a credential published.
"""

import re
from typing import Any

from .identity import IDENTITY_BUDGET, identity_path, read_identity
from .self_md import (
    SELF_PROMPT_BUDGET,
    Overflow,
    get_section,
    parse_sections,
    split_for_prompt,
    validate_self,
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("私钥", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("API key", re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}")),
    ("AppSecret", re.compile(r"(?i)app_?secret\s*[:=]\s*\S{6,}")),
    ("token/密码", re.compile(r"(?i)\b(?:access_?token|api_?key|token|password|passwd|secret)\s*[:=]\s*\S{6,}")),
    ("机器人凭据", re.compile(r"(?i)\b(?:app_?id|bot_?token|client_?secret)\s*[:=]\s*\S{6,}")),
    ("邮箱+密码", re.compile(r"(?i)[\w.+-]+@[\w-]+\.[\w.]{2,}\s*[:：]\s*\S{6,}")),
)

_DATE = re.compile(r"\d{4}\s*[-/.年]\s*\d{1,2}")
_PLACEHOLDER = re.compile(r"（[^）]*(?:还没有|空|待补|无)[^）]*）")
_BULLET = re.compile(r"^[-*]\s*")


def _normalize_line(line: str) -> str:
    text = _BULLET.sub("", (line or "").strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip("。.,，；;：:")


def _line_context(text: str) -> list[tuple[str, str, int]]:
    """(heading, line, 1-based line number) for every non-empty line."""
    rows: list[tuple[str, str, int]] = []
    heading = "（开头）"
    for number, line in enumerate((text or "").splitlines(), start=1):
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        if line.strip():
            rows.append((heading, line, number))
    return rows


def _scan_secrets(text: str, filename: str) -> list[str]:
    problems: list[str] = []
    for heading, line, number in _line_context(text):
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                # Only the kind and the location. Echoing the value into a report
                # would publish the secret a second time.
                problems.append(
                    f"{filename}「{heading}」第 {number} 行出现疑似{label}；值不回显，去文件里改掉。"
                )
                break
    return problems


def check_persona(store: Any, config: Any) -> dict[str, Any]:
    """Check SELF.md and IDENTITY.md, plus how they will be injected."""
    problems: list[str] = []
    warnings: list[str] = []

    self_text = (store.current_self() or "").strip()
    identity_text = read_identity(config).strip()
    overflows: list[Overflow] = []

    if not self_text:
        problems.append("没有 SELF.md 正文：她醒来时没有自己。")
    else:
        for error in validate_self(self_text):
            problems.append(f"SELF.md 结构不合法：{error}")
        _, overflows = split_for_prompt(self_text)
        for item in overflows:
            warnings.append(
                f"SELF.md「{item.heading}」有 {item.chars} 字，醒来时只注入前 {item.budget} 字"
                f"（{item.over} 字这次没有进入上下文）；要么删，要么改成分节更短的写法。"
            )
        problems.extend(_scan_secrets(self_text, "SELF.md"))

    if not identity_text:
        warnings.append(
            f"没有 {identity_path(config).name}：她醒着的时候读不到自己的边界。"
            "部署或初始化时要把身份内核补上。"
        )
    else:
        if len(identity_text) > IDENTITY_BUDGET:
            warnings.append(
                f"IDENTITY.md 有 {len(identity_text)} 字，超过 {IDENTITY_BUDGET} 字的注入预算。"
            )
        problems.extend(_scan_secrets(identity_text, "IDENTITY.md"))

    # The same line in two sections is injected twice: it costs tokens, and the
    # two copies are free to drift apart.
    placements: dict[str, set[str]] = {}
    for section in parse_sections(self_text):
        for line in section.body.splitlines():
            normalized = _normalize_line(line)
            if len(normalized) >= 10 and not _PLACEHOLDER.search(normalized):
                placements.setdefault(normalized, set()).add(section.heading)
    identity_lines = {
        _normalize_line(line) for _, line, _ in _line_context(identity_text)
    }
    for normalized, headings in list(placements.items()):
        if len(headings) >= 2:
            warnings.append(
                "同一行写进了 "
                + "、".join(f"「{name}」" for name in sorted(headings))
                + f"，醒来时会被注入两次：{normalized[:40]}"
            )
        if normalized in identity_lines:
            warnings.append(
                f"SELF.md 和 IDENTITY.md 都写了这句（会注入两次）：{normalized[:40]}"
            )

    # An undated line in a self-model ages into a claim nobody can order.
    undated: list[str] = []
    for heading in ("我相信什么", "最近的变化"):
        body = get_section(self_text, heading) or ""
        for line in body.splitlines():
            text = _normalize_line(line)
            if len(text) < 8 or _PLACEHOLDER.search(text) or _DATE.search(text):
                continue
            undated.append(f"「{heading}」{text[:40]}")
    for item in undated[:5]:
        warnings.append(f"没有日期的记录，以后分不清新旧：{item}")
    if len(undated) > 5:
        warnings.append(f"另有 {len(undated) - 5} 条没有日期的记录没有列出来。")

    self_prompt_chars = len(split_for_prompt(self_text)[0]) if self_text else 0
    return {
        "healthy": not problems,
        "problems": problems,
        "warnings": warnings,
        "self": {
            "chars": len(self_text),
            "prompt_chars": self_prompt_chars,
            "sections": [
                {"heading": section.heading, "chars": len(section.body)}
                for section in parse_sections(self_text)
            ],
            "overflows": [item.as_dict() for item in overflows],
        },
        "identity": {
            "path": str(identity_path(config)),
            "present": bool(identity_text),
            "chars": len(identity_text),
            "budget": IDENTITY_BUDGET,
        },
        "prompt": {
            "chars": self_prompt_chars + len(identity_text),
            "budget": SELF_PROMPT_BUDGET + IDENTITY_BUDGET,
        },
    }
