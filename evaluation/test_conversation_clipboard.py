"""Real model-free Tk-to-Windows clipboard and emitted-reasoning retention checks."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_reasoning_display as reasoning
from runtime.conversations import ConversationJournal

variant = receipts.variant
runtime = reasoning.runtime
display_runtime = reasoning.display_runtime


@pytest.mark.parametrize("visible", [False, True])
def test_emitted_reasoning_is_journaled_even_when_hidden(display_runtime, monkeypatch, tmp_path, visible):
    base, client, namespace, observed = display_runtime
    journal = ConversationJournal(tmp_path, reserve_bytes=0)
    namespace["_TOOL_CALL_CONTEXT"].get().conversation_journal = journal
    client.config.show_reasoning = visible
    try:
        reasoning.install_stream(monkeypatch, base, client, [
            reasoning.chunk(reasoning_content="emitted reasoning \u6f22\u5b57"), reasoning.chunk(content="answer"),
        ])
        client.chat(**reasoning.contracts.request(num_predict=128))
        snapshot = journal.snapshot()
        assert snapshot.count("emitted reasoning \u6f22\u5b57") == 1
        assert any(event[0] == "reasoning_delta" for event in observed) is visible
    finally:
        journal.close(timeout=5)


@pytest.mark.skipif(os.environ.get("QUBITZ_REAL_CLIPBOARD_CHECK") != "1",
                    reason="Explicit real Windows clipboard validation only")
def test_real_gui_clipboard_full_unicode_large_text(variant, monkeypatch, tmp_path):
    base = variant._APP.base
    loads = []

    def no_model(*_args):
        loads.append(True)
        pytest.fail("Model-free clipboard validation attempted model loading")

    monkeypatch.setattr(base.AgentRunner, "_get_llm", no_model)
    monkeypatch.setattr(base.QubitzGUI, "_schedule_startup_visibility_recovery", lambda _self: None)
    if hasattr(base.QubitzGUI, "_maybe_start_background_model_prewarm"):
        monkeypatch.setattr(base.QubitzGUI, "_maybe_start_background_model_prewarm", lambda _self: None)
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=receipts.ROOT))
    try:
        assert gui.conversation_journal.path.is_relative_to(tmp_path.resolve())
        monkeypatch.setattr(gui.messagebox, "showerror", lambda *_args, **_kwargs: None)
        gui.root.title("Qubitz Clipboard Validation - MODEL-FREE")
        gui.root.geometry("1200x1100")
        gui.root.deiconify()
        gui.root.update()
        assert gui.clipboard_button.winfo_ismapped()
        gui._append_transcript("user", "MODEL-FREE transcript and Clipboard validation; not a model conversation.")
        text = "early reasoning \U0001f30d \u6f22\u5b57 e\u0301\n" * 18000
        assert len(text) > gui._REASONING_DISPLAY_LIMIT
        assert gui.conversation_journal.record("reasoning", text)
        gui.event_queue.put(("tool", "tool-display sentinel"))
        gui.event_queue.put(("status", "status sentinel"))
        gui.event_queue.put(("error", "fixture error sentinel, not a runtime failure"))
        gui.root.update()
        # Copy All must not depend on anything surviving in the Text widget.
        gui.transcript.configure(state="normal")
        gui.transcript.delete("1.0", "end")
        gui.transcript.configure(state="disabled")
        expected = gui.conversation_journal.snapshot()
        gui.clipboard_button.invoke()
        deadline = time.monotonic() + 40
        while gui._copy_busy and time.monotonic() < deadline:
            gui.root.update()
            time.sleep(0.01)
        assert not gui._copy_busy
        receipt = gui.last_clipboard_receipt
        assert receipt["sha256"] == hashlib.sha256(expected.encode("utf-8")).hexdigest()
        assert receipt["windows_readback_equal"]
        assert text in expected and "tool-display sentinel" in expected and "error sentinel" in expected
        assert not loads
        destination = Path(os.environ["QUBITZ_CLIPBOARD_RECEIPT"])
        destination.write_text(json.dumps({**receipt, "model_load_attempts": 0,
            "visible_real_tk_button_invoked": True, "physical_mouse_click": False,
            "widget_cleared_before_copy": True, "transcript_path": str(gui.conversation_journal.path),
            "retention_error": gui.conversation_journal.error}, indent=2), encoding="utf-8")
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()
        gui.conversation_journal.close(timeout=5)
