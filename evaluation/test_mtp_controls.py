"""Model-free MTP configuration, Tk controls and browser lifecycle boundaries."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from types import SimpleNamespace

import pytest

from evaluation import test_browser_interface as browser
from runtime.mtp_controls import (
    MTPGuiControl,
    configured_mtp,
    mtp_control_status,
    reset_mtp_runner,
)
from runtime.web_controller import SessionConflict

session = browser.session


def provider():
    def status(config):
        return {"supported": True, "enabled": bool(getattr(config, "mtp_enabled", False)),
                "available": True, "label": "fixture Q4", "detail": "Fixture; no model loading."}

    def configure(config, enabled):
        config.mtp_enabled = enabled
        config.llama_server_path = "fixture-on" if enabled else "fixture-off"

    return SimpleNamespace(qubitz_mtp_status=status, qubitz_configure_mtp=configure)


def test_unsupported_variant_is_not_reinterpreted_as_mtp_off():
    base, config = SimpleNamespace(), SimpleNamespace()
    assert mtp_control_status(base, config)["enabled"] is None
    with pytest.raises(ValueError, match="validated MTP toggle"):
        configured_mtp(base, config, False)


@pytest.mark.parametrize("value", [None, 0, 1, "On", [], {}])
def test_selection_requires_exact_boolean(value):
    with pytest.raises(ValueError, match="boolean"):
        configured_mtp(provider(), SimpleNamespace(), value)


def test_selection_is_copied_and_noop_preserves_explicit_existing_mode():
    original = SimpleNamespace(mtp_enabled=False, custom="retained")
    changed = configured_mtp(provider(), original, True)
    assert changed is not original and changed.mtp_enabled and changed.custom == "retained"
    assert original.mtp_enabled is False and not hasattr(original, "llama_server_path")
    assert configured_mtp(provider(), changed, True).llama_server_path == "fixture-on"


@pytest.mark.parametrize("outcome", [None, {"status": "failed"}, {"status": "unknown"}])
def test_owned_cleanup_requires_positive_receipt(outcome):
    manager = SimpleNamespace(_qubitz_owned_launch_epoch=1, _qubitz_cleanup_outcome=outcome)
    runner = SimpleNamespace(_llm=SimpleNamespace(server_process=manager), reset_runtime=lambda: None)
    with pytest.raises(RuntimeError, match="confirm cleanup"):
        reset_mtp_runner(runner)


def test_browser_mode_survives_workspace_config_and_resets_before_commit(session):
    value, _, _, _ = session
    base = value.namespace["_APP"].base
    hooks = provider()
    base.qubitz_mtp_status, base.qubitz_configure_mtp = hooks.qubitz_mtp_status, hooks.qubitz_configure_mtp
    original = value.config
    observations = []
    value.runner = SimpleNamespace(_llm=None, reset_runtime=lambda: observations.append(value.config is original))
    value.configure({"mtp_enabled": True})
    assert observations == [True] and not hasattr(original, "mtp_enabled")
    assert value.snapshot()["settings"]["mtp_enabled"] is True
    value.configure({"temperature": 0.8}, workspace=str(value.config.workspace))
    assert value.config.mtp_enabled and value.config.llama_server_path == "fixture-on"
    value.configure({"mtp_enabled": False})
    assert value.snapshot()["settings"]["mtp_enabled"] is False


def test_browser_refuses_unsupported_or_busy_mtp_changes(session):
    value, _, entered, release = session
    before = value.config
    with pytest.raises(ValueError, match="validated MTP toggle"):
        value.configure({"mtp_enabled": True})
    assert value.config is before and value.phase == "ready"
    value.submit("fixture", "a" * 32)
    assert entered.wait(2)
    with pytest.raises(SessionConflict):
        value.configure({"mtp_enabled": True})
    release.set()
    browser.finish(value)


@pytest.fixture
def gui():
    root = tk.Tk()
    root.withdraw()
    errors, closed, resets = [], [], []
    base = provider()

    class Runner:
        def __init__(self, config):
            self.config, self._llm = config, None

        def reset_runtime(self):
            resets.append(self)

    base.AgentRunner = Runner
    config = SimpleNamespace(mtp_enabled=False)
    frame = ttk.Frame(root)
    frame.pack()
    entry = ttk.Entry(frame)
    entry.pack(side="left")
    subject = SimpleNamespace(root=root, config=config, agent=Runner(config), tk=tk, ttk=ttk,
                              temperature_entry=entry, busy=False, pending_prompts=[], cancel_requested=False,
                              status_var=tk.StringVar(root), messages=[],
                              messagebox=SimpleNamespace(showerror=lambda *args: errors.append((threading.get_ident(), args))))
    subject._append_transcript = lambda *args: subject.messages.append(args)
    subject._set_busy = lambda value: setattr(subject, "busy", value)
    subject._handle_close = lambda: closed.append(True)
    control = MTPGuiControl(subject, base)
    try:
        yield subject, base, control, errors, closed, resets
    finally:
        if control.transition:
            control.transition["finished"].wait(3)
        try:
            root.destroy()
        except tk.TclError:
            pass


def drive(gui, predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "Tk transition did not finish"
        gui.root.update()
        time.sleep(0.005)


def test_tk_toggle_replaces_only_after_cleanup_and_stays_next_to_temperature(gui):
    subject, _, control, errors, _, resets = gui
    old = subject.agent
    assert control.selector.master is subject.temperature_entry.master
    control.variable.set("On")
    control.change()
    drive(subject, lambda: control.transition is None)
    assert resets == [old] and subject.agent is not old
    assert subject.config.mtp_enabled and control.variable.get() == "On" and not subject.busy
    assert not errors
    # Simulate the existing dataclasses.replace workspace transition omitting dynamic preferences.
    subject.config, subject.agent.config = SimpleNamespace(), SimpleNamespace()
    control.sync()
    assert subject.config.mtp_enabled and subject.agent.config.mtp_enabled


def test_tk_busy_toggle_is_disabled_and_cannot_mutate_mode(gui):
    subject, _, control, _, _, resets = gui
    subject._set_busy(True)
    assert str(control.selector.cget("state")) == "disabled"
    control.variable.set("On")
    control.change()
    assert not subject.config.mtp_enabled and not resets and control.variable.get() == "Off"


def test_tk_failed_cleanup_is_held_and_not_retried_on_close(gui, capsys):
    subject, _, control, errors, closed, resets = gui
    subject.agent._llm = SimpleNamespace(server_process=SimpleNamespace(
        _qubitz_owned_launch_epoch=1, _qubitz_cleanup_outcome={"status": "failed"}))
    old = subject.agent
    control.variable.set("On")
    control.change()
    drive(subject, lambda: bool(errors))
    assert subject.busy and not subject.config.mtp_enabled and subject.agent is old
    assert errors[0][0] == threading.get_ident()
    control.close()
    assert resets == [old] and not closed
    assert "cleanup remains unverified" in capsys.readouterr().err


def test_tk_close_waits_for_transition_instead_of_double_stopping(gui):
    subject, _, control, _, closed, resets = gui
    entered, release = threading.Event(), threading.Event()
    old = subject.agent

    def wait_for_reset():
        resets.append(old)
        entered.set()
        assert release.wait(2)

    old.reset_runtime = wait_for_reset
    control.variable.set("On")
    control.change()
    assert entered.wait(2)
    control.close()
    assert not closed
    release.set()
    drive(subject, lambda: bool(closed))
    assert resets == [old] and subject.agent is not old
