"""A typed rejected completion remains failed while the real GUI accepts a fresh task."""
from __future__ import annotations

from evaluation import test_gui_event_delivery as delivery_tests
from runtime.request_failures import generation_rejection

variant = delivery_tests.variant
delivery = delivery_tests.delivery
pytestmark = delivery_tests.pytestmark


def test_rejection_then_fresh_normal_send_without_replay(delivery, monkeypatch):
    probe, _offset = delivery
    gui = probe.gui
    calls = []

    def run(prompt, _callback):
        calls.append(prompt)
        if len(calls) == 1:
            raise generation_rejection({"code": 500, "type": "server_error",
                                       "message": "The model produced output that does not match the expected peg-native format"},
                                       {"messages": []}, http_status=200, request_id="gui-fixture")
        return "New independent task answered."

    monkeypatch.setattr(gui.agent, "run_sync", run)
    for prompt in ("First rejected fixture task.", "New independent fixture task."):
        gui.prompt_box.delete("1.0", "end")
        gui.prompt_box.insert("1.0", prompt)
        gui.send_prompt()
        assert probe.drive(lambda: not gui.busy and gui.event_queue.empty(), seconds=5)
        if len(calls) == 1:
            assert gui.agent._last_request_failure["retry_permitted"] is False
        else:
            assert gui.agent._last_request_failure is None
    events = [item for _instant, item in probe.dequeued if len(item) == 3]
    assert sum(item[1] == "done" for item in events) == 2
    assert not any(item[1] == "error" for item in events)
    assert any(item[1] == "answer" and "[Completion status: partial]" in str(item[2]) for item in events)
    assert any(item[1] == "answer" and "New independent" in str(item[2]) for item in events)
    assert len(calls) == 2 and not probe.load_attempts
