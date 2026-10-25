"""Her body, checked from outside her own process.

The failure this exists for: a gateway whose HTTP thread keeps answering while
nothing reaches her. A process that is alive is not evidence that she is
reachable, and a quiet afternoon is not evidence that something is wrong -- she
sleeps. So the probe reports facts and leaves the judgement to a reader: is the
gateway answering, is the L1 chain intact, is there a backup that would actually
restore her, has the budget already run out, how much disk is left. `deep` adds
one real model round trip, which is the only check that proves the credential
and the network still work.

Nothing here repairs anything, and nothing here pretends to be a heartbeat from
inside her. It writes what it saw; the alerting is a separate decision so that a
flapping channel cannot flood L1 with notes.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .audit import audit_memory
from .ids import day_key, parse_ts, utcnow

GATEWAY_TIMEOUT_SECONDS = 5.0
HISTORY_LIMIT = 500
ALERT_COOLDOWN_HOURS = 6.0


def health_dir(config: Any) -> Path:
    return Path(config.home).expanduser() / "health"


def health_path(config: Any) -> Path:
    return health_dir(config) / "health.json"


def history_path(config: Any) -> Path:
    return health_dir(config) / "history.jsonl"


def state_path(config: Any) -> Path:
    return health_dir(config) / "alert-state.json"


def deep_stamp_path(config: Any) -> Path:
    """The day the paid probe last ran.

    The stamp lives next to the code that spends the money, not in the shell
    wrapper: a config error before the probe starts must not consume the day, and
    a crash after the call must not spend it again fifteen minutes later.
    """
    return health_dir(config) / "deep-probe.day"


def deep_ran_today(config: Any, *, now: datetime | None = None) -> bool:
    path = deep_stamp_path(config)
    try:
        return path.read_text(encoding="utf-8").strip() == day_key(now or utcnow())
    except OSError:
        return False


def _stamp_deep(config: Any, *, now: datetime | None = None) -> None:
    _atomic_write(deep_stamp_path(config), day_key(now or utcnow()))


def probe_gateway(config: Any, *, timeout: float = GATEWAY_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask the live process what it thinks, over the same port a user would."""
    http = getattr(config, "http", None)
    if http is None or not getattr(http, "enabled", False):
        return {"reachable": None, "skipped": "http disabled"}
    url = f"http://{http.host}:{int(http.port)}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
            return {"reachable": True, "status": int(response.status), "detail": body}
    except urllib.error.HTTPError as exc:
        # 503 still carries a body, and that body is the interesting part.
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return {"reachable": True, "status": int(exc.code), "detail": body, "error": f"HTTP {exc.code}"}
    except Exception as exc:  # noqa: BLE001 - the point is to report any failure
        return {"reachable": False, "status": None, "detail": {}, "error": f"{type(exc).__name__}: {exc}"}


def _probe_model(store: Any, config: Any, runtime: Any, *, now: datetime) -> dict[str, Any]:
    """One tiny real completion: the only proof the credential still works."""
    import time as _time

    from .gateway.budget import BudgetGuard

    started = _time.monotonic()
    try:
        completion = runtime.complete(
            [{"role": "user", "content": "健康探针。只回复一个字：ok"}],
            max_tokens=16,
            purpose="probe",
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    elapsed_ms = int((_time.monotonic() - started) * 1000)
    text = str(getattr(completion, "text", "") or "").strip()
    try:
        BudgetGuard(store, config).record_completion(completion, kind="probe", now=now)
    except Exception as exc:  # noqa: BLE001 - accounting must not mask a good probe
        return {"ok": bool(text), "text": text[:40], "ms": elapsed_ms, "accounting_error": f"{type(exc).__name__}: {exc}"}
    return {"ok": bool(text), "text": text[:40], "ms": elapsed_ms}


def collect_health(
    store: Any,
    config: Any,
    *,
    now: datetime | None = None,
    deep: bool = False,
    runtime: Any = None,
    gateway_timeout: float = GATEWAY_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    now = now or utcnow()
    problems: list[str] = []
    warnings: list[str] = []

    report = audit_memory(store, config, now=now)
    problems.extend(str(item) for item in report.get("problems") or [])
    warnings.extend(str(item) for item in report.get("warnings") or [])

    gateway = probe_gateway(config, timeout=gateway_timeout)
    if gateway.get("reachable") is False:
        problems.append(f"网关没有应答（{gateway.get('error')}）")
    elif gateway.get("reachable") is True:
        status = gateway.get("status")
        if status != 200:
            problems.append(f"网关自述不健康：HTTP {status}")
        detail = gateway.get("detail") or {}
        if detail.get("degraded"):
            warnings.append("网关自述有通道不健康：进程活着，但外面进不来消息")
        if detail.get("paused"):
            warnings.append("她当前被操作员暂停，模型调用关闭")

    disk: dict[str, Any] = {}
    try:
        usage = shutil.disk_usage(str(Path(config.home).expanduser()))
        disk = {
            "free_gb": round(usage.free / (1024**3), 2),
            "total_gb": round(usage.total / (1024**3), 2),
        }
        if usage.free < 1 * 1024**3:
            problems.append(f"磁盘剩余不足 1G（{disk['free_gb']}G），备份可能写不进去")
        elif usage.free < 3 * 1024**3:
            warnings.append(f"磁盘剩余 {disk['free_gb']}G")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"读不到磁盘用量：{type(exc).__name__}: {exc}")

    day = day_key(now)
    try:
        summary = store.budget_summary(day, float(getattr(config, "daily_budget_cny", 5.0)))
        budget = {
            "day": day,
            "spent_cny": round(float(summary.spent_cny), 6),
            "budget_cny": float(summary.budget_cny),
            "remaining_cny": round(float(summary.remaining_cny), 6),
        }
        if float(summary.remaining_cny) <= 0:
            problems.append(f"今天的预算已用尽（{budget['spent_cny']} / {budget['budget_cny']} 元）：她会 fail-loud，不会静默不回")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"读不到预算：{type(exc).__name__}: {exc}")
        budget = {"day": day, "error": f"{type(exc).__name__}: {exc}"}

    model: dict[str, Any] | None = None
    if deep and runtime is not None:
        # Stamp before the call, not after: the stamp exists to bound spending,
        # and a crash between the call and the write would re-bill every tick.
        _stamp_deep(config, now=now)
        model = _probe_model(store, config, runtime, now=now)
        if not model.get("ok"):
            problems.append(f"模型探针失败：{model.get('error') or '空回复'}")
    elif deep:
        problems.append("要求了 --deep 但没有可用的 runtime")

    return {
        "ok": not problems,
        "ts": now.isoformat(),
        "home": str(Path(config.home).expanduser()),
        "gateway": gateway,
        "memory": {
            "chain_ok": (report.get("events") or {}).get("chain_ok"),
            "events": (report.get("events") or {}).get("count"),
            "problems": list(report.get("problems") or []),
            "warnings": list(report.get("warnings") or []),
            "backup_age_hours": (report.get("backup") or {}).get("age_hours"),
        },
        "disk": disk,
        "budget": budget,
        "model": model,
        "problems": problems,
        "warnings": warnings,
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_health(config: Any, payload: dict[str, Any]) -> str:
    target = health_path(config)
    _atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return str(target)


def append_history(config: Any, payload: dict[str, Any], *, limit: int = HISTORY_LIMIT) -> int:
    """Append one probe, then keep the file bounded.

    Truncating in place rather than rotating means there is never a second file
    to clean up, and the newest lines are always the ones on disk.
    """
    target = history_path(config)
    line = json.dumps(
        {
            "ts": payload.get("ts"),
            "ok": payload.get("ok"),
            "problems": payload.get("problems") or [],
        },
        ensure_ascii=False,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    lines = target.read_text(encoding="utf-8").splitlines()
    if len(lines) > limit:
        _atomic_write(target, "\n".join(lines[-limit:]) + "\n")
        return limit
    return len(lines)


def read_state(config: Any) -> dict[str, Any]:
    target = state_path(config)
    if not target.exists():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _age_hours(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return (now - parse_ts(ts)).total_seconds() / 3600.0
    except Exception:
        return None


def transition(
    store: Any,
    config: Any,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    cooldown_hours: float = ALERT_COOLDOWN_HOURS,
) -> dict[str, Any]:
    """Decide whether this probe deserves an L1 note.

    A persistent fault is one note, not ninety-six a day. A recovery note says
    how many suppressed failures it is closing, so silence never looks like
    health.
    """
    now = now or utcnow()
    state = read_state(config)
    problems = [str(item) for item in payload.get("problems") or []]
    signature = " | ".join(sorted(problems))
    previous = str(state.get("signature") or "")
    suppressed = int(state.get("suppressed") or 0)
    notified: str | None = None
    text = ""

    if not signature:
        if previous:
            text = f"体检恢复（此前未通过期间还有 {suppressed} 次未通知，已抑制）"
            notified = "recovery"
        suppressed = 0
    else:
        first_line = problems[0] if problems else "未知问题"
        more = f"；另有 {len(problems) - 1} 条" if len(problems) > 1 else ""
        if signature != previous:
            tail = f"（此前 {suppressed} 次未通知，已抑制）" if suppressed else ""
            text = f"体检未通过：{first_line}{more}{tail}"
            notified = "problem"
            suppressed = 0
        elif (_age_hours(state.get("last_notified_ts"), now) or 0) >= cooldown_hours:
            text = f"体检仍未通过：{first_line}{more}（已持续，期间 {suppressed} 次未通知）"
            notified = "still-bad"
            suppressed = 0
        else:
            suppressed += 1

    if notified:
        store.append_event(
            "system",
            "error" if notified != "recovery" else "note",
            text,
            meta={"probe": True, "problems": problems, "notified": notified},
        )
        state = {"signature": signature, "last_notified_ts": now.isoformat(), "suppressed": 0}
    else:
        state = {
            "signature": signature,
            "last_notified_ts": state.get("last_notified_ts"),
            "suppressed": suppressed,
        }
    _atomic_write(state_path(config), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {"notified": notified, "text": text, "signature": signature, "suppressed": suppressed}
