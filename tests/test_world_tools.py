from __future__ import annotations

import json

import pytest

from yugang.config import Config
from yugang.gateway.service import GatewayService
from yugang.ids import day_key, utcnow
from yugang.memory.store import MemoryStore
from yugang.memory.tools import MemoryToolbox
from yugang.protocol.wake import build_messages, build_wake_context
from yugang.runtime.mock import MockRuntime
from yugang.world.sandbox import backend_available
from yugang.world.tools import WorldToolbox


@pytest.fixture
def env(tmp_path):
    cfg = Config.default(str(tmp_path / "ayu"))
    cfg.model = "mock"
    cfg.ensure_dirs()
    store = MemoryStore(cfg.db_path)
    yield cfg, store
    store.close()


def _box(cfg, store) -> WorldToolbox:
    return WorldToolbox(cfg, store, now_fn=utcnow)


def _sandbox_or_skip():
    backend, detail = backend_available()
    if backend == "none":
        pytest.skip(f"no sandbox backend here: {detail}")
    return backend


# --------------------------------------------------------------------- tools
def test_tool_list_follows_the_configuration(env):
    cfg, store = env
    names = {schema["function"]["name"] for schema in _box(cfg, store).schemas()}
    assert names == {
        "web_fetch",
        "web_search",
        "files_list",
        "files_read",
        "files_write",
        "sandbox_run",
    }

    cfg.world.search_enabled = False
    names = {schema["function"]["name"] for schema in _box(cfg, store).schemas()}
    assert "web_search" not in names and "web_fetch" in names

    cfg.world.enabled = False
    assert _box(cfg, store).schemas() == []


def test_a_write_lands_in_the_workspace_and_becomes_an_experience(env):
    cfg, store = env
    result = _box(cfg, store).run("files_write", {"path": "notes/first.md", "content": "第一次写东西。"})
    assert result["ok"] is True, result

    from pathlib import Path

    written = Path(cfg.workspace) / "notes" / "first.md"
    assert written.read_text(encoding="utf-8") == "第一次写东西。"

    acts = [event for event in store.events(limit=20) if event.kind == "action"]
    assert len(acts) == 1
    assert acts[0].source == "ai-observation"
    assert "notes/first.md" in acts[0].content
    assert acts[0].meta["bytes"] == len("第一次写东西。".encode())


def test_a_write_cannot_escape_the_workspace(env):
    cfg, store = env
    box = _box(cfg, store)
    for path in ("../escape.md", "/etc/passwd", "notes/../../escape.md", "~/.ssh/authorized_keys"):
        result = box.run("files_write", {"path": path, "content": "nope"})
        assert result["ok"] is False, (path, result)
        assert result["error"]
    assert not (store and (__import__("pathlib").Path(cfg.workspace).parent / "escape.md")).exists()


def test_reads_are_logged_in_the_world_ledger_not_in_L1(env):
    cfg, store = env
    box = _box(cfg, store)
    act = box.run("files_write", {"path": "notes/a.md", "content": "一"})
    assert act["ok"] is True
    assert box.run("files_read", {"path": "notes/a.md"})["ok"] is True
    assert box.run("files_list", {"path": ""})["ok"] is True

    # Only the write is an experience; two reads are not worth remembering.
    assert [event.kind for event in store.events(limit=10)].count("action") == 1

    summary = store.world_summary(day_key(utcnow()))
    assert summary["calls"] == 3
    assert summary["by_tool"]["files_write"]["kind"] == "act"
    assert summary["by_tool"]["files_read"]["kind"] == "read"
    assert box.run("files_read", {"path": "../config.json"})["ok"] is False
    log = store.world_log(limit=10)
    assert log and all(set(row) >= {"ts", "tool", "kind", "ok"} for row in log)


def test_a_blocked_url_fails_before_any_network_call(env):
    cfg, store = env
    result = _box(cfg, store).run("web_fetch", {"url": "http://127.0.0.1:3198/health"})
    assert result["ok"] is False
    assert result["error"]
    assert "127.0.0.1" not in json.dumps(result.get("text") or "")


def test_the_memory_toolbox_hands_world_tools_to_the_model(env):
    cfg, store = env
    toolbox = MemoryToolbox(store, cfg, now_fn=utcnow, world=_box(cfg, store))
    names = {schema["function"]["name"] for schema in toolbox.schemas()}
    assert "sandbox_run" in names and "memory_search" in names

    result = toolbox.run("files_list", {"path": ""})
    assert result["ok"] is True
    assert toolbox.run("no_such_tool", {})["ok"] is False


# ------------------------------------------------------------------- sandbox
def test_the_sandbox_runs_a_command_and_cannot_reach_the_network(env):
    _sandbox_or_skip()
    cfg, store = env
    box = _box(cfg, store)
    probe = chr(10).join([
        "import socket",
        "socket.setdefaulttimeout(3)",
        "try:",
        "    socket.create_connection(('1.1.1.1', 443))",
        "    print('NETWORK')",
        "except Exception as exc:",
        "    print('BLOCKED', type(exc).__name__)",
    ])
    assert box.run("files_write", {"path": "probe.py", "content": probe + chr(10)})["ok"] is True

    result = box.run("sandbox_run", {"command": "python3 probe.py"})
    assert result["ok"] is True, result
    assert result["network"] is False
    assert "BLOCKED" in result["stdout"]
    assert "NETWORK" not in result["stdout"]

    text = box.run("sandbox_run", {"command": "python3 -c 'print(6*7)'"})
    assert "42" in text["stdout"]


def test_the_sandbox_keeps_the_operator_credentials_out(env, monkeypatch):
    _sandbox_or_skip()
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-do-not-leak")
    cfg, store = env
    result = _box(cfg, store).run(
        "sandbox_run",
        {"command": "python3 -c \"import os;print(os.environ.get('DEEPSEEK_API_KEY','CLEAN'))\""},
    )
    assert "sk-do-not-leak" not in result["stdout"]
    assert "CLEAN" in result["stdout"]


def test_a_command_leaves_an_experience_behind(env):
    _sandbox_or_skip()
    cfg, store = env
    result = _box(cfg, store).run("sandbox_run", {"command": "true"})
    assert result["ok"] is True
    acts = [event for event in store.events(limit=10) if event.kind == "action"]
    assert len(acts) == 1
    assert "沙箱" in acts[0].content
    assert acts[0].meta["network"] is False


def test_a_written_file_is_visible_to_a_command(env):
    _sandbox_or_skip()
    cfg, store = env
    box = _box(cfg, store)
    box.run("files_write", {"path": "note.md", "content": "她写的字"})
    result = box.run("sandbox_run", {"command": "cat note.md"})
    assert result["ok"] is True, result
    assert "她写的字" in result["stdout"]
    assert not result["cwd"].rstrip("/").endswith("/sandbox")


# ------------------------------------------------------------------- wiring
def test_the_service_reports_what_she_can_reach(env):
    cfg, store = env
    service = GatewayService(cfg, store, MockRuntime(responder=lambda messages, purpose: "我在。"))
    world = service.status()["world"]
    assert world["enabled"] is True
    assert "web_fetch" in world["tools"] and "sandbox_run" in world["tools"]
    assert world["sandbox"]["backend"] in {"bubblewrap", "unshare", ""}
    assert world["today"]["calls"] == 0


def test_the_world_rule_reaches_the_prompt_only_when_the_tools_are_on(env):
    cfg, store = env
    context = build_wake_context(store, cfg, query="", now=utcnow(), new_wake=True)
    with_world = build_messages(context, "在吗", tools_enabled=True, world_enabled=True)
    assert "世界工具" in with_world[0]["content"]
    without = build_messages(context, "在吗", tools_enabled=True, world_enabled=False)
    assert "世界工具" not in without[0]["content"]
    no_tools = build_messages(context, "在吗", tools_enabled=False, world_enabled=True)
    assert "世界工具" not in no_tools[0]["content"]
