"""Shared maximized startup; normal user restore/resize remains available."""

import os as _gui_window_os

_QUBITZ_WSL_STARTUP_MAXIMIZE_DELAY_MS = 4000


def header_rows(widths, available):
    """Keep label/control groups together while wrapping at the actual viewport."""
    row, used = 0, 0
    result = []
    for width in widths:
        if used and used + width > available:
            row, used = row + 1, 0
        result.append(row)
        used += width
    return result


def install_responsive_header(gui):
    controls = gui.num_predict_entry.master
    widgets = list(controls.pack_slaves())
    if not widgets or getattr(gui, "_responsive_header_installed", False):
        return
    gui._responsive_header_installed = True
    groups = []
    for widget in widgets:
        widget.pack_forget()
        if groups and widget.winfo_class() in {"TEntry", "Entry", "TCombobox", "Combobox"}:
            groups[-1].append(widget)
        else:
            groups.append([widget])
    state = {"pending": None, "closed": False, "width": None}

    def layout():
        state["pending"] = None
        if state["closed"]:
            return
        available = max(180, gui.root.winfo_width() - 40)
        if state["width"] == available:
            return
        state["width"] = available
        for widget in widgets:
            if widget.winfo_class() in {"TLabel", "Label"}:
                widget.configure(wraplength=max(120, available - 20))
        widths = [sum(widget.winfo_reqwidth() + 10 for widget in group) for group in groups]
        rows = header_rows(widths, available)
        left, top, row_height = 0, 0, 0
        previous_row = -1
        for row, group in zip(rows, groups, strict=True):
            if row != previous_row:
                if previous_row >= 0:
                    top += row_height + 6
                left, row_height = 0, 0
            for widget in group:
                width, height = widget.winfo_reqwidth(), widget.winfo_reqheight()
                widget.place(x=left, y=top + 3, width=width, height=height)
                left += width + 10
                row_height = max(row_height, height)
            previous_row = row
        controls.configure(height=top + row_height + 6)

    def schedule(event=None):
        if event is not None and event.widget != gui.root:
            return
        if not state["closed"] and state["pending"] is None:
            state["pending"] = gui.root.after_idle(layout)

    def close(event):
        if event.widget == gui.root:
            state["closed"] = True
            if state["pending"] is not None:
                gui.root.after_cancel(state["pending"])
                state["pending"] = None

    def refresh():
        state["width"] = None
        schedule()

    gui._qubitz_refresh_responsive_header = refresh
    # Dynamic groups can grow without changing the root's viewport width.
    for widget in widgets:
        if widget.winfo_class() in {"TFrame", "Frame"}:
            widget.bind("<Configure>", lambda _event: refresh(), add="+")
    gui.root.bind("<Configure>", schedule, add="+")
    gui.root.bind("<Destroy>", close, add="+")
    schedule()


def _install_maximized_gui_startup(app):
    base = app.base
    if getattr(base, "_QUBITZ_MAXIMIZED_GUI_STARTUP", False):
        return

    class MaximizedStartupGUI(base.QubitzGUI):
        def _apply_theme(self):
            if not getattr(self, "_qubitz_startup_window_bound", False):
                self._qubitz_startup_window_bound = True
                self._qubitz_startup_maximize_pending = (
                    _gui_window_os.environ.get("QUBITZ_START_MAXIMIZED", "1").strip().casefold()
                    not in {"0", "no", "off", "false"}
                )
                self._qubitz_startup_maximize_timer = None
                self._qubitz_startup_map_seen = False
                self._qubitz_startup_input_seen = False
                self.root.bind("<Map>", self._startup_window_mapped, add="+")
                self.root.bind("<Unmap>", self._startup_window_hidden, add="+")
                self.root.bind("<ButtonPress>", self._startup_user_interaction, add="+")
                self.root.bind("<KeyPress>", self._startup_user_interaction, add="+")
                self.root.bind("<Destroy>", self._startup_window_hidden, add="+")
            super()._apply_theme()

        def _startup_window_log(self, event):
            log = getattr(self, "_qubitz_gui_log_event", None)
            if callable(log):
                log(event)

        def _cancel_startup_maximize(self):
            self._qubitz_startup_maximize_pending = False
            timer = self._qubitz_startup_maximize_timer
            if timer is not None:
                self._qubitz_startup_maximize_timer = None
                self.root.after_cancel(timer)

        def _startup_user_interaction(self, _event=None):
            if not self._qubitz_startup_input_seen:
                self._qubitz_startup_input_seen = True
                self._startup_window_log("gui_window_first_input")
            self._cancel_startup_maximize()

        def _startup_window_hidden(self, event):
            if event.widget is self.root:
                self._cancel_startup_maximize()

        def _startup_window_mapped(self, event):
            if event.widget is self.root:
                self._startup_window_ready()

        def _startup_window_ready(self):
            if getattr(self, "_qubitz_startup_map_seen", True):
                return
            self._qubitz_startup_map_seen = True
            self._startup_window_log("gui_window_mapped")
            if self._qubitz_startup_maximize_pending:
                in_wsl = getattr(base, "in_wsl", None)
                if callable(in_wsl) and in_wsl():
                    self._qubitz_startup_maximize_timer = self.root.after(
                        _QUBITZ_WSL_STARTUP_MAXIMIZE_DELAY_MS,
                        self._maximize_startup_window,
                    )
                else:
                    self._qubitz_startup_maximize_timer = self.root.after_idle(self._maximize_startup_window)

        def _maximize_startup_window(self):
            self._qubitz_startup_maximize_timer = None
            if not self._qubitz_startup_maximize_pending:
                return
            self._qubitz_startup_maximize_pending = False
            if self.root.winfo_ismapped() and not self._is_window_maximized():
                self._startup_window_log("gui_window_maximize_requested")
                self._maximize_window()

        def _present_window(self):
            if getattr(self, "_qubitz_startup_presented", False):
                return
            self._qubitz_startup_presented = True
            if self.root.state() == "withdrawn":
                self.root.deiconify()
            if self.root.winfo_ismapped():
                self._startup_window_ready()
            self.root.after(1500, self._ensure_window_visible)

        def _maximize_window(self):
            try:
                self.root.state("zoomed")
                return
            except self.tk.TclError:
                pass
            try:
                self.root.wm_attributes("-zoomed", True)
                return
            except self.tk.TclError:
                pass
            self.root.update_idletasks()
            width = max(800, self.root.winfo_screenwidth())
            height = max(800, self.root.winfo_screenheight())
            self.root.geometry(f"{width}x{height}+0+0")

        def _is_window_maximized(self):
            try:
                if self.root.state() == "zoomed":
                    return True
            except self.tk.TclError:
                pass
            try:
                return str(self.root.wm_attributes("-zoomed")).casefold() in {"1", "true", "yes", "on"}
            except self.tk.TclError:
                return False

        def _ensure_window_visible(self):
            self.root.update_idletasks()
            # Native maximized frames can have negative border coordinates.
            # Do not restore a window while its first-map action is pending.
            if not getattr(self, "_qubitz_startup_maximize_pending", False) and not self._is_window_maximized():
                super()._ensure_window_visible()

    base.QubitzGUI = MaximizedStartupGUI
    base._QUBITZ_MAXIMIZED_GUI_STARTUP = True


if "_APP" in globals():
    _install_maximized_gui_startup(globals()["_APP"])
