from __future__ import annotations

"""A place of her own to write things: notes, small scripts, half failures.

The point of this module is not the file API -- it is the boundary. She gets a
directory, and a path that would land outside that directory is refused, not
renamed or clamped. That matters because the process runs as the same user that
owns her memory database and her SELF.md: a hole here is not "a file in the wrong
folder", it is a way to edit her own history. So every entry point funnels through
Workspace.resolve, which is the only place that decides what a path means.

The guard has three layers, because a path can lie at three different moments:

- Before joining: absolute paths, "~user", "..", NUL bytes and Windows drive
  letters are rejected outright. These are mistakes or probes; there is nothing
  to resolve.
- After joining: the candidate is resolved with strict=False (so links along
  *missing* suffixes are still followed) and must stay inside the resolved root.
  This is what catches a symlink that points out of the workspace, including one
  that is the final component, and including a link whose target does not exist.
- While walking: info() prunes links that escape and never descends into a
  linked directory, so counting cannot be used to probe the rest of the disk.

Writes are atomic: bytes go to a sibling temporary file which is then renamed
over the target. A half-written note would be worse than a missing one.

Only the standard library is used, and failures are WorkspaceError with a Chinese
message -- those strings are read by her, not by the operator.
"""

import codecs
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["Workspace", "WorkspaceError"]

# "C:" or "C:\..." -- a drive letter is never a meaningful name in her workspace.
_DRIVE_RE = re.compile(r"^[A-Za-z]:")

# The smallest byte budget for read(): enough to decode a useful prefix and to
# detect a NUL byte without loading a multi-gigabyte file.
_MIN_READ_BYTES = 65536

# Prefix for the sibling temporary file used by write(). Dot-prefixed so a
# leftover (there should not be one) is recognisably machinery.
_TEMP_PREFIX = ".yugang-"


class WorkspaceError(RuntimeError):
    """A workspace path was refused, or the target is missing or unusable."""


def _iso_utc(timestamp: float) -> str:
    """Render a POSIX timestamp as an ISO-8601 UTC string with an offset."""
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def _display(label: str) -> str:
    """The relative path as shown to her; the root itself shows as a dot."""
    return label or "."


class Workspace:
    """A directory tree that a path may not leave.

    root is stored absolute and resolved, so later comparisons against it are
    lexical and cannot be fooled by a link in the parent chain. "relative"
    arguments are always taken relative to root; "" and None both mean the root
    itself. Returned "path" values are absolute resolved strings.
    """

    def __init__(self, root: str | Path):
        self.root = Path(os.path.expanduser(os.fspath(root))).resolve()

    # -- path discipline -------------------------------------------------

    def _prepare(self, relative: str | None) -> tuple[str, Path]:
        """Validate a caller-supplied path and return (label, resolved path).

        The label is a normalised workspace-relative POSIX string used in error
        messages; the path is absolute and guaranteed to be inside the root.
        """
        if isinstance(relative, os.PathLike):
            relative = os.fspath(relative)
        if relative is None:
            relative = ""
        if not isinstance(relative, str):
            raise WorkspaceError("路径必须是字符串。")
        if "\x00" in relative:
            raise WorkspaceError("路径里不能有 NUL 字节。")
        if "\\" in relative:
            raise WorkspaceError(f"路径里不能有反斜杠：{relative}")
        if _DRIVE_RE.match(relative):
            raise WorkspaceError(f"路径不能带盘符：{relative}")
        if relative.startswith("/") or relative.startswith("~"):
            raise WorkspaceError(f"只接受工作区内的相对路径：{relative}")

        parts = [part for part in relative.split("/") if part not in ("", ".")]
        for part in parts:
            if part == "..":
                raise WorkspaceError(f"路径里不能有 ..：{relative}")
            if part.startswith("~"):
                raise WorkspaceError(f"路径里不能用 ~：{relative}")
            if _DRIVE_RE.match(part):
                raise WorkspaceError(f"路径不能带盘符：{relative}")

        label = "/".join(parts)
        candidate = self.root.joinpath(*parts) if parts else self.root
        try:
            # strict=False: resolve every existing link, leave the missing tail
            # as-is. A link that points outside is caught on the next line even
            # when its target does not exist.
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise WorkspaceError(f"这个路径解析不了：{_display(label)}") from exc
        if not resolved.is_relative_to(self.root):
            raise WorkspaceError(f"这个路径指到了工作区外面：{_display(label)}")
        return label, resolved

    def _escapes(self, path: Path) -> bool:
        """True when path resolves outside the root, or cannot be resolved."""
        try:
            resolved = path.resolve(strict=False)
        except OSError:
            return True
        return not resolved.is_relative_to(self.root)

    # -- lifecycle -------------------------------------------------------

    def ensure(self) -> Path:
        """Create the root (and its parents) if needed, and return it."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"工作区目录建不出来：{self.root}（{exc.strerror or exc}）") from exc
        if not self.root.is_dir():
            raise WorkspaceError(f"工作区不是目录：{self.root}")
        return self.root

    def resolve(self, relative: str | None = "") -> Path:
        """Turn a workspace-relative path into an absolute path inside the root.

        Raises WorkspaceError for absolute paths, "~", "..", NUL bytes, drive
        letters, and for anything that resolves outside the root (a symlink
        escape is the interesting case).
        """
        return self._prepare(relative)[1]

    # -- inspection ------------------------------------------------------

    def info(self) -> dict[str, Any]:
        """Count regular files and their total bytes under the root.

        Links that point outside are skipped, linked directories are not walked,
        and non-regular files (fifos, sockets, devices) are not counted.
        """
        files = 0
        total = 0
        if self.root.is_dir():
            for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
                base = Path(dirpath)
                # Prune escaping links early: os.walk would still list them, and a
                # link is a directory entry like any other.
                dirnames[:] = [name for name in dirnames if not self._escapes(base / name)]
                for name in filenames:
                    entry = base / name
                    if self._escapes(entry):
                        continue
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    if not stat.S_ISREG(st.st_mode):
                        continue
                    files += 1
                    total += st.st_size
        return {"root": str(self.root), "files": files, "bytes": total}

    def list(self, relative: str = "", *, limit: int = 200) -> dict[str, Any]:
        """List one directory: directories first, then files, names ascending.

        Hidden entries are included; entries whose resolved target lies outside
        the root are not listed at all, so a link cannot be used to advertise
        readable paths. "truncated" says the limit cut the listing. Each entry
        "path" is absolute, and "mtime" is ISO-8601 UTC with an offset.
        """
        label, directory = self._prepare(relative)
        name = _display(label)
        if not directory.exists():
            raise WorkspaceError(f"没有这个目录：{name}")
        if not directory.is_dir():
            raise WorkspaceError(f"这不是一个目录：{name}")

        collected: list[dict[str, Any]] = []
        try:
            with os.scandir(directory) as scan:
                for item in scan:
                    entry = Path(item.path)
                    try:
                        target = entry.resolve(strict=False)
                        if not target.is_relative_to(self.root):
                            continue
                        st = target.stat()
                    except OSError:
                        continue
                    if stat.S_ISDIR(st.st_mode):
                        kind = "dir"
                    elif stat.S_ISREG(st.st_mode):
                        kind = "file"
                    else:
                        continue
                    collected.append(
                        {
                            "path": str(target),
                            "kind": kind,
                            "size": st.st_size,
                            "mtime": _iso_utc(st.st_mtime),
                        }
                    )
        except OSError as exc:
            raise WorkspaceError(f"这个目录读不了：{name}（{exc.strerror or exc}）") from exc

        collected.sort(key=lambda item: (0 if item["kind"] == "dir" else 1, Path(item["path"]).name))
        cap = max(int(limit), 0)
        truncated = len(collected) > cap
        return {"path": str(directory), "entries": collected[:cap], "truncated": truncated}

    def read(self, relative: str, *, max_chars: int = 20000) -> dict[str, Any]:
        """Read a text file without ever loading all of it.

        At most max(max_chars * 4, 65536) bytes are read, plus one byte to tell
        whether the file is longer. Binary detection is a NUL byte or a UTF-8
        decode failure; when truncated at a byte boundary the incomplete trailing
        sequence is ignored rather than counted as corruption. "text" is cut to
        max_chars characters; the caller renders the truncation marker.
        """
        label, path = self._prepare(relative)
        name = _display(label)
        if not path.exists():
            raise WorkspaceError(f"没有这个文件：{name}")
        if not path.is_file():
            raise WorkspaceError(f"这是一个目录，不是文件：{name}")

        cap = max(int(max_chars) * 4, _MIN_READ_BYTES)
        try:
            with path.open("rb") as handle:
                raw = handle.read(cap + 1)
            size = path.stat().st_size
        except OSError as exc:
            raise WorkspaceError(f"这个文件读不了：{name}（{exc.strerror or exc}）") from exc

        truncated = len(raw) > cap
        raw = raw[:cap]
        result: dict[str, Any] = {
            "path": str(path),
            "size": size,
            "text": "",
            "truncated": truncated,
            "binary": False,
        }
        if b"\x00" in raw:
            result["binary"] = True
            return result

        try:
            if truncated:
                # final=False keeps a half-decoded character pending instead of
                # raising, so a clean UTF-8 file cut mid-character is not binary.
                decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
                text = decoder.decode(raw, final=False)
            else:
                text = raw.decode("utf-8")
        except UnicodeDecodeError:
            result["binary"] = True
            return result

        keep = max(int(max_chars), 0)
        if len(text) > keep:
            text = text[:keep]
            truncated = True
        result["text"] = text
        result["truncated"] = truncated
        return result

    # -- mutation --------------------------------------------------------

    def write(self, relative: str, content: str, *, max_bytes: int = 1_000_000) -> dict[str, Any]:
        """Write UTF-8 text atomically, creating parent directories inside the root.

        The temp file lives beside the target so os.replace is a rename on the
        same filesystem; a failure cleans it up and leaves the old file intact.
        """
        if not isinstance(content, str):
            raise WorkspaceError("内容必须是字符串。")
        data = content.encode("utf-8")
        if len(data) > max_bytes:
            raise WorkspaceError(f"内容有 {len(data)} 字节，超过了上限 {max_bytes} 字节。")

        label, path = self._prepare(relative)
        name = _display(label)
        if path.is_dir():
            raise WorkspaceError(f"这是一个目录，不能当文件写：{name}")
        existed = path.exists()
        previous = path.stat().st_size if existed else None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"没能建出上级目录：{name}（{exc.strerror or exc}）") from exc

        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".tmp", dir=str(path.parent))
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(temp_name, path)
            temp_name = None
        except OSError as exc:
            raise WorkspaceError(f"没能写入：{name}（{exc.strerror or exc}）") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

        return {
            "path": str(path),
            "bytes": len(data),
            "created": not existed,
            "previous_bytes": previous,
        }

    def mkdir(self, relative: str) -> dict[str, Any]:
        """Create a directory and its parents; idempotent, reports what it did."""
        label, path = self._prepare(relative)
        name = _display(label)
        if path.exists():
            if path.is_dir():
                return {"path": str(path), "created": False}
            raise WorkspaceError(f"已经有一个同名文件：{name}")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise WorkspaceError(f"没能建出目录：{name}（{exc.strerror or exc}）") from exc
        return {"path": str(path), "created": True}

