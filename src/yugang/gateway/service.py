from __future__ import annotations

import json
import queue
import threading
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ..ids import day_key, now_iso, parse_ts, utcnow
from ..protocol.sleep import SleepResult, apply_sleep_result, build_sleep_messages, parse_sleep_output
from ..protocol.wake import build_messages, build_wake_context, morning_check_messages, record_wake
from ..selfmodel.awareness import (
    TRIGGERS,
    build_awareness_messages,
    create_card,
    parse_awareness_output,
    split_awareness_block,
)
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
        self._channel_health_cache: dict[str, bool | None] = {}
        self.last_reply: str | None = None
        self.started_at = now_iso()
        self._last_tick_monotonic = time.monotonic()

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
        self._recover_pending()

    def _recover_pending(self) -> None:
        """Requeue messages that were stored but never answered before a restart.

        A message is written to L1 before it is queued, so a crash can leave it
        'received' (never picked up) or 'processing' (the worker died mid-turn).
        Recovery reuses the original message/event ids instead of calling
        receive_message again, so no duplicate L1 event is created.
        """
        if self.is_paused():
            # Leave the backlog in L1 while paused. Resume requeues it; do not
            # send a second paused notice on every process restart.
            return
        try:
            messages = self.store.recoverable_messages()
        except Exception as exc:
            self.store.append_event("system", "error", f"未处理消息恢复失败：{type(exc).__name__}: {exc}")
            return
        if not messages:
            return
        for message in messages:
            meta = message.meta if isinstance(message.meta, dict) else {}
            incoming = IncomingMessage(
                channel=message.channel,
                content=message.content,
                sender=message.sender,
                external_id=message.external_id,
                meta=meta,
                wait_for_reply=False,
            )
            incoming.message_id = message.id
            incoming.event_id = meta.get("event_id")
            self.queue.put(incoming)
        self.store.append_event(
            "system",
            "note",
            f"恢复了 {len(messages)} 条上次没有处理完的消息。",
            meta={"count": len(messages)},
        )

    # -------------------------------------------------------------------- pause
    def is_paused(self) -> bool:
        return Path(self.config.pause_file).exists()

    def paused_info(self) -> dict[str, Any] | None:
        path = Path(self.config.pause_file)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            raw = {}
        return {
            "reason": str(raw.get("reason") or "operator pause"),
            "at": str(raw.get("at") or ""),
        }

    def pause(self, *, reason: str = "operator") -> dict[str, Any]:
        """Stop all model calls without pretending to be asleep.

        A paused gateway still records incoming messages in L1, tells the sender
        that she is paused, and leaves the message pending. Resume requeues it,
        so a pause cannot silently swallow a message.
        """
        self.config.ensure_dirs()
        reason = str(reason or "operator").strip()[:500] or "operator"
        path = Path(self.config.pause_file)
        path.write_text(
            json.dumps({"reason": reason, "at": now_iso(), "by": "operator"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        self.store.append_event(
            "system",
            "note",
            f"操作员暂停网关：{reason}",
            meta={"paused": True, "reason": reason},
        )
        if self.window.is_awake():
            self._end_wake()
            self.store.append_event(
                "system",
                "note",
                "暂停结束了当前清醒窗口；这不是睡眠，没有睡去日记。",
                meta={"paused": True},
            )
        return {"paused": True, **self.paused_info()}

    def resume(self) -> dict[str, Any]:
        path = Path(self.config.pause_file)
        path.unlink(missing_ok=True)
        self.store.append_event("system", "note", "操作员恢复网关：模型调用重新开放。", meta={"paused": False})
        self._recover_pending()
        return {"paused": False}

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
        incoming.event_id = event.id
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
            # The health endpoint uses this heartbeat to tell a live worker
            # from a process whose HTTP thread is up but whose worker died.
            self._last_tick_monotonic = time.monotonic()
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

    def _channel_health(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for channel in self.channels:
            name = str(getattr(channel, "name", type(channel).__name__))
            probe = getattr(channel, "health", None)
            if callable(probe):
                try:
                    value = probe()
                    result[name] = value if isinstance(value, dict) else {"healthy": value}
                except Exception as exc:
                    result[name] = {"healthy": False, "last_error": f"{type(exc).__name__}: {exc}"}
            else:
                result[name] = {"healthy": True}
            result[name].setdefault("name", name)
        return result

    def _watch_channels(self) -> None:
        """Write one L1 event when a channel changes health, not every second."""
        current = self._channel_health()
        for name, info in current.items():
            healthy = info.get("healthy")
            if healthy is None:
                continue
            previous = self._channel_health_cache.get(name)
            if previous is healthy:
                continue
            self._channel_health_cache[name] = healthy
            if previous is None and healthy:
                continue
            if healthy:
                self.store.append_event(
                    "system",
                    "note",
                    f"通道恢复：{name}",
                    meta={"channel": name},
                )
            else:
                detail = str(info.get("last_error") or "unknown")
                self.store.append_event(
                    "system",
                    "error",
                    f"通道不健康：{name}（{detail}）",
                    meta={"channel": name, "failures": info.get("consecutive_failures")},
                )

    def health(self) -> dict[str, Any]:
        worker_alive = bool(self._worker is not None and self._worker.is_alive())
        age = max(0.0, round(time.monotonic() - self._last_tick_monotonic, 3))
        max_age = float(getattr(self.config, "health_max_tick_age_seconds", 600))
        channels = self._channel_health()
        degraded = any(info.get("healthy") is False for info in channels.values())
        return {
            "ok": bool(self._started and worker_alive and age <= max_age),
            "degraded": degraded,
            "awake": self.window.is_awake(),
            "paused": self.is_paused(),
            "worker_alive": worker_alive,
            "last_tick_age_seconds": age,
            "channels": channels,
        }

    def process_message(self, incoming: IncomingMessage) -> str | None:
        now = self.now_fn()
        if self.is_paused():
            self._buffer_while_paused(incoming, now)
            return None
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
                self.store,
                self.config,
                query=incoming.content,
                now=now,
                current_wake_id=wake_event.id,
                new_wake=True,
                body=self._body_state(now),
            )
        else:
            context = build_wake_context(
                self.store,
                self.config,
                query=incoming.content,
                now=now,
                current_wake_id=self._current_wake_id,
                new_wake=False,
                body=self._body_state(now),
            )
            self.window.touch(now)
        messages = build_messages(context, incoming.content)
        # A second message in the same wake is a continuation of this
        # conversation, not a fresh retrieval. Keep the actual prior turns
        # (bounded) between the system context and the new user message.
        history = self.window.history()
        if history:
            messages[1:1] = history
        max_tokens = int(getattr(self.config, "reply_max_tokens", 2000))
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
        text, awareness = split_awareness_block((completion.text or "").strip())
        if not text:
            self._fail(incoming, "模型返回了空回复")
            return None
        self.window.touch(now)
        self.window.transcript.append({"role": "user", "content": incoming.content})
        self.window.transcript.append({"role": "assistant", "content": text})
        self._finalize(incoming, text, now=now, awareness=awareness)
        return text

    def _buffer_while_paused(self, incoming: IncomingMessage, now: datetime) -> None:
        """Tell the sender the truth, keep the message pending for resume."""
        notice = (
            "（系统）她暂时被操作员暂停，模型不会醒来。你的消息已写入 L1，"
            "恢复后会看到；这不是她的回答。"
        )
        refs = [ref for ref in (incoming.message_id, incoming.event_id) if ref]
        self.store.append_event(
            "system",
            "note",
            f"暂停期间收到消息，已缓冲：{incoming.content[:120]}",
            channel=incoming.channel,
            refs=refs,
            meta={"paused": True, "message_id": incoming.message_id},
            ts=now.isoformat(timespec="microseconds"),
        )
        self.store.record_message(
            incoming.channel,
            notice,
            direction="out",
            sender="system",
            meta={"paused": True, "reply_to": incoming.message_id},
        )
        incoming.finish(notice)
        channel = self._channel(incoming.channel)
        if channel is not None and incoming.channel != "http":
            try:
                delivered = channel.send(
                    incoming.sender, notice, meta={"subject": (incoming.meta or {}).get("subject")}
                )
                if delivered is False:
                    raise RuntimeError("channel.send returned False")
            except Exception as exc:
                self.store.append_event(
                    "system",
                    "error",
                    f"暂停通知投递失败 via {incoming.channel}：{type(exc).__name__}: {exc}",
                    channel=incoming.channel,
                    refs=refs,
                )

    def _finalize(
        self,
        incoming: IncomingMessage,
        text: str,
        *,
        error: str | None = None,
        now: datetime | None = None,
        awareness: dict[str, Any] | None = None,
    ) -> Any:
        now = now or self.now_fn()
        message_id = incoming.message_id
        if message_id:
            self.store.mark_message(message_id, "failed" if error else "processed", error=error)
            self.store.update_message_meta(message_id, {"reply": text, "channel": incoming.channel})
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
        event = self.store.append_event(
            "system" if error else "ai-observation",
            "error" if error else "reply",
            text,
            actor=self.config.name,
            channel=incoming.channel,
            refs=[message_id] if message_id else [],
            meta=meta,
            ts=now.isoformat(timespec="microseconds"),
        )
        if awareness and not error:
            self._record_inline_awareness(incoming, awareness, event)
        self.last_reply = text
        incoming.finish(text, error)
        channel = self._channel(incoming.channel)
        if channel is not None and incoming.channel != "http":
            try:
                delivered = channel.send(
                    incoming.sender, text, meta={"subject": (incoming.meta or {}).get("subject")}
                )
                if delivered is False:
                    raise RuntimeError("channel.send returned False")
            except Exception as exc:
                # Her reply is in L1, but the human never received it. That is
                # a delivery failure and must be visible, not swallowed.
                self.store.append_event(
                    "system",
                    "error",
                    f"回复投递失败 via {incoming.channel}：{type(exc).__name__}: {exc}",
                    channel=incoming.channel,
                    refs=[message_id] if message_id else [],
                    meta={"message_id": message_id, "reply_event": event.id},
                )
        return event

    def _record_inline_awareness(
        self, incoming: IncomingMessage, data: dict[str, Any], reply_event: Any
    ) -> None:
        """Record a self-awareness card the reply itself proposed.

        The model never chooses evidence ids; the gateway supplies only events
        that actually exist in this exchange. A malformed card is dropped and
        audited, never fabricated into L1.
        """
        refs = [ref for ref in (incoming.event_id, getattr(reply_event, "id", None), self._current_wake_id) if ref]
        try:
            create_card(self.store, {**data, "evidence_refs": refs})
        except ValueError as exc:
            self.store.append_event(
                "system",
                "note",
                f"觉察卡未记录：{exc}",
                channel=incoming.channel,
                meta={"trigger": data.get("trigger")},
            )

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
        self._watch_channels()
        if self.is_paused():
            if self.window.is_awake():
                self._end_wake()
                self.store.append_event(
                    "system",
                    "note",
                    "暂停期间结束当前清醒窗口；这不是睡眠，没有睡去日记。",
                    meta={"paused": True},
                )
            return
        if self.window.is_awake():
            if self.window.should_sleep(now):
                self.sleep_window(reason="清醒窗口结束")
            return
        if self._maybe_fire_alarm(now):
            return
        self._maybe_daily_floor(now)

    def _maybe_fire_alarm(self, now: datetime) -> bool:
        due = self.store.due_alarms(now=now, limit=1)
        if not due:
            return False
        alarm = due[0]
        text = self.autonomous_wake(f"她设的闹钟 {alarm.id}")
        if text:
            self.store.mark_alarm(alarm.id, "fired", note=text[:300])
        else:
            self.store.mark_alarm(alarm.id, "missed", note="闹钟到点时没能醒来（预算/模型/运行时）")
        return True

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
        if self.is_paused():
            return None
        if self.window.is_awake():
            return None
        # Check the budget before writing a wake event. A blocked attempt is not
        # an awakening; recording one would later look like an interruption with
        # no sleep, inventing a gap that never happened.
        probe = build_wake_context(
            self.store,
            self.config,
            query="",
            now=now,
            current_wake_id=None,
            new_wake=True,
            body=self._body_state(now),
        )
        probe_messages = morning_check_messages(probe, reason=reason)
        estimated = self.budget.estimate(
            probe_messages, int(getattr(self.config, "reply_max_tokens", 2000)), self.config.model
        )
        decision = self.budget.check(estimated, kind="autonomous", user_facing=False, now=now)
        if not decision.allowed:
            self.store.append_event("system", "note", f"自主醒来被预算拦下：{decision.reason}", meta={"reason": reason})
            return None
        wake_event = self._record_wake(reason, now)
        context = build_wake_context(
            self.store,
            self.config,
            query="",
            now=now,
            current_wake_id=wake_event.id,
            new_wake=True,
            body=self._body_state(now),
        )
        messages = morning_check_messages(context, reason=reason)
        try:
            if self.runtime is None:
                raise RuntimeError("runtime is not configured")
            completion = self.runtime.complete(messages, max_tokens=int(getattr(self.config, "reply_max_tokens", 2000)))
        except Exception as exc:
            self.store.append_event("system", "error", f"自主醒来失败：{type(exc).__name__}: {exc}")
            self._end_wake()
            return None
        self.budget.record_completion(completion, kind="autonomous", now=now, user_facing=False)
        text, awareness = split_awareness_block((completion.text or "").strip())
        display = text or "（自主醒来没有留下可显示的文本。）"
        observation = self.store.append_event(
            "ai-observation",
            "observation",
            display,
            actor=self.config.name,
            meta={"reason": reason, "autonomous": True},
            ts=now.isoformat(timespec="microseconds"),
        )
        if awareness is not None:
            refs = [wake_event.id, observation.id]
            try:
                create_card(self.store, {**awareness, "evidence_refs": refs})
            except ValueError as exc:
                self.store.append_event(
                    "system",
                    "note",
                    f"自主醒来的觉察卡未记录：{exc}",
                    meta={"trigger": awareness.get("trigger")},
                )
        self.window.transcript.append({"role": "assistant", "content": display})
        self.last_reply = display
        self._broadcast(display, meta={"kind": "autonomous", "reason": reason})
        self.sleep_window(reason=f"{reason}结束")
        return display

    # --------------------------------------------------------------------- sleep
    def sleep_window(self, *, reason: str = "清醒窗口结束") -> dict[str, Any] | None:
        if self.is_paused():
            return None
        if not self.window.is_awake():
            return None
        now = self.now_fn()
        transcript = list(self.window.transcript)
        messages = build_sleep_messages(self.store, self.config, transcript)
        max_tokens = int(getattr(self.config, "sleep_max_tokens", 6000))
        estimated = self.budget.estimate(messages, max_tokens, self.config.model)
        decision = self.budget.check(estimated, kind="sleep", user_facing=True, now=now)
        if not decision.allowed:
            result = SleepResult(
                diary=f"（这次睡去没有调用模型：{decision.reason}。清醒窗口结束，材料仍在 L1。）",
                next_intent=None,
                parsed=False,
            )
            applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
            self._end_wake()
            return applied
        try:
            if self.runtime is None:
                raise RuntimeError("runtime is not configured")
            completion = self.runtime.complete(messages, max_tokens=max_tokens)
        except Exception as exc:
            result = SleepResult(
                diary=f"（这次睡去没有完成模型整理：{type(exc).__name__}: {exc}。材料仍在 L1。）",
                parsed=False,
            )
            applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
            self._end_wake()
            return applied
        self.budget.record_completion(completion, kind="sleep", now=now)
        result = parse_sleep_output(completion.text or "")
        finish_reason = str(getattr(completion, "finish_reason", "") or "")
        retried = False
        if not result.parsed:
            raw = (completion.text or "")[:4000]
            self.store.append_event(
                "system",
                "error",
                f"睡去整理输出无法解析（finish_reason={finish_reason or 'unknown'}）；将用更短约束重试一次。",
                meta={"stage": "sleep_parse", "finish_reason": finish_reason, "raw": raw},
                ts=now.isoformat(timespec="microseconds"),
            )
            retry_messages = messages + [
                {"role": "assistant", "content": (completion.text or "")[:6000]},
                {
                    "role": "user",
                    "content": (
                        "上面的整理没有形成完整 JSON。请重新输出一个更短、完整的 JSON："
                        "diary 不超过 300 字；judgments 最多 2 条；open_loops 最多 1 条；loop_updates 最多 1 条；closed_loops 最多 1 条；"
                        "stance 与 next_intent 各一句；self_edits 最多 1 条且只用 append。"
                        "不要解释，不要输出完整 SELF.md。"
                    ),
                },
            ]
            retry_estimate = self.budget.estimate(
                retry_messages, min(max_tokens, 4000), self.config.model
            )
            retry_decision = self.budget.check(
                retry_estimate, kind="sleep", user_facing=True, now=now
            )
            if retry_decision.allowed:
                try:
                    completion = self.runtime.complete(
                        retry_messages, max_tokens=min(max_tokens, 4000)
                    )
                    self.budget.record_completion(completion, kind="sleep", now=now)
                    result = parse_sleep_output(completion.text or "")
                    finish_reason = str(getattr(completion, "finish_reason", "") or "")
                    retried = True
                except Exception as exc:
                    self.store.append_event(
                        "system",
                        "error",
                        f"睡去整理重试失败：{type(exc).__name__}: {exc}",
                        ts=now.isoformat(timespec="microseconds"),
                    )
            else:
                self.store.append_event(
                    "system",
                    "note",
                    f"睡去整理重试被预算拦下：{retry_decision.reason}",
                    ts=now.isoformat(timespec="microseconds"),
                )
        if not result.parsed:
            result = SleepResult(
                diary=(
                    "（这次睡去没有形成可解析的整理；原始输出已作为 system/error 留在 L1，材料仍在。"
                    "这不是一次体验，是整理失败。）"
                ),
                next_intent=None,
                parsed=False,
            )
        applied = apply_sleep_result(self.store, self.config, result, transcript=transcript, now=now)
        self.store.append_event(
            "system",
            "note",
            f"清醒窗口结束：{reason}",
            meta={
                "applied": applied,
                "turns": len(transcript),
                "parsed": bool(result.parsed),
                "retried": retried,
                "finish_reason": finish_reason,
            },
            ts=now.isoformat(timespec="microseconds"),
        )
        self._end_wake()
        return applied

    # ------------------------------------------------------------------- status
    def _body_state(self, now: datetime | None = None) -> dict[str, Any]:
        """Real system readings she can use while waking. This is data, not an experience."""
        now = now or self.now_fn()
        summary = self.budget.summary(now)
        backup: dict[str, Any] | None = None
        backup_dir = Path(self.config.home) / "backups"
        try:
            manifests = sorted(
                backup_dir.glob("*.manifest.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            manifests = []
        if manifests:
            try:
                data = json.loads(manifests[0].read_text(encoding="utf-8"))
                backup = {"created_at": data.get("created_at"), "db": data.get("db")}
            except (OSError, ValueError):
                backup = {"created_at": None, "db": manifests[0].name}
        alarms = self.store.pending_alarms(limit=1)
        due_alarm = None
        next_alarm = None
        if alarms:
            alarm = alarms[0]
            try:
                is_due = parse_ts(alarm.due_at) <= now
            except Exception:
                is_due = False
            payload = {"due_at": alarm.due_at, "reason": alarm.reason}
            if is_due:
                due_alarm = payload
            else:
                next_alarm = payload
        return {
            "events": self.store.event_count(),
            "budget": {
                "budget_cny": summary.budget_cny,
                "spent_cny": summary.spent_cny,
                "remaining_cny": summary.remaining_cny,
            },
            "stage": self.budget.stage(now),
            "last_backup": backup,
            "next_alarm": next_alarm,
            "due_alarm": due_alarm,
            "channels": [getattr(c, "name", type(c).__name__) for c in self.channels],
            "paused": self.paused_info() is not None,
        }

    def status(self) -> dict[str, Any]:
        now = self.now_fn()
        summary = self.budget.summary(now)
        pending_alarms = self.store.pending_alarms(limit=1)
        paused = self.paused_info()
        chain_ok, chain_bad = self.store.verify_chain()
        return {
            "name": self.config.name,
            "awake": self.window.is_awake(),
            "paused": paused is not None,
            "pause": paused,
            "next_alarm": _jsonable(pending_alarms[0]) if pending_alarms else None,
            "wake_remaining_seconds": round(self.window.remaining(now), 1),
            "wake_elapsed_seconds": round(self.window.elapsed(now), 1),
            "wake_count": self.window.wake_count,
            "queue": self.queue.qsize(),
            "event_count": self.store.event_count(),
            "last_event_ts": self.store.last_event_ts(),
            "chain": {"ok": chain_ok, "bad_event": chain_bad},
            "budget": _jsonable(summary),
            "budget_stage": self.budget.stage(now),
            "channels": [getattr(c, "name", type(c).__name__) for c in self.channels],
            "channel_health": self._channel_health(),
            "started_at": self.started_at,
        }

    def tank(self) -> dict[str, Any]:
        self_text = self.store.current_self() or ""
        stance = self.store.current_stance()
        last_sleep_event = self.store.latest_event("sleep")
        last_sleep: dict[str, Any] | None = None
        if last_sleep_event is not None:
            last_sleep = _jsonable(last_sleep_event)
            meta = last_sleep_event.meta or {}
            diary_id = meta.get("diary_event")
            diary = self.store.get_event(str(diary_id)) if diary_id else None
            last_sleep["diary_event"] = diary.id if diary else None
            last_sleep["diary_ts"] = diary.ts if diary else None
            last_sleep["diary"] = diary.content if diary else None
            last_sleep["next_intent"] = meta.get("next_intent")
        judgments = [
            {
                **_jsonable(j),
                "status": self.store.judgment_status(j.id),
                "supersedes": self.store.superseded_targets(j.id),
            }
            for j in self.store.current_judgments(limit=10)
        ]
        cards = [
            {**_jsonable(c), "status": self.store.card_status(c.id)}
            for c in self.store.awareness_cards(limit=10)
        ]
        return {
            "name": self.config.name,
            "status": self.status(),
            "self": self_text,
            "stance": _jsonable(stance) if stance else None,
            "self_history": [
                {
                    "id": v.id,
                    "ts": v.ts,
                    "reason": v.reason,
                    "evidence_refs": list(v.evidence_refs),
                    "diff": v.diff,
                }
                for v in self.store.self_history(limit=5)
            ],
            "last_sleep": last_sleep,
            "open_loops": _jsonable(self.store.open_loops(limit=20)),
            "alarms": _jsonable(self.store.pending_alarms(limit=20)),
            "awareness": cards,
            "recent_events": _jsonable(self.store.events(limit=30)),
            "judgments": judgments,
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

    def _notification_targets(self, channel: Any) -> list[str | None]:
        """Who receives her self-initiated output on this channel.

        HTTP has no addressee (the reply list is the inbox); email and OneBot
        need a real recipient, so they fall back to the already allowlisted
        operator rather than implying she may message strangers.
        """
        name = str(getattr(channel, "name", ""))
        if name == "email":
            return list(self.config.email.notify_to or self.config.email.allow_senders)
        if name == "onebot":
            return list(self.config.onebot.allowed_user_ids)
        return [None]

    def _broadcast(self, text: str, *, meta: dict[str, Any] | None = None) -> None:
        payload = meta or {}
        for channel in self.channels:
            name = str(getattr(channel, "name", type(channel).__name__))
            targets = self._notification_targets(channel)
            if not targets:
                self.store.append_event(
                    "system",
                    "note",
                    f"自主输出没有可送达的通知目标（{name}）；内容已留在 L1。",
                    meta={"kind": payload.get("kind"), "channel": name},
                )
                continue
            for target in targets:
                try:
                    delivered = channel.send(target, text, meta=payload)
                    if delivered is False:
                        raise RuntimeError("channel.send returned False")
                except Exception as exc:
                    self.store.append_event(
                        "system",
                        "error",
                        f"自主输出投递失败 via {name}：{type(exc).__name__}: {exc}",
                        meta={"kind": payload.get("kind"), "channel": name},
                    )
