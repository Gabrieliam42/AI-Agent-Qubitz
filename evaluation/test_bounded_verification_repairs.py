"""CPU-only recovery contracts; no inference server or model is loaded."""
from __future__ import annotations

import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_reasoning_display as reasoning

variant = receipts.variant
runner = receipts.runner
runtime = contracts.runtime
display_runtime = reasoning.display_runtime


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("runner_name", ["", "unittest", "custom"])
def test_requested_test_assertion_failure_is_actionable(variant, tmp_path, runner_name):
    target = tmp_path / "engine.py"
    target.write_text("return_value = 6\n")
    result = {"test_runner": runner_name, "returncode": 1,
              "stderr": "AssertionError: 6 != 5\nFAILED (failures=1)\n"}
    assert variant._actionable_committed_pytest_failure(
        tmp_path, {str(target): digest(target)}, result, explicit_test=True,
    )
    assert not variant._actionable_committed_pytest_failure(tmp_path, {str(target): digest(target)}, result)


@pytest.mark.parametrize("output", ["ImportError: missing runtime", "KeyboardInterrupt",
                                   "CUDA out of memory", "Permission denied", "runner failed without evidence"])
def test_custom_runner_blockers_do_not_authorize_edits(variant, tmp_path, output):
    target = tmp_path / "engine.py"
    target.write_text("value = 6\n")
    assert not variant._actionable_committed_pytest_failure(
        tmp_path, {str(target): digest(target)}, {"returncode": 1, "stderr": output}, explicit_test=True,
    )


def test_correction_scope_and_concurrent_change_guard(variant, runner, tmp_path):
    target = tmp_path / "engine.py"
    target.write_text("value = 6\n")
    (tmp_path / "other.py").write_text("unchanged = True\n")
    broker = receipts.begin(runner, "Fix engine.py then run run_tests.py. Do not edit other.py.")
    broker.transaction_changed_hashes[str(target)] = digest(target)
    broker._test_correction_targets = dict(broker.transaction_changed_hashes)
    broker._test_correction_active = True
    snapshot = receipts.result({"status": "text_snapshot", "content": target.read_text(),
                                "sha256": digest(target), "start_line": 1, "end_line": 1})
    broker.record_result("read_file_snapshot", {"path": "engine.py"}, snapshot)
    arguments = {"path": "engine.py", "replacements": [{"old_text": "6", "new_text": "5"}]}
    allowed, _ = broker.authorize("apply_text_patch", arguments.copy())
    assert allowed
    allowed, _ = broker.authorize("apply_text_patch", {**arguments, "path": "other.py"})
    assert not allowed
    target.write_text("value = 99\n")
    allowed, error = broker.authorize("apply_text_patch", arguments.copy())
    assert not allowed and error["status"] == "snapshot_mismatch"
    assert target.read_text() == "value = 99\n"


def test_custom_test_failure_preserves_committed_artifact(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "engine.py"
    target.write_text("value = 6\n")
    (tmp_path / "run_tests.py").write_text("raise SystemExit(1)\n")
    prompt = "Fix engine.py then run run_tests.py. Do not edit run_tests.py."
    broker = receipts.begin(runner, prompt)
    broker.transaction_changed_hashes[str(target)] = digest(target)
    calls = receipts.install_host(variant, runner, monkeypatch, handler=lambda *_args: {
        "returncode": 1, "stderr": "AssertionError: 6 != 5\nFAILED (failures=1)",
    })
    passed, _ = asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
    assert passed is False
    assert target.read_text() == "value = 6\n"
    assert broker._test_correction_targets == {str(target): digest(target)}
    assert len(calls) == 1


@pytest.mark.parametrize("passes_after_correction", [True, False])
def test_failed_edit_gets_one_scoped_correction_and_same_test_rerun(
    variant, runner, tmp_path, monkeypatch, passes_after_correction,
):
    target = tmp_path / "engine.py"
    target.write_text("value = 6\n")
    test_runner = tmp_path / "run_tests.py"
    test_runner.write_text("assert value == 5\n")
    prompt = "Fix engine.py then run run_tests.py. Do not edit run_tests.py."
    broker = receipts.begin(runner, prompt)
    broker.transaction_changed_hashes[str(target)] = digest(target)
    proposals = []

    def propose(**arguments):
        proposals.append(arguments)
        assert "value = 6" in str(arguments["messages"])
        return {"patches": [{"path": "engine.py", "replacements": [
            {"old_text": "value = 6", "new_text": "value = 5"},
        ]}]}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _prompt: "engine.py: value = 6\n")
    tests = []

    def handler(name, arguments):
        if name == "read_file_snapshot":
            return {"status": "text_snapshot", "content": target.read_text(), "sha256": digest(target),
                    "start_line": 1, "end_line": 1}
        if name == "apply_text_patch":
            allowed, error = broker.authorize(name, arguments)
            assert allowed, error
            assert arguments["expected_sha256"] == digest(target)
            for replacement in arguments["replacements"]:
                target.write_text(target.read_text().replace(replacement["old_text"], replacement["new_text"], 1))
            broker.transaction_changed_hashes[str(target)] = digest(target)
            return {"status": "completed", "path": str(target), "new_sha256": digest(target)}
        assert name == "run_existing_entrypoint"
        tests.append(dict(arguments))
        passed = len(tests) > 1 and passes_after_correction
        return {"returncode": 0 if passed else 1, "status": "completed",
                "command": ["python", "run_tests.py"],
                "stderr": "" if passed else "AssertionError: 6 != 5\nFAILED (failures=1)"}

    receipts.install_host(variant, runner, monkeypatch, handler=handler)
    passed, _ = asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
    assert passed is False
    answer = asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "failed tests", None))
    assert len(proposals) == 1
    assert len(tests) == 2 and tests[0] == tests[1]
    assert target.read_text() == "value = 5\n"
    assert test_runner.read_text() == "assert value == 5\n"
    assert broker._test_correction_active is False
    if passes_after_correction:
        assert "tests passed" in answer
    else:
        assert "saved edits were preserved" in answer
        assert broker.transaction_verification_failures == 2
        assert asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, answer, None)) is None


@pytest.mark.parametrize("prompt", [
    "Create draft.txt containing exactly ready followed by a newline.",
    "Create draft.txt with exactly three level-two sections named Overview, Transfer, and Closing.",
])
def test_artifact_correction_prefetches_only_authorized_fresh_snapshots(variant, runner, tmp_path, monkeypatch, prompt):
    target = tmp_path / "draft.txt"
    target.write_text("wrong saved content\n")
    other = tmp_path / "source.txt"
    other.write_text("do not retrieve\n")
    broker = receipts.begin(runner, prompt)
    broker.creation_targets.add(str(target))
    calls = receipts.install_host(variant, runner, monkeypatch)
    context = asyncio.run(runner._wrapper_saved_contract_snapshots())
    assert "wrong saved content" in context
    assert "do not retrieve" not in context
    assert [name for name, _arguments in calls] == ["read_file_snapshot"]
    assert broker.transaction_snapshot_hashes[str(target)] == digest(target)
    assert target.read_text() == "wrong saved content\n"


def test_prose_guard_detects_only_strong_repetition(runtime):
    base, _, _ = runtime
    repeated = ("The requested tool failed without changing state, and its captured receipt remains available for reporting. " * 100)
    assert base.LlamaCppClient.qubitz_degenerate_prose(repeated)
    assert not base.LlamaCppClient.qubitz_degenerate_prose("```python\n" + repeated + "\n```")
    assert not base.LlamaCppClient.qubitz_degenerate_prose(json.dumps({"content": repeated}))
    assert base.LlamaCppClient.qubitz_degenerate_prose("A short repeated sentence. " * 200)


@pytest.mark.parametrize("with_tool", [False, True])
def test_stream_guard_does_not_replay_or_trim_tool_arguments(display_runtime, monkeypatch, with_tool):
    base, client, namespace, events = display_runtime
    stops = []
    broker = SimpleNamespace(callback=lambda *event: events.append(event), current_prompt="Report the tool failure.",
                             _emit=lambda _message: None, stop_callback=stops.append)
    namespace["_TOOL_CALL_CONTEXT"].set(broker)
    sentence = "The requested tool failed without changing state, and its captured receipt remains available for reporting. "
    chunks = [reasoning.chunk(content=sentence * 10) for _ in range(20)]
    if with_tool:
        arguments = json.dumps({"text": sentence * 200})
        chunks.insert(0, reasoning.chunk(tool_calls=[{"index": 0, "id": "call-1", "function": {
            "name": "write_file", "arguments": arguments,
        }}]))
    payloads, _ = reasoning.install_stream(monkeypatch, base, client, chunks)
    response = client.chat(**contracts.request(num_predict=16384))
    assert len(payloads) == 1
    if with_tool:
        assert not stops
        assert response["message"]["tool_calls"][0]["function"]["arguments"] == arguments
    else:
        assert stops == ["repetitive assistant prose"]
        assert response["finish_reason"] == "repetition_guard"
        assert len(response["message"]["content"]) < 200


@pytest.mark.parametrize("prefix,suffix", [("```text\n", "\n```"), ('{"document":"', '"}')])
def test_stream_guard_remembers_artifact_prefix_beyond_rolling_window(
    display_runtime, monkeypatch, prefix, suffix,
):
    base, client, namespace, events = display_runtime
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        callback=lambda *event: events.append(event), current_prompt="Show the document.",
        _emit=lambda _message: None, stop_callback=stops.append,
    ))
    sentence = "The requested tool failed without changing state, and its captured receipt remains available for reporting. "
    content = [prefix, "preface " * 2000, *(sentence * 10 for _ in range(20)), suffix]
    payloads, _ = reasoning.install_stream(monkeypatch, base, client, [reasoning.chunk(content=text) for text in content])
    response = client.chat(**contracts.request(num_predict=16384))
    assert not stops
    assert response["message"]["content"] == "".join(content)
    assert len(payloads) == 1


def test_stream_guard_resumes_after_closed_code_fence(display_runtime, monkeypatch):
    base, client, namespace, events = display_runtime
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        callback=lambda *event: events.append(event),
        current_prompt="Create calculator.py with an add function.",
        _emit=lambda _message: None, stop_callback=stops.append,
    ))
    sentence = "The task is complete. The file calculator.py has been created. "
    chunks = [
        reasoning.chunk(content="The function is defined as:\n``"),
        reasoning.chunk(content="`python\ndef add(a, b):\n    return a + b\n``"),
        reasoning.chunk(content="`\n"),
        *(reasoning.chunk(content=sentence * 4) for _ in range(8)),
    ]
    payloads, _ = reasoning.install_stream(monkeypatch, base, client, chunks)
    response = client.chat(**contracts.request(num_predict=16384))
    assert len(payloads) == 1
    assert stops == ["repetitive assistant prose"]
    assert response["finish_reason"] == "repetition_guard"
    assert response["message"]["content"].count("The task is complete.") <= 1


def test_stream_guard_preserves_explicit_repetition_after_code_fence(display_runtime, monkeypatch):
    base, client, namespace, events = display_runtime
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        callback=lambda *event: events.append(event),
        current_prompt="Repeat the sentence The task is complete. exactly 20 times.",
        _emit=lambda _message: None, stop_callback=stops.append,
    ))
    content = "```python\nvalue = 1\n```\n" + "The task is complete. " * 20
    payloads, _ = reasoning.install_stream(monkeypatch, base, client, [reasoning.chunk(content=content)])
    response = client.chat(**contracts.request(num_predict=16384))
    assert len(payloads) == 1
    assert not stops
    assert response["message"]["content"] == content


def test_reasoning_status_distinguishes_unemitted_and_unforwarded(display_runtime):
    _, client, _, events = display_runtime
    client.config.show_reasoning = False
    client._qubitz_reasoning_capabilities = {"reasoning_format": "none", "chat_format": "Content-only"}
    client._call_with_fallback = lambda *_args: {"choices": [{"message": {
        "content": "Answer", "reasoning_content": "Runtime text",
    }, "finish_reason": "stop"}]}
    client.chat(**contracts.request())
    status = json.loads(events[-1][1])
    assert status["received"] == len("Runtime text") and status["forwarded"] == 0
    gui, _ = reasoning.gui_subject()
    gui._update_reasoning_status(json.dumps({"received": 0, "forwarded": 0, "reasoning_format": "none"}))
    assert "does not prove reasoning is disabled" in gui._reasoning_status_message
