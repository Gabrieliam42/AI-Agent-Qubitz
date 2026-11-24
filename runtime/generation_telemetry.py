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
class DecodeSpeedSummary:
    """Token-weighted distribution; logarithmic bins bound storage, about 1% wide."""

    buckets: dict[int, list[float]] = field(default_factory=dict)
    peak: float = 0.0

    def add(self, tokens: float, seconds: float) -> None:
        tokens, seconds = positive(tokens), positive(seconds)
        rate = positive(tokens / seconds) if seconds else 0.0
        if not rate:
            return
        key = round(math.log2(rate) * 64)
        bucket = self.buckets.setdefault(key, [0.0, 0.0])
        bucket[0] += tokens
        bucket[1] += seconds
        self.peak = max(self.peak, rate)

    def merge(self, other: DecodeSpeedSummary) -> None:
        for key, values in other.buckets.items():
            bucket = self.buckets.setdefault(key, [0.0, 0.0])
            bucket[0] += values[0]
            bucket[1] += values[1]
        self.peak = max(self.peak, other.peak)

    @property
    def tokens(self) -> float:
        return sum(values[0] for values in self.buckets.values())

    @property
    def seconds(self) -> float:
        return sum(values[1] for values in self.buckets.values())

    def median(self) -> float | None:
        target = self.tokens / 2
        cumulative = 0.0
        for _key, (tokens, seconds) in sorted(self.buckets.items()):
            cumulative += tokens
            if cumulative >= target:
                return positive(tokens / seconds) or None
        return None


def completion_speed(metrics: dict) -> str:
    average = positive(metrics.get("tokens_per_second"))
    typical = positive(metrics.get("typical_tokens_per_second"))
    if not average:
        return "Token speed: unavailable"
    if typical:
        return f"Token speed: {typical:.2f} tokens/s typical; {average:.2f} tokens/s average"
    return f"Token speed: {average:.2f} tokens/s average"


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
    decode_rates: DecodeSpeedSummary = field(default_factory=DecodeSpeedSummary)
    decode_anchor: tuple[float, float] | None = None
    decode_last: tuple[float, float] | None = None
    decode_valid: bool = True

    def _decode_interval(self, count: float, seconds: float, final: bool) -> None:
        if not self.decode_valid or not count or not seconds:
            return
        if self.decode_last is not None and (
            count < self.decode_last[0] or seconds < self.decode_last[1]
        ):
            self.decode_valid = False
            self.decode_rates = DecodeSpeedSummary()
            return
        self.decode_last = count, seconds
        if self.decode_anchor is None:
            # A final aggregate alone contains no interval/peak distribution.
            if final:
                return
            self.decode_anchor = 0.0, 0.0
        delta_tokens = count - self.decode_anchor[0]
        delta_seconds = seconds - self.decode_anchor[1]
        if delta_tokens > 0 and (delta_seconds >= 5 or final and delta_seconds >= 1):
            self.decode_rates.add(delta_tokens, delta_seconds)
            self.decode_anchor = count, seconds

    def update(
        self, timings: Any = None, *, phase: str = "generating", final: bool = False
    ) -> dict:
        now = self.clock()
        timing = timings if isinstance(timings, dict) else {}
        count = positive(timing.get("predicted_n"))
        seconds = positive(timing.get("predicted_ms")) / 1000
        self._decode_interval(count, seconds, final)
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


def backend_supports_prompt_progress(settings: Any, build: str) -> bool:
    params = settings.get("params") if isinstance(settings, dict) else None
    if isinstance(params, dict) and type(params.get("return_progress")) is bool:
        return True
    # These source-verified builds implement return_progress, but /props only
    # serializes sampling metrics and omits that flag.
    return any(part in {"6fcaa16f", "6fcaa16f4b360649933a54d1f91ad40ed35c0e11",
                         "b1b770d9", "b1b770d90d94cad9e9ea5b993bdbc383a522fb28"}
               for part in build.lower().replace("(", " ").replace(")", " ").replace("-", " ").split())


def prompt_progress(event: Any) -> tuple[int, int] | None:
    report = event.get("prompt_progress") if isinstance(event, dict) else None
    if not isinstance(report, dict):
        return None
    processed, total = report.get("processed"), report.get("total")
    if type(processed) is int and type(total) is int and 0 <= processed <= total and total > 0:
        return processed, total
    return None


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
