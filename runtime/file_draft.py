"""Bounded GUI-only previews of selected file tool fields, never tool authority."""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

from .workflow import CODE_SUFFIXES

_PREVIEW_SUFFIXES = CODE_SUFFIXES | {".css", ".html", ".htm", ".txt", ".md", ".json", ".java", ".sh"}
_PREVIEW_TEXT = 4096
_MAX_DRAFT_TEXT = 256 * 1024
_MAX_PATHS = 12
_MAX_STREAM = 1024 * 1024
_TOOL_FIELDS = {"write_file": "content", "replace_text": "new_text"}
_PATCH_TOOLS = {"apply_text_patch", "apply_text_patches"}
_STRING_PREFIX = re.compile(r'[^"\\\x00-\x1f]*(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})[^"\\\x00-\x1f]*)*')
_SINK = ContextVar("file_draft_sink", default=None)
_DRAFTS_ENABLED = ContextVar("file_drafts_enabled", default=None)
DRAFT_WORK_PHASES = frozenset({"Drafting file change", "Checking file change"})


@contextmanager
def capture(emit, *, drafts_enabled=None):
    token = _SINK.set(emit)
    enabled_token = _DRAFTS_ENABLED.set(drafts_enabled)
    try:
        yield
    finally:
        _DRAFTS_ENABLED.reset(enabled_token)
        _SINK.reset(token)


def file_drafts_enabled() -> bool:
    enabled = _DRAFTS_ENABLED.get()
    return enabled is not None and enabled()


def phase(label: str) -> None:
    if (sink := _SINK.get()) is not None:
        with suppress(Exception):
            sink("work_phase", label)


def _preview_path(broker, raw: object, *, exact: bool) -> Path | None:
    if not isinstance(raw, str) or not raw or len(raw) > 4096:
        return None
    try:
        workspace = Path(broker.workspace).resolve()
        name = Path(raw)
        if ".." in name.parts or any(ord(char) < 32 for char in raw):
            return None
        path = (workspace / name).resolve()
        relative = path.relative_to(workspace)
    except (OSError, RuntimeError, ValueError):
        return None
    if (path.suffix.lower() not in _PREVIEW_SUFFIXES or any(part.startswith(".") for part in relative.parts)
            or re.search(r"(?i)credential|secret|password|token[._-]|private[._-]?key", relative.as_posix())):
        return None
    if exact and not any(str(path) in targets for targets in (
        broker.creation_targets, broker.transaction_explicit_file_targets,
    )):
        return None
    return relative


def _string_fields(raw: str, field: str) -> tuple[dict[str, str], bool]:
    """Read only top-level strings and a valid prefix of an unfinished value."""
    decoder = json.JSONDecoder()
    if not raw.lstrip().startswith("{"):
        return {}, False
    raw = raw.lstrip()[1:]
    result = {}
    seen = set()
    unfinished = False
    while raw := raw.lstrip():
        try:
            key, end = decoder.raw_decode(raw)
            if not isinstance(key, str) or key in seen:
                return {}, False
            seen.add(key)
            if not (tail := raw[end:].lstrip()).startswith(":"):
                break
            raw = tail[1:].lstrip()
            try:
                value, end = decoder.raw_decode(raw)
            except ValueError:
                if key == field and raw.startswith('"'):
                    prefix = _STRING_PREFIX.match(raw, 1)[0]
                    rest = raw[1 + len(prefix):]
                    if not rest or re.fullmatch(r"\\(?:u[0-9a-fA-F]{0,3})?", rest):
                        result[key] = json.loads(f'"{prefix}"')
                        unfinished = True
                break
            if key in {"path", field}:
                if not isinstance(value, str):
                    return {}, False
                result[key] = value
            tail = raw[end:].lstrip()
            if not tail.startswith(","):
                if tail and tail != "}":
                    return {}, False
                break
            raw = tail[1:]
        except ValueError:
            break
    return result, unfinished


def _clean(text: str) -> str:
    return "".join(
        char if char in "\t\n" or (char.isprintable() and not 0xD800 <= ord(char) <= 0xDFFF)
        else " " for char in text
    )


def _emit(path: Path, state: str, text: str = "", *, capture_limited: bool = False) -> None:
    if file_drafts_enabled():
        event = {"path": path.as_posix(), "state": state, "text": _clean(text[:_MAX_DRAFT_TEXT])}
        if capture_limited or len(text) > _MAX_DRAFT_TEXT:
            event["capture_limited"] = True
        _SINK.get()("file_draft", json.dumps(event, ensure_ascii=False))


class StreamDraft:
    def __init__(self, broker):
        self.broker = broker
        self.next_update = {}
        self.last_preview = {}

    def update(self, index: int, function: dict) -> None:
        if not file_drafts_enabled() or self.broker is None or not 0 <= index < _MAX_PATHS:
            return
        field = _TOOL_FIELDS.get(function.get("name"))
        raw = function["arguments"]
        if not field:
            return
        now = time.monotonic()
        if now < self.next_update.get(index, 0):
            return
        self.next_update[index] = now + 0.4
        fields, unfinished = _string_fields(raw[:_MAX_STREAM], field)
        path = _preview_path(self.broker, fields.get("path"), exact=True)
        if path is not None and field in fields:
            content = fields[field]
            limited = len(content) > _MAX_DRAFT_TEXT or (unfinished and len(raw) > _MAX_STREAM)
            preview = (path, content[:_MAX_DRAFT_TEXT], limited)
            if preview != self.last_preview.get(index):
                self.last_preview[index] = preview
                phase("Drafting file change")
                _emit(path, "Not saved (draft)", preview[1], capture_limited=limited)


def _patch_text(replacements: list) -> str:
    parts = []
    remaining = _MAX_DRAFT_TEXT + 1
    for replacement in replacements:
        if not isinstance(replacement, dict):
            continue
        for field, prefix in (("old_text", "- "), ("new_text", "+ ")):
            value = replacement.get(field)
            if not isinstance(value, str):
                continue
            separator = "\n" if parts else ""
            part = separator + prefix + value[:remaining].replace("\n", "\n" + prefix)
            parts.append(part[:remaining])
            remaining -= len(parts[-1])
            if not remaining:
                return "".join(parts)
    return "".join(parts)


def tool_preview(broker, name: str, arguments: dict, *, state: str, verified=(), rejected=False) -> None:
    if (not file_drafts_enabled() or not isinstance(arguments, dict)
            or name not in {*_TOOL_FIELDS, *_PATCH_TOOLS, "write_files"}):
        return
    key = {"write_files": "files", "apply_text_patches": "patches"}.get(name)
    entries = arguments.get(key) if key else [arguments]
    if not isinstance(entries, list):
        return
    for entry in entries[:_MAX_PATHS]:
        if not isinstance(entry, dict):
            continue
        path = _preview_path(broker, entry.get("path"), exact=rejected)
        if path is None:
            continue
        item_state = "Saved" if str(Path(broker.workspace).resolve() / path) in verified else state
        if rejected:
            _emit(path, item_state)
            continue
        content = entry.get(_TOOL_FIELDS.get(name, "content"))
        if name in _PATCH_TOOLS:
            replacements = entry.get("replacements")
            if not isinstance(replacements, list):
                continue
            content = _patch_text(replacements)
        if isinstance(content, str):
            if state == "Not saved (proposal)":
                phase("Checking file change")
            _emit(path, item_state, content)


class DraftView:
    """Lazy inline panel; observing a draft never changes focus or the transcript."""

    def __init__(self, gui, *, on_hide=None):
        composer = gui.prompt_box.master
        self.frame = gui.ttk.LabelFrame(composer.master, text="File draft (proposed content)")
        self.composer = composer
        self.selection = gui.tk.StringVar(master=gui.root)
        self.state = gui.tk.StringVar(master=gui.root)
        self.entries = {}
        self.dismissed = False
        self.rendered_content = ""
        self.full_window = None
        self.full_content = ""
        self.full_path = None
        self.gui = gui
        controls = gui.ttk.Frame(self.frame)
        controls.pack(fill="x")
        self.paths = gui.ttk.Combobox(controls, textvariable=self.selection, state="readonly")
        self.paths.pack(side="left", fill="x", expand=True)
        self.paths.bind("<<ComboboxSelected>>", self.render)
        gui.ttk.Label(controls, textvariable=self.state).pack(side="left", padx=5)
        gui.ttk.Button(controls, text="Hide", command=on_hide or self.hide).pack(side="right")
        self.text = gui.tk.Text(self.frame, height=6, wrap="char", state="disabled", takefocus=False,
                                background=gui.prompt_box.cget("background"),
                                foreground=gui.prompt_box.cget("foreground"))
        self.text.pack(fill="both", expand=True)
        self.preview_footer = gui.ttk.Frame(self.frame)
        gui.ttk.Label(self.preview_footer, text="Preview truncated").pack(side="left", padx=(4, 4))
        self.expand_button = gui.ttk.Button(self.preview_footer, text="+", width=3, command=self.open_full)
        self.expand_button.pack(side="left")

    def hide(self):
        self.dismissed = True
        self.close_full()
        self.frame.pack_forget()

    def reset(self):
        self.hide()
        self.dismissed = False
        self.entries.clear()
        self.paths.configure(values=())
        self.selection.set("")
        self.render()

    def finish(self):
        for path, (state, content, limited) in self.entries.items():
            if state in {"Not saved (draft)", "Not saved (proposal)"}:
                self.entries[path] = ("Save unverified", content, limited)
        self.render()

    def update(self, message: str):
        event = json.loads(message)
        path = str(event["path"])
        if path not in self.entries and len(self.entries) >= _MAX_PATHS:
            return
        previous = self.entries.get(path)
        state, content = event["state"], event["text"]
        limited = bool(event.get("capture_limited"))
        if state == "Rejected" and previous and previous[0].startswith("Not saved"):
            content, limited = previous[1:]
        self.entries[path] = (state, content, limited)
        self.paths.configure(values=tuple(self.entries))
        if not self.selection.get():
            self.selection.set(path)
        if not self.dismissed and not self.frame.winfo_manager():
            if self.composer.pack_info()["side"] == "bottom":
                # Bottom-packed controls reserve their space before the chat expands.
                self.frame.pack(fill="x", side="bottom", after=self.composer)
            else:
                self.frame.pack(fill="x", before=self.composer)
        self.render()

    def render_evidence(self) -> dict:
        return {
            "mapped": bool(self.frame.winfo_ismapped()),
            "height": self.frame.winfo_height(),
            "text_height": self.text.winfo_height(),
            "state": self.state.get(),
            "dismissed": self.dismissed,
        }

    def render(self, _event=None):
        path = self.selection.get()
        state, content, limited = self.entries.get(path, ("", "", False))
        if self.state.get() != state:
            self.state.set(state)
        if len(content) > _PREVIEW_TEXT or limited:
            if not self.preview_footer.winfo_manager():
                self.preview_footer.pack(fill="x", before=self.text)
        else:
            self.preview_footer.pack_forget()
        preview = content[-_PREVIEW_TEXT:]
        if preview != self.rendered_content:
            self._set_text(self.text, self.rendered_content, preview)
            self.text.see("end-1c")
            self.rendered_content = preview
        if self.full_window is not None:
            self.render_full(path, state, content, limited)

    @staticmethod
    def _set_text(widget, previous, content):
        widget.configure(state="normal")
        if content.startswith(previous):
            widget.insert("end", content[len(previous):])
        else:
            widget.delete("1.0", "end")
            widget.insert("1.0", content)
        widget.configure(state="disabled")

    def open_full(self):
        if self.full_window is not None:
            self.full_window.lift()
            return
        gui = self.gui
        window = self.full_window = gui.tk.Toplevel(gui.root)
        window.title("File draft")
        window.transient(gui.root)
        window.protocol("WM_DELETE_WINDOW", self.close_full)
        window.bind("<Escape>", lambda _event: self.close_full())
        self.full_status = gui.ttk.Label(window, padding=6)
        self.full_status.pack(fill="x")
        body = gui.ttk.Frame(window)
        body.pack(fill="both", expand=True)
        scrollbar = gui.ttk.Scrollbar(body, orient="vertical")
        self.full_text = gui.tk.Text(
            body, width=90, height=25, wrap="char", state="disabled",
            background=self.text.cget("background"), foreground=self.text.cget("foreground"),
            yscrollcommand=scrollbar.set,
        )
        scrollbar.configure(command=self.full_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.full_text.pack(side="left", fill="both", expand=True)
        self.render()

    def close_full(self):
        if self.full_window is not None:
            self.full_window.destroy()
            self.full_window = None
        self.full_content = ""
        self.full_path = None

    def render_full(self, path, state, content, limited):
        status = f"{path} | {state}"
        if limited:
            status += " | Capture limit reached; this draft is incomplete"
        self.full_status.configure(text=status)
        changed_path = path != self.full_path
        if content == self.full_content and not changed_path:
            return
        top, bottom = self.full_text.yview()
        self._set_text(self.full_text, self.full_content, content)
        if changed_path:
            self.full_text.yview_moveto(0)
        elif bottom >= 0.999:
            self.full_text.see("end-1c")
        elif not content.startswith(self.full_content):
            self.full_text.yview_moveto(top)
        self.full_path, self.full_content = path, content
