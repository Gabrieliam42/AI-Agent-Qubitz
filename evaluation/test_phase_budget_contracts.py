"""Synthetic monotonic clocks: no loading, inference or runtime-default changes."""
from __future__ import annotations

import pytest

from evaluation.phase_budget import TurnBudget

LOADING = "Detecting the local llama.cpp GGUF backend and validating the target model."
READY = "Starting model loop."


def test_unused_startup_allowance_does_not_extend_active_work():
    now = [0.0]
    budget = TurnBudget(30, 120, clock=lambda: now[0])
    now[0] = 31
    assert budget.expired()
    assert budget.snapshot()["startup_elapsed_seconds"] == 0


def test_only_loading_consumes_startup_clock():
    now = [0.0]
    budget = TurnBudget(30, 120, clock=lambda: now[0])
    now[0] = 4
    budget.observe("status", LOADING)
    now[0] = 100
    assert not budget.expired()
    budget.observe("status", READY)
    now[0] = 126
    assert budget.expired()
    assert budget.snapshot()["active_elapsed_seconds"] == 30


def test_loading_timeout_does_not_reset_after_late_ready_event():
    now = [0.0]
    budget = TurnBudget(30, 120, clock=lambda: now[0])
    budget.observe("status", LOADING)
    now[0] = 121
    budget.observe("status", READY)
    assert budget.expired()


def test_recovery_cannot_open_another_loading_allowance():
    now = [0.0]
    budget = TurnBudget(30, 120, clock=lambda: now[0])
    budget.observe("status", READY)
    now[0] = 20
    budget.observe("status", LOADING)
    now[0] = 31
    assert budget.expired()
    assert budget.snapshot()["startup_elapsed_seconds"] == 0


def test_reasoning_text_cannot_change_deadline():
    now = [0.0]
    budget = TurnBudget(30, 120, clock=lambda: now[0])
    budget.observe("reasoning", LOADING)
    now[0] = 31
    assert budget.expired()


@pytest.mark.parametrize("active,startup", [(0, 1), (1, -1), (float('nan'), 0), (1, float('inf'))])
def test_invalid_limits_are_rejected(active, startup):
    with pytest.raises(ValueError):
        TurnBudget(active, startup)


def test_gui_drive_uses_phase_deadline_not_unused_startup_allowance():
    import tmp_smoke_variant as smoke

    now = [0.0]
    budget = TurnBudget(3, 120, clock=lambda: now[0])

    class Root:
        stopped = False

        def after(self, _delay, callback):
            self.callback = callback
            return "fixture-timer"

        def after_cancel(self, _identifier):
            pass

        def quit(self):
            self.stopped = True

        def mainloop(self):
            while not self.stopped:
                now[0] += 1
                self.callback()

    root = Root()
    assert not smoke._drive(root, lambda: False, 123, timeout_guard=budget)
    assert now[0] == 3 and root._smoke_drive_exit_reason == "deadline"
