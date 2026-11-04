"""Probe-gated launch policy, without launching or acquiring model servers."""
from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant


@pytest.mark.parametrize("encoded", [False, True])
@pytest.mark.parametrize("supported", [False, True])
@pytest.mark.parametrize("explicit", [False, True])
def test_cors_options_are_pinned_help_gated_and_preserve_explicit_policy(variant, monkeypatch, encoded, supported, explicit):
    args = ["/fixture/llama-server.exe", "--host", "127.0.0.1", "--model", "/fixture/model.gguf"]
    if explicit:
        args += ["--cors-origins", "http://localhost:3000", "--cors-credentials"]
    command = ["powershell.exe", "-EncodedCommand", base64.b64encode(
        ("& " + " ".join("'" + arg + "'" for arg in args)).encode("utf-16-le")).decode()] if encoded else args

    class Server:
        def __init__(self, config):
            self.config, self.process, self.model_path = config, None, None

        def _launch_command(self):
            return command[:]

        def ensure_started(self):
            pytest.fail("Model server must not launch in a CPU test")

        def shutdown(self):
            pytest.fail("No live server exists")

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: encoded)
    variant._patch_llamacpp_launch_fit(base)
    Server._qubitz_server_help_cache = {args[0]: "--cors-origins --no-cors-credentials" if supported else "--help"}
    monkeypatch.setattr(variant.subprocess, "run", lambda *_a, **_k: pytest.fail("Uncached probe or server launch"))
    result = Server(SimpleNamespace())._launch_command()
    text = base64.b64decode(result[-1]).decode("utf-16-le") if encoded else " ".join(result)
    assert "127.0.0.1" in text
    if explicit:
        assert "http://localhost:3000" in text and "--no-cors-credentials" not in text
    elif supported:
        assert "--cors-origins" in text and "localhost" in text and "--no-cors-credentials" in text
    else:
        assert "--cors-origins" not in text and "--no-cors-credentials" not in text
    assert "--api-key" not in text  # Authentication is not claimed or silently configured.
