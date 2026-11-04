"""Shared startup maximization and visibility recovery, without model loading."""
from __future__ import annotations

import ast
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as fixtures
from runtime import read_variant_source
from runtime.gui_window import _install_maximized_gui_startup
from runtime.paths import PROJECT_ROOT, variant_paths
from runtime.source import embedded_source

variant = fixtures.variant


class TclError(Exception):
    pass


class Window:
    def __init__(self, *, native=True, attribute=True, maximized=False):
        self.native = native
        self.attribute = attribute
        self.maximized = maximized
        self.calls = []

    def state(self, value=None):
        if value is None:
            return "zoomed" if self.maximized else "normal"
        self.calls.append(("state", value))
        if not self.native:
            raise TclError("State zoomed is not supported")
        self.maximized = value == "zoomed"

    def wm_attributes(self, name, value=None):
        if not self.attribute:
            raise TclError("Attribute is not supported")
        if value is None:
            return int(self.maximized)
        self.calls.append((name, value))
        self.maximized = bool(value)

    def update_idletasks(self):
        pass

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def winfo_x(self):
        return -8

    def winfo_y(self):
        return -8

    def winfo_width(self):
        return 1936

    def winfo_height(self):
        return 1096

    def geometry(self, value):
        self.calls.append(("geometry", value))

    def lift(self):
        self.calls.append(("lift",))


@pytest.fixture(scope="module", params=variant_paths(PROJECT_ROOT), ids=lambda path: path.stem)
def gui_methods(request):
    effective = read_variant_source(request.param)
    adapter = (PROJECT_ROOT / "runtime/gui_window.py").read_text()
    assert effective.count(adapter) == 1
    source, _ = embedded_source(effective)
    tree = ast.parse(source)
    owner = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "QubitzGUI")
    names = {"_ensure_window_visible", "_fit_window_to_screen"}
    methods = [node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(methods) == len(names)
    cls = ast.ClassDef(name="GUI", bases=[], keywords=[], body=methods, decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(request.param), "exec"), namespace)  # noqa: S102 - compile only local GUI methods under test
    app = SimpleNamespace(base=SimpleNamespace(QubitzGUI=namespace["GUI"]))
    _install_maximized_gui_startup(app)
    app.base.QubitzGUI._effective_source = effective
    return app.base.QubitzGUI


@pytest.mark.parametrize("native,attribute,expected", [
    (True, True, [("state", "zoomed")]),
    (False, True, [("state", "zoomed"), ("-zoomed", True)]),
    (False, False, [("state", "zoomed"), ("geometry", "1920x1080+0+0")]),
])
def test_every_public_variant_requests_maximization(gui_methods, native, attribute, expected):
    gui = gui_methods()
    gui.tk = SimpleNamespace(TclError=TclError)
    gui.root = Window(native=native, attribute=attribute)
    gui._maximize_window()
    assert gui.root.calls == expected


def test_visibility_recovery_preserves_maximized_negative_frame(gui_methods):
    gui = gui_methods()
    gui.tk = SimpleNamespace(TclError=TclError)
    gui.root = Window(maximized=True)
    gui._ensure_window_visible()
    assert not gui.root.calls


def test_visibility_recovery_still_repairs_unmaximized_offscreen_window(gui_methods):
    gui = gui_methods()
    gui.tk = SimpleNamespace(TclError=TclError)
    gui.root = Window()
    gui._ensure_window_visible()
    assert ("state", "normal") in gui.root.calls
    assert any(call[0] == "geometry" for call in gui.root.calls)


def test_manual_restore_is_not_undone_by_visibility_recovery(gui_methods):
    gui = gui_methods()
    gui.tk = SimpleNamespace(TclError=TclError)
    gui.root = Window(maximized=True)
    gui.root.state("normal")
    gui.root.winfo_x = lambda: 20
    gui.root.winfo_y = lambda: 20
    gui.root.winfo_width = lambda: 800
    gui.root.winfo_height = lambda: 800
    gui.root.calls.clear()
    gui._ensure_window_visible()
    assert not gui.root.calls and not gui.root.maximized


def test_every_public_foreground_recovery_preserves_existing_placement(gui_methods):
    source = gui_methods._effective_source
    assert "public static extern bool IsIconic(IntPtr hWnd);" in source
    assert "if ([QubitzWindowActivation]::IsIconic($target.MainWindowHandle)) { 9 } else { 5 }" in source
    assert "ShowWindowAsync($target.MainWindowHandle, $showCommand)" in source
    assert "ShowWindowAsync($target.MainWindowHandle, 9)" not in source


def test_real_gui_opens_maximized_without_model_loading(variant, monkeypatch, tmp_path):
    if Path(variant.__file__).name not in {"AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py",
                                         "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"}:
        pytest.skip("Real shared-window probes cover two profiles; static contracts cover every public variant")
    base = variant._APP.base
    loads = []

    def deny_model(*_args):
        loads.append(True)
        pytest.fail("Maximization probe attempted model loading")

    monkeypatch.setattr(base.AgentRunner, "_get_llm", deny_model)
    monkeypatch.setattr(base.QubitzGUI, "_maybe_start_background_model_prewarm", lambda _self: None)
    monkeypatch.setattr(base.QubitzGUI, "_schedule_selected_model_acquisition", lambda _self: None)
    # Exercise the same presentation and visibility recovery as the matrix.
    # Only model acquisition/prewarm is disabled; foreground recovery stays real.
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    gui.root.title("AI Agent Qubitz - maximized startup verification")
    from tmp_smoke_variant import _present_gui

    _present_gui(gui)
    try:
        started = time.monotonic()
        deadline = started + 12
        startup = None
        while time.monotonic() < deadline:
            gui.root.update()
            if startup is None and time.monotonic() - started > 5:
                if gui._is_window_maximized():
                    startup = {"maximized": True, "state": gui.root.state(), "geometry": gui.root.geometry()}
                else:
                    pytest.fail("Startup did not maximize the window: " + gui.root.geometry())
            time.sleep(0.02)
        assert startup is not None and not loads
        if sys.platform.startswith("linux"):
            gui.root.wm_attributes("-zoomed", False)
        else:
            gui.root.state("normal")
        restore_deadline = time.monotonic() + 3
        while time.monotonic() < restore_deadline:
            gui.root.update()
            gui._ensure_window_visible()
            time.sleep(0.02)
        assert gui.root.winfo_ismapped() and not gui._is_window_maximized()
        receipt = {"startup": startup, "mapped": bool(gui.root.winfo_ismapped()), "maximized": gui._is_window_maximized(),
                   "state": gui.root.state(), "geometry": gui.root.geometry(), "model_load_attempts": loads,
                   "manual_restore_stayed_open": True, "startup_visibility_recovery_enabled": True}
        (tmp_path / "maximized_gui_receipt.json").write_text(json.dumps(receipt, indent=2))
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()
