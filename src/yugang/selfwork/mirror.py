from __future__ import annotations

"""Her own copy of her source, as a git repository she may write to.

The live tree at /home/dafeiyu/yugang belongs to root, and this module never
touches it. What she gets instead is a mirror: a plain git repository under
~/.yugang/self, built once from an explicit include list so that the things
that are not source -- audio/, caches, the memory database -- never end up in
her copy. From there she can read, write and commit, and a proposal is simply
the diff between two of her own commits.

Why git, and not just a directory of files:

- a commit is the smallest rollback point she can name, and the applier takes
  the proposal id and the diff, not a copy of her filesystem;
- "what changed since the last proposal" becomes a question with one answer --
  git diff -- instead of a comparison someone has to remember how to do;
- the history stays hers. Later she can read her own reasoning in it.

The guard is the same shape as world/workspace.py, for the same reason: the
process runs as the user who owns her memory database, so a path that escapes
the mirror is not "a file in the wrong folder". Paths are rejected before
joining (absolute, "~", "..", NUL, backslash, drive letter), the joined
candidate is resolved and must stay inside the root (which catches symlinks),
and .git itself is off limits: letting her write .git/config would be a way to
run commands, not a way to edit her source.

Only the standard library is used. git is invoked with -C and an identity
pinned on the command line, so no global git configuration is required. Every
git failure becomes MirrorError carrying the first 500 characters of stderr,
because those strings are read by her.
"""

import argparse
import codecs
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "DEFAULT_INCLUDES",
    "MIRROR_NOT_READY",
    "Mirror",
    "MirrorError",
    "SelfWorkPaths",
    "atomic_write_text",
]

# What the mirror is made of. Deliberately an allow list: audio/, the memory
# database and everything else stay out, so her copy is small and every file
# in it is something a human could review.
DEFAULT_INCLUDES = (
    "src",
    "tests",
    "docs",
    "deploy",
    "pyproject.toml",
    "README.md",
    "LICENSE",
    ".github",
    ".gitignore",
)

MIRROR_NOT_READY = "我的镜像还没建好（需要操作员先跑 deploy/self-lane.sh）"

# Things that are never worth copying even inside an included directory.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "node_modules",
        "audio",
    }
)
_SKIP_SUFFIXES = (".pyc", ".pyo", ".sqlite", ".sqlite3", ".log")

_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")

_GIT_TIMEOUT = 30.0


class MirrorError(RuntimeError):
    """Something about the mirror did not work. The message is read by her."""


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _iso_ts(value: str) -> str:
    """Normalise a git timestamp into +00:00 ISO, so all times look the same."""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def _ignore_entries(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        if name in _SKIP_DIRS or name.endswith(_SKIP_SUFFIXES):
            ignored.add(name)
    return ignored


def _count_files(root: Path) -> int:
    total = 0
    for _dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        total += len(filenames)
    return total


def _atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    """Write through a temp file in the same directory, then replace.

    Not because a crash here is likely, but because a half-written
    self-state.json makes the next self_propose read garbage, and a
    half-written source file is a commit she cannot explain.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    handle_fd, tmp = tempfile.mkstemp(prefix=".yugang-", suffix=".tmp", dir=str(parent))
    try:
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, *, mode: int = 0o644) -> None:
    _atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def _git_env() -> dict[str, str]:
    """A small, predictable environment for git: no prompts, no inherited GIT_*."""
    env = dict(os.environ)
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_CEILING_DIRECTORIES",
    ):
        env.pop(key, None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_CONFIG_NOSYSTEM", "1")
    env.setdefault("LC_ALL", "C")
    return env


@dataclass(frozen=True)
class SelfWorkPaths:
    """Where her side of the lane lives. The directories are all derived.

    Passed around as one object so cli.py, service.py and the toolbox cannot
    disagree about where proposals go. source_root is the live tree the first
    mirror is copied from, and is only read -- never written.
    """

    home: Path
    source_root: Path
    venv_python: str = "/opt/yugang-venv/bin/python"
    max_file_bytes: int = 200_000
    test_timeout_seconds: float = 600.0

    @property
    def mirror_root(self) -> Path:
        return Path(self.home) / "self"

    @property
    def proposals_dir(self) -> Path:
        return Path(self.home) / "proposals"

    @property
    def results_dir(self) -> Path:
        return Path(self.home) / "apply-results"

    @property
    def state_file(self) -> Path:
        return Path(self.home) / "self-state.json"


class Mirror:
    """A git repository of her source that only she writes to."""

    def __init__(
        self,
        root: Path,
        *,
        source: Path | None = None,
        includes: Sequence[str] = DEFAULT_INCLUDES,
        max_file_bytes: int = 200_000,
    ) -> None:
        self.root = Path(root)
        self.source = Path(source) if source is not None else None
        self.includes = tuple(str(name) for name in includes)
        self.max_file_bytes = max(1, int(max_file_bytes))

    # -- paths ---------------------------------------------------------

    def _root(self) -> Path:
        return self.root.expanduser().resolve(strict=False)

    def resolve(self, rel: str | None) -> Path:
        """Turn a path she typed into an absolute path inside the mirror.

        Rejects everything world/workspace.py rejects, and additionally keeps
        .git/ out of reach, because a writable .git is a command runner.
        """
        raw = "" if rel is None else str(rel)
        if "\x00" in raw:
            raise MirrorError("路径里有 NUL，不能用来定位文件。")
        text = raw.strip()
        if not text:
            return self._root()
        if text.startswith("~"):
            raise MirrorError(f"路径不能以 ~ 开头：{raw}")
        if text.startswith("/") or _DRIVE_RE.match(text):
            raise MirrorError(f"路径必须相对镜像根目录：{raw}")
        if "\\" in text:
            raise MirrorError(f"路径里不能用反斜杠：{raw}")
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if any(part == ".." for part in parts):
            raise MirrorError(f"路径不能跳出我的镜像：{raw}")
        if parts and parts[0] == ".git":
            raise MirrorError("我的镜像里的 .git/ 不归我改。")
        root = self._root()
        candidate = root
        for part in parts:
            candidate = candidate / part
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(root):
            raise MirrorError(f"路径跳出了我的镜像：{raw}")
        return resolved

    def _relative(self, path: Path) -> str:
        return path.relative_to(self._root()).as_posix()

    def _entry(self, path: Path) -> dict[str, Any] | None:
        target = path.resolve(strict=False)
        if not target.is_relative_to(self._root()):
            return None
        name = self._relative(target)
        if target.is_dir():
            return {"path": name, "kind": "dir", "bytes": 0}
        if target.is_file():
            try:
                size = int(target.stat().st_size)
            except OSError:
                return None
            return {"path": name, "kind": "file", "bytes": size}
        return None

    def list(self, rel: str = "", max_entries: int = 200) -> list[dict[str, Any]]:
        limit = _clamp_int(max_entries, 200, 1, 1000)
        base = self.resolve(rel)
        if not base.exists():
            raise MirrorError(f"镜像里没有这个路径：{rel}")
        if base.is_file():
            entry = self._entry(base)
            return [entry] if entry is not None else []
        dirs: list[dict[str, Any]] = []
        files: list[dict[str, Any]] = []
        try:
            names = sorted(os.listdir(base))
        except OSError as exc:
            raise MirrorError(f"这个目录读不了：{rel}（{exc.strerror or exc}）") from exc
        for name in names:
            if len(dirs) + len(files) >= limit:
                break
            if name == ".git":
                continue
            entry = self._entry(base / name)
            if entry is None:
                continue
            if entry["kind"] == "dir":
                dirs.append(entry)
            else:
                files.append(entry)
        return (dirs + files)[:limit]

    def read(self, rel: str, max_chars: int = 20000) -> dict[str, Any]:
        limit = _clamp_int(max_chars, 20000, 1, 200_000)
        target = self.resolve(rel)
        if not target.exists():
            raise MirrorError(f"镜像里没有这个文件：{rel}")
        if not target.is_file():
            raise MirrorError(f"这是一个目录，不是文件：{rel}")
        # Read a little more than the caller asked for, so truncation is
        # decided on characters while the read stays one bounded call.
        read_bytes = max(limit * 4, 65536) + 1
        try:
            with target.open("rb") as handle:
                raw = handle.read(read_bytes)
        except OSError as exc:
            raise MirrorError(f"这个文件读不了：{rel}（{exc.strerror or exc}）") from exc
        if b"\x00" in raw:
            raise MirrorError(f"这是二进制文件，我读不了：{rel}")
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        try:
            text = decoder.decode(raw, final=False)
        except UnicodeDecodeError as exc:
            raise MirrorError(f"不是 UTF-8 文本，我读不了：{rel}") from exc
        truncated = len(text) > limit
        if truncated:
            text = text[:limit]
        return {
            "path": self._relative(target),
            "chars": len(text),
            "truncated": truncated,
            "text": text,
        }

    def write(self, rel: str, content: str, message: str) -> dict[str, Any]:
        """Write one UTF-8 text file inside the mirror and commit just it."""
        if not self._initialized():
            raise MirrorError(MIRROR_NOT_READY)
        if not isinstance(content, str):
            raise MirrorError("内容只能是文本，我还没学会写别的。")
        payload = content.encode("utf-8")
        if len(payload) > self.max_file_bytes:
            raise MirrorError(
                f"文件太大了：{len(payload)} 字节，上限是 {self.max_file_bytes} 字节。"
            )
        target = self.resolve(rel)
        if target == self._root():
            raise MirrorError("要给一个具体的文件路径，不能是整个镜像根目录。")
        if target.is_dir():
            raise MirrorError(f"这是一个目录，不是文件：{rel}")
        title = " ".join(str(message or "").split())
        if not title:
            raise MirrorError("提交信息不能为空，我得知道这次改的是什么。")
        name = self._relative(target)
        previous = target.read_bytes() if target.is_file() else None
        if previous == payload:
            raise MirrorError("内容没有变化，没有产生提交")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.parent.resolve(strict=False).is_relative_to(self._root()):
            raise MirrorError(f"路径跳出了我的镜像：{rel}")
        mode = 0o644
        if previous is not None:
            try:
                mode = target.stat().st_mode & 0o777 or 0o644
            except OSError:
                mode = 0o644
        atomic_write_text(target, content, mode=mode)
        try:
            # Only this one path is staged, so a file she edited earlier but
            # never committed cannot ride along in this commit by accident.
            self._git("add", "--", name)
            self._git("commit", "--quiet", "-m", title)
        except MirrorError:
            # Put the file back: a failed commit must not leave the working
            # tree different from the last commit, or the next diff lies.
            if previous is None:
                try:
                    target.unlink()
                except OSError:
                    pass
            else:
                _atomic_write_bytes(target, previous)
            raise
        commit = self.head()
        if commit is None:
            raise MirrorError("提交没有成功。")
        return {
            "ok": True,
            "commit": commit,
            "short": commit[:7],
            "path": name,
            "bytes": len(payload),
        }

    # -- git -----------------------------------------------------------

    def _run_git(self, args: Sequence[str], *, timeout: float = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
        cmd = [
            "git",
            "-C",
            str(self._root()),
            "-c",
            "user.name=yugang",
            "-c",
            "user.email=yugang@localhost",
            *args,
        ]
        try:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_git_env(),
            )
        except FileNotFoundError as exc:
            raise MirrorError("这台机器上没有 git，我这个能力用不了。") from exc
        except subprocess.TimeoutExpired as exc:
            raise MirrorError(f"git 太慢了，等了 {int(timeout)} 秒还没好。") from exc

    def _git(self, *args: str, timeout: float = _GIT_TIMEOUT) -> subprocess.CompletedProcess:
        proc = self._run_git(args, timeout=timeout)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise MirrorError(f"git 出错了：{detail[:500]}")
        return proc

    def _git_quiet(self, *args: str) -> tuple[bool, str]:
        """Run a git probe; a missing repo or a failure is (False, ""), not an error."""
        if not (self._root() / ".git").is_dir():
            return (False, "")
        proc = self._run_git(args)
        return (proc.returncode == 0, proc.stdout or "")

    def _initialized(self) -> bool:
        return (self._root() / ".git").is_dir() and self.head() is not None

    def head(self) -> str | None:
        ok, out = self._git_quiet("rev-parse", "--verify", "--quiet", "HEAD")
        value = out.strip()
        return value if ok and value else None

    def first_commit(self) -> str | None:
        ok, out = self._git_quiet("rev-list", "--max-parents=0", "HEAD")
        if not ok:
            return None
        roots = [line.strip() for line in out.splitlines() if line.strip()]
        # rev-list is newest first, so the oldest root is the last line.
        return roots[-1] if roots else None

    def exists(self, commit: str) -> bool:
        name = str(commit or "").strip()
        if not _SHA_RE.match(name):
            return False
        ok, _ = self._git_quiet("cat-file", "-e", f"{name}^{{commit}}")
        return ok

    def diff(self, base: str) -> str:
        if not self.exists(base):
            raise MirrorError(f"我的镜像里没有这个提交：{base}")
        proc = self._git("diff", "--no-color", "--no-ext-diff", "--text", f"{base}..HEAD")
        return proc.stdout

    def changed_files(self, base: str) -> list[str]:
        if not self.exists(base):
            raise MirrorError(f"我的镜像里没有这个提交：{base}")
        proc = self._git("diff", "--name-only", f"{base}..HEAD")
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    def log(self, limit: int = 10) -> list[dict[str, Any]]:
        if not self._initialized():
            return []
        count = _clamp_int(limit, 10, 1, 100)
        proc = self._git("log", f"-{count}", "--format=%H%x1f%h%x1f%aI%x1f%s", "HEAD")
        entries: list[dict[str, Any]] = []
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\x1f")
            if len(parts) < 4:
                continue
            entries.append(
                {
                    "commit": parts[0],
                    "short": parts[1],
                    "ts": _iso_ts(parts[2]),
                    "message": "\x1f".join(parts[3:]),
                }
            )
        return entries

    def status(self) -> dict[str, Any]:
        root = self._root()
        if not self._initialized():
            return {
                "ok": True,
                "initialized": False,
                "root": str(root),
                "head": None,
                "dirty": False,
                "commits": 0,
                "branch": "main",
            }
        _, branch_out = self._git_quiet("rev-parse", "--abbrev-ref", "HEAD")
        _, porcelain = self._git_quiet("status", "--porcelain", "--untracked-files=normal")
        _, counts = self._git_quiet("rev-list", "--count", "HEAD")
        branch = branch_out.strip() or "main"
        commits_text = counts.strip()
        commits = int(commits_text) if commits_text.isdigit() else 0
        return {
            "ok": True,
            "initialized": True,
            "root": str(root),
            "head": self.head(),
            "dirty": bool(porcelain.strip()),
            "commits": commits,
            "branch": branch,
        }

    # -- building ------------------------------------------------------

    def _own_writable(self, root: Path) -> None:
        """Make the tree writable by her.

        live is 0444 for everyone (that is the whole point), and copying preserves
        the mode — so after the first sync every mirror file is read-only, and the
        next sync dies with PermissionError on the very files it was following.
        """
        for path in [root, *root.rglob("*")]:
            try:
                if path.is_symlink():
                    continue
                if path.is_dir():
                    path.chmod(0o755)
                elif path.is_file():
                    path.chmod(0o644)
            except OSError:  # pragma: no cover - 竞态或权限，读不到就算了
                pass

    def _copy_includes(self, source: Path) -> int:
        root = self._root()
        self._own_writable(root)
        copied = 0
        for name in self.includes:
            src = source / name
            if not src.exists():
                continue
            dest = root / name
            if src.is_dir():
                shutil.copytree(
                    src,
                    dest,
                    symlinks=True,
                    ignore=_ignore_entries,
                    dirs_exist_ok=True,
                )
                copied += _count_files(dest)
            elif src.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                copied += 1
        self._own_writable(root)
        return copied

    def ensure(self) -> dict[str, Any]:
        """Build the mirror if it is not there yet; never rewrite an existing one."""
        if self._initialized():
            return {"created": False, "head": self.head()}
        root = self._root()
        if self.source is None:
            raise MirrorError("没有来源目录，我建不出镜像。")
        source = self.source.expanduser().resolve(strict=False)
        if source == root:
            raise MirrorError("来源目录和镜像目录是同一个，不能这样拷。")
        if not source.is_dir():
            raise MirrorError(f"来源目录不存在：{source}")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MirrorError(f"镜像目录建不出来：{root}（{exc.strerror or exc}）") from exc
        files = self._copy_includes(source)
        if files <= 0:
            raise MirrorError(f"来源目录里没有可拷贝的内容：{source}")
        self._git("init", "--quiet", "--initial-branch=main")
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", f"从 live 建起镜像（{files} 个文件）")
        head = self.head()
        if head is None:
            raise MirrorError("镜像建起来了，但第一个提交没有成功。")
        return {"created": True, "head": head, "files": files}


    def sync(self) -> dict[str, Any]:
        """Catch the mirror up to the live tree, as one commit on top.

        After every deploy the mirror is behind. Left alone, that means she
        tests code nobody runs any more, and her patches are cut against a base
        that already shipped — `git apply --check` then fails for reasons that
        have nothing to do with her change. Her own commits stay in the history;
        this only adds one, and the message says it was a catch-up, not her edit.
        """
        if not self._initialized():
            built = self.ensure()
            return {"ok": True, "synced": True, "created": True, "head": built.get("head")}
        if self.source is None:
            raise MirrorError("没有来源目录，我同步不了。")
        source = self.source.expanduser().resolve(strict=False)
        root = self._root()
        if source == root:
            raise MirrorError("来源目录和镜像目录是同一个，不能这样拷。")
        if not source.is_dir():
            raise MirrorError(f"来源目录不存在：{source}")
        before = self.head()
        # Anything she left uncommitted is hers: keep it under its own message
        # rather than folding it into an operator catch-up commit.
        if self._dirty():
            self._git("add", "--all")
            self._git("commit", "--quiet", "-m", "同步前先把她没提交的改动存下来")
        self._copy_includes(source)
        self._prune_missing(source)
        if not self._dirty():
            return {"ok": True, "synced": False, "head": before, "note": "镜像已经和 live 一样"}
        self._git("add", "--all")
        self._git("commit", "--quiet", "-m", "同步：把镜像跟到 live 上（操作员动作，不是她的改动）")
        head = self.head()
        changed = self._git_quiet("diff", "--name-only", f"{before}..{head}")[1].split()
        return {"ok": True, "synced": True, "head": head, "changed": len(changed), "files": changed[:50]}

    def _dirty(self) -> bool:
        """True when the worktree has edits git would commit.

        _git_quiet returns (ok, stdout), not "did it print anything" — reading it
        as a dirty flag is how an empty worktree ended up asking git to commit
        nothing, which exits 1 and looked like a broken mirror.
        """
        ok, out = self._git_quiet("status", "--porcelain")
        return ok and bool(out.strip())

    _FROM_LIVE_PREFIXES = ("同步：", "从 live 建起镜像")

    def _prune_missing(self, source: Path) -> int:
        """Delete mirror files the live tree no longer has — but only those from live.

        A blind prune is data loss: files she wrote and committed live only in her
        mirror, so deleting everything live lacks erases her work. The history says
        where a file came from — a catch-up commit or the initial copy — so only
        those are removed. What she added or edited stays, even if live never had it.
        """
        root = self._root()
        removed = 0
        for name in self.includes:
            src = source / name
            dest = root / name
            if not dest.exists():
                continue
            if src.is_dir() and dest.is_dir():
                for path in sorted(dest.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    rel = path.relative_to(dest)
                    if path.is_dir():
                        try:
                            path.rmdir()
                        except OSError:
                            pass
                        continue
                    if (src / rel).exists():
                        continue
                    if self._came_from_live(path):
                        path.unlink()
                        removed += 1
            elif dest.is_file() and not src.exists() and self._came_from_live(dest):
                dest.unlink()
                removed += 1
        return removed

    def _came_from_live(self, path: Path) -> bool:
        """Was this path last written by a catch-up copy rather than by her?"""
        rel = self._relative(path)
        _ok, subject = self._git_quiet("log", "-1", "--format=%s", "--", rel)
        text = subject.strip()
        return bool(text) and text.startswith(self._FROM_LIVE_PREFIXES)


def _guess_source() -> Path:
    """The repository this code is installed from -- the deploy-time default."""
    return Path(__file__).resolve().parents[3]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m yugang.selfwork.mirror",
        description="她自己的镜像：建起来，或者看一眼状态。",
    )
    parser.add_argument(
        "--root",
        default=str(Path.home() / ".yugang" / "self"),
        help="镜像目录，默认 ~/.yugang/self",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="从哪份 live 拷，默认 /home/dafeiyu/yugang；不存在时用这份代码所在的仓库",
    )
    parser.add_argument("--init", action="store_true", help="建镜像（已经建好就不动）")
    parser.add_argument("--status", action="store_true", help="看镜像状态")
    args = parser.parse_args(argv)
    source = Path(args.source) if args.source else _guess_source()
    mirror = Mirror(args.root, source=source)
    try:
        if args.init:
            payload = mirror.ensure()
        else:
            payload = mirror.status()
    except MirrorError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
