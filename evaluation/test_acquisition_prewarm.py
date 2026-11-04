"""Model-free GUI acquisition and prewarm ordering contracts."""
from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

from runtime import model_acquisition


class Root:
    def __init__(self):
        self.scheduled = []

    def bind(self, *_args, **_kwargs):
        pass

    def after_idle(self, callback):
        self.scheduled.append(callback)

    def after(self, _delay, callback):
        self.scheduled.append(callback)


class GUI:
    def __init__(self, config):
        self.config = config
        self.root = Root()
        self.event_queue = queue.Queue()
        self.prewarm_calls = 0

    def _maybe_start_background_model_prewarm(self):
        self.prewarm_calls += 1


def test_prewarm_waits_for_selected_model_acquisition(tmp_path, monkeypatch):
    monkeypatch.setattr(model_acquisition, "_install_model_storage_preflight", lambda _base: None, raising=False)
    ready = tmp_path / "model.gguf"
    base = SimpleNamespace(
        QubitzGUI=GUI,
        DEFAULT_GGUF_MODEL_FILENAME=ready.name,
        GGUF_MODEL_PATH_ENV_NAME="QUBITZ_GGUF_MODEL_PATH",
        config_runtime_workspace=lambda _config: tmp_path,
        resolve_gguf_model_path=lambda *_args: ready if ready.exists() else None,
        download_default_gguf_model=lambda *_args: ready,
    )
    model_acquisition._install_selected_model_acquisition(SimpleNamespace(base=base))
    gui = base.QubitzGUI(SimpleNamespace(selected_model_filename=ready.name, model_path=None))
    gui._maybe_start_background_model_prewarm()
    assert gui.prewarm_calls == 0
    assert len(gui.root.scheduled) == 2
    gui._qubitz_model_acquisition_state = {"state": "downloading", "filename": ready.name}
    gui.root.scheduled.pop()()
    assert gui.prewarm_calls == 0
    ready.write_bytes(b"GGUF" + b"\0" * 28)
    gui._qubitz_model_acquisition_state = {"state": "downloaded", "filename": ready.name}
    gui.root.scheduled.pop()()
    assert gui.prewarm_calls == 1


def test_failed_acquisition_does_not_start_background_prewarm(tmp_path, monkeypatch):
    monkeypatch.setattr(model_acquisition, "_install_model_storage_preflight", lambda _base: None, raising=False)
    base = SimpleNamespace(
        QubitzGUI=GUI,
        DEFAULT_GGUF_MODEL_FILENAME="model.gguf",
        GGUF_MODEL_PATH_ENV_NAME="QUBITZ_GGUF_MODEL_PATH",
        config_runtime_workspace=lambda _config: tmp_path,
        resolve_gguf_model_path=lambda *_args: None,
        download_default_gguf_model=lambda *_args: Path(tmp_path / "model.gguf"),
    )
    model_acquisition._install_selected_model_acquisition(SimpleNamespace(base=base))
    gui = base.QubitzGUI(SimpleNamespace(selected_model_filename="model.gguf", model_path=None))
    gui._qubitz_model_acquisition_state = {"state": "error", "filename": "model.gguf"}
    gui._maybe_start_background_model_prewarm()
    assert gui.prewarm_calls == 0
    assert len(gui.root.scheduled) == 1
