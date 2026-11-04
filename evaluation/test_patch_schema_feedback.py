"""Captured Gemma schema failure: exact feedback, unchanged admission and commit."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
PROMPT = "In shared.txt and related.txt replace state=original with state=revised-b in both files, preserving each workspace line."


@pytest.fixture
def setup(variant, tmp_path):
    paths = [tmp_path / name for name in ("related.txt", "shared.txt")]
    for path in paths:
        path.write_text(f"workspace={path.stem}\nstate=original\n", encoding="utf-8")
    protected = tmp_path / "protected.txt"
    protected.write_text("must stay unchanged\n", encoding="utf-8")
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode(variant.ACCESS_MODE_FULL)
    broker.begin_turn(PROMPT, None)
    # Feedback remains the fallback after the one deterministic layout correction is spent.
    broker._text_patch_layout_repair_used = True
    for path in paths:
        boundaries.record_snapshot(broker, path)
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    host = boundaries.host_for(variant, broker, server)
    host._qubitz_tool_contracts = {name: tools[name] for name in ("apply_text_patch", "apply_text_patches")}
    captured = json.loads((Path(__file__).parent / "fixtures/gemma_multifile_schema_rejection.json").read_text())
    return SimpleNamespace(paths=paths, protected=protected, broker=broker, host=host,
                           bad=captured["arguments"], tools=tools)


def test_captured_rejection_returns_exact_bounded_argument_contract_once(variant, setup):
    before = copy.deepcopy(setup.bad)
    payload = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad)).structuredContent
    assert payload["status"] == "invalid_arguments"
    assert payload.get("argument_contract") == variant._strict_tool_schema(setup.tools["apply_text_patches"].inputSchema)
    assert len(json.dumps(payload["argument_contract"], ensure_ascii=True)) <= 4096
    assert "replacements" in payload["instruction"] and "Preserve" in payload["instruction"]
    assert payload["dispatch_attempted"] is False and payload["mutation_dispatched"] is False
    assert setup.bad == before and not setup.host.dispatched
    assert all("state=original" in path.read_text() for path in setup.paths)
    repeated = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad)).structuredContent
    assert repeated["status"] == "repeated_invalid_arguments"
    assert "argument_contract" not in repeated
    assert setup.broker.transaction_recovery_required
    assert not setup.host.dispatched


def test_guided_corrected_batch_uses_normal_atomic_broker(variant, setup):
    result = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad))
    assert result.isError and "argument_contract" in result.structuredContent
    corrected = {"patches": [{"path": patch["path"], "replacements": [
        {key: patch[key] for key in ("old_text", "new_text")},
    ]} for patch in setup.bad["patches"]]}
    original = copy.deepcopy(corrected)
    assert not variant._json_schema_errors(corrected, result.structuredContent["argument_contract"])
    applied = asyncio.run(setup.host.call_tool("apply_text_patches", corrected))
    assert not applied.isError
    assert applied.structuredContent["file_count"] == 2
    assert len(setup.host.dispatched) == 1 and corrected == original
    assert all("state=revised-b" in path.read_text() for path in setup.paths)
    assert setup.protected.read_text() == "must stay unchanged\n"


@pytest.mark.parametrize("mode", ["stale", "read_only", "incomplete"])
def test_feedback_does_not_bypass_existing_safety(variant, setup, mode):
    rejected = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad))
    assert "argument_contract" in rejected.structuredContent
    corrected = {"patches": [{"path": patch["path"], "replacements": [
        {key: patch[key] for key in ("old_text", "new_text")},
    ]} for patch in setup.bad["patches"]]}
    if mode == "stale":
        setup.paths[0].write_text("external change\n", encoding="utf-8")
    elif mode == "read_only":
        setup.broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    else:
        corrected["patches"].pop()
    before = {path.name: path.read_bytes() for path in setup.paths}
    result = asyncio.run(setup.host.call_tool("apply_text_patches", corrected))
    if mode == "incomplete":
        assert not result.isError
        assert result.structuredContent["status"] == "patch_staged"
        assert result.structuredContent["file_unchanged"] is True
        assert result.structuredContent["pending_paths"] == ["shared.txt"]
    else:
        assert result.isError
    assert {path.name: path.read_bytes() for path in setup.paths} == before
    assert setup.protected.read_text() == "must stay unchanged\n"


def test_missing_replacement_value_is_not_invented(setup):
    setup.bad["patches"][0].pop("new_text")
    before = copy.deepcopy(setup.bad)
    result = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad))
    assert result.isError and "argument_contract" in result.structuredContent
    assert setup.bad == before and not setup.host.dispatched
    assert all("state=original" in path.read_text() for path in setup.paths)


def test_single_file_feedback_requires_snapshot_hash_and_replacements(variant, setup):
    args = {"path": "shared.txt", "old_text": "state=original", "new_text": "state=revised-b"}
    before = copy.deepcopy(args)
    result = asyncio.run(setup.host.call_tool("apply_text_patch", args))
    assert result.isError
    contract = result.structuredContent.get("argument_contract")
    assert contract == variant._strict_tool_schema(setup.tools["apply_text_patch"].inputSchema)
    assert {"path", "replacements"}.issubset(contract["required"])
    assert "expected_sha256" in contract["properties"]
    assert args == before and not setup.host.dispatched


def test_feedback_does_not_reopen_recovery_budget(setup):
    setup.broker._tool_argument_recovery_used = True
    setup.broker.no_progress_recovery_exhausted = True
    result = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad))
    assert result.isError and "argument_contract" in result.structuredContent
    assert setup.broker._tool_argument_recovery_used
    assert setup.broker.no_progress_recovery_exhausted
    assert not setup.host.dispatched


def test_oversized_contract_is_not_copied_into_feedback(setup):
    original = setup.host._qubitz_tool_contracts["apply_text_patches"]
    schema = copy.deepcopy(original.inputSchema)
    schema["description"] = "large local description " * 300
    setup.host._qubitz_tool_contracts["apply_text_patches"] = SimpleNamespace(inputSchema=schema)
    result = asyncio.run(setup.host.call_tool("apply_text_patches", setup.bad))
    assert result.isError and "argument_contract" not in result.structuredContent
    assert "replacements" in result.structuredContent["instruction"]
    assert len(result.structuredContent["instruction"]) < 700
    assert not setup.host.dispatched
