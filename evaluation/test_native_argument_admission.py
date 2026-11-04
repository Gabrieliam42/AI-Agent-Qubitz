"""Reject incomplete native arguments before dispatch or conversation history."""
from __future__ import annotations

import ast
import contextvars
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import read_variant_source

variant = receipts.variant


def native(arguments, name="read_file_snapshot", identifier="call-1"):
    return {"id": identifier, "type": "function", "function": {
        "name": name, "arguments": arguments,
    }}


def response(*calls):
    return {"message": {"role": "assistant", "content": "", "tool_calls": list(calls)},
            "finish_reason": "tool_calls"}


@pytest.fixture(scope="module")
def compiled(variant):
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    node = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "one_tool_chat")
    module = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    ), node], type_ignores=[])
    namespace = dict(vars(variant), base=variant._APP.base)
    exec(compile(ast.fix_missing_locations(module), variant.__file__, "exec"), namespace)  # noqa: S102
    return namespace


@pytest.fixture
def admission(compiled):
    seen, events = [], []

    class Owner:
        def __init__(self):
            self._cancel_event = threading.Event()
            self._qubitz_pending_tool_batch = []

        def stop(self, *_args):
            pass

    owner = Owner()
    broker = SimpleNamespace(
        stop_callback=owner.stop, callback=lambda role, text: events.append((role, text)),
        _tool_argument_recovery_used=False,
    )
    compiled["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("admission_broker", default=broker)
    request = {"messages": [{"role": "user", "content": "Inspect status.txt."}],
               "tools": [{"type": "function", "function": {
                   "name": "read_file_snapshot", "parameters": {
                       "type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"],
                   },
               }}], "num_predict": 4096}

    def run(*replies):
        remaining = iter(replies)

        def chat(_client, **kwargs):
            seen.append(copy.deepcopy(kwargs))
            return next(remaining)

        compiled["original_chat"] = chat
        return compiled["one_tool_chat"](None, **request)

    return SimpleNamespace(run=run, broker=broker, owner=owner, request=request,
                           seen=seen, events=events)


@pytest.mark.parametrize("arguments", ['{"path":"status.txt"}', {"path": "status.txt"}])
def test_valid_native_calls_are_unchanged(admission, arguments):
    original = response(native(arguments))
    before = copy.deepcopy(original)
    assert admission.run(original) == before
    assert original == before and len(admission.seen) == 1
    assert not admission.events and not admission.broker._tool_argument_recovery_used


@pytest.mark.parametrize("arguments", ['{"path":"unfinished', "", "[]", "null", "true", "7"])
def test_invalid_native_arguments_get_one_new_native_response(admission, arguments):
    rejected = response(native(arguments))
    original = copy.deepcopy(rejected)
    corrected = response(native('{"path":"status.txt"}', identifier="corrected"))
    request_before = copy.deepcopy(admission.request)
    assert admission.run(rejected, corrected) == corrected
    assert len(admission.seen) == 2
    assert rejected == original and admission.request == request_before
    assert admission.broker._tool_argument_recovery_used
    assert admission.seen[1]["tools"] == request_before["tools"]
    assert admission.seen[1]["num_predict"] == request_before["num_predict"]
    assert not any(message.get("tool_calls") for message in admission.seen[1]["messages"])
    role, text = admission.events[0]
    assert role == "native_tool_call_rejected"
    receipt = json.loads(text)
    assert receipt["tool_calls"] == original["message"]["tool_calls"]
    assert receipt["dispatch_attempted"] is False
    assert receipt["mutation_dispatched"] is False
    assert admission.owner._qubitz_pending_tool_batch == corrected["message"]["tool_calls"]


def test_second_malformed_response_stops_without_dispatch(admission):
    with pytest.raises(ValueError, match="native tool-call arguments"):
        admission.run(response(native("{")), response(native("{")))
    assert len(admission.seen) == 2 and len(admission.events) == 2
    assert admission.owner._qubitz_pending_tool_batch == []


@pytest.mark.parametrize("blocker", ["used", "cancelled", "no_owner"])
def test_recovery_budget_and_cancellation_are_respected(admission, blocker):
    if blocker == "used":
        admission.broker._tool_argument_recovery_used = True
    elif blocker == "cancelled":
        admission.owner._cancel_event.set()
    else:
        admission.broker.stop_callback = None
    with pytest.raises(ValueError, match="native tool-call arguments"):
        admission.run(response(native("{")))
    assert len(admission.seen) == 1 and len(admission.events) == 1
    assert admission.owner._qubitz_pending_tool_batch == []


def test_mixed_batch_is_withheld_until_the_model_replaces_it(admission):
    valid = native('{"path":"status.txt"}', identifier="valid-sibling")
    rejected = response(valid, native("{", identifier="broken-sibling"))
    corrected = response(valid, native('{"path":"other.txt"}', identifier="new-sibling"))
    before = copy.deepcopy(rejected)
    assert admission.run(rejected, corrected) == corrected
    assert rejected == before
    assert json.loads(admission.events[0][1])["tool_calls"] == before["message"]["tool_calls"]


def test_printed_json_correction_never_becomes_a_native_call(admission):
    printed = {"message": {"content": '{"path":"status.txt"}', "tool_calls": []},
               "finish_reason": "stop"}
    assert admission.run(response(native("{")), printed) == printed
    assert admission.owner._qubitz_pending_tool_batch == []


def test_recovered_native_rejection_remains_a_generation_problem(tmp_path):
    from evaluation.disposable_followup_matrix import generation_quality

    events = tmp_path / "events.jsonl"
    events.write_text(json.dumps({
        "kind": "callback", "role": "native_tool_call_rejected", "turn": 1, "time": 1,
        "message": json.dumps({"tool_calls": [native("{")], "dispatch_attempted": False}),
    }) + "\n", encoding="utf-8")
    observed = generation_quality(events, 1)
    assert "invalid_native_tool_arguments" in observed["problems"]
    assert observed["status"] == "problem"
