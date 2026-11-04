"""Cache results reach Tk despite a backward Tcl clock step and GUI closure."""
from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import threading
import time
import tkinter as tk
from contextlib import contextmanager, nullcontext

import pytest
import test_followup_state_receipts as receipt_fixtures

from runtime import catalog
from runtime.paths import PROJECT_ROOT

variant = receipt_fixtures.variant


class PipeLifetimes:
    def __init__(self):
        self.real_pipe, self.real_close, self.real_fstat = os.pipe, os.close, os.fstat
        self.records = []
        self.lock = threading.Lock()

    @staticmethod
    def identity(stat):
        return stat.st_dev, stat.st_ino, stat.st_mode

    def pipe(self):
        with self.lock:
            pair = self.real_pipe()
            for descriptor in pair:
                self.records.append({
                    "fd": descriptor,
                    "identity": self.identity(self.real_fstat(descriptor)),
                    "closed": False,
                })
            return pair

    def close(self, descriptor):
        with self.lock:
            try:
                identity = self.identity(self.real_fstat(descriptor))
            except OSError:
                identity = None
            result = self.real_close(descriptor)
            for record in self.records:
                if record["fd"] == descriptor and record["identity"] == identity:
                    record["closed"] = True
            return result

    def assert_closed(self):
        with self.lock:
            assert self.records, "No pipe lifetime was observed"
            for record in self.records:
                assert record["closed"], f"Original pipe endpoint was not closed: {record}"
                try:
                    current = self.identity(self.real_fstat(record["fd"]))
                except OSError as error:
                    assert error.errno == errno.EBADF
                else:
                    # FD integers may already refer to an unrelated file or socket.
                    assert current != record["identity"], f"Original pipe is still open: {record}"


@pytest.fixture
def pipe_lifetimes(monkeypatch):
    tracker = PipeLifetimes()
    monkeypatch.setattr(os, "pipe", tracker.pipe)
    monkeypatch.setattr(os, "close", tracker.close)
    return tracker


@pytest.mark.skipif(os.name != "posix", reason="Unix descriptor lifetime identity")
def test_pipe_cleanup_oracle_detects_an_open_endpoint(pipe_lifetimes):
    pair = os.pipe()
    try:
        with pytest.raises(AssertionError, match="was not closed"):
            pipe_lifetimes.assert_closed()
        os.close(pair[0])
        with pytest.raises(AssertionError, match="was not closed"):
            pipe_lifetimes.assert_closed()
    finally:
        for descriptor in pair:
            try:
                os.close(descriptor)
            except OSError as error:
                assert error.errno == errno.EBADF
    pipe_lifetimes.assert_closed()


@pytest.mark.skipif(os.name != "posix", reason="Unix descriptor lifetime identity")
def test_pipe_cleanup_oracle_distinguishes_reused_descriptor_slots(pipe_lifetimes):
    source = os.open(os.devnull, os.O_RDONLY)
    pair = os.pipe()
    try:
        for descriptor in pair:
            os.close(descriptor)
            os.dup2(source, descriptor)
            assert os.fstat(descriptor)
        pipe_lifetimes.assert_closed()
    finally:
        for descriptor in (*pair, source):
            os.close(descriptor)


@contextmanager
def virtual_tcl_clock():
    """Only this test process's Tcl time changes; the OS clock is untouched."""
    class TclTime(ctypes.Structure):
        _fields_ = [("sec", ctypes.c_long), ("usec", ctypes.c_long)]

    signature = ctypes.CFUNCTYPE(None, ctypes.POINTER(TclTime), ctypes.c_void_p)
    library = ctypes.CDLL(ctypes.util.find_library("tcl8.6"))
    library.Tcl_QueryTimeProc.argtypes = [ctypes.POINTER(ctypes.c_void_p)] * 3
    library.Tcl_SetTimeProc.argtypes = [ctypes.c_void_p] * 3
    original, scale, client = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    library.Tcl_QueryTimeProc(ctypes.byref(original), ctypes.byref(scale), ctypes.byref(client))
    anchor_mono, anchor_wall = time.monotonic(), time.time()
    offset = [0.0]

    @signature
    def clock(pointer, _client):
        now = anchor_wall + time.monotonic() - anchor_mono + offset[0]
        pointer.contents.sec = int(now)
        pointer.contents.usec = int((now - int(now)) * 1_000_000)

    library.Tcl_SetTimeProc(clock, scale, client)
    try:
        yield offset
    finally:
        library.Tcl_SetTimeProc(original, scale, client)


def pump_until(gui, predicate, seconds=2):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        gui.root.update()
        time.sleep(0.005)
    assert predicate()


@pytest.fixture
def stable_fallback_clock():
    # This functional check is distinct from the explicit clock-backstep checks.
    # Tcl's process-local hook prevents ambient wall-clock adjustments from
    # changing a timer's deadline; the product and two-second oracle are unchanged.
    with virtual_tcl_clock() if os.name == "posix" else nullcontext():
        yield


@pytest.mark.skipif(os.name != "posix", reason="Unix Tcl file events and time API")
def test_completed_scan_is_delivered_during_a_backward_tcl_clock_step(variant, tmp_path, monkeypatch):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def scan(paths, _root):
        names = tuple(path.name for path in paths)
        started.set()
        assert release.wait(5)
        completed.set()
        return dict.fromkeys(names, True)

    monkeypatch.setattr(catalog, "cached_model_states", scan)
    base = variant._APP.base
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    try:
        gui.root.withdraw()
        with virtual_tcl_clock() as offset:
            gui._qubitz_refresh_variant_cache()
            assert started.wait(2)
            timer_fired = []
            gui.root.after(50, lambda: timer_fired.append(True))
            offset[0] = -20.0
            release.set()
            assert completed.wait(2)
            pump_until(gui, lambda: not gui._qubitz_variant_cache_refresh_pending)
            assert not timer_fired, "Control timer should still be blocked by the virtual backstep"
            assert len(gui._qubitz_variant_cache_state) == 40
            assert all(value is True for value in gui._qubitz_variant_cache_state.values())
    finally:
        release.set()
        gui.agent.reset_runtime()
        gui.root.destroy()


@pytest.mark.skipif(os.name != "posix", reason="Unix file descriptor inventory")
def test_cache_notifier_closes_when_gui_is_destroyed_during_scan(variant, tmp_path, monkeypatch, pipe_lifetimes):
    started, release, completed = threading.Event(), threading.Event(), threading.Event()

    def scan(paths, _root):
        started.set()
        assert release.wait(5)
        completed.set()
        return dict.fromkeys((path.name for path in paths), False)

    monkeypatch.setattr(catalog, "cached_model_states", scan)
    base = variant._APP.base
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    gui.root.withdraw()
    gui._qubitz_refresh_variant_cache()
    assert started.wait(2)
    gui.agent.reset_runtime()
    gui.root.destroy()
    try:
        pipe_lifetimes.assert_closed()
    finally:
        release.set()
        assert completed.wait(2)
        for thread in threading.enumerate():
            if thread.name == "qubitz-variant-cache":
                thread.join(2)
                assert not thread.is_alive()


@pytest.mark.parametrize("failure", ["unavailable", "pipe", "registration"])
def test_cache_delivery_timer_fallback(variant, tmp_path, monkeypatch, failure, stable_fallback_clock, pipe_lifetimes):
    original_pipe = os.pipe

    def pipe():
        if failure == "pipe":
            raise OSError("fixture pipe refusal")
        pair = original_pipe()
        return pair

    def registration(*_args):
        raise tk.TclError("fixture file event refusal")

    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(tk.Tk, "createfilehandler",
                        None if failure == "unavailable" else registration, raising=False)
    monkeypatch.setattr(catalog, "cached_model_states",
                        lambda paths, _root: dict.fromkeys((path.name for path in paths), True))
    base = variant._APP.base
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    try:
        gui.root.withdraw()
        assert gui._qubitz_variant_cache_delivery == "timer"
        gui._qubitz_refresh_variant_cache()
        pump_until(gui, lambda: not gui._qubitz_variant_cache_refresh_pending)
        assert len(gui._qubitz_variant_cache_state) == 40
        assert all(value is True for value in gui._qubitz_variant_cache_state.values())
        if failure == "registration":
            pipe_lifetimes.assert_closed()
        else:
            assert not pipe_lifetimes.records
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()


@pytest.mark.skipif(os.name != "posix", reason="Process-local Unix Tcl time API")
def test_timer_fallback_waits_for_clock_recovery_not_worker_completion(variant, tmp_path, monkeypatch):
    completed = threading.Event()

    def scan(paths, _root):
        result = dict.fromkeys((path.name for path in paths), True)
        completed.set()
        return result

    monkeypatch.setattr(catalog, "cached_model_states", scan)
    monkeypatch.setattr(tk.Tk, "createfilehandler", None, raising=False)
    with virtual_tcl_clock() as offset:
        base = variant._APP.base
        gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
        try:
            gui.root.withdraw()
            assert gui._qubitz_variant_cache_delivery == "timer"
            gui._qubitz_refresh_variant_cache()
            assert completed.wait(2)
            control_fired = []
            gui.root.after(50, lambda: control_fired.append(True))
            offset[0] = -20.0
            deadline = time.monotonic() + 0.1
            while time.monotonic() < deadline:
                gui.root.update()
                time.sleep(0.005)
            assert gui._qubitz_variant_cache_refresh_pending
            assert not control_fired
            offset[0] = 0.0
            pump_until(gui, lambda: not gui._qubitz_variant_cache_refresh_pending)
            assert control_fired
            assert len(gui._qubitz_variant_cache_state) == 40
            assert all(value is True for value in gui._qubitz_variant_cache_state.values())
        finally:
            gui.agent.reset_runtime()
            gui.root.destroy()
