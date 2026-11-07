"""Monotonic request observations, independent of model prose and wall clocks."""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar

_TIMING = ContextVar("qubitz_transport_timing", default=None)


@contextmanager
def request_timings():
    events = {}
    token = _TIMING.set(events)
    try:
        yield events
    finally:
        _TIMING.reset(token)


def observed(event: str) -> None:
    events = _TIMING.get()
    if events is not None and event in {"request", "first_data", "response", "error"}:
        events.setdefault(event, time.perf_counter())


def durations(events: dict) -> dict:
    if "request" not in events:
        return {}
    fields = {"first_data": "first_model_data_seconds", "response": "first_model_response_seconds",
              "error": "model_time_to_error_seconds"}
    return {name: round(max(0.0, events[event] - events["request"]), 4)
            for event, name in fields.items() if event in events}
