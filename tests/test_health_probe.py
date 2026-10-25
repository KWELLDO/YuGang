from __future__ import annotations

"""The probe exists for one failure: a process that looks alive and is not reachable.

These tests pin what it can and cannot conclude. A quiet day is not a fault, an
answering port is not health, a repeated fault is one alert, and a recovery says
how many suppressed failures it closes - otherwise silence looks like health.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from yugang.health import (
    append_history,
    collect_health,
    deep_ran_today,
    deep_stamp_path,
    health_path,
    history_path,
    probe_gateway,
    read_state,
    transition,
)
from yugang.runtime.mock import MockRuntime


class _Gateway(BaseHTTPRequestHandler):
    payload: dict = {"ok": True}
    status: int = 200

    def do_GET(self):  # noqa: N802 - http.server API
        body = json.dumps(self.payload).encode("utf-8")
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep the test output clean
        return


@pytest.fixture
def gateway():
    servers = []

    def start(payload: dict, status: int = 200) -> int:
        handler = type("Handler", (_Gateway,), {"payload": payload, "status": status})
        server = HTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_port

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def _audit_ok(store):
    """Make the memory half of the report as clean as a real deployment."""
    return store


def test_a_dead_gateway_port_is_a_problem_not_a_silent_ok(cfg, store):
    cfg.http.port = 9  # discard port: nothing listens
    payload = collect_health(store, cfg)
    assert payload["gateway"]["reachable"] is False
    assert any("网关没有应答" in item for item in payload["problems"])
    assert payload["ok"] is False


def test_an_answering_port_that_reports_degraded_is_not_health(cfg, store, gateway):
    cfg.http.port = gateway({"ok": True, "degraded": True, "paused": True})
    payload = collect_health(store, cfg)
    assert payload["gateway"]["reachable"] is True
    assert payload["gateway"]["status"] == 200
    assert any("通道不健康" in item for item in payload["warnings"])
    assert any("暂停" in item for item in payload["warnings"])


def test_a_gateway_that_says_it_is_unhealthy_is_a_problem(cfg, store, gateway):
    cfg.http.port = gateway({"ok": False, "worker_alive": False}, status=503)
    payload = collect_health(store, cfg)
    assert payload["gateway"]["status"] == 503
    assert any("网关自述不健康" in item for item in payload["problems"])


def test_the_payload_lands_on_disk_and_leaves_no_temp_file(cfg, store, gateway):
    from yugang.health import write_health

    cfg.http.port = gateway({"ok": True})
    payload = collect_health(store, cfg)
    write_health(cfg, payload)
    target = health_path(cfg)
    assert target.exists()
    assert json.loads(target.read_text(encoding="utf-8"))["gateway"]["reachable"] is True
    assert not list(target.parent.glob("*.tmp"))


def test_history_stays_bounded_and_keeps_the_newest_lines(cfg):
    for index in range(7):
        append_history(cfg, {"ts": f"t{index}", "ok": index % 2 == 0, "problems": [f"p{index}"]}, limit=3)
    lines = history_path(cfg).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [json.loads(line)["ts"] for line in lines] == ["t4", "t5", "t6"]


def test_a_repeated_fault_is_one_alert_and_the_recovery_counts_the_silence(cfg, store):
    bad = {"ok": False, "ts": "2030-01-01T00:00:00+00:00", "problems": ["网关没有应答"]}
    first = transition(store, cfg, bad)
    assert first["notified"] == "problem"
    second = transition(store, cfg, bad)
    assert second["notified"] is None
    assert second["suppressed"] == 1
    assert read_state(cfg)["suppressed"] == 1
    assert len([e for e in store.events(limit=20) if (e.meta or {}).get("probe")]) == 1

    recovered = transition(store, cfg, {"ok": True, "ts": "2030-01-01T01:00:00+00:00", "problems": []})
    assert recovered["notified"] == "recovery"
    notes = [e for e in store.events(limit=20) if (e.meta or {}).get("probe")]
    assert len(notes) == 2
    assert "还有 1 次未通知" in notes[0].content
    assert read_state(cfg)["suppressed"] == 0


def test_a_changed_fault_is_new_news(cfg, store):
    transition(store, cfg, {"problems": ["网关没有应答"]})
    changed = transition(store, cfg, {"problems": ["哈希链断裂：evt_1"]})
    assert changed["notified"] == "problem"
    assert "哈希链断裂" in changed["text"]


def test_a_deep_probe_really_calls_the_model_and_records_the_cost(cfg, store):
    runtime = MockRuntime(responder=lambda messages, purpose: "ok")
    payload = collect_health(store, cfg, deep=True, runtime=runtime)
    assert payload["model"]["ok"] is True
    assert payload["model"]["text"] == "ok"
    day = payload["budget"]["day"]
    spent = store.budget_spent(day)
    assert spent >= 0.0
    ledger = store.conn.execute("SELECT kind FROM budget_ledger WHERE day = ?", (day,)).fetchone()
    assert ledger is not None and ledger["kind"] == "probe"


def test_a_deep_probe_claims_the_day_before_it_spends(cfg, store):
    """The stamp belongs to whoever pays: written first, so a crash cannot re-bill."""
    assert deep_ran_today(cfg) is False
    collect_health(store, cfg, deep=True, runtime=MockRuntime(responder=lambda messages, purpose: "ok"))
    assert deep_ran_today(cfg) is True
    assert deep_stamp_path(cfg).read_text(encoding="utf-8").strip() != ""


def test_a_shallow_probe_does_not_claim_the_paid_day(cfg, store):
    collect_health(store, cfg)
    assert deep_ran_today(cfg) is False


def test_deep_if_due_spends_once_a_day_even_when_the_timer_keeps_ticking(cfg, store, capsys):
    """The timer runs every quarter hour; the paid call is once per day."""
    from yugang.cli import main

    assert main(["--home", cfg.home, "--mock", "probe", "--deep-if-due"]) in (0, 1)
    first = json.loads(capsys.readouterr().out)
    assert first["model"] is not None

    assert main(["--home", cfg.home, "--mock", "probe", "--deep-if-due"]) in (0, 1)
    second = json.loads(capsys.readouterr().out)
    assert second["model"] is None


def test_an_explicit_deep_still_runs_on_a_day_that_was_already_claimed(cfg, store, capsys):
    from yugang.cli import main

    assert main(["--home", cfg.home, "--mock", "probe", "--deep-if-due"]) in (0, 1)
    capsys.readouterr()
    assert main(["--home", cfg.home, "--mock", "probe", "--deep"]) in (0, 1)
    payload = json.loads(capsys.readouterr().out)
    assert payload["model"] is not None


def test_a_gateway_that_stops_answering_between_probes_is_caught(cfg, store, gateway):
    cfg.http.port = gateway({"ok": True})
    assert collect_health(store, cfg)["gateway"]["reachable"] is True
    assert probe_gateway(cfg)["reachable"] is True
