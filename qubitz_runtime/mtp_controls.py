"""Optional variant-owned MTP selection; never acquire files or mutate the environment."""

import copy as _mtp_copy
import sys as _mtp_sys
import threading as _mtp_threading


def mtp_control_status(base, config):
    provider = getattr(base, "qubitz_mtp_status", None)
    if not callable(provider):
        return {"supported": False, "enabled": None, "available": False,
                "detail": "MTP is managed by this variant; no validated toggle is available."}
    return provider(config)


def configured_mtp(base, config, enabled):
    if type(enabled) is not bool:
        raise ValueError("MTP selection must be boolean.")
    status = mtp_control_status(base, config)
    if not status["supported"]:
        raise ValueError("This variant does not expose a validated MTP toggle.")
    candidate = _mtp_copy.copy(config)
    if enabled == status["enabled"]:
        return candidate
    base.qubitz_configure_mtp(candidate, enabled)
    return candidate


def reset_mtp_runner(runner):
    """Do not allow a replacement backend after missing or uncertain owned cleanup."""
    manager = getattr(getattr(runner, "_llm", None), "server_process", None)
    owned = getattr(manager, "_qubitz_owned_launch_epoch", None) is not None
    runner.reset_runtime()
    outcome = getattr(manager, "_qubitz_cleanup_outcome", None)
    if (owned and not outcome) or (outcome and outcome.get("status") not in {"stopped", "recovered_after_proxy_close"}):
        raise RuntimeError("MTP change held: the owned backend did not confirm cleanup; no restart was attempted.")
    return dict(outcome or {})


class MTPGuiControl:
    """Keep Tk work on its owning thread and backend shutdown off that thread."""

    def __init__(self, gui, base):
        self.gui, self.base = gui, base
        self.transition = None
        self.closing = False
        self.selection = getattr(gui.config, "mtp_enabled", None)
        self.original_busy = gui._set_busy
        self.original_close = gui._handle_close
        controls = gui.temperature_entry.master
        gui.ttk.Label(controls, text="MTP").pack(side="left", padx=(16, 0))
        self.variable = gui.tk.StringVar(master=gui.root)
        self.selector = gui.ttk.Combobox(controls, textvariable=self.variable, values=("Off", "On"), width=5, state="readonly")
        self.selector.pack(side="left", padx=(6, 0))
        self.selector.bind("<<ComboboxSelected>>", self.change)
        self.detail = gui.ttk.Label(controls)
        self.detail.pack(side="left", padx=(6, 0))
        gui._set_busy = self.set_busy
        gui._handle_close = self.close
        gui.root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh()

    def refresh(self):
        status = mtp_control_status(self.base, self.gui.config)
        self.variable.set(("On" if status["enabled"] else "Off") if status["supported"] else "N/A")
        self.detail.configure(text=status.get("label", "variant-managed"))
        disabled = bool(self.gui.busy or self.transition or not status["supported"])
        self.selector.configure(state="disabled" if disabled else "readonly")

    def set_busy(self, value):
        self.original_busy(value)
        self.refresh()

    def sync(self):
        # Workspace switches use dataclasses.replace and omit dynamic fields.
        if self.selection is not None:
            for config in (self.gui.config, self.gui.agent.config):
                config.mtp_enabled = self.selection

    def change(self, _event=None):
        gui = self.gui
        enabled = self.variable.get() == "On"
        if (gui.busy or self.transition or self.closing or getattr(gui, "pending_prompts", ())
                or getattr(gui, "_qubitz_close_started", False)
                or getattr(gui, "_qubitz_workspace_transition", None) is not None
                or getattr(gui, "_qubitz_variant_switch_in_progress", False)):
            self.refresh()
            return
        try:
            candidate = configured_mtp(self.base, gui.config, enabled)
        except (ValueError, RuntimeError, OSError) as exc:
            self.refresh()
            gui.messagebox.showerror("MTP selection unavailable", str(exc))
            return
        if mtp_control_status(self.base, gui.config)["enabled"] == enabled:
            self.refresh()
            return
        old_agent = gui.agent
        state = {"finished": _mtp_threading.Event(), "agent": None, "error": None,
                 "cleanup_started": False, "config": candidate}
        self.transition = state
        # Existing send/workspace guards also protect this owned transition.
        gui._qubitz_workspace_transition = state
        gui.pending_prompts.clear()
        gui._set_busy(True)
        gui.status_var.set("Stopping backend to change MTP...")
        gui._append_transcript("status", "Changing MTP; waiting for verified owned backend cleanup. No model will start yet.")

        def prepare():
            try:
                state["agent"] = self.base.AgentRunner(candidate)
                state["cleanup_started"] = True
                state["cleanup"] = reset_mtp_runner(old_agent)
            except Exception as exc:  # noqa: BLE001 - publish transition failures on the Tk thread.
                state["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                state["finished"].set()

        _mtp_threading.Thread(target=prepare, name="qubitz-mtp-transition", daemon=True).start()
        gui.root.after(0, self.finish)

    def finish(self):
        state, gui = self.transition, self.gui
        if state is None or self.closing:
            return
        if not state["finished"].is_set():
            gui.root.after(50, self.finish)
            return
        if state["error"]:
            gui.status_var.set("MTP cleanup held; close the application." if state["cleanup_started"] else "Ready")
            gui._append_transcript("error", state["error"])
            gui.messagebox.showerror("MTP change failed", state["error"])
            if not state["cleanup_started"]:
                self.transition = None
                gui._qubitz_workspace_transition = None
                gui._set_busy(False)
            return
        self.commit()

    def commit(self):
        state, gui = self.transition, self.gui
        gui.config, gui.agent = state["config"], state["agent"]
        self.selection = gui.config.mtp_enabled
        gui._qubitz_mtp_cleanup_receipt = state["cleanup"]
        self.transition = None
        gui._qubitz_workspace_transition = None
        gui.pending_prompts.clear()
        gui.cancel_requested = False
        gui._set_busy(False)
        gui.status_var.set("Ready")
        gui._append_transcript("status", f"MTP {'On' if self.selection else 'Off'} selected. Backend starts on the next task; transcript retained.")

    def close(self):
        state, gui = self.transition, self.gui
        if state is None:
            self.original_close()
            return
        self.closing = True
        gui.root.withdraw()
        if not state["finished"].is_set():
            gui.root.after(50, self.close)
            return
        if state["error"] and state["cleanup_started"]:
            # Unknown shutdown is not retried by the ordinary GUI close path.
            print("[warning] MTP transition cleanup remains unverified: " + state["error"], file=_mtp_sys.stderr)
            gui.root.destroy()
            return
        if not state["error"]:
            self.commit()
        else:
            self.transition = None
            gui._qubitz_workspace_transition = None
        self.original_close()
