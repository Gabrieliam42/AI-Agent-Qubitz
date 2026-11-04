"""Historical edit answers use verified receipts without loading a model."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


def prior_history():
    return [
        {"role": "user", "content": "Create a.txt and b.txt."},
        {"role": "assistant", "content": "Completed the requested task."},
        {"role": "user", "content": "Append done to each of qfiles."},
        {"role": "assistant", "content": "Completed the requested task."},
    ]


def edit_receipt(workspace, paths=("a.txt", "b.txt")):
    return {"workspace": str(workspace.resolve()), "turn_id": "verified-edit",
            "paths": [str(workspace / name) for name in paths]}


@pytest.mark.parametrize("action", ["edit", "change", "patch", "write", "creation"])
def test_explicit_historical_actions_are_followups(variant, action):
    prompt = f"List the two filenames from your last {action}. Do not modify or execute anything."
    assert variant._classify_turn_relation(prompt, prior_history()).depends_on_prior


def test_workspace_listing_stays_independent(variant):
    assert not variant._classify_turn_relation("List all filenames in the workspace.", prior_history()).depends_on_prior


def test_receipt_retains_mutations_and_excludes_inspection(variant, runner):
    broker = receipts.begin(runner, "Edit a.txt and b.txt, preserving context.txt.")
    for name in ("a.txt", "b.txt", "context.txt"):
        target = runner.workspace / name
        target.write_text("old\n")
        broker.record_result("read_file_snapshot", {"path": name}, receipts.result({
            "status": "text_snapshot", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "start_line": 1, "end_line": 1, "content": "old\n",
        }))
    for name in ("a.txt", "b.txt"):
        target = runner.workspace / name
        target.write_text("old\ndone\n")
        broker.record_result("apply_text_patch", {"path": name}, receipts.result({
            "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
    receipt = variant._retained_verified_edit_receipt(broker)
    assert receipt["turn_id"] == broker.turn_id
    assert {Path(path).name for path in receipt["paths"]} == {"a.txt", "b.txt"}


@pytest.mark.parametrize("status", ["failed", "patch_staged"])
def test_failed_or_staged_results_are_not_edits(variant, runner, status):
    broker = receipts.begin(runner, "Edit a.txt.")
    target = runner.workspace / "a.txt"
    target.write_text("old\n")
    broker.record_result("apply_text_patch", {"path": "a.txt"}, receipts.result({
        "status": status, "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }, error=status == "failed"))
    assert not variant._retained_verified_edit_receipt(broker)


def test_partial_progress_retains_only_verified_changed_files(variant, runner):
    broker = receipts.begin(runner, "Create a.txt and b.txt.")
    target = runner.workspace / "a.txt"
    target.write_text("saved\n")
    broker.record_result("write_file", {"path": "a.txt"}, receipts.result({
        "created": True, "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    broker.record_result("write_file", {"path": "b.txt"}, receipts.result({"status": "failed"}, error=True))
    receipt = variant._retained_verified_edit_receipt(broker)
    answer = variant._verified_edit_receipt_answer(
        runner.workspace, "List the filenames from your last edit.", receipt,
    )
    assert answer == "a.txt"


@pytest.mark.parametrize("directive", [
    "Do not modify or execute anything.", "Do not use any tools.",
    "Do not edit files.", "Without modifying or executing anything.",
    "Without tools.", "Answer with only the filenames.",
])
def test_real_answer_route_uses_retained_names_without_model(variant, runner, monkeypatch, directive):
    runner.history = prior_history()
    runner.history_summary = ""
    runner._last_verified_edit_receipt = edit_receipt(runner.workspace)
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def no_model(*_args, **_kwargs):
        pytest.fail("Historical filename answer must not load a model")

    monkeypatch.setattr(parent, "_run_async", no_model)
    answer = asyncio.run(runner._run_async("List the two filenames from your last edit. " + directive))
    assert answer == "a.txt, b.txt"
    assert not runner._tool_permission_broker.evidence
    assert runner._last_verified_edit_receipt == edit_receipt(runner.workspace)


def test_empty_history_does_not_invalidate_a_verified_receipt(variant, runner):
    runner.history = []
    runner.history_summary = ""
    runner._last_verified_edit_receipt = edit_receipt(runner.workspace)
    answer = asyncio.run(runner._run_async("List filenames from your last edit. Do not use any tools."))
    assert answer == "a.txt, b.txt"


def test_completed_edit_is_retained_across_an_inspection_turn(variant, runner, monkeypatch):
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        broker = self._tool_permission_broker
        if "context.txt" in prompt:
            target = runner.workspace / "context.txt"
            broker.record_result("read_file_snapshot", {"path": "context.txt"}, receipts.result({
                "status": "text_snapshot", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "start_line": 1, "end_line": 1, "content": "context\n",
            }))
            return "context"
        files = []
        for name, content in (("a.txt", "alpha\n"), ("b.txt", "beta\n")):
            target = runner.workspace / name
            target.write_text(content)
            files.append({"path": name, "created": True,
                          "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        broker.record_result("write_files", {"files": [
            {"path": "a.txt", "content": "alpha\n"}, {"path": "b.txt", "content": "beta\n"},
        ]}, receipts.result({"files": files}))
        return "Created the requested files."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    asyncio.run(runner._run_async("Create a.txt containing exactly alpha followed by a newline and b.txt containing exactly beta followed by a newline."))
    receipt = dict(runner._last_verified_edit_receipt)
    assert {Path(path).name for path in receipt["paths"]} == {"a.txt", "b.txt"}
    (runner.workspace / "context.txt").write_text("context\n")
    asyncio.run(runner._run_async("Read context.txt; do not modify anything."))
    assert runner._last_verified_edit_receipt == receipt
    answer = asyncio.run(runner._run_async("List the two filenames from your last edit. Do not use any tools."))
    assert answer == "a.txt, b.txt"


@pytest.mark.parametrize("receipt", [{}, {"workspace": "/different", "turn_id": "other", "paths": []}])
def test_missing_or_other_workspace_receipt_reports_limitation(variant, runner, receipt):
    answer = variant._verified_edit_receipt_answer(
        runner.workspace, "List the two filenames from your last edit.", receipt,
    )
    assert "No verified filenames" in answer


def test_blocked_new_edit_cannot_reuse_older_edit_names(variant, runner):
    runner._last_verified_edit_receipt = edit_receipt(runner.workspace)
    runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    answer = asyncio.run(runner._run_async("Edit different.txt to contain a replacement."))
    assert "does not allow" in answer
    assert not runner._last_verified_edit_receipt


def test_count_mismatch_does_not_invent_another_filename(variant, runner):
    answer = variant._verified_edit_receipt_answer(
        runner.workspace, "List the two filenames from your last edit.", edit_receipt(runner.workspace, ("a.txt",)),
    )
    assert "1 verified filename" in answer and "a.txt" in answer and "b.txt" not in answer


def test_historical_names_survive_external_deletion(variant, runner):
    assert variant._verified_edit_receipt_answer(
        runner.workspace, "List the two filenames from your last edit.", edit_receipt(runner.workspace),
    ) == "a.txt, b.txt"
    assert not (runner.workspace / "a.txt").exists()


@pytest.mark.parametrize("prompt", [
    "Which filenames from your last edit currently exist?",
    "List filenames from your last edit and execute run_tests.py.",
    "List filenames from your last edit and show their contents.",
    "List filenames from your last edit and tell me how many there are.",
    "List filenames from your last edit. Also describe their contents.",
    "List filenames from your last edit. Do not modify anything and describe their contents.",
    "List filenames from your last edit. Without editing anything, include their hashes.",
    "List filenames from your last edit. Do not use any tools and explain the changes.",
    "Which filenames from your last edit survived?",
    "List the filenames in this workspace.",
])
def test_current_state_and_mixed_actions_do_not_use_historical_fast_path(variant, runner, prompt):
    assert variant._verified_edit_receipt_answer(runner.workspace, prompt, edit_receipt(runner.workspace)) is None


def test_foreign_and_traversal_paths_are_rejected(variant, runner):
    for path in (str(runner.workspace.parent / "other.txt"), str(runner.workspace / ".." / "other.txt")):
        receipt = edit_receipt(runner.workspace)
        receipt["paths"] = [path]
        assert "No verified filenames" in variant._verified_edit_receipt_answer(
            runner.workspace, "List filenames from your last edit.", receipt,
        )


def test_broader_question_receives_receipt_without_becoming_filename_only(variant, runner, monkeypatch):
    runner.history = prior_history()
    runner._last_verified_edit_receipt = edit_receipt(runner.workspace)
    seen = []
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        seen.append(prompt)
        return "Two filenames: a.txt and b.txt."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async("List filenames from your last edit and tell me how many there are. Do not use any tools."))
    assert seen and "Verified historical mutation receipt" in seen[0]
    assert "a.txt" in seen[0] and "b.txt" in seen[0]
    assert "Two filenames" in answer
    assert not runner._tool_permission_broker.evidence


def test_new_action_keeps_prior_edit_context_without_reusing_its_completion(variant, runner, monkeypatch):
    runner.history = prior_history()
    runner._last_verified_edit_receipt = edit_receipt(runner.workspace)
    seen = []
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(self, prompt, callback=None):
        seen.append(prompt)
        target = runner.workspace / "manifest.txt"
        target.write_text("a.txt\nb.txt\n")
        self._tool_permission_broker.record_result("write_file", {"path": "manifest.txt"}, receipts.result({
            "created": True, "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        return "Created manifest.txt."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    asyncio.run(runner._run_async("Create manifest.txt listing the filenames from your last edit."))
    assert seen and "Verified historical mutation receipt" in seen[0]
    assert "a.txt" in seen[0] and "b.txt" in seen[0]
    assert runner._last_verified_edit_receipt["paths"] == [str(runner.workspace / "manifest.txt")]
