"""apply.py 的测试。

真的跑 git 与 tar（它们便宜、无副作用），但 systemctl 一律走 FakeOps：
跑测试的这台机器上，yugang.service 可能就是活的，绝不能让测试去重启它。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from yugang import apply as apply_mod
from yugang.apply import (
    ApplyError,
    ApplyResult,
    apply_proposal,
    load_proposal,
    recompute_tier,
    write_result,
)

GIT = shutil.which("git")
needs_git = pytest.mark.skipif(GIT is None, reason="需要 git")
needs_tar = pytest.mark.skipif(shutil.which("tar") is None, reason="需要 tar")

GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}

BASE_FILES = {
    "README.md": "# YuGang\n\nlive copy\n",
    "docs/STATUS.md": "# STATUS\n\nold line\n",
    "docs/OLD.md": "# OLD\n\nwill be deleted in one test\n",
    "src/yugang/__init__.py": '"""YuGang."""\n',
    "tests/test_sample.py": "def test_ok():\n    assert 1 + 1 == 2\n",
}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def write_tree(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def force_rw(root: Path) -> None:
    """live 合入后是 0555/0444；测试收尾前放开，免得 tmp 目录清不掉。"""
    if not root.exists():
        return
    for current, dirs, files in os.walk(root):
        try:
            os.chmod(current, 0o755)
        except OSError:
            pass
        for name in dirs + files:
            path = Path(current) / name
            try:
                if path.is_symlink():
                    continue
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except OSError:
                pass


@pytest.fixture
def lane(tmp_path):
    live = tmp_path / "home" / "yugang"
    write_tree(live, BASE_FILES)
    home = tmp_path / "home" / ".yugang"
    home.mkdir(parents=True)
    (home / "proposals").mkdir()
    lane = SimpleNamespace(
        tmp=tmp_path,
        home=home,
        live=live,
        proposals=home / "proposals",
        results=home / "apply-results",
        candidates=tmp_path / "var" / "candidates",
        rollback=tmp_path / "var" / "rollback",
    )
    yield lane
    for path in (lane.live, lane.candidates, lane.results, lane.rollback):
        force_rw(path)


class FakeOps:
    """记下每一次副作用；health / wait_active 可以给一串答案。"""

    def __init__(self, *, active="active", wait_active=True, health=True, journal="journal line\n"):
        self.active = active
        self.wait_answers = list(wait_active) if isinstance(wait_active, (list, tuple)) else [wait_active]
        self.health_answers = list(health) if isinstance(health, (list, tuple)) else [health]
        self.journal = journal
        self.restart_calls = 0
        self.active_calls = 0
        self.wait_active_calls = 0
        self.health_calls = 0
        self.disk_mb = 4242

    def restart(self):
        self.restart_calls += 1

    def is_active(self):
        self.active_calls += 1
        return self.active

    def wait_active(self, seconds):
        self.wait_active_calls += 1
        return self._next(self.wait_answers, True)

    def health(self):
        self.health_calls += 1
        answer = self._next(self.health_answers, {"ok": False, "raw": ""})
        if isinstance(answer, dict):
            return dict(answer)
        return {"ok": bool(answer), "raw": f"health={answer}"}

    def journal_tail(self, lines=40):
        return self.journal

    def disk_free_mb(self, path):
        return self.disk_mb

    @staticmethod
    def _next(answers, fallback):
        if not answers:
            return fallback
        if len(answers) > 1:
            return answers.pop(0)
        return answers[0]


class FakeSandbox:
    """假沙盒：只记录调用参数，返回预设的 pytest 结果。"""

    def __init__(self, *, ok=True, exit_code=0, stdout="", stderr="", timed_out=False, error=None):
        self.result = SimpleNamespace(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=12,
            backend="bubblewrap",
            network=False,
            error=error,
        )
        self.calls = []

    def __call__(self, command, *, workdir, limits, env, backend, drop_to=None):
        self.calls.append(
            SimpleNamespace(
                command=command,
                workdir=workdir,
                limits=limits,
                env=env,
                backend=backend,
                drop_to=drop_to,
            )
        )
        return self.result


def git(args, cwd: Path):
    return subprocess.run(
        [GIT, "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
        env=GIT_ENV,
    )


def build_proposal(
    lane,
    changes,
    *,
    proposal_id="p_20261026T120000_abcdef",
    tier=None,
    files_override=None,
    extra_meta=None,
):
    """在提案目录里用真 git 造出 base commit、change commit、patch.diff 与 meta.json。"""
    work = lane.proposals / proposal_id
    work.mkdir(parents=True)
    mirror = work / "mirror"
    shutil.copytree(lane.live, mirror)
    git(["init", "-q"], mirror)
    git(["add", "-A"], mirror)
    git(["commit", "-q", "-m", "base"], mirror)
    base_commit = git(["rev-parse", "HEAD"], mirror).stdout.strip()

    for name, text in changes.items():
        target = mirror / name
        if text is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")

    git(["add", "-A"], mirror)
    git(["commit", "-q", "-m", "change"], mirror)
    commit = git(["rev-parse", "HEAD"], mirror).stdout.strip()
    patch = git(["diff", base_commit, commit], mirror).stdout
    files = [
        line
        for line in git(["diff", "--name-only", base_commit, commit], mirror).stdout.splitlines()
        if line
    ]

    meta = {
        "id": proposal_id,
        "created_ts": "2026-10-26T04:00:00+00:00",
        "author": "dafeiyu",
        "base_commit": base_commit,
        "commit": commit,
        "summary": "test change",
        "reason": "only for tests",
        "files": files if files_override is None else files_override,
        "tier": tier if tier is not None else recompute_tier(files),
        "tests": {"ok": True, "passed": 2, "failed": 0, "summary": "2 passed"},
    }
    if extra_meta:
        meta.update(extra_meta)
    (work / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (work / "patch.diff").write_text(patch, encoding="utf-8")
    shutil.rmtree(mirror)
    return meta


def write_meta(lane, proposal_id="p_20261026T120000_abcdef", **overrides):
    meta = {
        "id": proposal_id,
        "created_ts": "2026-10-26T04:00:00+00:00",
        "author": "dafeiyu",
        "base_commit": "a" * 40,
        "commit": "b" * 40,
        "summary": "s",
        "reason": "r",
        "files": ["docs/STATUS.md"],
        "tier": "auto",
        "tests": {"ok": True},
    }
    meta.update(overrides)
    directory = lane.proposals / proposal_id
    directory.mkdir(parents=True)
    (directory / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return directory


def run_apply(lane, proposal_id, ops, sandbox, **extra):
    kwargs = dict(
        home=lane.home,
        proposal_id=proposal_id,
        live_root=lane.live,
        proposals_dir=lane.proposals,
        results_dir=lane.results,
        rollback_dir=lane.rollback,
        candidates_dir=lane.candidates,
        ops=ops,
        sandbox_run=sandbox,
    )
    kwargs.update(extra)
    return apply_proposal(**kwargs)


def snapshot_path(lane, meta) -> Path:
    return lane.rollback / f"{meta['base_commit']}.tar.gz"


# --------------------------------------------------------------------------- #
# load_proposal
# --------------------------------------------------------------------------- #


def test_load_proposal_reads_meta(lane):
    write_meta(lane, files=["./docs/STATUS.md", "README.md"])

    data = load_proposal(lane.proposals, "p_20261026T120000_abcdef")

    assert data["id"] == "p_20261026T120000_abcdef"
    assert data["files"] == ["docs/STATUS.md", "README.md"]
    assert data["tier"] == "auto"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"id": "p_other"},
        {"commit": "deadbeef"},
        {"tier": "fast"},
        {"files": ["docs/../etc/passwd"]},
        {"files": "/etc/passwd"},
        {"files": ["docs/STATUS.md", "docs/STATUS.md"]},
        {"files": []},
        {"summary": ""},
    ],
)
def test_load_proposal_rejects_bad_meta(lane, kwargs):
    write_meta(lane, **kwargs)

    with pytest.raises(ApplyError):
        load_proposal(lane.proposals, "p_20261026T120000_abcdef")


def test_load_proposal_requires_fields(lane):
    directory = write_meta(lane)
    meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
    del meta["tier"]
    (directory / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(ApplyError) as excinfo:
        load_proposal(lane.proposals, "p_20261026T120000_abcdef")
    assert "tier" in str(excinfo.value)


def test_load_proposal_missing_dir(lane):
    with pytest.raises(ApplyError):
        load_proposal(lane.proposals, "p_20260101T000000_000000")


def test_recompute_tier_rules():
    assert recompute_tier(["docs/a.md"]) == "auto"
    assert recompute_tier(["README.md"]) == "auto"
    assert recompute_tier(["docs/a.md", "README.md"]) == "auto"
    assert recompute_tier(["docs/a.md", "src/yugang/x.py"]) == "review"
    assert recompute_tier(["src/yugang/x.py"]) == "review"
    assert recompute_tier(["docsx/a.md"]) == "review"
    assert recompute_tier([]) == "review"


def test_write_result_is_json_0644(tmp_path):
    result = ApplyResult(
        id="p_20261026T120000_abcdef",
        ok=True,
        stage="done",
        applied_commit="c" * 40,
        rolled_back=False,
        error="",
        env={"python": "3.14.0", "health_ok": True},
        ts="2026-10-26T04:00:00+00:00",
    )

    target = write_result(tmp_path / "apply-results", result)

    assert target == tmp_path / "apply-results" / "p_20261026T120000_abcdef.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["id"] == result.id
    assert payload["applied_commit"] == "c" * 40
    assert payload["env"]["health_ok"] is True
    assert payload["ts"] == "2026-10-26T04:00:00+00:00"


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #


@needs_git
@needs_tar
def test_apply_auto_happy_path(lane):
    meta = build_proposal(
        lane,
        {"docs/STATUS.md": "# STATUS\n\nnew line\n"},
        tier="auto",
    )
    # 上一次留下的候选副本必须被清空重建。
    stale = lane.candidates / meta["id"] / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")

    ops = FakeOps(health=True)
    sandbox = FakeSandbox(exit_code=0, stdout="2 passed in 0.11s\n")
    now = datetime(2026, 10, 26, 4, 30, 0, tzinfo=timezone.utc)

    result = run_apply(lane, meta["id"], ops, sandbox, now_fn=lambda: now)

    assert result.ok is True
    assert result.stage == "done"
    assert result.applied_commit == meta["commit"]
    assert result.rolled_back is False
    assert result.error == ""
    assert result.ts == "2026-10-26T04:30:00+00:00"
    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == "# STATUS\n\nnew line\n"
    assert ops.restart_calls == 1
    assert ops.wait_active_calls == 1

    env = result.env
    assert env["health_ok"] is True
    assert env["unit_active"] == "active"
    assert env["base_commit"] == meta["base_commit"]
    assert env["commit"] == meta["commit"]
    assert env["pytest_summary"] == "2 passed in 0.11s"
    assert env["failed_tests"] == []
    assert env["disk_free_mb"] == 4242
    assert env["journal_tail"] == "journal line"
    assert env["python"] == sys.version.split()[0]
    assert env["duration_s"] >= 0

    # 沙盒被按约定调用。
    assert len(sandbox.calls) == 1
    call = sandbox.calls[0]
    assert call.command.startswith("/opt/yugang-venv/bin/python ")
    assert "-m pytest -x -q -p no:cacheprovider" in call.command
    assert call.workdir == str(lane.candidates / meta["id"])
    assert call.limits.timeout_seconds == 600.0
    assert call.env == {"PYTHONDONTWRITEBYTECODE": "1"}
    assert call.backend == "auto"

    # 候选副本打了 patch，且有临时 git 树；live 里不能有 .git。
    candidate = lane.candidates / meta["id"]
    assert (candidate / "docs/STATUS.md").read_text(encoding="utf-8") == "# STATUS\n\nnew line\n"
    assert (candidate / ".git").is_dir()
    assert not (candidate / "stale.txt").exists()
    assert not (lane.live / ".git").exists()

    # 快照与只读位。
    snapshot = snapshot_path(lane, meta)
    assert snapshot.is_file()
    assert snapshot.stat().st_size > 0
    assert stat.S_IMODE((lane.live / "docs/STATUS.md").stat().st_mode) == 0o444
    assert stat.S_IMODE((lane.live / "docs").stat().st_mode) == 0o555

    # 结果落盘。
    written = lane.results / f"{meta['id']}.json"
    assert written.is_file()
    assert stat.S_IMODE(written.stat().st_mode) == 0o644
    payload = json.loads(written.read_text(encoding="utf-8"))
    assert payload["ok"] is True
    assert payload["stage"] == "done"
    assert payload["applied_commit"] == meta["commit"]
    assert payload["env"]["health_ok"] is True


@needs_git
@needs_tar
def test_apply_deletes_file(lane):
    meta = build_proposal(lane, {"docs/OLD.md": None})
    assert meta["files"] == ["docs/OLD.md"]

    sandbox = FakeSandbox(stdout="1 passed in 0.01s\n")
    result = run_apply(lane, meta["id"], FakeOps(), sandbox)

    assert result.ok is True
    assert not (lane.live / "docs/OLD.md").exists()
    assert not (lane.candidates / meta["id"] / "docs/OLD.md").exists()
    assert (lane.live / "docs/STATUS.md").is_file()


# --------------------------------------------------------------------------- #
# 失败：测试
# --------------------------------------------------------------------------- #


@needs_git
def test_apply_test_failure_leaves_live_alone(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nbroken\n"})
    before = (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")
    ops = FakeOps()
    sandbox = FakeSandbox(
        ok=False,
        exit_code=1,
        stdout=(
            "FAILED tests/test_sample.py::test_ok - AssertionError: boom\n"
            "\n"
            "1 failed, 1 passed in 0.20s\n"
        ),
    )

    result = run_apply(lane, meta["id"], ops, sandbox)

    assert result.ok is False
    assert result.stage == "test"
    assert result.rolled_back is False
    assert result.applied_commit is None
    assert "FAILED tests/test_sample.py::test_ok - AssertionError: boom" in result.error
    assert result.env["failed_tests"] == [
        "tests/test_sample.py::test_ok - AssertionError: boom"
    ]
    assert result.env["pytest_summary"] == "1 failed, 1 passed in 0.20s"
    assert result.env["health_ok"] is True  # 只读探了一下，没重启

    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == before
    assert ops.restart_calls == 0
    assert not snapshot_path(lane, meta).exists()
    payload = json.loads((lane.results / f"{meta['id']}.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "test"
    assert payload["rolled_back"] is False


@needs_git
def test_apply_sandbox_unavailable_is_test_failure(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nbroken\n"})
    ops = FakeOps()

    def boom(command, *, workdir, limits, env, backend):
        raise RuntimeError("bwrap 不在")

    result = run_apply(lane, meta["id"], ops, boom)

    assert result.ok is False
    assert result.stage == "test"
    assert "测试没有跑起来" in result.error
    assert "bwrap 不在" in result.error
    assert ops.restart_calls == 0
    assert result.rolled_back is False


# --------------------------------------------------------------------------- #
# 失败：审批
# --------------------------------------------------------------------------- #


@needs_git
def test_apply_review_without_approval(lane):
    meta = build_proposal(lane, {"src/yugang/__init__.py": '"""changed."""\n'})
    assert meta["tier"] == "review"
    before = (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8")
    ops = FakeOps()

    result = run_apply(lane, meta["id"], ops, FakeSandbox())

    assert result.ok is False
    assert result.stage == "approve"
    assert result.rolled_back is False
    assert result.applied_commit is None
    assert result.error == apply_mod.REVIEW_MESSAGE
    assert ops.restart_calls == 0
    assert (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8") == before
    assert not snapshot_path(lane, meta).exists()
    payload = json.loads((lane.results / f"{meta['id']}.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "approve"
    assert payload["ok"] is False


@needs_git
@needs_tar
def test_apply_review_with_root_approved_marker(lane):
    meta = build_proposal(lane, {"src/yugang/__init__.py": '"""approved."""\n'})
    (lane.proposals / meta["id"] / "APPROVED").write_text("", encoding="utf-8")

    result = run_apply(lane, meta["id"], FakeOps(), FakeSandbox(stdout="2 passed\n"))

    assert result.ok is True
    assert result.stage == "done"
    assert (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8") == '"""approved."""\n'


@needs_git
@needs_tar
@pytest.mark.parametrize("flag", ["approve", "allow_review"])
def test_apply_review_with_cli_flag(lane, flag):
    meta = build_proposal(
        lane, {"src/yugang/__init__.py": '"""flagged."""\n'}, proposal_id=f"p_20261026T120001_abcde{flag[0]}"
    )

    result = run_apply(lane, meta["id"], FakeOps(), FakeSandbox(stdout="2 passed\n"), **{flag: True})

    assert result.ok is True
    assert (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8") == '"""flagged."""\n'


@needs_git
def test_apply_tier_recompute_is_stricter(lane):
    # meta 自称 auto，但 patch 动了 src/ —— 按 spec §3 必须降成 review。
    meta = build_proposal(
        lane,
        {"src/yugang/__init__.py": '"""sneaky."""\n'},
        tier="auto",
    )
    before = (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8")
    ops = FakeOps()

    result = run_apply(lane, meta["id"], ops, FakeSandbox())

    assert result.stage == "approve"
    assert result.ok is False
    assert result.rolled_back is False
    assert result.error == apply_mod.REVIEW_MESSAGE
    assert ops.restart_calls == 0
    assert (lane.live / "src/yugang/__init__.py").read_text(encoding="utf-8") == before


# --------------------------------------------------------------------------- #
# 失败：换装之后 —— 必须回退
# --------------------------------------------------------------------------- #


@needs_git
@needs_tar
def test_apply_health_failure_rolls_back(lane, monkeypatch):
    monkeypatch.setattr(apply_mod, "HEALTH_TIMEOUT_SECONDS", 0.0)
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nbroken health\n"})
    before = (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")
    ops = FakeOps(
        health=[
            {"ok": False, "raw": "worker_alive=false problems=[memory]"},
            {"ok": True, "raw": "ok"},
        ]
    )

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "health"
    assert result.rolled_back is True
    assert result.applied_commit is None
    assert ops.restart_calls == 2
    assert result.env["health_ok"] is False
    assert "worker_alive=false" in result.error
    assert "broken health" not in (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")
    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == before
    # 回退后 live 仍然是只读的。
    assert stat.S_IMODE((lane.live / "docs/STATUS.md").stat().st_mode) == 0o444
    payload = json.loads((lane.results / f"{meta['id']}.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "health"
    assert payload["rolled_back"] is True


@needs_git
@needs_tar
def test_apply_restart_failure_rolls_back(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nnever starts\n"})
    before = (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")
    ops = FakeOps(active="failed", wait_active=[False, True], health=True)

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "restart"
    assert result.rolled_back is True
    assert ops.restart_calls == 2
    assert result.env["health_ok"] is False
    assert "没有 active" in result.error
    assert "is-active=failed" in result.error
    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == before


@needs_git
@needs_tar
def test_apply_unit_stays_failed_rollback_also_fails(lane):
    # 第一次没起来；回退后又重启了一次，还是起不来 —— 回退也失败。
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nfailed forever\n"})
    ops = FakeOps(active="failed", wait_active=False, health=True)

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "rollback"
    assert result.rolled_back is False
    assert "回退也失败" in result.error
    assert "回退后服务没有起来" in result.error
    assert ops.restart_calls == 2
    assert result.env["health_ok"] is False


@needs_git
@needs_tar
def test_apply_rollback_failure_is_reported(lane, monkeypatch):
    monkeypatch.setattr(apply_mod, "HEALTH_TIMEOUT_SECONDS", 0.0)
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\ndoomed\n"})
    ops = FakeOps(health=False)

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "rollback"
    assert result.rolled_back is False
    assert result.applied_commit is None
    assert "回退也失败" in result.error
    assert ops.restart_calls == 2
    payload = json.loads((lane.results / f"{meta['id']}.json").read_text(encoding="utf-8"))
    assert payload["stage"] == "rollback"
    assert payload["rolled_back"] is False


@needs_git
@needs_tar
def test_apply_swap_failure_rolls_back(lane, monkeypatch):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nswap dies\n"})
    before = (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")

    def boom(candidate, live_root):
        raise ApplyError("模拟 rsync 失败")

    monkeypatch.setattr(apply_mod, "_swap", boom)
    ops = FakeOps()

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "swap"
    assert result.rolled_back is True
    assert "模拟 rsync 失败" in result.error
    assert ops.restart_calls == 1
    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == before


@needs_git
def test_apply_snapshot_failure(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nno snapshot\n"})
    before = (lane.live / "docs/STATUS.md").read_text(encoding="utf-8")
    lane.rollback.parent.mkdir(parents=True, exist_ok=True)
    lane.rollback.write_text("not a directory", encoding="utf-8")
    ops = FakeOps()

    result = run_apply(lane, meta["id"], ops, FakeSandbox(stdout="2 passed\n"))

    assert result.ok is False
    assert result.stage == "snapshot"
    assert result.rolled_back is False
    assert ops.restart_calls == 0
    assert (lane.live / "docs/STATUS.md").read_text(encoding="utf-8") == before
    assert (lane.results / f"{meta['id']}.json").is_file()


# --------------------------------------------------------------------------- #
# 失败：verify
# --------------------------------------------------------------------------- #


@needs_git
def test_apply_file_list_mismatch_is_verify_failure(lane):
    meta = build_proposal(
        lane,
        {"docs/STATUS.md": "# STATUS\n\nx\n"},
        files_override=["README.md"],
    )
    ops = FakeOps()
    before = (lane.live / "README.md").read_text(encoding="utf-8")

    result = run_apply(lane, meta["id"], ops, FakeSandbox())

    assert result.ok is False
    assert result.stage == "verify"
    assert result.rolled_back is False
    assert "不一致" in result.error
    assert ops.restart_calls == 0
    assert (lane.live / "README.md").read_text(encoding="utf-8") == before
    assert result.env["base_commit"] == meta["base_commit"]


@needs_git
def test_apply_bad_commit_is_verify_failure(lane):
    meta = build_proposal(
        lane,
        {"docs/STATUS.md": "# STATUS\n\ny\n"},
        extra_meta={"commit": "not-a-sha"},
    )
    ops = FakeOps()

    result = run_apply(lane, meta["id"], ops, FakeSandbox())

    assert result.stage == "verify"
    assert result.ok is False
    assert "commit" in result.error
    assert ops.restart_calls == 0


@needs_git
def test_apply_missing_patch_is_verify_failure(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# STATUS\n\nz\n"})
    (lane.proposals / meta["id"] / "patch.diff").write_text("", encoding="utf-8")

    result = run_apply(lane, meta["id"], FakeOps(), FakeSandbox())

    assert result.stage == "verify"
    assert "patch.diff" in result.error


# --------------------------------------------------------------------------- #
# 退路与默认实现
# --------------------------------------------------------------------------- #


def test_error_clip_keeps_head_and_tail():
    text = "FAILED tests/a.py::test_x - boom\n" + ("x" * 5000) + "\nlast traceback line"

    clipped = apply_mod._clip_error(text)

    assert len(clipped) <= apply_mod.ERROR_LIMIT
    assert clipped.startswith("FAILED tests/a.py::test_x - boom")
    assert clipped.endswith("last traceback line")


def test_sync_tree_fallback_matches_rsync_semantics(tmp_path):
    src = tmp_path / "candidate"
    dst = tmp_path / "live"
    write_tree(src, {"a.txt": "new", "docs/b.md": "b", ".git/config": "[core]\n"})
    write_tree(dst, {"a.txt": "old", "stale.txt": "stale", "olddir/x": "x", ".git/config": "[core]\n"})

    apply_mod._sync_tree(src, dst)

    assert (dst / "a.txt").read_text(encoding="utf-8") == "new"
    assert (dst / "docs/b.md").read_text(encoding="utf-8") == "b"
    assert not (dst / "stale.txt").exists()
    assert not (dst / "olddir").exists()
    assert (dst / ".git/config").is_file()  # rsync --exclude=.git 也不会删 dest 的 .git


def test_default_ops_health_tolerates_missing_env_file(tmp_path, monkeypatch):
    ops = apply_mod.SystemdOps(env_file=tmp_path / "nope.env", health_url="http://127.0.0.1:1/health")
    monkeypatch.setattr(apply_mod.urllib.request, "urlopen", _raise_connection_refused)

    payload = ops.health()

    assert payload["ok"] is False
    assert payload["raw"]


def _raise_connection_refused(*args, **kwargs):
    raise ConnectionRefusedError("connection refused")


def test_default_ops_never_raises_for_env_probes(monkeypatch):
    ops = apply_mod.SystemdOps()

    monkeypatch.setattr(apply_mod, "_run_command", _broken_run)
    assert ops.is_active() == ""
    assert ops.journal_tail(5) == ""

    monkeypatch.setattr(apply_mod.shutil, "disk_usage", _broken_disk)
    assert ops.disk_free_mb("/nonexistent") == 0


def _broken_run(*args, **kwargs):
    raise FileNotFoundError("systemctl 不在")


def _broken_disk(path):
    raise OSError("no such path")


# --------------------------------------------------------------------------- #
# 跑测试的身份
# --------------------------------------------------------------------------- #


def test_tests_run_as_her_when_drop_to_is_given(lane):
    """合入进程是 root；测试要在她的 uid 下跑，否则套娃的 bwrap 起不来。"""
    meta = build_proposal(lane, {"docs/STATUS.md": "# status\n# 改过\n"})
    ops = FakeOps()
    sandbox = FakeSandbox(ok=True)

    result = run_apply(lane, meta["id"], ops, sandbox, drop_to=(1001, 1001))

    assert result.ok is True
    assert sandbox.calls[0].drop_to == (1001, 1001)


def test_tests_get_no_drop_to_by_default(lane):
    meta = build_proposal(lane, {"docs/STATUS.md": "# status\n# 改过\n"})
    sandbox = FakeSandbox(ok=True)

    run_apply(lane, meta["id"], FakeOps(), sandbox)

    assert sandbox.calls[0].drop_to is None


def test_candidate_is_opened_for_reading(tmp_path):
    from yugang.apply import _open_for_reading

    root = tmp_path / "candidate"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    root.chmod(0o700)
    (root / "src").chmod(0o700)
    (root / "src" / "a.py").chmod(0o600)

    _open_for_reading(root)

    assert root.stat().st_mode & 0o777 == 0o755
    assert (root / "src").stat().st_mode & 0o777 == 0o755
    assert (root / "src" / "a.py").stat().st_mode & 0o777 == 0o644



def test_default_runner_forwards_drop_to(monkeypatch):
    """_run_tests 给的 drop_to 必须真的传到沙盒；上一次它在这里丢了，整个提案都过不了。"""
    from yugang import apply as apply_mod

    seen = {}

    def fake(command, *, workdir, limits, env, backend, drop_to=None):
        seen["drop_to"] = drop_to
        return SimpleNamespace(
            ok=True,
            exit_code=0,
            stdout="1 passed",
            stderr="",
            timed_out=False,
            duration_ms=1,
            backend="bubblewrap",
            network=False,
            error=None,
        )

    monkeypatch.setattr("yugang.world.sandbox.run", fake)
    apply_mod._default_sandbox_run(
        "pytest", workdir="/tmp", limits=None, env={}, backend="auto", drop_to=(1001, 1001)
    )

    assert seen["drop_to"] == (1001, 1001)
