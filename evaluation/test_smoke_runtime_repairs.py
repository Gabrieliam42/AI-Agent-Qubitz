"""Model-free bridge failures, smoke provenance, and creation-scope recovery."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import shutil
import socket
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from evaluation import smoke_create_calculator as smoke
from evaluation import test_followup_state_receipts as receipts
from runtime.paths import DEFAULT_VARIANT, variant_path
from runtime.source import embedded_source, read_variant_source
from runtime.symbols import symbol_sources

ROOT = Path(__file__).resolve().parents[1]
variant = receipts.variant
runner = receipts.runner


@pytest.fixture(params=["composed", "registry"])
def bridge_type(request):
    if request.param == "composed":
        base, _span = embedded_source(read_variant_source(variant_path(DEFAULT_VARIANT)))
        tree = ast.parse(base)
    else:
        sources = symbol_sources("base", ROOT / "runtime")
        block = next(value for value in sources.values() if value.startswith("class WindowsBridgeHTTPTransport:"))
        tree = ast.parse(block)
    node = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                and node.name == "WindowsBridgeHTTPTransport")
    namespace = {"base64": base64, "json": json, "subprocess": subprocess, "textwrap": textwrap,
                 "Any": Any, "DEFAULT_LLAMACPP_BASE_URL": "http://127.0.0.1:8001"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "<bridge under test>", "exec"), namespace)  # noqa: S102
    return namespace["WindowsBridgeHTTPTransport"]


@pytest.mark.parametrize("method", ["get_json", "post_json"])
@pytest.mark.parametrize("output", ["", "null", "[]", "not json"])
def test_bridge_rejects_absent_or_nonobject_responses(bridge_type, monkeypatch, method, output):
    bridge = bridge_type()
    monkeypatch.setattr(bridge, "_run_script", lambda _script: output)
    arguments = ("/v1/models",) if method == "get_json" else ("/v1/chat/completions", {})
    with pytest.raises(RuntimeError, match="Windows HTTP bridge returned"):
        getattr(bridge, method)(*arguments)


def test_bridge_preserves_model_response_and_terminates_powershell_errors(bridge_type, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append(base64.b64decode(command[-1]).decode("utf-16le"))
        envelope = {"status_code": 200, "text": '{"data":[{"id":"fixture"}]}'}
        return subprocess.CompletedProcess(command, 0, json.dumps(envelope), "")

    monkeypatch.setattr(subprocess, "run", run)
    assert bridge_type().get_json("/v1/models") == {"data": [{"id": "fixture"}]}
    assert calls[0].startswith("$ErrorActionPreference = 'Stop'\n")


def test_bridge_preserves_loading_http_status_and_body(bridge_type, monkeypatch):
    bridge = bridge_type()
    body = '{"error":{"message":"Loading model"}}'
    monkeypatch.setattr(bridge, "_run_script", lambda _script:
                        json.dumps({"status_code": 503, "text": body}))
    assert bridge.probe_json("/v1/models") == {
        "status_code": 503, "text": body, "json": json.loads(body),
    }
    with pytest.raises(RuntimeError, match="HTTP 503.*Loading model"):
        bridge.get_json("/v1/models")


def test_bridge_propagates_nonzero_process_status(bridge_type, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda command, **_kwargs:
                        subprocess.CompletedProcess(command, 1, "", "fixture HTTP failure"))
    with pytest.raises(RuntimeError, match="fixture HTTP failure"):
        bridge_type().get_json("/v1/models")


@pytest.mark.parametrize("method", ["get_json", "post_json"])
def test_real_windows_bridge_rejects_closed_port_without_a_model(bridge_type, method):
    if shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell interop is unavailable")
    with socket.socket() as guard:
        guard.bind(("127.0.0.1", 0))
        bridge = bridge_type(f"http://127.0.0.1:{guard.getsockname()[1]}")
        arguments = ("/v1/models",) if method == "get_json" else ("/v1/chat/completions", {})
        with pytest.raises(RuntimeError):
            getattr(bridge, method)(*arguments)


def test_stale_log_is_not_captured_even_if_its_timestamp_was_touched(tmp_path):
    source = tmp_path / "shared.log"
    source.write_text("old model output\n", encoding="utf-8")
    baseline = smoke.server_log_snapshot(source)
    started = time.time_ns()
    os.utime(source, ns=(started + 1000000, started + 1000000))
    receipt = smoke.capture_server_log(source, tmp_path, baseline, started, [{"ProcessId": 42}])
    assert receipt["status"] == "unchanged_not_captured"
    assert not (tmp_path / "llama-server.log").exists()


@pytest.mark.parametrize("append", [False, True])
def test_run_log_capture_excludes_older_bytes_and_records_process_evidence(tmp_path, append):
    source = tmp_path / "shared.log"
    source.write_bytes(b"old output\n")
    baseline = smoke.server_log_snapshot(source)
    started = time.time_ns()
    fresh = b"current run output\n"
    source.write_bytes((b"old output\n" if append else b"") + fresh)
    # The Windows-mounted test filesystem can truncate explicit utime to seconds.
    future = (started // 10**9 + 2) * 10**9
    os.utime(source, ns=(future, future))
    assert source.stat().st_mtime_ns >= started
    owners = [{"ProcessId": 42, "CreationDate": "exact fixture identity"}]
    receipt = smoke.capture_server_log(source, tmp_path, baseline, started, owners)
    assert receipt["status"] == "captured"
    assert receipt["observed_servers"] == owners
    assert receipt["offset"] == (len(b"old output\n") if append else 0)
    assert receipt["sha256"] == hashlib.sha256(fresh).hexdigest()
    assert (tmp_path / "llama-server.log").read_bytes() == fresh


def test_new_log_without_observed_owner_is_preserved_as_unattributed(tmp_path):
    source = tmp_path / "shared.log"
    source.write_bytes(b"startup failed\n")
    receipt = smoke.capture_server_log(source, tmp_path, None, 0, [])
    assert receipt["status"] == "captured_unattributed"
    assert (tmp_path / receipt["artifact"]).read_bytes() == source.read_bytes()
    assert not (tmp_path / "llama-server.log").exists()


def test_missing_server_log_never_substitutes_another_runs_log(tmp_path):
    receipt = smoke.capture_server_log(tmp_path / "missing.log", tmp_path, None, 0, [])
    assert receipt["status"] == "not_created"
    assert not list(tmp_path.iterdir())


def test_creation_verification_stays_within_requested_targets(variant, runner, tmp_path):
    prompt = "Create calculator.py as a basic calculator."
    broker = receipts.begin(runner, prompt)
    target = tmp_path / "calculator.py"
    content = "def add(a, b):\n    return a + b\n"
    target.write_text(content, encoding="utf-8")
    broker.record_result("write_file", {"path": "calculator.py", "content": content}, receipts.result({
        "path": "calculator.py", "created": True,
        "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    assert not variant._artifact_completion_can_end_turn(broker)
    contract = runner._runtime_tool_contract_for_harness("named_file_operation", "coding_task", prompt)
    assert "Do not create extra test or helper files" in contract
    stops = []
    broker.stop_callback = stops.append
    for expected, name in enumerate(("test_calculator.py", "_smoke_test.py"), start=1):
        arguments = {"path": name, "content": "assert True\n"}
        allowed, payload = broker.authorize("write_file", arguments)
        assert not allowed and payload["policy_id"] == "creation_target_scope"
        assert payload["retryable"] is False
        assert "inline checks" in payload["instruction"]
        assert broker.register_permission_denial("write_file", arguments, payload) == expected
    assert stops == ["repeated equivalent permission denials"]
    assert target.read_text(encoding="utf-8") == content
    assert sorted(path.name for path in tmp_path.glob("*.py")) == ["calculator.py"]
    record = next(iter(broker.permission_denials.values()))
    assert set(record["targets"]) == {str(tmp_path / "test_calculator.py"), str(tmp_path / "_smoke_test.py")}
    broker.begin_turn(prompt, None)
    assert not broker.permission_denial_counts


def test_explicit_test_file_creation_and_execution_prohibition_remain_independent(variant, runner, tmp_path):
    prompt = "Create calculator.py and test_calculator.py. Do not execute any code."
    broker = receipts.begin(runner, prompt)
    arguments = {"files": [{"path": name, "content": "pass\n"}
                           for name in ("calculator.py", "test_calculator.py")]}
    assert broker.authorize("write_files", arguments)[0]
    assert not broker.authorize("run_command", {"command": "python -c 'assert 1 + 1 == 2'"})[0]
