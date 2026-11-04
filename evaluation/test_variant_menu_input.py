"""Model-free tests of actual variant-menu input on an isolated Tk display."""
from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import catalog, read_variant_source

variant = receipts.variant
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def gui(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    monkeypatch.setattr(base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("Menu test attempted model loading"))
    monkeypatch.setattr(base.QubitzGUI, "_present_window", lambda _self: None)
    monkeypatch.setattr(base.QubitzGUI, "_schedule_startup_visibility_recovery", lambda _self: None)
    monkeypatch.setattr(catalog, "cached_model_states", lambda paths, _root: dict.fromkeys((p.name for p in paths), False))
    instance = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT))
    original_reset = instance.agent.reset_runtime
    assert instance.agent.memory.workspace.is_relative_to(tmp_path)
    instance.root.update()
    instance.root.withdraw()
    try:
        yield instance
    finally:
        original_reset()
        if instance.root.winfo_exists():
            if instance._qubitz_variant_menu.winfo_exists():
                instance._qubitz_variant_menu.unpost()
                instance._qubitz_variant_menu.grab_release()
            instance.root.destroy()


@pytest.fixture
def switch_trace(variant, gui, monkeypatch):
    trace = []
    monkeypatch.setattr(gui.agent, "reset_runtime", lambda: trace.append(("cleanup",)))
    monkeypatch.setattr(variant.os, "chdir", lambda path: trace.append(("cwd", path)))

    def execute(executable, command, environment):
        trace.append(("exec", executable, command, environment))
        raise OSError("model-free exec boundary")

    monkeypatch.setattr(variant.os, "execve", execute)
    return trace


def radio_entries(gui):
    menu = gui._qubitz_variant_menu
    return [(index, str(menu.entrycget(index, "label")))
            for index in range(int(menu.index("end")) + 1) if menu.type(index) == "radiobutton"]


def other_entry(gui, variant):
    return next(item for item in radio_entries(gui) if item[1] != Path(variant.__file__).name)


def activate_menu(gui, index):
    menu = gui._qubitz_variant_menu
    menu.activate(index)
    gui.root.tk.setvar("::tk::Priv(window)", str(menu))
    return menu


@pytest.mark.parametrize("event", ["<ButtonRelease-4>", "<ButtonRelease-5>"])
def test_wheel_release_cannot_select_or_relaunch(variant, gui, switch_trace, event):
    index, _target = other_entry(gui, variant)
    menu = activate_menu(gui, index)
    current = gui.variant_selector_var.get()
    menu.event_generate(event, when="now")
    gui.root.update()
    assert gui.variant_selector_var.get() == current
    assert not gui._qubitz_variant_switch_in_progress
    assert switch_trace == []


@pytest.mark.parametrize("event,delta,step", [
    ("<Button-4>", None, -1), ("<Button-5>", None, 1),
    ("<MouseWheel>", 120, -1), ("<MouseWheel>", -120, 1),
    ("<MouseWheel>", 0, 0),
])
def test_wheel_browses_only_without_changing_selection(gui, switch_trace, event, delta, step):
    entries = radio_entries(gui)
    position = len(entries) // 2
    menu = activate_menu(gui, entries[position][0])
    current = gui.variant_selector_var.get()
    kwargs = {} if delta is None else {"delta": delta}
    menu.event_generate(event, when="now", **kwargs)
    gui.root.update()
    assert int(menu.index("active")) == entries[position + step][0]
    assert gui.variant_selector_var.get() == current
    assert switch_trace == []


@pytest.mark.parametrize("step", [-1, 1])
def test_wheel_wraps_and_skips_headers_separators_and_disabled_entries(gui, switch_trace, step):
    entries = radio_entries(gui)
    start = entries[0][0] if step < 0 else entries[-1][0]
    skipped = entries[-1][0] if step < 0 else entries[0][0]
    expected = entries[-2][0] if step < 0 else entries[1][0]
    menu = activate_menu(gui, start)
    menu.entryconfigure(skipped, state="disabled")
    current = gui.variant_selector_var.get()
    menu.event_generate("<Button-4>" if step < 0 else "<Button-5>", when="now")
    gui.root.update()
    assert int(menu.index("active")) == expected
    assert gui.variant_selector_var.get() == current and switch_trace == []


@pytest.mark.parametrize("position", ["body", "arrow"])
def test_opening_click_posts_only_after_release_and_never_switches(gui, switch_trace, position):
    button = gui.variant_selector
    x = button.winfo_width() // 2 if position == "body" else max(0, button.winfo_width() - 2)
    y = button.winfo_height() // 2
    current = gui.variant_selector_var.get()
    button.event_generate("<ButtonPress-1>", x=x, y=y, when="now")
    gui.root.update()
    assert not gui._qubitz_variant_menu.winfo_ismapped()
    button.event_generate("<ButtonRelease-1>", x=x, y=y, when="now")
    assert not gui._qubitz_variant_menu.winfo_ismapped()
    gui.root.update()
    assert gui._qubitz_variant_menu.winfo_ismapped()
    assert gui.variant_selector_var.get() == current and switch_trace == []
    assert len(button.master.winfo_children()) == 2  # Existing label and dropdown only.


@pytest.mark.parametrize("condition", ["disabled", "released_outside", "destroyed_before_idle"])
def test_invalid_or_cancelled_opening_does_not_post_or_switch(gui, switch_trace, condition):
    button = gui.variant_selector
    x, y = max(0, button.winfo_width() // 2), max(0, button.winfo_height() // 2)
    if condition == "disabled":
        button.state(("disabled",))
    elif condition == "released_outside":
        x = -1
    button.event_generate("<ButtonRelease-1>", x=x, y=y, when="now")
    if condition == "destroyed_before_idle":
        button.destroy()
    gui.root.update()
    if condition != "destroyed_before_idle":
        assert not gui._qubitz_variant_menu.winfo_ismapped()
    assert switch_trace == []


def test_repeated_opening_release_coalesces_to_one_popup(gui, switch_trace, monkeypatch):
    button = gui.variant_selector
    opened = []
    original = button.event_generate

    def generate(event, **kwargs):
        if event == "<<Invoke>>":
            opened.append(event)
        return original(event, **kwargs)

    monkeypatch.setattr(button, "event_generate", generate)
    for _ in range(3):
        button.event_generate("<ButtonRelease-1>", x=0, y=0, when="now")
    gui.root.update()
    assert opened == ["<<Invoke>>"] and switch_trace == []


@pytest.mark.parametrize("event", ["<ButtonRelease-1>", "<KeyPress-Return>", "<KeyPress-space>"])
def test_deliberate_entry_activation_keeps_cleanup_before_single_relaunch(variant, gui, switch_trace, event):
    index, filename = other_entry(gui, variant)
    gui.root.deiconify()
    gui.root.update()
    gui.variant_selector.event_generate("<<Invoke>>", when="now")
    gui.root.update()
    menu = activate_menu(gui, index)
    assert menu.winfo_ismapped()
    if event.startswith("<Key"):
        assert str(gui.root.tk.call("focus")) == str(menu)
    menu.event_generate(event, when="now")
    gui.root.update()
    assert [item[0] for item in switch_trace] == ["cleanup", "cwd", "exec"]
    assert switch_trace[1] == ("cwd", ROOT)
    assert switch_trace[2][2] == [variant.sys.executable, str(Path(variant.__file__).parent / filename),
                                  "--workspace", str(gui.agent.workspace)]
    assert gui.variant_selector_var.get() == Path(variant.__file__).name


def test_shared_registry_input_guards_remain_identical(variant):
    sources = [(ROOT / "runtime/wrapper.shared.py.in").read_text(),
               read_variant_source(Path(variant.__file__))]
    blocks = []
    for text in sources:
        start = text.index("            # X11 wheel releases share Tk's menu-invocation binding.")
        end = text.index("            self._qubitz_variant_switch_pending:", start)
        blocks.append(ast.parse(textwrap.dedent(text[start:end])))
    assert ast.dump(blocks[0]) == ast.dump(blocks[1])
