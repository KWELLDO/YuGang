from __future__ import annotations

"""Channel visibility for memory recall.

A shared word must never be enough to carry one conversation into another. The
failure this guards against is concrete: a direct message and a public post both
contain 「字幕」, recall returns the private passage, and her public answer quotes
it. Recall therefore takes an explicit scope; when the answer is going out in
public, anything that arrived on a private channel is filtered out in SQL, not
filtered afterwards by eyeballing the prompt.
"""

from typing import Any

# Venues where an answer is readable by people who were not in the conversation.
PUBLIC_CHANNEL_PREFIXES = (
    "public",
    "bili",
    "youtube",
    "douyin",
    "weibo",
    "xhs",
    "twitter",
    "mastodon",
)

# Venues that carry somebody's private words. Anything that is neither public
# nor empty is treated as private, so a new channel name defaults to the safe
# side instead of leaking by default.
KNOWN_PRIVATE_CHANNELS = (
    "email",
    "onebot",
    "qq",
    "cli",
    "http",
    "web",
    "dm",
    "private",
    "console",
    "test",
)


def normalize_channel(channel: Any) -> str:
    return str(channel or "").strip().lower()


def _matches_public(name: str) -> bool:
    for prefix in PUBLIC_CHANNEL_PREFIXES:
        if name == prefix or name.startswith(prefix + ":") or name.startswith(prefix + "/"):
            return True
    return False


def is_public_channel(channel: Any) -> bool:
    name = normalize_channel(channel)
    return bool(name) and _matches_public(name)


def is_private_channel(channel: Any) -> bool:
    name = normalize_channel(channel)
    return bool(name) and not _matches_public(name)


def visibility_clause(column: str) -> tuple[str, list[Any]]:
    """SQL fragment keeping only material that may be shown in public.

    Events with no channel are her own mind (diary, judgments, self versions),
    not somebody else's private message, so they stay reachable. Events that
    arrived on a private channel do not. The cost of this asymmetry is the right
    one: a missing channel can only hide a leak that never happened, while the
    opposite default would publish one.
    """
    params: list[Any] = list(PUBLIC_CHANNEL_PREFIXES)
    marks = ",".join("?" for _ in PUBLIC_CHANNEL_PREFIXES)
    parts = [f"{column} IN ({marks})"]
    for separator in (":", "/"):
        parts.append(" OR ".join(f"{column} LIKE ?" for _ in PUBLIC_CHANNEL_PREFIXES))
        params.extend(f"{prefix}{separator}%" for prefix in PUBLIC_CHANNEL_PREFIXES)
    clause = f" AND ({column} IS NULL OR {column} = '' OR " + " OR ".join(parts) + ")"
    return clause, params
