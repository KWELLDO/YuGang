"""Run commands in an operating-system sandbox with no network.

She is getting real agency: reading the web is one GET, but running a command is a
different kind of act. Reading is cheap to undo; a command writes files, starts
processes, and could -- if it had a route out -- publish, leak or post by
accident. So commands run inside a sandbox with no network at all. That is
exactly why running one does not need per-call operator approval: a sandbox that
cannot reach out cannot do the damage approval would be guarding against.

Backends, in order of preference:

- 'bubblewrap' (the bwrap binary). The whole filesystem is mounted read-only, the
  workspace is the only writable directory, /tmp is a fresh tmpfs, and the user,
  pid, net, ipc, uts and cgroup namespaces are unshared. The command cannot reach
  the network and cannot write outside its workspace.
- 'unshare' (unshare -rn). The weaker fallback: only the network namespace is
  unshared, so the command has no egress, but the filesystem is *not* made
  read-only. SandboxResult.backend reports "unshare" so a caller can say which
  guarantee it got.

If neither backend is usable the module fails closed: run() raises
SandboxUnavailable instead of running the command unsandboxed.

The child sees a scrubbed environment: only PATH, HOME (the workspace), TMPDIR,
LANG and PYTHONIOENCODING, plus whatever the caller passes in env. The parent's
environment is never inherited, so API keys and channel secrets stay outside.
Every call is capped: a wall-clock timeout that kills the whole process group,
CPU/address-space/file-size/core limits, and clipped output.

One limit needs a note. RLIMIT_NPROC cannot be set before exec: bubblewrap clones
its sandbox after our preexec_fn runs, and the kernel charges that clone against
the *calling user's* existing threads, so any finite value makes bubblewrap fail
with EAGAIN. The process cap is therefore lowered to SandboxLimits.max_processes
inside the sandbox by a tiny python3 prelude that runs immediately before
/bin/sh; inside the fresh user namespace the process count starts at zero, so the
cap means what it says. The other four limits are applied in preexec_fn before
exec.

Only the standard library is used.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_TRUNCATION_MARKER = "\n…[已截断]"

# The child never inherits the parent's environment; this is all it starts with.
_BASE_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "TMPDIR": "/tmp",
    "LANG": "C.UTF-8",
    "PYTHONIOENCODING": "utf-8",
}

# Runs inside the sandbox, after the namespaces exist, before /bin/sh replaces it.
# RLIMIT_NPROC is handled here rather than in preexec_fn: see the module docstring.
_NPROC_PRELUDE = (
    "import os, resource, sys;"
    " n = int(sys.argv[1]);"
    " resource.setrlimit(resource.RLIMIT_NPROC, (n, n));"
    " os.execv(sys.argv[2], sys.argv[2:])"
)


class SandboxError(RuntimeError):
    """Base class for sandbox configuration and setup failures."""


class SandboxUnavailable(SandboxError):
    """No usable isolation backend: the caller reports "cannot run"."""


@dataclass(frozen=True)
class SandboxLimits:
    """Resource ceilings for one command."""

    timeout_seconds: float = 30.0
    memory_mb: int = 1024
    cpu_seconds: int = 60
    max_output_chars: int = 6000
    max_file_mb: int = 16
    max_processes: int = 128


@dataclass(frozen=True)
class SandboxResult:
    """What happened when the sandbox ran one command."""

    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int
    backend: str  # "bubblewrap" | "unshare" | ""
    network: bool  # always False; callers use it when explaining the guarantee
    error: str | None = None  # Chinese, tool-facing, only when ok is False


def backend_available() -> tuple[str, str]:
    """Return the (name, path) of the best backend, or ("", "") when none exists.

    bubblewrap is preferred: it also makes the filesystem read-only.
    """
    bwrap = shutil.which("bwrap")
    if bwrap:
        return "bubblewrap", bwrap
    unshare = shutil.which("unshare")
    if unshare:
        return "unshare", unshare
    return "", ""


def run(
    command: str,
    *,
    workdir: str | Path,
    limits: SandboxLimits = SandboxLimits(),
    backend: str = "auto",
    env: dict[str, str] | None = None,
    drop_to: tuple[int, int] | None = None,
) -> SandboxResult:
    """Run /bin/sh -c <command> in workdir inside the sandbox.

    workdir must already exist and be a directory (the caller owns the
    workspace); it is mounted read-write, everything else is read-only where the
    backend supports it. env is added to the fixed, scrubbed environment the
    child sees. Raises SandboxError for a missing workdir and
    SandboxUnavailable when no backend can be used.
    """
    workspace = _existing_directory(workdir)
    backend_name, backend_path = _resolve_backend(backend)
    python = _bootstrap_python()
    child_env = _child_env(workspace, env)
    argv = _build_argv(
        backend_name, backend_path, python, command, workspace, limits, child_env
    )
    if drop_to is not None:
        # Start the backend as the account the code belongs to, not as whoever
        # called us. Root inside its own userns maps to root outside, so a nested
        # bwrap (the suite tests one) has no uid to fall back to; run as her and
        # the namespace maps her, which is also the honest identity for her tests.
        argv = ["setpriv", f"--reuid={drop_to[0]}", f"--regid={drop_to[1]}", "--clear-groups", "--", *argv]
    # bwrap receives its environment through --setenv, so it starts from nothing;
    # the unshare fallback has no such flags and gets the scrubbed dict directly.
    popen_env = {} if backend_name == "bubblewrap" else dict(child_env)

    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            cwd=workspace,
            env=popen_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=_limit_preexec(limits),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SandboxUnavailable(f"沙盒启动失败：{exc}") from exc

    timed_out = False
    try:
        raw_out, raw_err = proc.communicate(timeout=limits.timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        _kill_process_group(proc)
        raw_out, raw_err = _collect_after_kill(proc, exc)

    duration_ms = int((time.monotonic() - started) * 1000)
    exit_code = proc.returncode
    full_stderr = raw_err.decode("utf-8", "replace")

    if not timed_out:
        startup_failure = _backend_startup_error(backend_name, exit_code, full_stderr)
        if startup_failure is not None:
            raise SandboxUnavailable(f"沙盒启动失败：{startup_failure}")

    error = _describe_failure(exit_code, timed_out, limits.timeout_seconds)
    return SandboxResult(
        ok=exit_code == 0 and not timed_out and error is None,
        exit_code=exit_code,
        stdout=_clip(raw_out.decode("utf-8", "replace"), limits.max_output_chars),
        stderr=_clip(full_stderr, limits.max_output_chars),
        timed_out=timed_out,
        duration_ms=duration_ms,
        backend=backend_name,
        network=False,
        error=error,
    )


def _existing_directory(workdir: str | Path) -> str:
    """Return the absolute workdir, or raise SandboxError. Never creates it."""
    path = Path(workdir).expanduser()
    try:
        path = path.resolve()
    except OSError:
        raise SandboxError(f"工作目录不存在：{path}") from None
    if not path.is_dir():
        raise SandboxError(f"工作目录不存在：{path}")
    return str(path)


def _resolve_backend(requested: str) -> tuple[str, str]:
    """Map the backend argument to a concrete (name, path)."""
    if requested == "auto":
        name, path = backend_available()
        if not name:
            raise SandboxUnavailable(
                "没有可用的沙盒后端（bubblewrap 或 unshare），拒绝在无隔离环境中执行命令"
            )
        return name, path
    if requested in ("bubblewrap", "unshare"):
        path = shutil.which("bwrap" if requested == "bubblewrap" else "unshare")
        if not path:
            raise SandboxUnavailable(f"沙盒后端不可用：{requested}")
        return requested, path
    raise SandboxError(f"未知的沙盒后端：{requested}")


def _bootstrap_python() -> str:
    """Absolute python3 used for the in-sandbox process-limit prelude."""
    for candidate in (shutil.which("python3"), "/usr/bin/python3"):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise SandboxUnavailable("找不到 python3，无法设置沙盒的进程数上限")


def _child_env(workspace: str, extra: dict[str, str] | None) -> dict[str, str]:
    """The complete environment the child will see."""
    env = dict(_BASE_ENV)
    env["HOME"] = workspace
    if extra:
        env.update({str(key): str(value) for key, value in extra.items()})
    return env


def _build_argv(
    backend_name: str,
    backend_path: str,
    python: str,
    command: str,
    workspace: str,
    limits: SandboxLimits,
    child_env: dict[str, str],
) -> list[str]:
    """Build the backend argv; the command is always handed to /bin/sh -c."""
    prelude = [
        python,
        "-c",
        _NPROC_PRELUDE,
        str(limits.max_processes),
        "/bin/sh",
        "-c",
        command,
    ]
    if backend_name == "unshare":
        # Network only: no read-only root, no tmpfs /tmp (see module docstring).
        return [backend_path, "-rn", "--"] + prelude

    argv = [
        backend_path,
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/tmp",
        "--bind", workspace, workspace,
        "--chdir", workspace,
    ]
    for key in sorted(child_env):
        argv += ["--setenv", key, child_env[key]]
    return argv + ["--"] + prelude


def _limit_preexec(limits: SandboxLimits):
    """Return a preexec_fn applying the limits that are safe to set before exec.

    It runs in the forked child, so it stays minimal: only setrlimit calls, no
    logging, no I/O. RLIMIT_NPROC is applied inside the sandbox instead.
    """

    def _apply() -> None:
        import resource

        cpu = int(limits.cpu_seconds)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        memory = int(limits.memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        max_file = int(limits.max_file_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (max_file, max_file))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

    return _apply


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the sandbox's whole process group (start_new_session made it one)."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:  # pragma: no cover - the child is ours
        proc.kill()


def _collect_after_kill(
    proc: subprocess.Popen[bytes], exc: subprocess.TimeoutExpired
) -> tuple[bytes, bytes]:
    """Drain the pipes after a kill without ever hanging the caller."""
    try:
        return proc.communicate(timeout=5.0)
    except subprocess.TimeoutExpired:
        # A stray descendant still holds the pipes; the sandbox is dead either way.
        proc.kill()
        proc.wait()
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        return exc.stdout or b"", exc.stderr or b""


def _describe_failure(
    exit_code: int | None, timed_out: bool, timeout_seconds: float
) -> str | None:
    """Chinese, tool-facing description; None when the command succeeded."""
    if timed_out:
        return f"命令超过 {timeout_seconds:g} 秒，已终止"
    if exit_code is None:
        return "命令没有正常结束"
    if exit_code < 0:
        return f"命令被信号 {-exit_code} 终止"
    if exit_code != 0:
        return f"命令执行失败，退出码 {exit_code}"
    return None


def _backend_startup_error(
    backend_name: str, exit_code: int | None, stderr: str
) -> str | None:
    """Detect the backend refusing to start, so we fail closed instead of blaming the command."""
    prefix = "unshare:" if backend_name == "unshare" else "bwrap:"
    if exit_code not in (None, 0) and stderr.lstrip().startswith(prefix):
        lines = stderr.strip().splitlines() or [prefix]
        return lines[-1]
    return None


def _clip(text: str, limit: int) -> str:
    """Cut text to limit characters and mark it when something was lost."""
    if len(text) <= limit:
        return text
    return text[: max(limit, 0)] + _TRUNCATION_MARKER
