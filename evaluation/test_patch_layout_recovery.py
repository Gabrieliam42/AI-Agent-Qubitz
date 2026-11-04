"""Captured patch failures through real local admission, without model inference."""
from __future__ import annotations

import asyncio
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
PROMPT = "In related.txt and shared.txt replace state=original with state=revised-b, preserving each workspace line."


@pytest.fixture
def case(variant, tmp_path):
    paths = [tmp_path / name for name in ("related.txt", "shared.txt")]
    for path in paths:
        path.write_text(f"workspace={path.stem}\nstate=original\n", encoding="utf-8")
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode(variant.ACCESS_MODE_FULL)
    broker.begin_turn(PROMPT, None)
    owner = SimpleNamespace(_cancel_event=threading.Event())
    class Owner:
        _cancel_event = owner._cancel_event

        def stop(self, *_args):
            pass
    broker.stop_callback = Owner().stop
    for path in paths:
        boundaries.record_snapshot(broker, path)
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    host = boundaries.host_for(variant, broker, server)
    host._qubitz_tool_contracts = {name: tools[name] for name in ("apply_text_patch", "apply_text_patches")}
    captured = json.loads((Path(__file__).parent / "fixtures/gemma_multifile_schema_rejection.json").read_text())
    return SimpleNamespace(paths=paths, broker=broker, host=host, bad=captured["arguments"], owner=owner)


def call(case, args=None, name="apply_text_patches"):
    return asyncio.run(case.host.call_tool(name, case.bad if args is None else args))


def test_captured_flat_batch_commits_once_through_original_atomic_tool(case):
    before = copy.deepcopy(case.bad)
    result = call(case)
    assert not result.isError
    assert result.structuredContent["file_count"] == 2
    assert len(case.host.dispatched) == 1
    assert case.bad == before
    dispatched = case.host.dispatched[0][1]["patches"]
    assert dispatched == [{"path": item["path"], "replacements": [
        {key: item[key] for key in ("old_text", "new_text")},
    ]} for item in before["patches"]]
    assert all("state=revised-b" in path.read_text() for path in case.paths)
    assert case.broker.schema_repairs[-1]["original"] == before
    assert call(case).isError
    assert len(case.host.dispatched) == 1


@pytest.mark.parametrize("damage", ["missing", "extra", "conflict", "type", "top_level", "mixed"])
def test_ambiguous_layout_is_not_rewritten_or_dispatched(case, damage):
    patch = case.bad["patches"][0]
    if damage == "missing":
        patch.pop("new_text")
    elif damage == "extra":
        patch["expected_sha256"] = "0" * 64
    elif damage == "conflict":
        patch["replacements"] = [{"old_text": "state=original", "new_text": "other"}]
    elif damage == "type":
        patch["new_text"] = None
    elif damage == "top_level":
        case.bad["other"] = "unadvertised"
    else:
        item = case.bad["patches"][1]
        case.bad["patches"][1] = {"path": item["path"], "replacements": [
            {key: item[key] for key in ("old_text", "new_text")},
        ]}
    original = copy.deepcopy(case.bad)
    assert call(case).isError
    assert not case.host.dispatched and case.bad == original
    assert all("state=original" in path.read_text() for path in case.paths)


@pytest.mark.parametrize("blocker", ["stale", "read_only", "exhausted", "uncertain", "snapshot", "target", "cancelled", "used"])
def test_layout_adapter_preserves_safety_and_budget(case, blocker):
    if blocker == "stale":
        case.paths[0].write_text("external change\n")
    elif blocker == "read_only":
        case.broker.set_access_mode("read_only")
    elif blocker == "exhausted":
        case.broker.no_progress_recovery_exhausted = True
    elif blocker == "uncertain":
        case.broker.evidence.append({"tool": "apply_text_patches", "success": False, "wrapper_rejected": False})
    elif blocker == "snapshot":
        case.broker.transaction_snapshot_hashes.clear()
    elif blocker == "target":
        case.bad["patches"][0]["path"] = "other.txt"
    elif blocker == "cancelled":
        case.owner._cancel_event.set()
    else:
        case.broker._text_patch_layout_repair_used = True
    before = {path: path.read_bytes() for path in case.paths}
    original = copy.deepcopy(case.bad)
    assert call(case).isError
    assert not case.host.dispatched and case.bad == original
    assert {path: path.read_bytes() for path in case.paths} == before


def test_relative_and_absolute_invalid_layout_share_retry_budget(case):
    case.bad["patches"][0].pop("new_text")
    assert call(case).structuredContent["status"] == "invalid_arguments"
    for item in case.bad["patches"]:
        item["path"] = str(case.broker.workspace / item["path"])
    assert call(case).structuredContent["status"] == "repeated_invalid_arguments"
    assert case.broker.transaction_recovery_required
    assert not case.host.dispatched


@pytest.mark.parametrize("absolute", [False, True])
def test_escaped_newline_mismatch_is_verified_before_dispatch(case, absolute):
    target = case.paths[1]
    case.broker.begin_turn("Edit shared.txt, replacing state=original with state=revised-b.", None)
    boundaries.record_snapshot(case.broker, target)
    interruptions = []
    case.broker.transaction_recovery_callback = lambda: interruptions.append(True)
    args = {"path": str(target) if absolute else target.name, "replacements": [
        {"old_text": r"state=original\n", "new_text": r"state=revised-b\n"},
    ]}
    before = copy.deepcopy(args)
    result = call(case, args, "apply_text_patch")
    assert result.isError
    payload = result.structuredContent
    assert payload["status"] == "patch_text_mismatch"
    assert payload["exact_match_count"] == 0
    assert payload["dispatch_attempted"] is False and payload["mutation_dispatched"] is False
    assert payload["qubitz_recovery"]["outcome"] == "rejected_before_dispatch"
    assert "snapshot" in payload["instruction"] and "escape" in payload["instruction"]
    assert interruptions == [True] and case.broker.transaction_recovery_required
    assert case.broker.transaction_recovery_interrupt_requested
    assert not case.host.dispatched and args == before
    assert "state=original\n" in target.read_text()
    corrected = {"path": target.name, "replacements": [
        {"old_text": "state=original\n", "new_text": "state=revised-b\n"},
    ]}
    assert not call(case, corrected, "apply_text_patch").isError
    assert len(case.host.dispatched) == 1 and "state=revised-b\n" in target.read_text()


def test_literal_backslash_text_is_preserved_not_decoded(case):
    target = case.paths[1]
    target.write_text(r"state=original\n" + "\n")
    case.broker.begin_turn("Edit shared.txt, replacing original with revised-b.", None)
    boundaries.record_snapshot(case.broker, target)
    args = {"path": target.name, "replacements": [
        {"old_text": r"state=original\n", "new_text": r"state=revised-b\n"},
    ]}
    assert not call(case, args, "apply_text_patch").isError
    assert target.read_text() == r"state=revised-b\n" + "\n"
