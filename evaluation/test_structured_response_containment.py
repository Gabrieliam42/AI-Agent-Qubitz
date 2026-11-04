"""HTTP response-to-real-orchestrator failures without inference or model loading."""
from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_creation_recovery_provenance as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation import test_verified_recovery_handoff as handoff
from evaluation.disposable_followup_matrix import runner_snapshot
from evaluation.test_snapshot_behavior_recovery import AFTER, BEFORE

variant = receipts.variant
runner = receipts.runner


def response_body(mode):
    if mode == "backend_error":
        return {"error": {"message": "fixture backend unavailable"}}
    message = {"content": '{"patches":[]}', "reasoning_content": ""}
    finish = "stop"
    if mode == "length":
        message = {"content": "", "reasoning_content": "reasoning-only " * 100}
        finish = "length"
    if mode == "empty":
        message["content"] = ""
    if mode == "malformed":
        message["content"] = '{"patches":'
    if mode == "wrong_root":
        message["content"] = "[]"
    if mode == "missing_finish":
        finish = None
    if mode == "valid_but_truncated":
        finish = "length"
    return {"id": "captured-shape-fixture", "choices": [{"message": message, "finish_reason": finish}]}


def wire_transport(variant, runner, monkeypatch, mode, on_response=None):
    base = variant._APP.base
    client = object.__new__(base.LlamaCppClient)
    client.config = runner.config
    client.transport = SimpleNamespace(label="direct", base_url="http://127.0.0.1:1")
    monkeypatch.setattr(client, "ensure_model", lambda name: name)
    monkeypatch.setattr(client, "_probe_with_fallback", lambda _path: {
        "status_code": 200, "json": {"default_generation_settings": {"n_ctx": runner.config.num_ctx}},
    })
    requests = []

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def close(self):
            pass

        @contextmanager
        def stream(self, method, url, **kwargs):
            requests.append(kwargs["json"])
            if on_response:
                on_response()
            raw = b"not-json" if mode == "invalid_envelope" else json.dumps(response_body(mode)).encode()
            yield httpx.Response(200, request=httpx.Request(method, url), content=raw)

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: HTTPClient())
    monkeypatch.setattr(runner, "_get_llm", lambda: client)
    return client, requests


@pytest.mark.parametrize("mode,expected", [
    ("length", "output_limit"), ("valid_but_truncated", "output_limit"),
    ("empty", "empty_content"), ("malformed", "invalid_json"), ("wrong_root", "invalid_json"),
    ("missing_finish", "incomplete_response"), ("invalid_envelope", "invalid_response_envelope"),
])
def test_real_transport_classifies_failure_without_replay(variant, runner, monkeypatch, mode, expected):
    client, requests = wire_transport(variant, runner, monkeypatch, mode)
    with pytest.raises((ValueError, RuntimeError)) as error:
        client.qubitz_json_chat(model_name=runner.config.model_name, messages=[{"role": "user", "content": "Edit."}],
                                schema_name="qubitz_transaction_proposal", schema={"type": "object"}, num_predict=4096)
    assert len(requests) == 1
    assert error.value.receipt["outcome"] == expected
    assert error.value.receipt["retry_permitted"] is False
    assert error.value.receipt["schema_name"] == "qubitz_transaction_proposal"


@pytest.mark.parametrize("mode", ["length", "empty", "malformed", "missing_finish", "wrong_schema", "invalid_envelope", "backend_error"])
def test_failed_proposal_is_partial_and_same_runner_accepts_new_task(variant, runner, tmp_path, monkeypatch, mode):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    passes = []
    _client, requests = wire_transport(variant, runner, monkeypatch, mode)
    host_calls = receipts.install_host(variant, runner, monkeypatch)

    async def model_pass(self, prompt, callback=None):
        passes.append(prompt)
        broker = self._tool_permission_broker
        if len(passes) == 2:
            following = tmp_path / "recovered.txt"
            creation.record_creation(broker, following, "ready")
            return "Saved recovered.txt."
        boundaries.record_snapshot(broker, target)
        host = boundaries.host_for(variant, broker)
        rejected = await host.call_tool("apply_text_patch", {
            "path": target.name, "replacements": [{"old_text": "not current", "new_text": AFTER}],
        })
        assert rejected.isError and not host.dispatched
        self._raise_if_cancelled()
        return "Not changed."

    monkeypatch.setattr(handoff.parent_runner(runner), "_run_async", model_pass)
    if mode == "backend_error":
        from runtime.structured_response import StructuredBackendError

        with pytest.raises(StructuredBackendError):
            asyncio.run(runner._run_async("Edit calculator.py to add divide(a, b). Preserve add."))
        assert len(passes) == len(requests) == 1 and target.read_text() == BEFORE
        assert not runner_snapshot(runner)["recovery_receipts"]
        return
    answer = asyncio.run(runner._run_async("Edit calculator.py to add divide(a, b), raising ValueError for a zero divisor. Preserve add."))
    assert len(passes) == len(requests) == 1
    assert "[Completion status: partial]" in answer and target.read_text() == BEFORE
    assert not any(name == "apply_text_patch" for name, _arguments in host_calls)
    failures = runner_snapshot(runner)["recovery_receipts"]
    assert len(failures) == 1 and failures[0]["success"] is False
    assert failures[0]["retry_permitted"] is False
    assert not runner._tool_permission_broker.evidence
    following = asyncio.run(runner._run_async("Create recovered.txt containing exactly ready."))
    assert "[Completion status: verified]" in following
    assert (tmp_path / "recovered.txt").read_text() == "ready"
    assert runner_snapshot(runner)["recovery_receipts"] == []
    assert len(requests) == 1 and len(passes) == 2


def test_cancellation_during_bad_response_is_not_converted_to_partial(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    client, requests = wire_transport(variant, runner, monkeypatch, "malformed", runner.request_cancel)
    broker = receipts.begin(runner, "Edit calculator.py.")
    broker.stop_callback = runner._request_wrapper_stop
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        with pytest.raises((asyncio.CancelledError, variant._APP.base.RunCancelled)):
            client.qubitz_json_chat(model_name=runner.config.model_name, messages=[], schema_name="proposal",
                                    schema={"type": "object"}, num_predict=4096)
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert len(requests) == 1 and target.read_text() == BEFORE


@pytest.mark.parametrize("mode", ["length", "malformed", "missing_finish"])
def test_numeric_response_failure_preserves_committed_edit_and_receipt(variant, runner, tmp_path, monkeypatch, mode):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    _client, requests = wire_transport(variant, runner, monkeypatch, mode)
    receipts.install_host(variant, runner, monkeypatch)
    passes = []

    async def model_pass(self, prompt, callback=None):
        passes.append(prompt)
        broker = self._tool_permission_broker
        broker.required_postconditions.add("tests")
        broker.transaction_originals[str(target)] = {"data": BEFORE.encode()}
        target.write_text(AFTER)
        broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
            "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        self._raise_if_cancelled()
        return "Saved the edit."

    monkeypatch.setattr(handoff.parent_runner(runner), "_run_async", model_pass)
    answer = asyncio.run(runner._run_async("Add divide(a, b) to calculator.py, raising ValueError for a zero divisor. Preserve add."))
    assert "[Completion status: partial]" in answer and target.read_text() == AFTER
    assert len(passes) == len(requests) == 1
    recorded = runner_snapshot(runner)["recovery_receipts"]
    assert len(recorded) == 1 and recorded[0]["phase"] == "numeric_verification"
    assert not recorded[0]["success"]
    verification = runner_snapshot(runner)["verification_receipts"]
    assert len(verification) == 1 and verification[0]["success"] is False
    assert verification[0]["phase"] == "proposal" and verification[0]["checks"] == []
    assert not verification[0]["module_executed"] and verification[0]["outcome"] == "unavailable"
