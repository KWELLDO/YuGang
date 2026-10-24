from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class IncomingMessage:
    channel: str
    content: str
    sender: str | None = None
    external_id: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    wait_for_reply: bool = False
    reply_timeout: float = 120.0
    message_id: str | None = None
    event_id: str | None = None
    reply: str | None = None
    error: str | None = None
    _done: threading.Event = field(default_factory=threading.Event, repr=False)

    def finish(self, reply: str | None = None, error: str | None = None) -> None:
        self.reply = reply
        self.error = error
        self._done.set()

    def wait(self) -> bool:
        return self._done.wait(self.reply_timeout)


class Channel(Protocol):
    name: str

    def start(self, on_message: Callable[[IncomingMessage], None]) -> None:
        ...

    def stop(self) -> None:
        ...

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        ...


class NullChannel:
    name = "null"

    def __init__(self) -> None:
        self.sent: list[tuple[str | None, str, dict[str, Any]]] = []

    def start(self, on_message: Callable[[IncomingMessage], None]) -> None:
        self._on_message = on_message

    def stop(self) -> None:
        pass

    def send(self, recipient: str | None, content: str, *, meta: dict[str, Any] | None = None) -> bool:
        self.sent.append((recipient, content, meta or {}))
        return True
