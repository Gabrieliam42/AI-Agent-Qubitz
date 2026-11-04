"""Evaluator-only active/startup clocks; runtime settings and recovery budgets stay fixed."""
from __future__ import annotations

import math
import threading
import time


class TurnBudget:
    def __init__(self, active_seconds: float, startup_seconds: float = 0, *, clock=time.monotonic):
        if not math.isfinite(active_seconds) or active_seconds <= 0 or not math.isfinite(startup_seconds) or startup_seconds < 0:
            raise ValueError("Finite positive active and nonnegative startup budgets required")
        self.active_limit = active_seconds
        self.startup_limit = startup_seconds
        self.clock = clock
        self.started = clock()
        self.loading_started = None
        self.loading_finished = None
        self.ready = False
        self.lock = threading.RLock()

    def observe(self, kind: str, message: str, *, at: float | None = None):
        if kind != "status":
            return
        with self.lock:
            now = self.clock()
            instant = now if at is None else min(now, max(self.started, at))
            if message == "Detecting the local llama.cpp GGUF backend and validating the target model.":
                if not self.ready and self.loading_started is None and self.startup_limit:
                    self.loading_started = instant
            elif (message.startswith("llama.cpp model ready:") or message == "Starting model loop.") and not self.ready:
                self.ready = True
                self.loading_finished = instant

    def snapshot(self) -> dict:
        with self.lock:
            now = self.clock()
            loading = 0 if self.loading_started is None else max(
                0, (self.loading_finished if self.loading_finished is not None else now) - self.loading_started,
            )
            active = max(0, now - self.started - loading)
            phase = "startup" if self.loading_started is not None and self.loading_finished is None else "active_turn"
            expired = active >= self.active_limit or (self.loading_started is not None and loading >= self.startup_limit)
            return {"phase": phase, "active_elapsed_seconds": active, "startup_elapsed_seconds": loading,
                    "active_limit_seconds": self.active_limit, "startup_limit_seconds": self.startup_limit,
                    "remaining_phase_seconds": max(0, (self.startup_limit - loading) if phase == "startup"
                                                     else self.active_limit - active),
                    "expired": expired, "clock": "monotonic", "is_eta": False,
                    "gui_health": "Clock progress is not native presentation or input evidence."}

    def expired(self) -> bool:
        return self.snapshot()["expired"]
