"""Real Tk constructor isolation after shared repairs; no inference or native-health claim."""
from __future__ import annotations

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant


def test_repaired_shared_runtime_constructs_real_gui(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    loads = []

    def refuse_model(*_args, **_kwargs):
        loads.append(True)
        pytest.fail("Constructor smoke must not load a model")

    monkeypatch.setattr(base.AgentRunner, "_get_llm", refuse_model)
    monkeypatch.setattr(base.QubitzGUI, "_maybe_start_background_model_prewarm", lambda _self: None)
    monkeypatch.setattr(base.QubitzGUI, "_handle_wslg_copy_mode", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(base.QubitzGUI, "_launch_wslg_recovery", lambda *_args, **_kwargs: False)
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=receipts.ROOT))
    try:
        gui.root.withdraw()
        gui.root.update_idletasks()
        assert gui.root.winfo_exists()
        assert gui.root.title()
        assert not loads
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()
