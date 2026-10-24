from __future__ import annotations

from typing import Callable, Iterable

from .base import Completion, estimate_tokens


class MockRuntime:
    """Deterministic runtime for tests and offline demos."""

    name = "mock"
    model = "mock"

    def __init__(
        self,
        replies: Iterable[str] | None = None,
        *,
        responder: Callable[[list[dict[str, str]], str], str] | None = None,
        fail: Exception | None = None,
    ):
        self._replies = list(replies or [])
        self._responder = responder
        self._fail = fail
        self.calls: list[dict] = []

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1200,
        purpose: str = "chat",
    ) -> Completion:
        self.calls.append({"messages": messages, "purpose": purpose, "temperature": temperature})
        if self._fail is not None:
            raise self._fail
        if self._replies:
            text = self._replies.pop(0)
        elif self._responder is not None:
            text = self._responder(messages, purpose)
        else:
            last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
            text = f"（mock）我听到你了：{last[:80]}"
        prompt = "\n".join(m.get("content", "") for m in messages)
        return Completion(
            text=text,
            input_tokens=estimate_tokens(prompt),
            output_tokens=estimate_tokens(text),
            model=self.model,
            raw={"mock": True, "purpose": purpose},
        )


class ScriptedRuntime(MockRuntime):
    def __init__(self, replies: Iterable[str], **kwargs):
        super().__init__(replies, **kwargs)
