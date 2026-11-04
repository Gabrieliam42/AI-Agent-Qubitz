"""Saved Python definition scope checks; no inference or arbitrary source execution."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner
PROMPT = "Add divide(a, b) to calculator.py, raising ValueError for a zero divisor. Preserve add."
ORIGINAL = "def add(a, b):\n    return a + b\n"
NESTED = ORIGINAL + "\n    def divide(a, b):\n        return a / b\n"
CORRECT = ORIGINAL + "\ndef divide(a, b):\n    if b == 0:\n        raise ValueError('zero divisor')\n    return a / b\n"


def record_edit(broker, target):
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "status": "patched", "path": str(target), "new_sha256": digest,
    }))


def test_observed_nested_definition_is_not_verified_completion(variant, runner, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text(ORIGINAL)
    broker = receipts.begin(runner, PROMPT)
    target.write_text(NESTED)
    record_edit(broker, target)
    assert "saved_file_contract" in broker.completion_report(PROMPT)[1]
    assert any("divide" in issue and "module" in issue for issue in broker._saved_file_contract_issues)
    assert target.read_text() == NESTED


@pytest.mark.parametrize("source", [
    NESTED,
    ORIGINAL + "\nclass Calculator:\n    def divide(self, a, b):\n        return a / b\n",
    ORIGINAL + '\n# def divide(a, b): pass\n',
    ORIGINAL + '\nexample = "def divide(a, b): pass"\n',
    ORIGINAL + "\ndef Divide(a, b):\n    return a / b\n",
    ORIGINAL + "\ndef divider(a, b):\n    return a / b\n",
])
def test_other_scopes_comments_and_similar_names_do_not_satisfy_definition(variant, runner, tmp_path, source):
    target = tmp_path / "calculator.py"
    target.write_text(source)
    broker = receipts.begin(runner, PROMPT)
    assert variant._saved_text_contract_issues(broker, PROMPT, [])


@pytest.mark.parametrize("source", [
    CORRECT,
    CORRECT.replace("def divide", "async def divide"),
    ORIGINAL + "\nif enabled:\n    def divide(a, b):\n        return a / b\n",
    CORRECT + "\nraise RuntimeError('must not execute this file')\n",
])
def test_module_scope_presence_is_structural_not_runtime_proof(variant, runner, tmp_path, source):
    target = tmp_path / "calculator.py"
    target.write_text(source)
    broker = receipts.begin(runner, PROMPT)
    assert variant._saved_text_contract_issues(broker, PROMPT, []) == []
    assert target.read_text() == source


@pytest.mark.parametrize("prompt", [
    "Explain how to add divide(a, b) to calculator.py.",
    "Do not add divide(a, b) to calculator.py.",
    "Hypothetically, add divide(a, b) to calculator.py.",
    'Create note.txt containing "add divide(a, b) to calculator.py".',
    "Add a method divide(a, b) to Calculator in calculator.py.",
    "Add divide(a, b) inside add in calculator.py.",
    "Add a nested function divide(a, b) in calculator.py.",
    "Add divide(a, b) as a callback in calculator.py.",
    "Add Calculator.divide(a, b) to calculator.py.",
    "Add divide(a, b) to Calculator in calculator.py.",
    "Add division support to calculator.py.",
    "Change divide(a, b) in calculator.py to return a / b.",
])
def test_ambiguous_non_definition_and_non_action_requests_do_not_reread(
    variant, runner, tmp_path, monkeypatch, prompt,
):
    (tmp_path / "calculator.py").write_text(NESTED)
    broker = receipts.begin(runner, prompt)
    monkeypatch.setattr(Path, "read_bytes", lambda *_args: pytest.fail("Out-of-scope definition scan"))
    assert variant._saved_text_contract_issues(broker, prompt, []) == []


def test_followup_binds_only_one_current_authorized_python_target(variant, runner, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text(NESTED)
    (tmp_path / "other.py").write_text(CORRECT)
    prompt = "Add divide(a, b) to it, raising ValueError for a zero divisor. Preserve add."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    assert variant._saved_text_contract_issues(broker, prompt, [])
    broker.transaction_explicit_file_targets.add(str(tmp_path / "other.py"))
    assert variant._saved_text_contract_issues(broker, prompt, []) == []


def test_separate_clauses_bind_their_own_definitions(variant, runner, tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("def beta(): pass\n")
    second.write_text("def alpha(): pass\n")
    prompt = "Add alpha() to first.py. Add beta() to second.py."
    broker = receipts.begin(runner, prompt)
    issues = variant._saved_text_contract_issues(broker, prompt, [])
    assert any("first.py" in issue and "alpha" in issue for issue in issues)
    assert any("second.py" in issue and "beta" in issue for issue in issues)


def test_missing_and_invalid_python_are_contract_issues(variant, runner, tmp_path):
    broker = receipts.begin(runner, PROMPT)
    broker.transaction_explicit_file_targets.add(str(tmp_path / "calculator.py"))
    assert variant._saved_text_contract_issues(broker, PROMPT, [])
    (tmp_path / "calculator.py").write_text("def divide(:\n")
    assert variant._saved_text_contract_issues(broker, PROMPT, [])


def test_explicit_definition_is_size_bounded_before_read(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "calculator.py"
    target.write_text(CORRECT)
    broker = receipts.begin(runner, PROMPT)
    monkeypatch.setitem(variant._saved_text_contract_issues.__globals__, "TRANSACTIONAL_PATCH_MAX_BYTES", 4)
    monkeypatch.setattr(Path, "read_bytes", lambda *_args: pytest.fail("Oversized source was read"))
    assert variant._saved_text_contract_issues(broker, PROMPT, [])


def test_followup_does_not_read_target_replaced_with_external_symlink(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "calculator.py"
    target.write_text(NESTED)
    prompt = "Add divide(a, b) to it."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    outside = tmp_path.parent / (tmp_path.name + "_external.py")
    outside.write_text(CORRECT)
    target.unlink()
    target.symlink_to(outside)
    monkeypatch.setattr(Path, "read_bytes", lambda *_args: pytest.fail("Escaped source was read"))
    assert variant._saved_text_contract_issues(broker, prompt, [])


def test_bounded_correction_uses_fresh_snapshot_and_cannot_expand_scope(
    variant, runner, tmp_path, monkeypatch,
):
    target = tmp_path / "calculator.py"
    target.write_text(ORIGINAL)
    other = tmp_path / "other.py"
    other.write_text("untouched = True\n")
    broker = receipts.begin(runner, PROMPT)
    target.write_text(NESTED)
    record_edit(broker, target)
    assert "saved_file_contract" in broker.completion_report(PROMPT)[1]
    calls = receipts.install_host(variant, runner, monkeypatch)
    context = asyncio.run(runner._wrapper_saved_contract_snapshots())
    assert NESTED.strip() in context
    assert [name for name, _args in calls] == ["read_file_snapshot"]
    broker._saved_contract_correction_active = True
    arguments = {"path": str(target), "replacements": [{"old_text": NESTED, "new_text": CORRECT}]}
    allowed, notice = broker.authorize("apply_text_patch", arguments)
    assert allowed, notice
    assert arguments["expected_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    allowed, _ = broker.authorize("apply_text_patch", {**arguments, "path": str(other)})
    assert not allowed
    target.write_text(CORRECT)
    record_edit(broker, target)
    assert "saved_file_contract" not in broker.completion_report(PROMPT)[1]
    allowed, _ = broker.authorize("apply_text_patch", arguments)
    assert not allowed
    assert other.read_text() == "untouched = True\n"


