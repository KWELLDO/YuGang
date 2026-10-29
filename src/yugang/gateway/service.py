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

from ..audit import audit_memory, memory_health
from ..ids import day_key, now_iso, parse_ts, utcnow
from ..memory.tools import MemoryToolbox
from ..world.tools import WorldToolbox
from ..memory.trust import inbound_meta
from ..protocol.sleep import SleepResult, apply_sleep_result, build_sleep_messages, parse_sleep_output
from ..protocol.wake import build_messages, build_wake_context, morning_check_messages, record_wake
from ..selfmodel.awareness import (
    TRIGGERS,
    build_awareness_messages,
    create_card,
    parse_awareness_output,
    split_awareness_block,
)
from ..selfmodel.identity import bootstrap_identity
from ..selfmodel.self_md import bootstrap_self
from .budget import BudgetGuard
from .channels.base import IncomingMessage
from .window import WakeWindow


class ToolBudgetExceeded(RuntimeError):
    """A tool round would exceed the daily budget."""


class ToolRoundLimitExceeded(RuntimeError):
    """She kept calling tools past the configured round limit."""


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
        self._wake_health: dict[str, Any] | None = None
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
        bootstrap_identity(self.store, self.config)
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
        # Mark the origin while it is still known. Every channel funnels through
        # here, so this is the one place that cannot be forgotten.
        person = self.store.person_for(incoming.channel, incoming.sender) if incoming.sender else None
        incoming.person = person
        if person is not None:
            self.store.touch_person(person.id)
        incoming.meta = {
            **(incoming.meta or {}),
            **inbound_meta(
                incoming.channel,
                incoming.sender,
                trusted_senders=getattr(self.config, "trusted_senders", ()),
                person=person,
            ),
        }
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

    # ------------------------------------------------------------ memory tools
    def _toolbox(self) -> MemoryToolbox | None:
        if not bool(getattr(self.config, "memory_tools_enabled", True)):
            return None
        world = WorldToolbox(self.config, self.store, now_fn=self.now_fn)
        return MemoryToolbox(
            self.store, self.config, now_fn=self.now_fn, world=world, extras=[self._self_toolbox()]
        )

    def _world_enabled(self) -> bool:
        world = getattr(self.config, "world", None)
        return bool(world is not None and getattr(world, "enabled", False))

    def _self_change_enabled(self) -> bool:
        lane = getattr(self.config, "self_change", None)
        return bool(lane is not None and getattr(lane, "enabled", False))

    def _test_backend(self) -> dict[str, Any]:
        """Which sandbox backs source_test, and whether that is the strong one."""
        try:
            from ..world.sandbox import backend_available

            name, path = backend_available()
        except Exception as exc:  # noqa: BLE001 - status must never take /status down
            return {"name": None, "weak": True, "error": f"{type(exc).__name__}: {exc}"}
        return {"name": name, "path": path, "weak": name != "bubblewrap"}

    def _self_paths(self) -> Any:
        from pathlib import Path

        from ..selfwork.mirror import SelfWorkPaths

        lane = getattr(self.config, "self_change", None)
        home = Path(self.config.home)
        source = str(getattr(lane, "source_root", "") or "")
        return SelfWorkPaths(
            home=home,
            source_root=Path(source).expanduser() if source else home.parent / "yugang",
            venv_python=str(getattr(lane, "venv_python", "/opt/yugang-venv/bin/python")),
            max_file_bytes=int(getattr(lane, "max_file_bytes", 200_000)),
            test_timeout_seconds=float(getattr(lane, "test_timeout_seconds", 600.0)),
        )

    def _self_toolbox(self) -> Any:
        """Her source-mirror lane, if it is switched on.

        A missing module must not take the whole tool table down with it: memory
        and world tools still work while the lane is being deployed.
        """
        if not self._self_change_enabled():
            return None
        try:
            from ..selfwork.tools import SelfToolbox

            return SelfToolbox(self._self_paths(), self.store, now_fn=self.now_fn)
        except Exception:  # noqa: BLE001 - optional capability
            return None

    def _tool_overhead_cny(self) -> float:
        toolbox = self._toolbox()
        if toolbox is None:
            return 0.0
        price_in, _ = self.budget.prices(getattr(self.config, "model", None))
        schema_chars = len(json.dumps(toolbox.schemas(), ensure_ascii=False))
        return schema_chars / 1_000_000.0 * price_in

    def _complete_with_memory_tools(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        kind: str,
        user_facing: bool,
        purpose: str = "chat",
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Run the model, letting her look things up in her own memory.

        Every round goes through the same budget gate. Tool schemas count as
        input, and each tool result is appended to the next request, so a tool
        loop cannot quietly spend past the daily budget.
        """
        if self.runtime is None:
            raise RuntimeError("runtime is not configured")
        toolbox = self._toolbox()
        if toolbox is None:
            return self.runtime.complete(messages, max_tokens=max_tokens, purpose=purpose), []
        schemas = toolbox.schemas()
        overhead = self._tool_overhead_cny()
        rounds = max(0, int(getattr(self.config, "max_tool_rounds", 6)))
        working = list(messages)
        trace: list[dict[str, Any]] = []
        for index in range(rounds + 1):
            estimated = self.budget.estimate(working, max_tokens, self.config.model) + overhead
            decision = self.budget.check(estimated, kind=kind, user_facing=user_facing, now=self.now_fn())
            if not decision.allowed:
                raise ToolBudgetExceeded(decision.reason)
            completion = self.runtime.complete(
                working,
                max_tokens=max_tokens,
                purpose=purpose,
                tools=schemas,
            )
            self.budget.record_completion(
                completion, kind=kind, now=self.now_fn(), user_facing=user_facing
            )
            calls = list(getattr(completion, "tool_calls", []) or [])
            if not calls:
                return completion, trace
            if index >= rounds:
                return self._answer_at_the_ceiling(
                    working,
                    max_tokens=max_tokens,
                    kind=kind,
                    user_facing=user_facing,
                    purpose=purpose,
                    rounds=rounds,
                    trace=trace,
                )
            working.append(
                {"role": "assistant", "content": completion.text or "", "tool_calls": calls}
            )
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = function.get("arguments")
                result = toolbox.run(name, arguments)
                record: dict[str, Any] = {"name": name, "ok": bool(result.get("ok"))}
                if not result.get("ok"):
                    record["error"] = str(result.get("error") or "")[:300]
                trace.append(record)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(call.get("id") or ""),
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
        return self._answer_at_the_ceiling(
            working,
            max_tokens=max_tokens,
            kind=kind,
            user_facing=user_facing,
            purpose=purpose,
            rounds=rounds,
            trace=trace,
        )

    _CEILING_NOTE = (
        "（系统）工具轮数到顶：这次已经用了 {rounds} 轮，不能再调用工具了。"
        "用你手上已经查到的材料回答；没查到的、没做完的，就直接说没查到、没做完。"
    )

    def _answer_at_the_ceiling(
        self,
        working: list[dict[str, Any]],
        *,
        max_tokens: int,
        kind: str,
        user_facing: bool,
        purpose: str,
        rounds: int,
        trace: list[dict[str, Any]],
    ) -> tuple[Any, list[dict[str, Any]]]:
        """Spend the last call on answering, not on a refusal.

        2026-10-26 12:39: 老大问「你的机制是每次运行都要读一遍 self 吗」，她问了三次目录
        还想再问，第四轮撞上「工具调用超过 3 轮」——整轮被判失败，他收到的是一条系统错误，
        而那一轮她已经查到了答案要用的东西。上限本身是对的（无界检索会花光预算和她自己的
        时间），但「到顶」不等于「不回答」。

        所以到顶后不再给工具，让她拿手里的材料说话，并在 meta.tools 里留下
        tool_round_limit，事后能看出这句回答是在什么条件下说出来的。
        """
        if self.runtime is None:
            raise RuntimeError("runtime is not configured")
        final = list(working)
        final.append({"role": "system", "content": self._CEILING_NOTE.format(rounds=rounds)})
        estimated = self.budget.estimate(final, max_tokens, self.config.model)
        decision = self.budget.check(estimated, kind=kind, user_facing=user_facing, now=self.now_fn())
        if not decision.allowed:
            raise ToolBudgetExceeded(decision.reason)
        completion = self.runtime.complete(final, max_tokens=max_tokens, purpose=purpose)
        self.budget.record_completion(completion, kind=kind, now=self.now_fn(), user_facing=user_facing)
        trace.append({"name": "-", "ok": True, "note": "tool_round_limit", "rounds": rounds})
        if list(getattr(completion, "tool_calls", []) or []):
            # Nothing was offered, so this can only be a provider echoing a call
            # back. Refusing is still better than pretending the tools ran.
            raise ToolRoundLimitExceeded(
                f"工具调用超过 {rounds} 轮；停止继续调用，避免无界检索和无界动作。"
            )
        return completion, trace

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
            wake_event = self._record_wake(
                f"用户消息 via {incoming.channel}", now, trigger_event=incoming.event_id
            )
            context = build_wake_context(
                self.store,
                self.config,
                query=incoming.content,
                now=now,
                current_wake_id=wake_event.id,
                new_wake=True,
                body=self._body_state(now),
                channel=incoming.channel,
                person=incoming.person,
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
                channel=incoming.channel,
                person=incoming.person,
            )
            self.window.touch(now)
        messages = build_messages(
            context,
            incoming.content,
            tools_enabled=bool(getattr(self.config, "memory_tools_enabled", True)),
            world_enabled=self._world_enabled(),
            self_change_enabled=self._self_change_enabled(),
        )
        # A second message in the same wake is a continuation of this
        # conversation, not a fresh retrieval. Keep the actual prior turns
        # (bounded) between the system context and the new user message.
        history = self.window.history()
        if history:
            messages[1:1] = history
        max_tokens = int(getattr(self.config, "reply_max_tokens", 2000))
        estimated = (
            self.budget.estimate(messages, max_tokens, self.config.model)
            + self._tool_overhead_cny()
        )
        decision = self.budget.check(estimated, kind="chat", user_facing=True, now=now)
        if not decision.allowed:
            reply = BudgetGuard.fail_loud(decision.reason)
            self._finalize(incoming, reply, error=decision.reason, now=now)
            return reply
        tool_trace: list[dict[str, Any]] = []
        try:
            completion, tool_trace = self._complete_with_memory_tools(
                messages,
                max_tokens=max_tokens,
                kind="chat",
                user_facing=True,
                purpose="chat",
            )
        except ToolBudgetExceeded as exc:
            self._fail(incoming, f"记忆工具预算不足：{exc}")
            return None
        except ToolRoundLimitExceeded as exc:
            self._fail(incoming, str(exc))
            return None
        except Exception as exc:
            self._fail(incoming, f"模型调用失败：{type(exc).__name__}: {exc}")
            return None
        text, awareness = split_awareness_block((completion.text or "").strip())
        if not text:
            self._fail(incoming, "模型返回了空回复")
            return None
        self.window.touch(now)
        self.window.transcript.append({"role": "user", "content": incoming.content})
        self.window.transcript.append({"role": "assistant", "content": text})
        self._finalize(incoming, text, now=now, awareness=awareness, tools=tool_trace)
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
                delivered = channel.send(incoming.sender, notice, meta=self._reply_meta(incoming))
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

    def _reply_meta(self, incoming: IncomingMessage) -> dict[str, Any]:
        """Addressing a reply needs more than the text.

        The QQ platform treats a message without `msg_id` as proactive (limited
        quota, and it may be refused); everything else only needs the subject.
        Unknown keys are ignored by the other channels.
        """
        meta: dict[str, Any] = {"subject": (incoming.meta or {}).get("subject")}
        msg_id = (incoming.meta or {}).get("msg_id")
        if msg_id:
            meta["msg_id"] = msg_id
        return meta

    def _finalize(
        self,
        incoming: IncomingMessage,
        text: str,
        *,
        error: str | None = None,
        now: datetime | None = None,
        awareness: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> Any:
        now = now or self.now_fn()
        message_id = incoming.message_id
        if message_id:
            self.store.mark_message(message_id, "failed" if error else "processed", error=error)
            self.store.update_message_meta(message_id, {"reply": text, "channel": incoming.channel})
        meta: dict[str, Any] = {"reply": text, "channel": incoming.channel}
        if error:
            meta["error"] = error
        if tools:
            meta["tools"] = tools
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
        if not error:
            self._confirm_citations(
                text,
                source_event=event.id,
                now=now,
                exclude=[incoming.event_id] if incoming.event_id else [],
            )
        self.last_reply = text
        incoming.finish(text, error)
        channel = self._channel(incoming.channel)
        if channel is not None and incoming.channel != "http":
            try:
                delivered = channel.send(incoming.sender, text, meta=self._reply_meta(incoming))
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

    def _confirm_citations(
        self,
        text: str,
        *,
        source_event: str,
        now: datetime,
        exclude: list[str] | None = None,
    ) -> list[str]:
        """Confirm old L1 events she cited in a reply.

        The current waking window is the boundary: the message that just woke
        her is context, not a memory she remembered. Failure to update a derived
        counter must be visible but must not turn a delivered reply into an
        error.
        """
        if not text or not source_event:
            return []
        wake = self.store.get_event(self._current_wake_id) if self._current_wake_id else None
        before_ts = wake.ts if wake is not None else now.isoformat(timespec="microseconds")
        try:
            return self.store.confirm_citations(
                text,
                source_event=source_event,
                before_ts=before_ts,
                exclude=exclude or [],
                ts=now.isoformat(timespec="microseconds"),
            )
        except Exception as exc:
            self.store.append_event(
                "system",
                "error",
                f"引用确认失败：{type(exc).__name__}: {exc}",
                meta={"source_event": source_event},
            )
            return []

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
    def _record_wake(
        self, reason: str, now: datetime, trigger_event: str | None = None
    ) -> Any:
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
        wake_event = record_wake(
            self.store,
            reason,
            now=now,
            gap_seconds=gap,
            interrupted=interrupted,
            trigger_event=trigger_event,
        )
        self._current_wake_id = wake_event.id
        self._wake_health = None
        self.window.wake(now)
        return wake_event

    def _end_wake(self) -> None:
        self.window.sleep()
        self._current_wake_id = None
        self._wake_health = None

    # --------------------------------------------------------------------- idle
    def idle_tick(self) -> None:
        now = self.now_fn()
        monotonic = time.monotonic()
        if monotonic - self._last_idle_check < 2.0:
            return
        self._last_idle_check = monotonic
        self._watch_channels()
        # What the root-side applier did to her body arrives as a file, never as
        # a phone call: the L1 chain keeps a single writer.
        try:
            self._ingest_apply_results()
        except Exception as exc:  # noqa: BLE001 - the tick loop must not die
            self.store.append_event(
                "system", "error", f"收改动结果时出错：{type(exc).__name__}: {exc}"
            )
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
        probe_messages = morning_check_messages(
            probe,
            reason=reason,
            tools_enabled=bool(getattr(self.config, "memory_tools_enabled", True)),
            world_enabled=self._world_enabled(),
            self_change_enabled=self._self_change_enabled(),
        )
        estimated = self.budget.estimate(
            probe_messages, int(getattr(self.config, "reply_max_tokens", 2000)), self.config.model
        ) + self._tool_overhead_cny()
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
        messages = morning_check_messages(
            context,
            reason=reason,
            tools_enabled=bool(getattr(self.config, "memory_tools_enabled", True)),
            world_enabled=self._world_enabled(),
            self_change_enabled=self._self_change_enabled(),
        )
        tool_trace: list[dict[str, Any]] = []
        try:
            completion, tool_trace = self._complete_with_memory_tools(
                messages,
                max_tokens=int(getattr(self.config, "reply_max_tokens", 2000)),
                kind="autonomous",
                user_facing=False,
                purpose="autonomous",
            )
        except (ToolBudgetExceeded, ToolRoundLimitExceeded) as exc:
            self.store.append_event(
                "system",
                "error",
                f"自主醒来被记忆工具限制终止：{exc}",
                meta={"reason": reason},
                ts=now.isoformat(timespec="microseconds"),
            )
            self._end_wake()
            return None
        except Exception as exc:
            self.store.append_event("system", "error", f"自主醒来失败：{type(exc).__name__}: {exc}")
            self._end_wake()
            return None
        text, awareness = split_awareness_block((completion.text or "").strip())
        display = text or "（自主醒来没有留下可显示的文本。）"
        observation = self.store.append_event(
            "ai-observation",
            "observation",
            display,
            actor=self.config.name,
            meta={"reason": reason, "autonomous": True, **({"tools": tool_trace} if tool_trace else {})},
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
        self._confirm_citations(display, source_event=observation.id, now=now)
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
        messages = build_sleep_messages(self.store, self.config, transcript, now=now)
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
            "memory_health": self._memory_health_state(now),
        }

    def _memory_health_state(self, now: datetime) -> dict[str, Any]:
        """Audit once per waking window, not on every turn.

        A broken audit must not prevent her from waking. If the check itself
        fails, she is told that the check failed rather than being told her
        memory is fine.
        """
        if self._wake_health is not None:
            return self._wake_health
        try:
            health = memory_health(self.store, self.config, now=now)
        except Exception as exc:
            health = {
                "healthy": False,
                "audit_error": f"{type(exc).__name__}: {exc}",
                "problems": [f"记忆体检无法完成：{type(exc).__name__}: {exc}"],
                "warnings": [],
                "problem_count": 1,
                "warning_count": 0,
            }
        self._wake_health = health
        return health

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
            "cache": self.budget.cache(now),
            "world": self._world_status(now),
            "self_change": self._self_change_status(),
            "channels": [getattr(c, "name", type(c).__name__) for c in self.channels],
            "channel_health": self._channel_health(),
            "started_at": self.started_at,
        }

    def _world_status(self, now) -> dict[str, Any]:
        """What she can reach right now, and what she did today.

        Reads are cheap to undo; acts are not. The two are counted separately so
        the tank page and the CLI can show the difference instead of one blended
        number.
        """
        from ..ids import day_key
        from ..world.sandbox import backend_available

        toolbox = self._toolbox()
        tools = sorted(toolbox.names()) if toolbox is not None else []
        backend, detail = backend_available()
        today = self.store.world_summary(day_key(now))
        return {
            "enabled": self._world_enabled(),
            "tools": tools,
            "act_tools": [name for name in tools if name in ("files_write", "sandbox_run")],
            "sandbox": {"backend": backend, "detail": detail},
            "today": today,
        }

    def _self_change_status(self) -> dict[str, Any]:
        """What she may read of herself, what she proposed, what became of it."""
        if not self._self_change_enabled():
            return {"enabled": False}
        from pathlib import Path

        try:
            from ..selfwork.mirror import Mirror
        except Exception as exc:  # noqa: BLE001 - status must survive a missing lane
            return {"enabled": True, "installed": False, "error": f"{type(exc).__name__}: {exc}"}

        paths = self._self_paths()
        toolbox = self._self_toolbox()
        out: dict[str, Any] = {
            "enabled": True,
            "tools": sorted(toolbox.names()) if toolbox is not None else [],
            "mirror": None,
            "proposals": [],
            "results": [],
            # Her tests run in a sandbox. Which one matters: without bubblewrap
            # the run is weaker, and a green result reads the same either way.
            "test_backend": self._test_backend(),
        }
        try:
            out["mirror"] = Mirror(paths.mirror_root, source=paths.source_root).status()
        except Exception as exc:  # noqa: BLE001 - status must never take /status down
            out["mirror"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            for path in sorted(Path(paths.proposals_dir).glob("*/meta.json"))[-5:]:
                meta = json.loads(path.read_text(encoding="utf-8"))
                out["proposals"].append(
                    {key: meta.get(key) for key in ("id", "created_ts", "summary", "tier", "files", "commit")}
                )
        except Exception:  # noqa: BLE001
            pass
        try:
            for path in sorted(Path(paths.results_dir).glob("*.json"))[-3:]:
                out["results"].append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
        return out

    def _ingest_apply_results(self) -> None:
        """Read what the root-side applier did to her body, exactly once per file.

        The applier runs outside her process and may restart it, so the result
        arrives as a file on disk. Reading it here keeps a single writer for the
        L1 hash chain: she learns what happened to her own body the same way she
        learns everything else, as an event she can recall and argue with.
        """
        from pathlib import Path

        results_dir = Path(self.config.home) / "apply-results"
        if not results_dir.is_dir():
            return
        for path in sorted(results_dir.glob("*.json"))[:5]:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - a broken file must not loop
                done = path.with_name(path.name + ".bad")
                try:
                    path.rename(done)
                except OSError:
                    pass
                self.store.append_event(
                    "system", "error", f"读不懂一份改动结果：{path.name}（{type(exc).__name__}）",
                    meta={"file": str(path)},
                )
                continue
            try:
                self._write_apply_result_event(payload)
            except Exception as exc:  # noqa: BLE001
                self.store.append_event(
                    "system", "error", f"把改动结果写成记忆时出错：{type(exc).__name__}: {exc}",
                    meta={"file": str(path)},
                )
            try:
                path.rename(path.with_name(path.name + ".done"))
            except OSError:
                pass

    def _write_apply_result_event(self, payload: dict[str, Any]) -> None:
        proposal = str(payload.get("id") or "?")
        stage = str(payload.get("stage") or "?")
        ok = bool(payload.get("ok"))
        env = payload.get("env") or {}
        base = str(env.get("base_commit") or "")[:7]
        commit = str(env.get("commit") or payload.get("applied_commit") or "")[:7]
        error = " ".join(str(payload.get("error") or "").split())[:400]
        if ok:
            text = (
                f"我改了自己：{payload.get('summary') or proposal}。"
                f"测试 {env.get('pytest_summary') or '通过'}，合入 {commit}。"
            )
            meta = {"proposal": proposal, "commit": commit, "tier": payload.get("tier"), "stage": "done"}
        elif stage == "approve":
            text = f"我提的改动 {proposal} 还在等批准：{error}"
            meta = {"proposal": proposal, "stage": stage, "tier": payload.get("tier")}
        elif payload.get("rolled_back"):
            text = (
                f"我的改动没过：在 {stage} 这一步失败。{error}。"
                f"已经回退到 {base or '原来的版本'}，什么都没变。"
            )
            meta = {
                "proposal": proposal,
                "stage": stage,
                "rolled_back": True,
                "tier": payload.get("tier"),
                "env": {
                    key: env.get(key)
                    for key in ("python", "unit_active", "health_ok", "failed_tests", "journal_tail")
                },
            }
        else:
            text = f"我的改动没过：在 {stage} 这一步失败。{error}。这次没有动到 live，也没有回退。"
            meta = {"proposal": proposal, "stage": stage, "rolled_back": False, "tier": payload.get("tier")}
        self._append_apply_result(text, meta)

    def _append_apply_result(self, text: str, meta: dict[str, Any]) -> bool:
        """Write one apply result into L1 — but never the same one twice.

        The applier writes a result file every time it runs, so a proposal that
        keeps failing at the same step produces the same sentence again and
        again. That happened: a timer re-ran an already-applied proposal for two
        days and put 347 identical "verify 这一步失败" lines into L1, which pushed
        everything else out of the recent-memory block she reads on waking. The
        repetition adds nothing she can act on; the first one already says it.
        """
        proposal = str(meta.get("proposal") or "")
        for event in self.store.events(limit=200, order="desc"):
            if event.content != text:
                continue
            if str((event.meta or {}).get("proposal") or "") == proposal:
                return False
        self.store.append_event("ai-observation", "action", text, meta=meta)
        return True

    def audit(self) -> dict[str, Any]:
        """Run the read-only memory audit. Never repairs anything."""
        return _jsonable(audit_memory(self.store, self.config, now=self.now_fn()))

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
        current_judgments = self.store.current_judgments(limit=10)
        review_counts = self.store.judgment_review_counts([j.id for j in current_judgments])
        judgments = [
            {
                **_jsonable(j),
                "status": self.store.judgment_status(j.id),
                "supersedes": self.store.superseded_targets(j.id),
                "reconfirmations": int(
                    (review_counts.get(j.id) or {}).get("supported") or 0
                ),
                "reviews": review_counts.get(j.id)
                or {"supported": 0, "refuted": 0, "needs-evidence": 0, "total": 0, "last": None},
            }
            for j in current_judgments
        ]
        cards = [
            {**_jsonable(c), "status": self.store.card_status(c.id)}
            for c in self.store.awareness_cards(limit=10)
        ]
        episodes = self.store.episodes(limit=8)
        active_practices = self.store.practices(status="active", limit=50)
        dropped_practices = self.store.practices(status="dropped", limit=200)
        practice_rows = []
        check_count = 0
        for practice in active_practices:
            reviews = self.store.practice_reviews(practice.id, limit=5)
            check_count += len(reviews)
            serialized = _jsonable(reviews)
            practice_rows.append(
                {
                    **_jsonable(practice),
                    "checks": serialized,
                    "last_check": serialized[-1] if serialized else None,
                }
            )
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
            "episodes": [
                {
                    **_jsonable(episode),
                    "event_count": len(episode.event_ids),
                }
                for episode in episodes
            ],
            "episode_count": self.store.episode_count(),
            "open_loops": _jsonable(self.store.open_loops(limit=20)),
            "alarms": _jsonable(self.store.pending_alarms(limit=20)),
            "awareness": cards,
            "practices": practice_rows,
            "practice_stats": {
                "active": len(active_practices),
                "dropped": len(dropped_practices),
                "checks": check_count,
            },
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
        if name == "qq":
            return list(self.config.qq.allowed_openids)
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
