"""CPU-only reasoning-channel contracts; no model server or product GUI is started."""
from __future__ import annotations

import ast
import contextvars
import json
import queue
import re
import sys
import time
import tkinter as tk
from contextlib import contextmanager, suppress
from pathlib import Path
from tkinter import scrolledtext, ttk
from types import MethodType, SimpleNamespace

import httpx
import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from runtime import read_variant_source

runtime = contracts.runtime


@pytest.fixture
def display_runtime(runtime):
    base, client, namespace = runtime
    events = []
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar(
        "reasoning_display_fixture", default=None,
    )
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(callback=lambda *event: events.append(event)))
    namespace["sys"] = sys
    base.LlamaCppClient._normalize_tool_calls = staticmethod(lambda calls: calls or [])
    client.config.show_reasoning = True
    return base, client, namespace, events


def install_stream(monkeypatch, base, client, events, *, disconnect=False):
    payloads = []
    answers = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def iter_lines(self):
            for event in events:
                yield "data: " + json.dumps(event)
            if disconnect:
                raise httpx.ReadError("fixture disconnection")
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, method, url, json, **_options):
            payloads.append(json)
            yield Response()

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: HTTPClient())
    client.transport = SimpleNamespace(label="direct", base_url="http://fixture")
    base.set_qubitz_stream_callback(answers.append)
    return payloads, answers


def chunk(**delta):
    return {"choices": [{"delta": delta, "finish_reason": None}]}


@pytest.mark.parametrize("enabled", [False, True])
def test_stream_channel_is_opt_in_and_separate(display_runtime, monkeypatch, enabled):
    base, client, _, observed = display_runtime
    client.config.show_reasoning = enabled
    calls = [{"index": 0, "id": "call-1", "function": {"name": "demo", "arguments": '{"n":1}'}}]
    payloads, answers = install_stream(monkeypatch, base, client, [
        chunk(reasoning_content="Checking "), chunk(reasoning_content="the result."),
        chunk(content="Answer", tool_calls=calls),
    ])
    response = client.chat(**contracts.request(num_predict=128))
    assert response["message"]["content"] == "Answer"
    assert response["message"]["tool_calls"][0]["function"]["arguments"] == '{"n":1}'
    assert "reasoning_content" not in response["message"]
    assert "".join(answers) == "Answer"
    assert [event for event in observed if event[0] == "reasoning_delta"] == (
        [("reasoning_delta", "Checking the result.")] if enabled else []
    )
    status = json.loads(observed[-1][1])
    assert observed[-1][0] == "reasoning_status"
    assert status["received"] == len("Checking the result.")
    assert status["forwarded"] == (len("Checking the result.") if enabled else 0)
    assert len(payloads) == 1
    assert payloads[0]["messages"] == [{"role": "user", "content": "hello"}]
    assert "show_reasoning" not in payloads[0]


def test_nonstreaming_reasoning_never_replaces_answer(display_runtime):
    _, client, _, observed = display_runtime
    client._call_with_fallback = lambda *_args: {"choices": [{"message": {
        "content": "Answer", "reasoning_content": "Provisional reasoning", "tool_calls": [],
    }, "finish_reason": "stop"}]}
    assert client.chat(**contracts.request())["message"]["content"] == "Answer"
    assert observed[0] == ("reasoning_delta", "Provisional reasoning")
    assert json.loads(observed[-1][1])["received"] == len("Provisional reasoning")


def test_missing_channel_does_not_guess_from_content(display_runtime):
    _, client, _, observed = display_runtime
    client._call_with_fallback = lambda *_args: {"choices": [{"message": {
        "content": "Literal <think>markup</think> in a requested example", "reasoning_content": {},
    }, "finish_reason": "stop"}]}
    assert "<think>markup</think>" in client.chat(**contracts.request())["message"]["content"]
    assert len(observed) == 1 and observed[0][0] == "reasoning_status"
    status = json.loads(observed[0][1])
    assert status["received"] == 0 and status["forwarded"] == 0
    assert status["display_enabled"] is True
    assert status["reasoning_format"] == "unknown" and status["chat_format"] == "unknown"
    assert status["supports_reasoning_effort"] is None
    assert status["supports_preserve_reasoning"] is None


@pytest.mark.parametrize("streaming", [False, True])
def test_empty_tool_manifest_disables_tool_choice_and_parser(display_runtime, monkeypatch, streaming):
    base, client, _, _ = display_runtime
    if streaming:
        payloads, _answers = install_stream(monkeypatch, base, client, [chunk(content="3")])
    else:
        payloads = []

        def call(_method, _url, payload):
            payloads.append(payload)
            return {"choices": [{"message": {"content": "3", "tool_calls": []}, "finish_reason": "stop"}]}

        client._call_with_fallback = call
    assert client.chat(**contracts.request())["message"]["content"] == "3"
    assert payloads[0]["tools"] == []
    assert payloads[0]["tool_choice"] == "none"
    assert payloads[0]["parse_tool_calls"] is False


def test_partial_stream_keeps_diagnostics_without_replaying_request(display_runtime, monkeypatch):
    from runtime.transport_failures import TransportFailure

    base, client, _, observed = display_runtime
    payloads, _ = install_stream(
        monkeypatch, base, client, [chunk(reasoning_content="Partial reasoning")], disconnect=True,
    )
    client._call_with_fallback = lambda *_args: pytest.fail("Must not replay a partial stream")
    with pytest.raises(TransportFailure, match="fixture disconnection") as failure:
        client.chat(**contracts.request())
    assert failure.value.receipt["retry_permitted"] is False
    assert failure.value.receipt["completion_outcome"] == "unknown"
    assert observed[0] == ("reasoning_delta", "Partial reasoning")
    assert json.loads(observed[-1][1])["received"] == len("Partial reasoning")
    assert len(payloads) == 1


def test_optional_sink_failure_cannot_fail_the_task(display_runtime, capsys):
    _, client, namespace, _ = display_runtime

    def broken_sink(*_args):
        raise ValueError("Do not include private callback details")

    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(callback=broken_sink))
    client._call_with_fallback = lambda *_args: {"choices": [{"message": {
        "content": "Answer", "reasoning_content": "Provisional",
    }, "finish_reason": "stop"}]}
    assert client.chat(**contracts.request())["message"]["content"] == "Answer"
    error = capsys.readouterr().err
    assert "Reasoning display callback failed: ValueError" in error
    assert "private callback details" not in error


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class TimerRoot:
    def __init__(self):
        self.now = 0
        self.callbacks = {}
        self.serial = 0

    def after(self, delay, callback):
        self.serial += 1
        token = f"after-{self.serial}"
        self.callbacks[token] = (self.now + delay, callback)
        return token

    def after_idle(self, callback):
        return self.after(0, callback)

    def after_cancel(self, token):
        self.callbacks.pop(token, None)

    def advance(self, milliseconds):
        target = self.now + milliseconds
        while self.callbacks:
            token = min(self.callbacks, key=lambda key: self.callbacks[key][0])
            due, callback = self.callbacks[token]
            if due > target:
                break
            self.callbacks.pop(token)
            self.now = due
            callback()
        self.now = target


class Text:
    def __init__(self):
        self.text = ""
        self.state = "disabled"
        self.tags = {}
        self.marks = {}
        self.mark_gravities = {}
        self.tag_bindings = {}
        self.top = 0
        self.bindings = {}
        self.vbar = SimpleNamespace(configure=lambda **options: setattr(self, "scroll_command", options["command"]))

    def configure(self, **options):
        self.state = options.get("state", self.state)

    def insert(self, index, text, tags=()):
        start = self._offset(index)
        size = len(text)
        self.text = self.text[:start] + text + self.text[start:]
        self.tags = {
            name: [
                (a + size if a >= start else a, b + size if b > start else b)
                for a, b in ranges
            ] for name, ranges in self.tags.items()
        }
        self.marks = {
            name: offset + size if offset > start or (
                offset == start and self.mark_gravities.get(name, "right") == "right"
            ) else offset
            for name, offset in self.marks.items()
        }
        for tag in tags:
            ranges = self.tags.setdefault(tag, [])
            if ranges and ranges[-1][1] == start:
                ranges[-1] = (ranges[-1][0], start + size)
            else:
                ranges.append((start, start + size))

    def _offset(self, index):
        if index in {"end", "end-1c"}:
            return len(self.text)
        if index == "@0,0":
            return self.top
        name = str(index).split("+", 1)[0]
        return self.marks.get(name, 0) + sum(int(count) for count in re.findall(r"\+(\d+)c", str(index)))

    def index(self, index):
        return f"1.0+{self._offset(index)}c"

    def delete(self, start, end):
        start, end = self._offset(start), self._offset(end)
        self.text = self.text[:start] + self.text[end:]

        def shift(offset):
            return offset if offset <= start else max(start, offset - (end - start))

        for tag, ranges in self.tags.items():
            self.tags[tag] = [(shift(a), shift(b)) for a, b in ranges if shift(a) < shift(b)]
        self.marks = {name: shift(offset) for name, offset in self.marks.items()}
        self.top = shift(self.top)

    def mark_set(self, name, index):
        self.marks[name] = self._offset(index)

    def mark_gravity(self, name, gravity):
        self.mark_gravities[name] = gravity

    def mark_unset(self, name):
        self.marks.pop(name, None)
        self.mark_gravities.pop(name, None)

    def bind(self, sequence, callback, **_options):
        self.bindings[sequence] = callback

    def winfo_ismapped(self):
        return True

    def tag_ranges(self, tag):
        return tuple(f"1.0+{offset}c" for pair in self.tags.get(tag, []) for offset in pair)

    def tag_remove(self, tag, _start, _end):
        self.tags.pop(tag, None)

    def tag_delete(self, tag):
        self.tags.pop(tag, None)
        self.tag_bindings = {
            key: value for key, value in self.tag_bindings.items() if key[0] != tag
        }

    def tag_configure(self, *_args, **_kwargs):
        pass

    def tag_bind(self, tag, sequence, callback):
        self.tag_bindings[(tag, sequence)] = callback

    def count(self, start, end, _unit):
        return (self._offset(end) - self._offset(start),)

    def get(self, start, end):
        return self.text[self._offset(start):self._offset(end)]

    def yview(self, *args):
        if args:
            if args[0] == "moveto":
                self.top = min(max(0, len(self.text) - 80), round(float(args[1]) * len(self.text)))
            elif args[0] == "scroll":
                self.top = min(max(0, len(self.text) - 80), max(0, self.top + int(args[1]) * 10))
            else:
                self.top = self._offset(args[0])
        size = max(1, len(self.text))
        return (self.top / size, min(1.0, (self.top + 80) / size))

    def see(self, _index):
        self.top = max(0, len(self.text) - 80)


class TranscriptParent:
    def _start_prompt(self, prompt, *, queued=False):
        self.started = (prompt, queued)

    def _change_workspace(self):
        self.changed_workspace = True

    def _ensure_transcript_tags(self):
        pass

    def _append_transcript(self, role, message):
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"[{role}] {message}\n\n", (f"role_{role}",))
        self.transcript.configure(state="disabled")
        self.transcript.see("end")


def gui_subject(parent_type=None):
    tree = ast.parse(read_variant_source(contracts.REFERENCE))
    parent = parent_type or TranscriptParent
    preview_class = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                         and node.name == "EnhancedGUI")
    previews = [next(node for node in preview_class.body if isinstance(node, ast.FunctionDef)
                     and node.name == name)
                for name in ("_append_stream_delta", "_clear_stream_preview")]
    preview_namespace = {}
    exec(compile(ast.Module(body=previews, type_ignores=[]), "<answer-preview-contract>", "exec"), preview_namespace)  # noqa: S102 - Trusted local AST subject.
    for name in ("_append_stream_delta", "_clear_stream_preview"):
        setattr(parent, name, preview_namespace[name])
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "RunControlGUI")
    module = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    ), cls], type_ignores=[])
    namespace = {"original_gui": parent, "base": SimpleNamespace(
        pick_workspace_directory=lambda *_args, **_kwargs: None,
    ), "steering_status": "fixture-steering"}
    exec(compile(ast.fix_missing_locations(module), "<reasoning-gui-contract>", "exec"), namespace)  # noqa: S102 - Trusted local AST subject.
    gui = object.__new__(namespace["RunControlGUI"])
    gui.config = SimpleNamespace(show_reasoning=True, workspace=Path.cwd())
    gui.busy = False
    gui.filedialog = None
    gui.transcript = Text()
    gui.reasoning_visible_var = Variable(True)
    gui._reasoning_status_message = ""
    gui._reasoning_display_chars = 0
    gui.root = TimerRoot()
    hardened = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
                    and node.name == "HardenedGUI")
    drain_names = ("_drain_gui_events", "_consume_gui_events")
    drains = [next(node for node in hardened.body if isinstance(node, ast.FunctionDef)
                   and node.name == name) for name in drain_names]
    drain_namespace = {"base": SimpleNamespace(queue=queue), "time": time, "suppress": suppress,
                       "_normalize_terminal_display_text": lambda value: value}
    exec(compile(ast.Module(body=drains, type_ignores=[]), "<actual-gui-drain-contract>", "exec"), drain_namespace)  # noqa: S102 - Trusted local AST subject.
    for name in drain_names:
        setattr(gui, name, MethodType(drain_namespace[name], gui))
    return gui, tree


def test_gui_reasoning_defaults_to_visible_and_toggle_only_changes_display(monkeypatch):
    widgets = []

    def widget(master, **options):
        item = SimpleNamespace(master=master, options=options, pack=lambda **_kwargs: None,
                               pack_forget=lambda: None)
        widgets.append(item)
        return item

    class Parent(TranscriptParent):
        def __init__(self, config):
            self.config = config
            self.root = TimerRoot()
            self.transcript = Text()
            self.tk = SimpleNamespace(BooleanVar=lambda *, master, value: Variable(value),
                                      StringVar=lambda *, master, value: Variable(value))
            self.ttk = SimpleNamespace(Checkbutton=widget, Button=widget, Label=widget, Entry=widget)
            self.send_button = widget(SimpleNamespace())
            self.clear_button = widget(self.send_button.master)

    subject, _ = gui_subject(Parent)
    cls = type(subject)
    monkeypatch.setattr(cls, "_update_run_control_buttons", lambda _self: None)
    monkeypatch.setattr(cls, "_schedule_startup_visibility_recovery", lambda _self: None)
    monkeypatch.setattr(cls, "_prioritize_composer_layout", lambda _self: None)
    gui = cls(SimpleNamespace(thinking_effort="xhigh"))
    assert gui.config.show_reasoning and gui.reasoning_visible_var.get()
    checkbox = next(item for item in widgets if "variable" in item.options)
    assert checkbox.options["text"] == "Show Reasoning"
    assert gui._REASONING_READING_WPM == 300
    assert not hasattr(gui, "reasoning_wpm_var")
    assert not hasattr(gui, "reasoning_wpm_entry")
    assert not hasattr(gui, "_reasoning_speed_label")
    assert any(item.options.get("text") == "Jump to latest" for item in widgets)
    gui._append_reasoning_delta("Visible by default")
    gui.reasoning_visible_var.set(False)
    checkbox.options["command"]()
    gui._append_reasoning_delta("Hidden after unchecking")
    assert not gui.config.show_reasoning
    assert "Hidden after unchecking" not in gui.transcript.text
    assert "Visible by default" in gui.transcript.text
    gui.reasoning_visible_var.set(True)
    checkbox.options["command"]()
    gui._append_reasoning_delta("Visible again")
    assert "Visible again" in gui.transcript.text
    assert gui.config.thinking_effort == "xhigh"


def test_real_tk_reasoning_is_inline_bounded_and_survives_preview_cleanup(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()

    class Parent(TranscriptParent):
        def __init__(self, config):
            self.config = config
            self.root = root
            self.tk, self.ttk, self.scrolledtext = tk, ttk, scrolledtext
            frame = ttk.Frame(root)
            frame.pack(fill="both", expand=True)
            header = ttk.Frame(frame)
            header.pack(fill="x")
            self.status_var = tk.StringVar(master=root, value="Ready")
            self.change_workspace_button = ttk.Button(header, text="Change Workspace")
            self.change_workspace_button.pack(side="left")
            status_label = ttk.Label(header, textvariable=self.status_var)
            status_label.pack(side="left")
            self.status_label = status_label
            self.transcript = scrolledtext.ScrolledText(frame, state="disabled")
            self.transcript.pack(fill="both", expand=True)
            composer = ttk.Frame(frame)
            composer.pack(fill="both")
            self.prompt_box = tk.Text(composer)
            self.prompt_box.pack(side="left")
            buttons = ttk.Frame(composer)
            buttons.pack(side="left")
            self.send_button = ttk.Button(buttons, text="Send")
            self.send_button.pack()
            self.clear_button = ttk.Button(buttons, text="Clear")
            self.clear_button.pack()

        def _start_prompt(self, *_args, **_kwargs):
            pass

        def _change_workspace(self):
            pass

    try:
        subject, _ = gui_subject(Parent)
        cls = type(subject)
        monkeypatch.setattr(cls, "_update_run_control_buttons", lambda _self: None)
        monkeypatch.setattr(cls, "_schedule_startup_visibility_recovery", lambda _self: None)
        gui = cls(SimpleNamespace(thinking_effort="xhigh"))
        assert gui.config.show_reasoning
        assert gui.reasoning_visible_var.get()
        assert not gui.status_label.winfo_manager()
        assert not hasattr(gui, "reasoning_frame")
        assert not hasattr(gui, "reasoning_text")
        checkbox = next(child for child in gui.send_button.master.winfo_children()
                        if isinstance(child, ttk.Checkbutton))
        assert checkbox.cget("text") == "Show Reasoning"
        assert not any(isinstance(child, ttk.Label) and child.cget("text") in {"Reading Speed", "Speed"}
                       for child in gui.send_button.master.winfo_children())
        assert not hasattr(gui, "reasoning_wpm_entry")
        root.update_idletasks()
        assert gui.config.show_reasoning
        gui._REASONING_DISPLAY_LIMIT = 20
        gui._append_transcript("user", "Keep this request")
        gui._append_reasoning_delta("0123456789")
        assert gui.transcript.tag_cget("role_reasoning", "foreground") == "#969696"
        gui._append_transcript("tool", "Keep this receipt")
        gui._append_reasoning_delta("abcdefghijklmnopqrstuvwxyz")
        gui._append_stream_delta("Replaceable answer preview")
        gui._clear_stream_preview()
        gui._append_transcript("assistant", "Final answer")
        transcript = gui.transcript.get("1.0", "end-1c")
        assert "[reasoning] (runtime-emitted; provisional; live tail capped at 20 characters)\n" in transcript
        assert "ghijklmnopqrstuvwxyz" in transcript and "0123456789" not in transcript
        assert "Keep this request" in transcript and "Keep this receipt" in transcript
        assert "Final answer" in transcript and "Replaceable answer preview" not in transcript
        assert str(gui.transcript.cget("state")) == "disabled"
        gui._start_prompt("new task")
        collapsed = gui.transcript.get("1.0", "end-1c")
        assert "[reasoning] Provisional;" in collapsed and "ghijklmnopqrstuvwxyz" not in collapsed
        assert "Keep this request" in collapsed and "Keep this receipt" in collapsed
        assert "Final answer" in collapsed
        gui._toggle_reasoning_archive(1)
        expanded = gui.transcript.get("1.0", "end-1c")
        assert "ghijklmnopqrstuvwxyz" in expanded
        assert expanded.index("Keep this receipt") < expanded.index("ghijklmnopqrstuvwxyz")
        assert expanded.index("ghijklmnopqrstuvwxyz") < expanded.index("Final answer")
        gui._toggle_reasoning_archive(1)
        assert "ghijklmnopqrstuvwxyz" not in gui.transcript.get("1.0", "end-1c")
        assert not gui.transcript.tag_ranges("reasoning_current_turn")
        checkbox.invoke()
        assert not gui.config.show_reasoning
        before = gui.transcript.get("1.0", "end-1c")
        gui._append_reasoning_delta("Hidden after unchecking")
        assert gui.transcript.get("1.0", "end-1c") == before
        checkbox.invoke()
        assert gui.config.show_reasoning
        assert gui.config.thinking_effort == "xhigh"
    finally:
        root.destroy()


def test_gui_display_bounds_memory_and_resets_at_task_workspace_boundaries():
    gui, _ = gui_subject()
    gui._REASONING_DISPLAY_LIMIT = 20
    gui._append_reasoning_delta("0123456789")
    gui._append_transcript("tool", "Keep receipt")
    gui._append_reasoning_delta("abcdefghijklmnopqrstuvwxyz")
    assert "ghijklmnopqrstuvwxyz" in gui.transcript.text
    assert "0123456789" not in gui.transcript.text
    assert "Keep receipt" in gui.transcript.text
    assert gui._reasoning_display_chars == 20
    assert gui.transcript.state == "disabled"
    gui._start_prompt("new task", queued=True)
    previous = gui.transcript.text
    assert "[reasoning] Provisional;" in previous
    assert "ghijklmnopqrstuvwxyz" not in previous
    assert "ghijklmnopqrstuvwxyz" in "".join(gui._reasoning_archives[1])
    assert gui._reasoning_display_chars == 0
    assert gui.started == ("new task", True)
    gui._append_reasoning_delta("Previous workspace")
    gui._change_workspace()
    assert gui.transcript.text.startswith(previous)
    assert "Previous workspace" in gui._reasoning_archives[2]
    assert not getattr(gui, "_qubitz_workspace_transition", None)
    gui.config.show_reasoning = False
    gui._append_reasoning_delta("Not visible")
    assert "Not visible" not in gui.transcript.text


def test_inline_bound_trims_across_reasoning_blocks_without_touching_receipts():
    gui, _ = gui_subject()
    gui._REASONING_DISPLAY_LIMIT = 15
    for number, text in enumerate(("0123456789", "abcdefghij", "KLMNOPQRST")):
        gui._append_reasoning_delta(text)
        gui._append_transcript("tool", f"Receipt {number}")
    ranges = gui.transcript.tag_ranges("reasoning_current_turn")
    retained = "".join(gui.transcript.get(a, b) for a, b in zip(ranges[::2], ranges[1::2]))
    assert retained == "fghijKLMNOPQRST"
    assert gui._reasoning_display_chars == 15
    assert all(f"Receipt {number}" in gui.transcript.text for number in range(3))


def test_default_live_tail_and_archives_are_bounded_for_long_reasoning():
    gui, _ = gui_subject()
    limit = gui._REASONING_DISPLAY_LIMIT
    assert limit == 12000
    for number in range(11):
        gui._append_reasoning_delta("discarded prefix " + "x" * limit + f" tail {number}")
        assert gui._reasoning_display_chars == limit
        assert len(gui._reasoning_block_text) <= limit
        assert "discarded prefix" not in gui.transcript.text
        gui._append_transcript("tool", f"Verified receipt {number}")
        gui._collapse_reasoning_turn()
        assert all(sum(map(len, blocks)) <= limit for blocks in gui._reasoning_archives.values())
    assert len(gui._reasoning_archives) == 10
    assert sum(len(block) for blocks in gui._reasoning_archives.values() for block in blocks) <= 120000
    assert all(f"Verified receipt {number}" in gui.transcript.text for number in range(11))
    gui._toggle_reasoning_archive(11)
    assert "tail 10" in gui.transcript.text


def test_large_reasoning_is_trimmed_only_in_display_not_journal_or_reply(display_runtime, tmp_path):
    from runtime.conversations import ConversationJournal

    _, client, namespace, observed = display_runtime
    gui, _ = gui_subject()
    raw = "early reasoning sentinel " + "x" * (gui._REASONING_DISPLAY_LIMIT * 2) + " late sentinel"
    journal = ConversationJournal(tmp_path, reserve_bytes=0)
    namespace["_TOOL_CALL_CONTEXT"].get().conversation_journal = journal
    payloads = []

    def reply(_method, _path, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": "Answer", "reasoning_content": raw,
                                         "tool_calls": []}, "finish_reason": "stop"}]}

    client._call_with_fallback = reply
    try:
        response = client.chat(**contracts.request())
        for kind, text in observed:
            if kind == "reasoning_delta":
                gui._append_reasoning_delta(text)
        assert gui._reasoning_display_chars == gui._REASONING_DISPLAY_LIMIT
        assert "early reasoning sentinel" not in gui.transcript.text
        assert "late sentinel" in gui.transcript.text
        assert raw in journal.snapshot()
        assert response["message"] == {"role": "assistant", "content": "Answer", "tool_calls": []}
        assert len(payloads) == 1
        assert payloads[0]["messages"] == [{"role": "user", "content": "hello"}]
    finally:
        journal.close(timeout=5)


@pytest.mark.parametrize("rate,words,expected", [
    ("300", 1, 2000), ("300", 25, 5000), ("300", 200, 20000),
    ("238", 25, 6303), ("invalid", 25, 5000), ("0", 25, 20000),
    ("900", 25, 2500),
])
def test_developer_constant_reading_hold_rate_and_bounds(rate, words, expected):
    gui, _ = gui_subject()
    gui._REASONING_READING_WPM = rate
    assert gui._reasoning_hold_ms("word " * words) == expected


def test_reading_hold_counts_streamed_words_without_pausing_tail_following():
    gui, _ = gui_subject()
    gui._append_reasoning_delta("Testing a split wor")
    gui._append_reasoning_delta("d " + "word " * 21)
    gui._append_transcript("tool", "Receipt " * 100)
    token = gui._reading_hold_after_id
    assert gui.root.callbacks[token][0] == 5000
    gui._append_stream_delta("Answer preview " * 100)
    gui._clear_stream_preview()
    gui._append_transcript("assistant", "Final answer " * 100)
    gui._append_reasoning_delta("Another block " * 30)
    gui._append_transcript("status", "Still processing")
    assert gui.transcript.yview()[1] == 1.0
    assert "Final answer" in gui.transcript.text and "Receipt" in gui.transcript.text
    assert "Answer preview" not in gui.transcript.text
    assert gui._reading_hold_after_id == token  # No growing backlog of reading timers.
    gui.root.advance(4999)
    assert gui.transcript.yview()[1] == 1.0
    gui.root.advance(1)
    assert gui._reading_hold_after_id is None
    assert gui.transcript.yview()[1] == 1.0


@pytest.mark.parametrize("resume", ["scroll", "jump"])
def test_all_output_follows_unless_user_scrolls_and_resumes_at_latest(resume):
    gui, _ = gui_subject()
    gui._append_reasoning_delta("Initial reasoning\n" * 100)
    gui._append_transcript("tool", "Initial receipt\n" * 100)
    assert gui._reading_hold_after_id is not None

    def append_lines():
        for append, text in (
            (gui._append_reasoning_delta, "New reasoning line\n"),
            (gui._append_stream_delta, "New answer line\n"),
            (lambda text: gui._append_transcript("tool", text), "New tool line\n"),
            (lambda text: gui._append_transcript("status", text), "New status line\n"),
            (lambda text: gui._append_transcript("assistant", text), "Final answer line\n"),
        ):
            append(text)
            yield

    for _ in append_lines():
        assert gui.transcript.yview()[1] == 1.0
    gui._scroll_transcript("moveto", 0.1)
    top = gui.transcript.top
    for _ in append_lines():
        assert gui.transcript.top == top
    if resume == "scroll":
        gui._scroll_transcript("moveto", 1.0)
    else:
        gui._jump_to_latest()
    for _ in append_lines():
        assert gui.transcript.yview()[1] == 1.0
    assert gui._reading_follow_tail


def test_manual_scroll_holds_indefinitely_and_jump_resumes():
    gui, _ = gui_subject()
    gui._append_reasoning_delta("word " * 100)
    gui._append_transcript("assistant", "Answer " * 100)
    old_callback = gui.root.callbacks[gui._reading_hold_after_id][1]
    gui._scroll_transcript("moveto", 0.1)
    top = gui.transcript.top
    assert not gui._reading_follow_tail and gui._reading_hold_after_id is None
    gui._append_transcript("tool", "Receipt " * 100)
    gui.root.advance(60000)
    old_callback()  # Even an already-queued stale callback cannot release a manual lock.
    assert gui.transcript.top == top
    gui._jump_to_latest()
    assert gui._reading_follow_tail and gui.transcript.yview()[1] == 1.0
    gui._append_transcript("status", "Following tail " * 100)
    assert gui.transcript.yview()[1] == 1.0
    gui.transcript.yview("moveto", 0.0)
    gui._queue_transcript_scroll_check()
    gui.root.advance(0)
    assert not gui._reading_follow_tail
    gui._scroll_transcript("moveto", 1.0)
    assert gui._reading_follow_tail


def test_pending_scroll_input_wins_over_incoming_output_and_jump_cancels_it():
    gui, _ = gui_subject()
    gui._append_reasoning_delta("word " * 100)
    gui._append_transcript("tool", "Receipt " * 100)
    gui._queue_transcript_scroll_check()
    gui.transcript.yview("scroll", -3, "units")  # Tk's native binding follows the widget binding.
    top = gui.transcript.top
    gui._append_transcript("assistant", "Answer " * 100)  # A timer can run before the idle callback.
    assert gui.transcript.top == top and gui._reading_hold_after_id is None
    gui.root.advance(0)
    assert not gui._reading_follow_tail
    gui._queue_transcript_scroll_check()
    gui._jump_to_latest()
    assert gui._reading_scroll_after_id is None and not gui.root.callbacks
    gui.root.advance(0)
    assert gui._reading_follow_tail and gui.transcript.yview()[1] == 1.0


def test_scrolling_near_the_bottom_of_a_long_chat_still_holds_position():
    gui, _ = gui_subject()
    gui._append_transcript("assistant", "Long history " * 100000)
    gui._scroll_transcript("moveto", 0.9998)
    top = gui.transcript.top
    assert gui.transcript.yview()[1] > 0.999
    assert not gui._reading_follow_tail
    gui._append_transcript("status", "Later update")
    assert gui.transcript.top == top


@pytest.mark.parametrize("boundary", ["task", "workspace", "toggle", "close_callbacks"])
def test_reading_timers_are_cancelled_at_boundaries(boundary):
    gui, _ = gui_subject()
    gui._append_reasoning_delta("word " * 20)
    gui._append_transcript("tool", "Receipt " * 100)
    stale = gui.root.callbacks[gui._reading_hold_after_id][1]
    if boundary == "task":
        gui._start_prompt("Next task")
    elif boundary == "workspace":
        gui._change_workspace()
    elif boundary == "toggle":
        gui.reasoning_visible_var.set(False)
        gui._toggle_reasoning_display()
    else:
        gui._queue_transcript_scroll_check()
        gui._cancel_reading_callbacks()
    assert not gui.root.callbacks
    gui._scroll_transcript("moveto", 0.0)
    top = gui.transcript.top
    stale()
    assert gui.transcript.top == top
    assert "word" in gui.transcript.text


def test_real_tk_output_follows_until_manual_scroll_and_resumes(monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.title("Qubitz tail following CPU-only widget test")
    root.geometry("600x650")

    class Parent(TranscriptParent):
        def __init__(self, config):
            self.config, self.root, self.tk, self.ttk = config, root, tk, ttk
            self.transcript = scrolledtext.ScrolledText(root, state="disabled", height=5)
            self.transcript.pack(fill="both", expand=True)
            composer = ttk.Frame(root)
            composer.pack(fill="both")
            buttons = ttk.Frame(composer)
            buttons.pack()
            self.send_button = ttk.Button(buttons)
            self.clear_button = ttk.Button(buttons)

    try:
        subject, _ = gui_subject(Parent)
        cls = type(subject)
        monkeypatch.setattr(cls, "_update_run_control_buttons", lambda _self: None)
        monkeypatch.setattr(cls, "_schedule_startup_visibility_recovery", lambda _self: None)
        gui = cls(SimpleNamespace(thinking_effort="xhigh"))
        root.update()
        gui._append_transcript("user", "Earlier history\n" * 50)
        gui._append_reasoning_delta("One reasoning line\n" * 5)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 1.0 - 1e-9
        gui._append_transcript("tool", "Tool receipt\n" * 80)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 1.0 - 1e-9
        gui._append_stream_delta("Answer preview\n" * 80)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 1.0 - 1e-9
        gui._clear_stream_preview()
        gui._append_transcript("assistant", "Final answer\n" * 80)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 1.0 - 1e-9
        assert gui._reading_hold_after_id is not None
        serviced = []
        root.after(0, lambda: serviced.append("tool events still run"))
        root.update()
        assert serviced == ["tool events still run"]
        root.after(3200, root.quit)
        root.mainloop()
        assert gui._reading_hold_after_id is None and gui.transcript.yview()[1] >= 0.999
        gui._append_reasoning_delta("New reasoning\n" * 10)
        gui._append_transcript("tool", "More receipts\n" * 80)
        gui.transcript.event_generate("<Button-4>")
        root.update()
        assert gui._reading_hold_after_id is None and not gui._reading_follow_tail
        gui._scroll_transcript("moveto", 0.2)
        root.update_idletasks()
        top = gui.transcript.index("@0,0")
        gui._append_reasoning_delta("Held reasoning\n" * 30)
        gui._append_stream_delta("Held answer preview\n" * 30)
        gui._clear_stream_preview()
        gui._append_transcript("status", "Held status\n" * 30)
        gui._append_transcript("assistant", "Later answer\n" * 80)
        root.update_idletasks()
        assert gui.transcript.index("@0,0") == top
        assert gui._reading_hold_after_id is None and not gui._reading_follow_tail
        gui._scroll_transcript("moveto", 1.0)
        root.update_idletasks()
        assert gui._reading_follow_tail
        gui._append_reasoning_delta("Resumed reasoning\n" * 30)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 1.0 - 1e-9
        gui._scroll_transcript("moveto", 0.2)
        gui._jump_to_latest()
        gui._append_transcript("status", "Following after jump\n" * 30)
        root.update_idletasks()
        assert gui.transcript.yview()[1] >= 0.999
        text = gui.transcript.get("1.0", "end-1c")
        assert "One reasoning line" in text and "Final answer" in text
        assert "Answer preview" not in text and gui.config.thinking_effort == "xhigh"
    finally:
        root.destroy()


@pytest.mark.parametrize("width,height", [(800, 800), (1800, 1000)])
def test_real_product_theme_and_layout_keep_pacing_and_run_controls_visible(monkeypatch, width, height):
    tree = ast.parse(read_variant_source(contracts.REFERENCE))
    embedded = ast.literal_eval(next(node.value for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_EMBEDDED_BASE_SOURCE" for target in node.targets)))
    base_tree = ast.parse(embedded)
    namespace = {target.id: ast.literal_eval(node.value)
                 for node in base_tree.body if isinstance(node, ast.Assign)
                 for target in node.targets if isinstance(target, ast.Name) and target.id.startswith("UI_")}
    namespace["DEFAULT_GGUF_MODEL_LABEL"] = "CPU GUI fixture"
    base_gui = next(node for node in base_tree.body if isinstance(node, ast.ClassDef) and node.name == "QubitzGUI")
    methods = [node for node in base_gui.body if isinstance(node, ast.FunctionDef)
               and node.name in {"_build_layout", "_apply_theme"}]
    exec(compile(ast.Module(body=methods, type_ignores=[]), "<product-layout-contract>", "exec"), namespace)  # noqa: S102 - Trusted local AST subject.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.title("Qubitz themed layout CPU-only widget test")
    root.geometry(f"{width}x{height}")

    class Parent(TranscriptParent):
        _apply_theme = namespace["_apply_theme"]
        _build_layout = namespace["_build_layout"]

        def __init__(self, config):
            self.config, self.root, self.tk, self.ttk, self.scrolledtext = config, root, tk, ttk, scrolledtext
            self.status_var = tk.StringVar(master=root, value="Ready")
            self.num_ctx_var = tk.StringVar(master=root, value="16384")
            self.num_predict_var = tk.StringVar(master=root, value="4096")
            self._apply_theme()
            self._build_layout()
            buttons = self.send_button.master
            ttk.Label(buttons, text="Effort").pack(fill="x", pady=(12, 0))
            self.thinking_effort_combo = ttk.Combobox(buttons, values=("xhigh",), state="readonly", width=8)
            self.thinking_effort_combo.pack(fill="x", pady=(4, 0))
            self.access_bar = ttk.Frame(self.prompt_box.master.master)
            self.access_bar.pack(fill="x", pady=(8, 0))
            ttk.Label(self.access_bar, text="Access and network settings").pack(side="left")

    for name in ("send_prompt", "clear_input", "_handle_copy_shortcut", "_handle_send_shortcut",
                 "_handle_newline_shortcut", "_handle_cancel_shortcut"):
        setattr(Parent, name, lambda *_args: None)
    try:
        subject, _ = gui_subject(Parent)
        cls = type(subject)
        monkeypatch.setattr(cls, "_schedule_startup_visibility_recovery", lambda _self: None)
        gui = cls(SimpleNamespace(thinking_effort="xhigh"))
        root.update()
        assert not hasattr(gui, "reasoning_wpm_entry")
        assert not hasattr(gui, "reasoning_wpm_var")
        assert not hasattr(gui, "_reasoning_speed_label")
        checkbox = next(widget for widget in gui._prompt_settings_frame.place_slaves()
                        if isinstance(widget, ttk.Checkbutton))
        assert checkbox.winfo_rooty() == gui.thinking_effort_combo.winfo_rooty()
        assert checkbox.winfo_rootx() >= gui.thinking_effort_combo.winfo_rootx() + gui.thinking_effort_combo.winfo_width() + 8
        assert checkbox.winfo_rooty() >= gui.prompt_box.winfo_rooty() + gui.prompt_box.winfo_height()
        style = ttk.Style(root)
        assert style.lookup("TEntry", "fieldbackground") == namespace["UI_PANEL"]
        assert style.lookup("TEntry", "foreground") == namespace["UI_TEXT"]
        assert namespace["UI_TEXT"] != namespace["UI_PANEL"]
        for widget in (checkbox, gui.send_button, gui.steer_button,
                       gui.cancel_button, gui.clear_button, gui.access_bar):
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty() >= root.winfo_rooty()
            assert widget.winfo_rooty() + widget.winfo_height() <= root.winfo_rooty() + root.winfo_height()
        assert gui.transcript.winfo_height() > 80
        assert gui.transcript.winfo_rooty() < gui.prompt_box.winfo_rooty() < gui.access_bar.winfo_rooty()
    finally:
        root.destroy()


def test_poll_batches_reasoning_drops_stale_runs_and_keeps_it_out_of_transcript_logs():
    gui, tree = gui_subject()
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "HardenedGUI")
    poll = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_poll_events")
    namespace = {"base": SimpleNamespace(queue=queue), "time": time, "suppress": suppress,
                 "_normalize_terminal_display_text": lambda value: value}
    exec(compile(ast.Module(body=[poll], type_ignores=[]), "<reasoning-poll-contract>", "exec"), namespace)  # noqa: S102 - Trusted local AST subject.
    gui.event_queue = queue.Queue()
    for event in [(1, "reasoning_delta", "stale"), (2, "reasoning_delta", "First "),
                  (2, "reasoning_delta", "second"), (2, "status", "Working")]:
        gui.event_queue.put(event)
    gui._active_gui_run_id = 2
    gui.status_var = Variable()
    gui.root = SimpleNamespace(after=lambda *_args: None)
    gui.tk = SimpleNamespace(TclError=RuntimeError)
    gui._append_stream_delta = lambda *_args: pytest.fail("Reasoning entered answer preview")
    gui._clear_stream_preview = lambda: None
    transcript = []
    logged = []
    gui._append_transcript = lambda *args: transcript.append(args)
    gui._qubitz_gui_log_event = lambda *args, **kwargs: logged.append((args, kwargs))
    namespace["_poll_events"](gui)
    assert "[reasoning] (runtime-emitted; provisional; live tail capped at 12,000 characters)\nFirst second" in gui.transcript.text
    assert "stale" not in gui.transcript.text
    assert transcript == [("status", "Working")]
    assert len(logged) == 1 and logged[0][1]["kind"] == "status"


def test_interleaved_reasoning_and_answer_events_preserve_order_and_final_answer():
    gui, tree = gui_subject()
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "HardenedGUI")
    poll = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_poll_events")
    namespace = {"base": SimpleNamespace(queue=queue), "time": time, "suppress": suppress,
                 "_normalize_terminal_display_text": lambda value: value}
    exec(compile(ast.Module(body=[poll], type_ignores=[]), "<inline-order-contract>", "exec"), namespace)  # noqa: S102 - Trusted local AST subject.
    gui.event_queue = queue.Queue()
    gui._active_gui_run_id = 2
    gui.status_var = Variable()
    gui.root = SimpleNamespace(after=lambda *_args: None)
    gui.tk = SimpleNamespace(TclError=RuntimeError)
    logged = []
    gui._qubitz_gui_log_event = lambda *args, **kwargs: logged.append(kwargs)
    gui._finalize_elapsed_message = lambda text: text
    for event in [(2, "reasoning_delta", "First reasoning"),
                  (2, "assistant_delta", "Temporary preview"),
                  (2, "reasoning_delta", "Later reasoning"),
                  (2, "assistant_delta", "New preview"),
                  (2, "answer", "Final answer")]:
        gui.event_queue.put(event)
    namespace["_poll_events"](gui)
    gui._collapse_reasoning_turn()
    text = gui.transcript.text
    assert "First reasoning" not in text and "Later reasoning" not in text
    assert "[reasoning] Provisional;" in text and "Final answer" in text
    gui._toggle_reasoning_archive(1)
    expanded = gui.transcript.text
    assert expanded.index("First reasoning") < expanded.index("Later reasoning") < expanded.index("Final answer")
    assert "Temporary preview" not in text and "New preview" not in text
    assert [event["kind"] for event in logged] == ["answer"]
    assert gui._reasoning_total_displayed == len("First reasoningLater reasoning")


def test_expanded_reasoning_preserves_interleaved_tool_order():
    gui, _ = gui_subject()
    gui._append_reasoning_delta("First thought")
    gui._append_transcript("tool", "Verified receipt")
    gui._append_reasoning_delta("Second thought")
    gui._append_transcript("assistant", "Final answer")
    gui._collapse_reasoning_turn()
    collapsed = gui.transcript.text
    assert "First thought" not in collapsed and "Second thought" not in collapsed
    assert "Verified receipt" in collapsed and "Final answer" in collapsed
    gui._toggle_reasoning_archive(1)
    expanded = gui.transcript.text
    assert expanded.index("First thought") < expanded.index("Verified receipt")
    assert expanded.index("Verified receipt") < expanded.index("Second thought")
    assert expanded.index("Second thought") < expanded.index("Final answer")
    gui._toggle_reasoning_archive(1)
    assert gui.transcript.text == collapsed


def test_completed_reasoning_retains_only_ten_expandable_turns():
    gui, _ = gui_subject()
    for index in range(11):
        gui._append_reasoning_delta(f"Reasoning turn {index} first")
        gui._append_transcript("tool", f"Receipt {index}")
        gui._append_reasoning_delta(f"Reasoning turn {index} second")
        gui._collapse_reasoning_turn()
    assert len(gui._reasoning_archives) == 10
    assert 1 not in gui._reasoning_archives
    assert "[reasoning] Earlier text cleared." in gui.transcript.text
    assert "reasoning_archive_anchor_1_1" not in gui.transcript.marks
    assert not any(key[0] == "reasoning_archive_summary_1" for key in gui.transcript.tag_bindings)
    gui._toggle_reasoning_archive(1)
    assert "Reasoning turn 0" not in gui.transcript.text
    gui._toggle_reasoning_archive(11)
    expanded = gui.transcript.text
    assert expanded.index("Reasoning turn 10 first") < expanded.index("Receipt 10")
    assert expanded.index("Receipt 10") < expanded.index("Reasoning turn 10 second")


@pytest.mark.parametrize("request_method", ["_request_cancel", "_cancel_active_prompt"])
def test_cancel_feedback_is_logged_once_without_header_status(request_method):
    calls = []

    class Parent(TranscriptParent):
        def _request_cancel(self):
            if not self.busy or self.cancel_requested:
                return
            self.cancel_requested = True
            calls.append("cancel")

    gui, _ = gui_subject(Parent)
    gui.busy = True
    gui.cancel_requested = False
    getattr(gui, request_method)()
    getattr(gui, request_method)()
    assert calls == ["cancel"]
    assert gui.transcript.text.count("Cancellation requested for the active task") == 1


@pytest.mark.parametrize("scroll_before_done", [False, True])
def test_manual_scroll_keeps_completed_reasoning_until_next_task(scroll_before_done):
    gui, _ = gui_subject()
    gui._append_reasoning_delta("word " * 100)
    gui._append_transcript("assistant", "Final answer " * 100)
    if scroll_before_done:
        gui._scroll_transcript("moveto", 0.1)
    gui._collapse_reasoning_turn(defer_for_reading=True)
    if not scroll_before_done:
        assert gui._reasoning_collapse_pending
        gui._scroll_transcript("moveto", 0.1)
    assert not gui._reasoning_collapse_pending
    gui.root.advance(20000)
    assert "word " in gui.transcript.text
    gui._start_prompt("Next task")
    assert "word " not in gui.transcript.text
    assert "[reasoning] Provisional;" in gui.transcript.text


def test_event_queue_reaches_done_during_the_reading_hold():
    gui, tree = gui_subject()
    cls = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "HardenedGUI")
    poll = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "_poll_events")
    namespace = {"base": SimpleNamespace(queue=queue), "time": time, "suppress": suppress,
                 "_normalize_terminal_display_text": lambda value: value}
    exec(compile(ast.Module(body=[poll], type_ignores=[]), "<reading-hold-poll-contract>", "exec"), namespace)  # noqa: S102 - Trusted local AST subject.
    gui.event_queue = queue.Queue()
    gui._active_gui_run_id = 1
    gui.status_var = Variable()
    gui.tk = SimpleNamespace(TclError=RuntimeError)
    gui._qubitz_gui_log_event = lambda *_args, **_kwargs: None
    gui._finalize_elapsed_message = lambda value: value
    gui.prompt_box = SimpleNamespace(focus_set=lambda: None)
    gui.pending_prompts = []
    gui.busy = True
    gui._set_busy = lambda value: setattr(gui, "busy", value)
    gui._poll_events = lambda: namespace["_poll_events"](gui)
    for kind, text in [("reasoning_delta", "word " * 100),
                       ("tool", "A verified receipt\n" * 50),
                       ("assistant_delta", "Replaceable preview"),
                       ("answer", "Final answer\n" * 50), ("done", "")]:
        gui.event_queue.put((1, kind, text))
    gui._poll_events()
    assert not gui.busy and gui.event_queue.empty()
    assert gui.root.now == 0 and gui._reading_hold_after_id is not None
    assert "A verified receipt" in gui.transcript.text and "Final answer" in gui.transcript.text
    assert "word " in gui.transcript.text and gui._reasoning_collapse_pending
    assert "Replaceable preview" not in gui.transcript.text
    assert gui.transcript.yview()[1] == 1.0
    gui.root.advance(20000)
    assert gui._reading_hold_after_id is None and not gui._reasoning_collapse_pending
    assert "word " not in gui.transcript.text
    assert "[reasoning] Provisional;" in gui.transcript.text
    assert "A verified receipt" in gui.transcript.text and "Final answer" in gui.transcript.text
