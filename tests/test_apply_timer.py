"""巡检只看 `.json` 的代价：已经合入的提案被当成没跑过，每十分钟重跑一次。

2026-10-26 20:09 到 2026-10-28 23:43，同一个 auto 档提案在 verify 上失败了 347 次，
347 条一模一样的「我的改动没过」进了 L1，把她自己的最近记忆挤掉了。她自己的日记
（evt_20261026T182324698467_ad756b）写的是「像被定时器推着重复同一个动作」。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from yugang.cli import _pending_auto_proposals


def _proposal(root, pid: str, tier: str) -> None:
    folder = root / "proposals" / pid
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"id": pid, "tier": tier, "summary": pid}), encoding="utf-8")


def test_the_timer_does_not_rerun_a_proposal_whose_result_was_already_read(tmp_path):
    proposals = tmp_path / "proposals"
    results = tmp_path / "apply-results"
    results.mkdir()
    _proposal(tmp_path, "prop_done", "auto")
    _proposal(tmp_path, "prop_fresh", "auto")
    _proposal(tmp_path, "prop_review", "review")
    (results / "prop_done.json.done").write_text("{}", encoding="utf-8")

    paths = SimpleNamespace(proposals_dir=proposals, results_dir=results)
    assert _pending_auto_proposals(paths) == ["prop_fresh"]


def test_an_unreadable_result_still_counts_as_run(tmp_path):
    proposals = tmp_path / "proposals"
    results = tmp_path / "apply-results"
    results.mkdir()
    _proposal(tmp_path, "prop_bad", "auto")
    (results / "prop_bad.json.bad").write_text("{not json", encoding="utf-8")

    paths = SimpleNamespace(proposals_dir=proposals, results_dir=results)
    assert _pending_auto_proposals(paths) == []


def test_a_review_proposal_is_never_picked_up_by_the_timer(tmp_path):
    proposals = tmp_path / "proposals"
    results = tmp_path / "apply-results"
    results.mkdir()
    _proposal(tmp_path, "prop_review", "review")

    paths = SimpleNamespace(proposals_dir=proposals, results_dir=results)
    assert _pending_auto_proposals(paths) == []
