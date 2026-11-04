"""Exercise every variant's GUI startup filter without loading models or Tk."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime import read_variant_source

from runtime.paths import variant_paths

ROOT = Path(__file__).resolve().parents[1]


class TranscriptSink:
    def __init__(self):
        self.messages = []

    def _append_transcript(self, role, message):
        self.messages.append((role, message))

    def _filter_gui_transcript_message(self, _role, message):
        return message


@pytest.fixture(scope="module", params=sorted(variant_paths(ROOT)),
                ids=lambda path: path.stem)
def gui_type(request):
    tree = ast.parse(read_variant_source(request.param))
    owner = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.ClassDef) and node.name == "TemperatureStatusGUI")
    method = next(node for node in owner.body
                  if isinstance(node, ast.FunctionDef) and node.name == "_append_transcript")
    selected = ast.ClassDef(name="StartupGUI", bases=[ast.Name(id="TranscriptSink", ctx=ast.Load())],
                            keywords=[], body=[method], decorator_list=[])
    module = ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[]))
    namespace = {
        "TranscriptSink": TranscriptSink,
        "base": SimpleNamespace(in_wsl=lambda: False),
        "_normalize_terminal_display_text": lambda text: text,
        "_format_gui_mounted_paths": lambda _role, text, **_kwargs: text,
    }
    exec(compile(module, str(request.param), "exec"), namespace)
    return namespace["StartupGUI"]


@pytest.mark.parametrize("message", [
    "GGUF model: local-model (local-model.gguf)",
    "  GGUF model: local-model (local-model.gguf)\n",
    "Model alias: local-model",
    "Runtime defaults: local settings",
    "Local-only wrapper active. Execution remains local.",
])
def test_redundant_system_startup_messages_are_hidden(gui_type, message):
    gui = gui_type()
    gui._append_transcript("system", message)
    assert not gui.messages


@pytest.mark.parametrize("role,message", [
    ("system", "Workspace: D:\\project"),
    ("system", "Embedding Model: BAAI/bge-code-v1"),
    ("system", "Local llama.cpp backend: http://127.0.0.1:8001"),
    ("system", "GGUF model download failed"),
    ("status", "GGUF model: download in progress"),
    ("error", "GGUF model: startup failed"),
    ("assistant", "GGUF model: explanation requested by the user"),
    ("user", "GGUF model: question about the selected model"),
])
def test_useful_messages_and_other_roles_remain_visible(gui_type, role, message):
    gui = gui_type()
    gui._append_transcript(role, message)
    assert gui.messages == [(role, message)]
