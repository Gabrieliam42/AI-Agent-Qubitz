"""Scrollable Tk picker; browsing does not change the running variant."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk
    from tkinter import ttk


class VariantPicker:
    def __init__(
        self,
        root: tk.Tk,
        anchor: ttk.Menubutton,
        groups: Sequence[tuple[str, Sequence[str]]],
        choose: Callable[[str], None],
        refresh: Callable[[], None],
        *,
        background: str,
        border: str,
        muted: str,
        highlight: str,
    ) -> None:
        from runtime.tk_environment import configure_tk_environment

        configure_tk_environment()
        import tkinter as tk
        from tkinter import ttk

        self.root = root
        self.anchor = anchor
        self._choose = choose
        self._refresh = refresh
        self._enabled = True
        self._pressed_row: int | None = None
        self.panel = tk.Frame(root, background=background, highlightbackground=border, highlightthickness=1)
        self.summary = tk.Label(self.panel, background=background, foreground=muted, anchor="w", padx=6, pady=3)
        self.summary.pack(side="top", fill="x")
        self.listbox = tk.Listbox(
            self.panel, background=background, foreground="#9aa0a6", selectbackground=highlight,
            activestyle="none", selectmode=tk.BROWSE, exportselection=False,
            relief="flat", borderwidth=0, highlightthickness=0,
        )
        self.scrollbar = ttk.Scrollbar(self.panel, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self._rows: dict[int, str] = {}
        for tier, filenames in groups:
            self.listbox.insert("end", tier)
            self.listbox.itemconfigure("end", foreground=muted)
            for name in filenames:
                self._rows[self.listbox.size()] = name
                self.listbox.insert("end", name)
        self._indices = {name: index for index, name in self._rows.items()}
        self.listbox.configure(height=max(1, self.listbox.size()))
        self._summary = f"{len(self._rows)} variants"
        self.summary.configure(text=self._summary)
        row_font = self.listbox.cget("font")
        self._label_width = max(
            (int(root.tk.call("font", "measure", row_font, label)) for label in self.listbox.get(0, "end")),
            default=0,
        )
        self.listbox.bind("<ButtonPress-1>", self._press)
        self.listbox.bind("<ButtonRelease-1>", self._release)
        self.listbox.bind("<Double-ButtonPress-1>", self._press)
        self.listbox.bind("<Button-4>", lambda _event: self._scroll(-3))
        self.listbox.bind("<Button-5>", lambda _event: self._scroll(3))
        self.listbox.bind("<ButtonRelease-4>", lambda _event: "break")
        self.listbox.bind("<ButtonRelease-5>", lambda _event: "break")
        self.listbox.bind("<MouseWheel>", self._wheel)
        for key, step in (("Up", -1), ("Down", 1), ("Prior", -10), ("Next", 10)):
            self.listbox.bind(f"<{key}>", lambda _event, step=step: self._move(step))
        self.listbox.bind("<Home>", lambda _event: self._move_to(0))
        self.listbox.bind("<End>", lambda _event: self._move_to(len(self._rows) - 1))
        for key in ("Return", "space"):
            self.listbox.bind(f"<{key}>", self._commit_highlighted)
        self.panel.bind("<Escape>", self._dismiss)
        self.listbox.bind("<Escape>", self._dismiss)
        self.listbox.bind("<Tab>", self._dismiss)
        self.listbox.bind("<FocusOut>", lambda _event: self.close())
        root.bind("<ButtonPress-1>", self._outside_click, add="+")
        root.bind("<Configure>", self._resize, add="+")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._indices)

    @property
    def is_open(self) -> bool:
        return self.panel.winfo_manager() == "place"

    @property
    def enabled(self) -> bool:
        return self._enabled and not self.anchor.instate(("disabled",))

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if not enabled:
            self.close()

    def set_cached(self, states: Mapping[str, bool | None]) -> None:
        for name, index in self._indices.items():
            color = "#ffffff" if states.get(name) is True else "#9aa0a6"
            self.listbox.itemconfigure(index, foreground=color)

    def toggle(self, current: str) -> None:
        if self.is_open:
            self.close(restore_focus=True)
        elif self.enabled and self._rows and self._place():
            self._highlight(self._indices.get(current, next(iter(self._rows))), reveal=False)
            self.listbox.yview_moveto(0)
            self.listbox.focus_set()
            self._refresh()

    def close(self, *, restore_focus: bool = False) -> None:
        self._pressed_row = None
        self.panel.place_forget()
        if restore_focus and self.anchor.winfo_exists():
            self.anchor.focus_set()

    def commit(self, index: int) -> None:
        if not self.is_open or not self.enabled or index not in self._rows:
            return
        self.close(restore_focus=True)
        self._choose(self._rows[index])

    def _place(self) -> bool:
        x = self.anchor.winfo_rootx() - self.root.winfo_rootx()
        y = self.anchor.winfo_rooty() - self.root.winfo_rooty()
        bottom = y + self.anchor.winfo_height()
        below = self.root.winfo_height() - bottom
        above = y
        requested = self.panel.winfo_reqheight()
        open_below = below >= requested or below >= above
        height = min(requested, below if open_below else above)
        content_width = self._label_width + self.scrollbar.winfo_reqwidth() + 14
        width = min(self.root.winfo_width(), max(content_width, self.panel.winfo_reqwidth()))
        if height < 20 or width < 1:
            return False
        x = max(0, min(x, self.root.winfo_width() - width))
        suffix = " - scroll for more" if height < requested else ""
        self.summary.configure(text=self._summary + suffix)
        self.panel.place(x=x, y=bottom if open_below else above - height, width=width, height=height)
        self.panel.lift()
        return True

    def _resize(self, event: tk.Event) -> None:
        if event.widget is self.root and self.is_open and not self._place():
            self.close()

    def _outside_click(self, event: tk.Event) -> None:
        if not self.is_open:
            return
        if event.widget in (self.panel, self.summary, self.listbox, self.scrollbar, self.anchor):
            return
        self.close()

    def _dismiss(self, _event: tk.Event) -> str:
        self.close(restore_focus=True)
        return "break"

    def _row_at(self, event: tk.Event) -> int | None:
        if not (0 <= event.x < self.listbox.winfo_width() and 0 <= event.y < self.listbox.winfo_height()):
            return None
        index = self.listbox.nearest(event.y)
        bbox = self.listbox.bbox(index)
        return index if index in self._rows and bbox and bbox[1] <= event.y < bbox[1] + bbox[3] else None

    def _press(self, event: tk.Event) -> str:
        self._pressed_row = self._row_at(event) if self.enabled else None
        if self._pressed_row is not None:
            self._highlight(self._pressed_row)
        return "break"

    def _release(self, event: tk.Event) -> str:
        index, self._pressed_row = self._pressed_row, None
        if index is not None and index == self._row_at(event):
            self.commit(index)
        return "break"

    def _scroll(self, units: int) -> str:
        self._pressed_row = None
        self.listbox.yview_scroll(units, "units")
        return "break"

    def _wheel(self, event: tk.Event) -> str:
        if not event.delta:
            return "break"
        steps = max(1, abs(event.delta) // 120) * 3
        return self._scroll(-steps if event.delta > 0 else steps)

    def _highlight(self, index: int, *, reveal: bool = True) -> None:
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(index)
        self.listbox.activate(index)
        if reveal:
            self.listbox.see(index)

    def _move_to(self, position: int) -> str:
        self._pressed_row = None
        rows = tuple(self._rows)
        if rows:
            self._highlight(rows[min(len(rows) - 1, max(0, position))])
        return "break"

    def _move(self, step: int) -> str:
        rows = tuple(self._rows)
        highlight = self.listbox.curselection()
        position = rows.index(highlight[0]) if highlight and highlight[0] in self._rows else 0
        return self._move_to(position + step)

    def _commit_highlighted(self, _event: tk.Event) -> str:
        highlight = self.listbox.curselection()
        if highlight and self.listbox.bbox(highlight[0]):
            self.commit(highlight[0])
        return "break"
