"""Created HTML review scope and DOM-discovered graphical previews; no model loads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess

import pytest

from evaluation import test_creation_completion_boundary as boundary
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_html_browser_diagnostics as diagnostics

variant = receipts.variant
runner = receipts.runner
PROMPT = "Create an HTML page with an interactive 3D scene and working speed controls."
HTML = "<html><script>const speed = 1;</script></html>\n"


def start_review(runner, path):
    broker = receipts.begin(runner, PROMPT)
    broker.completion_interrupt_enabled = True
    broker.transaction_recovery_callback = runner._request_transaction_recovery
    boundary.record_write(broker, path, HTML, {"browser_load": "passed", "browser_canvas_count": 1})
    return broker


def test_probe_reports_dom_created_canvases(variant, monkeypatch, capsys):
    result = diagnostics.run_probe(variant, monkeypatch, capsys, canvas_count=2)
    assert result["status"] == "passed"
    assert result["canvas_count"] == 2


@pytest.mark.parametrize("canvas_count", [0, 1])
def test_preview_uses_observed_dom_not_library_source_patterns(variant, monkeypatch, canvas_count):
    monkeypatch.setattr(variant, "_check_html_dependencies", lambda _source: {})
    monkeypatch.setattr(variant.subprocess, "run", lambda *args, **_kwargs: subprocess.CompletedProcess(
        args, 0, json.dumps({"status": "passed", "canvas_count": canvas_count}), "",
    ))
    previews = []
    monkeypatch.setattr(variant, "_check_graphical_html_visibility", lambda source, **_kwargs: (
        previews.append(source) or {"visual_preview": "nonblank"}
    ))
    source = "<script>new THREE.WebGLRenderer()</script>"
    result = variant._validate_html_artifact(source)
    assert bool(previews) == bool(canvas_count)
    assert result["browser_rendered"] == bool(canvas_count)
    assert result["browser_canvas_count"] == canvas_count


def test_dynamic_canvas_real_browser(variant, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    source = """<html><body><script>
    const surface = document.createElement(['can', 'vas'].join(''));
    surface.width = surface.height = 128;
    document.body.appendChild(surface);
    const brush = surface.getContext('2d');
    brush.fillStyle = 'red'; brush.fillRect(0, 0, 128, 128);
    brush.fillStyle = 'blue'; brush.fillRect(0, 0, 64, 64);
    </script></body></html>"""
    result = variant._validate_html_artifact(source)
    assert result["browser_load"] == "passed", result
    assert result["browser_canvas_count"] == 1
    assert result["visual_preview"] == "nonblank"
    assert result["browser_rendered"] is True


def test_creation_interrupt_starts_review_without_claiming_completion(variant, runner):
    target = runner.workspace / "scene.html"
    broker = start_review(runner, target)
    assert broker._created_html_review_pending == {
        "path": str(target.resolve()), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "prompt": PROMPT,
    }
    assert broker._created_html_review_used
    assert runner._cancel_source == "wrapper_transaction_recovery"
    assert not broker.verified_completion_requested
    assert not variant._artifact_completion_can_end_turn(broker)


def test_unverified_write_cannot_bind_review(runner):
    target = runner.workspace / "scene.html"
    broker = receipts.begin(runner, PROMPT)
    broker.completion_interrupt_enabled = True
    broker.transaction_recovery_callback = runner._request_transaction_recovery
    target.write_text(HTML)
    broker.record_result("write_file", {"path": str(target), "content": HTML}, receipts.result({
        "path": str(target), "created": True, "new_sha256": "0" * 64,
    }))
    assert not broker._created_html_review_pending


def test_review_scope_allows_only_one_bound_patch(variant, runner):
    target = runner.workspace / "scene.html"
    broker = start_review(runner, target)
    runner._wrapper_interrupt_event.clear()
    broker._created_html_review_active = True
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "path": str(target), "status": "text_snapshot", "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "content": HTML, "start_line": 1, "end_line": 1,
    }))
    definitions = [{"name": name} for name in (
        "read_file_snapshot", "apply_text_patch", "write_file", "run_command", "install_python_package",
    )]
    assert {runner._tool_name(tool) for tool in runner._filter_tools_by_intent(PROMPT, definitions)} == {
        "read_file_snapshot", "apply_text_patch",
    }
    for name, arguments in (
        ("write_file", {"path": str(target), "content": HTML}),
        ("run_command", {"command": "npm install three"}),
        ("read_file_snapshot", {"path": str(runner.workspace / "other.html")}),
        ("apply_text_patch", {"path": str(runner.workspace / "other.html"), "replacements": []}),
    ):
        assert not broker.authorize(name, arguments)[0]
    arguments = {"path": str(target), "replacements": [{"old_text": "speed = 1", "new_text": "speed = 3"}]}
    assert broker.authorize("apply_text_patch", arguments)[0]
    target.write_text(HTML.replace("speed = 1", "speed = 3"))
    broker.record_result("apply_text_patch", arguments, receipts.result({
        "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    assert broker._created_html_review_committed
    assert not broker.authorize("apply_text_patch", arguments)[0]


def test_review_refuses_outside_changes_and_resets_each_turn(runner):
    target = runner.workspace / "scene.html"
    broker = start_review(runner, target)
    broker._created_html_review_active = True
    target.write_text("user changed this\n")
    allowed, denial = broker.authorize("read_file_snapshot", {"path": str(target)})
    assert not allowed and denial["status"] == "snapshot_mismatch"
    broker.begin_turn("Explain JavaScript without tools.", None)
    assert not broker._created_html_review_pending
    assert not broker._created_html_review_active
    assert not broker._created_html_review_used


@pytest.mark.parametrize("outcome", [
    "patch", "no_change", "cancel", "changed_snapshot", "review_error", "creation_interrupt",
    "no_progress", "unclassified_cancel", "review_user_cancel",
])
def test_runner_finishes_one_review_without_reentering_write_loop(variant, runner, monkeypatch, outcome):
    target = runner.workspace / "scene.html"
    host_calls = receipts.install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]
    passes = []

    async def model_pass(self, prompt, callback=None):
        broker = self._tool_permission_broker
        passes.append(prompt)
        if len(passes) == 1:
            boundary.record_write(broker, target, HTML, {"browser_load": "passed", "visual_preview": "nonblank"})
            if outcome == "changed_snapshot":
                target.write_text("user changed this\n")
            if outcome == "cancel":
                self._cancel_source = "user"
                self._cancel_event.set()
                raise asyncio.CancelledError
            if outcome == "creation_interrupt":
                raise asyncio.CancelledError
        else:
            assert len(passes) == 2 and broker._created_html_review_active
            assert self._prompt_step_retry_override == 2
            assert "one bounded phase" in prompt
            if outcome == "review_error":
                raise RuntimeError("simulated review transport failure")
            if outcome == "no_progress":
                callback("status", "Model step 2/2")
                pytest.fail("No-progress review should have stopped before another model step")
            if outcome == "unclassified_cancel":
                raise asyncio.CancelledError("private backend detail must not be displayed")
            if outcome == "review_user_cancel":
                self._cancel_source = "user"
                self._cancel_event.set()
                raise asyncio.CancelledError("explicit user cancellation")
            if outcome == "patch":
                args = {"path": str(target), "replacements": [{"old_text": "speed = 1", "new_text": "speed = 3"}]}
                assert broker.authorize("apply_text_patch", args)[0]
                target.write_text(HTML.replace("speed = 1", "speed = 3"))
                broker.record_result("apply_text_patch", args, receipts.result({
                    "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    "artifact_validation": {"browser_load": "passed", "visual_preview": "nonblank"},
                }))
                raise asyncio.CancelledError
        return "Unsupported claim: all interactions and science are verified."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(PROMPT))
    assert len(passes) == (1 if outcome in {"cancel", "changed_snapshot"} else 2)
    assert "[Completion status: verified]" not in answer
    assert "Unsupported claim" not in answer
    if outcome == "patch":
        assert "speed = 3" in target.read_text()
        assert "patch was committed" in answer
    elif outcome == "changed_snapshot":
        assert target.read_text() == "user changed this\n"
    elif outcome == "review_error":
        assert "RuntimeError" in answer
        assert "reason=runtime_error" in answer
        assert target.read_text() == HTML
    elif outcome == "no_progress":
        assert "ArtifactReviewNoProgressError; reason=no_verified_tool_progress" in answer
        assert "[Completion status: partial]" in answer
        assert target.read_text() == HTML
    elif outcome == "unclassified_cancel":
        assert "CancelledError; reason=cancellation_unclassified" in answer
        assert "private backend detail" not in answer
        assert target.read_text() == HTML
    elif outcome == "review_user_cancel":
        assert runner._last_turn_control["cancelled"] is True
        assert runner._last_turn_control["finished"] is True
        assert runner._cancel_source == ""
        assert not runner._cancel_event.is_set()
        assert "Artifact source review stopped" not in answer
        assert target.read_text() == HTML
    else:
        assert target.read_text() == HTML
    assert all(name == "read_file_snapshot" for name, _arguments in host_calls)
    assert not runner._tool_permission_broker._created_html_review_active
