"""Installed public providers acquire exactly their files using a fake Hub only."""
from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

import huggingface_hub

from evaluation import test_followup_state_receipts as receipts
from runtime.catalog import model_files

variant = receipts.variant


def test_installed_provider_and_disk_preflight_acquire_exact_public_files(variant, tmp_path, monkeypatch):
    metadata = model_files(Path(variant.__file__))
    assert metadata is not None
    names = metadata.parts or metadata.shards
    prefix = metadata.subdirectories[0] + "/" if metadata.subdirectories else ""
    names = tuple(prefix + name for name in names)
    files = [SimpleNamespace(rfilename=name, size=32, blob_id="fixture-etag", lfs=None) for name in names]
    base = variant._APP.base
    calls = []

    def download(*, repo_id, filename, local_dir):
        assert repo_id == metadata.repo and filename in names
        target = local_dir / filename
        assert target.resolve().is_relative_to(tmp_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"GGUF" + b"\0" * 28)
        calls.append(filename)
        return str(target)

    def no_network(*_args, **_kwargs):
        raise AssertionError("Public provider regression attempted a real network connection")

    for name in ("QUBITZ_OFFLINE", "HF_HUB_OFFLINE", "QUBITZ_NETWORK_MODE", base.GGUF_MODEL_PATH_ENV_NAME):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=files)))
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    filename = base.DEFAULT_GGUF_MODEL_FILENAME
    assert base.resolve_gguf_model_path(tmp_path, None, filename) is None
    resolved = base.download_default_gguf_model(tmp_path, filename)
    assert len(calls) == len(names) and set(calls) == set(names)
    assert base.resolve_gguf_model_path(tmp_path, None, filename) == resolved
    assert base.download_default_gguf_model(tmp_path, filename) == resolved
    assert len(calls) == len(names)
