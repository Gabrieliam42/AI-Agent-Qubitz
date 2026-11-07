"""Model-free regression coverage for the retained bridge and monitor failures."""
from __future__ import annotations

import ast
import base64
import json
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pytest

from evaluation import complex_smoke_runtime
from evaluation import test_campaign_monitoring as monitoring
from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_reasoning_display as reasoning
from runtime import read_variant_source
from runtime.paths import DEFAULT_VARIANT, variant_path
from runtime.request_budget import request_budget, request_timeout
from runtime.source import embedded_source

monitor = monitoring.monitor
runtime = contracts.runtime
display_runtime = reasoning.display_runtime


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_invalid_deadline_never_becomes_an_unbounded_wait(value):
    with pytest.raises((ValueError, TimeoutError)):
        request_timeout(snapshot=lambda: {"remaining_phase_seconds": value})


def test_budget_scope_restores_after_failure_and_never_replenishes():
    remaining = [1450.0]
    with pytest.raises(RuntimeError), request_budget(lambda: {"remaining_phase_seconds": remaining[0]}):
        assert request_timeout() == 1450
        remaining[0] = 1400
        assert request_timeout() == 1400
        raise RuntimeError("fixture")
    assert request_timeout() == 600
    with pytest.raises(TimeoutError), request_budget(lambda: {"remaining_phase_seconds": 0, "expired": True}):
        request_timeout()


def bridge_class():
    source = embedded_source(read_variant_source(variant_path(DEFAULT_VARIANT)))[0]
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef) and n.name == "WindowsBridgeHTTPTransport")
    namespace = {"base64": base64, "json": json, "textwrap": textwrap, "subprocess": subprocess,
                 "DEFAULT_LLAMACPP_BASE_URL": "http://127.0.0.1:8001"}
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "bridge", "exec"), namespace)  # noqa: S102 - trusted local test subject
    return namespace["WindowsBridgeHTTPTransport"]


def test_bridge_and_inner_http_share_the_existing_remaining_allowance(monkeypatch):
    seen = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: seen.append((a, kw)) or SimpleNamespace(
        returncode=0, stdout='{"ok":true}', stderr=""))
    bridge = bridge_class()()
    with request_budget(lambda: {"remaining_phase_seconds": 1420.25}):
        assert bridge.post_json("/v1/chat/completions", {"messages": []}) == {"ok": True}
    assert len(seen) == 1
    assert seen[0][1]["timeout"] == 1420.25
    assert "-TimeoutSec 1421" in seen[0][1]["input"]
    assert "messages" not in " ".join(seen[0][0][0])


def test_expired_bridge_budget_never_launches_a_helper(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("Expired request launched"))
    with pytest.raises(TimeoutError), request_budget(lambda: {"expired": True, "remaining_phase_seconds": 0}):
        bridge_class()().post_json("/v1/chat/completions", {})


def test_streaming_client_inherits_budget_without_changing_generation_parameters(display_runtime, monkeypatch):
    import httpx

    base, client, namespace, _ = display_runtime
    owner = SimpleNamespace(_qubitz_request_budget=lambda: {"remaining_phase_seconds": 1400},
                            _cancel_source="")
    owner.stop = lambda *_: None
    # A bound callback is how the actual runner owns the transport context.
    class Owner:
        _qubitz_request_budget = staticmethod(owner._qubitz_request_budget)
        _cancel_source = ""

        def stop(self, *_):
            pass

    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(stop_callback=Owner().stop))
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def stream(self, *_a, **_kw):
            raise httpx.ReadTimeout("fixture")

    monkeypatch.setattr(httpx, "Client", Client)
    base.set_qubitz_stream_callback(lambda _: None)
    client.transport = SimpleNamespace(label="direct", base_url="http://fixture")
    client._call_with_fallback = lambda *_: pytest.fail("Request replayed")
    with pytest.raises(RuntimeError, match="not replayed"):
        client.chat(**contracts.request())
    assert calls[0]["timeout"].read == 1400
    assert calls[0]["timeout"].connect == 600


@pytest.mark.parametrize("code,status,attention", [(0, "complete", False), (0, "paused", False),
                                                   (2, "stopped_infrastructure", True), (0, "invalid_source_drift", True)])
def test_terminal_attention_is_durable_and_never_dispatches_stop(monitor, code, status, attention):
    (monitor.directory / "coordinator_exit.json").write_text(json.dumps({"actual_wait": True, "exit_code": code}))
    (monitor.output / "summary.json").write_text(json.dumps({"status": status}))
    monitor.stop = lambda *_: pytest.fail("Exited coordinator was stopped again")
    monitor.tick(terminal=True)
    alert = monitor.directory / "monitor_terminal_alert.json"
    before = alert.read_bytes() if alert.exists() else None
    monitor.tick(terminal=True)
    assert json.loads((monitor.directory / "monitor_state.json").read_text())["requires_attention"] is attention
    assert alert.exists() is attention
    if attention:
        assert alert.read_bytes() == before
        assert json.loads(before)["no_action_dispatched"]


def test_unknown_terminal_outcome_needs_attention_without_inventing_a_wait(monitor):
    monitor.tick(terminal=True)
    state = json.loads((monitor.directory / "monitor_state.json").read_text())
    assert state["requires_attention"]
    assert state["terminal_problem"] == {"status": "terminal_outcome_unknown"}


def test_timeout_helper_keeps_partial_output_and_actual_wait(monkeypatch):
    receipts = []
    # Restore the global class after exercising the real owner wrapper.
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)
    complex_smoke_runtime.propagate_owner("a" * 32, receipts)
    process = subprocess.Popen([sys.executable, "-u", "-c",
                                "import sys,time;print('ready',flush=True);sys.stdin.readline();print('started',flush=True);time.sleep(10)"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert process.stdout.readline().strip() == "ready"
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            process.communicate(input="go\n", timeout=1)
    finally:
        process.kill()
        process.wait()
    assert len(receipts) == 1
    row = receipts[0]
    assert row["actual_wait"] and row["communication_timeout_observed"] and row["exit_code"] is not None
    assert row["timeout_scope"] == "communicate_wait_not_command_outcome"
    assert "started" in (row["stdout"] or "")
    assert row["stdout_complete"] is False


def test_later_complete_communication_updates_the_same_timeout_receipt(monkeypatch):
    receipts = []
    monkeypatch.setattr(subprocess, "Popen", subprocess.Popen)
    complex_smoke_runtime.propagate_owner("a" * 32, receipts)
    process = subprocess.Popen([sys.executable, "-u", "-c", "import time;time.sleep(0.2);print('complete')"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(subprocess.TimeoutExpired):
        process.communicate(timeout=0.01)
    process.communicate(timeout=5)
    assert len(receipts) == 1
    assert receipts[0]["stdout"] == "complete\n"
    assert receipts[0]["stdout_complete"] and receipts[0]["stderr_complete"]
    assert receipts[0]["communication_timeout_observed"] and receipts[0]["actual_wait"]
