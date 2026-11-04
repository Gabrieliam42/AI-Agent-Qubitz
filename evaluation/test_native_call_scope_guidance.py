"""Captured textual tool envelopes and exact workspace paths; no model loads."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("name", ["write_file", "functions.write_file"])
def test_captured_tool_envelope_requests_native_correction_only(variant, runner, name):
    # The full-prompt diagnostic emitted this shape as ordinary content.
    prompt = "Create calculator.py with add(a, b) returning their sum."
    broker = receipts.begin(runner, prompt)
    payload = {"tool": name, "arguments": {
        "path": "calculator.py", "content": "def add(a, b):\n    return a + b\n",
    }}
    original = copy.deepcopy(payload)
    rendered = json.dumps(payload)
    before = copy.deepcopy(broker.evidence)

    assert variant._unexecuted_tool_call_name(rendered, ("write_file",)) == "write_file"
    assert payload == original
    assert broker.evidence == before
    assert "created_outputs" in broker.completion_report(prompt)[1]
    assert not (runner.workspace / "calculator.py").exists()


@pytest.mark.parametrize("payload", [
    {"tool": "functions.unknown", "arguments": {}},
    {"tool": "other.write_file", "arguments": {}},
    {"tool": "write_file"},
    {"tool": {"name": "write_file"}, "arguments": {}},
    {"path": "calculator.py", "content": "text"},
])
def test_nonadvertised_or_argument_only_json_is_not_a_named_call(variant, payload):
    assert variant._unexecuted_tool_call_name(json.dumps(payload), ("write_file",)) == ""


def test_fenced_tool_example_remains_data(variant):
    rendered = 'Example:\n```json\n{"tool":"functions.write_file","arguments":{}}\n```'
    assert variant._unexecuted_tool_call_name(rendered, ("write_file",)) == ""


def test_snapshot_schema_explains_exact_workspace_paths(variant, runner):
    server = variant._build_local_mcp_server(
        variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__),
    )
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "read_file_snapshot")
    assert "Relative paths resolve from the active workspace" in tool.description
    assert "copy absolute paths exactly" in tool.description
    assert "never reconstruct directory prefixes" in tool.description


def test_runtime_guidance_keeps_relative_paths_scoped(variant, runner):
    policy = runner._runtime_tool_contract_for_harness(
        "retrieval_plus_model", "coding_edit", "Edit calculator.py.",
    )
    assert "Relative filesystem tool paths resolve from the active workspace" in policy
    assert "copy absolute paths exactly" in policy


def test_snapshot_rejects_wrong_prefix_then_reads_exact_relative_file(variant, runner):
    target = runner.workspace / "calculator.py"
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    original = target.read_bytes()
    server = variant._build_local_mcp_server(
        variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__),
    )
    with pytest.raises(Exception, match="calculator"):
        asyncio.run(server.call_tool("read_file_snapshot", {"path": "wrong_prefix/calculator.py"}))
    snapshot = asyncio.run(server.call_tool("read_file_snapshot", {"path": "calculator.py"}))[1]
    assert snapshot["path"] == "calculator.py"
    assert snapshot["content"].strip().endswith("return a + b")
    assert target.read_bytes() == original
