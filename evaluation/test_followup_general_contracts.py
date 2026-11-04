"""General follow-up contracts, with model loading forbidden by the fixtures."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import queue
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import gui_diagnostics, read_variant_source

begin = receipts.begin
runner = receipts.runner
variant = receipts.variant


@pytest.mark.parametrize("prompt,action", [
    ("New task after cancellation: create fresh.txt containing done. Do not create protected.txt.", "create"),
    ("After it returns, create receipt.txt with the returned value.", "create"),
    ("Insert a paragraph from origin.md into guide.md. Do not modify origin.md.", "edit"),
    ("Create output.txt.\nDo not create other.txt.", "create"),
])
def test_context_and_scoped_prohibitions_do_not_remove_actions(variant, prompt, action):
    semantic = variant._analyze_semantic_intent(prompt)
    assert semantic.speech_act == "request"
    assert semantic.requests(action)
    assert action not in semantic.prohibited_actions


def test_long_multiple_alias_expansions_remain_edit_requests(variant):
    paths = ["/tmp/" + "nested/" * 30 + name for name in ("alpha.txt", "beta.txt")]
    semantic = variant._analyze_semantic_intent(f"In `{paths[0]}` and `{paths[1]}` replace old with new.")
    assert semantic.requests("edit")


@pytest.mark.parametrize("prompt", [
    "Explain how to create a file after it returns.",
    "What happens when the application creates fresh.txt?",
    "Hypothetically, in the workspace create fresh.txt.",
])
def test_explanations_and_hypotheticals_do_not_authorize_mutation(variant, prompt):
    assert not variant._analyze_semantic_intent(prompt).requests("create", "edit")


def test_transfer_binds_destination_not_source(variant):
    targets = variant._explicit_edit_target_tokens(
        variant._APP.base,
        "Insert only the paragraph under ## Details from origin.md into guide.md. Do not modify origin.md.",
    )
    assert targets == ("guide.md",)


def test_negated_install_does_not_suppress_metadata_fetch(variant):
    url = "https://example.org/package-metadata.json"
    decision = variant._research_decision_for_prompt(f"Fetch package metadata from {url}. Replace the section in note.md. Do not install anything.")
    assert decision.needed and decision.named_urls == (url,)
    assert not variant._research_decision_for_prompt("Install the package https://example.org/library-1.0-py3-none-any.whl").needed


@pytest.mark.parametrize("prompt", [
    "Explain why your last fix worked.",
    "What label was returned by the latest invocation? Answer only the label.",
])
def test_explicit_prior_action_references_are_followups(variant, prompt):
    history = [{"role": "user", "content": "Repair a file and verify it."},
               {"role": "assistant", "content": "The test passed."}]
    assert variant._classify_turn_relation(prompt, history).depends_on_prior


def test_exact_scalar_receipt_without_tool_word(runner):
    previous = {"workspace": str(runner.workspace.resolve()),
                "last_mcp_result": {"success": True, "value": {"label": "verified-label", "value": 17}}}
    prompt = "What label was returned by the latest invocation? Answer only the label; do not invoke a tool again."
    assert runner._verified_mcp_result_answer(prompt, previous) == "verified-label"
    assert runner._verified_mcp_result_answer(prompt, {**previous, "workspace": "/unrelated"}) is None
    assert runner._verified_mcp_result_answer(prompt, {**previous, "last_mcp_result": {"success": False}}) is None
    assert runner._verified_mcp_result_answer(prompt + " Also edit note.txt.", previous) is None


def test_missing_creation_recovery_keeps_write_tools(variant, runner):
    prompt = "Call the MCP echo tool, then create output.txt with its result."
    broker = begin(runner, prompt)
    broker.transaction_recovery_required = True
    definitions = [{"name": name} for name in (
        "read_file_snapshot", "apply_text_patch", "write_file", "write_files", "list_project_mcp_tools", "call_project_mcp_tool",
    )]
    selected = runner._filter_tools_by_intent(prompt, definitions)
    assert "write_file" in {runner._tool_name(tool) for tool in selected}
    assert "do not request snapshots" in broker.transaction_recovery_context().lower()


def test_parameter_steering_preserves_creation_scope(variant, runner):
    prompt = "Call the MCP echo tool with value=7, then create output.txt with the result."
    broker = begin(runner, prompt)
    runner._active_callback = lambda *_args: None
    runner._qubitz_applied_side_notes = []
    runner._route_decision = runner._decide_route(prompt)
    runner._qubitz_live_tool_definitions = []
    runner._unfiltered_tool_definitions = [{"name": name} for name in (
        "write_file", "write_files", "read_file_snapshot", "apply_text_patch", "list_project_mcp_tools", "call_project_mcp_tool",
    )]
    assert runner.submit_side_note("Use value=9 for the pending call.")
    assert runner._apply_side_notes([], None)
    assert {"created_outputs", "mcp_tool_called"} <= broker.required_postconditions
    assert broker.task_intent.semantic.requests("create")
    assert "write_file" in {runner._tool_name(tool) for tool in runner._qubitz_live_tool_definitions}


def test_output_json_is_not_an_mcp_configuration(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "selected.json").write_text('{"value":1}\n')
    (tmp_path / "protocol.py").write_text("from mcp.server.fastmcp import FastMCP\nserver = FastMCP('local')\nserver.run(transport='stdio')\n")
    prompt = "Call that same typed_echo with value=11, then update selected.json from the result."
    broker = begin(runner, prompt)
    schema = {"type": "object", "properties": {"value": {"type": "integer"}}, "required": ["value"]}
    broker.follow_up_mcp_bindings = {"protocol.py": {"schemas": {"typed_echo": schema}}}
    broker.mcp_tool_schemas["typed_echo"] = schema
    calls = receipts.install_host(variant, runner, monkeypatch, tools=({"name": "typed_echo", "inputSchema": schema},))
    asyncio.run(runner._wrapper_mcp_lifecycle_recovery(prompt, "", None))
    assert calls
    assert all(Path(args.get("server_reference", "protocol.py")).name == "protocol.py" for _, args in calls)


def test_exact_reads_skip_embeddings_only_when_complete_and_sufficient(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text('{"value":1}\n')
    prompt = "In settings.json replace value 1 with value 2."
    begin(runner, prompt)
    receipts.install_host(variant, runner, monkeypatch)
    assert asyncio.run(runner._prefetch_named_source_context(prompt, None))
    assert runner._exact_target_context_ready
    broad = "Review settings.json and its dependencies across the repository."
    begin(runner, broad)
    asyncio.run(runner._prefetch_named_source_context(broad, None))
    assert not runner._exact_target_context_ready


def test_saved_heading_contract_rejects_false_verified_completion(variant, runner, tmp_path):
    prompt = "Create notes.md with exactly two level-two sections named Purpose and Checks in that order."
    broker = begin(runner, prompt)
    target = tmp_path / "notes.md"
    target.write_text("# Purpose\n\nText.\n\n# Checks\n\nText.\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("write_file", {"path": "notes.md", "content": target.read_text()}, receipts.result({"created": True, "new_sha256": digest}))
    assert "saved_file_contract" in broker.completion_report(prompt)[1]
    assert target.exists() and "# Purpose" in target.read_text()
    target.write_text("## Purpose\n\nText.\n\n## Checks\n\nText.\n")
    assert variant._saved_text_contract_issues(broker, prompt, broker.evidence) == []
    target.write_text("# Notes\n\n## Purpose\n\nText.\n\n## Checks\n\nText.\n")
    assert variant._saved_text_contract_issues(broker, prompt, broker.evidence) == []
    bounded = prompt + " Each section must contain one paragraph of 1 to 2 words."
    assert variant._saved_text_contract_issues(broker, bounded, broker.evidence) == []


def test_unspecified_text_contract_does_not_reread_artifacts(variant, runner, tmp_path, monkeypatch):
    prompt = "Create note.txt containing a short explanation."
    broker = begin(runner, prompt)
    (tmp_path / "note.txt").write_text("Explanation.\n")
    monkeypatch.setattr(Path, "read_bytes", lambda *_args: pytest.fail("Unspecified contract reread the file"))
    assert not variant._saved_text_contract_issues(broker, prompt, [])


def test_fragment_verification_reads_actual_source_and_preserved_bytes(variant, runner, tmp_path):
    source = tmp_path / "origin.md"
    target = tmp_path / "guide.md"
    source.write_text("## Details\n\nActual verified paragraph.\n")
    original = b"prefix\r\n<!-- region:open -->\r\n<!-- region:close -->\r\nfooter\r\n"
    target.write_bytes(original)
    prompt = "Insert only the paragraph under ## Details from origin.md into guide.md between <!-- region:open --> and <!-- region:close -->. Copy it verbatim, followed by one newline. Preserve both markers and every byte outside them."
    broker = begin(runner, prompt)
    broker.transaction_originals[str(target)] = {"data": original}
    broker.transaction_snapshot_hashes[str(source)] = hashlib.sha256(source.read_bytes()).hexdigest()
    target.write_bytes(original.replace(b"<!-- region:close -->", b"Actual verified paragraph.\r\n<!-- region:close -->"))
    assert variant._saved_text_contract_issues(broker, prompt, []) == []
    target.write_bytes(target.read_bytes().replace(b"Actual", b"Invented"))
    assert any("differs" in issue for issue in variant._saved_text_contract_issues(broker, prompt, []))
    target.write_bytes(target.read_bytes().replace(b"footer", b"altered"))
    assert any("outside" in issue for issue in variant._saved_text_contract_issues(broker, prompt, []))


def test_saved_fields_must_equal_actual_mcp_receipt(variant, runner, tmp_path):
    prompt = "Create selected.json with exactly the returned value and label fields. Use the actual structured result."
    broker = begin(runner, prompt)
    broker.last_mcp_result = {"success": True, "value": {"value": 17, "label": "observed", "extra": 2}}
    target = tmp_path / "selected.json"
    target.write_text('{"value":17,"label":"invented"}\n')
    assert variant._saved_text_contract_issues(broker, prompt, [])
    target.write_text('{"value":17,"label":"observed"}\n')
    assert variant._saved_text_contract_issues(broker, prompt, []) == []


def test_saved_fields_reuse_prior_receipt_but_not_after_current_failure(variant, runner, tmp_path):
    prompt = "Create selected.json with exactly the returned value and label fields."
    broker = begin(runner, prompt)
    broker.follow_up_mcp_result = {"success": True, "value": {"value": 3, "label": "prior-receipt"}}
    (tmp_path / "selected.json").write_text('{"value":3,"label":"prior-receipt"}\n')
    assert not variant._saved_text_contract_issues(broker, prompt, [])
    broker.last_mcp_result = {"success": False}
    assert variant._saved_text_contract_issues(broker, prompt, [])
    broker.last_mcp_result = None
    broker.required_postconditions.add("mcp_tool_called")
    assert variant._saved_text_contract_issues(broker, prompt, [])
    broker.begin_turn("Create another.json with exactly the returned value and label fields.", None)
    assert broker.follow_up_mcp_result is None


def test_live_gui_log_barrier_flushes_without_stopping_writer(variant, tmp_path):
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    gui = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "HardenedGUI")
    initializer = next(node for node in gui.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    nested = [node for node in initializer.body if isinstance(node, ast.FunctionDef) and node.name in {"write_gui_log", "flush_gui_log"}]
    owner = SimpleNamespace(_qubitz_gui_log_path=tmp_path / "gui.jsonl", _qubitz_gui_log_queue=queue.Queue(),
                            _qubitz_gui_log_lock=threading.Lock(),
                            _qubitz_gui_log_error="", _qubitz_gui_log_flushed_bytes=None,
                            _qubitz_gui_log_closed=threading.Event(), _qubitz_gui_watchdog_stop=threading.Event())
    scope = {"self": owner, "base": SimpleNamespace(queue=queue), "json": json, "threading": threading, "time": time}
    scope.update({name: value for name, value in vars(gui_diagnostics).items()
                  if name.startswith("_qubitz_gui_log_")})
    exec(compile(ast.Module(body=nested, type_ignores=[]), "<gui-log-functions>", "exec"), scope)
    writer = threading.Thread(target=scope["write_gui_log"], daemon=True)
    owner._qubitz_gui_log_writer = writer
    writer.start()
    try:
        owner._qubitz_gui_log_queue.put({"event": "gui_event", "kind": "done"})
        assert scope["flush_gui_log"]()
        assert writer.is_alive()
        assert json.loads(owner._qubitz_gui_log_path.read_text())["kind"] == "done"
        assert owner._qubitz_gui_log_flushed_bytes == owner._qubitz_gui_log_path.stat().st_size
    finally:
        owner._qubitz_gui_log_queue.put(None)
        writer.join(timeout=2)
    assert scope["flush_gui_log"]()


def test_gui_collector_uses_acknowledged_bytes_and_keeps_stalls(tmp_path):
    from tmp_smoke_variant import _collect_gui_diagnostics

    log = tmp_path / "gui.jsonl"
    captured = json.dumps({"event": "gui_heartbeat_stalled", "timestamp": "1"}) + "\n"
    log.write_bytes((captured + '{"event":').encode())
    gui = SimpleNamespace(_qubitz_gui_log_path=log, _qubitz_gui_log_flushed_bytes=len(captured.encode()),
                          _qubitz_flush_gui_log=lambda **_kwargs: True)
    result = _collect_gui_diagnostics(gui)
    assert result["behavior_issues"] == ["gui_heartbeat_stalled"]
    assert not result["read_errors"]
    gui._qubitz_flush_gui_log = lambda **_kwargs: False
    assert "gui_log_flush_incomplete" in _collect_gui_diagnostics(gui)["behavior_issues"]


def test_steering_oracle_requires_applied_update(tmp_path):
    from evaluation.followup_matrix_cases import Check, Turn, verify_turn

    turn = Turn("Create note.txt.", (Check("steered"),), steering="Also preserve the header.")
    observed = {"intervention": {"accepted": True}, "runtime": {"applied_steering": []}}
    assert verify_turn(tmp_path, turn, observed, {}, {})[0]
    observed["runtime"]["applied_steering"] = [turn.steering]
    assert not verify_turn(tmp_path, turn, observed, {}, {})[0]


@pytest.mark.parametrize("role,accepted", [("steer", True), ("btw", True), ("status", False)])
def test_gui_steering_acceptance_is_not_just_a_status_message(tmp_path, role, accepted):
    from evaluation.disposable_followup_matrix import intervene

    (tmp_path / "waiting.json").write_text("{}")
    box = SimpleNamespace(delete=lambda *_args: None, insert=lambda *_args: None)
    gui = SimpleNamespace(prompt_box=box, _smoke_events=[])
    turn = SimpleNamespace(cancel=False, steering="Preserve the current file target.")
    gui.send_prompt = lambda: gui._smoke_events.append((0, role, turn.steering))
    result = {}
    intervene(turn, SimpleNamespace(), gui, None, tmp_path, result)
    assert result["accepted"] is accepted
