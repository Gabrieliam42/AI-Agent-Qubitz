"""Guarded real-Tk normal Send and escaped-worker diagnostics; no inference."""
from __future__ import annotations

import asyncio
import queue
import threading
from types import SimpleNamespace

import pytest

import tmp_smoke_variant as smoke
from evaluation import test_gui_event_delivery as delivery_tests
from evaluation.disposable_followup_matrix import apply_gui_diagnostics
from evaluation.gui_diagnostics import collect_gui_diagnostics

variant = delivery_tests.variant
delivery = delivery_tests.delivery


@pytest.mark.parametrize("outcome", ["normal", "user", "unexpected"])
def test_normal_send_delivers_exactly_one_terminal_event(delivery, monkeypatch, outcome):
    probe, _offset = delivery
    gui = probe.gui
    unhandled = []
    dialogs = []
    monkeypatch.setattr(threading, "excepthook", unhandled.append)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda title, message: dialogs.append((title, message)))

    def run(_prompt, _callback):
        if outcome == "normal":
            return "Model-free verified fixture answer."
        gui.agent._cancel_source = "user" if outcome == "user" else "wrapper_verified_completion"
        gui.agent._cancel_partial_answer = "Cancelled; preserved fixture output."
        raise asyncio.CancelledError("fixture worker boundary")

    monkeypatch.setattr(gui.agent, "run_sync", run)
    gui.prompt_box.insert("1.0", "Guarded fixture request.")
    gui.send_prompt()
    assert probe.drive(lambda: not gui.busy and gui.event_queue.empty(), seconds=5)
    events = [item for _time, item in probe.dequeued if len(item) == 3]
    assert sum(item[1] == "done" for item in events) == 1
    assert sum(item[1] == ("error" if outcome == "unexpected" else "answer") for item in events) == 1
    assert not unhandled and not probe.load_attempts
    assert len(dialogs) == (1 if outcome == "unexpected" else 0)
    diagnostics = collect_gui_diagnostics(gui)
    assert ("gui_logged_error" in diagnostics.behavior_issues) is (outcome == "unexpected")


def test_evaluator_retains_a_worker_exception_even_without_gui_error_event(tmp_path):
    def worker(*_args):
        raise asyncio.CancelledError("escaped test worker")

    log = tmp_path / "gui.jsonl"
    log.write_text('{"event":"gui_event","kind":"done"}\n')
    gui = SimpleNamespace(_append_transcript=lambda *_: None, event_queue=queue.Queue(),
                          _worker_run_tagged=worker, _qubitz_gui_log_path=log,
                          _qubitz_flush_gui_log=lambda **_: True)
    smoke._attach_event_capture(gui)
    with pytest.raises(asyncio.CancelledError, match="escaped test worker"):
        gui._worker_run_tagged("request", 3)
    receipt = collect_gui_diagnostics(gui)
    assert "gui_worker_exception" in receipt.behavior_issues
    assert receipt.worker_exceptions[0]["exception_type"] == "CancelledError"


@pytest.mark.parametrize("status", ["passed", "failed", "interrupted"])
@pytest.mark.parametrize("issue", ["gui_worker_exception", "gui_logged_error"])
def test_worker_fault_holds_matrix_even_if_task_or_cancellation_looked_normal(status, issue):
    report = {"status": status, "issues": []}
    apply_gui_diagnostics(report, {"behavior_issues": [issue]})
    assert report["status"] == "harness_error"
    assert report["status_before_runtime_fault"] == status
    assert report["issues"] == ["runtime_worker_error"]


@pytest.mark.parametrize("status", ["passed", "failed", "interrupted"])
def test_clean_worker_preserves_task_verdict_and_expected_cancellation(status):
    report = {"status": status, "issues": []}
    apply_gui_diagnostics(report, {"behavior_issues": [], "warnings": ["gui_heartbeat_stall_recovered"]})
    assert report["status"] == status and report["gui_status"] == "passed"
