"""Captured invalid edit and recovery-history failures, without inference."""
from __future__ import annotations

import ast
import asyncio
import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_http_client_startup as startup_tests
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
startup = startup_tests.startup


@pytest.fixture
def python_case(variant, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode(variant.ACCESS_MODE_FULL)
    broker.begin_turn("Edit calculator.py to add divide(a, b), preserving add.", None)
    boundaries.record_snapshot(broker, target)
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    host = boundaries.host_for(variant, broker, server)
    host._qubitz_tool_contracts = {name: tools[name] for name in ("apply_text_patch", "apply_text_patches")}
    return SimpleNamespace(target=target, broker=broker, host=host)


@pytest.mark.parametrize("suffix", [
    '\n\ndef divide(a, b):\n    raise ValueError(\\"zero divisor\\")\n',
    "\nreturn 1\n", "\nvalue = '\x00'\n",
])
def test_invalid_python_candidate_is_rejected_before_dispatch(python_case, suffix):
    case = python_case
    original = case.target.read_bytes()
    args = {"path": case.target.name, "replacements": [{"old_text": original.decode(),
                                                         "new_text": original.decode() + suffix}]}
    before = copy.deepcopy(args)
    result = asyncio.run(case.host.call_tool("apply_text_patch", args))
    assert result.isError and result.structuredContent["status"] == "invalid_patch_content"
    payload = result.structuredContent
    assert payload["line"] and not payload["dispatch_attempted"] and not payload["mutation_dispatched"]
    assert payload["qubitz_recovery"]["outcome"] == "rejected_before_dispatch"
    assert not case.host.dispatched and case.target.read_bytes() == original and args == before
    assert case.broker.transaction_recovery_required


def test_batch_invalid_python_preserves_every_file(python_case):
    case = python_case
    other = case.broker.workspace / "notes.txt"
    other.write_text("original\n")
    case.broker.begin_turn("Edit calculator.py and notes.txt to add divide and update the note.", None)
    for path in (case.target, other):
        boundaries.record_snapshot(case.broker, path)
    before = {path: path.read_bytes() for path in (case.target, other)}
    args = {"patches": [{"path": path.name, "replacements": [{"old_text": before[path].decode(),
             "new_text": "updated\n" if path == other else "def divide(:\n"}]} for path in (other, case.target)]}
    result = asyncio.run(case.host.call_tool("apply_text_patches", args))
    assert result.isError and result.structuredContent["status"] == "invalid_patch_content"
    assert not case.host.dispatched and {path: path.read_bytes() for path in before} == before


def test_complete_candidate_not_intermediate_replacements_is_validated(python_case):
    case = python_case
    args = {"path": case.target.name, "replacements": [
        {"old_text": "def add(a, b):", "new_text": "def add(a, b)"},
        {"old_text": "def add(a, b)\n", "new_text": "def add(a, b):\n"},
        {"old_text": "return a + b", "new_text": "return a + b + 0"},
    ]}
    result = asyncio.run(case.host.call_tool("apply_text_patch", args))
    assert not result.isError and len(case.host.dispatched) == 1
    ast.parse(case.target.read_text())


def history():
    return [{"role": "system", "content": "Follow the task."}, {"role": "user", "content": "Edit calculator.py."},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "read-1", "type": "function",
             "function": {"name": "read_file_snapshot", "arguments": '{"path":"calculator.py"}'}}]},
            {"role": "tool", "tool_call_id": "read-1", "content": '{"sha256":"verified","content":"exact"}'}]


def test_recovery_after_tool_result_preserves_pairs_and_does_not_add_user_role(variant):
    append = boundaries.embedded_node(variant, ast.FunctionDef, "_append_adapter_instruction", {})
    messages = history()
    original = copy.deepcopy(messages)
    updated = append(messages, "The existing target has been inspected. Use a focused native edit tool call now.")
    assert [item["role"] for item in updated] == ["system", "user", "assistant", "tool"]
    assert updated[-2:] == original[-2:] and updated[0] == original[0] and messages == original
    assert updated[1]["content"].startswith(original[1]["content"])
    assert "existing target" in updated[1]["content"]


@pytest.mark.parametrize("damage", ["missing_result", "orphan_result", "duplicate_id", "wrong_id"])
def test_invalid_tool_history_never_reaches_http(startup, monkeypatch, damage):
    messages = history()
    if damage == "missing_result":
        messages.pop()
    elif damage == "orphan_result":
        messages.pop(2)
    elif damage == "duplicate_id":
        messages[2]["tool_calls"] *= 2
    else:
        messages[-1]["tool_call_id"] = "wrong"
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("Malformed history reached HTTP"))
    from evaluation.test_context_and_campaign_regressions import request
    with pytest.raises(ValueError, match="tool"):
        startup.client.chat(**{**request(), "messages": messages})


@pytest.mark.parametrize("prompt,allowed", [
    ("Make calculator.py intentionally invalid Python for a parser fixture.", True),
    ("Do not make calculator.py intentionally invalid Python.", False),
    ('Explain "make calculator.py intentionally invalid Python"; fix calculator.py.', False),
    ("Edit calculator.py and preserve working Python.", False),
])
def test_malformed_python_exception_requires_explicit_positive_request(prompt, allowed):
    from runtime.recovery_protocol import python_edit_error
    assert (python_edit_error("def broken(:", Path("calculator.py"), prompt) is None) is allowed


def test_syntax_validation_does_not_execute_or_unescape():
    from runtime.recovery_protocol import python_edit_error
    assert python_edit_error("raise RuntimeError('never executed')\n", Path("a.py"), "Edit a.py.") is None
    assert python_edit_error('value = \\"invalid\\"\n', Path("a.py"), "Edit a.py.") is not None
    assert python_edit_error("non-Python text", Path("a.txt"), "Edit a.txt.") is None


@pytest.mark.parametrize("message", ["ReadError", "500 Internal Server Error", "Server disconnected"])
def test_error_text_never_authorizes_generation_replay(startup, monkeypatch, message):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *_args, **_kwargs):
            calls.append(True)
            raise RuntimeError(message)
            yield  # pragma: no cover

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(RuntimeError, match=message):
        startup.invoke("answer")
    assert calls == [True]


def test_http_template_error_retains_status_and_is_not_retried(startup, monkeypatch):
    calls = []
    detail = "Jinja Exception: conversation roles must alternate user and assistant"

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            response = httpx.Response(500, request=httpx.Request(method, url),
                                      json={"error": {"message": detail}})
            try:
                yield response
            finally:
                response.close()

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(startup.base.QubitzChatRequestError, match="HTTP 500") as caught:
        startup.invoke("answer")
    assert caught.value.status_code == 500 and detail in str(caught.value) and calls and len(calls) == 1
