"""Mirror：从 live 拷出来的影子仓库，只许在里面动，一次提交只带一个文件。"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from yugang.selfwork import DEFAULT_INCLUDES, Mirror, MirrorError
from yugang.selfwork.mirror import MIRROR_NOT_READY

SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


@pytest.fixture()
def live(tmp_path: Path) -> Path:
    root = tmp_path / "live"
    (root / "src" / "yugang").mkdir(parents=True)
    (root / "src" / "yugang" / "ids.py").write_text("x = 1\n", encoding="utf-8")
    (root / "src" / "yugang" / "__pycache__").mkdir()
    (root / "src" / "yugang" / "__pycache__" / "ids.cpython-311.pyc").write_bytes(b"\x00junk")
    (root / "tests").mkdir()
    (root / "tests" / "test_ids.py").write_text("def test_ids():\n    assert True\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "STATUS.md").write_text("# status\n", encoding="utf-8")
    (root / "docs" / "SELFCHANGE.md").write_text("# spec\n", encoding="utf-8")
    (root / "deploy").mkdir()
    (root / "deploy" / "self-lane.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    (root / "README.md").write_text("# fake\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    # 这两个目录不在 includes 里：音频素材和大文件永远不该进镜像。
    (root / "audio").mkdir()
    (root / "audio" / "note.txt").write_text("noise\n", encoding="utf-8")
    (root / "hyperframes").mkdir()
    (root / "hyperframes" / "x.json").write_text("{}", encoding="utf-8")
    # live 自己的 .git 也不该被拷进来。
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n# live-config-sentinel\n", encoding="utf-8")
    return root


@pytest.fixture()
def mirror(tmp_path: Path, live: Path) -> Mirror:
    obj = Mirror(tmp_path / "home" / "self", source=live)
    obj.ensure()
    return obj


def test_default_includes_are_the_agreed_list() -> None:
    assert DEFAULT_INCLUDES == (
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


def test_status_before_ensure_reports_uninitialized(tmp_path: Path) -> None:
    status = Mirror(tmp_path / "nothing").status()
    assert status["ok"] is True
    assert status["initialized"] is False
    assert status["head"] is None
    assert status["commits"] == 0
    assert status["dirty"] is False
    assert status["root"] == str(tmp_path / "nothing")


def test_ensure_reports_files_and_head(tmp_path: Path, live: Path) -> None:
    obj = Mirror(tmp_path / "home" / "self", source=live)
    result = obj.ensure()
    assert result["created"] is True
    # src 1 + tests 1 + docs 2 + deploy 1 + .github 1 + 根目录 4 = 10
    assert result["files"] == 10
    assert SHA_RE.match(result["head"])
    assert obj.head() == result["head"]


def test_ensure_copies_only_includes(mirror: Mirror) -> None:
    root = mirror.root
    assert (root / "src" / "yugang" / "ids.py").is_file()
    assert (root / "tests" / "test_ids.py").is_file()
    assert (root / "docs" / "SELFCHANGE.md").is_file()
    assert (root / "deploy" / "self-lane.sh").is_file()
    assert (root / ".github" / "workflows" / "ci.yml").is_file()
    assert (root / ".gitignore").is_file()
    assert not (root / "audio").exists()
    assert not (root / "hyperframes").exists()
    assert not (root / "src" / "yugang" / "__pycache__").exists()
    assert (root / ".git").is_dir()
    assert "live-config-sentinel" not in (root / ".git" / "config").read_text(encoding="utf-8")


def test_ensure_is_idempotent(mirror: Mirror) -> None:
    head = mirror.head()
    again = mirror.ensure()
    assert again["created"] is False
    assert again["head"] == head
    assert mirror.status()["commits"] == 1


def test_ensure_requires_a_source(tmp_path: Path) -> None:
    with pytest.raises(MirrorError):
        Mirror(tmp_path / "self").ensure()


def test_ensure_rejects_missing_source(tmp_path: Path) -> None:
    with pytest.raises(MirrorError):
        Mirror(tmp_path / "self", source=tmp_path / "nope").ensure()


def test_status_reports_branch_dirty_and_commits(mirror: Mirror) -> None:
    status = mirror.status()
    assert status["initialized"] is True
    assert status["branch"] == "main"
    assert status["dirty"] is False
    assert status["commits"] == 1
    assert status["head"] == mirror.head()
    (mirror.root / "docs" / "scratch.md").write_text("x\n", encoding="utf-8")
    assert mirror.status()["dirty"] is True


def test_resolve_stays_inside_the_mirror(mirror: Mirror) -> None:
    assert mirror.resolve("") == mirror.root
    assert mirror.resolve("docs/STATUS.md") == mirror.root / "docs" / "STATUS.md"
    assert mirror.resolve("./docs/STATUS.md") == mirror.root / "docs" / "STATUS.md"


@pytest.mark.parametrize(
    "bad",
    [
        "/etc/passwd",
        "/",
        "~/.bashrc",
        "../x",
        "docs/../../x",
        "docs/\x00.md",
        "docs\\STATUS.md",
        "C:\\Windows\\x",
        "C:x",
        ".git/config",
    ],
)
def test_resolve_rejects_escapes(mirror: Mirror, bad: str) -> None:
    with pytest.raises(MirrorError):
        mirror.resolve(bad)


def test_resolve_rejects_symlink_escape(tmp_path: Path, mirror: Mirror) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        os.symlink(outside, mirror.root / "docs" / "link")
    except (OSError, NotImplementedError):
        pytest.skip("这个平台不让建符号链接")
    with pytest.raises(MirrorError):
        mirror.resolve("docs/link/secret.md")


def test_list_root_puts_dirs_first(mirror: Mirror) -> None:
    entries = mirror.list()
    names = [entry["path"] for entry in entries]
    assert "docs" in names
    assert "audio" not in names
    kinds = [entry["kind"] for entry in entries]
    assert kinds == sorted(kinds, key=lambda kind: 0 if kind == "dir" else 1)
    docs = next(entry for entry in entries if entry["path"] == "docs")
    assert docs == {"path": "docs", "kind": "dir", "bytes": 0}


def test_list_subdir_and_limit(mirror: Mirror) -> None:
    entries = mirror.list("docs", max_entries=1)
    assert len(entries) == 1
    assert entries[0]["path"] == "docs/SELFCHANGE.md"
    assert entries[0]["kind"] == "file"


def test_list_file_and_missing(mirror: Mirror) -> None:
    assert mirror.list("docs/STATUS.md") == [
        {"path": "docs/STATUS.md", "kind": "file", "bytes": len("# status\n")}
    ]
    with pytest.raises(MirrorError):
        mirror.list("docs/nope.md")


def test_read_text(mirror: Mirror) -> None:
    data = mirror.read("docs/STATUS.md")
    assert data == {"path": "docs/STATUS.md", "chars": 9, "truncated": False, "text": "# status\n"}


def test_read_truncates(mirror: Mirror) -> None:
    data = mirror.read("docs/STATUS.md", max_chars=4)
    assert data["text"] == "# st"
    assert data["chars"] == 4
    assert data["truncated"] is True


def test_read_rejects_binary_and_missing(mirror: Mirror) -> None:
    (mirror.root / "docs" / "blob.bin").write_bytes(b"\x00\x01\x02")
    with pytest.raises(MirrorError):
        mirror.read("docs/blob.bin")
    with pytest.raises(MirrorError):
        mirror.read("docs/nope.md")
    with pytest.raises(MirrorError):
        mirror.read("docs")


def test_write_commits_one_file(mirror: Mirror) -> None:
    content = "# status\n\n更准一点\n"
    result = mirror.write("docs/STATUS.md", content, "更新状态")
    assert result["ok"] is True
    assert SHA_RE.match(result["commit"])
    assert result["short"] == result["commit"][:7]
    assert result["path"] == "docs/STATUS.md"
    assert result["bytes"] == len(content.encode("utf-8"))
    assert mirror.head() == result["commit"]
    assert mirror.changed_files(mirror.first_commit()) == ["docs/STATUS.md"]
    assert "+更准一点" in mirror.diff(mirror.first_commit())
    assert mirror.log(limit=1)[0]["message"] == "更新状态"


def test_write_pins_git_identity(mirror: Mirror) -> None:
    mirror.write("docs/STATUS.md", "# status\n\nx\n", "谁的提交")
    assert _git(mirror.root, "log", "-1", "--format=%an <%ae>").strip() == "yugang <yugang@localhost>"


def test_write_unchanged_content_is_rejected(mirror: Mirror) -> None:
    with pytest.raises(MirrorError, match="内容没有变化"):
        mirror.write("docs/STATUS.md", "# status\n", "没有变化")
    assert mirror.status()["commits"] == 1


def test_write_rejects_too_large(tmp_path: Path, live: Path) -> None:
    obj = Mirror(tmp_path / "home" / "self", source=live, max_file_bytes=8)
    obj.ensure()
    with pytest.raises(MirrorError, match="文件太大了"):
        obj.write("docs/STATUS.md", "0123456789", "太大")


def test_write_rejects_empty_message(mirror: Mirror) -> None:
    with pytest.raises(MirrorError):
        mirror.write("docs/STATUS.md", "# status\n\nx\n", "   ")


def test_write_refuses_outside_and_git_dir(mirror: Mirror) -> None:
    with pytest.raises(MirrorError):
        mirror.write("../escape.md", "x\n", "越界")
    with pytest.raises(MirrorError):
        mirror.write(".git/config", "x\n", "改配置")
    assert not (mirror.root.parent / "escape.md").exists()


def test_write_creates_parents_and_can_clear(mirror: Mirror) -> None:
    result = mirror.write("docs/deep/new.md", "", "先建个空文件")
    target = mirror.root / "docs" / "deep" / "new.md"
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == ""
    assert result["bytes"] == 0


def test_write_only_stages_the_named_file(mirror: Mirror) -> None:
    (mirror.root / "docs" / "scratch.md").write_text("not mine\n", encoding="utf-8")
    result = mirror.write("docs/STATUS.md", "# status\n\n只带一个\n", "只提交一个")
    committed = _git(mirror.root, "show", "--name-only", "--format=", result["commit"]).split()
    assert committed == ["docs/STATUS.md"]
    assert mirror.status()["dirty"] is True


def test_write_keeps_existing_mode(mirror: Mirror) -> None:
    target = mirror.root / "docs" / "STATUS.md"
    os.chmod(target, 0o600)
    mirror.write("docs/STATUS.md", "# status\n\nmode\n", "改过权限的文件")
    assert target.stat().st_mode & 0o777 == 0o600


def test_write_requires_initialized_mirror(tmp_path: Path) -> None:
    with pytest.raises(MirrorError) as err:
        Mirror(tmp_path / "not-yet").write("docs/STATUS.md", "x\n", "没镜像")
    assert str(err.value) == MIRROR_NOT_READY


def test_log_first_commit_and_exists(mirror: Mirror) -> None:
    first = mirror.first_commit()
    assert SHA_RE.match(first)
    assert first == mirror.log()[-1]["commit"]
    assert mirror.exists(first) is True
    assert mirror.exists(mirror.head()) is True
    assert mirror.exists("0" * 40) is False
    assert mirror.exists("not-a-sha") is False


def test_log_newest_first_with_utc_timestamps(mirror: Mirror) -> None:
    mirror.write("docs/STATUS.md", "# status\n\n一\n", "第一次")
    mirror.write("docs/STATUS.md", "# status\n\n二\n", "第二次")
    entries = mirror.log(limit=2)
    assert [entry["message"] for entry in entries] == ["第二次", "第一次"]
    for entry in entries:
        assert SHA_RE.match(entry["commit"])
        assert entry["short"] == entry["commit"][:7]
        assert entry["ts"].endswith("+00:00")


def test_log_returns_empty_before_ensure(tmp_path: Path) -> None:
    assert Mirror(tmp_path / "empty").log() == []


def test_diff_and_changed_files_need_a_known_commit(mirror: Mirror) -> None:
    with pytest.raises(MirrorError):
        mirror.diff("0" * 40)
    with pytest.raises(MirrorError):
        mirror.changed_files("0" * 40)


def test_changed_files_and_diff_are_empty_on_a_clean_tree(mirror: Mirror) -> None:
    head = mirror.head()
    assert mirror.changed_files(head) == []
    assert mirror.diff(head).strip() == ""
