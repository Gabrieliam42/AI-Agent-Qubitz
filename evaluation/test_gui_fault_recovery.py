"""A turn-local runtime error must not leave the real Tk GUI unusable."""
from evaluation import test_gui_event_delivery as delivery_tests

variant = delivery_tests.variant
delivery = delivery_tests.delivery


def test_normal_send_works_after_a_previous_turn_error(delivery, monkeypatch):
    probe, _offset = delivery
    gui = probe.gui
    calls = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *_a: None)
    def run(prompt, _callback):
        calls.append(prompt)
        if len(calls) == 1:
            raise RuntimeError("Test-owned transient turn error; no files changed")
        return "Fresh task completed after the earlier error."
    monkeypatch.setattr(gui.agent, "run_sync", run)
    for prompt in ("First failing fixture task.", "New independent fixture task."):
        gui.prompt_box.delete("1.0", "end")
        gui.prompt_box.insert("1.0", prompt)
        gui.send_prompt()
        assert probe.drive(lambda: not gui.busy and gui.event_queue.empty(), seconds=5)
    events = [item for _instant, item in probe.dequeued if len(item) == 3]
    assert sum(item[1] == "done" for item in events) == 2
    assert sum(item[1] == "error" for item in events) == 1
    assert any(item[1] == "answer" and "Fresh task" in str(item[2]) for item in events)
    assert len(calls) == 2 and not probe.load_attempts
