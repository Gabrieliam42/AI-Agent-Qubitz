"""Notification failure, descriptor lifetime, FIFO and reentrant-drain checks."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import pytest
import test_gui_event_delivery as probes

from runtime import gui_event_delivery as delivery_module

variant = probes.variant
delivery = probes.delivery


class Root:
    def __init__(self):
        self.handlers = {}
        self.bindings = []
        self.idle_callbacks = {}
        self.reported_errors = []
        self.serial = 0

    def createfilehandler(self, descriptor, _mask, callback):
        self.handlers[descriptor] = callback

    def deletefilehandler(self, descriptor):
        del self.handlers[descriptor]

    def bind(self, _event, callback, **_kwargs):
        self.bindings.append(callback)

    def after_idle(self, callback):
        self.serial += 1
        self.idle_callbacks[self.serial] = callback
        return self.serial

    def after_cancel(self, token):
        self.idle_callbacks.pop(token, None)

    def report_callback_exception(self, *details):
        self.reported_errors.append(details)

    def run_idle(self):
        pending, self.idle_callbacks = self.idle_callbacks, {}
        for callback in pending.values():
            callback()


@pytest.fixture
def owner():
    seen = []
    work = queue.Queue()

    def drain():
        while not work.empty():
            seen.append(work.get_nowait())

    value = SimpleNamespace(root=Root(), tk=tk, event_queue=work, _drain_gui_events=drain)
    yield value, seen
    close = getattr(value, "_qubitz_close_gui_event_delivery", None)
    if close:
        close()


def invoke_ready(owner):
    descriptor, callback = next(iter(owner.root.handlers.items()))
    callback(descriptor, tk.READABLE)


def test_original_queue_and_fifo_are_preserved(owner):
    value, seen = owner
    original = value.event_queue
    delivery_module._qubitz_install_gui_event_delivery(value)
    for index in range(100):
        value.event_queue.put_nowait(("status", index))
    invoke_ready(value)
    assert value.event_queue is original
    assert seen == [("status", index) for index in range(100)]
    assert value._qubitz_gui_event_delivery == "fileevent"


@pytest.mark.parametrize("failure", ["unavailable", "pipe", "registration"])
def test_setup_failure_retains_timer_queue_and_closes_descriptors(owner, monkeypatch, failure):
    value, _ = owner
    descriptors = []
    real_pipe = os.pipe

    def pipe():
        if failure == "pipe":
            raise OSError("fixture pipe failure")
        pair = real_pipe()
        descriptors.extend(pair)
        return pair

    def register(*_args):
        raise tk.TclError("fixture registration failure")

    monkeypatch.setattr(delivery_module._gui_event_os, "pipe", pipe)
    value.root.createfilehandler = None if failure == "unavailable" else register
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.event_queue.put(("status", "retained"))
    assert value.event_queue.get_nowait() == ("status", "retained")
    assert value._qubitz_gui_event_delivery == "timer"
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


@pytest.mark.parametrize("error", [BlockingIOError, OSError])
def test_wakeup_failure_never_fails_or_rewrites_worker_put(owner, monkeypatch, error):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)

    def fail(*_args):
        raise error("fixture write failure")

    monkeypatch.setattr(delivery_module._gui_event_os, "write", fail)
    value.event_queue.put(("status", "original"))
    assert value.event_queue.get_nowait() == ("status", "original")
    assert value._qubitz_gui_event_delivery == ("timer" if error is OSError else "fileevent")


def test_read_failure_unregisters_reader_and_keeps_queued_event(owner, monkeypatch):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.event_queue.put(("status", "retained"))

    def fail(*_args):
        raise OSError("fixture reader failure")

    monkeypatch.setattr(delivery_module._gui_event_os, "read", fail)
    invoke_ready(value)
    assert not value.root.handlers
    assert value.event_queue.get_nowait() == ("status", "retained")
    assert value._qubitz_gui_event_delivery == "timer"


def test_late_worker_cannot_write_to_reused_descriptor(owner, monkeypatch):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    saved_put = value.event_queue.put
    old_read = next(iter(value.root.handlers))
    value._qubitz_close_gui_event_delivery()
    fresh = os.pipe()
    writes = []
    real_write = os.write

    def write(descriptor, payload):
        writes.append(descriptor)
        return real_write(descriptor, payload)

    try:
        assert old_read in fresh
        monkeypatch.setattr(delivery_module._gui_event_os, "write", write)
        worker = threading.Thread(target=saved_put, args=(("status", "late"),))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        assert not writes
        assert value.event_queue.get_nowait() == ("status", "late")
    finally:
        for descriptor in fresh:
            os.close(descriptor)


def test_child_widget_destruction_does_not_close_notifier(owner):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.root.bindings[0](SimpleNamespace(widget=object()))
    assert value.root.handlers


def test_reinstall_is_idempotent(owner):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    original_put = value.event_queue.put
    delivery_module._qubitz_install_gui_event_delivery(value)
    assert value.event_queue.put is original_put
    assert len(value.root.handlers) == len(value.root.bindings) == 1


def test_consumer_exception_uses_tk_reporter_without_escaping_file_callback(owner):
    value, seen = owner
    original_drain = value._drain_gui_events
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.event_queue.put(("status", "retained"))
    error = ValueError("fixture render error")

    def fail():
        raise error

    value._drain_gui_events = fail
    invoke_ready(value)
    assert value.root.reported_errors[0][:2] == (ValueError, error)
    assert not value.root.handlers and len(value.root.idle_callbacks) == 1
    value._drain_gui_events = original_drain
    value.root.run_idle()
    invoke_ready(value)
    assert seen == [("status", "retained")]


def test_close_cancels_pending_idle_rearm_and_late_callback_is_inert(owner):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.event_queue.put(("status", "original"))
    invoke_ready(value)
    saved_resume = next(iter(value.root.idle_callbacks.values()))
    value._qubitz_close_gui_event_delivery()
    assert not value.root.idle_callbacks
    saved_resume()
    assert not value.root.handlers


@pytest.mark.parametrize("failure", ["idle", "rearm"])
def test_idle_setup_and_rearm_failure_fall_back_without_failing_put(owner, failure):
    value, seen = owner
    delivery_module._qubitz_install_gui_event_delivery(value)

    def fail(*_args):
        raise tk.TclError("fixture idle or rearm failure")

    if failure == "idle":
        value.root.after_idle = fail
    value.event_queue.put(("status", "original"))
    invoke_ready(value)
    if failure == "rearm":
        value.root.createfilehandler = fail
        value.root.run_idle()
    assert seen == [("status", "original")]
    assert value._qubitz_gui_event_delivery == "timer"
    assert not value.root.handlers and not value.root.idle_callbacks
    value.event_queue.put(("status", "later"))
    assert value.event_queue.get_nowait() == ("status", "later")


def test_stale_reader_cannot_read_a_reused_descriptor(owner, monkeypatch):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    descriptor, callback = next(iter(value.root.handlers.items()))
    value._qubitz_close_gui_event_delivery()
    fresh = os.pipe()
    reads = []
    real_read = os.read

    def read(descriptor, count):
        reads.append(descriptor)
        return real_read(descriptor, count)

    try:
        assert descriptor in fresh
        monkeypatch.setattr(delivery_module._gui_event_os, "read", read)
        callback(descriptor, tk.READABLE)
        assert not reads
    finally:
        for descriptor in fresh:
            os.close(descriptor)


def test_pipe_eof_unregisters_reader_and_retains_queue(owner, monkeypatch):
    value, _ = owner
    delivery_module._qubitz_install_gui_event_delivery(value)
    value.event_queue.put(("status", "retained"))
    monkeypatch.setattr(delivery_module._gui_event_os, "read", lambda *_args: b"")
    invoke_ready(value)
    assert not value.root.handlers
    assert value.event_queue.get_nowait() == ("status", "retained")
    assert value._qubitz_gui_event_delivery == "timer"


def test_more_than_one_batch_delivers_during_clock_backstep(delivery):
    probe, offset = delivery
    offset[0] = -20.0
    probe.emit(256)
    assert probe.drive(lambda: len(probe.statuses()) == 256 and probe.gui.event_queue.empty())
    assert probe.statuses() == [f"probe:{index:04d}" for index in range(256)]
    probe.report("backstep_multiple_batches")


def test_nested_drain_preserves_order_and_single_consumption(delivery, monkeypatch):
    probe, _ = delivery
    original = probe.gui._append_transcript

    def nested(role, message):
        if message == "probe:0000":
            probe.gui._drain_gui_events()
            probe.gui.root.update()
        return original(role, message)

    monkeypatch.setattr(probe.gui, "_append_transcript", nested)
    probe.emit(100)
    assert probe.drive(lambda: len(probe.statuses()) == 100 and probe.gui.event_queue.empty())
    assert probe.statuses() == [f"probe:{index:04d}" for index in range(100)]
    probe.report("nested_drain")


@pytest.mark.parametrize("backstep", [False, True])
def test_sustained_stream_services_input_and_idle_work(delivery, monkeypatch, backstep):
    probe, offset = delivery
    gui = probe.gui
    gui._active_gui_run_id = 41
    gui._set_busy(True)
    gui.config.show_reasoning = True
    main_thread = threading.get_ident()
    render_threads = []
    idle_calls = []
    queued, completed, stop = threading.Event(), threading.Event(), threading.Event()
    emitted = []
    worker_finished = []
    original_stream = gui._append_stream_delta
    original_reasoning = gui._append_reasoning_delta

    def stream(message):
        render_threads.append(threading.get_ident())
        original_stream(message)
        time.sleep(0.002)  # Bounded fixture work keeps a backlog across many batches.

    def reasoning(message):
        render_threads.append(threading.get_ident())
        original_reasoning(message)

    def put(kind, message):
        item = (41, kind, message)
        emitted.append(item)
        gui.event_queue.put(item)

    def producer():
        put("reasoning_delta", "Fixture reasoning, not an answer.")
        for _ in range(1024):
            put("assistant_delta", "x")
        queued.set()
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline and not stop.wait(0.01):
            probe.input_requested = time.monotonic()
        put("answer", "delivery-final-answer")
        put("done", "")
        worker_finished.append(time.monotonic())
        completed.set()

    monkeypatch.setattr(gui, "_append_stream_delta", stream)
    monkeypatch.setattr(gui, "_append_reasoning_delta", reasoning)
    timer = []
    gui.root.after(30, lambda: timer.append(True))
    if backstep:
        offset[0] = -20.0
    worker = threading.Thread(target=producer, name="test-sustained-stream-worker")
    worker.start()
    try:
        assert queued.wait(2)
        gui.root.after_idle(lambda: idle_calls.append((
            time.monotonic(), gui.event_queue.qsize(), completed.is_set(), threading.get_ident(),
        )))
        finished = probe.drive(lambda: completed.is_set() and gui.event_queue.empty()
                               and not gui.busy and bool(probe.inputs) and bool(idle_calls), seconds=4)
    finally:
        stop.set()
        worker.join(2)
    idle_with_backlog = any(size > 0 and not done for _, size, done, _ in idle_calls)
    probe.report("sustained_stream_backstep" if backstep else "sustained_stream",
                 delivered=finished, idle_with_backlog=idle_with_backlog,
                 fixture_render_delay_s=0.002, model_messages=len(emitted),
                 gui_thread_only=all(ident == main_thread for ident in render_threads),
                 control_timer_blocked=not timer)
    assert finished and not worker.is_alive()
    assert len(probe.dequeued) == len(emitted)
    assert all(actual is expected for (_, actual), expected in zip(probe.dequeued, emitted))
    assert render_threads and set(render_threads) == {main_thread}
    assert any(instant < worker_finished[0] for instant in probe.inputs)
    assert all(ident == main_thread for _, _, _, ident in idle_calls)
    assert idle_with_backlog, "File-event queue delivery deferred all idle work until the backlog drained"
    transcript = gui.transcript.get("1.0", "end-1c")
    assert transcript.count("delivery-final-answer") == 1
    assert "x" * 64 not in transcript
    assert gui._stream_preview_start is None
    if backstep:
        assert not timer
        offset[0] = 0.0
        assert probe.drive(lambda: bool(timer))
