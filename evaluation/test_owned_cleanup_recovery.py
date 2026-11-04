"""Investigate the existing bounded native/proxy cleanup without launching a model."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant


def owned_server(variant, monkeypatch, results):
    closed = []
    scripts = []

    class Server:
        def __init__(self, config):
            self.config = config
            self.process = SimpleNamespace(poll=lambda: None)
            self.model_path = Path("/mnt/d/model.gguf")

        def resolve_executable(self):
            return "/mnt/d/llama-server.exe"

        def _launch_command(self):
            pytest.fail("Cleanup investigation attempted a launch")

        def ensure_started(self):
            pytest.fail("An unresolved owned server allowed another launch")

        def shutdown(self):
            closed.append(True)
            self.process = None

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: True,
                           normalize_base_url=lambda value: value, DEFAULT_LLAMACPP_PORT=8001,
                           wsl_path_to_windows_path=str)
    variant._patch_llamacpp_launch_fit(base)
    server = Server(SimpleNamespace(model_name="owned"))
    server.base_url = "http://127.0.0.1:8001"
    server._qubitz_owner_token = "a" * 32
    server._qubitz_owned_launch_epoch = time.time()
    monkeypatch.setattr(variant, "_build_powershell_management_command", lambda *_: ["powershell", "-Command", ""])
    iterator = iter(results)

    def run(command, **kwargs):
        scripts.append(base64.b64decode(command[-1]).decode("utf-16-le"))
        assert kwargs["timeout"] <= 30
        return next(iterator)

    monkeypatch.setattr(variant.subprocess, "run", run)
    return server, closed, scripts


def result(code, receipt=False, detail=""):
    return SimpleNamespace(returncode=code,
                           stdout='[{"pid":123,"created":"exact-created"}]' if receipt else "[]",
                           stderr=detail)


def test_slow_native_stop_recovers_after_proxy_close_and_verifies_again(variant, monkeypatch):
    server, closed, scripts = owned_server(variant, monkeypatch, [
        result(1, True, "Owned llama-server is still running after bounded shutdown wait"),
        result(0),
    ])
    server.shutdown()
    outcome = server._qubitz_cleanup_outcome
    assert closed == [True] and len(scripts) == 2
    assert outcome["status"] == "recovered_after_proxy_close"
    assert "bounded shutdown wait" in outcome["initial_detail"]
    assert outcome["final_detail"] == ""
    assert outcome["receipt"] == {"pid": 123, "created": "exact-created"}
    assert server._qubitz_owned_launch_epoch is None
    assert server._qubitz_owned_windows_receipt == {}


def test_unresolved_owner_retains_receipt_and_blocks_next_launch(variant, monkeypatch):
    server, closed, scripts = owned_server(variant, monkeypatch, [
        result(1, True, "A matching server has no verifiable ownership"),
        result(1, False, "A matching server has no verifiable ownership"),
        result(1, False, "A matching server has no verifiable ownership"),
    ])
    server.shutdown()
    assert closed == [True] and server._qubitz_cleanup_outcome["status"] == "failed"
    assert server._qubitz_owned_launch_epoch is not None
    assert server._qubitz_owned_windows_receipt["pid"] == 123
    with pytest.raises(RuntimeError, match="could not be confirmed stopped"):
        server.ensure_started()
    assert len(scripts) == 3


def test_cleanup_management_keeps_exact_identity_checks(variant, monkeypatch):
    server, _closed, scripts = owned_server(variant, monkeypatch, [result(0, True), result(0)])
    server._qubitz_owned_windows_receipt = {"pid": 123, "created": "exact-created"}
    server.shutdown()
    script = scripts[0]
    assert "$receiptPid = 123" in script and "$receiptCreated = 'exact-created'" in script
    assert "$createdText -eq $receiptCreated" in script
    assert "$remaining.CreationDate.ToUniversalTime().ToString('o') -eq $receipt.created" in script
    assert "ExecutablePath, $expectedExecutable" in script
    assert "# QUBITZ_OWNER=" in script
    assert "if (-not $owned) { $unverified = $true; continue }" in script


def test_no_owned_native_receipt_does_not_signal_anything(variant, monkeypatch):
    server, closed, scripts = owned_server(variant, monkeypatch, [])
    server._qubitz_owned_launch_epoch = None
    server.shutdown()
    assert closed == [True] and scripts == []
    assert server._qubitz_cleanup_outcome["status"] == "stopped"


def test_missing_receipt_cannot_report_successful_shutdown(variant, monkeypatch):
    server, closed, scripts = owned_server(variant, monkeypatch, [
        result(1, detail="Owned llama-server identity was not captured"),
        result(1, detail="Owned llama-server identity was not captured"),
    ])
    server.shutdown()
    assert closed == [True] and len(scripts) == 2
    assert server._qubitz_cleanup_outcome["status"] == "failed"
    assert server._qubitz_owned_launch_epoch is not None
    assert server._qubitz_owner_token == "a" * 32
    assert server._qubitz_owned_windows_receipt == {}
    assert "FromUnixTimeMilliseconds" not in scripts[0]
    assert "Owned llama-server identity was not captured" in scripts[0]


def capture_server(variant, monkeypatch, outcome):
    scripts = []

    class Server:
        def __init__(self, config):
            self.config = config
            self.process = None
            self.model_path = Path("/mnt/d/model.gguf")
            self.base_url = "http://127.0.0.1:8001"

        def resolve_executable(self):
            return "/mnt/d/llama-server.exe"

        def _launch_command(self):
            pytest.fail("CPU ownership test attempted a real server launch")

        def ensure_started(self):
            self.process = SimpleNamespace(poll=lambda: None)

        def shutdown(self):
            self.process = None

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: True,
                           normalize_base_url=lambda value: value, DEFAULT_LLAMACPP_PORT=8001,
                           wsl_path_to_windows_path=str)
    variant._patch_llamacpp_launch_fit(base)
    server = Server(SimpleNamespace(model_name="owned"))
    monkeypatch.setattr(variant, "_build_powershell_management_command", lambda *_: ["powershell", "-Command", ""])

    def run(command, **_kwargs):
        scripts.append(base64.b64decode(command[-1]).decode("utf-16-le"))
        return outcome

    monkeypatch.setattr(variant.subprocess, "run", run)
    return server, scripts


def test_startup_requires_owned_windows_identity(variant, monkeypatch):
    server, scripts = capture_server(
        variant, monkeypatch, result(1, detail="Owned llama-server identity was not captured"),
    )
    with pytest.raises(RuntimeError, match="Could not capture the owned Windows server identity"):
        server.ensure_started()
    assert len(scripts) == 1
    assert server.process is not None
    assert server._qubitz_owned_launch_epoch is not None
    assert server._qubitz_owner_token
    assert server._qubitz_owned_windows_receipt == {}


@pytest.mark.skipif(shutil.which("powershell.exe") is None,
                    reason="Requires real Windows PowerShell")
def test_windows_token_capture_does_not_depend_on_wsl_clock(variant, monkeypatch):
    actual_run = subprocess.run
    server, scripts = capture_server(variant, monkeypatch, result(0, receipt=True))
    server.ensure_started()
    assert len(scripts) == 1
    parent_script = "# QUBITZ_OWNER=" + server._qubitz_owner_token
    encoded_parent = base64.b64encode(parent_script.encode("utf-16-le")).decode("ascii")
    shim = "\n".join([
        "function Get-CimInstance {",
        "  param([string]$ClassName, [string]$Filter)",
        "  if ($Filter -eq \"Name='llama-server.exe'\") {",
        "    [pscustomobject]@{ ProcessId=321; ParentProcessId=654;",
        "      CreationDate=[datetime]::UtcNow.AddHours(-1);",
        "      ExecutablePath='/mnt/d/llama-server.exe';",
        "      CommandLine='llama-server.exe --model /mnt/d/model.gguf --alias owned --port 8001' }",
        "  } elseif ($Filter -eq 'ProcessId=654') {",
        "    [pscustomobject]@{ CreationDate=[datetime]::UtcNow.AddHours(-2);",
        f"      CommandLine='powershell.exe -EncodedCommand {encoded_parent}' }}",
        "  }",
        "}",
    ])
    command = base64.b64encode((shim + "\n" + scripts[0]).encode("utf-16-le")).decode("ascii")
    observed = actual_run(["powershell.exe", "-NoProfile", "-EncodedCommand", command],
                          capture_output=True, text=True, timeout=30, check=False)
    assert observed.returncode == 0, observed.stderr
    assert json.loads(observed.stdout)[0]["pid"] == 321
