"""CPU fixtures must not read, lock or overwrite application-owned memory."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_followup_state_receipts as receipts

import qubitz_release_gate as gate

variant = receipts.variant
ROOT = Path(__file__).resolve().parents[1]


def test_application_runtime_memory_is_test_owned(variant, tmp_path):
    store = variant._APP.base.MemoryStore(ROOT)
    assert store.workspace != ROOT
    assert store.memory_dir.is_relative_to(tmp_path)
    assert store.current_path.is_file()


def test_explicit_disposable_memory_workspace_is_preserved(variant, tmp_path):
    workspace = tmp_path / "explicit"
    workspace.mkdir()
    store = variant._APP.base.MemoryStore(workspace)
    assert store.workspace == workspace
    assert store.memory_dir == workspace / ".memory"


@pytest.fixture
def gate_runtime(tmp_path, monkeypatch):
    root = tmp_path / "application"
    (root / ".cache").mkdir(parents=True)
    current = root / ".memory" / "MEMORY.md"
    current.parent.mkdir()
    current.write_bytes(b"original application memory\n")
    monkeypatch.setattr(gate, "ROOT", root)

    class Store:
        def __init__(self, workspace):
            self.workspace = workspace
            self.current_path = workspace / ".memory" / "MEMORY.md"
            self.current_path.parent.mkdir(parents=True, exist_ok=True)
            self.current_path.write_bytes(b"validation-owned memory\n")

    module = SimpleNamespace(__name__="gate_memory_fixture", _APP=SimpleNamespace(base=SimpleNamespace(MemoryStore=Store)))
    yield root, current, module
    gate._unload_variant(module)


def test_release_gate_redirects_only_application_root_memory(gate_runtime):
    root, current, module = gate_runtime
    before = current.read_bytes(), current.stat().st_mtime_ns
    gate._isolate_variant_memory(module)
    store = module._APP.base.MemoryStore(root)
    assert store.workspace.is_relative_to(root / ".cache")
    assert store.current_path.read_bytes() == b"validation-owned memory\n"
    assert (current.read_bytes(), current.stat().st_mtime_ns) == before


def test_release_gate_preserves_explicit_disposable_memory(gate_runtime):
    root, _current, module = gate_runtime
    gate._isolate_variant_memory(module)
    workspace = root / "disposable"
    store = module._APP.base.MemoryStore(workspace)
    assert store.workspace == workspace
    assert store.current_path == workspace / ".memory" / "MEMORY.md"


def test_release_gate_restores_initializer_after_failed_check(gate_runtime):
    _root, _current, module = gate_runtime
    store = module._APP.base.MemoryStore
    original = store.__init__
    gate._isolate_variant_memory(module)
    temporary = Path(module._gate_memory_scope[2].name)
    with pytest.raises(RuntimeError, match="simulated gate failure"):
        try:
            raise RuntimeError("simulated gate failure")
        finally:
            gate._unload_variant(module)
    assert store.__init__ is original
    assert not temporary.exists()
