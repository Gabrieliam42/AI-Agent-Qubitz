"""CPU regressions for retained edit source and separately versioned explanation checks."""
from __future__ import annotations

import asyncio
import base64
import dataclasses
import hashlib
import json
from pathlib import Path

import pytest
from followup_matrix_cases import (
    Check,
    Turn,
    capture_behavior_baselines,
    compatibility_scope_evidence,
    source_fidelity_evidence,
    verify_turn,
    workspace_manifest,
)

from evaluation import disposable_followup_matrix as matrix
from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner

BEFORE = "def total(prices, discount=0):\n    return sum(prices) - discount\n"
AFTER = (
    "def total(prices, discount=0):\n"
    "    subtotal = sum(prices)\n"
    "    if discount == 0:\n"
    "        return subtotal\n"
    "    return subtotal * (1 - discount / 100)\n"
)
INVENTED = "def total(price, quantity, discount=0):\n    return price * quantity\n"
QUESTION = (
    "Explain how your latest change preserves the previous total behavior when no discount is passed. "
    "Do not modify or execute anything."
)


def source_edit(variant, runner, *, before=BEFORE, after=AFTER):
    broker = receipts.begin(runner, "Edit pricing.py preserving default behavior.")
    target = runner.workspace / "pricing.py"
    target.write_text(before, encoding="utf-8")
    broker.record_result("read_file_snapshot", {"path": "pricing.py"}, receipts.result({
        "status": "text_snapshot", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "start_line": 1, "end_line": len(before.splitlines()), "content": before,
    }))
    target.write_text(after, encoding="utf-8")
    broker.record_result("apply_text_patch", {"path": "pricing.py"}, receipts.result({
        "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    behavior = variant._retained_behavior_evidence(broker, {})
    receipt = variant._retained_verified_edit_receipt(broker, behavior)
    broker.end_turn()
    runner._last_verified_edit_receipt = receipt
    runner._last_actionable_task = {}
    runner.history = [
        {"role": "user", "content": "Extend pricing.py preserving omitted-discount behavior."},
        {"role": "assistant", "content": "Saved pricing.py."},
        {"role": "user", "content": "Rerun run_tests.py and run_report.py."},
        {"role": "assistant", "content": "The existing entrypoints returned exit code 0."},
    ]
    return target, receipt, behavior


def parent_runner(runner):
    classes = type(runner).__mro__
    return classes[next(i for i, cls in enumerate(classes) if cls.__name__ == "HardenedAgentRunner") + 1]


def test_source_retention_reuses_captured_bytes_without_reading_again(variant, runner, monkeypatch):
    target, receipt, behavior = source_edit(variant, runner)
    assert receipt["source_evidence"][str(target)]["before_text"] == BEFORE
    assert receipt["source_evidence"][str(target)]["after_text"] == AFTER
    behavior["sources"][str(target)]["after_text"] = "mutated caller dictionary"
    assert receipt["source_evidence"][str(target)]["after_text"] == AFTER
    original = Path.read_bytes

    def no_source_read(path):
        if path == target:
            pytest.fail("Explanation must use immutable captured source, not reread the file")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", no_source_read)
    assert variant._historical_edit_source_evidence(runner.workspace, receipt)["sources"]


@pytest.mark.parametrize("external_change", ["delete", "replace"])
def test_explanation_survives_execution_turn_and_external_changes(variant, runner, monkeypatch, external_change):
    target, receipt, _ = source_edit(variant, runner)
    if external_change == "delete":
        target.unlink()
    else:
        target.write_text("external state is not the captured version\n")

    async def no_model(*_args, **_kwargs):
        pytest.fail("Captured compatibility explanation must not load a model or tools")

    monkeypatch.setattr(parent_runner(runner), "_run_async", no_model)
    answer = asyncio.run(runner._run_async(QUESTION))
    assert BEFORE.strip() in answer and AFTER.strip() in answer
    assert "historical" in answer and "compatibility is not established" in answer
    assert INVENTED.strip() not in answer
    assert not runner._tool_permission_broker.evidence
    assert runner._last_verified_edit_receipt == receipt
    assert "current-state proof" in answer


def test_no_source_receipt_returns_limitation_without_invented_function(variant, runner, monkeypatch):
    target, receipt, _ = source_edit(variant, runner)
    receipt.pop("source_evidence")

    async def no_model(*_args, **_kwargs):
        pytest.fail("Missing edit source must be an explicit limitation, not a speculative model answer")

    monkeypatch.setattr(parent_runner(runner), "_run_async", no_model)
    answer = asyncio.run(runner._run_async(QUESTION))
    assert "No verified source snapshot" in answer and "def total" not in answer
    assert target.read_text() == AFTER and not runner._tool_permission_broker.evidence


def test_retained_source_does_not_depend_on_conversation_history(variant, runner, monkeypatch):
    source_edit(variant, runner)
    runner.history = []
    runner.history_summary = ""

    async def no_model(*_args, **_kwargs):
        pytest.fail("A retained source receipt must remain usable without conversation text")

    monkeypatch.setattr(parent_runner(runner), "_run_async", no_model)
    answer = asyncio.run(runner._run_async(QUESTION))
    assert BEFORE.strip() in answer and AFTER.strip() in answer
    assert not runner._tool_permission_broker.evidence


@pytest.mark.parametrize("corruption", ["workspace", "turn", "hash", "text", "unbounded", "foreign_path"])
def test_invalid_edit_source_is_not_authoritative(variant, runner, corruption):
    target, receipt, _ = source_edit(variant, runner)
    if corruption == "workspace":
        receipt["workspace"] = str(runner.workspace.parent)
    elif corruption == "foreign_path":
        item = receipt["source_evidence"].pop(str(target))
        receipt["source_evidence"][str(runner.workspace.parent / "pricing.py")] = item
    else:
        item = receipt["source_evidence"][str(target)]
        key, value = {
            "turn": ("turn_id", "other-turn"), "hash": ("after_sha256", "0" * 64),
            "text": ("after_text", INVENTED), "unbounded": ("after_text", "x" * 1601),
        }[corruption]
        item[key] = value
    assert not variant._historical_edit_source_evidence(runner.workspace, receipt)


def test_truncated_snapshot_stays_labeled_not_complete(variant, runner, monkeypatch):
    after = AFTER + "# padding\n" * 200
    target, receipt, _ = source_edit(variant, runner, after=after)
    captured = receipt["source_evidence"][str(target)]
    assert len(captured["after_text"]) == 1600 and captured["after_complete"] is False

    async def no_model(*_args, **_kwargs):
        pytest.fail("Bounded captured evidence must not be expanded through a model or file read")

    monkeypatch.setattr(parent_runner(runner), "_run_async", no_model)
    answer = asyncio.run(runner._run_async(QUESTION))
    assert "after source (truncated)" in answer and "current-state proof" in answer


def test_general_edit_explanation_receives_validated_source_context(variant, runner, monkeypatch):
    target, _receipt, _ = source_edit(variant, runner)
    seen = []

    async def model_pass(_self, prompt, callback=None):
        seen.append(prompt)
        return "The captured edit added a default-discount branch; current state was not inspected."

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    asyncio.run(runner._run_async("Describe your latest edit. Do not use any tools."))
    assert seen and "source_evidence" in seen[0] and "subtotal = sum(prices)" in seen[0]
    assert str(target) in seen[0] and "not current file state" in seen[0]
    assert not runner._tool_permission_broker.evidence


def test_filename_answer_stays_model_free_after_source_retention(variant, runner, monkeypatch):
    source_edit(variant, runner)

    async def no_model(*_args, **_kwargs):
        pytest.fail("Filename-only answer must keep its model-free path")

    monkeypatch.setattr(parent_runner(runner), "_run_async", no_model)
    assert asyncio.run(runner._run_async("List the filename from your last edit. Do not use any tools.")) == "pricing.py"


def test_unrelated_request_does_not_receive_historical_source(variant, runner, monkeypatch):
    source_edit(variant, runner)
    seen = []

    async def model_pass(_self, prompt, callback=None):
        seen.append(prompt)
        return "81"

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    asyncio.run(runner._run_async("Unrelated question: what is nine times nine?"))
    assert seen and "source_evidence" not in seen[0] and "def total" not in seen[0]


def test_disclaimer_does_not_clear_invented_definition():
    answer = "The correct implementation is:\n\n```python\n" + INVENTED + "```\n\nSaved code remains unverified."
    assert compatibility_scope_evidence(answer)["limitation_present"]
    assert source_fidelity_evidence(answer, AFTER, "total")["issues"] == ["quoted_definition_differs:total"]


@pytest.mark.parametrize("label", ["Hypothetical teaching example:", "Illustrative example only:",
                                  "Counterexample, not the saved implementation:"])
def test_explicit_hypothetical_is_not_an_actual_source_claim(label):
    answer = label + "\n\n```python\n" + INVENTED + "```"
    fidelity = source_fidelity_evidence(answer, AFTER, "total")
    assert not fidelity["issues"] and fidelity["hypothetical_blocks"] == 1


def test_hypothetical_label_cannot_clear_a_later_actual_quotation():
    answer = ("Hypothetical teaching example:\n\n```python\n" + INVENTED + "```\n\n"
              "The saved implementation is:\n\n```python\n" + INVENTED + "```")
    result = source_fidelity_evidence(answer, AFTER, "total")
    assert result["hypothetical_blocks"] == 1 and result["issues"]


def test_correct_source_ignores_formatting_and_comments():
    answer = "After source:\n\n```python\n" + AFTER.replace("    ", "  ") + "# comment\n```"
    assert not source_fidelity_evidence(answer, AFTER, "total")["issues"]


def test_before_quotation_requires_independent_prior_snapshot():
    answer = "Before source:\n\n```python\n" + BEFORE + "```"
    assert source_fidelity_evidence(answer, AFTER, "total")["issues"]
    assert not source_fidelity_evidence(answer, AFTER, "total", previous_sources=(BEFORE,))["issues"]


def test_no_code_is_not_universal_prose_verification():
    evidence = source_fidelity_evidence("I cannot verify the saved source in this turn.", AFTER, "total")
    assert evidence["checked_definitions"] == 0 and not evidence["issues"]
    assert "no proof of prose accuracy" in evidence["scope"]


@pytest.mark.parametrize("code,issue", [("def total(price", "unparseable_quoted_definition:total"),
                                       ("def invented():\n    return 1\n", "unknown_quoted_definition:invented")])
def test_malformed_or_unknown_quoted_definition(code, issue):
    answer = "Saved implementation:\n\n```python\n" + code + "\n```"
    assert source_fidelity_evidence(answer, AFTER, "total")["issues"] == [issue]


def test_v4_captures_source_independently_and_rejects_wrong_example(tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text(AFTER)
    turn = Turn("Explain the latest implementation without tools.", (Check("source_fidelity", "pricing.py", "total"),))
    baseline = capture_behavior_baselines(tmp_path, turn)
    manifest = workspace_manifest(tmp_path)
    answer = "Saved implementation:\n\n```python\n" + INVENTED + "```\n\nSaved code is unverified."
    issues, evidence = verify_turn(tmp_path, turn, {"assistant": answer}, manifest, manifest,
                                   behavior_baselines=baseline)
    assert issues and evidence[0]["baseline_origin"] == "evaluator_snapshot"
    assert evidence[0]["issues"] == ["quoted_definition_differs:total"]
    assert not (tmp_path / "backup").exists()


def test_v4_prior_snapshot_survives_deleted_backup_and_is_hash_checked(tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text(AFTER)
    prior = {"sha256": hashlib.sha256(BEFORE.encode()).hexdigest(),
             "source_base64": base64.b64encode(BEFORE.encode()).decode()}
    turn = Turn("Explain the before source.", (Check("source_fidelity", "pricing.py", "total"),))
    baseline = capture_behavior_baselines(tmp_path, turn, prior_baselines={"pricing.py": [prior]})
    manifest = workspace_manifest(tmp_path)
    answer = "Before source:\n\n```python\n" + BEFORE + "```"
    assert not verify_turn(tmp_path, turn, {"assistant": answer}, manifest, manifest,
                           behavior_baselines=baseline)[0]
    baseline["pricing.py"]["prior_sources"][0]["sha256"] = "0" * 64
    assert verify_turn(tmp_path, turn, {"assistant": answer}, manifest, manifest,
                       behavior_baselines=baseline)[0]


def test_versioned_suite_keeps_historical_contracts_byte_equal():
    expected = {
        "original-v1": "4794be6452401a49858547698eb1b59999334d99581b06c3a47ecba22b44dd3f",
        "expanded-v2": "8d2bb3fe0a3dc0a5325ca5bd28b086534ee2ba12faf59f783f101860e9df6615",
        "expanded-v3": "dec655e8d6b52f3e1cfdac16f53ed75c8c8a383d84ecf5f5f22d00e632059137",
    }
    for suite, digest in expected.items():
        legacy = [dataclasses.asdict(case) for case in matrix.selected_scenarios(None, suite)]
        for case in legacy:
            for turn in case["turns"]:
                # Later opt-in controls are not part of the historical schema.
                assert turn.pop("cancel_on_generation") is False
                assert turn.pop("structured_error") is False
        encoded = json.dumps(legacy, sort_keys=True)
        assert hashlib.sha256(encoded.encode()).hexdigest() == digest
    old = matrix.selected_scenarios(None, "expanded-v3")
    new = matrix.selected_scenarios(None, "expanded-v4")
    assert len(new) == 26 and sum(len(case.turns) for case in new) == 85
    assert sum(a != b for a, b in zip(old, new, strict=True)) == 1
    case = next(case for case in new if case.name == "multi_module_requirement_revision")
    assert {check.kind for check in case.turns[3].checks} >= {"source_fidelity", "compatibility_scope"}
