"""Actual embedder state reporting with fake CUDA and no model/GPU loading."""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import read_variant_source
from runtime.symbols import symbol_sources

variant = receipts.variant
GIB = 1024**3


@pytest.fixture
def embedder(variant, tmp_path):
    messages = []
    instance = variant._APP.base.BGECodeEmbedder(
        tmp_path, "unused-test-model", progress_callback=messages.append,
    )
    return instance, messages


def fake_torch(*, available=True, free=8 * GIB, total=24 * GIB):
    def memory():
        if free is None:
            raise RuntimeError("memory snapshot unavailable")
        return free, total

    cuda = SimpleNamespace(
        is_available=lambda: available, mem_get_info=memory,
        device_count=lambda: 1, set_device=lambda _index: None,
        empty_cache=lambda: None, ipc_collect=lambda: None,
    )
    return SimpleNamespace(cuda=cuda, backends=SimpleNamespace(
        cuda=SimpleNamespace(matmul=SimpleNamespace(allow_tf32=False)),
        cudnn=SimpleNamespace(allow_tf32=False),
    ), float16="fake-float16")


class FakeModel:
    def __init__(self, *, fail_cuda=False):
        self.fail_cuda = fail_cuda
        self.moves = []

    def to(self, device):
        if device == "cuda" and self.fail_cuda:
            raise RuntimeError("allocation failed")
        self.moves.append(device)
        return self

    def eval(self):
        return self


def install_fake_load(variant, embedder, monkeypatch, *, available=True, free=8 * GIB, fail_load=False, fail_cuda=False):
    instance, _messages = embedder
    torch = ModuleType("torch")
    torch.__dict__.update(vars(fake_torch(available=available, free=free)))
    nn = ModuleType("torch.nn")
    functional = ModuleType("torch.nn.functional")
    nn.functional = functional
    torch.nn = nn
    transformers = ModuleType("transformers")
    transformers.__path__ = []
    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=lambda *_args, **_kwargs: object())
    transformers.AutoModel = object()
    modeling = ModuleType("transformers.modeling_utils")
    pytorch_utils = ModuleType("transformers.pytorch_utils")
    pytorch_utils.Conv1D = object()
    transformers.modeling_utils = modeling
    transformers.pytorch_utils = pytorch_utils
    for name, module in (("torch", torch), ("torch.nn", nn), ("torch.nn.functional", functional),
                         ("transformers", transformers), ("transformers.modeling_utils", modeling),
                         ("transformers.pytorch_utils", pytorch_utils)):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(variant._APP.base, "configure_project_environment", lambda _workspace: None)
    monkeypatch.setattr(instance, "_has_flash_attention", lambda: False)
    monkeypatch.setattr(instance, "_has_xformers", lambda: False)
    install_fake_inventory(variant, monkeypatch, free)
    model = FakeModel(fail_cuda=fail_cuda)
    loads = []

    def load(_model_class, kwargs):
        loads.append(dict(kwargs))
        if fail_load and "dtype" in kwargs:
            raise RuntimeError("CUDA load failed")
        return model

    monkeypatch.setattr(instance, "_load_model", load)
    return model, loads


def install_fake_inventory(variant, monkeypatch, free):
    def probe(command, **_kwargs):
        assert command[0] == "nvidia-smi", command
        text = "" if free is None else f"0, fake CUDA device, {free // 1024**2}, 24576\n"
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(variant.subprocess, "run", probe)


def test_unused_retrieval_has_no_device_policy_message(embedder):
    instance, messages = embedder
    assert not instance._loaded and messages == []
    assert not instance.release_gpu() and messages == []


@pytest.mark.parametrize("available,free,requested,device,reason", [
    (False, 8 * GIB, "auto", "cpu", "CUDA unavailable"),
    (True, 8 * GIB, "cpu", "cpu", "CPU explicitly configured"),
    (True, 2 * GIB, "auto", "cpu", "insufficient free CUDA memory"),
    (True, 8 * GIB, "auto", "cuda", ""),
    (True, None, "auto", "cuda", ""),
])
def test_report_follows_real_selection_without_claiming_reserved_memory(
    variant, embedder, monkeypatch, available, free, requested, device, reason,
):
    instance, messages = embedder
    instance.requested_device = requested
    install_fake_load(variant, embedder, monkeypatch, available=available, free=free)
    instance.load()
    assert instance.device == device and instance._loaded
    status = [message for message in messages if message.startswith("Retrieval device:")]
    assert len(status) == 1
    assert device.upper() in status[0] and reason in status[0]
    assert "Generation headroom target: 1.0 GiB" in status[0]
    assert "preserv" not in status[0] and "snapshot" not in status[0]
    count = len(messages)
    instance.load()
    assert len(messages) == count


def test_cpu_selection_does_not_probe_cuda(embedder):
    instance, messages = embedder
    instance.requested_device = "cpu"

    def forbidden():
        pytest.fail("Explicit CPU selection probed CUDA")

    assert instance._resolve_device(SimpleNamespace(cuda=SimpleNamespace(is_available=forbidden))) == "cpu"
    assert messages == []


def test_cuda_load_failure_reports_actual_cpu_fallback(variant, embedder, monkeypatch):
    instance, messages = embedder
    _model, loads = install_fake_load(variant, embedder, monkeypatch, fail_load=True)
    instance.load()
    assert len(loads) == 2 and "dtype" in loads[0] and "dtype" not in loads[1]
    assert instance.device == "cpu" and instance._loaded
    assert any("Retrieval device: CPU (CUDA load failed)" in message for message in messages)
    assert not any("Retrieval device: CUDA" in message for message in messages)


def test_initial_cuda_allocation_failure_is_not_misreported_as_success(variant, embedder, monkeypatch):
    instance, messages = embedder
    install_fake_load(variant, embedder, monkeypatch, fail_cuda=True)
    # The reporting-only change preserves the existing propagated allocation failure.
    with pytest.raises(RuntimeError, match="allocation failed"):
        instance.load()
    assert not instance._loaded
    assert not any(message.startswith("Retrieval device:") for message in messages)


def test_restore_allocation_failure_reports_actual_cpu_copy(variant, embedder, monkeypatch):
    instance, messages = embedder
    instance._torch = fake_torch()
    instance._model = FakeModel(fail_cuda=True)
    instance._loaded = True
    instance._released_from_gpu = True
    install_fake_inventory(variant, monkeypatch, 8 * GIB)
    instance.load()
    assert instance.device == "cpu"
    assert any("Retrieval device: CPU (CUDA restore failed)" in message for message in messages)
    assert not any("Retrieval device: CUDA" in message for message in messages)


@pytest.mark.parametrize("free,state", [(2 * GIB, "met"), (GIB // 2, "not met"), (None, "not verified")])
def test_release_reports_only_fresh_snapshot_headroom(embedder, free, state):
    instance, messages = embedder
    instance._torch = fake_torch(free=free)
    instance._model = FakeModel()
    instance._loaded = True
    instance.device = "cuda"
    assert instance.release_gpu()
    assert instance.device == "cpu" and instance._model.moves == ["cpu"]
    assert "headroom target: 1.0 GiB" in messages[0]
    assert "headroom target " + state in messages[-1]
    assert "in this snapshot" in messages[-1] if free is not None else "memory unavailable" in messages[-1]
    assert not any("preserv" in message for message in messages)
    count = len(messages)
    assert not instance.release_gpu() and len(messages) == count


def test_repeated_cpu_fallback_reports_once_until_actual_state_changes(embedder):
    instance, messages = embedder
    instance._loaded = True
    instance._released_from_gpu = True
    instance._torch = fake_torch(available=False)
    instance._model = FakeModel()
    instance.load()
    instance.load()
    assert len(messages) == 1 and "CUDA unavailable" in messages[0]


def test_generic_policy_paragraph_is_absent_from_effective_sources(variant):
    assert "Retrieval GPU policy: prefer CUDA whenever" not in read_variant_source(Path(variant.__file__))
    assert "Retrieval GPU policy: prefer CUDA whenever" not in variant._EMBEDDED_BASE_SOURCE


def test_shared_embedder_registry_preserves_reporting_methods(variant):
    root = Path(__file__).resolve().parents[1]
    sources = symbol_sources("base", root / "runtime")
    block = next(value for value in sources.values() if value.startswith("class BGECodeEmbedder:"))
    compatibility = ast.parse(block)
    current = ast.parse(variant._EMBEDDED_BASE_SOURCE)
    old_class = next(node for node in compatibility.body if isinstance(node, ast.ClassDef) and node.name == "BGECodeEmbedder")
    new_class = next(node for node in current.body if isinstance(node, ast.ClassDef) and node.name == "BGECodeEmbedder")
    for name in ("__init__", "_report_device_status", "_resolve_device", "_report_cuda_device", "release_gpu"):
        old_method = next(node for node in old_class.body if isinstance(node, ast.FunctionDef) and node.name == name)
        new_method = next(node for node in new_class.body if isinstance(node, ast.FunctionDef) and node.name == name)
        assert ast.dump(old_method) == ast.dump(new_method), name


def test_existing_transformers_compatibility_and_flash_retry_remain_present(variant):
    constants = variant._APP.base.BGECodeEmbedder.load.__code__.co_consts
    assert any(isinstance(value, str) and "retrying CUDA without FlashAttention2" in value for value in constants)
    assert "Conv1D" in variant._APP.base.BGECodeEmbedder.load.__code__.co_names


def test_flash_failure_retries_cuda_before_cpu_as_before(variant, embedder, monkeypatch):
    instance, messages = embedder
    model, _loads = install_fake_load(variant, embedder, monkeypatch)
    monkeypatch.setattr(instance, "_has_flash_attention", lambda: True)
    loads = []

    def load(_model_class, kwargs):
        loads.append(dict(kwargs))
        if kwargs.get("attn_implementation") == "flash_attention_2":
            raise RuntimeError("fake FlashAttention failure")
        return model

    monkeypatch.setattr(instance, "_load_model", load)
    instance.load()
    assert len(loads) == 2 and all("dtype" in kwargs for kwargs in loads)
    assert "attn_implementation" in loads[0] and "attn_implementation" not in loads[1]
    assert instance.device == "cuda" and instance._loaded
    assert any("Retrieval device: CUDA" in message for message in messages)
    assert not any("Retrieval device: CPU" in message for message in messages)
