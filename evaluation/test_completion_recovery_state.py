"""Model-free cancellation evidence and snapshot-aware repeat guard regressions."""
from __future__ import annotations

import hashlib

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


def record_creation(broker, target):
    target.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    broker.record_result("write_file", {"path": str(target)}, receipts.result({
        "created": True, "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))


def record_snapshot(broker, target, *, error=False, checksum=None):
    data = target.read_bytes()
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "status": "text_snapshot", "sha256": checksum or hashlib.sha256(data).hexdigest(),
        "content": data.decode("utf-8"), "start_line": 1, "end_line": len(data.splitlines()),
    }, error=error))


def test_cancel_keeps_verified_creation_when_file_postconditions_are_complete(variant, runner):
    prompt = "Create calculator.py as a basic calculator."
    broker = receipts.begin(runner, prompt)
    target = runner.workspace / "calculator.py"
    record_creation(broker, target)
    assert not broker.completion_report(prompt)[1]
    assert not variant._artifact_completion_can_end_turn(broker)
    runner.request_cancel()
    answer = runner._cancel_partial_answer
    assert "Verified before cancellation:" in answer
    assert "verified paths: 1" in answer
    assert "before any requested result was verified" not in answer
    assert "[Completion status: verified]" not in answer
    assert "unverified" in answer.lower()
    assert target.is_file()


def test_wrapper_stop_preserves_complete_file_evidence_without_claiming_task_success(variant, runner):
    prompt = "Create calculator.py as a basic calculator."
    broker = receipts.begin(runner, prompt)
    record_creation(broker, runner.workspace / "calculator.py")
    runner._request_wrapper_stop("bounded recovery exhausted")
    answer = runner._cancel_partial_answer
    assert "Verified before the wrapper stop:" in answer
    assert "No requested result was verified" not in answer
    assert "[Completion status: verified]" not in answer


def test_partial_progress_never_borrows_success_from_another_turn(variant, runner):
    broker = receipts.begin(runner, "Create calculator.py and notes.txt.")
    broker.evidence.append({"turn_id": "old-turn", "success": True,
                            "paths": [str(runner.workspace / "unrelated.txt")],
                            "categories": ["created_outputs"]})
    assert broker.partial_progress_answer(broker.current_prompt) == ""


def test_failed_creation_is_not_cancelled_verified_progress(variant, runner):
    broker = receipts.begin(runner, "Create calculator.py.")
    broker.record_result("write_file", {"path": "calculator.py"},
                         receipts.result({"status": "failed"}, error=True))
    assert broker.partial_progress_answer(broker.current_prompt) == ""


@pytest.mark.parametrize("batch", [False, True])
def test_successful_same_target_snapshot_allows_a_new_bounded_patch_attempt(variant, runner, batch):
    broker = receipts.begin(runner, "Edit calculator.py to fix addition.")
    target = runner.workspace / "calculator.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    patch = {"path": "calculator.py", "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
             "replacements": [{"old_text": "a - b", "new_text": "a + b"}]}
    name, arguments = ("apply_text_patches", {"patches": [patch]}) if batch else ("apply_text_patch", patch)
    blocked_key = broker._result_fingerprint(name, arguments)
    repeated_calls = {blocked_key: 2}
    record_snapshot(broker, target)
    recovered_key = broker._result_fingerprint(name, arguments)
    assert recovered_key != blocked_key
    assert repeated_calls.get(recovered_key, 0) == 0
    record_snapshot(broker, target)
    assert broker._result_fingerprint(name, arguments) == recovered_key
    assert target.read_text(encoding="utf-8").endswith("return a - b\n")


@pytest.mark.parametrize("snapshot_kind", ["unrelated", "failed", "wrong_hash"])
def test_unrelated_or_unverified_snapshot_does_not_reset_patch_repetition(variant, runner, snapshot_kind):
    broker = receipts.begin(runner, "Edit calculator.py to fix addition.")
    target = runner.workspace / "calculator.py"
    target.write_text("a = 1\n", encoding="utf-8")
    arguments = {"path": "calculator.py", "expected_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                 "replacements": [{"old_text": "1", "new_text": "2"}]}
    before = broker._result_fingerprint("apply_text_patch", arguments)
    if snapshot_kind == "unrelated":
        other = runner.workspace / "notes.txt"
        other.write_text("notes\n", encoding="utf-8")
        record_snapshot(broker, other)
    else:
        record_snapshot(broker, target, error=snapshot_kind == "failed",
                        checksum="0" * 64 if snapshot_kind == "wrong_hash" else None)
    assert broker._result_fingerprint("apply_text_patch", arguments) == before


def test_unavailable_diagnostics_do_not_count_as_snapshot_progress(variant, runner):
    broker = receipts.begin(runner, "Edit calculator.py to fix addition.")
    arguments = {"path": "calculator.py", "expected_sha256": "a" * 64, "replacements": []}
    before = broker._result_fingerprint("apply_text_patch", arguments)
    broker.record_result("codeintel_diagnostics", {"path": "calculator.py"},
                         receipts.result({"available": False, "diagnostics": []}))
    assert broker._result_fingerprint("apply_text_patch", arguments) == before
