"""Fixed-variant evaluator controls must not cancel work or escape private memory."""
from __future__ import annotations

import gc
import sys
import threading
import tkinter as tk
import weakref
from pathlib import Path
from types import SimpleNamespace

import conftest as fixture_support
import pytest
import test_followup_state_receipts as receipts

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix

variant = receipts.variant


@pytest.fixture
def gui(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    monkeypatch.setattr(base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("Loaded a model in a CPU test"))
    instance = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=matrix.ROOT))
    instance.root.withdraw()
    yield instance
    instance.agent.reset_runtime()
    matrix.destroy_gui_once(instance)


def entries(gui):
    menu = gui._qubitz_variant_menu
    return {str(menu.entrycget(index, "label")): index for index in range(int(menu.index("end")) + 1)
            if menu.type(index) == "radiobutton"}


def observer(events):
    return SimpleNamespace(emit=lambda kind, **payload: events.append({"kind": kind, **payload}))


def test_guard_blocks_menu_and_direct_switch_without_touching_active_work(gui, variant, monkeypatch):
    expected = Path(variant.__file__).name
    targets = entries(gui)
    target = next(name for name in targets if name != expected)
    events = []
    pending = list(gui.pending_prompts)
    gui.busy = True
    try:
        with monkeypatch.context() as guarded:
            guarded.setattr(gui, "_request_cancel", lambda: pytest.fail("Variant guard cancelled the active turn"))
            guarded.setattr(gui.agent, "reset_runtime", lambda: pytest.fail("Variant guard reset the runtime"))
            guarded.setattr(variant.os, "execve", lambda *_args: pytest.fail("Variant guard executed another variant"))
            receipt = matrix.guard_variant_selection(gui, expected, observer(events))
            assert receipt == {"fixed_variant": expected, "locked_entries": 37,
                               "selector_state": "disabled", "scope": "evaluator_owned_gui_only"}
            assert all(str(gui._qubitz_variant_menu.entrycget(index, "state")) == "disabled"
                       for index in targets.values())
            gui._qubitz_variant_menu.invoke(targets[target])
            assert [event["kind"] for event in events] == ["variant_selection_locked"]
            gui.variant_selector_var.set(target)
            gui._qubitz_select_variant(target)
            assert gui.variant_selector_var.get() == expected
            assert events[-1] == {"kind": "variant_switch_rejected", "requested_variant": target,
                                  "fixed_variant": expected, "reason": "fixed_evaluation_contract"}
            # Even a re-enabled entry retains the evaluator callback, not the original exec closure.
            gui._qubitz_variant_menu.entryconfigure(targets[target], state="normal")
            gui._qubitz_variant_menu.invoke(targets[target])
            assert events[-1]["kind"] == "variant_switch_rejected"
            gui._qubitz_select_variant(expected)
            assert len(events) == 3
            assert gui.busy and list(gui.pending_prompts) == pending
            assert gui._qubitz_variant_switch_pending is None
            assert not gui._qubitz_variant_switch_in_progress
            heartbeat = []
            gui.root.after(0, lambda: heartbeat.append(True))
            gui.root.update()
            assert heartbeat == [True]
    finally:
        gui.busy = False


@pytest.mark.parametrize("damage", ["selected", "in-progress", "missing-entry", "empty"])
def test_guard_rejects_invalid_contract_before_mutating_controls(gui, variant, damage):
    expected = Path(variant.__file__).name
    if damage == "selected":
        gui.variant_selector_var.set("unexpected.py")
    elif damage == "in-progress":
        gui._qubitz_variant_switch_in_progress = True
    elif damage == "missing-entry":
        gui._qubitz_variant_menu.delete(entries(gui)[expected])
    else:
        gui._qubitz_variant_menu.delete(0, "end")
    original = gui._qubitz_select_variant
    with pytest.raises(RuntimeError, match="variant|selector"):
        matrix.guard_variant_selection(gui, expected, observer([]))
    assert gui._qubitz_select_variant is original
    assert str(gui.variant_selector.cget("state")) == "normal"


def test_normal_product_gui_has_no_evaluator_lock(gui):
    assert str(gui.variant_selector.cget("state")) == "normal"
    assert all(str(gui._qubitz_variant_menu.entrycget(index, "state")) == "normal"
               for index in entries(gui).values())


def test_destroyed_tk_cycles_are_finalized_before_worker_collection(monkeypatch):
    root = tk.Tk()
    root.withdraw()
    variable = tk.StringVar(master=root, value="fixture")
    variable.cycle = variable
    target = id(variable)
    reference = weakref.ref(variable)
    finalized = []
    original = tk.Variable.__del__

    def finalize(value):
        if id(value) == target:
            finalized.append(threading.current_thread())
        original(value)

    monkeypatch.setattr(tk.Variable, "__del__", finalize)
    root.destroy()
    del variable, root
    fixture_support.collect_destroyed_tk_cycles()
    assert reference() is None
    assert finalized == [threading.main_thread()]
    worker = threading.Thread(target=gc.collect)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert finalized == [threading.main_thread()]


def test_tk_cycle_collection_refuses_foreign_threads(monkeypatch):
    monkeypatch.setattr(fixture_support, "_tk_collection_pending", True)
    errors = []

    def collect():
        try:
            fixture_support.collect_destroyed_tk_cycles()
        except RuntimeError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=collect)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert errors == ["Test-owned Tk cycles must be collected on the main thread"]
    assert fixture_support._tk_collection_pending
    fixture_support.collect_destroyed_tk_cycles()
    assert not fixture_support._tk_collection_pending
