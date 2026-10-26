from __future__ import annotations

"""The six tools she uses to change her own source code.

These are ordinary tools in the same shape as world/tools.py: a JSON schema
each, a handler each, and every call written down. What is different is the
subject -- instead of the world, they act on her own source tree, through the
mirror in selfwork/mirror.py.

The names all start with source_ on purpose. self_read and self_history mean
"read SELF.md" and "SELF.md versions" and she already uses those words; the
mirror is her source, so the tools say source.

Rules that hold for all of them:

- she can only touch files inside her mirror; there is no argument that names
  a path outside it, and Mirror.resolve refuses the shapes that try;
- only UTF-8 text, one file, at most 200 KB per commit;
- a write is one commit, which is the unit the applier later applies;
- every action is also written to L1, because "I changed my own source" is
  something she will want to remember without reading git.

self_test runs pytest inside the ordinary sandbox (no network, resource
limits) against the mirror, and records which commit was tested. A proposal
carries that record with it, so the applier can tell "tested this exact
commit" from "tested something earlier". If the venv is missing the answer is
an error, not a pass -- a test that did not run is not a test that passed.
"""

import json
import os
import re
import time
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..ids import day_key, utcnow
from .mirror import MIRROR_NOT_READY, Mirror, MirrorError, SelfWorkPaths, atomic_write_text
from .tier import classify

__all__ = ["NAMES", "SOURCE_TOOL_SCHEMAS", "SelfToolbox"]

SOURCE_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "source_list",
            "description": (
                "列出我自己那份源码镜像里有哪些文件（只读）。路径沿用镜像里的相对路径，"
                "空表示根目录；目录排在文件前面。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对镜像根目录的路径，空表示根目录",
                    },
                    "max": {
                        "type": "integer",
                        "description": "最多返回多少条，默认 200，上限 1000",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_read",
            "description": (
                "读我自己那份源码镜像里的一个文件（只读，只收 UTF-8 文本）。"
                "超出 max_chars 的部分会被截断，truncated 会告诉我。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对镜像根目录的文件路径",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "最多返回多少字符，默认 20000",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_write",
            "description": (
                "改我自己那份源码镜像里的一个文本文件，改完就是一个提交。"
                "只支持 UTF-8 文本、单个文件、不超过 200KB；内容为空表示清空这个文件。"
                "路径写错或者内容没变都会失败，不会悄悄什么都不做。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对镜像根目录的文件路径，例如 docs/STATUS.md",
                    },
                    "content": {
                        "type": "string",
                        "description": "新的完整内容（不是补丁），空字符串表示清空文件",
                    },
                    "message": {
                        "type": "string",
                        "description": "这次提交的一句话说明，会出现在我自己的 git 历史里",
                    },
                },
                "required": ["path", "content", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_log",
            "description": "看我自己那份源码镜像最近的提交（只读）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "最多看多少条，默认 10",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_test",
            "description": (
                "在无网络的沙箱里对我的源码镜像跑一遍 pytest（会记录测的是哪个提交）。"
                "这个操作可能要好几分钟；失败了会告诉我失败的是哪几个测试。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "最多跑多久，默认 600 秒，不会超过配置里的上限",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_propose",
            "description": (
                "把上一次合入以来的改动打成一个提案，交给操作员那一侧决定要不要合。"
                "summary 和 reason 是写给人看的：改了什么，以及为什么。"
                "文档类改动标 auto，碰代码和配置的标 review；没有新改动就拿不出提案。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "这次改了什么，一句话",
                    },
                    "reason": {
                        "type": "string",
                        "description": "为什么要这样改",
                    },
                },
                "required": ["summary", "reason"],
            },
        },
    },
)

NAMES: tuple[str, ...] = tuple(schema["function"]["name"] for schema in SOURCE_TOOL_SCHEMAS)

_READ_TOOLS = frozenset({"source_list", "source_read", "source_log"})

_COUNT_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|error|errors|xfailed|xpassed|deselected|warnings?)\b")

_DEFAULT_TEST_TIMEOUT = 600


def _clip_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _args(arguments: object) -> dict[str, Any]:
    """Accept a dict, None, or the JSON string some callers hand over."""
    if arguments is None:
        return {}
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str):
        text = arguments.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MirrorError(f"参数不是合法的 JSON：{exc}") from exc
        if isinstance(data, dict):
            return data
        raise MirrorError("参数应该是一个对象。")
    raise MirrorError("参数看不懂。")


def _field(result: object, name: str, default: Any = None) -> Any:
    if isinstance(result, Mapping):
        return result.get(name, default)
    return getattr(result, name, default)


def _parse_counts(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for match in _COUNT_RE.finditer(output or ""):
        number = int(match.group(1))
        word = match.group(2)
        if word in ("error", "errors"):
            counts["errors"] += number
        elif word in counts:
            counts[word] += number
    return counts


def _last_line(text: str) -> str:
    for line in reversed(str(text or "").splitlines()):
        line = line.strip()
        if line:
            return line
    return ""


def _parse_ts(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


class SelfToolbox:
    """Six tools, all acting on her mirror and nowhere else."""

    def __init__(
        self,
        paths: SelfWorkPaths,
        store: Any = None,
        *,
        mirror: Mirror | None = None,
        sandbox_runner: Any = None,
        now_fn: Any = utcnow,
    ) -> None:
        self.paths = paths
        self.store = store
        self._mirror = mirror
        self._sandbox_runner = sandbox_runner
        self.now_fn = now_fn
        self._handlers_map = {
            "source_list": self._source_list,
            "source_read": self._source_read,
            "source_write": self._source_write,
            "source_log": self._source_log,
            "source_test": self._source_test,
            "source_propose": self._source_propose,
        }

    # -- the toolbox protocol world/tools.py and memory/tools.py speak --

    @property
    def mirror(self) -> Mirror:
        if self._mirror is None:
            self._mirror = Mirror(
                self.paths.mirror_root,
                source=self.paths.source_root,
                max_file_bytes=int(getattr(self.paths, "max_file_bytes", 200_000)),
            )
        return self._mirror

    def schemas(self) -> tuple[dict[str, Any], ...]:
        return SOURCE_TOOL_SCHEMAS

    def names(self) -> set[str]:
        return set(NAMES)

    def run(self, name: str, arguments: object = None) -> dict[str, Any]:
        handler = self._handlers_map.get(name)
        if handler is None:
            return {"ok": False, "tool": name, "error": f"没有这个工具：{name}"}
        started = time.perf_counter()
        try:
            result = handler(_args(arguments))
        except MirrorError as exc:
            result = {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - 她调的工具，不把异常抛给她
            result = {"ok": False, "error": f"工具出错了：{exc}"}
        if not isinstance(result, dict):
            result = {"ok": False, "error": "工具返回了看不懂的东西。"}
        result.setdefault("ok", True)
        result.setdefault("tool", name)
        self._ledger(name, result, started)
        return result

    # -- bookkeeping ----------------------------------------------------

    def _ledger(self, name: str, result: dict[str, Any], started: float) -> None:
        if self.store is None:
            return
        elapsed = max(0, int((time.perf_counter() - started) * 1000))
        try:
            self.store.record_world_call(
                day_key(self.now_fn()),
                name,
                kind="read" if name in _READ_TOOLS else "act",
                ok=bool(result.get("ok")),
                detail=_detail(result),
                chars=_chars(result),
                ms=elapsed,
            )
        except Exception:  # noqa: BLE001 - 记账失败不能影响她做事
            pass

    def _remember_act(self, content: str, meta: dict[str, Any]) -> bool:
        if self.store is None:
            return False
        try:
            self.store.append_event("ai-observation", "action", content, meta=meta)
        except Exception:  # noqa: BLE001
            return False
        return True

    def _ready_mirror(self) -> Mirror:
        mirror = self.mirror
        try:
            ready = bool(mirror.status().get("initialized"))
        except MirrorError:
            ready = False
        if not ready:
            raise MirrorError(MIRROR_NOT_READY)
        return mirror

    def _write_state(self, payload: dict[str, Any]) -> None:
        try:
            home = Path(self.paths.home)
            home.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                self.paths.state_file,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                mode=0o600,
            )
        except OSError:
            # 状态写不下来不该让这次测试结果消失；下一次 self_propose 会
            # 看到旧的 state，从而认为这个提交没测过，这是安全的方向。
            pass

    # -- handlers -------------------------------------------------------

    def _source_list(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        rel = str(args.get("path") or "")
        limit = _clip_int(args.get("max"), 200, 1, 1000)
        entries = mirror.list(rel, max_entries=limit)
        return {"ok": True, "path": rel, "entries": entries, "count": len(entries)}

    def _source_read(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        rel = str(args.get("path") or "").strip()
        if not rel:
            raise MirrorError("要看哪个文件？给我一个路径。")
        max_chars = _clip_int(args.get("max_chars"), 20000, 1, 200_000)
        data = mirror.read(rel, max_chars=max_chars)
        return {
            "ok": True,
            "path": data["path"],
            "text": data["text"],
            "chars": data["chars"],
            "truncated": data["truncated"],
        }

    def _source_log(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        limit = _clip_int(args.get("limit"), 10, 1, 100)
        entries = mirror.log(limit)
        return {"ok": True, "entries": entries, "count": len(entries)}

    def _source_write(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        rel = str(args.get("path") or "").strip()
        content = args.get("content")
        message = str(args.get("message") or "").strip()
        if not rel:
            raise MirrorError("要改哪个文件？给我一个路径。")
        if not message:
            raise MirrorError("提交信息不能为空，我得知道这次改的是什么。")
        if not isinstance(content, str):
            raise MirrorError("内容只能是文本，我还没学会写别的。")
        limit = int(getattr(self.paths, "max_file_bytes", 200_000) or 200_000)
        size = len(content.encode("utf-8"))
        if size > limit:
            raise MirrorError(f"文件太大了：{size} 字节，上限是 {limit} 字节。")
        result = mirror.write(rel, content, message)
        l1 = self._remember_act(
            f"我改了自己的 {result['path']}：{message}（{result['short']}）",
            {
                "area": "self-change",
                "tool": "source_write",
                "path": result["path"],
                "commit": result["commit"],
                "short": result["short"],
                "message": message,
            },
        )
        return {
            "ok": True,
            "commit": result["commit"],
            "short": result["short"],
            "path": result["path"],
            "bytes": result["bytes"],
            "l1": l1,
        }

    def _source_test(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        python = str(getattr(self.paths, "venv_python", "") or "")
        if not python or not Path(python).is_file():
            return {"ok": False, "error": "测试环境还没装好（/opt/yugang-venv）"}
        ceiling = float(getattr(self.paths, "test_timeout_seconds", 600.0) or 600.0)
        requested = _clip_int(args.get("timeout_seconds"), _DEFAULT_TEST_TIMEOUT, 1, 3600)
        timeout = min(float(requested), ceiling)

        runner = self._sandbox_runner
        if runner is None:
            # 延迟导入：测试里注入假沙箱时，这台机器上没有 bwrap 也能走完
            # 这一整条路径。
            from ..world.sandbox import run as runner
        from ..world.sandbox import SandboxLimits

        commit = mirror.head()
        # -o addopts="" so the summary line survives: pyproject already passes -q,
        # and -qq hides "496 passed, 2 skipped", which is the only part worth
        # reading and the only part she can quote back to herself.
        command = f"{python} -m pytest -x -q -p no:cacheprovider -o addopts="
        started = time.perf_counter()
        result = runner(
            command,
            workdir=str(mirror.root),
            limits=SandboxLimits(timeout_seconds=timeout),
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            backend="auto",
        )
        elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))

        exit_code = _field(result, "exit_code")
        timed_out = bool(_field(result, "timed_out", False))
        backend = str(_field(result, "backend", "") or "")
        duration_ms = _field(result, "duration_ms", None)
        if not isinstance(duration_ms, int) or duration_ms < 0:
            duration_ms = elapsed_ms
        output = "\n".join(
            part for part in (str(_field(result, "stdout", "") or ""), str(_field(result, "stderr", "") or "")) if part
        )
        counts = _parse_counts(output)
        summary = _last_line(output)
        failed_tests: list[str] = []
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("FAILED "):
                name = stripped[len("FAILED "):].strip()
                if name and name not in failed_tests:
                    failed_tests.append(name)
        ok = (
            bool(_field(result, "ok", False))
            and not timed_out
            and int(exit_code or 0) == 0
            and counts["failed"] == 0
            and counts["errors"] == 0
        )
        if not summary:
            summary = "通过了" if ok else "没通过"

        self._write_state(
            {
                "commit": commit,
                "ok": ok,
                "summary": summary,
                "failed_tests": failed_tests,
                "passed": counts["passed"],
                "failed": counts["failed"],
                "skipped": counts["skipped"],
                "errors": counts["errors"],
                "ts": utcnow().isoformat(),
                "duration_ms": duration_ms,
                "exit_code": exit_code,
            }
        )
        return {
            "ok": ok,
            "passed": counts["passed"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "errors": counts["errors"],
            "summary": summary,
            "failed_tests": failed_tests,
            "timed_out": timed_out,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "backend": backend,
        }

    def _source_propose(self, args: dict[str, Any]) -> dict[str, Any]:
        mirror = self._ready_mirror()
        summary = str(args.get("summary") or "").strip()
        if not summary:
            raise MirrorError("提案要有一句说明，讲讲这次改了什么。")
        reason = str(args.get("reason") or "").strip()
        head = mirror.head()
        base = self._latest_applied_base(mirror) or mirror.first_commit()
        if not head or not base or base == head:
            raise MirrorError("没有要提交的改动")
        files = mirror.changed_files(base)
        if not files:
            raise MirrorError("没有要提交的改动")
        patch = mirror.diff(base)
        tier = classify(files)
        tests = self._tests_for(head)
        proposal_id = "p_" + utcnow().strftime("%Y%m%dT%H%M%S") + "_" + os.urandom(3).hex()

        folder = Path(self.paths.proposals_dir) / proposal_id
        folder.mkdir(parents=True, exist_ok=True)
        os.chmod(folder, 0o755)
        meta = {
            "id": proposal_id,
            "created_ts": utcnow().isoformat(),
            "author": "her",
            "base_commit": base,
            "commit": head,
            "summary": summary,
            "reason": reason,
            "files": files,
            "tier": tier,
            "tests": tests,
        }
        atomic_write_text(
            folder / "meta.json",
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            mode=0o644,
        )
        atomic_write_text(folder / "patch.diff", patch, mode=0o644)
        return {
            "ok": True,
            "id": proposal_id,
            "tier": tier,
            "files": files,
            "base_commit": base,
            "commit": head,
            "summary": summary,
        }

    # -- helpers --------------------------------------------------------

    def _latest_applied_base(self, mirror: Mirror) -> str | None:
        """The commit of the most recent successful apply, if we can still see it.

        apply-results entries are written by root. We only trust one when its
        commit still exists in the mirror: after a rebuild of the mirror those
        shas are gone, and a stale base would make the next diff include
        everything since the beginning.
        """
        folder = Path(self.paths.results_dir)
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            return None
        best: tuple[tuple[float, str], str] | None = None
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or not data.get("ok"):
                continue
            commit = data.get("commit")
            if not isinstance(commit, str) or not commit.strip():
                continue
            commit = commit.strip()
            if not mirror.exists(commit):
                continue
            created = data.get("created") or data.get("ts") or ""
            key = (_parse_ts(created) or 0.0, path.name)
            if best is None or key >= best[0]:
                best = (key, commit)
        return best[1] if best else None

    def _tests_for(self, head: str | None) -> dict[str, Any]:
        """The test record, but only when it belongs to this exact commit."""
        try:
            data = json.loads(Path(self.paths.state_file).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None
        if not isinstance(data, dict) or not head or str(data.get("commit") or "") != head:
            return {"ok": False, "summary": "这个提交还没跑过 self_test"}
        return {
            "ok": bool(data.get("ok")),
            "passed": int(data.get("passed") or 0),
            "failed": int(data.get("failed") or 0),
            "skipped": int(data.get("skipped") or 0),
            "errors": int(data.get("errors") or 0),
            "summary": str(data.get("summary") or ""),
            "failed_tests": [str(item) for item in (data.get("failed_tests") or [])],
            "ts": str(data.get("ts") or ""),
        }


def _detail(result: dict[str, Any]) -> str:
    for key in ("error", "summary", "path", "id"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return ""


def _chars(result: dict[str, Any]) -> int:
    text = result.get("text")
    if isinstance(text, str):
        return len(text)
    size = result.get("bytes")
    if isinstance(size, int) and size > 0:
        return size
    return 0
