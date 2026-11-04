"""Model-free current-turn provenance and terminal recovery obligations."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


def record_creation(broker, path, content):
    path.write_text(content, encoding="utf-8")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    broker.record_result("write_file", {"path": str(path), "content": content}, receipts.result({
        "path": str(path), "created": True, "new_sha256": checksum,
    }))
    return checksum


def rejected_patch(broker, path):
    arguments = {"path": str(path), "replacements": [{"old_text": "old", "new_text": "new"}]}
    allowed, denial = broker.authorize("apply_text_patch", arguments)
    assert not allowed and denial["status"] == "transaction_required"
    broker.record_result("apply_text_patch", arguments, receipts.result(denial, error=True))
    assert broker.transaction_recovery_required


def parent_runner(runner):
    classes = type(runner).__mro__
    index = next(i for i, cls in enumerate(classes) if cls.__name__ == "HardenedAgentRunner")
    return classes[index + 1]


@pytest.mark.parametrize("name,content,prompt", [
    ("report.py", "print('old')\n", "Create report.py that prints a status message."),
    ("guide.md", "# Setup\n\nRun the application.\n", "Create guide.md with setup instructions."),
    ("settings.json", '{"mode":"old"}\n', "Create settings.json with application defaults."),
])
def test_satisfied_creation_does_not_force_a_patch_recovery(
    variant, runner, monkeypatch, name, content, prompt,
):
    calls = receipts.install_host(variant, runner, monkeypatch)
    passes = []
    target = runner.workspace / name

    async def model_pass(self, _prompt, callback=None):
        passes.append(_prompt)
        if len(passes) == 1:
            broker = self._tool_permission_broker
            record_creation(broker, target, content)
            rejected_patch(broker, target)
            assert not broker.completion_report(prompt)[1]
        return "Saved the requested file; behavior was not independently tested."

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(prompt))
    assert len(passes) == 1
    assert target.read_text() == content
    assert "Saved the requested file" in answer
    assert not calls


def test_recovery_context_preserves_current_turn_creation_provenance(runner):
    broker = receipts.begin(runner, "Create report.py that prints a status message.")
    target = runner.workspace / "report.py"
    checksum = record_creation(broker, target, "print('old')\n")
    rejected_patch(broker, target)
    context = broker.transaction_recovery_context()
    assert "created in the current turn" in context.lower()
    assert "report.py" in context and checksum in context
    assert "not pre-existing" in context.lower()
    assert "behavior" in context.lower() and "security" in context.lower()
    assert not broker.transaction_snapshot_hashes
    assert not broker.transaction_changed_hashes


def test_partial_creation_preserves_provenance_and_pending_write(runner):
    broker = receipts.begin(runner, "Create guide.md and settings.json with setup defaults.")
    assert broker.creation_targets == {str(runner.workspace / name) for name in ("guide.md", "settings.json")}
    checksum = record_creation(broker, runner.workspace / "guide.md", "# Setup\n")
    context = broker.transaction_recovery_context()
    assert "created in the current turn" in context.lower()
    assert checksum in context
    assert "settings.json" in context and "write_file" in context
    assert "do not request snapshots" in context.lower()
    assert "created_outputs" in broker.completion_report(broker.current_prompt)[1]


@pytest.mark.parametrize("state", ["modified", "deleted", "new_turn", "pre_existing"])
def test_context_never_borrows_verified_creation_from_invalid_state(runner, state):
    prompt = "Create report.py that prints a status message."
    target = runner.workspace / "report.py"
    if state == "pre_existing":
        target.write_text("print('old')\n")
        broker = receipts.begin(runner, "Edit report.py to print a new status message.")
    else:
        broker = receipts.begin(runner, prompt)
        record_creation(broker, target, "print('old')\n")
        if state == "modified":
            target.write_text("print('external edit')\n")
        elif state == "deleted":
            target.unlink()
        else:
            broker.begin_turn("Edit report.py to print a new status message.", None)
    assert "created in the current turn" not in broker.transaction_recovery_context().lower()


def test_existing_edit_still_runs_pending_recovery(variant, runner, monkeypatch):
    target = runner.workspace / "status.txt"
    target.write_text("old\n")
    prompt = "Edit status.txt to replace old with new."
    calls = receipts.install_host(variant, runner, monkeypatch)
    passes = []

    async def model_pass(self, _prompt, callback=None):
        passes.append(_prompt)
        broker = self._tool_permission_broker
        if len(passes) == 1:
            # Exact-target preflight can provide a snapshot before this model pass.
            broker.transaction_snapshot_hashes.clear()
            rejected_patch(broker, target)
        else:
            assert len(passes) == 2
            checksum = hashlib.sha256(target.read_bytes()).hexdigest()
            broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
                "status": "text_snapshot", "sha256": checksum, "content": target.read_text(),
                "start_line": 1, "end_line": 1,
            }))
            arguments = {"path": str(target), "replacements": [{"old_text": "old", "new_text": "new"}]}
            assert broker.authorize("apply_text_patch", arguments)[0]
            target.write_text("new\n")
            broker.record_result("apply_text_patch", arguments, receipts.result({
                "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }))
        return "Saved the requested change."

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    asyncio.run(runner._run_async(prompt))
    assert len(passes) == 2
    assert target.read_text() == "new\n"
    assert calls and all(name == "read_file_snapshot" and arguments["path"] == str(target)
                         for name, arguments in calls)


def test_provenance_does_not_grant_overwrite_or_snapshot_permission(runner):
    broker = receipts.begin(runner, "Create report.py that prints a status message.")
    target = runner.workspace / "report.py"
    record_creation(broker, target, "print('old')\n")
    broker.transaction_recovery_context()
    assert not broker.authorize("write_file", {"path": str(target), "content": "replacement"})[0]
    rejected_patch(broker, target)
    assert not broker.transaction_snapshot_hashes


def test_created_file_does_not_clear_requested_verification(runner):
    (runner.workspace / "check_tests.py").write_text("raise SystemExit(0)\n")
    prompt = "Create report.py that prints a status message, then run check_tests.py."
    broker = receipts.begin(runner, prompt)
    checksum = record_creation(broker, runner.workspace / "report.py", "print('old')\n")
    context = broker.transaction_recovery_context()
    assert "created in the current turn" in context.lower() and checksum in context
    assert "tests" in broker.completion_report(prompt)[1]
    assert "tests" in context
    assert "remaining" in context.lower()
