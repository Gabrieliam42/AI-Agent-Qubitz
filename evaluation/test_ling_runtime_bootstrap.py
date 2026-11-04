"""CPU-only Ling bootstrap contracts; no model, GPU, or network calls."""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
from contextlib import suppress
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime import read_variant_source

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def base():
    path = variant_path("AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py", ROOT)
    tree = ast.parse(read_variant_source(path))
    namespace = {
        "Path": Path, "Any": Any, "LocalOnlyApp": SimpleNamespace,
        "re": re, "os": os, "json": json, "hashlib": hashlib,
        "shutil": shutil, "suppress": suppress,
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name)
            and target.id.startswith("LING3_FLASH_ISOLATED_LLAMA_")
            for target in node.targets
        ) or isinstance(node, ast.FunctionDef) and node.name == "_install_ling3_flash_llamacpp_runtime":
            selected.append(node)
    # Load only the installer, never the agent entrypoint or model runtime.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102
    instance = SimpleNamespace(
        in_wsl=lambda: True,
        parse_cuda_release_version=lambda text: tuple(map(int, text.split("."))),
    )
    namespace["_install_ling3_flash_llamacpp_runtime"](SimpleNamespace(base=instance))
    return instance


def test_windows_bundle_supplies_both_runtime_roles(base):
    asset = {"name": "llama-turboquant-windows-x64-cuda-12.4.zip"}
    main, cudart = base.select_latest_llamacpp_cuda_assets({"assets": [asset]})
    assert main is asset and cudart is asset
    assert "AtomicBot-ai/atomic-llama-cpp-turboquant" in base.DEFAULT_LLAMACPP_RELEASE_API_URL


@pytest.mark.parametrize("assets", [None, [], [{"name": "llama-turboquant-windows-x64-cpu.zip"}]])
def test_missing_supported_windows_bundle_is_explicit(base, assets):
    with pytest.raises(RuntimeError, match="pinned Atomic TurboQuant"):
        base.select_latest_llamacpp_cuda_assets({"assets": assets})


def test_missing_native_runtime_respects_offline_mode(base, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    with pytest.raises(RuntimeError, match="downloads are disabled offline"):
        base.ensure_project_local_native_llamacpp_runtime(tmp_path)


@pytest.mark.parametrize("cached_corrupt", [False, True])
def test_native_fallback_downloads_atomic_once_and_reuses_it(base, tmp_path, monkeypatch, cached_corrupt):
    monkeypatch.delenv("QUBITZ_OFFLINE", raising=False)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        payload = b"fixture server; not executed"
        entry = tarfile.TarInfo("bundle/llama-server")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    data = buffer.getvalue()
    name = "llama-turboquant-linux-x64-cuda-12.4.tar.gz"
    url = "https://github.com/AtomicBot-ai/atomic-llama-cpp-turboquant/releases/download/b10269-1.6.0/" + name
    asset = {"name": name, "browser_download_url": url, "size": len(data),
             "digest": "sha256:" + hashlib.sha256(data).hexdigest()}
    observed = []

    def fetch():
        observed.append("release")
        return {"assets": [asset]}

    def download(source, destination):
        assert source == url
        observed.append("download")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)

    base.fetch_latest_llamacpp_release = fetch
    base.download_url_to_path = download
    base.normalize_archive_root = lambda root: root / "bundle"
    if cached_corrupt:
        cached = tmp_path / ".cache/downloads/llama.cpp/b10269-1.6.0" / name
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b"incomplete")
    first = base.ensure_project_local_native_llamacpp_runtime(tmp_path)
    assert first["downloaded"]
    assert first["executable"].read_bytes() == payload
    assert "atomic-llama.cpp-turboquant-b10269-1.6.0" in str(first["executable"])
    assert "llama.cpp-linux" not in str(first["executable"])
    second = base.ensure_project_local_native_llamacpp_runtime(tmp_path)
    assert not second["downloaded"]
    assert observed == ["release", "download"]


def test_native_archive_integrity_failure_does_not_install(base, tmp_path, monkeypatch):
    monkeypatch.delenv("QUBITZ_OFFLINE", raising=False)
    name = "llama-turboquant-linux-x64-cuda-12.4.tar.gz"
    base.fetch_latest_llamacpp_release = lambda: {"assets": [{
        "name": name, "browser_download_url": "https://example.invalid/fixture",
        "size": 4, "digest": "sha256:" + "0" * 64,
    }]}

    def download(_url, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bad!")

    base.download_url_to_path = download
    with pytest.raises(RuntimeError, match="size/checksum"):
        base.ensure_project_local_native_llamacpp_runtime(tmp_path)
    assert not (base.llamacpp_runtime_dir(tmp_path) / "linux-cuda-12.4/qubitz-runtime.json").exists()
