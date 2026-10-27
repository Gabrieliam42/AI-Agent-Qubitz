"""GUI FIFO logging helpers; state belongs to each GUI, never this module.

This ordinary module is also composed verbatim into standalone variant exports.
Private import aliases keep it independent from each variant's patched globals.
"""
import json as _gui_log_json
import os as _gui_log_os
import queue as _gui_log_queue
import threading as _gui_log_threading
import time as _gui_log_time
from typing import Any as _GuiLogOwner


def _qubitz_gui_log_enqueue(owner: _GuiLogOwner, variant: str, event_name: str,
                            details: dict[str, _GuiLogOwner]) -> None:
    with owner._qubitz_gui_log_lock:
        if owner._qubitz_gui_watchdog_stop.is_set() or owner._qubitz_gui_log_error:
            return
        owner._qubitz_gui_log_sequence += 1
        utc_seconds = _gui_log_time.time()
        payload = {
            **details,
            "timestamp": _gui_log_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timestamp_utc": _gui_log_time.strftime("%Y-%m-%dT%H:%M:%SZ", _gui_log_time.gmtime(utc_seconds)),
            "utc_unix_seconds": utc_seconds,
            "monotonic_seconds": _gui_log_time.monotonic(),
            "gui_log_session": owner._qubitz_gui_log_session,
            "sequence": owner._qubitz_gui_log_sequence,
            "event": event_name,
            "variant": variant,
            "pid": _gui_log_os.getpid(),
        }
        try:
            owner._qubitz_gui_log_queue.put_nowait(payload)
        except _gui_log_queue.Full:
            owner._qubitz_gui_log_dropped += 1


def _qubitz_gui_log_write(owner: _GuiLogOwner) -> None:
    try:
        with owner._qubitz_gui_log_path.open("a", encoding="utf-8", buffering=1) as handle:
            while True:
                try:
                    item = owner._qubitz_gui_log_queue.get(timeout=0.2)
                except _gui_log_queue.Empty:
                    if owner._qubitz_gui_watchdog_stop.is_set():
                        # Stop and the empty-queue check share the producer lock.
                        with owner._qubitz_gui_log_lock:
                            if owner._qubitz_gui_log_queue.empty():
                                handle.flush()
                                owner._qubitz_gui_log_flushed_bytes = handle.tell()
                                owner._qubitz_gui_log_closed.set()
                                return
                    continue
                try:
                    if item is None:
                        handle.flush()
                        owner._qubitz_gui_log_flushed_bytes = handle.tell()
                        owner._qubitz_gui_log_closed.set()
                        return
                    if isinstance(item, tuple):
                        handle.flush()
                        owner._qubitz_gui_log_flushed_bytes = handle.tell()
                        item[1].set()
                    else:
                        handle.write(_gui_log_json.dumps(item, ensure_ascii=True, default=str) + "\n")
                finally:
                    owner._qubitz_gui_log_queue.task_done()
    except OSError as exc:
        owner._qubitz_gui_log_error = str(exc)


def _qubitz_gui_log_flush(owner: _GuiLogOwner, timeout: float = 2.0) -> bool:
    deadline = _gui_log_time.monotonic() + max(0.0, timeout)
    acknowledged = _gui_log_threading.Event()
    if owner._qubitz_gui_log_error:
        return False
    if owner._qubitz_gui_watchdog_stop.is_set() or not owner._qubitz_gui_log_writer.is_alive():
        owner._qubitz_gui_log_writer.join(timeout=max(0.0, deadline - _gui_log_time.monotonic()))
        return owner._qubitz_gui_log_closed.is_set() and not owner._qubitz_gui_log_error
    try:
        owner._qubitz_gui_log_queue.put(("flush", acknowledged), timeout=max(0.0, deadline - _gui_log_time.monotonic()))
    except _gui_log_queue.Full:
        return False
    return acknowledged.wait(max(0.0, deadline - _gui_log_time.monotonic())) and not owner._qubitz_gui_log_error


def _qubitz_gui_log_stop(owner: _GuiLogOwner, event: _GuiLogOwner) -> None:
    if getattr(event, "widget", None) is not owner.root:
        return
    with owner._qubitz_gui_log_lock:
        owner._qubitz_gui_watchdog_stop.set()
        try:
            owner._qubitz_gui_log_queue.put_nowait(None)
        except _gui_log_queue.Full:
            pass
