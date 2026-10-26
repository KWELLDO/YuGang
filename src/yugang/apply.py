"""合入她自己的改动：一条有回滚点的原子动作。

她能改自己的镜像、跑自己的测试、提一份提案；但让改动真正生效的只有这一条
root 侧的通道。合入的顺序是固定的：

    verify -> test -> approve -> snapshot -> swap -> restart -> health

任何一步失败都走第 8 步回退：解回快照、重启、再验一次健康。回退本身失败也
不掩盖——结果里写 stage="rollback"、rolled_back=false，并让 systemd 保持失败
状态，人再去看。每个结果都带一份环境信息（Python 版本、unit 状态、健康、
提交、失败的测试、磁盘、日志尾巴、耗时），因为"她的改动没过"这句话本身没有
可操作的信息，环境才有。

所有副作用都从 SystemOps 走：重启、unit 状态、健康探针、日志、磁盘。默认
实现 SystemdOps 调 systemctl / journalctl / HTTP。测试注入假 ops，永远不碰
真的 systemd。外部命令（git / tar / rsync）用 subprocess 调用，失败时把 stderr
前 800 字放进 ApplyError——回退和快照可以重来，但错误要说清。

只使用标准库。
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Protocol

from .ids import utcnow
from .world.sandbox import SandboxLimits

__all__ = [
    "AUTO_PREFIXES",
    "ApplyError",
    "ApplyResult",
    "SystemOps",
    "SystemdOps",
    "apply_proposal",
    "load_proposal",
    "recompute_tier",
    "write_result",
]

UNIT_NAME = "yugang.service"
HEALTH_URL = "http://127.0.0.1:3198/health"
ENV_FILE = Path("/etc/dafeiyu-dsh.env")
TOKEN_KEY = "YUGANG_HTTP_TOKEN"

# spec §3：只有这两处能自动合，其余一切（src/tests/deploy/CI/pyproject）都要人批。
AUTO_PREFIXES = ("docs/", "README.md")

ERROR_LIMIT = 4000
ACTIVE_TIMEOUT_SECONDS = 30.0
HEALTH_TIMEOUT_SECONDS = 45.0
HEALTH_POLL_SECONDS = 0.5
JOURNAL_LINES = 40
MAX_FAILED_TESTS = 20
STDERR_IN_ERROR = 800
HEALTH_RAW_IN_ERROR = 500

# -o addopts="" matters: pyproject already passes -q, and a second -q makes it -qq,
# which suppresses the very summary line we carry into env.pytest_summary and
# into her memory ("2 failed, 496 passed" instead of a line of dots).
PYTEST_ARGS = '-m pytest -x -q -p no:cacheprovider -o addopts='
REVIEW_MESSAGE = "这份改动要我批（review 档），还没有批准"
TRUNCATION_MARKER = "\n…[已截断]…\n"

_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HEX_SHA = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

_META_FIELDS = (
    "id",
    "created_ts",
    "base_commit",
    "commit",
    "summary",
    "reason",
    "files",
    "tier",
)

# 失败后需要动快照的三个阶段：swap 可能已经改了 live，restart/health 已经换装。
_ROLLBACK_STAGES = ("swap", "restart", "health")


class ApplyError(RuntimeError):
    """合入的某一步失败了；消息是可读的，带 stderr / health raw 的摘要。"""


@dataclass(frozen=True)
class ApplyResult:
    """一次合入尝试的结论，也是 apply-results/<id>.json 的内容。"""

    id: str
    ok: bool
    stage: str
    applied_commit: str | None
    rolled_back: bool
    error: str
    env: dict[str, Any]
    ts: str


class SystemOps(Protocol):
    """所有带副作用的系统动作；默认实现是 SystemdOps。"""

    def restart(self) -> None:
        """重启服务，失败时抛 ApplyError。"""

    def is_active(self) -> str:
        """systemctl is-active 的答案：active / failed / ..."""

    def wait_active(self, seconds: float) -> bool:
        """在 seconds 秒内等它变成 active。"""

    def health(self) -> dict:
        """HTTP 体检：{"ok": bool, "raw": str}。"""

    def journal_tail(self, lines: int = 40) -> str:
        """unit 最近的日志。"""

    def disk_free_mb(self, path: str) -> int:
        """path 所在文件系统的剩余 MB。"""


def load_proposal(proposals_dir: Path, proposal_id: str) -> dict:
    """读 <proposals_dir>/<id>/meta.json 并校验它。

    只校验"这份提案本身是否说得通"：字段齐、id 与目录一致、files 是安全的相对
    路径、commit 是 40/64 位 hex、tier 已知。patch 与 tier 的复核在合入流程里
    做，因为它们要用到 patch 内容和规则表。
    """
    if not isinstance(proposal_id, str) or not _SAFE_NAME.match(proposal_id):
        raise ApplyError(f"提案 id 不合法：{proposal_id!r}")
    meta_path = Path(proposals_dir) / proposal_id / "meta.json"
    try:
        text = meta_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ApplyError(f"提案不存在：{meta_path}") from None
    except OSError as exc:
        raise ApplyError(f"读不到提案 meta.json：{exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApplyError(f"meta.json 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ApplyError("meta.json 必须是一个 JSON 对象")

    missing = [name for name in _META_FIELDS if name not in data]
    if missing:
        raise ApplyError("meta.json 缺字段：" + ", ".join(missing))
    if str(data.get("id") or "") != proposal_id:
        raise ApplyError(f"meta.json 的 id 与目录名不一致：{data.get('id')!r} != {proposal_id!r}")
    for name in ("created_ts", "base_commit", "commit", "summary", "reason"):
        if not isinstance(data.get(name), str):
            raise ApplyError(f"meta.json 的 {name} 必须是字符串")
    if not str(data.get("summary") or "").strip():
        raise ApplyError("meta.json 的 summary 不能是空的")
    if not str(data.get("created_ts") or "").strip():
        raise ApplyError("meta.json 的 created_ts 不能是空的")

    commit = str(data["commit"])
    if not _HEX_SHA.match(commit):
        raise ApplyError(f"meta.json 的 commit 不是 40/64 位 hex：{commit!r}")
    base_commit = str(data["base_commit"])
    if not _SAFE_NAME.match(base_commit):
        raise ApplyError(f"meta.json 的 base_commit 不合法：{base_commit!r}")

    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ApplyError("meta.json 的 files 必须是非空的路径列表")
    normalized: list[str] = []
    for item in files:
        if not isinstance(item, str):
            raise ApplyError(f"meta.json 的 files 里有非字符串：{item!r}")
        normalized.append(_safe_rel_path(item))
    if len(set(normalized)) != len(normalized):
        raise ApplyError("meta.json 的 files 里有重复路径")
    data["files"] = normalized

    if data.get("tier") not in ("auto", "review"):
        raise ApplyError(f"meta.json 的 tier 不合法：{data.get('tier')!r}")
    return data


def recompute_tier(files: list[str]) -> str:
    """按 spec §3 用改动路径重算 tier；不认识的一律 review。"""
    if not files:
        return "review"
    for name in files:
        path = PurePosixPath(str(name)).as_posix()
        if not _under_auto_prefix(path):
            return "review"
    return "auto"


def write_result(results_dir: Path, result: ApplyResult) -> Path:
    """把结果写成 <results_dir>/<id>.json（0644），返回路径。"""
    if not isinstance(result.id, str) or not _SAFE_NAME.match(result.id):
        raise ValueError(f"结果 id 不合法：{result.id!r}")
    directory = Path(results_dir)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"建不了结果目录 {directory}：{exc}") from exc
    target = directory / f"{result.id}.json"
    payload = {
        "id": result.id,
        "ts": result.ts,
        "ok": result.ok,
        "stage": result.stage,
        "applied_commit": result.applied_commit,
        "rolled_back": result.rolled_back,
        "error": result.error,
        "env": result.env,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n"
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, target)
    return target


def apply_proposal(
    *,
    home: Path,
    proposal_id: str,
    live_root: Path,
    proposals_dir: Path | None = None,
    results_dir: Path | None = None,
    rollback_dir: Path = Path("/var/lib/yugang/rollback"),
    candidates_dir: Path = Path("/var/lib/yugang/candidates"),
    venv_python: str = "/opt/yugang-venv/bin/python",
    approve: bool = False,
    allow_review: bool = False,
    ops: SystemOps | None = None,
    sandbox_run: Any = None,
    now_fn: Callable[[], Any] = utcnow,
    test_timeout_seconds: float = 600.0,
) -> ApplyResult:
    """验证、测试、审批、快照、换装、重启、体检；失败自动回退。"""
    started = time.monotonic()
    ts = _iso(now_fn())
    home = Path(home).expanduser()
    live_root = Path(live_root).expanduser()
    proposals_dir = (
        Path(proposals_dir).expanduser() if proposals_dir is not None else home / "proposals"
    )
    results_dir = (
        Path(results_dir).expanduser() if results_dir is not None else home / "apply-results"
    )
    rollback_dir = Path(rollback_dir).expanduser()
    candidates_dir = Path(candidates_dir).expanduser()
    system: SystemOps = ops if ops is not None else SystemdOps()
    runner = sandbox_run if sandbox_run is not None else _default_sandbox_run

    env = _empty_env()
    meta: dict[str, Any] = {}
    snapshot_path: Path | None = None
    health_ok: bool | None = None
    stage = "verify"
    failed_stage: str | None = None
    error = ""
    rolled_back = False

    try:
        if not live_root.is_dir():
            raise ApplyError(f"live 目录不存在：{live_root}")
        if live_root.name in ("", ".", ".."):
            raise ApplyError(f"live 目录不合法：{live_root}")

        meta = load_proposal(proposals_dir, proposal_id)
        env["base_commit"] = str(meta["base_commit"])
        env["commit"] = str(meta["commit"])
        tier = _effective_tier(meta)
        patch_path = _verify_patch(proposals_dir, proposal_id, meta)

        # 第 1 步的余下部分：把 live 拷成候选副本，git apply --check 再真 apply。
        candidate = _prepare_candidate(candidates_dir, proposal_id, live_root, patch_path)

        stage = "test"
        outcome = _run_tests(runner, candidate, venv_python, float(test_timeout_seconds))
        env["failed_tests"] = list(outcome.failed)
        env["pytest_summary"] = outcome.summary
        if not outcome.ok:
            raise ApplyError(outcome.error)

        stage = "approve"
        if tier == "review" and not _review_approved(
            proposals_dir, proposal_id, approve, allow_review
        ):
            raise _ReviewRequired(REVIEW_MESSAGE)

        stage = "snapshot"
        snapshot_path = _snapshot(live_root, rollback_dir, str(meta["base_commit"]))

        stage = "swap"
        _swap(candidate, live_root)

        stage = "restart"
        _restart_and_wait(system)

        stage = "health"
        health_ok, payload = _wait_health(system)
        if not health_ok:
            raise ApplyError(f"服务起来了，但体检没过：{_health_excerpt(payload)}")
    except _ReviewRequired as exc:
        failed_stage = stage
        error = str(exc)
    except ApplyError as exc:
        failed_stage = stage
        error = str(exc) or "合入失败"
    except Exception as exc:  # noqa: BLE001 - 没有预料到的错也要带着环境信息回退
        failed_stage = stage
        error = _one_line(exc)

    if failed_stage is not None:
        if not error:
            error = "合入失败"
        reported = failed_stage
        if snapshot_path is not None and failed_stage in _ROLLBACK_STAGES:
            restored, reason = _rollback(snapshot_path, live_root, system)
            if restored:
                rolled_back = True
            else:
                reported = "rollback"
                error = f"{error}\n[回退也失败了] {reason}"
        stage = reported

    ok = failed_stage is None
    if ok:
        stage = "done"

    if not env["base_commit"]:
        peeked = _peek_meta(proposals_dir, proposal_id)
        env["base_commit"] = str(peeked.get("base_commit") or "")
        env["commit"] = str(peeked.get("commit") or "")
    if health_ok is None:
        if failed_stage == "restart":
            # unit 都没起来，不必再去问 /health。
            health_ok = False
        else:
            health_ok = _probe_health(system)["ok"]
    env["health_ok"] = bool(health_ok)
    env["unit_active"] = _safe_active(system)
    env["journal_tail"] = _safe_journal(system)
    env["disk_free_mb"] = _safe_disk(system, live_root)
    env["duration_s"] = round(time.monotonic() - started, 1)

    result = ApplyResult(
        id=proposal_id,
        ok=ok,
        stage=stage,
        applied_commit=str(meta.get("commit") or "") if ok else None,
        rolled_back=rolled_back,
        error=_clip_error(error) if error else "",
        env=env,
        ts=ts,
    )
    try:
        write_result(results_dir, result)
    except OSError as exc:
        # 结果写不进也要把结论带回去，人至少能看到"合了，但结果没落盘"。
        note = f"\n[结果没有写成 {results_dir}：{exc}]"
        result = ApplyResult(
            id=result.id,
            ok=result.ok,
            stage=result.stage,
            applied_commit=result.applied_commit,
            rolled_back=result.rolled_back,
            error=_clip_error(result.error + note),
            env=result.env,
            ts=result.ts,
        )
    return result


# --------------------------------------------------------------------------- #
# 默认的系统动作
# --------------------------------------------------------------------------- #


class SystemdOps:
    """线上实现：systemctl / journalctl / /health / 磁盘。测试里不要用它。"""

    def __init__(
        self,
        *,
        unit: str = UNIT_NAME,
        env_file: Path = ENV_FILE,
        health_url: str = HEALTH_URL,
        health_timeout: float = 5.0,
    ) -> None:
        self.unit = unit
        self.env_file = Path(env_file)
        self.health_url = health_url
        self.health_timeout = float(health_timeout)

    def restart(self) -> None:
        try:
            proc = _run_command(["systemctl", "restart", self.unit], timeout=60.0)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApplyError(f"systemctl restart 起不来：{type(exc).__name__}: {exc}") from exc
        if proc.returncode != 0:
            detail = _first_line_text(proc.stderr or proc.stdout, 500)
            raise ApplyError(
                f"systemctl restart {self.unit} 失败（退出码 {proc.returncode}）"
                + (f"：{detail}" if detail else "")
            )

    def is_active(self) -> str:
        try:
            proc = _run_command(["systemctl", "is-active", self.unit], timeout=15.0)
        except (OSError, subprocess.SubprocessError):
            return ""
        answer = (proc.stdout or "").strip()
        return answer or (proc.stderr or "").strip()

    def wait_active(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while True:
            if self.is_active() == "active":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    def health(self) -> dict:
        token = _read_env_file(self.env_file).get(TOKEN_KEY, "")
        request = urllib.request.Request(self.health_url, headers={"Accept": "application/json"})
        if token:
            request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.health_timeout) as response:
                body = response.read().decode("utf-8", "replace")
                status = int(getattr(response, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 读 body 失败不影响结论
                body = ""
            raw = body.strip() or f"HTTP {exc.code}"
            return {"ok": _json_ok(body), "raw": _clip(raw, 2000), "status": int(exc.code)}
        except Exception as exc:  # noqa: BLE001 - 连不上也要是一个结论
            return {"ok": False, "raw": _clip(f"{type(exc).__name__}: {exc}", 300)}
        return {"ok": _json_ok(body), "raw": _clip(body, 2000), "status": status}

    def journal_tail(self, lines: int = JOURNAL_LINES) -> str:
        try:
            proc = _run_command(
                ["journalctl", "-u", self.unit, "-n", str(max(1, int(lines))), "--no-pager"],
                timeout=20.0,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if proc.returncode != 0:
            return ""
        return proc.stdout or ""

    def disk_free_mb(self, path: str) -> int:
        try:
            return int(shutil.disk_usage(str(path)).free // (1024 * 1024))
        except OSError:
            return 0


# --------------------------------------------------------------------------- #
# 各步的实现
# --------------------------------------------------------------------------- #


class _ReviewRequired(Exception):
    """review 档没批：结果要写成 approve，不是失败回退。"""


@dataclass(frozen=True)
class _TestOutcome:
    ok: bool
    exit_code: int | None
    timed_out: bool
    summary: str
    failed: list[str]
    error: str


def _default_sandbox_run(command: str, *, workdir: str, limits: Any, env: dict, backend: str):
    from .world.sandbox import run as sandbox_run

    return sandbox_run(command, workdir=workdir, limits=limits, env=env, backend=backend)


def _empty_env() -> dict[str, Any]:
    parts = sys.version.split()
    return {
        "python": parts[0] if parts else "",
        "unit_active": "",
        "health_ok": False,
        "base_commit": "",
        "commit": "",
        "failed_tests": [],
        "pytest_summary": "",
        "disk_free_mb": 0,
        "journal_tail": "",
        "duration_s": 0.0,
    }


def _iso(moment: Any) -> str:
    if isinstance(moment, datetime):
        aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat()
    return str(moment)


def _peek_meta(proposals_dir: Path, proposal_id: str) -> dict:
    """失败时也能填 env 的尽力而为：不校验，只读。"""
    try:
        data = json.loads((Path(proposals_dir) / proposal_id / "meta.json").read_text("utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return data if isinstance(data, dict) else {}


def _safe_rel_path(value: str) -> str:
    text = str(value).strip()
    if (
        not text
        or text.startswith("/")
        or "\x00" in text
        or "\\" in text
        or (len(text) > 1 and text[1] == ":")
    ):
        raise ApplyError(f"meta.json 的 files 里有不安全路径：{value!r}")
    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", "..") for part in pure.parts):
        raise ApplyError(f"meta.json 的 files 里有不安全路径：{value!r}")
    return pure.as_posix()


def _under_auto_prefix(path: str) -> bool:
    for prefix in AUTO_PREFIXES:
        if prefix.endswith("/"):
            if path.startswith(prefix):
                return True
        elif path == prefix:
            return True
    return False


def _effective_tier(meta: dict) -> str:
    """meta 自称的 tier 与重算结果取更严格的那档。"""
    recomputed = recompute_tier([str(item) for item in meta.get("files") or []])
    if str(meta.get("tier")) == "review" or recomputed == "review":
        return "review"
    return "auto"


def _patch_paths(diff_text: str) -> list[str]:
    """从 patch 里取改动路径：优先 +++ b/，删除文件时退到 --- a/。"""
    found: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            found.append(line[len("+++ b/") :])
        elif line.startswith("--- a/"):
            found.append(line[len("--- a/") :])
    cleaned: list[str] = []
    for item in found:
        text = item.strip()
        if not text or text == "/dev/null":
            continue
        # diff 行尾偶尔带制表符分隔的时间戳（手写的 patch 才会这样）。
        text = text.split("\t", 1)[0].strip()
        cleaned.append(PurePosixPath(text).as_posix())
    return cleaned


def _verify_patch(proposals_dir: Path, proposal_id: str, meta: dict) -> Path:
    patch_path = Path(proposals_dir) / proposal_id / "patch.diff"
    try:
        text = patch_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ApplyError(f"提案里没有 patch.diff：{patch_path}") from None
    except OSError as exc:
        raise ApplyError(f"读不到 patch.diff：{exc}") from exc
    if not text.strip():
        raise ApplyError("patch.diff 是空的")

    declared = {str(item) for item in meta.get("files") or []}
    in_patch = set(_patch_paths(text))
    if not in_patch:
        raise ApplyError("patch.diff 里找不到 +++ b/ 行，不像是 git diff")
    if declared != in_patch:
        raise ApplyError(
            "meta.files 与 patch.diff 里的路径不一致："
            f"meta={sorted(declared)} patch={sorted(in_patch)}"
        )
    return patch_path


def _prepare_candidate(
    candidates_dir: Path, proposal_id: str, live_root: Path, patch_path: Path
) -> Path:
    """清空重建候选副本（= live 的拷贝 + 一个临时 git 树），再把 patch 打上去。"""
    if not _SAFE_NAME.match(proposal_id):
        raise ApplyError(f"提案 id 不合法：{proposal_id!r}")
    _ensure_dir(candidates_dir, 0o700)
    candidate = Path(candidates_dir) / proposal_id
    if candidate.exists() or candidate.is_symlink():
        _remove_path(candidate)
    try:
        shutil.copytree(
            live_root,
            candidate,
            symlinks=False,
            dirs_exist_ok=True,
            ignore_dangling_symlinks=True,
        )
    except (OSError, shutil.Error) as exc:
        raise ApplyError(f"把 live 拷进候选副本失败：{exc}") from exc
    _make_writable(candidate)

    _must_run(["git", "-C", str(candidate), "init", "-q"], "在候选副本里 git init")
    _must_run(
        ["git", "-C", str(candidate), "apply", "--check", "-p1", str(patch_path)],
        "git apply --check",
    )
    _must_run(
        ["git", "-C", str(candidate), "apply", "-p1", str(patch_path)],
        "git apply",
    )
    return candidate


def _run_tests(
    runner: Any, candidate: Path, venv_python: str, timeout_seconds: float
) -> _TestOutcome:
    command = f"{shlex.quote(str(venv_python))} {PYTEST_ARGS}"
    try:
        outcome = runner(
            command,
            workdir=str(candidate),
            limits=SandboxLimits(timeout_seconds=timeout_seconds),
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            backend="auto",
        )
    except Exception as exc:  # noqa: BLE001 - 沙盒不可用也是"测试没通过"
        return _TestOutcome(
            ok=False,
            exit_code=None,
            timed_out=False,
            summary="",
            failed=[],
            error=f"测试没有跑起来：{_one_line(exc)}",
        )

    stdout = str(getattr(outcome, "stdout", "") or "")
    stderr = str(getattr(outcome, "stderr", "") or "")
    exit_code = getattr(outcome, "exit_code", None)
    timed_out = bool(getattr(outcome, "timed_out", False))
    primary = stdout.strip() or stderr.strip()
    lines = [line.strip() for line in primary.splitlines() if line.strip()]
    summary = lines[-1] if lines else ""
    failed: list[str] = []
    for line in (stdout + "\n" + stderr).splitlines():
        if line.startswith("FAILED "):
            item = line[len("FAILED ") :].strip()
            if item:
                failed.append(item)
    failed = failed[:MAX_FAILED_TESTS]

    ok = (not timed_out) and exit_code == 0
    if ok:
        return _TestOutcome(True, exit_code, timed_out, summary, failed, "")

    if timed_out:
        head = f"测试超过 {timeout_seconds:g} 秒，已终止"
    else:
        reason = str(getattr(outcome, "error", "") or "")
        head = reason or f"测试没通过（退出码 {exit_code}）"
    parts = [head]
    if failed:
        parts.append("\n".join("FAILED " + item for item in failed))
    combined = "\n".join(part for part in (stdout, stderr) if part.strip()).strip()
    if combined:
        parts.append(combined[-2500:])
    return _TestOutcome(False, exit_code, timed_out, summary, failed, "\n".join(parts))


def _review_approved(
    proposals_dir: Path, proposal_id: str, approve: bool, allow_review: bool
) -> bool:
    if approve or allow_review:
        return True
    return (Path(proposals_dir) / proposal_id / "APPROVED").is_file()


def _snapshot(live_root: Path, rollback_dir: Path, base_commit: str) -> Path:
    """tar -C <live 的父目录> -czf <rollback>/<base_commit>.tar.gz <live 的名字>"""
    _ensure_dir(rollback_dir, 0o700)
    target = Path(rollback_dir) / f"{base_commit}.tar.gz"
    _must_run(
        ["tar", "-C", str(live_root.parent), "-czf", str(target), live_root.name],
        "给 live 打快照",
    )
    if not target.is_file() or target.stat().st_size == 0:
        raise ApplyError(f"快照文件没有生成：{target}")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    return target


def _swap(candidate: Path, live_root: Path) -> None:
    """把候选副本同步进 live；先放开写位，同步完由 _lock_down 收回。"""
    _make_writable(live_root)
    rsync = shutil.which("rsync")
    if rsync:
        # --checksum 是必要的：候选里的文件 mtime 可能和 live 落在同一秒，
        # rsync 默认的 size+mtime 快检会把"大小相同的改动"当成没变而跳过。
        _must_run(
            [
                rsync,
                "-a",
                "--delete",
                "--checksum",
                "--exclude=.git",
                f"{str(candidate).rstrip('/')}/",
                f"{str(live_root).rstrip('/')}/",
            ],
            "rsync 同步候选副本到 live",
        )
    else:  # pragma: no cover - 线上有 rsync；这里留给没有 rsync 的机器
        _sync_tree(candidate, live_root)
    _lock_down(live_root)


def _restart_and_wait(system: SystemOps) -> None:
    try:
        system.restart()
    except ApplyError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ApplyError(f"重启失败：{_one_line(exc)}") from exc
    try:
        active = bool(system.wait_active(ACTIVE_TIMEOUT_SECONDS))
    except Exception:  # noqa: BLE001
        active = False
    if not active:
        raise ApplyError(
            f"服务在 {ACTIVE_TIMEOUT_SECONDS:g} 秒内没有 active"
            f"（is-active={_safe_active(system)}）"
        )


def _wait_health(system: SystemOps, *, timeout: float | None = None) -> tuple[bool, dict]:
    budget = HEALTH_TIMEOUT_SECONDS if timeout is None else timeout
    deadline = time.monotonic() + max(0.0, float(budget))
    while True:
        payload = _probe_health(system)
        if payload["ok"]:
            return True, payload
        if time.monotonic() >= deadline:
            return False, payload
        time.sleep(HEALTH_POLL_SECONDS)


def _probe_health(system: SystemOps) -> dict:
    try:
        payload = system.health()
    except Exception as exc:  # noqa: BLE001 - 探针抛错就是"不健康"
        return {"ok": False, "raw": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "raw": str(payload)[:300]}
    return {
        "ok": bool(payload.get("ok")),
        "raw": str(payload.get("raw") or ""),
    }


def _rollback(snapshot_path: Path, live_root: Path, system: SystemOps) -> tuple[bool, str]:
    """解回快照 → 重启 → 等 active → 再验健康。返回 (成功了吗, 失败原因)。"""
    try:
        _restore_snapshot(snapshot_path, live_root)
    except ApplyError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"解回快照失败：{_one_line(exc)}"
    try:
        system.restart()
    except Exception as exc:  # noqa: BLE001
        return False, f"回退后重启失败：{_one_line(exc)}"
    try:
        active = bool(system.wait_active(ACTIVE_TIMEOUT_SECONDS))
    except Exception:  # noqa: BLE001
        active = False
    if not active:
        return False, f"回退后服务没有起来（is-active={_safe_active(system)}）"
    ok, payload = _wait_health(system)
    if not ok:
        return False, f"回退后体检没过：{_health_excerpt(payload)}"
    return True, ""


def _restore_snapshot(snapshot_path: Path, live_root: Path) -> None:
    snapshot_path = Path(snapshot_path)
    if not snapshot_path.is_file():
        raise ApplyError(f"回滚快照不见了：{snapshot_path}")
    try:
        live_root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApplyError(f"建不了 live 的父目录：{exc}") from exc
    if live_root.exists():
        _make_writable(live_root)
    _must_run(["tar", "-C", str(live_root.parent), "-xzf", str(snapshot_path)], "解回快照")
    _lock_down(live_root)


# --------------------------------------------------------------------------- #
# env 采集（全部尽力而为，永不抛异常）
# --------------------------------------------------------------------------- #


def _safe_active(system: SystemOps) -> str:
    try:
        return str(system.is_active() or "")
    except Exception:  # noqa: BLE001
        return ""


def _safe_journal(system: SystemOps) -> str:
    try:
        text = str(system.journal_tail(JOURNAL_LINES) or "")
    except Exception:  # noqa: BLE001
        return ""
    lines = text.splitlines()[-JOURNAL_LINES:]
    return _clip_error("\n".join(lines))


def _safe_disk(system: SystemOps, live_root: Path) -> int:
    try:
        return int(system.disk_free_mb(str(live_root)))
    except Exception:  # noqa: BLE001
        return 0


# --------------------------------------------------------------------------- #
# 文件与文本
# --------------------------------------------------------------------------- #


def _run_command(argv: list[str], *, timeout: float = 120.0) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _must_run(argv: list[str], what: str) -> None:
    try:
        proc = _run_command(argv)
    except FileNotFoundError as exc:
        raise ApplyError(f"{what} 用的命令不在这台机器上：{exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ApplyError(f"{what} 超时了：{exc}") from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApplyError(f"{what} 起不来：{_one_line(exc)}") from exc
    if proc.returncode != 0:
        detail = _first_line_text(proc.stderr or proc.stdout, STDERR_IN_ERROR)
        raise ApplyError(
            f"{what} 失败（退出码 {proc.returncode}）" + (f"：{detail}" if detail else "")
        )


def _ensure_dir(path: Path, mode: int) -> None:
    directory = Path(path)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApplyError(f"建不了目录 {directory}：{exc}") from exc
    try:
        os.chmod(directory, mode)
    except OSError:
        pass


def _make_writable(root: Path) -> None:
    """把 read-only 位放开，让换装与解包能覆盖 live 里的每个文件。"""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        return
    for current, dirs, files in os.walk(root):
        add = stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
        try:
            os.chmod(current, os.stat(current).st_mode | add)
        except OSError:
            continue
        for name in dirs + files:
            child = Path(current) / name
            try:
                if child.is_symlink():
                    continue
                mode = os.stat(child).st_mode
                extra = stat.S_IRUSR | stat.S_IWUSR
                if stat.S_ISDIR(mode):
                    extra |= stat.S_IXUSR
                os.chmod(child, mode | extra)
            except OSError:
                continue


def _lock_down(root: Path) -> None:
    """收回写位：目录 0555、普通文件 0444（原来是可执行文件的留 0555）。"""
    root = Path(root)
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root, topdown=False):
        for name in files:
            child = Path(current) / name
            try:
                if child.is_symlink():
                    continue
                mode = os.stat(child).st_mode
                os.chmod(child, 0o555 if mode & 0o111 else 0o444)
            except OSError:
                continue
        for name in dirs:
            child = Path(current) / name
            try:
                if child.is_symlink():
                    continue
                os.chmod(child, 0o555)
            except OSError:
                continue
    try:
        os.chmod(root, 0o555)
    except OSError:
        pass
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        for current, _dirs, files in os.walk(root):
            try:
                os.chown(current, 0, 0)
            except OSError:
                pass
            for name in files:
                try:
                    os.chown(Path(current) / name, 0, 0, follow_symlinks=False)
                except OSError:
                    continue


def _remove_path(path: Path) -> None:
    target = Path(path)
    try:
        if target.is_symlink() or target.is_file():
            target.unlink()
            return
        if target.is_dir():
            _make_writable(target)
            shutil.rmtree(target)
    except FileNotFoundError:
        return


def _rmtree(path: Path) -> None:
    _remove_path(Path(path))


def _copy_file(source: Path, target: Path) -> None:
    if target.is_dir() and not target.is_symlink():
        _remove_path(target)
    tmp = target.with_name(f".{target.name}.apply-tmp-{os.getpid()}")
    if tmp.exists() or tmp.is_symlink():
        _remove_path(tmp)
    shutil.copy2(source, tmp)
    os.replace(tmp, target)


def _sync_tree(src: Path, dst: Path, *, skip: frozenset = frozenset({".git"})) -> None:
    """rsync 不在时的退路：逐项覆盖 + 删掉候选里没有的东西。"""
    src = Path(src)
    dst = Path(dst)
    try:
        dst.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ApplyError(f"建不了目录 {dst}：{exc}") from exc
    names: set[str] = set()
    for entry in sorted(src.iterdir()):
        if entry.name in skip:
            continue
        names.add(entry.name)
        target = dst / entry.name
        try:
            if entry.is_symlink():
                resolved = entry.resolve()
                if resolved.is_dir():
                    if target.is_symlink() or (target.exists() and not target.is_dir()):
                        _remove_path(target)
                    _sync_tree(resolved, target, skip=skip)
                else:
                    _copy_file(resolved, target)
            elif entry.is_dir():
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    _remove_path(target)
                _sync_tree(entry, target, skip=skip)
            else:
                _copy_file(entry, target)
        except OSError as exc:
            raise ApplyError(f"同步 {entry} 到 live 失败：{exc}") from exc
    for existing in dst.iterdir():
        if existing.name in names or existing.name in skip:
            continue
        _remove_path(existing)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _json_ok(body: str) -> bool:
    try:
        data = json.loads(body or "{}")
    except (json.JSONDecodeError, TypeError):
        return False
    return bool(isinstance(data, dict) and data.get("ok"))


def _health_excerpt(payload: dict) -> str:
    raw = str(payload.get("raw") or "").strip()
    if not raw:
        return "体检接口没有给出内容"
    return _clip(raw, HEALTH_RAW_IN_ERROR)


def _one_line(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}".strip()
    return _clip(text.replace("\n", " "), STDERR_IN_ERROR)


def _first_line_text(text: str | None, limit: int) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    return _clip(cleaned, limit)


def _clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit]


def _clip_error(text: str, limit: int = ERROR_LIMIT) -> str:
    """截到 limit 字；保头部（FAILED 行）也保尾部（traceback 最后一段）。"""
    if len(text) <= limit:
        return text
    keep_head = min(1200, max(0, limit // 3))
    keep_tail = max(0, limit - keep_head - len(TRUNCATION_MARKER))
    return text[:keep_head] + TRUNCATION_MARKER + text[-keep_tail:]
