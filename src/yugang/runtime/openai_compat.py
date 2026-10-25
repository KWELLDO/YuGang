from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from .base import Completion, estimate_tokens


def parse_usage(
    usage: dict[str, Any], *, prompt_text: str, output_text: str
) -> tuple[int, int, int, int]:
    """Split one provider usage block into (input, output, cache_hit, cache_miss).

    Both cache counts stay 0 when the provider reports no cache fields. That is a
    deliberate "unknown" reading: turning it into a 0% hit rate would look exactly
    like a cache that never works.
    """
    input_tokens = int(usage.get("prompt_tokens") or estimate_tokens(prompt_text))
    output_tokens = int(usage.get("completion_tokens") or estimate_tokens(output_text))
    detail = usage.get("prompt_tokens_details") or {}
    hit = usage.get("prompt_cache_hit_tokens")
    if hit is None:
        hit = detail.get("cached_tokens")
    hit_tokens = max(0, int(hit or 0))
    miss = usage.get("prompt_cache_miss_tokens")
    if miss is None:
        miss_tokens = max(0, input_tokens - hit_tokens) if hit is not None else 0
    else:
        miss_tokens = max(0, int(miss))
    return input_tokens, output_tokens, hit_tokens, miss_tokens


class OpenAICompatRuntime:
    """Minimal OpenAI-compatible chat-completions client (stdlib only)."""

    name = "openai-compat"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com",
        model: str = "deepseek-chat",
        timeout: float = 90.0,
    ):
        if not api_key:
            raise ValueError("api_key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/v1"):
            return self.base_url + "/chat/completions"
        return self.base_url + "/chat/completions"

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
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
            raise RuntimeError(f"model HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            raise RuntimeError(f"model transport error: {exc}") from exc
        try:
            parsed: dict[str, Any] = json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"model returned non-JSON: {body[:400]}") from exc
        choices = parsed.get("choices") or []
        if not choices:
            raise RuntimeError(f"model returned no choices: {body[:400]}")
        message = choices[0].get("message") or {}
        finish_reason = str(choices[0].get("finish_reason") or "")
        text = message.get("content") or ""
        if not isinstance(text, str):
            text = str(text)
        raw_calls = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = [call for call in raw_calls if isinstance(call, dict)]
        if not text.strip() and not tool_calls:
            raise RuntimeError(f"model returned empty content: {body[:400]}")
        usage = parsed.get("usage") or {}
        prompt_text = "\n".join(str(m.get("content") or "") for m in messages)
        input_tokens, output_tokens, cache_hit, cache_miss = parse_usage(
            usage, prompt_text=prompt_text, output_text=text
        )
        return Completion(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=str(parsed.get("model") or self.model),
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            raw=parsed,
            cache_hit_tokens=cache_hit,
            cache_miss_tokens=cache_miss,
        )
