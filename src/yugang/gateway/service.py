from __future__ import annotations

import queue
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..ids import day_key, now_iso, parse_ts, utcnow
from ..protocol.sleep import SleepResult, apply_sleep_result, build_sleep_messages, parse_sleep_output
from ..protocol.wake import build_messages, build_wake_context, morning_check_messages, record_wake
from ..selfmodel.awareness import TRIGGERS, build_awareness_messages, create_card, parse_awareness_output
from ..selfmodel.self_md import bootstrap_self
from .budget import BudgetGuard
from .channels.base import IncomingMessage
from .window import WakeWindow


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


class GatewayService:
    """Always-on body. The model is only called when there is a reason to wake."""

    def __init__(
        self,
        config: Any,
        store: Any,
        runtime: Any = None,
        *,
        channels: list[Any] | None = None,
        now_fn=utcnow,
    ):
        self.config = config
        self.store = store
        self.runtime = runtime
        self.channels = list(channels or [])
        self.now_fn = now_fn
        self.window = WakeWindow(
            idle_seconds=int(getattr(config, "wake_idle_seconds", 900)),
            max_seconds=int(getattr(config, "max_wake_seconds", 5400)),
        )
        self.budget = BudgetGuard(store, config, now_fn=now_fn)
        self.queue: queue.Queue[IncomingMessage] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._started = False
        self._lock = threading.Lock()
        self._daily_attempt_day: str | None = None
        self._last_idle_check = 0.0
        self._current_wake_id: str | None = None
        self.last_reply: str | None = None
        self.started_at = now_iso()

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._started:
            return
        self.config.ensure_dirs()
        bootstrap_self(self.store, self.config)
        if self.runtime is None:
            self.runtime = self._default_runtime()
        for channel in self.channels:
            if hasattr(channel, "bind"):
                channel.bind(self)
            channel.start(self.handle_incoming)
        self._stop.clear()
        self._worker = threading.Thread(target=self._worker_loop, name="yugang-worker", daemon=True)
        self._worker.start()
        self._started = True

    def stop(self) -> None:
        self._stop.set()
        for channel in self.channels:
            try:
                channel.stop()
            except Exception:
                pass
        if self._worker is not None:
            self._worker.join(timeout=5)
            self._worker = None
        self._started = False

    def _default_runtime(self) -> Any:
        key = self.config.api_key()
        if not key:
            raise RuntimeError(f"missing API key in environment: {self.config.api_key_env}")
        from ..runtime.openai_compat import OpenAICompatRuntime

        return OpenAICompatRuntime(
            api_key=key,
            base_url=self.config.base_url,
            model=self.config.model,
            timeout=int(getattr(self.config, "request_timeout_seconds", 90)),
        )

    # -------------------------------------------------------------------- intake
    def handle_incoming(self, incoming: IncomingMessage) -> Any:
        message, event = self.store.receive_message(
            incoming.channel,
            incoming.content,
            sender=incoming.sender,
            external_id=incoming.external_id,
            meta=incoming.meta,
        )
        incoming.message_id = message.id
        if message.status == "processed" and isinstance(message.meta, dict) and message.meta.get("reply"):
            incoming.finish(str(message.meta["reply"]))
            return message
        if message.status == "processing":
            incoming.finish("（系统）这条消息正在处理，稍等。")
            return message
        self.queue.put(incoming)
        return message

    def chat(self, content: str, *, channel: str = "cli", sender: str = "kwelldo", timeout: float = 180.0) -> str | None:
        """Synchronous convenience path for CLI and tests."""
        incoming = IncomingMessage(channel=channel, content=content, sender=sender, wait_for_reply=True, reply_timeout=timeout)
        message = self.handle_incoming(incoming)
        if message.status == "processed" and isinstance(message.meta, dict) and message.meta.get("reply"):
            return str(message.meta["reply"])
        incoming.wait()
        return incoming.reply

    # -------------------------------------------------------------------- worker
    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                incoming = self.queue.get(timeout=1.0)
            except queue.Empty:
                self.idle_tick()
                continue
            try:
                self.process_message(incoming)
            except Exception as exc:  # fail loud, never swallow
                self._fail(incoming, f"{type(exc).__name__}: {exc}")
            finally:
                self.queue.task_done()

    def process_message(self, incoming: IncomingMessage) -> str | None:
        now = self.now_fn()
        message_id = incoming.message_id
        if not message_id:
            message = self.store.message_by_external(incoming.channel, incoming.external_id)
            message_id = message.id if message else None
        if message_id:
            self.store.mark_message(message_id, "processing")
        # A user message always wins over sleep and over the user reserve.
        if not self.window.is_awake():
            wake_event = self._record_wake(f"用户消息 via {incoming.channel}", now)
            context = build_wake_context(
                self.store, self.config, query=incoming.content, now=now, current_wake_id=wake_event.id, new_wake=True
            )
        else:
            context = build_wake_context(
                self.store, self.config, query=incoming.content, now=now, current_wake_id=self._current_wake_id, new_wake=False
            )
            self.window.touch(now)
        messages = build_messages(context, incoming.content)
        max_tokens = int(getattr(self.config, "reply_max_tokens", 900))
        estimated = self.budget.estimate(messages, max_tokens, self.config.model)
        decision = self.budget.check(estimated, kind="chat", user_facing=True, now=now)
        if not decision.allowed:
            reply = BudgetGuard.fail_loud(decision.reason)
            self._finalize(incoming, reply, error=decision.reason, now=now)
            return reply
        try:
            if self.runtime is None:
                raise RuntimeError("runtime is not configured")
            completion = self.runtime.complete(messages, max_tokens=max_tokens)
        except Exception as exc:
            self._fail(incoming, f"模型调用失败：{type(exc).__name__}: {exc}")
            return None
        self.budget.record_completion(completion, kind="chat", now=now)
        text = (completion.text or "").strip()
        if not text:
            self._fail(incoming, "模型返回了空回复")
            return None
        self.window.touch(now)
        self.window.transcript.append({"role": "user", "content": incoming.content})
        self.window.transcript.append({"role": "assistant", "content": text})
        self._finalize(incoming, text, now=now)
        return text

    def _finalize(self, incoming: IncomingMessage, text: str, *, error: str | None = None, now: datetime | None = None) -> None:
        now = now or self.now_fn()
        message_id = incoming.message_id
        if message_id:
            self.store.mark_message(message_id, "failed" if error else "processed", error=error)
        meta: dict[str, Any] = {"reply": text, "channel": incoming.channel}
        if error:
            meta["error"] = error
        self.store.record_message(
            incoming.channel,
            text,
            direction="out",
            sender=self.config.name,
            meta={"reply_to": message_id, **meta},
        )
        self.store.append_event(
            "system" if error else "ai-observation",
            "error" if error else "reply",
            text,
            actor=self.config.name,
            channel=incoming.channel,
            refs=[message_id] if message_id else [],
            meta=meta,
            ts=now.isoformat(timespec="microseconds"),
        )
        self.last_reply = text
        incoming.finish(text, error)
        channel = self._channel(incoming.channel)
        if channel is not None and incoming.channel != "http":
            try:
                channel.send(incoming.sender, text, meta={"subject": (incoming.meta or {}).get("subject")})
            except Exception:
                pass

    def _fail(self, incoming: IncomingMessage, reason: str) -> None:
        now = self.now_fn()
        reply = BudgetGuard.fail_loud(reason)
        self.store.append_event(
            "system",
            "error",
            f"消息处理失败：{reason}",
            channel=incoming.channel,
            meta={"message_id": incoming.message_id},
            ts=now.isoformat(timespec="microseconds"),
        )
        self._finalize(incoming, reply, error=reason, now=now)

    def _channel(self, name: str) -> Any:
        for channel in self.channels:
            if getattr(channel, "name", None) == name:
                return channel
        return None

    # --------------------------------------------------------------------- wake
    def _record_wake(self, reason: str, now: datetime) -> Any:
        last_sleep = self.store.latest_event("sleep")
        last_wake = self.store.latest_event("wake")
        interrupted = False
        if last_wake is not None:
            try:
                interrupted = last_sleep is None or parse_ts(last_wake.ts) > parse_ts(last_sleep.ts)
            except Exception:
                interrupted = last_sleep is None
        gap = None
        anchor = last_wake if interrupted else last_sleep
        if anchor is not None:
            try:
                gap = max(0.0, (now - parse_ts(anchor.ts)).total_seconds())
            except Exception:
                gap = None
        wake_event = record_wake(self.store, reason, now=now, gap_seconds=gap, interrupted=interrupted)
        self._current_wake_id = wake_event.id
        self.window.wake(now)
        return wake_event

    def _end_wake(self) -> None:
        self.window.sleep()
        self._current_wake_id = None

    # --------------------------------------------------------------------- idle
    def idle_tick(self) -> None:
        now = self.now_fn()
        monotonic = time.monotonic()
        if monotonic - self._last_idle_check < 2.0:
            return
        self._last_idle_check = monotonic
        if self.window.is_awake():
            if self.window.should_sleep(now):
                self.sleep_window(reason="清醒窗口结束")
            return
        self._maybe_daily_floor(now)

    def _maybe_daily_floor(self, now: datetime) -> None:
        today = day_key(now, getattr(self.config, "timezone", "Asia/Shanghai"))
        if self._daily_attempt_day == today:
            return
        hour = int(getattr(self.config, "daily_floor_hour", 9))
        local_now = now.astimezone(ZoneInfo(getattr(self.config, "timezone", "Asia/Shanghai")))
        if local_now.hour < hour:
            return
        latest_wake = self.store.latest_event("wake")
        if latest_wake:
            try:
                if day_key(parse_ts(latest_wake.ts), getattr(self.config, "timezone", "Asia/Shanghai")) == today:
                    self._daily_attempt_day = today
                    return
            except Exception:
                pass
        self._daily_attempt_day = today
        self.autonomous_wake("每日保底")

    def autonomous_wake(self, reason: str) -> str | None:
        now = self.now_fn()
        if self.window.is_awake():
            return None
        wake_event = self._record_wake(reason, now)
        context = build_wake_context(
            self.store, self.config, query="", now=now, current_wake_id=wake_event.id, new_wake=True
        )
        messages = morning_check_messages(context)
        estimated = self.budget.estimate(messages, int(getattr(self.config, "reply_max_tokens", 900)), self.config.model)
        decision = self.budget.check(estimated, kind="autonomous", user_facing=False, now=now)
        if not decision.allowed:
            self.store.append_event("system", "note", f"自主醒来被预算拦下：{decision.reason}", meta={"reason": reason})
            self._end_wake()
            return None
        try:
            if self.runtime is None:
                raise RuntimeError("runtime is not configured")
            completion = self.runtime.complete(messages, max_tokens=int(getattr(self.config, "reply_max_tokens", 900)))
        except Exception as exc:
            self.store.append_event("system", "error", f"自主醒来失败：{type(exc).__name__}: {exc}")
            self._end_wake()
            return None
        self.budget.record_completion(completion, kind="autonomous", now=now, user_facing=False)
        text = (completion.text or "").strip()
        self.store.append_event(
            "ai-observation",
            "observation",
            text or "（自主醒来没有留下文字。）",
            actor=self.config.name,
            meta={"reason": reason, "autonomous": True},
            ts=now.isoformat(timespec="microseconds"),
        )
        self.window.transcript.append({"role": "assistant", "content": text})
        self.last_reply = text
        self._broadcast(text, meta={"kind": "autonomous", "reason": reason})
        self.sleep_window(reason=f"{reason}结束")
        return text

    # --------------------------------------------------------------------- sleep
    def sleep_window(self, *, reason: str = "清醒窗口结束") -> dict[str, Any] | None:
        if not self.window.is_awake():
            return None
        now = self.now_fn()
        transcript = list(self.window.transcript)
        messages = build_sleep_messages(self.store, self.config, transcript)
        estimated = self.budget.estimate(messages, int(getattr(self.config, "sleep_max_tokens", 1200)), self.config.model)
        decision = self.budget.check(estimated, kind="sleep", user_facing=True, now=now)
        if not decision.allowed:
            result = SleepResult(
                diary=f"（这次睡去没有调用模型：{decision.reason}。清醒窗口结束，材料仍在 L1。）",
                next_intent=None,
            )
            applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
            self._end_wake()
            return applied
        try:
            if self.runtime is None:
                raise RuntimeError("runtime is not configured")
            completion = self.runtime.complete(messages, max_tokens=int(getattr(self.config, "sleep_max_tokens", 1200)))
        except Exception as exc:
            result = SleepResult(diary=f"（这次睡去没有完成模型整理：{type(exc).__name__}: {exc}。材料仍在 L1。）")
            applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
            self._end_wake()
            return applied
        self.budget.record_completion(completion, kind="sleep", now=now)
        result = parse_sleep_output(completion.text or "")
        applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
        self.store.append_event(
            "system",
            "note",
            f"清醒窗口结束：{reason}",
            meta={"applied": applied, "turns": len(transcript)},
            ts=now.isoformat(timespec="microseconds"),
        )
        self._end_wake()
        return applied

    # ------------------------------------------------------------------- status
    def status(self) -> dict[str, Any]:
        now = self.now_fn()
        summary = self.budget.summary(now)
        return {
            "name": self.config.name,
            "awake": self.window.is_awake(),
            "wake_remaining_seconds": round(self.window.remaining(now), 1),
            "wake_elapsed_seconds": round(self.window.elapsed(now), 1),
            "wake_count": self.window.wake_count,
            "queue": self.queue.qsize(),
            "event_count": self.store.event_count(),
            "last_event_ts": self.store.last_event_ts(),
            "budget": _jsonable(summary),
            "budget_stage": self.budget.stage(now),
            "channels": [getattr(c, "name", type(c).__name__) for c in self.channels],
            "started_at": self.started_at,
        }

    def tank(self) -> dict[str, Any]:
        self_text = self.store.current_self() or ""
        last_sleep = self.store.latest_event("sleep")
        return {
            "name": self.config.name,
            "status": self.status(),
            "self": self_text,
            "last_sleep": _jsonable(last_sleep) if last_sleep else None,
            "open_loops": _jsonable(self.store.open_loops(limit=20)),
            "awareness": _jsonable(self.store.awareness_cards(limit=10)),
            "recent_events": _jsonable(self.store.events(limit=30)),
            "judgments": _jsonable(self.store.judgments(limit=10)),
            "stats": self.store.stats(),
        }

    def recall(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        return [
            {"id": hit.event.id, "ts": hit.event.ts, "content": hit.event.content, "score": hit.score}
            for hit in self.store.recall(query, limit=limit)
        ]

    def awareness(self, trigger: str = "T1", context: str = "") -> Any | None:
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger: {trigger}")
        messages = build_awareness_messages(self.store, trigger, context=context)
        estimated = self.budget.estimate(messages, 800, self.config.model)
        decision = self.budget.check(estimated, kind="awareness", user_facing=False)
        if not decision.allowed:
            return None
        if self.runtime is None:
            return None
        completion = self.runtime.complete(messages, max_tokens=800)
        self.budget.record_completion(completion, kind="awareness", user_facing=False)
        data = parse_awareness_output(completion.text or "", trigger)
        if data is None:
            return None
        try:
            card = create_card(self.store, data)
        except ValueError:
            return None
        self._broadcast(f"（自我觉察卡）{card.observation}", meta={"kind": "awareness", "card_id": card.id})
        return card

    def _broadcast(self, text: str, *, meta: dict[str, Any] | None = None) -> None:
        for channel in self.channels:
            try:
                channel.send(None, text, meta=meta or {})
            except Exception:
                pass
