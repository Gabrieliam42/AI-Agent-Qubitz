"""Large requests use stdin and survive the actual WSL-to-Windows HTTP path."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from evaluation import test_smoke_runtime_repairs as bridge_repairs

bridge_type = bridge_repairs.bridge_type
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def windows_http_server():
    script = ROOT / "evaluation/windows_bridge_http_fixture.py"
    if os.name == "nt":
        command = [sys.executable, "-B", "-u", str(script)]
    else:
        executable = Path("/mnt/c/Program Files/Python312/python.exe")
        if shutil.which("powershell.exe") is None or not executable.is_file():
            pytest.skip("Real Windows Python/PowerShell interop is unavailable")
        windows_path = subprocess.run(["wslpath", "-w", str(script)], capture_output=True,
                                      text=True, check=True, timeout=10).stdout.strip()
        command = [str(executable), "-B", "-u", windows_path]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, encoding="utf-8")
    ready = queue.Queue()
    reader = threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True)
    reader.start()
    try:
        receipt = json.loads(ready.get(timeout=20))
        assert receipt["pid"] > 0 and receipt["port"] > 0
        yield f"http://127.0.0.1:{receipt['port']}"
    finally:
        try:
            _output, errors = process.communicate("\n", timeout=15)
        except subprocess.TimeoutExpired:
            process.terminate()
            _output, errors = process.communicate(timeout=10)
            pytest.fail(f"Owned Windows HTTP fixture exceeded shutdown deadline: {errors}")
        reader.join(2)
        assert process.returncode == 0 and not errors


def test_large_payload_is_complete_and_absent_from_command_line(bridge_type, monkeypatch):
    calls = []
    payload = {"marker": "private prompt marker", "messages": [{"content": "x" * 500_000}]}

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"accepted":true}', "")

    monkeypatch.setattr(subprocess, "run", run)
    assert bridge_type().post_json("/v1/chat/completions", payload) == {"accepted": True}
    command, kwargs = calls[0]
    assert len(calls) == 1 and sum(map(len, command)) < 2048
    assert payload["marker"] not in " ".join(command)
    assert json.dumps(payload, ensure_ascii=True) in kwargs["input"]
    assert kwargs["encoding"] == "utf-8" and kwargs["timeout"] == 600
    bootstrap = base64.b64decode(command[-1]).decode("utf-16le")
    assert "ReadToEnd" in bootstrap and "OutputEncoding" in bootstrap


def test_timeout_propagates_without_replaying_request(bridge_type, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        bridge_type().post_json("/v1/chat/completions", {})
    assert len(calls) == 1


@pytest.mark.parametrize("characters", [8, 500_000])
def test_real_bridge_preserves_request_bytes_and_unicode(bridge_type, windows_http_server, characters):
    payload = {"model": "fixture", "marker": "quotes '\" \\ \n\u0219\u4e16\U0001f680",
               "messages": [{"role": "user", "content": "x" * characters}]}
    body = json.dumps(payload, ensure_ascii=True).encode("ascii")
    response = bridge_type(windows_http_server).post_json("/echo", payload)
    assert response == {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
                        "marker": payload["marker"], "model": payload["model"]}


def test_real_bridge_preserves_status_errors_and_timeout(bridge_type, windows_http_server):
    bridge = bridge_type(windows_http_server)
    assert bridge.get_json("/ready") == {"ready": True}
    assert bridge.probe_json("/loading")["status_code"] == 503
    with pytest.raises(RuntimeError, match="HTTP 503.*Loading model"):
        bridge.get_json("/loading")
    with pytest.raises(RuntimeError, match="fixture rejected request|422"):
        bridge.post_json("/error", {})
    with pytest.raises(RuntimeError):
        bridge.probe_json("/slow", timeout=1)
    assert bridge.get_json("/ready") == {"ready": True}
