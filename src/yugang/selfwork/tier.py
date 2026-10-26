from __future__ import annotations

"""Which of her own changes may go in without a human looking first.

The rule is deliberately dumb, and it is not allowed to be clever: a change is
"auto" only when every path it touches is documentation. Anything that can
change what she is able to do -- src/, tests/, deploy/, pyproject, CI, the
guard rails themselves -- is "review". The applier recomputes this from the
patch rather than trusting meta.json, so a bug here can only make review more
likely, never less: every branch that is not a clean documentation match ends
in "review".

Paths arrive from git, so they are normally "docs/STATUS.md". They are still
treated as untrusted input: an absolute path, a "~", a drive letter, a NUL, a backslash
or any ".." segment is never auto, because the applier will apply the diff to a
different tree and a path that means one thing here could mean another there.
"""

from collections.abc import Sequence

__all__ = ["AUTO_PREFIXES", "classify"]

# Prefixes a change may live in and still go in automatically. "docs/" is a
# directory prefix; "README.md" is exact (a file, not a prefix for README.md.bak).
AUTO_PREFIXES: tuple[str, ...] = ("docs/", "README.md")


def _candidate(raw: object) -> str | None:
    """Normalise one changed path, or None when it is not a plain relative path."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or "\x00" in text:
        return None
    if "\\" in text:
        return None
    if text.startswith("/") or text.startswith("~"):
        return None
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if not parts:
        return None
    if any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _is_auto(path: str) -> bool:
    for prefix in AUTO_PREFIXES:
        if prefix.endswith("/"):
            base = prefix[:-1]
            if path == base or path.startswith(prefix):
                return True
        elif path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def classify(paths: Sequence[str]) -> str:
    """Return "auto" when every path is documentation, else "review".

    An empty list is "review": a proposal that changes nothing is not something
    an auto-merge should wave through, and "no files" usually means the caller
    made a mistake rather than that the change is harmless.
    """
    if not paths:
        return "review"
    seen = False
    for raw in paths:
        name = _candidate(raw)
        if name is None or not _is_auto(name):
            return "review"
        seen = True
    return "auto" if seen else "review"
