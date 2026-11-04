"""Model-free checks for evidence-backed tool failure recovery."""
from __future__ import annotations

import asyncio
import hashlib
import textwrap

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
runner = receipts.runner


def test_rejected_path_reports_current_workspace_and_verified_snapshots(variant, runner):
    targets = [runner.workspace / name for name in ("related.txt", "shared.txt")]
    for target in targets:
        target.write_text(f"original {target.stem}\n", encoding="utf-8")
    broker = receipts.begin(runner, "Edit shared.txt and related.txt to add a note.")
    digest = boundaries.record_snapshot(broker, targets[1])
    host = boundaries.host_for(variant, broker)

    result = asyncio.run(host.call_tool("read_file_snapshot", {"path": "fixtures/a/shared.txt"}))

    assert result.isError
    assert result.structuredContent["status"] == "path_resolution_error"
    assert host.dispatched == []
    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["outcome"] == "rejected_before_dispatch"
    assert recovery["active_workspace"] == str(runner.workspace.resolve())
    assert [item["path"] for item in recovery["current_targets"]] == ["related.txt", "shared.txt"]
    assert recovery["current_targets"][1]["sha256"] == digest
    assert recovery["current_targets"][1]["matches_verified_snapshot"] is True
    assert "changed_files" in recovery["pending_postconditions"]
    assert broker.evidence[-1]["wrapper_rejected"] is True


def test_incomplete_batch_names_only_unstaged_exact_target(variant, runner):
    targets = [runner.workspace / name for name in ("related.txt", "shared.txt")]
    for target in targets:
        target.write_text("original\n", encoding="utf-8")
    broker = receipts.begin(runner, "Edit shared.txt and related.txt to add a note.")
    broker.transaction_staged_patches[str(targets[1])] = {"path": "shared.txt"}
    result = receipts.result({
        "status": "atomic_batch_incomplete",
        "dispatch_attempted": False,
        "qubitz_recovery": {"outcome": "forged"},
    }, error=True)

    broker.record_result("apply_text_patches", {"patches": []}, result, wrapper_rejected=True)

    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["outcome"] == "rejected_before_dispatch"
    assert recovery["pending_paths"] == ["related.txt"]
    assert "complete atomic batch" in recovery["next_action"]
    assert broker.transaction_staged_patches[str(targets[1])] == {"path": "shared.txt"}


@pytest.mark.parametrize("changed_after_snapshot", [False, True])
def test_underlying_mutation_error_cannot_claim_wrapper_rejection(
    variant, runner, changed_after_snapshot,
):
    target = runner.workspace / "state.txt"
    target.write_text("original\n", encoding="utf-8")
    broker = receipts.begin(runner, "Edit state.txt to add a note.")
    boundaries.record_snapshot(broker, target)
    if changed_after_snapshot:
        target.write_text("external change\n", encoding="utf-8")
    result = receipts.result({
        "status": "patch_failed", "dispatch_attempted": False,
        "mutation_dispatched": False, "qubitz_recovery": {"outcome": "forged"},
    }, error=True)

    broker.record_result("apply_text_patch", {"path": "state.txt"}, result)

    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["outcome"] == "mutation_outcome_uncertain"
    assert recovery["current_targets"][0]["matches_verified_snapshot"] is not changed_after_snapshot
    assert recovery["current_targets"][0]["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert "Do not replay" in recovery["next_action"]
    assert broker.evidence[-1]["wrapper_rejected"] is False


def test_dispatched_mcp_mutation_requires_read_only_reconciliation(variant, runner):
    broker = receipts.begin(runner, "Call the project MCP mutation tool with the supplied value.")
    arguments = {"server_reference": "fixture", "tool_name": "update", "arguments": {"value": 7}}
    broker.mcp_invocations[broker._mcp_invocation_key(arguments)] = {
        "turn_id": broker.turn_id, "outcome": "attempted",
    }
    result = receipts.result({"status": "failed", "dispatch_attempted": False}, error=True)

    broker.record_result("call_project_mcp_tool", arguments, result)

    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["outcome"] == "mutation_outcome_uncertain"
    assert "authorized read-only tool" in recovery["next_action"]
    assert broker.evidence[-1]["mcp_dispatched"] is True
    assert broker.evidence[-1]["mcp_outcome"] == "uncertain"


def test_failed_command_may_have_side_effects_despite_false_dispatch_claim(variant, runner):
    broker = receipts.begin(runner, "Run check.py and report its exit code.")
    result = receipts.result({
        "status": "failed", "returncode": 1, "dispatch_attempted": False,
        "stderr": "assertion failed",
    })

    broker.record_result("run_existing_entrypoint", {"path": "check.py"}, result)

    recovery = result.structuredContent["qubitz_recovery"]
    assert recovery["outcome"] == "mutation_outcome_uncertain"
    assert "Do not replay" in recovery["next_action"]


def test_embedded_no_progress_exhaustion_is_owned_by_the_turn(variant, runner):
    prompt = "Edit state.txt to add a note."
    (runner.workspace / "state.txt").write_text("original\n", encoding="utf-8")
    broker = receipts.begin(runner, prompt)
    assert runner._wrapper_no_progress_tool_choice(prompt, "") is not None
    scope = {}
    source = (
        "def finish(self, empty_model_steps, focused_no_progress_recovery_used, messages):\n"
        "    step = 0\n"
        + textwrap.indent(textwrap.dedent(variant._EMBEDDED_EMPTY_STEP_NEW), "    ")
    )
    exec(compile(source, "<trusted embedded no-progress block>", "exec"), scope)  # noqa: S102

    result = scope["finish"](runner, 2, True, [])

    assert result.startswith("Stopped after a focused recovery attempt")
    assert broker.no_progress_recovery_exhausted is True
    assert runner._wrapper_no_progress_tool_choice(prompt, result) is None
    broker.begin_turn(prompt, None)
    assert broker.no_progress_recovery_exhausted is False


def test_exhausted_recovery_does_not_start_a_second_model_pass(variant, runner, monkeypatch):
    prompt = "Edit state.txt to add a note."
    target = runner.workspace / "state.txt"
    target.write_text("original\n", encoding="utf-8")
    calls = []
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]

    async def exhausted_pass(self, pass_prompt, callback=None):
        calls.append(pass_prompt)
        self._tool_permission_broker.no_progress_recovery_exhausted = True
        return (
            "Stopped after a focused recovery attempt because consecutive model steps produced "
            "no tool call, final answer, changed state, or verified evidence."
        )

    monkeypatch.setattr(parent, "_run_async", exhausted_pass)
    answer = asyncio.run(runner._run_async(prompt))

    assert len(calls) == 1
    assert "[Completion status: partial]" in answer
    assert target.read_text(encoding="utf-8") == "original\n"
