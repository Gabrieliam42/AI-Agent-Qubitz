"""Model-free real-Tk probes for queue delivery versus input/render blocking."""
from __future__ import annotations

import json
import os
import threading
import time
from contextlib import suppress

import pytest
import test_followup_state_receipts as receipt_fixtures
from test_cache_result_delivery import virtual_tcl_clock

from runtime.paths import PROJECT_ROOT

variant = receipt_fixtures.variant
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Unix Tcl clock/file-event diagnostic")


class DeliveryProbe:
    """The test's file event bounds mainloop without invoking product polling."""

    def __init__(self, gui, load_attempts):
        self.gui = gui
        self.load_attempts = load_attempts
        self.enqueued = []
        self.dequeued = []
        self.rendered = []
        self.inputs = []
        self.input_requested = None
        self.predicate = lambda: False
        self.deadline = 0.0
        self.stop = threading.Event()
        self.read_fd, self.write_fd = os.pipe()
        for descriptor in (self.read_fd, self.write_fd):
            os.set_blocking(descriptor, False)
        gui.root.createfilehandler(self.read_fd, gui.tk.READABLE, self._tick)
        gui.root.bind("<<QubitzDeliveryProbe>>", self._input, add="+")
        self.ticker = threading.Thread(target=self._wake, name="test-gui-probe-wake", daemon=True)
        self.ticker.start()
        put, get = gui.event_queue.put, gui.event_queue.get_nowait

        def observed_put(item, *args, **kwargs):
            self.enqueued.append((time.monotonic(), item))
            return put(item, *args, **kwargs)

        def observed_get():
            item = get()
            self.dequeued.append((time.monotonic(), item))
            return item

        gui.event_queue.put = observed_put
        gui.event_queue.get_nowait = observed_get
        append = gui._append_transcript

        def observed_append(role, message):
            started = time.monotonic()
            try:
                return append(role, message)
            finally:
                self.rendered.append((started, time.monotonic(), role, message))

        gui._append_transcript = observed_append

    def _wake(self):
        while not self.stop.wait(0.005):
            with suppress(BlockingIOError):
                os.write(self.write_fd, b"x")

    def _input(self, _event):
        self.inputs.append(time.monotonic())

    def _tick(self, descriptor, _mask):
        with suppress(BlockingIOError):
            os.read(descriptor, 8192)
        if self.input_requested is not None:
            self.gui.root.event_generate("<<QubitzDeliveryProbe>>", when="tail")
            self.input_requested = None
        if self.predicate() or time.monotonic() >= self.deadline:
            self.gui.root.quit()

    def drive(self, predicate, seconds=2.0):
        self.predicate = predicate
        self.deadline = time.monotonic() + seconds
        self.gui.root.mainloop()
        return predicate()

    def close(self):
        self.stop.set()
        self.ticker.join(2)
        assert not self.ticker.is_alive()
        self.gui.root.deletefilehandler(self.read_fd)
        os.close(self.read_fd)
        os.close(self.write_fd)

    def emit(self, count):
        completed = threading.Event()

        def producer():
            for index in range(count):
                self.gui.event_queue.put(("status", f"probe:{index:04d}"))
            self.gui.event_queue.put(("done", ""))
            completed.set()

        worker = threading.Thread(target=producer, name="test-gui-probe-worker")
        worker.start()
        assert completed.wait(2)
        worker.join(2)
        assert not worker.is_alive()
        self.input_requested = time.monotonic()
        return completed

    def statuses(self):
        return [item[1] for _, item in self.dequeued if item[0] == "status" and item[1].startswith("probe:")]

    def report(self, condition, **details):
        put_times = {id(item): instant for instant, item in self.enqueued}
        waits = [instant - put_times[id(item)] for instant, item in self.dequeued if id(item) in put_times]
        print("GUI_DELIVERY_PROBE " + json.dumps({
            "condition": condition, "variant": self.gui._qubitz_gui_log_path.name,
            "queue_puts": len(self.enqueued), "queue_gets": len(self.dequeued),
            "max_queue_wait_s": max(waits, default=None),
            "max_render_s": max((end - start for start, end, _, _ in self.rendered), default=None),
            "input_events": len(self.inputs), "model_load_attempts": len(self.load_attempts), **details,
        }, sort_keys=True))


@pytest.fixture
def delivery(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    load_attempts = []

    def refuse_model(*_args):
        load_attempts.append(time.monotonic())
        pytest.fail("Probe attempted model loading")

    monkeypatch.setattr(base.AgentRunner, "_get_llm", refuse_model)
    monkeypatch.setattr(base.QubitzGUI, "_present_window", lambda _self: None)
    monkeypatch.setattr(base.QubitzGUI, "_schedule_startup_visibility_recovery", lambda _self: None)
    if hasattr(base.QubitzGUI, "_maybe_start_background_model_prewarm"):
        monkeypatch.setattr(base.QubitzGUI, "_maybe_start_background_model_prewarm", lambda _self: None)
    with virtual_tcl_clock() as offset:
        gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
        gui.root.withdraw()
        probe = DeliveryProbe(gui, load_attempts)
        try:
            started = time.monotonic()
            assert probe.drive(lambda: time.monotonic() - started >= 0.15 and gui.event_queue.empty())
            probe.enqueued.clear()
            probe.dequeued.clear()
            probe.rendered.clear()
            yield probe, offset
            assert not load_attempts
        finally:
            offset[0] = 0.0
            probe.close()
            gui.agent.reset_runtime()
            gui.root.destroy()


@pytest.mark.parametrize("count", [8, 256])
def test_worker_events_are_fifo_and_input_is_serviced(delivery, count):
    probe, _ = delivery
    completed = probe.emit(count)
    finished = probe.drive(lambda: len(probe.statuses()) == count and bool(probe.inputs)
                           and probe.gui.event_queue.empty())
    probe.report("normal" if count == 8 else "burst", producer_completed=completed.is_set())
    assert finished
    assert probe.statuses() == [f"probe:{index:04d}" for index in range(count)]
    assert probe.gui.event_queue.empty()


def test_worker_delivery_survives_backward_tcl_clock_while_input_is_live(delivery):
    probe, offset = delivery
    control_timer = []
    probe.gui.root.after(30, lambda: control_timer.append(True))
    offset[0] = -20.0
    completed = probe.emit(8)
    delivered = probe.drive(lambda: len(probe.statuses()) == 8 and bool(probe.inputs), seconds=0.5)
    blocked_timer = not control_timer
    probe.report("backward_tcl_clock", producer_completed=completed.is_set(),
                 delivered_during_backstep=delivered, control_timer_blocked=blocked_timer,
                 queue_remaining=probe.gui.event_queue.qsize())
    offset[0] = 0.0
    assert probe.drive(lambda: len(probe.statuses()) == 8 and bool(control_timer))
    assert completed.is_set() and probe.inputs and blocked_timer
    assert delivered, "Completed worker events waited for Tcl clock recovery while input remained live"


def test_deliberately_slow_render_blocks_input_but_not_worker(delivery, monkeypatch):
    probe, _ = delivery
    entered, finished = threading.Event(), threading.Event()
    progress = []
    original = probe.gui._append_transcript
    requested = []
    render_times = []

    def slow_append(role, message):
        if message == "probe:slow-render":
            started = time.monotonic()
            entered.set()
            time.sleep(0.2)
            render_times.append(time.monotonic() - started)
        return original(role, message)

    def producer():
        assert entered.wait(2)
        requested.append(time.monotonic())
        probe.input_requested = requested[0]
        while not finished.wait(0.01):
            progress.append(time.monotonic())

    monkeypatch.setattr(probe.gui, "_append_transcript", slow_append)
    worker = threading.Thread(target=producer, name="test-slow-render-worker")
    worker.start()
    probe.gui.event_queue.put(("status", "probe:slow-render"))
    try:
        assert probe.drive(lambda: bool(probe.inputs))
    finally:
        finished.set()
        worker.join(2)
    lag = probe.inputs[0] - requested[0]
    probe.report("deliberately_slow_render", input_wait_s=lag, worker_progress_count=len(progress),
                 injected_render_s=render_times[0])
    assert not worker.is_alive()
    assert progress and lag >= 0.15
