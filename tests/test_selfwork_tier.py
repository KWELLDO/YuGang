"""classify() 的规则：只有文档能自动合入，别的都要人看。"""

from __future__ import annotations

import pytest

from yugang.selfwork import AUTO_PREFIXES, classify


def test_auto_prefixes_are_the_agreed_ones() -> None:
    assert AUTO_PREFIXES == ("docs/", "README.md")


def test_empty_paths_need_review() -> None:
    # 没有文件通常意味着调用方搞错了，不该被当成"无害"。
    assert classify([]) == "review"


@pytest.mark.parametrize(
    "paths",
    [
        ["docs/STATUS.md"],
        ["docs/"],
        ["docs/a/b/c.md"],
        ["README.md"],
        ["docs/STATUS.md", "docs/SELFCHANGE.md", "README.md"],
        ["./docs/STATUS.md", "docs//STATUS.md"],
    ],
)
def test_documentation_only_is_auto(paths: list[str]) -> None:
    assert classify(paths) == "auto"


@pytest.mark.parametrize(
    "paths",
    [
        ["src/yugang/selfwork/mirror.py"],
        ["docs/STATUS.md", "src/yugang/ids.py"],
        ["tests/test_selfwork_tier.py"],
        ["pyproject.toml"],
        ["deploy/self-lane.sh"],
        ["README.md.bak"],
        ["README"],
        ["docsx/STATUS.md"],
    ],
)
def test_anything_else_is_review(paths: list[str]) -> None:
    assert classify(paths) == "review"


@pytest.mark.parametrize(
    "bad",
    [
        "../docs/STATUS.md",
        "docs/../../src/yugang/ids.py",
        "/etc/passwd",
        "/docs/STATUS.md",
        "~/.bashrc",
        "docs/\x00.md",
        "docs\\STATUS.md",
        "C:\\Windows\\STATUS.md",
        "C:docs/STATUS.md",
        "",
        "   ",
    ],
)
def test_suspicious_paths_are_never_auto(bad: str) -> None:
    assert classify([bad]) == "review"


def test_one_bad_path_downgrades_the_whole_batch() -> None:
    assert classify(["docs/STATUS.md", "../outside.md", "README.md"]) == "review"


def test_non_string_is_review() -> None:
    assert classify([None]) == "review"  # type: ignore[list-item]


@pytest.mark.parametrize("paths", [["docs/STATUS.md"], ["README.md"], ["src/yugang/ids.py"]])
def test_classify_is_deterministic(paths: list[str]) -> None:
    first = classify(paths)
    assert all(classify(paths) == first for _ in range(5))
