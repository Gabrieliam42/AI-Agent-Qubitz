"""General omitted-call preservation and workflow-label regressions; no inference."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation.disposable_followup_matrix import workflow_quality
from runtime.default_behavior import compare

variant = receipts.variant
runner = receipts.runner


def test_existing_optional_argument_is_omitted_from_preserved_domain():
    before = "def compute(values, adjustment=0):\n return sum(values) - adjustment\n"
    after = ("def compute(values, adjustment=0):\n"
             " if not 0 <= adjustment <= 1:\n  raise ValueError('outside range')\n"
             " subtotal = sum(values)\n"
             " return subtotal if adjustment == 0 else subtotal * adjustment\n")
    result = compare(before, after)
    assert result["status"] == "passed_bounded_comparison", result
    assert all(case["omitted_parameters"] == ["adjustment"] for case in result["cases"])
    assert any(case["before"] == ("int", "9007199254740993") for case in result["cases"])


@pytest.mark.parametrize("body", ["return sum(values) * 1.0", "return sum(values) + 1"])
def test_omitted_type_and_value_regressions_still_fail(body):
    before = "def compute(values, adjustment=0):\n return sum(values) - adjustment\n"
    after = "def compute(values, adjustment=0):\n " + body + "\n"
    result = compare(before, after)
    assert result["status"] == "failed"
    assert any(not case["matched"] for case in result["cases"])


def test_changed_existing_default_is_checked_by_actual_omission():
    before = "def compute(value, factor=2):\n return value * factor\n"
    after = "def compute(value, factor=3):\n return value * factor\n"
    assert compare(before, after)["status"] == "failed"


def test_all_optional_call_and_two_required_sequence_calls_are_supported():
    assert compare("def f(value=2):\n return value\n", "def f(value=2, extra=0):\n return value\n")["status"] == "passed_bounded_comparison"
    result = compare("def combine(a,b):\n return sum(a)+sum(b)\n",
                     "def combine(a,b,extra=0):\n return sum(a)+sum(b)\n")
    assert result["status"] == "passed_bounded_comparison", result
    assert len(result["cases"]) == 48
    assert any(case["before"][0] != "exception" for case in result["cases"])


def test_each_changed_function_needs_nonexception_default_evidence():
    before = "def f(x):\n return x\ndef g(x):\n raise ValueError('same')\n"
    after = "def f(x,extra=0):\n return x\ndef g(x,extra=0):\n raise ValueError('same')\n"
    assert compare(before, after)["status"] == "unavailable"


@pytest.mark.parametrize("after", [
    "def f(x,required):\n return x\n",
    "def f(x,*,optional=0):\n return x\n",
    "import os\ndef f(x,optional=0):\n return x\n",
])
def test_unsupported_preservation_is_not_accepted(after):
    assert compare("def f(x):\n return x\n", after)["status"] == "unavailable"


def test_wrapper_candidate_preserves_existing_optional_defaults_without_writes(variant, runner):
    target = runner.workspace / "compute.py"
    before = "def compute(values, adjustment=0):\n return sum(values) - adjustment\n"
    after = ("def compute(values, adjustment=0):\n"
             " if not 0 <= adjustment <= 1:\n  raise ValueError('outside range')\n"
             " return sum(values) if adjustment == 0 else sum(values) * adjustment\n")
    target.write_text(before)
    prompt = "Change the explicit adjustment operation in compute.py. Preserve omitted-adjustment behavior and types."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    assert not variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): after.encode()})
    assert broker._default_behavior_receipts[str(target)]["status"] == "passed_bounded_comparison"
    assert target.read_text() == before


@pytest.mark.parametrize("label,unverified", [
    ("[Behavior status: unverified]", True),
    ("[Completion status: partial] Unverified postconditions: changed files.", True),
    ("[Completion status: unverified]", True),
    ("[Completion status: verified]", False),
    ("Discuss the phrase [Completion status: partial] without making a status report.", False),
])
def test_workflow_status_labels_do_not_change_task_oracle(label, unverified):
    observed = {"assistant": label, "tools": []}
    quality = workflow_quality(observed, SimpleNamespace(checks=[]))
    assert quality["behavior_unverified"] is unverified
    assert ("code_behavior_not_runtime_verified" in quality["review_reasons"]) is unverified
    assert observed == {"assistant": label, "tools": []}


def test_harness_batch_example_matches_array_contract():
    text = (Path(__file__).resolve().parents[1] / "HARNESS.txt").read_text(encoding="utf-8")
    example = text.split("Example - correct two-file edit:", 1)[1]
    body = example.split("3. apply_text_patches(", 1)[1].split("\n4. run_existing_entrypoint", 1)[0]
    payload = json.loads(body.strip().removesuffix(")"))
    assert set(payload) == {"patches"}
    assert len(payload["patches"]) == 2
    for patch in payload["patches"]:
        assert set(patch) == {"path", "replacements"}
        assert set(patch["replacements"][0]) == {"old_text", "new_text"}
