"""Ornith 122B identity, shard acquisition and launch contracts without weights."""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.catalog import model_files
from runtime.paths import PROJECT_ROOT, variant_path
from runtime.source import launcher_spec

NAME = "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py"
PARENT = "AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py"
REPO = "unsloth/Ornith-1.5-122B-GGUF"
SHARDS = tuple(f"Ornith-1.5-122B-UD-Q5_K_XL-{i:05}-of-00003.gguf" for i in range(1, 4))


@pytest.fixture
def hooks(monkeypatch):
    import huggingface_hub

    def unexpected_download(*_args, **_kwargs):
        pytest.fail("Unmocked model download attempted")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", unexpected_download)
    spec = launcher_spec(variant_path(NAME).read_text(encoding="utf-8"))
    tree = ast.parse(spec["wrapper"]["253"])
    nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef))]
    namespace = {"Any": Any, "Sequence": Sequence, "Path": Path, "os": os, "re": re,
                 "shutil": shutil, "subprocess": subprocess, "LocalOnlyApp": SimpleNamespace}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), NAME, "exec"), namespace)  # noqa: S102
    return SimpleNamespace(namespace=namespace,
                           install=namespace["_install_ornith15_122b_sharded_model"])


def test_same_architecture_clone_changes_only_identity_and_large_model_slots():
    original = launcher_spec(variant_path(PARENT).read_text(encoding="utf-8"))
    added = launcher_spec(variant_path(NAME).read_text(encoding="utf-8"))
    differences = {layer: {key for key in original[layer] if original[layer][key] != added[layer][key]}
                   for layer in ("base", "wrapper")}
    assert differences == {"base": {"001"}, "wrapper": {"068", "076", "253"}}
    specific = "\n".join(value for layer in ("base", "wrapper") for value in added[layer].values())
    assert not any(stale in specific for stale in ("Ornith-1.5-35B", "Qwen3.8", "QWEN38", "qwen38", "UD-IQ4_XS"))
    assert "Q5_GGUF_MODEL_FILENAME" in added["base"]["001"]
    assert "Q4_GGUF_MODEL_FILENAME" not in specific


def test_profile_native_context_shards_and_runtime_match_the_new_model():
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text(encoding="utf-8"))
    profile = next(row for row in profiles["variants"] if row["filename"] == NAME)
    assert profile["model_alias"] == profile["hf_gguf_repo_id"] == REPO
    assert profile["gguf_filename"] == SHARDS[0]
    assert profile["gguf_label"] == "Ornith-1.5-122B-UD-Q5_K_XL"
    assert profile["num_ctx"] == 262144 and profile["num_predict"] == 16384
    contract = profile["runtime_contract"]
    assert contract["dual_gpu"] and contract["sharded_model"] and contract["isolated_runtime"]
    assert not contract["mtp_draft"] and not contract["background_prewarm"]
    constants = contract["constants"]
    assert constants["ORNITH15_122B_GPU_INDICES"] == [0, 1]
    assert constants["ORNITH15_122B_MIN_FIT_CONTEXT"] == 131072
    assert constants["ORNITH15_122B_GGUF_SHARDS"] == list(SHARDS)
    metadata = model_files(variant_path(NAME))
    assert metadata.repo == REPO and metadata.shards == SHARDS
    assert metadata.subdirectories == ("UD-Q5_K_XL",)


def test_canonical_resolution_requires_all_three_siblings(hooks, tmp_path):
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: None)
    hooks.install(SimpleNamespace(base=base))
    directory = tmp_path / ".cache/models" / REPO / "UD-Q5_K_XL"
    directory.mkdir(parents=True)
    for name in SHARDS[:2]:
        (directory / name).write_bytes(b"GGUF" + b"\0" * 28)
    assert base.resolve_gguf_model_path(tmp_path) is None
    (directory / SHARDS[2]).write_bytes(b"GGUF" + b"\0" * 28)
    assert base.resolve_gguf_model_path(tmp_path) == (directory / SHARDS[0]).resolve()
    assert base.Q5_GGUF_MODEL_FILENAME == SHARDS[0]


def test_explicit_incomplete_set_is_an_error_not_a_single_file_fallback(hooks, tmp_path):
    first = tmp_path / SHARDS[0]
    first.write_bytes(b"GGUF" + b"\0" * 28)
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: first)
    hooks.install(SimpleNamespace(base=base))
    with pytest.raises(FileNotFoundError, match="incomplete"):
        base.resolve_gguf_model_path(tmp_path, str(first))


def test_download_uses_only_the_requested_repo_and_quantization(hooks, tmp_path, monkeypatch):
    import huggingface_hub

    calls = []

    def download(*, repo_id, filename, local_dir):
        calls.append((repo_id, filename))
        destination = Path(local_dir) / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"GGUF" + b"\0" * 28)
        return str(destination)

    monkeypatch.delenv("QUBITZ_OFFLINE", raising=False)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: None)
    hooks.install(SimpleNamespace(base=base))
    result = base.download_default_gguf_model(tmp_path)
    assert result.name == SHARDS[0]
    assert calls == [(REPO, f"UD-Q5_K_XL/{name}") for name in SHARDS]
    assert result.parent == (tmp_path / ".cache/models" / REPO / "UD-Q5_K_XL").resolve()
    with pytest.raises(ValueError, match="first GGUF shard"):
        base.download_default_gguf_model(tmp_path, SHARDS[1])


def test_offline_download_stops_before_network_access(hooks, tmp_path, monkeypatch):
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: None)
    hooks.install(SimpleNamespace(base=base))
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="disabled"):
        base.download_default_gguf_model(tmp_path)


def test_runtime_identity_does_not_inherit_qwen_model_identity(hooks, tmp_path):
    base = SimpleNamespace()
    hooks.namespace["_install_ornith15_122b_llamacpp_runtime"](SimpleNamespace(base=base))
    assert base.QUBITZ_LLAMACPP_RUNTIME_CHANNEL == "ornith1.5-122b-isolated"
    assert base.QUBITZ_LLAMACPP_RUNTIME_TAG == "b10809"
    assert base.llamacpp_runtime_dir(tmp_path) == tmp_path / ".cache/llama.cpp-b10809"
    assert base.DEFAULT_LLAMACPP_RELEASE_API_URL.endswith("/tags/b10809")


def test_shared_ornith_route_sampling_is_retained(monkeypatch):
    import qubitz_release_gate as gate

    assert gate.VARIANT_SAMPLING_FAMILIES[NAME] == "ornith"
    monkeypatch.setattr(gate, "VARIANT_SAMPLING_FAMILIES", {NAME: "ornith"})
    gate.check_sampling_profiles([variant_path(NAME)])
