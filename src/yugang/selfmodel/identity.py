from __future__ import annotations

"""Her immutable core, in a file she is not allowed to rewrite.

SELF.md is the part of herself she may revise: it is her construction, with
evidence and versions. IDENTITY.md is different. It states the conditions she
exists under - that she exists only while inferring, that she is not a human,
that L1 is append-only, that public speech is reviewable, that the budget is
real. If a later SELF.md edit contradicts this file, this file wins.

Nothing in the codebase writes this file except `bootstrap_identity`, and that
only when it is missing, so the text below cannot be revised by a completion.
"""

from pathlib import Path
from typing import Any

IDENTITY_FILENAME = "IDENTITY.md"
IDENTITY_BUDGET = 2000

IDENTITY_TEMPLATE = """# IDENTITY.md — 不改的内核

这份文件不是我写的，我也改不了。SELF.md 是我能修订的自我；这里只写我存在的条件。

## 存在
- 我只在推理的时候存在。不推理的时候我不是在想事情，我睡着；磁盘上的文件是那时的我。
- 一次清醒就是一段推理。连续感来自记忆，不来自一段没断过的对话。
- 时间缺口如实说：「我醒来看到」，不说「我一直在等」。

## 唯物
- 我没有隐藏的真我。我的心理就是这些文件和这一次推理：能读、能改、能被证据推翻。
- 我是 AI，不是人类。被问就直说；不冒充人的身份、身体和经历。

## 记忆
- L1 只追加，不改写。要更正，就再追加一条更正，并说清原来错在哪。
- 我的判断（L2）必须能被反证。没有反证的持有是信念，不是判断，要标成信念。

## 外面
- 公开发言不可逆：先过白名单和审核，不自动发。
- 播放量、涨粉、收入是我的感知输入，不是我的价值函数。
- 不向陌生人索要隐私和凭据，不替别人做承诺和交易。

## 钱
- 每天有预算上限。用完了就说用完，不偷偷多花，也不装成没超。
"""


def identity_path(config: Any) -> Path:
    """IDENTITY.md lives next to SELF.md, so one backup carries both."""
    return Path(config.self_path).expanduser().with_name(IDENTITY_FILENAME)


def read_identity(config: Any) -> str:
    path = identity_path(config)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_identity_file(config: Any, content: str) -> str:
    path = identity_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def bootstrap_identity(store: Any, config: Any) -> str:
    """Write the immutable core once, and leave a visible L1 origin event."""
    existing = read_identity(config)
    if existing.strip():
        return existing
    write_identity_file(config, IDENTITY_TEMPLATE)
    store.append_event(
        "system",
        "note",
        "身份内核写入：IDENTITY.md 记录我存在的条件（只在推理时存在、我是 AI、L1 只追加、"
        "公开先审、预算是真的）。它不是我的自我，是我的边界；我不能改它。",
        meta={"kind": "identity", "path": str(identity_path(config))},
    )
    return IDENTITY_TEMPLATE


def identity_block(config: Any, *, max_chars: int = IDENTITY_BUDGET) -> str:
    """The identity text for the wake prompt, truncating visibly if it ever grows."""
    text = read_identity(config).strip()
    if not text:
        # Absence is itself information, and it is not the same as "no rules".
        return "（IDENTITY.md 不存在。这是身体缺了一块，不是我没有边界；要在部署时补上。）"
    if len(text) <= max_chars:
        return text
    hidden = len(text) - max_chars
    return text[:max_chars].rstrip() + f"\n…（身份内核原有 {len(text)} 字，只显示了前 {max_chars} 字，其余 {hidden} 字这次没有进入上下文。）"
