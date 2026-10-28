"""她改自己的通道：状态可见、合入结果进 L1、提示词只在开关打开时多一段。

这三件事都发生在网关侧接线里，不需要真的合入一次才看得见。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from yugang.gateway.service import GatewayService
from yugang.protocol.wake import SOURCE_RULE, build_messages, build_wake_context
from yugang.runtime.mock import MockRuntime


def _service(cfg, store):
    now = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
    service = GatewayService(cfg, store, MockRuntime(responder=lambda messages, purpose: "我在。"), now_fn=lambda: now)
    return service, now


def _actions(store):
    return store.events(limit=20, source="ai-observation")


def test_status_exposes_the_lane_and_which_sandbox_tests_run_in(cfg, store):
    service, _ = _service(cfg, store)
    lane = service.status()["self_change"]

    assert lane["enabled"] is True
    assert {"source_list", "source_read", "source_write", "source_log", "source_test", "source_propose"} <= set(
        lane["tools"]
    )
    # 她看到的是一张扁平工具表：记忆工具与改自己的工具在同一处。
    flat = service._toolbox().names()
    assert {"source_propose", "source_test", "memory_audit"} <= flat
    backend = lane["test_backend"]
    assert set(backend) >= {"name", "weak"}
    # 没建镜像时也给结论，而不是让 /status 挂掉。
    assert lane["mirror"] is not None


def test_a_rolled_back_change_reaches_her_memory_exactly_once(cfg, store):
    service, now = _service(cfg, store)
    results = Path(cfg.home) / "apply-results"
    results.mkdir(parents=True)
    payload = {
        "id": "prop_20261026T040000000000_abcdef",
        "ok": False,
        "stage": "test",
        "rolled_back": True,
        "summary": "补一段说明",
        "error": "pytest: 2 failed",
        "env": {"base_commit": "abc1234", "commit": "def5678", "pytest_summary": "2 failed, 493 passed"},
        "ts": now.isoformat(),
    }
    path = results / "prop_20261026T040000000000_abcdef.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    service._ingest_apply_results()
    events = _actions(store)
    assert len(events) == 1
    text = events[0].content
    assert "没过" in text and "test" in text
    assert "回退" in text and "abc1234" in text
    assert "2 failed" in text
    assert events[0].meta["rolled_back"] is True
    # 一次就够：读过的文件改名，重扫不再产生第二条一样的记录。
    assert not path.exists()
    assert path.with_name(path.name + ".done").exists()

    service._ingest_apply_results()
    assert len(_actions(store)) == 1


def test_a_proposal_waiting_for_approval_is_not_reported_as_a_failure(cfg, store):
    service, now = _service(cfg, store)
    results = Path(cfg.home) / "apply-results"
    results.mkdir(parents=True)
    path = results / "prop_wait.json"
    path.write_text(
        json.dumps(
            {
                "id": "prop_wait",
                "ok": False,
                "stage": "approve",
                "rolled_back": False,
                "error": "这份改动要我批（review 档），还没有批准",
                "ts": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    service._ingest_apply_results()
    events = _actions(store)
    assert len(events) == 1
    assert "等批准" in events[0].content
    assert events[0].meta["stage"] == "approve"


def test_a_broken_result_file_is_quarantined_without_looping(cfg, store):
    service, _ = _service(cfg, store)
    results = Path(cfg.home) / "apply-results"
    results.mkdir(parents=True)
    bad = results / "prop_broken.json"
    bad.write_text("{not json", encoding="utf-8")

    service._ingest_apply_results()
    assert bad.with_name(bad.name + ".bad").exists()
    system = store.events(limit=10, source="system")
    assert system and "读不懂一份改动结果" in system[0].content


def test_the_source_rule_only_appears_when_the_lane_is_on(cfg, store):
    context = build_wake_context(store, cfg, query="改自己")
    off = build_messages(context, "在吗", tools_enabled=True, world_enabled=True)
    on = build_messages(context, "在吗", tools_enabled=True, world_enabled=True, self_change_enabled=True)

    marker = "关于改自己（你现在能了）"
    assert not any(marker in message["content"] for message in off)
    assert any(marker in message["content"] for message in on)
    assert SOURCE_RULE.strip()[:20] in "".join(message["content"] for message in on)

def test_the_same_failure_arriving_again_is_said_only_once(cfg, store):
    """合入器每跑一次都写一份结果；同一份失败重复回来，L1 里只该有一句。"""
    service, now = _service(cfg, store)
    results = Path(cfg.home) / "apply-results"
    results.mkdir(parents=True)
    payload = {
        "id": "prop_20261026T115538_5de5ce",
        "ok": False,
        "stage": "verify",
        "rolled_back": False,
        "error": "git apply --check 失败（退出码 1）：docs/NOTES.md: already exists in working directory",
        "env": {"base_commit": "fc708ae", "commit": "e4bea08"},
        "ts": now.isoformat(),
    }
    for _ in range(3):
        (results / "prop_20261026T115538_5de5ce.json").write_text(json.dumps(payload), encoding="utf-8")
        service._ingest_apply_results()

    events = _actions(store)
    assert len(events) == 1
    assert "verify" in events[0].content

