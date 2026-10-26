"""SOURCE_* 六个工具：读、写、跑测试、提提案；都走沙箱假件，不碰真 bwrap。"""

from __future__ import annotations

import json
import re
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from yugang.selfwork import NAMES, SOURCE_TOOL_SCHEMAS, SelfToolbox, SelfWorkPaths
from yugang.selfwork.mirror import MIRROR_NOT_READY


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.events: list[dict] = []

    def record_world_call(
        self,
        day,
        tool,
        *,
        kind="read",
        ok=True,
        detail="",
        chars=0,
        ms=0,
        ts=None,
    ):
        self.calls.append(
            {
                "day": day,
                "tool": tool,
                "kind": kind,
                "ok": ok,
                "detail": detail,
                "chars": chars,
                "ms": ms,
            }
        )

    def append_event(self, source, kind, content, *, meta=None, **kwargs):
        self.events.append({"source": source, "kind": kind, "content": content, "meta": meta})
        return SimpleNamespace(id="evt")


class FakeRunner:
    def __init__(
        self,
        *,
        ok=True,
        exit_code=0,
        stdout="",
        stderr="",
        timed_out=False,
        duration_ms=5,
        backend="fake",
    ) -> None:
        self.calls: list[dict] = []
        self.result = SimpleNamespace(
            ok=ok,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            duration_ms=duration_ms,
            backend=backend,
        )

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, "kwargs": kwargs})
        return self.result


@pytest.fixture()
def live(tmp_path: Path) -> Path:
    root = tmp_path / "live"
    (root / "src" / "yugang").mkdir(parents=True)
    (root / "src" / "yugang" / "ids.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_ids.py").write_text("def test_ids():\n    assert True\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "STATUS.md").write_text("# status\n", encoding="utf-8")
    (root / "audio").mkdir()
    (root / "audio" / "note.txt").write_text("noise\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    (root / "README.md").write_text("# fake\n", encoding="utf-8")
    return root


@pytest.fixture()
def paths(tmp_path: Path, live: Path) -> SelfWorkPaths:
    # venv_python 指向当前解释器：只要它存在，source_test 就会走到注入的假沙箱。
    return SelfWorkPaths(home=tmp_path / "home", source_root=live, venv_python=sys.executable)


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def box(paths: SelfWorkPaths, store: FakeStore) -> SelfToolbox:
    toolbox = SelfToolbox(paths, store, sandbox_runner=FakeRunner())
    toolbox.mirror.ensure()
    return toolbox


def _box_with_runner(paths: SelfWorkPaths, store, runner: FakeRunner) -> SelfToolbox:
    toolbox = SelfToolbox(paths, store, sandbox_runner=runner)
    toolbox.mirror.ensure()
    return toolbox


# -- 表与分发 -------------------------------------------------------------


def test_names_are_the_six_source_tools() -> None:
    assert NAMES == (
        "source_list",
        "source_read",
        "source_write",
        "source_log",
        "source_test",
        "source_propose",
    )
    assert not any(name.startswith("self_") for name in NAMES)


def test_schemas_follow_the_world_tool_shape() -> None:
    assert len(SOURCE_TOOL_SCHEMAS) == 6
    for schema in SOURCE_TOOL_SCHEMAS:
        assert schema["type"] == "function"
        function = schema["function"]
        assert function["name"] in NAMES
        assert function["description"]
        parameters = function["parameters"]
        assert parameters["type"] == "object"
        assert isinstance(parameters["properties"], dict)
        assert isinstance(parameters["required"], list)
    by_name = {schema["function"]["name"]: schema["function"] for schema in SOURCE_TOOL_SCHEMAS}
    assert by_name["source_list"]["parameters"]["required"] == []
    assert by_name["source_read"]["parameters"]["required"] == ["path"]
    assert by_name["source_write"]["parameters"]["required"] == ["path", "content", "message"]
    assert by_name["source_log"]["parameters"]["required"] == []
    assert by_name["source_test"]["parameters"]["required"] == []
    assert by_name["source_propose"]["parameters"]["required"] == ["summary", "reason"]


def test_toolbox_exposes_schemas_and_names(box: SelfToolbox) -> None:
    assert box.schemas() == SOURCE_TOOL_SCHEMAS
    assert box.names() == set(NAMES)


def test_unknown_tool_is_refused(box: SelfToolbox) -> None:
    result = box.run("self_read", {"path": "SELF.md"})
    assert result["ok"] is False
    assert result["tool"] == "self_read"
    assert "没有这个工具" in result["error"]


def test_arguments_accept_a_json_string(box: SelfToolbox) -> None:
    result = box.run("source_read", json.dumps({"path": "docs/STATUS.md"}))
    assert result["ok"] is True
    assert result["text"] == "# status\n"


def test_arguments_reject_bad_json_and_non_objects(box: SelfToolbox) -> None:
    bad = box.run("source_read", "{not json")
    assert bad["ok"] is False
    assert "JSON" in bad["error"]
    not_object = box.run("source_read", "[1, 2]")
    assert not_object["ok"] is False
    assert not_object["error"]


# -- 镜像没建好 -----------------------------------------------------------


def test_all_tools_say_operator_must_build_the_mirror(paths: SelfWorkPaths) -> None:
    toolbox = SelfToolbox(paths, None, sandbox_runner=FakeRunner())
    cases = [
        ("source_list", {}),
        ("source_read", {"path": "docs/STATUS.md"}),
        ("source_log", {}),
        ("source_write", {"path": "docs/STATUS.md", "content": "x\n", "message": "改"}),
        ("source_test", {}),
        ("source_propose", {"summary": "改", "reason": "因为"}),
    ]
    for name, arguments in cases:
        result = toolbox.run(name, arguments)
        assert result["ok"] is False, name
        assert result["error"] == MIRROR_NOT_READY, name


# -- 只读三件套 -----------------------------------------------------------


def test_list_read_log(box: SelfToolbox) -> None:
    listed = box.run("source_list", {"path": "", "max": 2})
    assert listed["ok"] is True
    assert listed["count"] == 2
    assert len(listed["entries"]) == 2

    read = box.run("source_read", {"path": "docs/STATUS.md"})
    assert read["ok"] is True
    assert read["chars"] == 9
    assert read["text"] == "# status\n"
    assert read["truncated"] is False

    log = box.run("source_log", {"limit": 1})
    assert log["ok"] is True
    assert log["count"] == 1
    assert "从 live" in log["entries"][0]["message"]


def test_read_only_tools_never_commit(box: SelfToolbox, store: FakeStore) -> None:
    box.run("source_list", {})
    box.run("source_read", {"path": "README.md"})
    box.run("source_log", {})
    assert box.mirror.status()["commits"] == 1
    assert [call["kind"] for call in store.calls] == ["read", "read", "read"]
    assert all(call["ok"] for call in store.calls)


def test_read_missing_file_is_an_error_result(box: SelfToolbox) -> None:
    result = box.run("source_read", {"path": "docs/nope.md"})
    assert result["ok"] is False
    assert "error" in result


# -- 写入 -----------------------------------------------------------------


def test_write_commits_and_leaves_an_l1(box: SelfToolbox, store: FakeStore) -> None:
    content = "# status\n\n更准一点\n"
    result = box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": content, "message": "更新状态"},
    )
    assert result["ok"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", result["commit"])
    assert result["short"] == result["commit"][:7]
    assert result["path"] == "docs/STATUS.md"
    assert result["bytes"] == len(content.encode("utf-8"))
    assert result["l1"] is True
    assert box.mirror.head() == result["commit"]

    event = store.events[-1]
    assert event["source"] == "ai-observation"
    assert event["kind"] == "action"
    assert event["content"] == f"我改了自己的 docs/STATUS.md：更新状态（{result['short']}）"
    assert event["meta"]["path"] == "docs/STATUS.md"
    assert event["meta"]["commit"] == result["commit"]
    assert store.calls[-1]["tool"] == "source_write"
    assert store.calls[-1]["kind"] == "act"
    assert store.calls[-1]["ok"] is True


def test_write_works_without_a_store(paths: SelfWorkPaths) -> None:
    toolbox = SelfToolbox(paths, None, sandbox_runner=FakeRunner())
    toolbox.mirror.ensure()
    result = toolbox.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n没有记忆库\n", "message": "没记忆库"},
    )
    assert result["ok"] is True
    assert result["l1"] is False


@pytest.mark.parametrize(
    "arguments, needle",
    [
        ({"path": "", "content": "x\n", "message": "改"}, "路径"),
        ({"path": "docs/STATUS.md", "content": "x\n", "message": "  "}, "提交信息"),
        ({"path": "docs/STATUS.md", "content": 1, "message": "改"}, "文本"),
        ({"path": "../escape.md", "content": "x\n", "message": "越界"}, "路径"),
    ],
)
def test_write_validates_input(box: SelfToolbox, arguments: dict, needle: str) -> None:
    result = box.run("source_write", arguments)
    assert result["ok"] is False
    assert needle in result["error"]


def test_write_rejects_too_large_content(box: SelfToolbox, paths: SelfWorkPaths) -> None:
    result = box.run(
        "source_write",
        {"path": "docs/big.md", "content": "x" * (paths.max_file_bytes + 1), "message": "太大"},
    )
    assert result["ok"] is False
    assert "太大了" in result["error"]


def test_write_without_a_change_is_refused(box: SelfToolbox) -> None:
    result = box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n", "message": "没有变化"},
    )
    assert result["ok"] is False
    assert "内容没有变化" in result["error"]


# -- 跑测试 ---------------------------------------------------------------


def test_test_parses_pytest_output(paths: SelfWorkPaths, store: FakeStore) -> None:
    runner = FakeRunner(
        stdout="\nFAILED tests/test_ids.py::test_ids - assert False\n3 passed, 1 failed, 2 skipped in 0.42s\n",
        exit_code=1,
        ok=False,
    )
    toolbox = _box_with_runner(paths, store, runner)
    result = toolbox.run("source_test", {"timeout_seconds": 30})
    assert result["ok"] is False
    assert result["passed"] == 3
    assert result["failed"] == 1
    assert result["skipped"] == 2
    assert result["errors"] == 0
    assert result["failed_tests"] == ["tests/test_ids.py::test_ids - assert False"]
    assert result["summary"] == "3 passed, 1 failed, 2 skipped in 0.42s"
    assert result["timed_out"] is False
    assert result["exit_code"] == 1
    assert result["backend"] == "fake"

    call = runner.calls[0]
    assert call["command"] == f"{paths.venv_python} -m pytest -x -q -p no:cacheprovider"
    assert call["kwargs"]["workdir"] == str(toolbox.mirror.root)
    assert call["kwargs"]["backend"] == "auto"
    assert call["kwargs"]["env"] == {"PYTHONDONTWRITEBYTECODE": "1"}
    assert call["kwargs"]["limits"].timeout_seconds == 30.0


def test_test_counts_errors(paths: SelfWorkPaths, store: FakeStore) -> None:
    runner = FakeRunner(stdout="1 passed, 2 errors in 0.3s\n", exit_code=1, ok=False)
    toolbox = _box_with_runner(paths, store, runner)
    result = toolbox.run("source_test", {})
    assert result["ok"] is False
    assert result["errors"] == 2
    assert result["summary"] == "1 passed, 2 errors in 0.3s"


def test_test_success_writes_state(paths: SelfWorkPaths, store: FakeStore) -> None:
    runner = FakeRunner(stdout="341 passed, 2 skipped in 12.34s\n")
    toolbox = _box_with_runner(paths, store, runner)
    result = toolbox.run("source_test", {})
    assert result["ok"] is True
    assert result["passed"] == 341
    assert result["skipped"] == 2
    assert result["summary"] == "341 passed, 2 skipped in 12.34s"

    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["commit"] == toolbox.mirror.head()
    assert state["ok"] is True
    assert state["summary"] == "341 passed, 2 skipped in 12.34s"
    assert state["failed_tests"] == []
    assert state["ts"].endswith("+00:00")
    assert stat.S_IMODE(paths.state_file.stat().st_mode) == 0o600


def test_test_timeout_is_a_failure(paths: SelfWorkPaths, store: FakeStore) -> None:
    runner = FakeRunner(ok=False, exit_code=None, timed_out=True, stdout="")
    toolbox = _box_with_runner(paths, store, runner)
    result = toolbox.run("source_test", {})
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["summary"] == "没通过"
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["ok"] is False
    assert state["commit"] == toolbox.mirror.head()


def test_test_timeout_is_capped_by_config(paths: SelfWorkPaths, store: FakeStore) -> None:
    capped = SelfWorkPaths(
        home=paths.home,
        source_root=paths.source_root,
        venv_python=paths.venv_python,
        test_timeout_seconds=5.0,
    )
    runner = FakeRunner(stdout="1 passed in 0.01s\n")
    toolbox = _box_with_runner(capped, store, runner)
    toolbox.run("source_test", {"timeout_seconds": 3600})
    assert runner.calls[0]["kwargs"]["limits"].timeout_seconds == 5.0


def test_test_without_venv_reports_setup(tmp_path: Path, live: Path, store: FakeStore) -> None:
    paths = SelfWorkPaths(
        home=tmp_path / "home2",
        source_root=live,
        venv_python=str(tmp_path / "no-python"),
    )
    runner = FakeRunner()
    toolbox = _box_with_runner(paths, store, runner)
    result = toolbox.run("source_test", {})
    assert result["ok"] is False
    assert result["error"] == "测试环境还没装好（/opt/yugang-venv）"
    assert runner.calls == []


# -- 提案 -----------------------------------------------------------------


def test_propose_without_changes_is_refused(box: SelfToolbox) -> None:
    result = box.run("source_propose", {"summary": "没啥", "reason": "因为"})
    assert result["ok"] is False
    assert "没有要提交的改动" in result["error"]


def test_propose_docs_change_is_auto_and_marks_stale_tests(
    box: SelfToolbox, paths: SelfWorkPaths
) -> None:
    box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n改\n", "message": "改文档"},
    )
    result = box.run("source_propose", {"summary": "更新状态", "reason": "因为想让它更准"})
    assert result["ok"] is True
    assert re.fullmatch(r"p_\d{8}T\d{6}_[0-9a-f]{6}", result["id"])
    assert result["tier"] == "auto"
    assert result["files"] == ["docs/STATUS.md"]
    assert result["base_commit"] == box.mirror.first_commit()
    assert result["commit"] == box.mirror.head()
    assert result["summary"] == "更新状态"

    folder = paths.proposals_dir / result["id"]
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    assert meta["id"] == result["id"]
    assert meta["author"] == "her"
    assert meta["base_commit"] == result["base_commit"]
    assert meta["commit"] == result["commit"]
    assert meta["files"] == ["docs/STATUS.md"]
    assert meta["tier"] == "auto"
    assert meta["summary"] == "更新状态"
    assert meta["reason"] == "因为想让它更准"
    assert meta["created_ts"].endswith("+00:00")
    assert meta["tests"] == {"ok": False, "summary": "这个提交还没跑过 self_test"}

    patch = (folder / "patch.diff").read_text(encoding="utf-8")
    assert patch.startswith("diff --git ")
    assert "+改" in patch

    assert stat.S_IMODE(folder.stat().st_mode) == 0o755
    assert stat.S_IMODE((folder / "meta.json").stat().st_mode) == 0o644
    assert stat.S_IMODE((folder / "patch.diff").stat().st_mode) == 0o644


def test_propose_code_change_needs_review(box: SelfToolbox) -> None:
    box.run(
        "source_write",
        {"path": "src/yugang/ids.py", "content": "x = 2\n", "message": "改代码"},
    )
    result = box.run("source_propose", {"summary": "改代码", "reason": "因为要修"})
    assert result["ok"] is True
    assert result["tier"] == "review"
    assert result["files"] == ["src/yugang/ids.py"]


def test_propose_requires_a_summary(box: SelfToolbox) -> None:
    box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n改\n", "message": "改文档"},
    )
    result = box.run("source_propose", {"summary": "   ", "reason": "因为"})
    assert result["ok"] is False
    assert "说明" in result["error"]


def test_propose_carries_test_state_for_the_same_commit(
    paths: SelfWorkPaths, store: FakeStore
) -> None:
    runner = FakeRunner(stdout="5 passed, 1 skipped in 0.2s\n")
    toolbox = _box_with_runner(paths, store, runner)
    toolbox.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n测过\n", "message": "先改后测"},
    )
    toolbox.run("source_test", {})
    result = toolbox.run("source_propose", {"summary": "测过的改动", "reason": "因为"})
    assert result["ok"] is True
    meta = json.loads((paths.proposals_dir / result["id"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["tests"]["ok"] is True
    assert meta["tests"]["passed"] == 5
    assert meta["tests"]["skipped"] == 1
    assert meta["tests"]["summary"] == "5 passed, 1 skipped in 0.2s"
    assert meta["tests"]["ts"].endswith("+00:00")


def test_propose_marks_test_state_stale_after_a_new_commit(
    paths: SelfWorkPaths, store: FakeStore
) -> None:
    runner = FakeRunner(stdout="1 passed in 0.01s\n")
    toolbox = _box_with_runner(paths, store, runner)
    toolbox.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n一\n", "message": "第一次"},
    )
    toolbox.run("source_test", {})
    toolbox.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n二\n", "message": "第二次"},
    )
    result = toolbox.run("source_propose", {"summary": "第二次", "reason": "因为"})
    assert result["ok"] is True
    meta = json.loads((paths.proposals_dir / result["id"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["tests"] == {"ok": False, "summary": "这个提交还没跑过 self_test"}


def test_propose_uses_the_latest_successful_apply_as_base(
    box: SelfToolbox, paths: SelfWorkPaths
) -> None:
    first = box.mirror.first_commit()
    box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n一\n", "message": "第一次"},
    )
    first_change = box.mirror.head()
    box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n二\n", "message": "第二次"},
    )
    paths.results_dir.mkdir(parents=True, exist_ok=True)
    (paths.results_dir / "old.json").write_text(
        json.dumps({"ok": True, "commit": first, "created": "2026-10-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (paths.results_dir / "new.json").write_text(
        json.dumps({"ok": True, "commit": first_change, "created": "2026-10-02T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (paths.results_dir / "failed.json").write_text(
        json.dumps({"ok": False, "commit": box.mirror.head(), "created": "2026-10-03T00:00:00+00:00"}),
        encoding="utf-8",
    )
    result = box.run("source_propose", {"summary": "接着上次", "reason": "因为"})
    assert result["ok"] is True
    assert result["base_commit"] == first_change
    assert result["files"] == ["docs/STATUS.md"]


def test_propose_ignores_apply_results_with_unknown_commits(
    box: SelfToolbox, paths: SelfWorkPaths
) -> None:
    paths.results_dir.mkdir(parents=True, exist_ok=True)
    (paths.results_dir / "bogus.json").write_text(
        json.dumps({"ok": True, "commit": "0" * 40, "created": "2026-10-05T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (paths.results_dir / "broken.json").write_text("not json", encoding="utf-8")
    box.run(
        "source_write",
        {"path": "docs/STATUS.md", "content": "# status\n\n一\n", "message": "第一次"},
    )
    result = box.run("source_propose", {"summary": "忽略坏结果", "reason": "因为"})
    assert result["ok"] is True
    assert result["base_commit"] == box.mirror.first_commit()


def test_failures_are_ledgered(box: SelfToolbox, store: FakeStore) -> None:
    result = box.run("source_propose", {"summary": "没啥", "reason": "因为"})
    assert result["ok"] is False
    assert store.calls[-1]["tool"] == "source_propose"
    assert store.calls[-1]["ok"] is False
    assert "没有要提交的改动" in store.calls[-1]["detail"]
