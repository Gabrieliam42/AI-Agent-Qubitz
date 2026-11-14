"""Request-scoped, backend-counted decode telemetry without interpreting model text."""

from __future__ import annotations

import json
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


def positive(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


@dataclass
class LiveTokenMeter:
    request_id: str
    emit: Callable[[str, str], None] | None = None
    clock: Callable[[], float] = time.monotonic
    window_seconds: float = 5.0
    samples: deque = field(default_factory=deque)
    last_emit: float = float("-inf")
    last_count: float = 0.0
    display_error: str = ""

    def update(
        self, timings: Any = None, *, phase: str = "generating", final: bool = False
    ) -> dict:
        now = self.clock()
        timing = timings if isinstance(timings, dict) else {}
        count = positive(timing.get("predicted_n"))
        seconds = positive(timing.get("predicted_ms")) / 1000
        rate = None
        if count and count >= self.last_count:
            if count > self.last_count:
                self.samples.append((now, count))
            self.last_count = count
            while (
                len(self.samples) > 2 and self.samples[1][0] < now - self.window_seconds
            ):
                self.samples.popleft()
            if final and seconds:
                rate = count / seconds
            elif len(self.samples) > 1:
                elapsed = now - self.samples[0][0]
                if elapsed >= 1:
                    rate = (count - self.samples[0][1]) / elapsed
        result = {
            "request_id": self.request_id,
            "phase": phase,
            "tokens": self.last_count,
            "tok_s": rate,
            "source": "backend_token_counter",
            "final": final,
        }
        if callable(self.emit) and (final or now - self.last_emit >= 0.25):
            self.last_emit = now
            try:
                self.emit("generation_progress", json.dumps(result, allow_nan=False))
            except Exception as error:  # noqa: BLE001 - Display telemetry cannot change inference or tool execution.
                self.display_error = type(error).__name__
        return result


def display_rate(message: str) -> str:
    try:
        event = json.loads(message)
    except (ValueError, TypeError):
        return "-- tok/s"
    if not isinstance(event, dict):
        return "-- tok/s"
    rate = positive(event.get("tok_s"))
    if event.get("phase") not in {"generating", "complete"} or not rate:
        return "-- tok/s"
    return f"{rate:.2f} tok/s"
