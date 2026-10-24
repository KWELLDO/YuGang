from __future__ import annotations

import secrets
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat(timespec="microseconds")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def new_id(prefix: str) -> str:
    stamp = utcnow().strftime("%Y%m%dT%H%M%S%f")
    return f"{prefix}_{stamp}_{secrets.token_hex(3)}"


def local_iso(ts: str, tz: str = "Asia/Shanghai") -> str:
    """Render a stored UTC timestamp in her local time, with an explicit offset.

    Prompts must not mix "now" in local time with raw UTC event timestamps: a model
    will otherwise subtract the wrong clocks and report a gap that never happened.
    """
    try:
        return parse_ts(ts).astimezone(ZoneInfo(tz)).isoformat(timespec="seconds")
    except Exception:
        return ts


def day_key(dt: datetime, tz: str = "Asia/Shanghai") -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%Y-%m-%d")


def humanize_gap(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{int(seconds)} 秒"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)} 分钟"
    hours = minutes / 60
    if hours < 48:
        return f"{hours:.1f} 小时"
    return f"{hours / 24:.1f} 天"
