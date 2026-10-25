from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CANONICAL_SECTIONS = (
    "我是谁",
    "我相信什么",
    "我在做什么",
    "我的边界",
    "未闭合的事",
    "最近的变化",
)
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    heading: str
    body: str


def parse_sections(text: str) -> list[Section]:
    matches = list(_HEADING.finditer(text or ""))
    sections: list[Section] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append(Section(match.group(1).strip(), text[start:end].strip()))
    return sections


def section_map(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for section in parse_sections(text):
        result[section.heading] = section.body
    return result


def find_section(text: str, name: str) -> Section | None:
    for section in parse_sections(text):
        if section.heading == name or section.heading.startswith(name):
            return section
    return None


def get_section(text: str, name: str) -> str | None:
    section = find_section(text, name)
    return section.body if section else None


def validate_self(text: str) -> list[str]:
    if not isinstance(text, str) or not text.strip():
        return ["SELF.md must be non-empty"]
    errors: list[str] = []
    if len(text) > 40_000:
        errors.append("SELF.md is longer than 40000 characters")
    sections = parse_sections(text)
    if not sections:
        errors.append("SELF.md must contain at least one '## ' section")
    headings = [s.heading for s in sections]
    if len(headings) != len(set(headings)):
        errors.append("SELF.md has duplicate section headings")
    if not any(h == "我是谁" or h.startswith("我是谁") for h in headings):
        errors.append("SELF.md must contain a 我是谁 section")
    return errors


def replace_section(text: str, name: str, body: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(name)}(?:\s+.*|（[^）]*）)?$", re.MULTILINE)
    match = pattern.search(text or "")
    if not match:
        return (text.rstrip() + "\n\n## " + name + "\n\n" + body.strip() + "\n").lstrip()
    end = _HEADING.search(text, match.end())
    stop = end.start() if end else len(text)
    prefix = text[: match.end()]
    suffix = text[stop:]
    return prefix + "\n\n" + body.strip() + "\n\n" + suffix.lstrip("\n")


def append_change(text: str, change: str, limit: int = 10) -> str:
    change = change.strip()
    if not change:
        return text
    current = get_section(text, "最近的变化") or ""
    bullets = [line for line in current.splitlines() if line.strip().startswith("-")]
    bullets = [f"- {change}"] + [b for b in bullets if b.strip() not in {"- （还没有。）", "- (空。)"}]
    body = "\n".join(bullets[:limit]) or "- （还没有。）"
    return replace_section(text, "最近的变化", body)


_EVIDENCE_COMMENT = re.compile(r"^<!--\s*evidence:\s*(.*?)\s*-->\s*$", re.MULTILINE)
_PLACEHOLDER_LINE = re.compile(r"^-\s*（[^）]*(?:还没有|空)[^）]*）\s*$")


def _canonical_section(name: Any) -> str | None:
    value = str(name or "").strip()
    for canonical in CANONICAL_SECTIONS:
        if value == canonical or value.startswith(canonical):
            return canonical
    return None


def _evidence_refs(body: str) -> list[str]:
    refs: list[str] = []
    for match in _EVIDENCE_COMMENT.finditer(body or ""):
        refs.extend(part for part in re.split(r"[,，\s]+", match.group(1)) if part)
    return list(dict.fromkeys(refs))


def _strip_evidence(body: str) -> str:
    return _EVIDENCE_COMMENT.sub("", body or "").strip()


def section_evidence(text: str, name: str) -> list[str]:
    """Evidence ids attached to one section, in document order."""
    return _evidence_refs(get_section(text, name) or "")


def apply_self_edits(
    text: str,
    edits: list[dict[str, Any]],
    *,
    ref_exists: Any | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply small, auditable SELF.md edits instead of accepting a whole document.

    Each edit targets one canonical section and must cite at least one real L1
    event. The evidence ids are kept in an HTML comment, so the edit remains
    traceable after later rewrites. Invalid edits are skipped rather than
    producing a partial or fabricated self-model.
    """
    current = text
    applied: list[dict[str, Any]] = []
    for edit in edits or []:
        if not isinstance(edit, dict):
            continue
        section = _canonical_section(edit.get("section"))
        op = {"add": "append", "update": "replace"}.get(
            str(edit.get("op") or "").strip().lower(),
            str(edit.get("op") or "").strip().lower(),
        )
        content = str(edit.get("text") or "").strip()
        if not section or op not in {"append", "replace"} or not content:
            continue
        refs = [str(ref).strip() for ref in (edit.get("evidence_refs") or []) if str(ref).strip()]
        if ref_exists is not None:
            refs = [ref for ref in refs if ref_exists(ref)]
        refs = list(dict.fromkeys(refs))
        if not refs:
            continue
        body = get_section(current, section) or ""
        previous_refs = _evidence_refs(body)
        body = _strip_evidence(body)
        if op == "replace":
            new_body = content
            merged_refs = refs
        else:
            body = "\n".join(
                line for line in body.splitlines() if not _PLACEHOLDER_LINE.match(line.strip())
            ).strip()
            bullet = content if content.lstrip().startswith("-") else "- " + content
            new_body = (body + "\n" + bullet).strip() if body else bullet
            merged_refs = list(dict.fromkeys(previous_refs + refs))
        new_body = new_body + "\n\n<!-- evidence: " + ", ".join(merged_refs) + " -->"
        candidate = replace_section(current, section, new_body)
        if validate_self(candidate):
            continue
        current = candidate
        applied.append(
            {
                "section": section,
                "op": op,
                "text": content,
                "evidence_refs": merged_refs,
            }
        )
    return current, applied


def render_self(name: str, sections: dict[str, str], *, preamble: str = "") -> str:
    lines = [f"# SELF.md — {name}", ""]
    if preamble.strip():
        lines.extend([preamble.strip(), ""])
    for heading in CANONICAL_SECTIONS:
        body = sections.get(heading, "（空。）").strip() or "（空。）"
        lines.extend([f"## {heading}", "", body, ""])
    return "\n".join(lines).rstrip() + "\n"


def diff_self(old: str, new: str) -> str:
    return "".join(
        difflib.unified_diff(
            (old or "").splitlines(keepends=True),
            (new or "").splitlines(keepends=True),
            fromfile="SELF.md.previous",
            tofile="SELF.md.new",
        )
    )


def summarize_for_prompt(text: str, max_chars: int = 6000) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n…（SELF.md 已截断）"


def write_self_file(config: Any, content: str) -> str:
    path = Path(config.self_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def bootstrap_self(store: Any, config: Any) -> str:
    """Create SELF.md v0 exactly once, with a visible L1 origin event."""
    current = store.current_self()
    if current:
        write_self_file(config, current)
        return current
    from ..config import DEFAULT_SELF_TEMPLATE

    origin = store.append_event(
        "system",
        "note",
        "出生材料：SELF.md v0 由被给的材料构成；每条 given 主张等待经历检验。",
        meta={"kind": "birth", "template": "default"},
    )
    version = store.commit_self(
        DEFAULT_SELF_TEMPLATE,
        "出生：SELF.md v0，逐条标注 given，等待经历检验",
        [origin.id],
        accepted_by="kwelldo",
    )
    write_self_file(config, version.content)
    return version.content
