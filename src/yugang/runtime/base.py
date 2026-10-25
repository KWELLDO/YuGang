from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    finish_reason: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    # Provider-reported prefix-cache accounting. DeepSeek returns
    # prompt_cache_hit_tokens / prompt_cache_miss_tokens; a gateway that reports
    # neither leaves both at 0, and 0/0 means "unknown", never "the cache missed".
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def cache_read_ratio(self) -> float | None:
        """Share of the prompt the provider served from its prefix cache.

        None when the provider said nothing about caching, so a missing reading can
        never be reported as a real 0% hit rate.
        """
        reported = self.cache_hit_tokens + self.cache_miss_tokens
        if reported <= 0:
            return None
        return self.cache_hit_tokens / reported


class Runtime(Protocol):
    name: str
    model: str

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1200,
        purpose: str = "chat",
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
    ) -> Completion:
        ...


def estimate_tokens(text: str) -> int:
    """Conservative token estimate for mixed Chinese/English text.

    One token per character overestimates Latin text but keeps budget checks safe.
    """
    return max(1, len(text or ""))
