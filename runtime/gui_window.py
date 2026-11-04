"""Shared maximized startup; normal user restore/resize remains available."""


def _install_maximized_gui_startup(app):
    base = app.base
    if getattr(base, "_QUBITZ_MAXIMIZED_GUI_STARTUP", False):
        return

    class MaximizedStartupGUI(base.QubitzGUI):
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
            if not self._is_window_maximized():
                super()._ensure_window_visible()

    base.QubitzGUI = MaximizedStartupGUI
    base._QUBITZ_MAXIMIZED_GUI_STARTUP = True


if "_APP" in globals():
    _install_maximized_gui_startup(globals()["_APP"])
