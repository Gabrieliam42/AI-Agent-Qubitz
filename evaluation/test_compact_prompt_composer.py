"""Model-free widget contracts for the actual shared Tk composer method."""
from __future__ import annotations

import ast
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk
from types import SimpleNamespace

import pytest

from runtime import read_variant_source
from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"


@pytest.fixture(scope="module")
def layout_methods():
    tree = ast.parse(read_variant_source(variant_path(REFERENCE)))
    gui = next(node for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef) and node.name == "RunControlGUI")
    methods = [node for node in gui.body if isinstance(node, ast.FunctionDef)
               and node.name in {"_compact_prompt_composer", "_prioritize_composer_layout"}]
    namespace = {}
    exec(compile(ast.Module(body=methods, type_ignores=[]), "<actual-compact-composer>", "exec"), namespace)  # noqa: S102 - Trusted local product AST, without variant execution.
    return namespace


@pytest.fixture(scope="module")
def compact_method(layout_methods):
    return layout_methods["_compact_prompt_composer"]


@pytest.fixture
def composer(compact_method):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.title("Qubitz model-free compact composer check")
    root.geometry("800x600")
    errors = []
    root.report_callback_exception = lambda *_args: errors.append(_args)
    chat = scrolledtext.ScrolledText(root, state="disabled")
    chat.pack(fill="both", expand=True)
    frame = ttk.Frame(root)
    frame.pack(fill="x")
    prompt = tk.Text(frame, height=8, wrap="word", undo=True)
    prompt.pack(side="left", fill="both", expand=True)
    controls = ttk.Frame(frame)
    controls.pack(side="left", fill="y")
    invocations = []
    send = ttk.Button(controls, text="Send", command=lambda: invocations.append("send"))
    send.pack(fill="x")
    settings = [ttk.Label(controls, text="Effort"),
                ttk.Combobox(controls, values=("xhigh",), state="readonly", width=8)]
    reasoning = [ttk.Checkbutton(controls, text="Show Reasoning")]
    for widget in (*settings, *reasoning):
        widget.pack(fill="x")
    actions = [send]
    for label in ("Jump to latest", "Steer", "Cancel", "Clear"):
        widget = ttk.Button(controls, text=label)
        widget.pack(fill="x")
        actions.append(widget)
    clipboard = ttk.Button(controls, text="Clipboard")
    clipboard.pack(fill="x", before=send)
    actions.insert(0, clipboard)
    access_bar = ttk.Frame(root)
    access_bar.pack(fill="x")
    access_label = ttk.Label(access_bar, text="Access")
    access_label.pack(side="left")
    access = ttk.Combobox(access_bar, values=("Full Access",), state="readonly", width=14)
    access.pack(side="left", padx=(6, 0))
    access.current(0)
    network_label = ttk.Label(access_bar, text="Network")
    network_label.pack(side="left", padx=(18, 0))
    network = ttk.Combobox(access_bar, values=("Offline",), state="readonly", width=9)
    network.pack(side="left", padx=(6, 0))
    network.current(0)
    settings.extend((access_label, access, network_label, network))
    prompt.bind("<Return>", lambda _event: "break")
    prompt.bind("<Shift-Return>", lambda _event: "break")
    prompt.bind("<Escape>", lambda _event: "break")
    bindings = {name: prompt.bind(name) for name in ("<Return>", "<Shift-Return>", "<Escape>")}
    gui = SimpleNamespace(tk=tk, ttk=ttk, prompt_box=prompt, send_button=send,
                          access_mode_entry=access, network_mode_entry=network)
    compact_method(gui)
    root.update()
    input_host = root.nametowidget(str(prompt.grid_info()["in"]))
    scrollbar = next(widget for widget in input_host.winfo_children() if isinstance(widget, ttk.Scrollbar))
    try:
        yield SimpleNamespace(root=root, prompt=prompt, controls=controls, scrollbar=scrollbar,
                              chat=chat, send=send, invocations=invocations, bindings=bindings,
                              actions=actions, settings=settings, reasoning=reasoning, input_host=input_host,
                              effort=settings[1],
                              actions_host=gui._prompt_actions_frame, settings_host=gui._prompt_settings_frame,
                              access=access, network=network)
    finally:
        root.destroy()
        assert not errors, errors


@pytest.mark.parametrize("draft", [
    "", "Short query", "one\ntwo", "one\ntwo\nthree",
    "one\ntwo\nthree\nfour", "\n", "\n\n", "\n\n\n",
])
def test_draft_preserves_control_aligned_height_and_every_character(composer, draft):
    initial_height = composer.prompt.winfo_height()
    composer.prompt.insert("1.0", draft)
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    assert composer.prompt.winfo_height() == initial_height
    assert composer.prompt.get("1.0", "end-1c") == draft


def test_wrapped_draft_does_not_change_height_when_window_resizes(composer):
    draft = "word " * 24
    composer.root.geometry("360x600")
    composer.prompt.insert("1.0", draft)
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    composer.root.geometry("1800x600")
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    assert composer.prompt.get("1.0", "end-1c") == draft


def test_long_paste_scrolls_without_resizing_or_counting_and_clear_keeps_height(composer, monkeypatch):
    initial_height = composer.prompt.winfo_height()
    counted = []
    original_count = composer.prompt.count

    def count(first, last, *options):
        counted.append(last)
        return original_count(first, last, *options)

    monkeypatch.setattr(composer.prompt, "count", count)
    draft = "Long pasted query line\n" * 1000
    composer.prompt.insert("1.0", draft)
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    assert composer.prompt.winfo_height() == initial_height
    assert composer.scrollbar.winfo_ismapped()
    assert counted == []
    composer.prompt.yview_moveto(0.0)
    composer.root.update()
    assert composer.prompt.yview()[0] == 0.0
    composer.prompt.see("end-1c")
    composer.root.update()
    assert composer.prompt.yview()[0] > 0.0
    assert composer.prompt.get("1.0", "end-1c") == draft
    composer.prompt.delete("1.0", "end")
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    assert composer.prompt.winfo_height() == initial_height
    assert composer.scrollbar.winfo_ismapped()


@pytest.mark.parametrize("width", [360, 800, 1800])
def test_actions_stay_right_and_only_settings_are_below(composer, width):
    composer.root.geometry(f"{width}x600")
    composer.root.update()
    assert composer.settings_host.winfo_height() < 180
    assert composer.chat.winfo_height() > 250
    assert composer.actions_host.winfo_rootx() >= composer.prompt.winfo_rootx() + composer.prompt.winfo_width()
    assert composer.settings_host.winfo_rooty() >= max(
        composer.input_host.winfo_rooty() + composer.input_host.winfo_height(),
        composer.actions_host.winfo_rooty() + composer.actions_host.winfo_height(),
    )
    for widget in composer.actions:
        assert widget.winfo_ismapped()
        assert str(widget.grid_info()["in"]) == str(composer.actions_host)
        assert widget.winfo_rootx() >= composer.actions_host.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= composer.root.winfo_rootx() + width
    for widget in composer.reasoning:
        assert widget.winfo_ismapped()
        assert str(widget.place_info()["in"]) == str(composer.settings_host)
        assert widget.winfo_rooty() == composer.effort.winfo_rooty()
        assert widget.winfo_rootx() >= composer.effort.winfo_rootx() + composer.effort.winfo_width() + 8
        assert widget.winfo_rootx() + widget.winfo_width() <= composer.settings_host.winfo_rootx() + composer.settings_host.winfo_width()
    checkbox, = composer.reasoning
    assert checkbox.winfo_rooty() >= composer.prompt.winfo_rooty() + composer.prompt.winfo_height()
    action_bottom = composer.actions_host.winfo_rooty() + composer.actions_host.winfo_height()
    assert composer.prompt.winfo_rooty() + composer.prompt.winfo_height() == action_bottom
    assert composer.scrollbar.winfo_rooty() + composer.scrollbar.winfo_height() == action_bottom
    assert not any(widget.winfo_class() in {"Label", "TLabel"}
                   and widget.cget("text") in {"Reading Speed", "Speed"}
                   for widget in composer.controls.winfo_children())
    for widget in composer.settings:
        assert widget.winfo_ismapped()
        assert widget.winfo_rootx() >= composer.settings_host.winfo_rootx()
        assert widget.winfo_rootx() + widget.winfo_width() <= composer.settings_host.winfo_rootx() + composer.settings_host.winfo_width()
        assert widget.winfo_rooty() >= composer.settings_host.winfo_rooty()
        assert widget.winfo_rooty() + widget.winfo_height() <= composer.settings_host.winfo_rooty() + composer.settings_host.winfo_height()
    assert composer.access.get() == "Full Access" and composer.network.get() == "Offline"
    composer.send.invoke()
    assert composer.invocations == ["send"]


def test_query_height_tracks_control_size_not_draft_size(composer):
    initial_height = composer.prompt.winfo_height()
    for button in composer.actions:
        button.configure(padding=(0, 12))
    composer.root.update()
    assert composer.prompt.winfo_height() > initial_height
    expected_bottom = composer.actions_host.winfo_rooty() + composer.actions_host.winfo_height()
    assert composer.prompt.winfo_rooty() + composer.prompt.winfo_height() == expected_bottom


def test_reasoning_visibility_is_a_bottom_setting_without_speed_controls(composer):
    assert len(composer.reasoning) == 1
    assert composer.reasoning[0].cget("text") == "Show Reasoning"
    assert composer.reasoning[0] not in composer.actions_host.grid_slaves()
    assert composer.reasoning[0] in composer.settings_host.place_slaves()
    assert not any(widget.winfo_class() in {"Entry", "TEntry"}
                   for widget in composer.controls.winfo_children())


def test_existing_shortcuts_and_programmatic_history_edits_are_preserved(composer):
    assert composer.bindings == {name: composer.prompt.bind(name) for name in composer.bindings}
    composer.prompt.insert("1.0", "Recalled\nprevious\nquery")
    composer.root.update()
    assert int(composer.prompt.cget("height")) == 4
    composer.prompt.edit_undo()
    composer.root.update()
    assert composer.prompt.get("1.0", "end-1c") == ""
    assert int(composer.prompt.cget("height")) == 4
    composer.prompt.edit_redo()
    composer.root.update()
    assert composer.prompt.get("1.0", "end-1c") == "Recalled\nprevious\nquery"
    assert int(composer.prompt.cget("height")) == 4


def test_destroy_cancels_pending_bottom_settings_reflow(composer):
    baseline = set(composer.root.tk.splitlist(composer.root.tk.call("after", "info")))
    composer.settings_host.event_generate("<Configure>", width=composer.settings_host.winfo_width() + 1)
    pending = set(composer.root.tk.splitlist(composer.root.tk.call("after", "info"))) - baseline
    assert pending
    composer.prompt.destroy()
    remaining = set(composer.root.tk.splitlist(composer.root.tk.call("after", "info")))
    assert pending.isdisjoint(remaining)
    composer.root.update()


def test_shared_registry_and_effective_source_have_identical_compact_layout():
    blocks = []
    for source in ((ROOT / "runtime/wrapper.shared.py.in").read_text(encoding="utf-8"),
                   read_variant_source(variant_path(REFERENCE))):
        start = source.index("        def _compact_prompt_composer(self)")
        end = source.index("        def _toggle_reasoning_display(self)", start)
        blocks.append(source[start:end])
    assert blocks[0] == blocks[1]


def test_compaction_waits_for_outer_gui_clipboard_button(layout_methods):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.title("Qubitz model-free outer GUI construction check")
    root.geometry("800x600")
    errors = []
    root.report_callback_exception = lambda *_args: errors.append(_args)
    try:
        panel = ttk.Frame(root)
        panel.pack(fill="both", expand=True)
        chat = scrolledtext.ScrolledText(panel)
        chat.pack(fill="both", expand=True)
        frame = ttk.Frame(panel)
        frame.pack(fill="x")
        prompt = tk.Text(frame, height=8, wrap="word")
        prompt.pack(side="left", fill="both", expand=True)
        controls = ttk.Frame(frame)
        controls.pack(side="left", fill="y")
        send = ttk.Button(controls, text="Send")
        send.pack(fill="x")
        gui = SimpleNamespace(root=root, tk=tk, ttk=ttk, transcript=chat,
                              prompt_box=prompt, send_button=send)
        gui._compact_prompt_composer = lambda: layout_methods["_compact_prompt_composer"](gui)
        layout_methods["_prioritize_composer_layout"](gui)
        assert send.winfo_manager() == "pack"
        # ConversationGUI performs this insertion after its parent constructor returns.
        clipboard = ttk.Button(controls, text="Clipboard")
        clipboard.pack(fill="x", before=send)
        root.update()
        assert not errors, errors
        assert int(prompt.cget("height")) == 4
        assert clipboard.winfo_ismapped() and send.winfo_ismapped()
        assert clipboard.winfo_manager() == send.winfo_manager() == "grid"
        assert clipboard.winfo_x() < send.winfo_x()
    finally:
        root.destroy()
