"""CPU-only artifact-receipt versus task-completion regressions, public variants only."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime.source import read_variant_source

variant = receipts.variant
runner = receipts.runner


def record_write(broker, path, content, validation=None):
    path.write_text(content, encoding="utf-8")
    broker.record_result("write_file", {"path": str(path), "content": content}, receipts.result({
        "path": str(path), "created": True,
        "new_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "artifact_validation": validation,
    }))


@pytest.mark.parametrize("named", [False, True])
@pytest.mark.parametrize("content", ["probe\n", "placeholder\n", "<!doctype html><html><body>",
                                     "<!doctype html><html><body><p>Loading</p></body></html>"])
def test_complex_creation_never_interrupts_on_file_receipt(variant, runner, named, content):
    prompt = ("Create " + ("solar_system.html" if named else "an HTML file")
              + " with an interactive 3D solar scene, scientific positions and working camera controls.")
    broker = receipts.begin(runner, prompt)
    callbacks = []
    broker.completion_interrupt_enabled = True
    broker.completion_callback = lambda: callbacks.append("interrupted")
    record_write(broker, runner.workspace / "solar_system.html", content,
                 {"browser_load": "passed", "interaction_status": "passed"})
    assert not broker.completion_report(prompt)[1]  # File evidence remains truthful.
    assert not variant._artifact_completion_can_end_turn(broker)
    assert not broker.verified_completion_requested
    assert callbacks == []


def test_small_code_creation_is_not_verified_by_probe_bytes(variant, runner):
    prompt = "Create calculator.py as a basic calculator."
    broker = receipts.begin(runner, prompt)
    broker.completion_interrupt_enabled = True
    record_write(broker, runner.workspace / "calculator.py", "# placeholder\n")
    assert not variant._artifact_completion_can_end_turn(broker)
    assert not broker.verified_completion_requested


def test_exact_literal_creation_keeps_verified_shortcut(variant, runner):
    prompt = "Create note.txt containing exactly ready followed by a newline."
    broker = receipts.begin(runner, prompt)
    callbacks = []
    broker.completion_interrupt_enabled = True
    broker.completion_callback = lambda: callbacks.append("verified")
    record_write(broker, runner.workspace / "note.txt", "wrong\n")
    assert not broker.verified_completion_requested
    assert "saved_file_contract" in broker.completion_report(prompt)[1]
    record_write(broker, runner.workspace / "note.txt", "ready\n")
    assert variant._artifact_completion_can_end_turn(broker)
    assert broker.verified_completion_requested
    assert callbacks == ["verified"]


def test_html_repair_remains_model_driven(variant, runner):
    prompt = "Edit scene.html to implement working camera controls."
    path = runner.workspace / "scene.html"
    path.write_text("old\n")
    broker = receipts.begin(runner, prompt)
    broker.completion_interrupt_enabled = True
    record_write(broker, path, "<html><body>Loading</body></html>")
    assert not variant._artifact_completion_can_end_turn(broker)
    assert not broker.verified_completion_requested


@pytest.mark.parametrize("suffix", [".txt", ".html"])
def test_simple_copy_keeps_receipt_completion(variant, runner, suffix):
    source = runner.workspace / ("source" + suffix)
    source.write_text("ready\n")
    broker = receipts.begin(runner, f"Copy source{suffix} to copied{suffix}.")
    broker.completion_interrupt_enabled = True
    destination = runner.workspace / ("copied" + suffix)
    destination.write_bytes(source.read_bytes())
    broker.record_result("copy_path", {"source": str(source), "destination": str(destination)},
                         receipts.result({"status": "copied"}))
    assert variant._artifact_completion_can_end_turn(broker)
    assert broker.verified_completion_requested


def test_unchecked_literal_format_does_not_get_shortcut(variant, runner):
    broker = receipts.begin(runner, "Create script.py containing exactly ready followed by a newline.")
    broker.completion_interrupt_enabled = True
    record_write(broker, runner.workspace / "script.py", "wrong\n")
    assert not variant._artifact_completion_can_end_turn(broker)
    assert not broker.verified_completion_requested


def actual_completion_answer(variant, runner, prompt):
    # Execute the real nested function without invoking retrieval or a model.
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    function = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "verified_completion_answer")
    namespace = dict(vars(variant), self=runner, base=variant._APP.base, prompt=prompt,
                     check_user_cancellation=lambda: None)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "<actual completion>", "exec"), namespace)  # noqa: S102 - trusted local source
    return namespace["verified_completion_answer"]


def test_synthetic_answer_obeys_same_boundary_even_with_stale_signal(variant, runner):
    prompt = "Create scene.html with interactive camera controls."
    broker = receipts.begin(runner, prompt)
    record_write(broker, runner.workspace / "scene.html", "probe\n")
    answer = actual_completion_answer(variant, runner, prompt)
    for signalled in (False, True):
        broker.verified_completion_requested = signalled
        runner._cancel_source = "wrapper_verified_completion" if signalled else ""
        assert answer() is None


def test_simple_literal_answer_remains_verified(variant, runner):
    prompt = "Create note.txt containing exactly ready followed by a newline."
    broker = receipts.begin(runner, prompt)
    record_write(broker, runner.workspace / "note.txt", "ready\n")
    runner._cancel_source = ""
    assert "[Completion status: verified]" in actual_completion_answer(variant, runner, prompt)()
