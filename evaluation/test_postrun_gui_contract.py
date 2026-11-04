"""Visible guarded Tk Send/cancel provenance check; no model is loaded."""
from __future__ import annotations

import json
import time

from evaluation import test_gui_event_delivery as delivery_tests

variant = delivery_tests.variant
delivery = delivery_tests.delivery


def test_visible_send_preserves_timeout_origin_and_private_transcript(delivery, monkeypatch, tmp_path):
    probe, _offset = delivery
    gui = probe.gui
    errors = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))

    def cancel_fixture(_prompt, _callback):
        gui.agent.request_cancel(origin="evaluator_timeout")
        return gui.agent._cancel_partial_answer

    monkeypatch.setattr(gui.agent, "run_sync", cancel_fixture)
    gui.root.title("Qubitz model-free post-run verification")
    gui.root.deiconify()
    gui.prompt_box.insert("1.0", "Guarded cancellation provenance fixture; no inference.")
    gui.send_prompt()
    assert probe.drive(lambda: not gui.busy and gui.event_queue.empty(), seconds=10)
    text = gui.conversation_journal.snapshot()
    assert "evaluator timeout" in text and "user's request" not in text
    assert str(gui.conversation_journal.path) in text
    assert not errors and not probe.load_attempts
    # Keep the known test-owned window available for independent read-only inspection.
    start = time.monotonic()
    assert probe.drive(lambda: time.monotonic() - start >= 35, seconds=40)
    (tmp_path / "gui_receipt.json").write_text(json.dumps({
        "window_title": gui.root.title(), "mapped": bool(gui.root.winfo_ismapped()),
        "geometry": gui.root.geometry(), "model_load_attempts": probe.load_attempts,
        "errors": errors, "cancel_origin": gui.agent._cancel_origin,
        "transcript_path": str(gui.conversation_journal.path),
    }, indent=2))
