from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from yugang.world.workspace import Workspace, WorkspaceError


def _symlink_or_skip(target: Path, link: Path) -> None:
    """Create a symlink, or skip on a platform/filesystem that refuses."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"这个平台不能建符号链接：{exc}")


@pytest.fixture
def ws(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure()
    return workspace


def test_root_is_absolute_and_resolved(tmp_path):
    workspace = Workspace(tmp_path / "a" / "b")
    assert workspace.root.is_absolute()
    assert workspace.root == (tmp_path / "a" / "b").resolve()
    # Constructing a workspace must not create it; ensure() does that.
    assert not workspace.root.exists()


def test_ensure_creates_root_once(tmp_path):
    workspace = Workspace(tmp_path / "deep" / "workspace")
    root = workspace.ensure()
    assert root == workspace.root
    assert root.is_dir()
    assert workspace.ensure() == root


def test_resolve_accepts_relative_paths(ws):
    assert ws.resolve("") == ws.root
    assert ws.resolve(None) == ws.root
    assert ws.resolve("a/b.txt") == ws.root / "a" / "b.txt"
    assert ws.resolve("a//b/./c") == ws.root / "a" / "b" / "c"


@pytest.mark.parametrize(
    "bad",
    [
        "../x",
        "..",
        "/etc/passwd",
        "~/x",
        "~root/x",
        "a/../../x",
        "a\x00b",
        "C:/Windows",
        "C:\\Windows",
        "a\\b",
    ],
)
def test_resolve_rejects_unsafe_paths(ws, bad):
    with pytest.raises(WorkspaceError):
        ws.resolve(bad)


def test_symlink_file_escape_is_refused(tmp_path, ws):
    outside = tmp_path / "outside.txt"
    outside.write_text("她的记忆库", encoding="utf-8")
    _symlink_or_skip(outside, ws.root / "escape.txt")

    with pytest.raises(WorkspaceError):
        ws.resolve("escape.txt")
    with pytest.raises(WorkspaceError):
        ws.read("escape.txt")
    with pytest.raises(WorkspaceError):
        ws.write("escape.txt", "改写")
    # The file outside must be untouched by the refused write.
    assert outside.read_text(encoding="utf-8") == "她的记忆库"


def test_symlink_directory_escape_is_refused(tmp_path, ws):
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "f.md").write_text("外面", encoding="utf-8")
    _symlink_or_skip(outside_dir, ws.root / "away")

    with pytest.raises(WorkspaceError):
        ws.read("away/f.md")
    with pytest.raises(WorkspaceError):
        ws.list("away")
    with pytest.raises(WorkspaceError):
        ws.mkdir("away/new")
    with pytest.raises(WorkspaceError):
        ws.write("away/new.md", "x")

    # A link whose target does not exist yet is still refused.
    _symlink_or_skip(tmp_path / "not-yet", ws.root / "ghost")
    with pytest.raises(WorkspaceError):
        ws.write("ghost/file.txt", "x")

    # Escaping links are not advertised by list() and not counted by info().
    names = {Path(entry["path"]).name for entry in ws.list("")["entries"]}
    assert names == set()
    assert ws.info()["files"] == 0


def test_symlink_inside_root_is_allowed(ws):
    ws.write("real.md", "真的")
    _symlink_or_skip(ws.root / "real.md", ws.root / "alias.md")
    assert ws.read("alias.md")["text"] == "真的"


def test_write_read_round_trip(ws):
    result = ws.write("notes/today.md", "今天的笔记\n")
    assert result["created"] is True
    assert result["previous_bytes"] is None
    assert result["bytes"] == len("今天的笔记\n".encode("utf-8"))
    assert result["path"] == str(ws.root / "notes" / "today.md")
    # Parent directories are created inside the root by the write itself.
    assert (ws.root / "notes").is_dir()

    got = ws.read("notes/today.md")
    assert got["text"] == "今天的笔记\n"
    assert got["size"] == result["bytes"]
    assert got["binary"] is False
    assert got["truncated"] is False
    assert Path(got["path"]) == ws.root / "notes" / "today.md"


def test_overwrite_reports_previous_bytes(ws):
    first = ws.write("a.md", "12345")
    assert first["created"] is True
    second = ws.write("a.md", "abc")
    assert second["created"] is False
    assert second["previous_bytes"] == 5
    assert second["bytes"] == 3
    assert ws.read("a.md")["text"] == "abc"


def test_write_refuses_oversize_content(ws):
    with pytest.raises(WorkspaceError) as excinfo:
        ws.write("big.txt", "x" * 50, max_bytes=20)
    message = str(excinfo.value)
    assert "50" in message
    assert "20" in message
    assert not (ws.root / "big.txt").exists()


def test_write_requires_text_and_refuses_directories(ws):
    with pytest.raises(WorkspaceError):
        ws.write("a.txt", b"bytes")  # type: ignore[arg-type]
    ws.mkdir("notes")
    with pytest.raises(WorkspaceError):
        ws.write("notes", "x")


def test_read_truncates_long_text(ws):
    body = "行\n" * 5000
    ws.write("long.txt", body)
    got = ws.read("long.txt", max_chars=10)
    assert got["truncated"] is True
    assert got["text"] == body[:10]
    assert got["text"].endswith("行\n")
    assert got["binary"] is False

    full = ws.read("long.txt", max_chars=len(body))
    assert full["text"] == body
    assert full["truncated"] is False


def test_read_stops_at_byte_budget(ws):
    body = "a" * 100_000
    ws.write("huge.txt", body)
    got = ws.read("huge.txt")  # default max_chars=20000 -> 80000-byte budget
    assert got["truncated"] is True
    assert got["size"] == 100_000
    assert len(got["text"]) == 20_000


def test_read_detects_binary(ws):
    (ws.root / "raw.bin").write_bytes(b"\x00\x01")
    got = ws.read("raw.bin")
    assert got["binary"] is True
    assert got["text"] == ""
    assert got["size"] == 2

    (ws.root / "bad.txt").write_bytes(b"\xff\xfe\xfa")
    bad = ws.read("bad.txt")
    assert bad["binary"] is True
    assert bad["text"] == ""


def test_read_missing_file_uses_chinese_message(ws):
    with pytest.raises(WorkspaceError) as excinfo:
        ws.read("notes/none.md")
    assert str(excinfo.value) == "没有这个文件：notes/none.md"


def test_read_of_directory_is_refused(ws):
    ws.mkdir("notes")
    with pytest.raises(WorkspaceError) as excinfo:
        ws.read("notes")
    assert "没有这个文件" not in str(excinfo.value)
    assert "目录" in str(excinfo.value)


def test_list_missing_directory_uses_chinese_message(ws):
    with pytest.raises(WorkspaceError) as excinfo:
        ws.list("nope")
    assert str(excinfo.value) == "没有这个目录：nope"


def test_list_orders_directories_then_files(ws):
    ws.mkdir("zeta")
    ws.mkdir("alpha")
    ws.write("m.md", "m")
    ws.write("b.md", "b")
    ws.write(".hidden", "h")

    listing = ws.list("")
    assert listing["path"] == str(ws.root)
    assert listing["truncated"] is False
    assert [Path(e["path"]).name for e in listing["entries"]] == [
        "alpha",
        "zeta",
        ".hidden",
        "b.md",
        "m.md",
    ]
    assert [e["kind"] for e in listing["entries"]] == ["dir", "dir", "file", "file", "file"]
    file_entry = next(e for e in listing["entries"] if Path(e["path"]).name == "m.md")
    assert file_entry["size"] == 1
    stamp = datetime.fromisoformat(file_entry["mtime"])
    assert stamp.tzinfo is not None
    assert stamp.utcoffset().total_seconds() == 0
    assert file_entry["mtime"].endswith("+00:00")


def test_list_limit_sets_truncated(ws):
    for index in range(5):
        ws.write(f"f{index}.md", "x")
    cut = ws.list("", limit=3)
    assert len(cut["entries"]) == 3
    assert cut["truncated"] is True
    exact = ws.list("", limit=5)
    assert exact["truncated"] is False


def test_list_subdirectory(ws):
    ws.write("notes/one.md", "1")
    listing = ws.list("notes")
    assert listing["path"] == str(ws.root / "notes")
    assert [Path(e["path"]).name for e in listing["entries"]] == ["one.md"]
    with pytest.raises(WorkspaceError):
        ws.list("notes/one.md")


def test_info_counts_files_and_bytes(ws):
    ws.write("a.md", "12345")
    ws.write("sub/b.md", "123")
    assert ws.info() == {"root": str(ws.root), "files": 2, "bytes": 8}

    absent = Workspace(ws.root / "nope")
    assert absent.info() == {"root": str(absent.root), "files": 0, "bytes": 0}


def test_mkdir_is_idempotent(ws):
    first = ws.mkdir("notes/deep")
    assert first["created"] is True
    assert Path(first["path"]) == ws.root / "notes" / "deep"
    again = ws.mkdir("notes/deep")
    assert again["created"] is False
    assert again["path"] == first["path"]

    ws.write("file.md", "x")
    with pytest.raises(WorkspaceError):
        ws.mkdir("file.md")


def test_write_is_atomic_and_leaves_no_temp(ws):
    ws.write("notes/today.md", "第一次")
    assert sorted(p.name for p in (ws.root / "notes").iterdir()) == ["today.md"]

    with pytest.raises(WorkspaceError):
        ws.write("notes/today.md", "x" * 10, max_bytes=5)
    assert sorted(p.name for p in (ws.root / "notes").iterdir()) == ["today.md"]
    assert ws.read("notes/today.md")["text"] == "第一次"

    # Root-level listing shows no leftover temp file either.
    assert [Path(e["path"]).name for e in ws.list("")["entries"]] == ["notes"]

