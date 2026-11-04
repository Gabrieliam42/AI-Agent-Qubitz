"""Model-free repair context, format fallback and preservation boundaries."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_http_client_startup as startup_tests

variant = receipts.variant
runner = receipts.runner
startup = startup_tests.startup


@pytest.mark.parametrize("kind", ["current", "old_turn", "old_workspace", "failed", "truncated"])
def test_saved_contract_context_binds_only_current_complete_mcp_data(variant, runner, monkeypatch, kind):
    prompt = "Call the MCP lookup tool, then update result.json with only the returned value and label fields."
    target = runner.workspace / "result.json"
    target.write_text('{"value": 7, "label": "old"}\n')
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets.add(str(target))
    receipt = {"success": True, "tool": "lookup", "turn_id": broker.turn_id,
               "workspace": str(runner.workspace.resolve()),
               "value": {"value": 11, "label": "observed-current"}}
    if kind == "old_turn":
        receipt["turn_id"] = "previous"
    elif kind == "old_workspace":
        receipt["workspace"] = str(runner.workspace / "other")
    elif kind == "failed":
        receipt["success"] = False
    elif kind == "truncated":
        receipt["truncated"] = True
    broker.last_mcp_result = receipt
    calls = receipts.install_host(variant, runner, monkeypatch)
    context = asyncio.run(runner._wrapper_saved_contract_snapshots())
    assert ("observed-current" in context) is (kind == "current")
    assert str(runner.workspace.resolve()) in context
    assert "nonempty" in context and "old_text" in context
    assert all(name == "read_file_snapshot" for name, _args in calls)
    assert json.loads(target.read_text()) == {"value": 7, "label": "old"}


@pytest.mark.parametrize("detail,fallback", [
    ("response_format json_schema is not supported", True),
    ("unknown response_format json_schema", True),
    ("invalid prompt: context window exceeded", False),
    ("schema required field missing", False),
])
def test_only_format_capability_rejection_advances_format(startup, monkeypatch, detail, fallback):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                yield httpx.Response(400, request=httpx.Request(method, url), json={"error": {"message": detail}})
            else:
                with super().stream(method, url, **kwargs) as response:
                    yield response

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    if fallback:
        assert startup.invoke("constrained_json") == {"ok": True}
        assert len(calls) == 2 and calls[1]["response_format"] == {"type": "json_object"}
    else:
        with pytest.raises(RuntimeError, match="HTTP 400"):
            startup.invoke("constrained_json")
        assert len(calls) == 1


def test_successful_generation_with_invalid_json_is_not_replayed(startup, monkeypatch):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            yield httpx.Response(200, request=httpx.Request(method, url), json={
                "choices": [{"message": {"content": '{"value":'}, "finish_reason": "stop"}],
            })

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(ValueError):
        startup.invoke("constrained_json")
    assert len(calls) == 1


def test_preservation_answer_keeps_numeric_and_exception_limits_explicit(variant):
    answer = variant._compatibility_evidence_answer("Did the edit preserve all existing behavior?", "yes", {
        "sources": {"library.py": {"before_text": "return sum(items)", "after_text": "return sum(items) * 1.0",
                                    "after_sha256": "a" * 64}},
        "executions": [{"command": "python check.py", "return_code": 0}],
    })
    assert all(word in answer for word in ("types", "precision", "overflow", "exceptions", "unverified"))
    assert "does not prove equivalence" in answer


def test_readiness_wait_honors_user_cancellation_before_probing(variant, runner):
    method = variant._APP.base.LlamaCppClient._wait_for_model_state
    context = method.__globals__["_TOOL_CALL_CONTEXT"]
    token = context.set(runner._tool_permission_broker)
    runner._cancel_source = "user"
    try:
        with pytest.raises(asyncio.CancelledError, match="readiness wait"):
            method(object(), 2)
    finally:
        context.reset(token)
