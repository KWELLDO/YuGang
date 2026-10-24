from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Runtime(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1200,
        purpose: str = "chat",
    ) -> Completion:
        ...


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed Chinese/English text.

    One token per character overestimates Latin text but keeps budget checks safe.
    """
    return max(1, len(text or ""))
