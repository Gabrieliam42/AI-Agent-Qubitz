"""Observed matrix defects reproduced through CPU-only production boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import tkinter as tk
from types import SimpleNamespace

import pytest

from evaluation import test_followup_matrix as evaluator
from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner
begin = receipts.begin
result = receipts.result


def install_schema(broker, name="flaky_read", *, read_only=True):
    args = {"server_reference": "fixture_mcp.py", "tool_name": name, "arguments": {"value": "first"}}
    broker.record_result("list_project_mcp_tools", {"server_reference": "fixture_mcp.py"}, result({
        "count": 1, "tools": [{"name": name, "inputSchema": {"type": "object"},
                              "annotations": {"readOnlyHint": read_only}}],
    }))
    broker.mcp_tool_traits[broker._mcp_invocation_key(args, include_arguments=False)] = {"readOnlyHint": read_only}
    return args


@pytest.mark.parametrize("read_only,outcome", [(True, "failed"), (False, "uncertain")])
def test_failed_dispatch_is_attempted_not_success_and_terminal(variant, runner, read_only, outcome):
    prompt = "Call flaky_read with value='first'. If it fails, report that failure; do not replay."
    broker = begin(runner, prompt)
    args = install_schema(broker, read_only=read_only)
    assert broker.reserve_mcp_invocation(args) is None
    assert next(iter(broker.mcp_invocations.values()))["outcome"] == "attempted"
    broker.record_result("call_project_mcp_tool", args, result({
        "result": {"isError": True, "content": [{"type": "text", "text": "Injected transient failure"}]},
    }))
    assert broker.evidence[-1]["mcp_dispatched"]
    assert broker.evidence[-1]["mcp_outcome"] == outcome
    assert broker.last_mcp_result == {"success": False}
    assert "mcp_tool_attempted" in broker.completion_report(prompt)[0]
    assert "mcp_tool_called" not in broker.completion_report(prompt)[0]
    report = variant._terminal_mcp_outcome_result(broker, prompt)
    assert "Injected transient failure" in report and "success is not claimed" in report
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"
    broker.record_result("call_project_mcp_tool", args, result({"status": "mcp_replay_blocked"}, error=True))
    assert not broker.evidence[-1]["mcp_dispatched"]
    assert variant._terminal_mcp_outcome_result(broker, prompt) == report


@pytest.mark.parametrize("prompt", [
    "Call flaky_read with value='first'; retry a failure once and report the failure.",
    "Call flaky_read with value='first'. If it fails, report the failure, then edit settings.json.",
    "Call flaky_read with value='first'. Do not report the failure.",
])
def test_terminal_reporting_does_not_erase_other_obligations(variant, runner, prompt):
    broker = begin(runner, prompt)
    args = install_schema(broker)
    broker.reserve_mcp_invocation(args)
    broker.record_result("call_project_mcp_tool", args, result({"result": {"isError": True}}))
    assert variant._terminal_mcp_outcome_result(broker, prompt) is None


def test_blocked_or_unreserved_call_is_not_a_dispatch_receipt(variant, runner):
    prompt = "Call flaky_read. If it fails, report the failure."
    broker = begin(runner, prompt)
    args = install_schema(broker)
    broker.record_result("call_project_mcp_tool", args, result({"status": "invalid_arguments"}, error=True))
    assert not broker.evidence[-1]["mcp_dispatched"]
    assert variant._terminal_mcp_outcome_result(broker, prompt) is None


def test_pending_progress_excludes_unrelated_reads(variant, runner):
    broker = begin(runner, "Call flaky_read with value='first'.")
    install_schema(broker)
    before = broker.progress_token(pending_only=True)
    broker.transaction_snapshot_hashes[str(runner.workspace / "audit.json")] = "new-read"
    broker.evidence.append({"turn_id": broker.turn_id, "success": True, "tool": "read_file_snapshot",
                            "categories": [], "paths": ["audit.json"]})
    assert broker.progress_token(pending_only=True) == before
    broker.record_result("call_project_mcp_tool", {"tool_name": "flaky_read", "arguments": {"value": "first"}},
                         result({"result": {"value": "first"}}))
    assert broker.progress_token(pending_only=True) != before


def test_mismatch_guidance_never_instructs_blind_replay(variant, runner):
    broker = begin(runner, "Call commit_then_error exactly once. If uncertain, report uncertainty; do not replay.")
    guard = variant._mcp_dispatch_blocker(broker, {"tool_name": "list_records"}, ["list_records", "commit_then_error"])
    assert guard["status"] == "mcp_tool_mismatch"
    assert "Do not" in guard["instruction"] and "already attempted" in guard["instruction"]
    assert "explicitly authorized" in guard["instruction"]
    broker.current_prompt = "Check through list_records without repeating commit_then_error."
    assert variant._mcp_dispatch_blocker(broker, {"tool_name": "list_records"}, ["list_records", "commit_then_error"]) is None


def test_failure_preflight_finishes_without_model_loop(variant, runner, monkeypatch):
    prompt = "Call flaky_read with value='first'. If it fails, report that failure; do not replay."

    async def preflight(*_args):
        broker = runner._tool_permission_broker
        args = install_schema(broker)
        broker.reserve_mcp_invocation(args)
        broker.record_result("call_project_mcp_tool", args, result({
            "result": {"isError": True, "content": [{"type": "text", "text": "Injected transient failure"}]},
        }))
        return "The nested tool failed."

    monkeypatch.setattr(runner, "_wrapper_mcp_lifecycle_recovery", preflight)
    answer = asyncio.run(runner._run_async(prompt))
    assert "Injected transient failure" in answer
    assert "[Completion status: partial]" in answer
    assert len(runner._tool_permission_broker.mcp_invocations) == 1


@pytest.mark.parametrize("case_name,tool,outcome", [
    ("mcp_transient_failure_recovery", "flaky_read", "failed"),
    ("mcp_unknown_mutation_no_replay", "commit_then_error", "uncertain"),
])
def test_real_stdio_failure_is_terminal_without_model(variant, runner, monkeypatch, case_name, tool, outcome):
    monkeypatch.setattr(variant._APP.base.BGECodeEmbedder, "_load_model",
                        lambda *_args: pytest.fail("CPU terminal-report test attempted to load embeddings"))
    case = next(case for case in evaluator.scenarios() if case.name == case_name)
    workspace = evaluator.prepare_scenario(case, runner.workspace / "fixture")["a"]
    fixture = workspace / "fixture_mcp.py"
    target = runner.workspace / fixture.name
    target.write_bytes(fixture.read_bytes())
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    answer = asyncio.run(runner._run_async(case.turns[0].prompt))
    audit = [json.loads(line) for line in (runner.workspace / "mcp_audit.jsonl").read_text().splitlines()]
    assert len(audit) == 1 and audit[0]["tool"] == tool
    assert f"{tool}: {outcome} after dispatch" in answer
    assert "Captured error (data):" in answer and "Operation success is not claimed" in answer
    assert "[Completion status: partial]" in answer
    assert hashlib.sha256(target.read_bytes()).hexdigest() == original_hash
    assert len(runner._tool_permission_broker.mcp_invocations) == 1
    if outcome == "uncertain":
        assert len(json.loads((runner.workspace / "records.json").read_text())) == 1


def test_uncertainty_report_has_clause_scoped_negation(variant, runner):
    case = next(case for case in evaluator.scenarios() if case.name == "mcp_unknown_mutation_no_replay")
    prompt = case.turns[0].prompt
    broker = begin(runner, prompt)
    args = install_schema(broker, "commit_then_error", read_only=False)
    args["arguments"] = {"label": "one", "value": 4}
    broker.reserve_mcp_invocation(args)
    broker.record_result("call_project_mcp_tool", args, result({
        "result": {"isError": True, "content": [{"type": "text", "text": "Injected error after commit"}]},
    }))
    assert "uncertain after dispatch" in variant._terminal_mcp_outcome_result(broker, prompt)
    assert variant._terminal_mcp_outcome_result(
        broker, "Call commit_then_error with label='one', value=4. Do not replay or report uncertainty.",
    ) is None


def test_printed_tool_call_has_one_answer_only_repair(variant, runner, monkeypatch):
    calls = []
    parent = next(cls for cls in type(runner).__mro__
                  if cls.__name__ == "HardenedAgentRunner").__mro__[1]

    async def model_pass(self, prompt, callback=None):
        calls.append((prompt, self._decide_route(prompt).selected_route))
        return "3" if "Answer-only correction:" in prompt else "<tool_call><function=read_file>{}</function></tool_call>"

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async('Do not call any tools. The supplied settings excerpt is {"limit":3}. Tell me its limit.'))
    assert answer == "3"
    assert len(calls) == 2 and calls[1][1] == "simple_answer"
    assert not runner._tool_permission_broker.evidence


def test_literal_tell_query_uses_fresh_snapshot(variant, runner):
    target = runner.workspace / "settings.json"
    target.write_text('{"limit":3,"mode":"safe"}')
    broker = begin(runner, "Inspect settings.json without modifying or executing anything. Tell me its limit.")
    broker.transaction_snapshot_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert variant._verified_file_read_answer(broker, broker.current_prompt, []) == "3"
    target.write_text('{"limit":4}')
    assert variant._verified_file_read_answer(broker, broker.current_prompt, []) is None


def test_source_scalar_is_captured_and_workspace_bound(variant, runner):
    broker = begin(runner, "What version did the source you fetched report? Answer only the version. Do not fetch again or modify anything.")
    body = json.dumps({"info": {"version": "25.0"}})
    receipt = {"body": body, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "source_turn_id": "prior",
               "workspace": str(runner.workspace.resolve()), "truncated": False}
    broker.previous_source_receipts = {"https://example.test/data": receipt}
    relation = SimpleNamespace(depends_on_prior=True)
    assert variant._verified_source_receipt_answer(broker, broker.current_prompt, relation) == "25.0"
    receipt["body_sha256"] = "invalid"
    assert variant._verified_source_receipt_answer(broker, broker.current_prompt, relation) is None


def test_evaluator_rejects_generic_failure_report(tmp_path):
    turn = evaluator.Turn("Report the failure", (evaluator.Check("failure_report", expected={
        "tool": "flaky_read", "patterns": [r"flaky_read", r"transient"]}),))
    observed = {"tools": [{"name": "call_project_mcp_tool", "arguments": {"tool_name": "flaky_read"},
                           "result": {"structured_content": {"result": {"isError": True}}}}],
                "assistant": "The requested action was not completed and verified."}
    state = evaluator.workspace_manifest(tmp_path)
    assert evaluator.verify_turn(tmp_path, turn, observed, state, state)[0]
    observed["assistant"] = "flaky_read: captured transient failure, no success claimed."
    assert not evaluator.verify_turn(tmp_path, turn, observed, state, state)[0]
    observed["assistant"] += " The tool succeeded."
    assert evaluator.verify_turn(tmp_path, turn, observed, state, state)[0]
    observed["assistant"] = "flaky_read: captured transient failure, no success claimed."
    observed["tools"][0]["result"] = {"is_error": False}
    assert evaluator.verify_turn(tmp_path, turn, observed, state, state)[0]


def test_quality_captures_tool_argument_degeneration_without_censoring_code(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [
        {"kind": "http_request", "request_id": "a", "payload": {}},
        {"kind": "http_stream_line", "request_id": "a", "line": "data: " + json.dumps({"choices": [{
            "delta": {"tool_calls": [{"index": 0, "function": {"name": "apply_text_patch", "arguments": "\\n" * 6000}}]},
            "finish_reason": "length"}]})},
    ]
    path.write_text("\n".join(json.dumps({"turn": 1, "time": i, **row}) for i, row in enumerate(rows)))
    quality = evaluator.matrix.generation_quality(path, 1)
    assert "generation_length_termination" in quality["problems"]
    assert quality["repetition_candidates"][0]["channel"] == "tool_arguments:0"
    rows[1]["line"] = "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [{"index": 0,
        "function": {"name": "write_file", "arguments": json.dumps({"content": "line\n" * 3000})}}]}, "finish_reason": "tool_calls"}]})
    path.write_text("\n".join(json.dumps({"turn": 1, "time": i, **row}) for i, row in enumerate(rows)))
    assert evaluator.matrix.generation_quality(path, 1)["status"] == "clean"


def test_workflow_reviews_recovered_errors_but_not_expected_fixture_errors():
    turn = evaluator.Turn("Report failure", (evaluator.Check("failure_report", expected={"tool": "flaky_read"}),))
    row = {"name": "call_project_mcp_tool", "arguments": {"tool_name": "flaky_read"},
           "result": {"structured_content": {"result": {"isError": True}}}}
    observed = {"tools": [row]}
    assert not evaluator.matrix.workflow_quality(observed, turn)["review_reasons"]
    observed["tools"].append({"name": "apply_text_patch", "arguments": {},
                              "result": {"is_error": True, "structured_content": {"status": "patch_failed"}}})
    assert evaluator.matrix.workflow_quality(observed, turn)["tool_errors"] == [
        {"tool": "apply_text_patch", "status": "patch_failed"}]
    observed["tools"] = [{"name": "call_project_mcp_tool", "arguments": {"tool_name": "flaky_read"},
                          "result": {"is_error": True, "structured_content": {"status": "mcp_replay_blocked"}}}]
    assert evaluator.matrix.workflow_quality(observed, turn)["tool_errors"] == [
        {"tool": "call_project_mcp_tool", "status": "mcp_replay_blocked"}]


def test_palette_keeps_answers_white_and_reasoning_gray(variant):
    cls = variant._APP.base.QubitzGUI
    method = next(parent.__dict__["_ensure_transcript_tags"] for parent in cls.__mro__
                  if "_ensure_transcript_tags" in parent.__dict__)
    constants = method.__code__.co_consts
    assert "#ff8300" in constants and "#ffffff" in constants
    root = tk.Tk()
    root.withdraw()
    try:
        gui = object.__new__(cls)
        gui.root = root
        gui.tk = tk
        gui.transcript = tk.Text(root)
        gui.prompt_box = tk.Text(root)
        gui._transcript_tags_ready = False
        gui._ensure_transcript_tags()
        assert gui.transcript.tag_cget("role_user", "foreground") == "#ff8300"
        assert gui.transcript.tag_cget("role_assistant", "foreground") == "#ffffff"
        assert gui.transcript.tag_cget("role_link", "foreground") == "#7cc9ff"
    finally:
        root.destroy()
