"""Nemotron Super VL identity and launch contracts without weights or inference."""
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

NAME = "AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py"
PARENT = "AI_Agent_Qubitz_Nemotron-3.5-Lightning-30B-A3B-IQ4.py"
REPO = "unsloth/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-GGUF"
LABEL = "NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL"
SHARDS = tuple(f"{LABEL}-{i:05}-of-00004.gguf" for i in range(1, 5))
PREFIX = "NEMOTRON35_SUPER_VL"


@pytest.fixture
def hooks(monkeypatch):
    import huggingface_hub

    def unexpected_download(*_args, **_kwargs):
        pytest.fail("Unmocked model download attempted")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", unexpected_download)
    for key in os.environ:
        if key.startswith(f"QUBITZ_{PREFIX}_"):
            monkeypatch.delenv(key)
    spec = launcher_spec(variant_path(NAME).read_text(encoding="utf-8"))
    tree = ast.parse(spec["wrapper"]["253"])
    nodes = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef))]
    namespace = {"Any": Any, "Sequence": Sequence, "Path": Path, "os": os, "re": re,
                 "shutil": shutil, "subprocess": subprocess, "LocalOnlyApp": SimpleNamespace}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), NAME, "exec"), namespace)  # noqa: S102
    return SimpleNamespace(namespace=namespace,
                           install=namespace["_install_nemotron35_super_vl_sharded_model"])


def test_clone_keeps_same_architecture_behavior_without_stale_model_references():
    original = launcher_spec(variant_path(PARENT).read_text(encoding="utf-8"))
    added = launcher_spec(variant_path(NAME).read_text(encoding="utf-8"))
    differences = {layer: {key for key in original[layer] if original[layer][key] != added[layer][key]}
                   for layer in ("base", "wrapper")}
    assert differences == {"base": {"001", "004", "005", "006"}, "wrapper": {"003", "253"}}
    specific = "\n".join(value for layer in ("base", "wrapper") for value in added[layer].values())
    assert not any(stale in specific for stale in (
        "Lightning", "LIGHTNING", "Ornith", "ORNITH", "Qwen", "QWEN", "IQ4", "Q5_K_XL", "24g_retrieval",
    ))
    assert "Q6_GGUF_MODEL_FILENAME" in added["base"]["001"]
    assert 'ROUTE_PROFILE_NEMOTRON35_SUPER_VL_48G = "48g_retrieval"' in specific


def test_profile_shards_and_dual_gpu_runtime_match_the_requested_model():
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text(encoding="utf-8"))
    profile = next(row for row in profiles["variants"] if row["filename"] == NAME)
    assert profile["model_alias"] == profile["hf_gguf_repo_id"] == REPO
    assert profile["gguf_filename"] == SHARDS[0] and profile["gguf_label"] == LABEL
    assert profile["num_ctx"] == 262144 and profile["num_predict"] == 16384
    contract = profile["runtime_contract"]
    assert contract["dual_gpu"] and contract["sharded_model"] and contract["isolated_runtime"]
    assert not contract["mtp_draft"] and not contract["background_prewarm"]
    constants = contract["constants"]
    assert constants[f"{PREFIX}_GPU_INDICES"] == [0, 1]
    assert constants[f"{PREFIX}_MIN_FIT_CONTEXT"] == 131072
    assert constants[f"{PREFIX}_GGUF_SHARDS"] == list(SHARDS)
    assert constants[f"{PREFIX}_DEFAULT_KV_CACHE_TYPE"] == "q8_0"
    metadata = model_files(variant_path(NAME))
    assert metadata.repo == REPO and metadata.shards == SHARDS
    assert metadata.subdirectories == ("UD-Q6_K_XL",)


def test_canonical_resolution_requires_all_four_siblings(hooks, tmp_path):
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: None)
    hooks.install(SimpleNamespace(base=base))
    directory = tmp_path / ".cache/models" / REPO / "UD-Q6_K_XL"
    directory.mkdir(parents=True)
    for name in SHARDS[:3]:
        (directory / name).write_bytes(b"GGUF" + b"\0" * 28)
    assert base.resolve_gguf_model_path(tmp_path) is None
    (directory / SHARDS[3]).write_bytes(b"GGUF" + b"\0" * 28)
    assert base.resolve_gguf_model_path(tmp_path) == (directory / SHARDS[0]).resolve()
    assert base.Q6_GGUF_MODEL_FILENAME == SHARDS[0]


def test_explicit_incomplete_set_is_an_error_not_a_single_file_fallback(hooks, tmp_path):
    first = tmp_path / SHARDS[0]
    first.write_bytes(b"GGUF" + b"\0" * 28)
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: first)
    hooks.install(SimpleNamespace(base=base))
    with pytest.raises(FileNotFoundError, match="incomplete"):
        base.resolve_gguf_model_path(tmp_path, str(first))


def test_mocked_download_uses_only_the_requested_repo_and_quantization(hooks, tmp_path, monkeypatch):
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
    assert calls == [(REPO, f"UD-Q6_K_XL/{name}") for name in SHARDS]
    assert result.parent == (tmp_path / ".cache/models" / REPO / "UD-Q6_K_XL").resolve()
    with pytest.raises(ValueError, match="first GGUF shard"):
        base.download_default_gguf_model(tmp_path, SHARDS[1])


def test_offline_download_stops_before_network_access(hooks, tmp_path, monkeypatch):
    base = SimpleNamespace(resolve_gguf_model_path=lambda *_args: None)
    hooks.install(SimpleNamespace(base=base))
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="disabled"):
        base.download_default_gguf_model(tmp_path)


def test_both_runtime_transports_are_isolated_at_the_supported_tag(hooks, tmp_path):
    base = SimpleNamespace()
    hooks.namespace["_install_nemotron35_super_vl_llamacpp_runtime"](SimpleNamespace(base=base))
    assert base.QUBITZ_LLAMACPP_RUNTIME_CHANNEL == "nemotron3.5-super-vl-isolated"
    assert base.QUBITZ_LLAMACPP_RUNTIME_TAG == "b11788"
    assert base.QUBITZ_LLAMACPP_RUNTIME_RELEASE == "v0.7.0"
    assert base.llamacpp_runtime_dir(tmp_path) == tmp_path / ".cache/llama.cpp-b11788"
    assert base.llamacpp_native_runtime_dir(tmp_path) == tmp_path / ".cache/llama.cpp-b11788-linux"
    assert base.DEFAULT_LLAMACPP_RELEASE_API_URL.endswith("/tags/b11788")


def test_native_context_and_layer_count_are_not_inherited_from_the_donor(hooks):
    assert hooks.namespace[f"{PREFIX}_NATIVE_CONTEXT"] == 1048576
    assert hooks.namespace[f"{PREFIX}_LAYER_COUNT"] == 88
    assert hooks.namespace["_nemotron35_super_vl_tuning"]()["kv_cache_type"] == "q8_0"


def test_tuning_bounds_follow_the_new_model(hooks, monkeypatch):
    monkeypatch.setenv(f"QUBITZ_{PREFIX}_CACHE_REUSE", "1048576")
    monkeypatch.setenv(f"QUBITZ_{PREFIX}_N_CPU_MOE", "88")
    tuning = hooks.namespace["_nemotron35_super_vl_tuning"]()
    assert tuning["cache_reuse"] == 1048576 and tuning["n_cpu_moe"] == 88
    monkeypatch.setenv(f"QUBITZ_{PREFIX}_N_CPU_MOE", "89")
    with pytest.raises(RuntimeError, match="between 0 and 88"):
        hooks.namespace["_nemotron35_super_vl_tuning"]()


def test_new_text_sampling_family_matches_official_recommendation(monkeypatch):
    import qubitz_release_gate as gate

    assert gate.VARIANT_SAMPLING_FAMILIES[NAME] == "nemotron_3_5_super_vl"
    monkeypatch.setattr(gate, "VARIANT_SAMPLING_FAMILIES", {NAME: "nemotron_3_5_super_vl"})
    gate.check_sampling_profiles([variant_path(NAME)])
