"""Tests for the command sandbox: no network, read-only root, caps, fail closed.

The filesystem checks need bubblewrap (the unshare fallback isolates the network
only), so they skip on hosts without bwrap. Everything else skips cleanly when no
backend exists at all -- absence of an isolation backend is a deployment gap, not
a code failure, and the module itself refuses to run rather than running raw.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from yugang.world import sandbox

BACKEND_NAME, BACKEND_PATH = sandbox.backend_available()
HAS_BACKEND = bool(BACKEND_NAME)
HAS_BWRAP = shutil.which("bwrap") is not None
HAS_UNSHARE = shutil.which("unshare") is not None

needs_backend = pytest.mark.skipif(
    not HAS_BACKEND, reason="no sandbox backend (bubblewrap or unshare) available"
)
needs_bwrap = pytest.mark.skipif(
    not HAS_BWRAP,
    reason="bubblewrap not available, so the read-only filesystem cannot be checked",
)
needs_unshare = pytest.mark.skipif(
    not HAS_UNSHARE, reason="unshare not available"
)

TRUNCATION_MARKER = "\n…[已截断]"

# Connecting to a raw IP needs no DNS; inside a network namespace it fails at once.
NETWORK_PROBE = (
    "python3 -c 'import socket; "
    'socket.create_connection(("1.1.1.1", 443), 2)\''
)


def test_backend_available_prefers_bubblewrap() -> None:
    name, path = sandbox.backend_available()
    if HAS_BWRAP:
        assert name == "bubblewrap"
        assert Path(path).is_file()
    elif HAS_UNSHARE:
        assert name == "unshare"
        assert Path(path).is_file()
    else:
        assert (name, path) == ("", "")


def test_error_types() -> None:
    assert issubclass(sandbox.SandboxError, RuntimeError)
    assert issubclass(sandbox.SandboxUnavailable, sandbox.SandboxError)


@needs_backend
def test_run_echo(tmp_path: Path) -> None:
    result = sandbox.run("echo hello from the sandbox", workdir=tmp_path)
    assert result.ok is True
    assert result.exit_code == 0
    assert "hello from the sandbox" in result.stdout
    assert result.stderr == ""
    assert result.timed_out is False
    assert result.backend == BACKEND_NAME
    assert result.network is False
    assert result.error is None
    assert result.duration_ms >= 0


@needs_bwrap
def test_no_network(tmp_path: Path) -> None:
    result = sandbox.run(
        NETWORK_PROBE,
        workdir=tmp_path,
        limits=sandbox.SandboxLimits(timeout_seconds=10),
    )
    assert result.ok is False
    assert result.exit_code not in (0, None)
    # The probe ran (its traceback is in stderr) and could not reach the address.
    assert "unreachable" in result.stderr.lower()


@needs_bwrap
def test_filesystem_is_read_only_except_workdir(tmp_path: Path) -> None:
    outside_target = Path("/etc/yugang-sandbox-write-test")
    assert not outside_target.exists()

    readable = sandbox.run("cat /etc/hostname", workdir=tmp_path)
    assert readable.ok is True
    assert readable.stdout.strip()

    write_outside = sandbox.run(f"printf x > {outside_target}", workdir=tmp_path)
    assert write_outside.ok is False
    assert write_outside.exit_code != 0
    assert "Read-only file system" in write_outside.stderr
    assert not outside_target.exists()

    write_inside = sandbox.run("printf 'hello file' > inside.txt", workdir=tmp_path)
    assert write_inside.ok is True
    assert (tmp_path / "inside.txt").read_text(encoding="utf-8") == "hello file"


@needs_backend
def test_environment_is_scrubbed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "leak-me")
    monkeypatch.setenv("YUGANG_QQ_APP_SECRET", "leak-me-too")
    probe = (
        "python3 -c 'import os; "
        'print(os.environ.get("DEEPSEEK_API_KEY", "")); '
        'print(os.environ.get("YUGANG_QQ_APP_SECRET", "")); '
        'print(os.environ.get("YUGANG_ALLOWED", ""))\''
    )
    result = sandbox.run(probe, workdir=tmp_path, env={"YUGANG_ALLOWED": "yes"})
    assert result.ok is True
    assert result.stdout.splitlines() == ["", "", "yes"]


@needs_backend
def test_timeout_kills_the_command(tmp_path: Path) -> None:
    started = time.monotonic()
    result = sandbox.run(
        "sleep 5",
        workdir=tmp_path,
        limits=sandbox.SandboxLimits(timeout_seconds=1),
    )
    elapsed = time.monotonic() - started
    assert result.ok is False
    assert result.timed_out is True
    assert result.exit_code != 0
    assert result.error is not None and "超过" in result.error
    assert elapsed < 4.0
    assert result.duration_ms < 4000


@needs_backend
def test_timeout_kills_nested_background_processes(tmp_path: Path) -> None:
    started = time.monotonic()
    result = sandbox.run(
        "sh -c 'sleep 30 & wait'",
        workdir=tmp_path,
        limits=sandbox.SandboxLimits(timeout_seconds=1),
    )
    elapsed = time.monotonic() - started
    assert result.ok is False
    assert result.timed_out is True
    assert elapsed < 4.0


@needs_backend
def test_output_is_truncated(tmp_path: Path) -> None:
    result = sandbox.run("seq 1 5000", workdir=tmp_path)
    assert result.ok is True
    assert result.stdout.endswith(TRUNCATION_MARKER)
    assert len(result.stdout) <= 6000 + len(TRUNCATION_MARKER)


@needs_backend
def test_missing_workdir_raises(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    with pytest.raises(sandbox.SandboxError, match="工作目录不存在"):
        sandbox.run("true", workdir=missing)
    assert not missing.exists()  # the caller owns the workspace; we never create it

    plain_file = tmp_path / "plain-file"
    plain_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(sandbox.SandboxError, match="工作目录不存在"):
        sandbox.run("true", workdir=plain_file)


def test_fails_closed_without_a_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert sandbox.backend_available() == ("", "")
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.run("echo should-not-run", workdir=tmp_path)


@needs_unshare
def test_unshare_fallback_isolates_the_network(tmp_path: Path) -> None:
    result = sandbox.run(
        NETWORK_PROBE,
        workdir=tmp_path,
        backend="unshare",
        limits=sandbox.SandboxLimits(timeout_seconds=10),
    )
    assert result.backend == "unshare"
    assert result.ok is False
    assert result.exit_code not in (0, None)
    assert "unreachable" in result.stderr.lower()
    assert result.network is False
