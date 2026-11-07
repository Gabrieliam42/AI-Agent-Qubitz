"""Model-free execution attempts must not become automatic execution retries."""
from __future__ import annotations

import asyncio
import os
import shlex
import sys
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import read_variant_source

variant = receipts.variant
runner = receipts.runner


def execution_result(code=0):
    identity = {"pid": 23, "creation": "fixture", "namespace": "fixture"}
    return receipts.result({
        "status": "completed", "command": "python job.py; echo finished", "returncode": code,
        "execution_receipt": {
            "actual_wait": True, "supervisor_actual_wait": True,
            "started": {"token": "fixture", "identity": identity},
            "exit": {"token": "fixture", "identity": identity, "actual_wait": True,
                     "tree_verified": True, "returncode": code},
        },
    })


@pytest.mark.parametrize("code", [0, 1])
def test_completed_shell_command_does_not_verify_exact_target_or_force_a_second_execution(
    variant, runner, code,
):
    (runner.workspace / "job.py").write_text("pass\n")
    prompt = "Run job.py and report its exit code. Do not edit it."
    broker = receipts.begin(runner, prompt)
    broker.record_result("run_command", {"command": "python job.py; echo finished"}, execution_result(code))
    assert broker.evidence[-1]["execution_verified"]
    assert "entrypoint_executed" in broker.completion_report(prompt)[1]
    assert runner._wrapper_no_progress_tool_choice(prompt, "Command exited.") is None
    assert not broker._missing_action_recovery_used


@pytest.mark.parametrize("tool", ["run_command", "run_existing_entrypoint", "run_python_script", "run_shell_command"])
def test_attempted_execution_with_unknown_outcome_never_forces_execution_recovery(variant, runner, tool):
    (runner.workspace / "job.py").write_text("pass\n")
    prompt = "Run job.py and report its exit code."
    broker = receipts.begin(runner, prompt)
    broker.record_result(tool, {"path": "job.py", "command": "python job.py"}, receipts.result(
        {"status": "failed", "reason": "Connection lost after dispatch"}, error=True,
    ))
    assert "entrypoint_executed" in broker.completion_report(prompt)[1]
    assert runner._wrapper_no_progress_tool_choice(prompt, "Outcome unknown.") is None


@pytest.mark.parametrize("tool", ["run_python_script", "run_shell_command"])
def test_legacy_execution_aliases_respect_execute_prohibition(variant, runner, tool):
    (runner.workspace / "job.py").write_text("pass\n")
    broker = receipts.begin(runner, "Read job.py only. Do not execute it.")
    allowed, denial = broker.authorize(tool, {"path": "job.py", "command": "python job.py"})
    assert not allowed and denial is not None


def test_proven_wrapper_predispatch_rejection_can_still_recover(variant, runner):
    (runner.workspace / "job.py").write_text("pass\n")
    prompt = "Run job.py and report its exit code."
    broker = receipts.begin(runner, prompt)
    broker.record_result("run_existing_entrypoint", {"path": "missing/job.py"}, receipts.result(
        {"status": "path_resolution_error", "dispatch_attempted": False}, error=True,
    ), wrapper_rejected=True)
    assert broker.evidence[-1]["wrapper_rejected"]
    assert runner._wrapper_no_progress_tool_choice(prompt, "")["function"]["name"] == "run_existing_entrypoint"


def test_previous_turn_execution_does_not_block_a_new_explicit_request(variant, runner):
    (runner.workspace / "job.py").write_text("pass\n")
    prompt = "Run job.py and report its exit code."
    broker = receipts.begin(runner, prompt)
    broker.record_result("run_command", {"command": "python job.py"}, execution_result())
    assert runner._wrapper_no_progress_tool_choice(prompt, "") is None
    broker.begin_turn(prompt, None)
    assert not broker._current_turn_evidence()
    assert runner._wrapper_no_progress_tool_choice(prompt, "")["function"]["name"] == "run_existing_entrypoint"


def test_read_only_inspection_does_not_block_first_execution(variant, runner):
    target = runner.workspace / "job.py"
    target.write_text("pass\n")
    prompt = "Run job.py and report its exit code."
    broker = receipts.begin(runner, prompt)
    broker.record_result("read_file", {"path": "job.py"}, receipts.result({"content": "pass\n"}))
    assert runner._wrapper_no_progress_tool_choice(prompt, "")["function"]["name"] == "run_existing_entrypoint"


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell semantics are exercised through the WSL project environment")
def test_real_stdio_shell_effect_happens_once_without_weakening_program_proof(variant, runner):
    target = runner.workspace / "job.py"
    target.write_text(
        "from pathlib import Path\n"
        "with Path('executions.txt').open('a') as stream: stream.write('x')\n"
        "raise SystemExit(7)\n",
    )
    prompt = "Run job.py and report its exit code. Do not edit it."
    broker = receipts.begin(runner, prompt)
    command = f"{shlex.quote(sys.executable)} {shlex.quote(str(target))}; printf 'finished\\n'"

    async def execute():
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            async with variant._APP.base.MCPHost(runner.workspace) as host:
                await host.list_tools()
                result = await host.call_tool("run_command", {"command": command, "timeout_seconds": 30})
                assert not result.isError, result.structuredContent
                return result.structuredContent
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    data = asyncio.run(execute())
    assert data["returncode"] == 0  # The shell's final printf masked the program's exit 7.
    assert data["execution_receipt"]["identity_scope"] == "managed_containment_root_not_target_program"
    assert data["execution_receipt"]["returncode_scope"] == "shell_command"
    assert (runner.workspace / "executions.txt").read_text() == "x"
    assert "entrypoint_executed" in broker.completion_report(prompt)[1]
    assert runner._wrapper_no_progress_tool_choice(prompt, "The shell returned zero.") is None
    assert (runner.workspace / "executions.txt").read_text() == "x"


def test_recovery_prompt_does_not_deny_an_actual_previous_tool_call(variant):
    source = read_variant_source(Path(variant.__file__))
    assert "previous model pass produced no tool call or usable final answer" not in source
    assert "previous model pass did not verify all required postconditions" in source
