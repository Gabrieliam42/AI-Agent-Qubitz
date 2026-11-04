"""CPU-only contracts for selecting an installable official llama.cpp release."""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from runtime import read_variant_source
from runtime.paths import variant_path
from runtime.source import embedded_source

ROOT = Path(__file__).resolve().parents[1]
LATEST = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
RELEASES = "https://api.github.com/repos/ggml-org/llama.cpp/releases"


def runtime_namespace(monkeypatch, responses):
    path = variant_path("AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py", ROOT)
    tree = ast.parse(embedded_source(read_variant_source(path))[0])
    selected = []
    names = {
        "DEFAULT_LLAMACPP_RELEASE_API_URL",
        "WINDOWS_CUDA_RUNTIME_PATTERN",
        "WINDOWS_CUDART_RUNTIME_PATTERN",
    }
    functions = {
        "fetch_latest_llamacpp_release",
        "select_latest_llamacpp_cuda_assets",
        "parse_cuda_release_version",
    }
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ) or isinstance(node, ast.FunctionDef) and node.name in functions:
            selected.append(node)
    namespace = {"os": os, "re": re}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response(responses[url])

    namespace["_httpx_client"] = Client
    monkeypatch.delenv("QUBITZ_OFFLINE", raising=False)
    return namespace, calls


def cuda_release(tag):
    return {
        "tag_name": tag,
        "assets": [
            {"name": f"llama-{tag}-bin-win-cuda-12.4-x64.zip"},
            {"name": "cudart-llama-bin-win-cuda-12.4-x64.zip"},
        ],
    }


def test_latest_source_only_release_falls_back_to_newest_cuda_pair(monkeypatch):
    namespace, calls = runtime_namespace(monkeypatch, {
        LATEST: {"tag_name": "v0.7.0", "assets": [{"name": "nightly-tag.txt"}]},
        RELEASES: [{"tag_name": "v0.7.0", "assets": []}, cuda_release("b11899")],
    })
    assert namespace["fetch_latest_llamacpp_release"]()["tag_name"] == "b11899"
    assert [url for url, _ in calls] == [LATEST, RELEASES]


def test_installable_latest_needs_no_fallback(monkeypatch):
    namespace, calls = runtime_namespace(monkeypatch, {LATEST: cuda_release("b11899")})
    assert namespace["fetch_latest_llamacpp_release"]()["tag_name"] == "b11899"
    assert [url for url, _ in calls] == [LATEST]


def test_pinned_variant_channel_never_substitutes_another_release(monkeypatch):
    pinned = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b10809"
    namespace, calls = runtime_namespace(monkeypatch, {pinned: {"tag_name": "b10809", "assets": []}})
    namespace["DEFAULT_LLAMACPP_RELEASE_API_URL"] = pinned
    assert namespace["fetch_latest_llamacpp_release"]()["tag_name"] == "b10809"
    assert [url for url, _ in calls] == [pinned]


def test_no_compatible_release_fails_without_installing(monkeypatch):
    namespace, _ = runtime_namespace(monkeypatch, {
        LATEST: {"tag_name": "v0.7.0", "assets": []},
        RELEASES: [{"tag_name": "v0.7.0", "assets": []}],
    })
    with pytest.raises(RuntimeError, match="matching Windows CUDA 12"):
        namespace["fetch_latest_llamacpp_release"]()


def test_offline_mode_skips_release_metadata_requests(monkeypatch):
    namespace, calls = runtime_namespace(monkeypatch, {})
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="disabled"):
        namespace["fetch_latest_llamacpp_release"]()
    assert calls == []


def test_missing_local_embedder_never_triggers_a_network_retry(monkeypatch, tmp_path):
    path = variant_path("AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py", ROOT)
    tree = ast.parse(embedded_source(read_variant_source(path))[0])
    embedder = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BGECodeEmbedder")
    load = next(node for node in embedder.body if isinstance(node, ast.FunctionDef) and node.name == "load")
    selected = ast.ClassDef(name="FixtureEmbedder", bases=[], keywords=[], body=[load], decorator_list=[])
    namespace = {"configure_project_environment": lambda _root: None}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[selected], type_ignores=[])), str(path), "exec"), namespace)  # noqa: S102
    torch = ModuleType("torch")
    torch.nn = ModuleType("torch.nn")
    torch.nn.functional = ModuleType("torch.nn.functional")
    transformers = ModuleType("transformers")
    calls = []

    def missing_tokenizer(*_args, **kwargs):
        calls.append(kwargs)
        raise FileNotFoundError("fixture tokenizer absent")

    transformers.AutoTokenizer = SimpleNamespace(from_pretrained=missing_tokenizer)
    transformers.AutoModel = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torch.nn", torch.nn)
    monkeypatch.setitem(sys.modules, "torch.nn.functional", torch.nn.functional)
    monkeypatch.setitem(sys.modules, "transformers", transformers)
    instance = SimpleNamespace(
        _loaded=False, _released_from_gpu=False, local_files_only=True,
        workspace=tmp_path, hf_cache_dir=tmp_path, model_name="fixture/embedder",
        _download_missing_embedder_files=lambda: pytest.fail("Local-only embedder attempted a download"),
    )
    with pytest.raises(RuntimeError, match="downloads are disabled in local-only mode"):
        namespace["FixtureEmbedder"].load(instance)
    assert len(calls) == 1 and calls[0]["local_files_only"] is True
