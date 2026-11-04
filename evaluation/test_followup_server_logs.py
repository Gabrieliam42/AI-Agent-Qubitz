"""Model-free ownership and overwrite checks for disposable matrix server logs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import smoke_create_calculator as smoke

from evaluation import disposable_followup_matrix as matrix
from evaluation import managed_server_evidence


def _observer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> matrix.Observer:
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    output = tmp_path / "attempt"
    output.mkdir()
    return matrix.Observer(output)


def _owned_log(observer: matrix.Observer, source: Path, content: bytes) -> tuple[int, str, str]:
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    identity = (42, "639283401887095720", "c:\\llama-server.exe")
    prefix = content[:256]
    observer.owned_windows_servers[identity] = {
        "ProcessId": identity[0], "CreationDate": identity[1], "ExecutablePath": identity[2],
        "log_path": str(source), "log_prefix_bytes": len(prefix),
        "log_prefix_sha256": hashlib.sha256(prefix).hexdigest(),
    }
    return identity


def test_owned_log_is_captured_without_callback_or_tool_path(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    content = b"[startup] llama-server\nrequest completed\n"
    _owned_log(observer, tmp_path / ".cache" / "llama.cpp" / "llama-server.log", content)

    logs = matrix.collect_logs(observer)

    assert len(logs) == 1
    assert logs[0]["scope"] == "owned_server_log"
    assert Path(logs[0]["capture"]).read_bytes() == content
    assert matrix.owned_server_logs_complete(observer, logs)


@pytest.mark.parametrize("replacement", [None, b"[later launch] llama-server\n"])
def test_missing_or_overwritten_owned_log_fails_closed(tmp_path, monkeypatch, replacement):
    observer = _observer(tmp_path, monkeypatch)
    source = tmp_path / ".cache" / "llama.cpp" / "llama-server.log"
    _owned_log(observer, source, b"[original launch] llama-server\n")
    if replacement is None:
        source.unlink()
    else:
        source.write_bytes(replacement)

    logs = matrix.collect_logs(observer)

    assert len(logs) == 1
    assert "not_captured" in logs[0]
    assert not matrix.owned_server_logs_complete(observer, logs)
    assert not list(observer.output.glob("server_*.log"))


def test_captured_owned_log_survives_later_shared_log_overwrite(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    source = tmp_path / ".cache" / "llama.cpp" / "llama-server.log"
    identity = _owned_log(observer, source, b"[original launch] llama-server\n")
    observer.capture_owned_server_log(identity, "shutdown")
    source.write_bytes(b"[later launch] llama-server\n")

    logs = matrix.collect_logs(observer)

    assert len(logs) == 1
    assert Path(logs[0]["capture"]).read_bytes() == b"[original launch] llama-server\n"
    assert matrix.owned_server_logs_complete(observer, logs)


def test_owned_log_outside_project_cache_is_not_captured(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    source = tmp_path / "outside" / "llama-server.log"
    _owned_log(observer, source, b"[launch] llama-server\n")

    logs = matrix.collect_logs(observer)

    assert "not_captured" in logs[0]
    assert not matrix.owned_server_logs_complete(observer, logs)
    assert not list(observer.output.glob("server_*.log"))


def test_restart_captures_each_owned_server_before_log_truncation(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    (observer.output / "launch.json").write_text(json.dumps({"owner": "a" * 32}), encoding="utf-8")
    source = tmp_path / ".cache" / "llama.cpp" / "llama-server.log"
    source.parent.mkdir(parents=True)

    class Process:
        running = True

        def poll(self):
            return None if self.running else 0

    class Server:
        def __init__(self):
            self.log_path = source
            self.process = None
            self.launches = 0
            self.model_path = tmp_path / "model.gguf"
            self.config = SimpleNamespace(model_name="fixture")
            self._qubitz_owner_token = "a" * 32

        def resolve_executable(self):
            return "C:\\llama-server.exe"

        def ensure_started(self):
            if self.process is not None and self.process.poll() is None:
                return
            self.launches += 1
            self.log_path.write_bytes(f"[launch {self.launches}] llama-server\n".encode())
            self.process = Process()
            self._qubitz_owned_windows_receipt = {
                "pid": 100 + self.launches,
                "created": f"2026-10-23T08:00:0{self.launches}.0000000Z",
            }

        def shutdown(self):
            self.process.running = False
            self._qubitz_owned_windows_receipt = {}
            self._qubitz_cleanup_outcome = {"status": "stopped"}

    class Host:
        async def call_tool(self, _name, _arguments):
            return None

    monkeypatch.setattr(smoke, "install_request_capture", lambda *_args: None)
    base = SimpleNamespace(LlamaCppServerProcess=Server, MCPHost=Host,
                           wsl_path_to_windows_path=str, serialize_mcp_result=lambda result: result)
    observer.install(base)
    server = Server()

    server.ensure_started()
    server.process.running = False
    server.ensure_started()
    server.shutdown()
    logs = matrix.collect_logs(observer)

    assert len(logs) == 2
    assert [Path(row["capture"]).read_bytes() for row in logs] == [
        b"[launch 1] llama-server\n", b"[launch 2] llama-server\n",
    ]
    assert matrix.owned_server_logs_complete(observer, logs)


def managed_log(observer, root, *, actual_wait=True, identity_matches=True):
    server_id, token = "b" * 12, "c" * 32
    directory = root / ".cache/mcp_servers" / server_id
    directory.mkdir(parents=True)
    source = directory / "server.log"
    source.write_bytes(b"READY\n")
    identity = {"pid": 42, "parent_pid": 41, "namespace": "linux:fixture-boot", "creation": "123",
                "executable": "/fixture/python", "command_line": "/fixture/python --child config.json"}
    metadata = {"server_id": server_id, "process_owner": token, "process_identity": identity,
                "log_path": str(source), "command": ["/fixture/python", "fixture.py"]}
    records = {
        "meta.json": metadata,
        "process_launch.json": {"token": token, "command": metadata["command"]},
        "process_started.json": {"token": token, "identity": identity},
        "process_exit.json": {"token": token, "identity": identity if identity_matches else {},
                              "actual_wait": actual_wait, "tree_verified": True, "returncode": 0, "error": None},
        "supervisor_wait.json": {"token": token, "actual_wait": True, "pid": 41, "returncode": 0},
    }
    for name, value in records.items():
        (directory / name).write_text(json.dumps(value))
    observer.tools.append({"name": "start_project_mcp_server", "result": {
        "is_error": False, "structured_content": metadata}})
    return source


def test_managed_mcp_log_has_its_own_identity_and_wait_evidence(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    managed_log(observer, tmp_path)
    logs = matrix.collect_logs(observer)
    assert len(logs) == 1
    assert logs[0]["scope"] == "managed_mcp_server_log"
    assert logs[0]["actual_wait"] is True
    assert not list(observer.output.glob("server_*.log"))
    assert matrix.owned_server_logs_complete(observer, logs)
    result = {"server_logs": logs, "turns": [{"observed": {"tools": observer.tools}}]}
    assert managed_server_evidence.audit(observer.output, result) == []


@pytest.mark.parametrize("actual_wait,identity_matches", [(False, True), (True, False)])
def test_managed_log_without_original_identity_and_actual_wait_fails_closed(
    tmp_path, monkeypatch, actual_wait, identity_matches,
):
    observer = _observer(tmp_path, monkeypatch)
    managed_log(observer, tmp_path, actual_wait=actual_wait, identity_matches=identity_matches)
    logs = matrix.collect_logs(observer)
    assert not matrix.owned_server_logs_complete(observer, logs)
    assert logs[0].get("not_captured")


def test_managed_ownership_artifact_tampering_is_detected(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    managed_log(observer, tmp_path)
    logs = matrix.collect_logs(observer)
    Path(logs[0]["ownership_capture"]).write_text("{}")
    result = {"server_logs": logs, "turns": [{"observed": {"tools": observer.tools}}]}
    assert managed_server_evidence.audit(observer.output, result)


@pytest.mark.parametrize("field", ["process_owner", "process_identity", "command"])
def test_managed_audit_binds_artifacts_back_to_the_original_tool_launch(tmp_path, monkeypatch, field):
    observer = _observer(tmp_path, monkeypatch)
    managed_log(observer, tmp_path)
    logs = matrix.collect_logs(observer)
    observer.tools[0]["result"]["structured_content"][field] = "changed"
    result = {"server_logs": logs, "turns": [{"observed": {"tools": observer.tools}}]}
    assert managed_server_evidence.audit(observer.output, result)


def test_failed_start_does_not_disappear_from_managed_evidence_coverage(tmp_path, monkeypatch):
    observer = _observer(tmp_path, monkeypatch)
    managed_log(observer, tmp_path)
    observer.tools[0]["result"]["is_error"] = True
    assert not managed_server_evidence.complete(observer.tools, [])
