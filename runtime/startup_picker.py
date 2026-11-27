"""Choose a desktop variant before loading any variant or model."""
from __future__ import annotations

import os
import sys
import threading
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from queue import Empty, SimpleQueue

from .catalog import VARIANT_TIERS, variant_tier


def choose_startup_variant(paths: Sequence[Path]) -> str | None:
    from .tk_environment import configure_tk_environment

    configure_tk_environment()
    import tkinter as tk
    from tkinter import ttk

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise RuntimeError(str(exc)) from exc

    root.title("AI Agent Qubitz - Choose a variant")
    root.minsize(585, 280)
    root.geometry("900x520")
    root.configure(background="#141416")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background="#141416", foreground="#ffffff")
    style.configure("Heading.TLabel", font=("TkDefaultFont", 16, "bold"))
    style.configure("Treeview", background="#1b1c1f", fieldbackground="#1b1c1f")
    style.configure("Treeview.Heading", background="#23252a")
    style.configure("TButton", background="#23252a", padding=(12, 6))
    style.map("Treeview", background=[("selected", "#3c4048")])
    style.map("TButton", background=[("active", "#3c4048")], foreground=[("disabled", "#9aa0a6")])

    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Choose a variant model to launch!", style="Heading.TLabel").pack(anchor="w", pady=(0, 12))

    actions = ttk.Frame(frame)
    actions.pack(side="bottom", fill="x", pady=(12, 0))
    table = ttk.Frame(frame)
    table.pack(fill="both", expand=True)
    rows = ttk.Treeview(table, columns=("variant", "tier", "files"), show="headings", selectmode="browse")
    rows.heading("variant", text="Variant")
    rows.heading("tier", text="VRAM tier")
    rows.heading("files", text="Local model files")
    rows.column("variant", width=435, minwidth=270)
    rows.column("tier", width=115, stretch=False)
    rows.column("files", width=145, stretch=False)
    rows.tag_configure("cached", foreground="#ffffff")
    rows.tag_configure("uncached", foreground="#9aa0a6")
    scrollbar = ttk.Scrollbar(table, orient="vertical", command=rows.yview)
    rows.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    rows.pack(side="left", fill="both", expand=True)

    names = {path.name for path in paths}
    for name in sorted(names, key=lambda item: (VARIANT_TIERS.index(variant_tier(item)), item.casefold())):
        rows.insert("", "end", iid=name, values=(name, variant_tier(name), "Checking..."), tags=("uncached",))

    cache_results: SimpleQueue[dict[str, bool | None]] = SimpleQueue()
    cache_callback: str | None = None

    def scan_model_cache() -> None:
        from .catalog import cached_model_states

        try:
            states = cached_model_states(paths, paths[0].parent.parent)
        except Exception:  # noqa: BLE001 - A failed local scan must not leave the UI stuck on Checking.
            states = {}
        cache_results.put(states)

    def display_cache_result() -> None:
        nonlocal cache_callback
        cache_callback = None
        try:
            states = cache_results.get_nowait()
        except Empty:
            cache_callback = root.after(50, display_cache_result)
            return
        for name in names:
            state = states.get(name)
            status = "Present" if state is True else "Missing / incomplete" if state is False else "Unverified"
            rows.set(name, "files", status)
            rows.item(name, tags=("cached" if state is True else "uncached",))

    def cancel_cache_callback(event) -> None:
        if event.widget is root and cache_callback is not None:
            root.after_cancel(cache_callback)

    if paths:
        threading.Thread(target=scan_model_cache, name="qubitz-startup-variant-cache", daemon=True).start()
        cache_callback = root.after(50, display_cache_result)
        root.bind("<Destroy>", cancel_cache_callback, add="+")

    selected = None

    def launch_selected(_event):
        nonlocal selected
        current = rows.selection()
        if len(current) == 1 and current[0] in names:
            selected = current[0]
            root.destroy()
        return "break"

    ttk.Button(actions, text="Cancel", command=root.destroy).pack(side="right")
    rows.bind("<<TreeviewSelect>>", launch_selected)
    root.bind("<Escape>", lambda _event: root.destroy())
    root.after_idle(rows.focus_set)

    if os.environ.get("QUBITZ_START_MAXIMIZED", "1").strip().casefold() not in {"0", "no", "off", "false"}:
        pending = None

        def maximize():
            nonlocal pending
            pending = None
            if root.winfo_viewable():
                with suppress(tk.TclError):
                    if sys.platform == "win32":
                        root.state("zoomed")
                    else:
                        root.wm_attributes("-zoomed", True)

        def request_maximize(event):
            nonlocal pending
            if event.widget is root:
                root.unbind("<Map>", map_binding)
                pending = root.after(4000 if os.environ.get("WSL_DISTRO_NAME") else 0, maximize)

        def cancel_maximize(_event):
            nonlocal pending
            if pending is not None:
                root.after_cancel(pending)
                pending = None

        map_binding = root.bind("<Map>", request_maximize)
        for event in ("<KeyPress>", "<ButtonPress>", "<Unmap>"):
            root.bind(event, cancel_maximize, add="+")

    root.mainloop()
    return selected
