"""Exercise progress and recovery hints at the actual public tool boundary."""
from __future__ import annotations

import asyncio
import copy

from evaluation import test_patch_layout_recovery as layout
from evaluation import test_tool_argument_boundaries as boundaries

variant = layout.variant
case = layout.case


def test_committed_hash_cycle_is_rejected_before_atomic_dispatch(case):
    assert not layout.call(case).isError
    for path in case.paths:
        boundaries.record_snapshot(case.broker, path)
    # Isolate the new hash guard from the stronger single-transaction gate.
    # Created outputs can reach this path without an existing-edit transaction.
    case.broker.transaction_changed_hashes.clear()
    undo = copy.deepcopy(case.bad)
    for patch in undo["patches"]:
        patch["old_text"], patch["new_text"] = patch["new_text"], patch["old_text"]
    # The layout correction itself is one-use; submit the ordinary valid schema.
    args = {"patches": [{"path": p["path"], "replacements": [
        {"old_text": p["old_text"], "new_text": p["new_text"]},
    ]} for p in undo["patches"]]}
    result = layout.call(case, args)
    assert result.isError and result.structuredContent["status"] == "action_cycle_blocked"
    assert result.structuredContent["mutation_dispatched"] is False
    assert case.broker.no_progress_recovery_exhausted
    assert len(case.host.dispatched) == 1
    assert all("state=revised-b" in path.read_text() for path in case.paths)


def test_rejection_hints_bind_only_exact_current_authorized_targets(case):
    malformed = {"patches": [{"path": "fixtures/b/related.txt", "old_text": "original"}]}
    result = layout.call(case, malformed)
    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["active_workspace"] == str(case.broker.workspace.resolve())
    assert {x["canonical_path"] for x in recovery["authorized_targets"]} == {str(p) for p in case.paths}
    assert {x["workspace_relative"] for x in recovery["authorized_targets"]} == {p.name for p in case.paths}
    assert not case.host.dispatched
    assert recovery["progress_budget"]["recovery_phases_remaining"] == 2


def test_new_turn_resets_progress_not_cross_workspace_authority(case):
    assert not layout.call(case).isError
    case.broker.begin_turn("Edit related.txt and shared.txt: restore state=original.", None)
    assert not case.broker.action_progress.hashes
    assert not case.broker.action_progress.recoveries
    assert not case.broker.no_progress_recovery_exhausted


def test_execution_cycle_cannot_be_reset_by_reading_same_snapshot(case, variant):
    (case.broker.workspace / "run_tests.py").write_text("print('ok')\n")
    case.broker.begin_turn("Edit related.txt and then run run_tests.py.", None)
    boundaries.record_snapshot(case.broker, case.paths[0])
    host = boundaries.host_for(variant, case.broker)
    for _ in range(2):
        result = asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "run_tests.py"}))
        assert not result.isError
        boundaries.record_snapshot(case.broker, case.paths[0])
    result = asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "run_tests.py"}))
    assert result.isError and result.structuredContent["status"] == "action_cycle_blocked"
    assert len(host.dispatched) == 2


def test_file_hashes_never_control_remote_mcp_progress(case, variant, monkeypatch):
    (case.broker.workspace / "fixture_mcp.py").write_text("# Test-owned endpoint; dispatch is mocked.\n")
    case.broker.record_result("list_project_mcp_tools", {"server_reference": "fixture_mcp.py"},
        layout.receipts.result({"count": 1, "tools": [{"name": "read", "inputSchema": {
            "type": "object", "properties": {},
        }}]}))
    host = boundaries.host_for(variant, case.broker)
    monkeypatch.setattr(case.broker, "authorize", lambda *_a: (True, None))
    monkeypatch.setattr(case.broker, "reserve_mcp_invocation", lambda *_a: None)
    monkeypatch.setattr(case.broker.action_progress, "reserve_execution",
                        lambda *_a: (_ for _ in ()).throw(AssertionError("Remote state is not a file hash")))
    result = asyncio.run(host.call_tool("call_project_mcp_tool", {
        "server_reference": "fixture_mcp.py", "tool_name": "read", "arguments": {},
    }))
    assert not result.isError and len(host.dispatched) == 1
