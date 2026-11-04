"""Cancellation provenance without retrying a possibly side-effecting callback."""
from __future__ import annotations

import asyncio
import inspect
from enum import StrEnum


class CancelOrigin(StrEnum):
    USER = "user"
    FIXTURE = "fixture"
    TIMEOUT = "evaluator_timeout"
    CAMPAIGN = "coordinator_stop"
    CONTROL = "evaluation_control_error"
    PERMISSION = "permission_change"
    THERMAL = "thermal_abort"
    COORDINATOR = "coordinator_exception"
    EXTERNAL = "external"
    SHUTDOWN = "shutdown"


def cancellation_origin(value: str) -> CancelOrigin:
    return CancelOrigin("evaluator_timeout" if value == "evaluation_timeout" else value)


class RequestCancelled(asyncio.CancelledError):
    def __init__(self, origin: str = "external") -> None:
        self.origin = cancellation_origin(origin)
        super().__init__(f"Request cancelled: {self.origin.value}")


def request_cancellation(runner, origin: str) -> None:
    """Inspect the API before calling it; a TypeError must never trigger replay."""
    origin = cancellation_origin(origin).value
    callback = runner.request_cancel
    parameters = inspect.signature(callback).parameters
    if "origin" in parameters:
        callback(origin=origin)
    else:
        runner._cancel_origin = origin
        callback()


def stop_description(origin: str) -> str:
    return {
        "user": "Stopped at the user's request.",
        "fixture": "Stopped by the authorized fixture intervention.",
        "evaluator_timeout": "Stopped at the evaluator timeout.",
        "coordinator_stop": "Stopped by the campaign coordinator.",
        "thermal_abort": "Stopped by the thermal safety controller.",
    }.get(origin, f"Stopped by {origin or 'external cancellation'}.")
