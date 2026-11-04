"""Real Tk attribution after user input is queued during an evaluator task."""
from __future__ import annotations

import threading

import tmp_smoke_variant as smoke
from evaluation import test_gui_event_delivery as delivery_tests

variant = delivery_tests.variant
delivery = delivery_tests.delivery


def test_original_answer_and_runtime_do_not_follow_queued_user_task(delivery, monkeypatch):
    probe, _ = delivery
    gui = probe.gui
    first_release = threading.Event()
    calls = []

    def run(prompt, _callback):
        calls.append(prompt)
        if prompt == "Original evaluator task.":
            assert first_release.wait(5)
        gui.agent._last_turn_control = {"turn_id": prompt, "cancelled": prompt.startswith("Original")}
        return "Original answer." if prompt.startswith("Original") else "Unrelated answer."

    monkeypatch.setattr(gui.agent, "run_sync", run)
    monkeypatch.setattr(smoke, "_drive", lambda _root, predicate, seconds: probe.drive(predicate, seconds=seconds))
    smoke._attach_event_capture(gui)
    gui._smoke_snapshot_runner = lambda: {"turn_control": dict(gui.agent._last_turn_control)}

    def queue_extra():
        gui.prompt_box.insert("1.0", "s")
        gui.send_prompt()
        first_release.set()

    gui.root.after(30, queue_extra)
    result = smoke._send_prompt(gui, "Original evaluator task.", 5)
    assert result["external_input"] is True
    assert result["assistant"].startswith("Original answer.")
    assert result["submitted_runtime"]["turn_control"]["turn_id"] == "Original evaluator task."
    assert result["submitted_gui_run_id"] == 1
    assert probe.drive(lambda: not gui.busy and gui.event_queue.empty(), seconds=5)
    assert calls == ["Original evaluator task.", "s"]
    assert not probe.load_attempts
