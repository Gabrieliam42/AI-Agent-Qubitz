"""Carry an existing turn allowance through transport layers, without extending it."""
from __future__ import annotations

import math
from contextlib import contextmanager
from contextvars import ContextVar

_REMAINING = ContextVar("qubitz_request_budget", default=None)


def request_timeout(default: float = 600.0, *, snapshot=None) -> float:
    provider = snapshot if callable(snapshot) else _REMAINING.get()
    remaining = default
    if callable(provider):
        state = provider()
        remaining = state["remaining_phase_seconds"]
        if state.get("expired"):
            raise TimeoutError("The existing turn deadline expired before request dispatch")
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or not math.isfinite(remaining) or remaining <= 0:
        raise ValueError("A finite positive remaining request allowance is required")
    return float(remaining)


@contextmanager
def request_budget(snapshot):
    token = _REMAINING.set(snapshot if callable(snapshot) else None)
    try:
        yield
    finally:
        _REMAINING.reset(token)
