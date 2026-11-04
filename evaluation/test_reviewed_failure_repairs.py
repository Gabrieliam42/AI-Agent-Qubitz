"""General regressions from saved failures; no models or historical case replay."""
from __future__ import annotations

import asyncio
import copy
import json
import queue
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as context
from evaluation import test_edit_source_fidelity as fidelity
from evaluation import test_followup_state_receipts as receipts
from runtime.model_evidence import (
    compact_tool_content,
    exact_read_receipt,
    static_output_paths,
)
from runtime.recovery_protocol import importmap_issues, snapshot_line_replacements
from runtime.request_failures import deliver_request_failure, generation_rejection

variant = receipts.variant
runner = receipts.runner
context_runtime = context.runtime


ERROR = {"code": 500, "type": "server_error",
         "message": "The model produced output that does not match the expected peg-native format"}


@pytest.mark.parametrize("field,value", [("code", 503), ("type", "unknown"), ("message", "GPU fault")])
def test_unknown_backend_errors_are_not_declared_isolated(field, value):
    assert generation_rejection({**ERROR, field: value}, {}) is None


@pytest.mark.parametrize("uncertain", [False, True])
def test_expected_request_error_is_partial_and_uncertainty_stays_terminal(uncertain):
    evidence = [{"success": False, "tool": "write_file", "wrapper_rejected": False}] if uncertain else []
    agent = SimpleNamespace(_tool_permission_broker=SimpleNamespace(_current_turn_evidence=lambda: evidence))
    logged = []
    gui = SimpleNamespace(agent=agent, event_queue=queue.Queue(),
                          _qubitz_gui_log_event=lambda *args, **kwargs: logged.append((args, kwargs)))
    error = generation_rejection(ERROR, {"messages": []}, http_status=200, request_id="fixture")
    assert deliver_request_failure(gui, error, 4) is not uncertain
    assert logged[0][1]["receipt"]["retry_permitted"] is False
    assert logged[0][1]["receipt"]["http_status"] == 200
    if uncertain:
        assert gui.event_queue.empty()
    else:
        _, kind, answer = gui.event_queue.get_nowait()
        assert kind == "answer" and "[Completion status: partial]" in answer


def test_current_short_snapshot_survives_actual_request_fitting(context_runtime):
    _, client, _ = context_runtime
    client.ensure_model("fixture")
    snapshot = json.dumps({"is_error": False, "structured_content": {
        "path": "tests/check.py", "sha256": "a" * 64, "size": 263,
        "content": "assert compute(3) == 7\n" * 12}})
    messages = [{"role": "user", "content": "Keep this user instruction."},
                {"role": "assistant", "tool_calls": [
                    {"id": "old", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "current", "function": {"name": "read_file", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "old", "content": "large result " * 5000},
                {"role": "tool", "tool_call_id": "current", "content": snapshot}]
    original = copy.deepcopy(messages)
    client.chat(**context.request(messages=messages))
    sent = client.payloads[-1]["messages"]
    assert sent[-1]["content"] == snapshot and messages == original
    assert json.loads(sent[2]["content"])["omitted"] is True


def test_long_snapshot_is_not_silently_clipped_to_metadata():
    data = json.dumps({"structured_content": {"path": "large.txt", "content": "x" * 20000}})
    assert compact_tool_content(data) == data


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_line_recovery_preserves_separator_and_final_terminator(newline):
    before = newline.join(["## First", "", "old paragraph", "", "## Last", "preserve", ""])
    patch = snapshot_line_replacements(before, [{"start_line": 3, "end_line": 3, "new_text": "new paragraph"}])[0]
    assert patch["new_text"] == before.replace("old paragraph", "new paragraph")
    assert snapshot_line_replacements("old\n", [{"start_line": 1, "end_line": 1, "new_text": "new"}],
                                      preserve_line_endings=False)[0]["new_text"] == "new"


def test_importmap_prefix_diagnostic_does_not_replace_urls():
    document = ('<script type="importmap">{"imports":{"library":"https://example.test/lib/"}}</script>'
                '<script type="module">import {x} from "library/part.js";</script>')
    assert "trailing slashes" in importmap_issues(document)[0]
    assert not importmap_issues(document.replace('"library":', '"library/":'))


def test_report_read_receipt_is_current_exact_and_complete(variant, runner):
    target = runner.workspace / "report.txt"
    target.write_text("result\n")
    prompt = "Verify the report file report.txt. Do not edit it."
    broker = receipts.begin(runner, prompt)
    broker.required_postconditions.add("workspace_file_read")
    broker.record_result("read_file", {"path": target.name}, receipts.result({
        "path": target.name, "start_line": 1, "end_line": 1, "content": "result",
    }))
    assert "workspace_file_read" not in broker.completion_report(prompt)[1]
    target.write_text("changed\n")
    assert "workspace_file_read" in broker.completion_report(prompt)[1]


def test_unrelated_or_false_read_never_satisfies_report(variant, runner):
    target = runner.workspace / "report.txt"
    target.write_text("expected\n")
    broker = receipts.begin(runner, "Verify the report file report.txt.")
    broker.record_result("read_file", {"path": target.name}, receipts.result({
        "path": "other.txt", "start_line": 1, "end_line": 1, "content": "expected",
    }))
    assert "workspace_file_read" in broker.completion_report(broker.current_prompt)[1]
    assert exact_read_receipt(target, {}, {"start_line": 1, "end_line": 1, "content": "invented"}) is None


def test_only_literal_authorized_script_outputs_are_resolved(tmp_path):
    target = tmp_path / "publish.py"
    target.write_text("from pathlib import Path\nPath('report.txt').write_text('ok')\n")
    assert static_output_paths(target, tmp_path) == {str(tmp_path / "report.txt")}
    target.write_text("from pathlib import Path\nPath('../outside').write_text('no')\n")
    assert static_output_paths(target, tmp_path) == set()


def test_report_read_must_follow_exact_current_producer(variant, runner):
    script = runner.workspace / "publish.py"
    script.write_text("from pathlib import Path\nPath('report.txt').write_text('ok')\n")
    report = runner.workspace / "report.txt"
    report.write_text("ok\n")
    prompt = "Run publish.py then read and verify its report."
    broker = receipts.begin(runner, prompt)
    broker.required_postconditions.add("workspace_file_read")

    def read():
        broker.record_result("read_file", {"path": "report.txt"}, receipts.result({
            "path": "report.txt", "start_line": 1, "end_line": 1, "content": "ok"}))

    read()
    broker.record_result("run_existing_entrypoint", {"path": "publish.py"}, receipts.result({
        "status": "completed", "returncode": 0, "command": ["python", "publish.py"]}))
    assert "workspace_file_read" in broker.completion_report(prompt)[1]
    read()
    assert "workspace_file_read" not in broker.completion_report(prompt)[1]
    broker.workspace_generation += 1
    read()
    assert "workspace_file_read" in broker.completion_report(prompt)[1]


def test_paragraph_boundary_whitespace_is_protected(variant, runner):
    target = runner.workspace / "note.md"
    before = "## First\nKeep.\n\n## Transfer\nOld paragraph.\n\n## Last\nKeep.\n"
    target.write_text(before)
    prompt = "Revise only the Transfer paragraph in note.md. Preserve all other sections."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    damaged = before.replace("Old paragraph.\n\n", "New paragraph.\n")
    issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): damaged.encode()})
    assert any("boundary whitespace" in issue for issue in issues)


def test_last_fix_explanation_uses_historical_sources_without_inspection(variant, runner, monkeypatch):
    target, _receipt, _behavior = fidelity.source_edit(variant, runner)
    target.write_text("external replacement is not the historical fix\n")
    seen = []

    async def model_pass(_self, prompt, callback=None):
        seen.append(prompt)
        return "The saved implementation introduces a separate branch; only historical evidence is available."

    monkeypatch.setattr(fidelity.parent_runner(runner), "_run_async", model_pass)
    asyncio.run(runner._run_async("Explain why your last fix worked, without editing or running anything."))
    assert seen and "Explain the recorded prior change now" in seen[0]
    assert "subtotal = sum(prices)" in seen[0] and "external replacement" not in seen[0]
    assert "Inspect only these targets first" not in seen[0]
    assert not runner._tool_permission_broker.evidence
