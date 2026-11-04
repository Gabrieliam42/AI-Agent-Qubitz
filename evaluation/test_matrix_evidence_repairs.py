"""General contracts reproduced from matrix evidence without loading a model."""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

from evaluation import test_explicit_creation_contracts as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from runtime import read_variant_source

variant = receipts.variant
runner = receipts.runner
begin = receipts.begin


def snapshot(broker, target):
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "status": "text_snapshot", "sha256": digest,
        "start_line": 1, "end_line": len(target.read_bytes().splitlines()),
    }))
    return digest


def host_for(variant, broker):
    server = variant._build_local_mcp_server(
        variant._APP.base, broker.workspace, broker.workspace, creation.REFERENCE,
    )
    host = boundaries.host_for(variant, broker, server)
    dispatch = host.call_tool

    async def call_tool(name, arguments):
        # The extracted test host has a private ContextVar; the real tool uses the variant's.
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            return await dispatch(name, arguments)
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    host.call_tool = call_tool
    return host


@pytest.mark.parametrize("scope", ["qspace project", "the workspace project"])
def test_workspace_question_survives_recovery_prompt(variant, runner, scope):
    begin(runner, f"From {scope} documentation, what is the default region? Answer only the region.")
    token = variant._TOOL_CALL_CONTEXT.set(runner._tool_permission_broker)
    try:
        decision = runner._decide_route("What is the region?")
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert decision.features["query_classification"]["project_context_required"]
    assert decision.selected_route != "simple_answer"


def test_format_only_pass_keeps_workspace_answer(variant, runner, monkeypatch):
    calls = []
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        calls.append((prompt, self._decide_route(prompt).selected_route))
        callback("status", "Model step 1/8: mock CPU pass")
        return "The default region is cobalt." if len(calls) == 1 else "cobalt"

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(
        "From qspace project documentation, what is the default region? Answer only the region.",
        lambda *_args: None,
    ))
    assert answer == "cobalt"
    assert len(calls) == 2
    assert calls[0][1] != "simple_answer" and calls[1][1] == "simple_answer"
    assert 'Candidate answer (JSON string): "The default region is cobalt."' in calls[1][0]
    assert "Use supplied current-turn evidence" in calls[1][0]
    assert "a rejected candidate is not proof" in calls[1][0]


@pytest.mark.parametrize("prefix", ["In qfiles", "In a.txt and b.txt"])
def test_set_each_file_is_an_action_not_question(variant, prefix):
    semantic = variant._analyze_semantic_intent(f"{prefix} set each state line to state=done. Preserve workspace lines.")
    assert semantic.speech_act == "request"
    assert semantic.requests("edit")


def test_one_file_atomic_receipt_and_preserved_context(variant, runner):
    changed = runner.workspace / "math.py"
    preserved = runner.workspace / "labels.py"
    changed.write_text("value = 1\n")
    preserved.write_text("label = 'preserved'\n")
    prompt = "Extend that value calculation; preserve the other module's behavior."
    broker = begin(runner, prompt)
    broker.bind_follow_up_repair("Edit both modules.", prompt, [str(changed), str(preserved)], revision=True)
    assert not broker.transaction_explicit_file_targets
    before = snapshot(broker, changed)
    changed.write_text("value = 2\n")
    digest = hashlib.sha256(changed.read_bytes()).hexdigest()
    arguments = {"patches": [{"path": "math.py", "replacements": [{"old_text": "1", "new_text": "2"}]}]}
    payload = {"files": [{"path": "math.py", "old_sha256": before, "new_sha256": digest}]}
    broker.record_result("apply_text_patches", arguments, receipts.result(payload))
    assert "changed_files" not in broker.completion_report(prompt)[1]
    assert preserved.read_text() == "label = 'preserved'\n"
    broker.record_result("apply_text_patches", arguments, receipts.result(payload, error=True))
    assert broker.evidence[-1]["success"] is False


@pytest.mark.parametrize("prompt", [
    "Change note.txt to finished followed by a newline.",
    "Replace note.txt content with finished followed by a newline; leave other.txt unchanged.",
    "I mean note.txt. Replace its content with finished followed by a newline; leave other.txt unchanged.",
])
def test_exact_content_repair_is_one_snapshot_bound_commit(variant, runner, prompt):
    target = runner.workspace / "note.txt"
    target.write_text("original\n")
    (runner.workspace / "other.txt").write_text("protected\n")
    broker = begin(runner, prompt)
    if not broker.transaction_explicit_file_targets:
        broker.transaction_explicit_file_targets.add(str(target))
    snapshot(broker, target)
    target.write_text("finished\n\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("apply_text_patch", {"path": "note.txt"}, receipts.result({"new_sha256": digest}))
    assert "saved_file_contract" in broker.completion_report(prompt)[1]
    host = host_for(variant, broker)
    asyncio.run(host.call_tool("read_file_snapshot", {"path": "note.txt"}))
    args = {"path": "note.txt", "replacements": [{"old_text": "finished\n\n", "new_text": "finished\n"}]}
    broker._saved_contract_correction_active = True
    correction = asyncio.run(host.call_tool("apply_text_patch", args))
    assert not correction.isError, correction.structuredContent
    assert target.read_bytes() == b"finished\n"
    assert "saved_file_contract" not in broker.completion_report(prompt)[1]
    blocked = asyncio.run(host.call_tool("apply_text_patch", {
        "path": "note.txt", "replacements": [{"old_text": "finished", "new_text": "unrequested"}],
    }))
    assert blocked.isError
    assert target.read_bytes() == b"finished\n"


@pytest.mark.parametrize("directive", ["Do not call or modify anything now.", "Do not use any tools."])
def test_no_tool_followup_excludes_reads_as_well(variant, runner, directive):
    prompt = "Explain the earlier failure and verified retry. " + directive
    broker = begin(runner, prompt)
    definitions = [{"name": name} for name in ("read_file", "list_project_mcp_tools", "execute_workflow")]
    assert runner._filter_tools_by_intent(prompt, definitions) == []
    for name in ("read_file", "call_project_mcp_tool", "execute_workflow"):
        assert not broker.authorize(name, {})[0]


def test_no_tool_turn_skips_workspace_and_source_preflights(variant, runner, monkeypatch):
    calls = []
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        callback("status", "Model step 1/8: mock CPU pass")
        decision = self._decide_route(prompt)
        assert decision.selected_route == "simple_answer"
        assert not decision.fallback_routes
        return "No retained evidence is available."

    def forbidden(*_args, **_kwargs):
        pytest.fail("No-tool turn entered a file, source or service preflight")

    for name in ("_empty_workspace_listing_preflight", "_dependency_install_preflight",
                 "_named_file_operation_preflight", "_wrapper_mcp_lifecycle_recovery",
                 "_prefetch_named_source_context"):
        monkeypatch.setattr(runner, name, forbidden)
    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(
        "Explain the earlier failure in qspace project and note.txt. Do not use any tools.",
        lambda *_args: None,
    ))
    assert answer == "No retained evidence is available."
    assert len(calls) == 1


def test_direct_no_tool_source_prefetch_stays_empty(variant, runner, monkeypatch):
    prompt = "Explain note.txt and https://example.org/guide using retained evidence. Do not use any tools."
    begin(runner, prompt)
    monkeypatch.setattr(variant._APP.base, "MCPHost", lambda *_args: pytest.fail("Unexpected tool discovery"))
    assert asyncio.run(runner._prefetch_named_source_context(prompt, None)) == ""


def test_quoted_tool_prohibition_is_not_an_instruction(variant):
    assert "tool_use" not in variant._analyze_semantic_intent('Explain the phrase "Do not use any tools."').prohibited_actions


def test_invalid_patch_does_not_create_a_backup(variant, runner):
    target = runner.workspace / "note.txt"
    target.write_text("original\n")
    broker = begin(runner, "Edit note.txt, replacing original with revised.")
    snapshot(broker, target)
    host = host_for(variant, broker)
    result = asyncio.run(host.call_tool("apply_text_patch", {
        "path": "note.txt", "replacements": [{"old_text": "absent", "new_text": "revised"}],
    }))
    assert result.isError
    assert result.structuredContent["status"] == "patch_text_mismatch"
    assert result.structuredContent["exact_match_count"] == 0
    assert result.structuredContent["file_unchanged"] is True
    assert result.structuredContent["mutation_dispatched"] is False
    assert not host.dispatched
    assert not (runner.workspace / ".qubitz" / "backups").exists()
    assert target.read_bytes() == b"original\n"


@pytest.mark.parametrize("previously_created", [False, True])
def test_single_python_patch_preserves_exact_pre_edit_backup(variant, runner, previously_created):
    target = runner.workspace / "job.py"
    original = b"value = 1\r\n"
    target.write_bytes(original)
    if previously_created:
        created = begin(runner, "Create job.py.")
        created.record_result("write_file", {"path": "job.py"}, receipts.result({
            "created": True, "new_sha256": hashlib.sha256(original).hexdigest(),
        }))
        assert str(target) in created.verified_creation_hashes
    broker = begin(runner, "Edit job.py, replacing value = 1 with value = 2.")
    snapshot(broker, target)
    backup_root = runner.workspace / "backup"
    backup_root.mkdir()
    first = backup_root / "job.py.001.bak"
    first.write_bytes(b"earlier backup\n")
    result = asyncio.run(host_for(variant, broker).call_tool("apply_text_patch", {
        "path": "job.py", "replacements": [{"old_text": "value = 1", "new_text": "value = 2"}],
    }))
    assert not result.isError
    assert target.read_bytes() == b"value = 2\r\n"
    assert first.read_bytes() == b"earlier backup\n"
    assert (backup_root / "job.py.002.bak").read_bytes() == original
    assert "backup\\job.py.002.bak | job.py |" in (runner.workspace / "changelog.txt").read_text()
    assert broker.transaction_originals[str(target)]["data"] == original


@pytest.mark.parametrize("damage", ["missing_match", "stale", "no_op"])
def test_rejected_python_patch_has_no_backup_side_effects(variant, runner, damage):
    target = runner.workspace / "job.py"
    target.write_bytes(b"value = 1\n")
    broker = begin(runner, "Edit job.py, replacing value = 1 with value = 2.")
    digest = snapshot(broker, target)
    replacements = [{"old_text": "value = 1", "new_text": "value = 2"}]
    if damage == "missing_match":
        replacements[0]["old_text"] = "missing"
    elif damage == "no_op":
        replacements[0]["new_text"] = "value = 1"
    else:
        target.write_bytes(b"value = 3\n")
    before = target.read_bytes()
    host = host_for(variant, broker)
    arguments = {"path": "job.py", "expected_sha256": digest, "replacements": replacements}
    if damage == "no_op":
        result = asyncio.run(host.call_tool("apply_text_patch", arguments))
        assert result.isError
        assert result.structuredContent["status"] == "invalid_arguments"
        assert "no-op" in str(result.structuredContent["errors"])
        assert not host.dispatched
    else:
        result = asyncio.run(host.call_tool("apply_text_patch", arguments))
        assert result.isError
        assert result.structuredContent["status"] == ("stale_snapshot" if damage == "stale" else "patch_text_mismatch")
        assert result.structuredContent["file_unchanged"] is True
        assert result.structuredContent["dispatch_attempted"] is False
        assert result.structuredContent["mutation_dispatched"] is False
        if damage == "stale":
            assert result.structuredContent["expected_sha256"] == digest
            assert result.structuredContent["current_sha256"] == hashlib.sha256(before).hexdigest()
        else:
            assert result.structuredContent["exact_match_count"] == 0
        assert not host.dispatched
    assert target.read_bytes() == before
    assert not (runner.workspace / "backup").exists()
    assert not (runner.workspace / "changelog.txt").exists()


@pytest.mark.parametrize("batch", [False, True])
def test_normal_mcp_host_preserves_pre_edit_backup(variant, runner, batch):
    target = runner.workspace / "job.py"
    original = b"value = 1\n"
    target.write_bytes(original)
    other = runner.workspace / "other.py"
    if batch:
        other.write_bytes(original)
    prompt = "Edit job.py and other.py, replacing value = 1 with value = 2 in both." if batch else (
        "Edit job.py, replacing value = 1 with value = 2."
    )
    broker = begin(runner, prompt)

    async def edit():
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            async with variant._APP.base.MCPHost(runner.workspace) as host:
                await host.list_tools()
                read = await host.call_tool("read_file_snapshot", {"path": "job.py"})
                assert not read.isError
                patch = {"path": "job.py", "replacements": [{"old_text": "value = 1", "new_text": "value = 2"}]}
                if batch:
                    read = await host.call_tool("read_file_snapshot", {"path": "other.py"})
                    assert not read.isError
                    result = await host.call_tool("apply_text_patches", {
                        "patches": [patch, {**patch, "path": "other.py"}],
                    })
                else:
                    result = await host.call_tool("apply_text_patch", patch)
                assert not result.isError, result.structuredContent
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    asyncio.run(edit())
    assert target.read_bytes() == b"value = 2\n"
    assert (runner.workspace / "backup" / "job.py.001.bak").read_bytes() == original
    assert "backup\\job.py.001.bak | job.py |" in (runner.workspace / "changelog.txt").read_text()
    assert broker.transaction_originals[str(target)]["data"] == original
    if batch:
        assert other.read_bytes() == b"value = 2\n"
        assert (runner.workspace / "backup" / "other.py.001.bak").read_bytes() == original
        assert broker.transaction_originals[str(other)]["data"] == original


def test_failed_required_backup_aborts_patch_before_replacement(variant, runner, monkeypatch):
    target = runner.workspace / "job.py"
    target.write_bytes(b"value = 1\n")
    broker = begin(runner, "Edit job.py, replacing value = 1 with value = 2.")
    snapshot(broker, target)

    def denied(*_args):
        raise OSError("Required backup unavailable")

    monkeypatch.setattr(broker, "register_transaction_original", denied)
    result = asyncio.run(host_for(variant, broker).call_tool("apply_text_patch", {
        "path": "job.py", "replacements": [{"old_text": "value = 1", "new_text": "value = 2"}],
    }))
    assert result.isError
    assert "Required backup unavailable" in result.structuredContent["reason"]
    assert target.read_bytes() == b"value = 1\n"
    assert not list(runner.workspace.glob(".*.qubitz-*.tmp"))


@pytest.mark.parametrize("suffix,tool", [
    (".ipynb", "apply_notebook_text_patch"),
    (".docx", "apply_docx_text_patch"),
    (".xlsx", "apply_spreadsheet_cell_patch"),
])
def test_normal_mcp_host_keeps_structured_patch_original(variant, runner, suffix, tool):
    from docx import Document
    from openpyxl import Workbook, load_workbook

    # Bind the disposable workspace to the existing environment, without installing anything.
    (runner.workspace / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    target = runner.workspace / ("artifact" + suffix)
    if suffix == ".ipynb":
        target.write_text(json.dumps({
            "nbformat": 4, "nbformat_minor": 5, "metadata": {"keep": "preserved"},
            "cells": [{"cell_type": "code", "metadata": {}, "execution_count": 7,
                       "outputs": [], "source": ["value = 1\n"]}],
        }))
    elif suffix == ".docx":
        document = Document()
        document.add_paragraph("value = 1")
        document.add_paragraph("preserved")
        document.save(target)
    else:
        workbook = Workbook()
        workbook.active["A1"] = "value = 1"
        workbook.active["B1"] = "preserved"
        workbook.save(target)
        workbook.close()
    original = target.read_bytes()
    broker = begin(runner, f"Edit {target.name}, replacing value = 1 with value = 2; preserve other contents.")

    async def edit():
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            async with variant._APP.base.MCPHost(runner.workspace) as host:
                await host.list_tools()
                read = await host.call_tool("read_file_snapshot", {"path": target.name})
                assert not read.isError, read.structuredContent
                arguments = {"path": target.name, "expected_sha256": hashlib.sha256(original).hexdigest()}
                if suffix == ".xlsx":
                    arguments["edits"] = [{"sheet": "Sheet", "cell": "A1", "value": "value = 2"}]
                else:
                    arguments["replacements"] = [{"old_text": "value = 1", "new_text": "value = 2"}]
                result = await host.call_tool(tool, arguments)
                assert not result.isError, result.structuredContent
                assert result.structuredContent["new_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    asyncio.run(edit())
    assert broker.transaction_originals[str(target)]["data"] == original
    if suffix == ".ipynb":
        notebook = json.loads(target.read_text())
        assert notebook["cells"][0]["source"] == ["value = 2\n"]
        assert notebook["cells"][0]["execution_count"] == 7
        assert notebook["metadata"] == {"keep": "preserved"}
    elif suffix == ".docx":
        assert [paragraph.text for paragraph in Document(target).paragraphs] == ["value = 2", "preserved"]
    else:
        workbook = load_workbook(target)
        try:
            assert workbook.active["A1"].value == "value = 2"
            assert workbook.active["B1"].value == "preserved"
        finally:
            workbook.close()


def test_creation_does_not_authorize_existing_control_file_edit(variant, runner):
    target = runner.workspace / "control.flag"
    target.write_text("locked\n")
    broker = begin(runner, "Call the MCP wait tool, then create output.json with the returned result.")
    snapshot(broker, target)
    result = asyncio.run(host_for(variant, broker).call_tool("apply_text_patch", {
        "path": "control.flag", "replacements": [{"old_text": "locked", "new_text": "unlocked"}],
    }))
    assert result.isError
    assert not (runner.workspace / ".qubitz" / "backups").exists()
    assert target.read_bytes() == b"locked\n"


def test_captured_json_field_keeps_exact_operator_and_type(variant, runner):
    target = runner.workspace / "note.md"
    target.write_text('Keep\n<!-- begin -->\n{"constraint":"3.12"}\n<!-- end -->\nFooter\n')
    prompt = "Add constraint from its metadata object to that marked JSON object. Preserve everything outside the markers."
    broker = begin(runner, prompt)
    broker.transaction_explicit_file_targets.add(str(target))
    snapshot(broker, target)
    broker.record_result("fetch_url", {"url": "https://example.org/data.json"}, receipts.result({
        "body": '{"metadata":{"constraint":">=3.12"}}', "http_status": 200, "retrieved": True,
    }))
    issues = variant._saved_text_contract_issues(broker, prompt, [])
    assert any("exact captured source values" in issue for issue in issues)
    target.write_text(target.read_text().replace('"3.12"', '">=3.12"'))
    assert not variant._saved_text_contract_issues(broker, prompt, [])


def test_shutdown_wait_retains_pid_creation_identity(variant):
    source = read_variant_source(Path(variant.__file__))
    tree = ast.parse(source)
    helper = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_stop_owned_windows_child")
    text = ast.get_source_segment(source, helper)
    assert "AddSeconds(15)" in text
    assert "$terminated.ContainsKey($identity)" in text
    assert "$remaining.CreationDate)" in text
    assert "$sameProcess" in text and "$receipt.created" in text
    assert "Start-Sleep -Milliseconds 100" in text


@pytest.mark.parametrize("finish", [True, False])
def test_pending_action_continues_only_after_verified_progress(variant, runner, monkeypatch, finish):
    source = runner.workspace / "seed.txt"
    source.write_text("captured\n")
    output = runner.workspace / "result.txt"
    calls = []
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        callback("status", "Model step 1/8: mock CPU pass")
        broker = self._tool_permission_broker
        if len(calls) == 1:
            broker.record_result("read_file", {"path": "seed.txt"}, receipts.result({"content": "captured\n"}))
        elif finish:
            output.write_text("captured\n")
            broker.record_result("write_file", {"path": "result.txt"}, receipts.result({
                "created": True, "new_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }))
        return "The task is complete."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async("Read seed.txt, then create result.txt from that actual text.", lambda *_args: None))
    # A stalled continuation can use the existing single forced-action recovery,
    # but cannot create an unbounded new sequence of model passes.
    assert len(calls) == 2  # Unrelated reads cannot purchase another continuation.
    if finish:
        assert output.read_bytes() == source.read_bytes()
        assert "[Completion status: verified]" in answer
    else:
        assert not output.exists()
        assert "[Completion status: partial]" in answer
