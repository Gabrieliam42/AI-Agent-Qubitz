"""Model-free contracts for unexecuted tool text and exact-edit recovery."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_file_content_recovery as file_recovery
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_reasoning_display as reasoning

display_runtime = reasoning.display_runtime
runtime = contracts.runtime
variant = receipts.variant
runner = receipts.runner

CYCLE = ('run_existing_entrypoint("ruff check")\n'
         'run_existing_entrypoint("ruff check --fix")\n'
         'run_existing_entrypoint("ruff check")\n'
         'run_existing_entrypoint("tests")\n')
TOOLS = [{"type": "function", "function": {"name": "apply_text_patches"}}]


@pytest.mark.parametrize("split_chunks", [False, True])
@pytest.mark.parametrize("prefix", [
    '', 'apply_text_patches({\n  "patches": []\n})\n',
    '```python\n', 'def example():\n    pass\n',
])
@pytest.mark.parametrize("channel", ["content", "reasoning_content"])
def test_repetitive_tool_text_cannot_disable_its_own_guard(display_runtime, monkeypatch, prefix, channel, split_chunks):
    base, client, namespace, events = display_runtime
    namespace["ACCESS_MODE_FULL"] = "full"
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        current_prompt="In qfiles set each state line to state=final-a, preserving their workspace lines.",
        task_intent=SimpleNamespace(task_kind="edit_or_refactor_task"),
        access_mode="full", _content_tool_recovery_used=True,
        callback=lambda *event: events.append(event), _emit=lambda _message: None,
        stop_callback=stops.append,
    ))
    text = prefix + CYCLE * 40
    chunks = [reasoning.chunk(**{channel: text[i:i + 1]}) for i in range(len(text))] if split_chunks else [
        reasoning.chunk(**{channel: prefix}), *[reasoning.chunk(**{channel: CYCLE}) for _ in range(40)],
    ]
    payloads, _answers = reasoning.install_stream(monkeypatch, base, client, chunks)
    response = client.chat(**contracts.request(tools=TOOLS))
    assert len(payloads) == 1
    assert response["finish_reason"] == "repetition_guard"
    assert len(stops) == 1
    assert not response["message"]["tool_calls"]
    assert response["message"]["content"].count('run_existing_entrypoint') < 32


@pytest.mark.parametrize("mode", ["native_arguments", "explicit_repetition", "source_example"])
def test_tool_text_guard_preserves_requested_data_and_native_arguments(display_runtime, monkeypatch, mode):
    base, client, namespace, events = display_runtime
    namespace["ACCESS_MODE_FULL"] = "full"
    stops = []
    prompt = {"native_arguments": "Edit qfiles.",
              "explicit_repetition": "Repeat this line run_existing_entrypoint twenty times.",
              "source_example": "Show a Python source example with repeated tool calls. Do not execute it."}[mode]
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        current_prompt=prompt,
        task_intent=SimpleNamespace(task_kind="edit_or_refactor_task" if mode != "source_example" else "question"),
        access_mode="full", _content_tool_recovery_used=True,
        callback=lambda *event: events.append(event), _emit=lambda _message: None,
        stop_callback=stops.append,
    ))
    content = '```python\n' + CYCLE * 40 + '\n```'
    arguments = json.dumps({"text": content})
    call = {"index": 0, "id": "actual-1", "function": {"name": "apply_text_patches", "arguments": arguments}}
    chunks = ([reasoning.chunk(tool_calls=[call])] if mode == "native_arguments"
              else [reasoning.chunk(content=content)])
    payloads, _answers = reasoning.install_stream(monkeypatch, base, client, chunks)
    response = client.chat(**contracts.request(tools=TOOLS))
    assert len(payloads) == 1 and not stops
    if mode == "native_arguments":
        assert response["message"]["tool_calls"][0]["function"]["arguments"] == arguments
    else:
        assert response["message"]["content"] == content


def test_named_edit_can_use_one_independent_snapshot_checked_proposal(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "shared.txt"
    target.write_text("workspace=A\nstate=original\n")
    prompt = "In shared.txt replace state=original with state=revised-a. Keep the workspace line unchanged."
    broker = receipts.begin(runner, prompt)
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _prompt: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _prompt: "")
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        assert 'state=original' in str(kwargs["messages"])
        return {"patches": [{"path": "shared.txt", "replacements": [
            {"old_text": "state=original", "new_text": "state=revised-a"},
        ]}]}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))

    def handler(name, arguments):
        if name == "read_file_snapshot":
            return {"status": "text_snapshot", "content": target.read_text(),
                    "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "start_line": 1, "end_line": 2}
        assert name == "apply_text_patch"
        allowed, error = broker.authorize(name, arguments)
        assert allowed, error
        assert arguments["expected_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        target.write_text(target.read_text().replace("state=original", "state=revised-a"))
        broker.transaction_changed_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
        return {"status": "completed", "path": str(target),
                "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    calls = receipts.install_host(variant, runner, monkeypatch, handler=handler)
    answer = asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "No native call was emitted.", None))
    assert answer and "SHA-256" in answer
    assert target.read_text() == "workspace=A\nstate=revised-a\n"
    assert len(proposals) == 1
    assert [name for name, _ in calls] == ["read_file_snapshot", "apply_text_patch"]


@pytest.mark.parametrize("mode", ["read_only", "wrong_target", "unavailable", "uncertain_mutation", "concurrent_edit", "exhausted"])
def test_named_edit_recovery_is_scoped_and_does_not_retry(variant, runner, tmp_path, monkeypatch, mode):
    target = tmp_path / "shared.txt"
    target.write_text("workspace=A\nstate=original\n")
    other = tmp_path / "other.txt"
    other.write_text("state=original\n")
    prompt = "In shared.txt replace state=original with state=revised-a. Keep the workspace line unchanged."
    broker = receipts.begin(runner, prompt)
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _prompt: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _prompt: "")
    if mode == "read_only":
        broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
        runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    if mode == "uncertain_mutation":
        broker.evidence.append({"tool": "apply_text_patch", "success": False, "wrapper_rejected": False})
    if mode == "exhausted":
        broker.no_progress_recovery_exhausted = True
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        if mode == "unavailable":
            raise RuntimeError("fixture proposal unavailable")
        if mode == "concurrent_edit":
            target.write_text("workspace=A\nstate=external\n")
        return {"patches": [{"path": "shared.txt" if mode == "concurrent_edit" else "other.txt", "replacements": [
            {"old_text": "state=external" if mode == "concurrent_edit" else "state=original", "new_text": "state=revised-a"},
        ]}]}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch)
    for _ in range(2):
        assert asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None)) is None
    assert len(proposals) == (0 if mode in {"read_only", "uncertain_mutation", "exhausted"} else 1)
    assert [name for name, _ in calls] == (["read_file_snapshot"] if mode == "concurrent_edit" else [])
    assert target.read_text() == ("workspace=A\nstate=external\n" if mode == "concurrent_edit" else "workspace=A\nstate=original\n")
    assert other.read_text() == "state=original\n"


@pytest.mark.parametrize("outcome", ["empty", "native", "length"])
def test_named_native_attempt_returns_to_orchestrator_before_empty_step_exhaustion(
    runtime, variant, runner, tmp_path, outcome,
):
    target = tmp_path / "shared.txt"
    target.write_text("workspace=A\nstate=original\n")
    prompt = "In shared.txt replace state=original with state=revised-a. Keep the workspace line unchanged."
    broker = receipts.begin(runner, prompt)
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "status": "text_snapshot", "content": target.read_text(),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "start_line": 1, "end_line": 2,
    }))
    _base, client, owner = file_recovery.wire(runtime, variant, broker)
    owner._wrapper_no_progress_tool_choice = lambda *_args: {
        "type": "function", "function": {"name": "apply_text_patch"},
    }
    seen = []
    call = {"id": "actual-native", "type": "function", "function": {
        "name": "apply_text_patch", "arguments": '{"path":"shared.txt"}',
    }}

    def reply(*args):
        seen.append(args[-1])
        value = file_recovery.raw(calls=[call] if outcome == "native" else [],
                                  finish="length" if outcome == "length" else "stop")
        value["choices"][0]["message"]["reasoning_content"] = '<tool_call>{"name":"untrusted"}</tool_call>'
        return value

    client._call_with_fallback = reply
    response = client.chat(**contracts.request(tools=[{"type": "function", "function": {"name": "apply_text_patch"}}]))
    assert len(seen) == (1 if outcome == "native" else 2)
    if outcome == "empty":
        assert "No edit was verified" in response["message"]["content"]
        assert not broker.no_progress_recovery_exhausted
    else:
        assert not response["message"]["content"]
    assert response["message"]["tool_calls"] == ([call] if outcome == "native" else [])
    assert target.read_text() == "workspace=A\nstate=original\n"
