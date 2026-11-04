"""Literal completion remains exact when topic labels/protected clauses are present."""
from __future__ import annotations

import pytest

from evaluation import test_creation_recovery_provenance as creation
from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("label", ["New task: ", "Separate request: ", "Unrelated task: ", ""])
def test_verified_literal_creation_ends_without_another_model_step(variant, runner, label):
    protected = runner.workspace / "state.txt"
    protected.write_text("preserved\n")
    target = runner.workspace / "result.txt"
    prompt = label + "Create result.txt containing exactly done followed by a newline. Do not edit state.txt."
    broker = receipts.begin(runner, prompt)
    completed = []
    broker.completion_callback = lambda: completed.append(True)
    broker.completion_interrupt_enabled = True
    creation.record_creation(broker, target, "done\n")
    assert not broker.completion_report(prompt)[1]
    assert variant._artifact_completion_can_end_turn(broker)
    assert broker.verified_completion_requested
    assert completed == [True]
    assert protected.read_text() == "preserved\n"


@pytest.mark.parametrize("prompt", [
    "Only if approval is granted: create result.txt containing exactly done followed by a newline.",
    "Create result.txt containing exactly done followed by a newline, then run check_tests.py.",
    "Create result.txt containing exactly done followed by a newline and explain the design.",
    "Create result.txt containing exactly done followed by a newline. Do not create result.txt.",
    "Create scene.html with a realistic interactive simulation and verify its behavior.",
    "Create scene.html with a realistic interactive simulation. Do not edit state.txt.",
])
def test_literal_shortcut_does_not_drop_conditions_or_other_obligations(variant, runner, prompt):
    broker = receipts.begin(runner, prompt)
    broker.creation_targets.add(str(runner.workspace / ("scene.html" if "scene.html" in prompt else "result.txt")))
    assert not variant._artifact_completion_can_end_turn(broker)


def test_wrong_literal_bytes_do_not_request_completion(variant, runner):
    prompt = "New task: create result.txt containing exactly done followed by a newline. Do not edit state.txt."
    (runner.workspace / "state.txt").write_text("preserved\n")
    broker = receipts.begin(runner, prompt)
    completed = []
    broker.completion_callback = lambda: completed.append(True)
    broker.completion_interrupt_enabled = True
    creation.record_creation(broker, runner.workspace / "result.txt", "wrong\n")
    assert broker.completion_report(prompt)[1]
    assert not broker.verified_completion_requested
    assert completed == []
