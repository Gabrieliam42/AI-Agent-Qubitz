"""Recorded verification and interrupted-edit defects, without model inference."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation.disposable_followup_matrix import runner_snapshot
from evaluation.test_snapshot_behavior_recovery import (
    AFTER,
    BEFORE,
    CASES,
    PROMPT,
    wire_checks,
)

variant = receipts.variant
runner = receipts.runner

WRONG_ZERO_CASES = [
    {"function": "divide", "args": [10, 2], "expected": 5, "raises": ""},
    {"function": "divide", "args": [-8, 4], "expected": -2, "raises": ""},
    {"function": "divide", "args": [0, 5], "expected": None, "raises": "ValueError"},
    {"function": "divide", "args": [7, 0], "expected": None, "raises": "ValueError"},
]


def parent_runner(runner):
    return next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]


def test_numeric_disagreement_identifies_the_failed_proposed_case():
    from runtime.behavioral_checks import check_numeric_cases, numeric_contract

    with pytest.raises(ValueError, match="Task-derived numeric check failed") as error:
        check_numeric_cases(AFTER, numeric_contract(BEFORE, AFTER), WRONG_ZERO_CASES)
    assert error.value.case_result == {
        "case_index": 3, "function": "divide", "args": [0, 5],
        "expected": None, "expected_exception": "ValueError",
        "observed": 0.0, "observed_exception": "", "matched": False,
    }


def test_numeric_disagreement_survives_turn_cleanup_without_repairing_correct_code(
    variant, runner, tmp_path, monkeypatch,
):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    passes, proposals = [], []

    def propose(**kwargs):
        proposals.append(kwargs)
        assert kwargs["schema_name"] == "qubitz_numeric_behavior_checks"
        assert "exception condition and affected" in kwargs["messages"][0]["content"]
        return {"checks": wire_checks([{"path": target.name, **case} for case in WRONG_ZERO_CASES])}

    async def model_pass(self, _prompt, callback=None):
        passes.append(_prompt)
        broker = self._tool_permission_broker
        broker.required_postconditions.add("tests")
        broker.transaction_originals[str(target)] = {"data": BEFORE.encode()}
        target.write_text(AFTER)
        broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
            "status": "completed", "path": str(target),
            "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        self._raise_if_cancelled()
        return "The edit was saved."

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch)
    answer = asyncio.run(runner._run_async(PROMPT))
    assert len(passes) == 1 and len(proposals) == 2
    assert target.read_text() == AFTER
    assert all(name == "read_file_snapshot" for name, _args in calls)
    assert "[Completion status: partial]" in answer
    assert not runner._tool_permission_broker.evidence
    recorded = runner_snapshot(runner)["verification_receipts"]
    assert len(recorded) == 1 and recorded[0]["outcome"] == "verification_disagreement"
    assert recorded[0]["checks"][2]["args"] == [0, 5]
    assert recorded[0]["case_results"][-1]["observed"] == 0.0
    assert not recorded[0]["success"] and not recorded[0]["module_executed"]


@pytest.mark.parametrize("mode", [
    "passed", "bad_span", "exhausted", "uncertain", "read_only", "cancelled",
    "changed", "late_read_only", "late_cancelled", "late_uncertain", "turn_changed", "workspace_changed",
])
def test_internal_patch_interrupt_uses_one_proposal_not_another_native_pass(
    variant, runner, tmp_path, monkeypatch, mode,
):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    prompt = "Edit calculator.py to add divide(a, b), raising ValueError for a zero divisor. Preserve add."
    passes, proposals, rejected = [], [], []

    def propose(**kwargs):
        proposals.append(kwargs["schema_name"])
        if kwargs["schema_name"] == "qubitz_numeric_behavior_checks":
            return {"checks": wire_checks([{"path": target.name, **case} for case in CASES])}
        assert kwargs["schema_name"] == "qubitz_transaction_proposal"
        broker = runner._tool_permission_broker
        if mode == "changed":
            target.write_text("external\n")
        if mode == "late_read_only":
            runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
        if mode == "late_cancelled":
            runner.request_cancel()
        if mode == "late_uncertain":
            broker.evidence.append({"turn_id": broker.turn_id, "tool": "apply_text_patch",
                                    "success": False, "wrapper_rejected": False})
        if mode == "turn_changed":
            broker.turn_id += "-changed"
        if mode == "workspace_changed":
            broker.workspace_generation += 1
        return {"patches": [{"path": target.name, "replacements": [{
            "start_line": 1, "end_line": 99 if mode == "bad_span" else 2, "new_text": AFTER,
        }]}]}

    async def model_pass(self, pass_prompt, callback=None):
        passes.append(pass_prompt)
        broker = self._tool_permission_broker
        broker.required_postconditions.add("tests")
        boundaries.record_snapshot(broker, target)
        host = boundaries.host_for(variant, broker)
        result = await host.call_tool("apply_text_patch", {
            "path": target.name, "replacements": [{"old_text": "not the current text", "new_text": AFTER}],
        })
        rejected.append(result.structuredContent)
        assert result.isError and not host.dispatched
        assert result.structuredContent["status"] == "patch_text_mismatch"
        if mode == "exhausted":
            broker.no_progress_recovery_exhausted = True
        if mode == "uncertain":
            broker.evidence.append({"turn_id": broker.turn_id, "tool": "apply_text_patch",
                                    "success": False, "wrapper_rejected": False})
        if mode == "read_only":
            self.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
        if mode == "cancelled":
            self.request_cancel()
        self._raise_if_cancelled()
        return "No edit was dispatched."

    def handle(name, arguments):
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if name == "read_file_snapshot":
            return {"status": "text_snapshot", "content": target.read_text(), "sha256": digest}
        assert name == "apply_text_patch" and arguments["expected_sha256"] == digest
        broker = runner._tool_permission_broker
        allowed, error = broker.authorize(name, arguments)
        assert allowed, error
        replacement = arguments["replacements"][0]
        assert replacement["old_text"] == BEFORE
        target.write_text(replacement["new_text"])
        return {"status": "completed", "path": str(target),
                "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch, handler=handle)
    try:
        answer = asyncio.run(runner._run_async(prompt))
    except (asyncio.CancelledError, variant._APP.base.RunCancelled):
        assert mode in {"cancelled", "late_cancelled"}
        answer = "cancelled"
    assert len(passes) == 1 and len(rejected) == 1
    if mode == "passed":
        assert proposals == ["qubitz_transaction_proposal", "qubitz_numeric_behavior_checks"]
        assert "[Completion status: verified]" in answer and target.read_text() == AFTER
        assert sum(name == "apply_text_patch" for name, _args in calls) == 1
    else:
        assert proposals == ([] if mode in {"exhausted", "uncertain", "read_only", "cancelled"}
                             else ["qubitz_transaction_proposal"])
        assert target.read_text() == ("external\n" if mode == "changed" else BEFORE)
        assert not any(name == "apply_text_patch" for name, _args in calls)

