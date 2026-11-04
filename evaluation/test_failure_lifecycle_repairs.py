"""Read-only reconciliation and request containment; no model execution."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from evaluation import campaign_lifecycle as lifecycle
from evaluation import disposable_followup_matrix as matrix
from evaluation import test_campaign_lifecycle_contracts as existing
from evaluation import test_context_and_campaign_regressions as context
from runtime.request_failures import GenerationRejectedError

context_runtime = context.runtime
ERROR = {"code": 500, "type": "server_error",
         "message": "The model produced output that does not match the expected peg-native format"}


def test_actual_stream_rejection_is_typed_and_not_replayed(context_runtime, monkeypatch):
    base, client, _ = context_runtime
    client.ensure_model("fixture")
    client.transport = type("Transport", (), {"label": "direct", "base_url": "http://fixture.test"})()
    client._qubitz_server_context_window = None
    client._probe_with_fallback = lambda _path: {"status_code": 200, "json": {
        "build_info": "fixture-build", "chat_template": "fixture-template",
        "default_generation_settings": {"n_ctx": 16384}}}
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, text="data: " + json.dumps({"error": ERROR}) + "\n\n")

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    base.set_qubitz_stream_callback(lambda _text: None)
    try:
        with pytest.raises(GenerationRejectedError) as failure:
            client.chat(**context.request())
        assert len(requests) == 1
        assert failure.value.receipt["http_status"] == 200
        assert failure.value.receipt["request_id"] == requests[0].headers["X-Qubitz-Request-Id"]
        assert failure.value.receipt["tool_call_accepted"] is False
        assert failure.value.receipt["partial_output_accepted"] is False
        assert failure.value.receipt["content_characters"] == 0
        assert failure.value.receipt["backend"]["build"] == "fixture-build"
        assert failure.value.receipt["backend"]["template_sha256"] == hashlib.sha256(b"fixture-template").hexdigest()
    finally:
        base.set_qubitz_stream_callback(None)


def test_constrained_json_rejection_is_typed_without_schema_fallback_replay(context_runtime, monkeypatch):
    _base, client, _ = context_runtime
    client.ensure_model("fixture")
    client.transport = type("Transport", (), {"label": "direct", "base_url": "http://fixture.test"})()
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"error": ERROR, "system_fingerprint": "fixture-backend"})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(GenerationRejectedError) as failure:
        client.qubitz_json_chat(model_name="fixture", messages=[{"role": "user", "content": "fixture"}],
                                schema_name="fixture", schema={"type": "object"}, num_predict=64)
    assert len(requests) == 1 and failure.value.receipt["http_status"] == 200
    assert failure.value.receipt["request_id"] == requests[0].headers["X-Qubitz-Request-Id"]
    assert failure.value.receipt["backend"]["response_fingerprint"] == "fixture-backend"


@pytest.mark.parametrize("damage", ["none", "wait", "identity", "live", "unknown_summary", "missing_summary"])
def test_terminal_projection_uses_bound_receipts_not_stale_flag(tmp_path, monkeypatch, damage):
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    output = tmp_path / "result"
    output.mkdir()
    directory = tmp_path / "result_supervision"
    directory.mkdir()
    expected = {"pid": 12345, "creation_ticks": "100", "executable": "fixture", "command_line": "fixture"}
    state = tmp_path / "work_state.json"
    state.write_text(json.dumps({"status": "running", "output": "result", "identity": expected}))
    (directory / "launch_receipt.json").write_text(json.dumps({"identity": expected}))
    waited = {"identity": expected, "actual_wait": damage != "wait", "exit_code": 2}
    if damage == "identity":
        waited["identity"] = {**expected, "creation_ticks": "other"}
    (directory / "coordinator_exit.json").write_text(json.dumps(waited))
    (output / "summary.json").write_text(json.dumps({"status": "running" if damage == "unknown_summary" else "stopped_infrastructure"}))
    if damage == "missing_summary":
        (output / "summary.json").unlink()
    identity = lambda _pid: expected if damage == "live" else None
    if damage in {"none", "unknown_summary", "missing_summary"}:
        after = lifecycle.reconcile_control_state(state, identity=identity)
        assert after["status"] == ("stopped_infrastructure" if damage == "none" else "exit_recorded_checkpoint_unknown")
        assert after["actual_wait_recorded"]
        assert lifecycle.reconcile_control_state(state, identity=identity) == after
        receipt = json.loads(state.with_name("terminal_reconciliation.json").read_text())
        assert receipt["previous_state"]["status"] == "running" and receipt["no_dispatch"]
    else:
        with pytest.raises(ValueError):
            lifecycle.reconcile_control_state(state, identity=identity)
        assert json.loads(state.read_text())["status"] == "running"


def test_rejected_generation_cannot_be_a_task_pass_or_hidden_infrastructure_failure():
    report = {"status": "passed", "issues": []}
    matrix.apply_gui_diagnostics(report, {"behavior_issues": [], "request_failures": [{"scope": "request"}]})
    assert report["status"] == "failed" and report["issues"] == ["generation_rejected"]
    matrix.apply_gui_diagnostics(report, {"behavior_issues": ["gui_logged_error"], "request_failures": [{}]})
    assert report["status"] == "harness_error"


def test_supervisor_projects_actual_harmless_child_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    output = tmp_path / "result"
    state = tmp_path / "work_state.json"
    state.write_text(json.dumps({"status": "authorized", "output": "result"}))
    calls = []

    def identity(pid):
        calls.append(pid)
        return existing.identity(pid) if len(calls) == 1 else None

    assert lifecycle.supervise(existing.command(output), output, identity=identity, control_state=state) == 0
    after = json.loads(state.read_text())
    assert len(calls) == 2 and after["actual_wait_recorded"] and after["status"] == "complete"


def test_aliased_control_state_never_dispatches(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"output": "result"}))
    alias = tmp_path / "alias.json"
    alias.symlink_to(state)
    with pytest.raises(ValueError, match="aliased"):
        lifecycle.supervise([], tmp_path / "result", identity=lambda _pid: None, control_state=alias,
                            popen=lambda *_args, **_kwargs: pytest.fail("Aliased launch dispatched"))
