"""--sync：部署之后把她的镜像跟到 live 上，不动她的提交。

没有这一步，镜像会一直停在部署前那一刻：她跑绿的是别人已经换掉的代码，
她的 patch 也是对着一个早就发出去过的 base 切的，git apply 会以与她无关的理由失败。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yugang.selfwork import Mirror


def _git(root: Path, *args: str) -> str:
    # Carry the identity explicitly: inside the sandbox HOME is empty, so a
    # test that leaned on a machine's global git config fails there and only there.
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=yugang",
            "-c",
            "user.email=yugang@localhost",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


@pytest.fixture()
def live(tmp_path: Path) -> Path:
    root = tmp_path / "live"
    (root / "src" / "yugang").mkdir(parents=True)
    (root / "src" / "yugang" / "ids.py").write_text("x = 1\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "STATUS.md").write_text("# status\n", encoding="utf-8")
    (root / "docs" / "GONE.md").write_text("# 这一份 live 里马上就没有了\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'fake'\n", encoding="utf-8")
    (root / "README.md").write_text("# fake\n", encoding="utf-8")
    (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
    return root


@pytest.fixture()
def mirror(tmp_path: Path, live: Path) -> Mirror:
    item = Mirror(tmp_path / "home" / "self", source=live)
    item.ensure()
    return item


def test_sync_brings_new_and_changed_files_and_removes_dropped_ones(mirror: Mirror, live: Path) -> None:
    # 她先在自己的副本里留了一个提交——同步不该把它冲掉。
    (mirror.root / "docs" / "MINE.md").write_text("# 她写的\n", encoding="utf-8")
    _git(mirror.root, "add", "--all")
    _git(mirror.root, "commit", "--quiet", "-m", "docs: 她自己的改动")
    hers = _git(mirror.root, "rev-parse", "HEAD")

    # 部署：live 多一个文件、改一个文件、少一个文件。
    (live / "docs" / "DEPLOYED.md").write_text("# 部署带来的\n", encoding="utf-8")
    (live / "src" / "yugang" / "ids.py").write_text("x = 2\n", encoding="utf-8")
    (live / "docs" / "GONE.md").unlink()
    subprocess.run(["git", "-C", str(live), "init", "--quiet"], check=True)

    result = mirror.sync()

    assert result["ok"] is True
    assert result["synced"] is True
    assert result["head"] != hers
    assert (mirror.root / "docs" / "DEPLOYED.md").read_text(encoding="utf-8") == "# 部署带来的\n"
    assert (mirror.root / "src" / "yugang" / "ids.py").read_text(encoding="utf-8") == "x = 2\n"
    assert not (mirror.root / "docs" / "GONE.md").exists()
    # 她的提交还在历史里，只是上面多了一条同步提交。
    assert _git(mirror.root, "merge-base", "--is-ancestor", hers, "HEAD") == ""
    assert _git(mirror.root, "log", "--format=%s") .splitlines()[0].startswith("同步：")
    assert "docs: 她自己的改动" in _git(mirror.root, "log", "--format=%s")
    # 同步进来的文件归她，否则她下一次 source_write 就写不动了。
    assert (mirror.root / "docs" / "DEPLOYED.md").stat().st_uid == (mirror.root / "docs" / "MINE.md").stat().st_uid


def test_sync_with_nothing_new_is_a_no_op(mirror: Mirror) -> None:
    head = _git(mirror.root, "rev-parse", "HEAD")
    result = mirror.sync()
    assert result["ok"] is True
    assert result["synced"] is False
    assert result["head"] == head
    assert _git(mirror.root, "rev-parse", "HEAD") == head


def test_sync_on_a_missing_mirror_builds_it(tmp_path: Path, live: Path) -> None:
    item = Mirror(tmp_path / "home" / "self", source=live)
    result = item.sync()
    assert result["ok"] is True
    assert (item.root / "docs" / "STATUS.md").exists()


def test_sync_refuses_to_copy_onto_itself(tmp_path: Path, live: Path) -> None:
    item = Mirror(live, source=live)
    with pytest.raises(Exception):
        item.sync()


def test_sync_leaves_the_mirror_writable_for_her(tmp_path: Path, live: Path) -> None:
    """live 是 0444；拷进镜像后如果还是 0444，下一次 sync 就会在它自己跟的文件上失败。"""
    for path in live.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    item = Mirror(tmp_path / "home" / "self", source=live)
    item.sync()

    (live / "docs" / "STATUS.md").chmod(0o644)
    (live / "docs" / "STATUS.md").write_text("# status 2\n", encoding="utf-8")
    result = item.sync()

    assert result["ok"] is True
    assert result["synced"] is True
    assert (item.root / "docs" / "STATUS.md").read_text(encoding="utf-8") == "# status 2\n"
    target = item.root / "docs" / "STATUS.md"
    assert target.stat().st_mode & 0o200  # 她自己还得能写

