"""Real model-free MCP context boundaries must not end a persistent server lease."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from runtime import managed_process

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("native_windows", [False, True])
def test_managed_server_survives_mcp_context_and_stops_in_a_later_turn(variant, runner, tmp_path, native_windows):
    interpreter = Path("/mnt/c/Program Files/Python312/python.exe") if native_windows else Path(sys.executable)
    if native_windows and (sys.platform != "linux" or not interpreter.is_file() or not variant._APP.base.in_wsl()):
        pytest.skip("The native Windows from WSL lifecycle fixture requires Windows Python 3.12 interop")
    script = tmp_path / "persistent.py"
    script.write_text(
        "import json,signal,time\nfrom pathlib import Path\n"
        "running=True\ndef stop(*_args):\n global running\n running=False\n"
        "signal.signal(signal.SIGTERM,stop)\nsignal.signal(signal.SIGINT,stop)\n"
        "Path('state.json').write_text(json.dumps({'state':'ready'}))\nprint('READY',flush=True)\n"
        "try:\n while running: time.sleep(.05)\n"
        "finally: Path('state.json').write_text(json.dumps({'state':'stopped'}))\n"
    )
    started = {}

    async def exercise():
        broker = receipts.begin(runner, "Start persistent.py as a managed server; leave it running for my next instruction.")
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            async with variant._APP.base.MCPHost(tmp_path) as host:
                await host.list_tools()
                result = await host.call_tool("start_project_mcp_server", {
                    "server_script": "persistent.py", "python_path": str(interpreter),
                    "ready_substring": "READY", "wait_seconds": 10})
                started.update(result.structuredContent or {})
                assert not result.isError, started
                assert started["status"] == "ready"
                assert started["process_identity"]["namespace"].startswith("windows" if native_windows else "linux:")
            assert json.loads((tmp_path / "state.json").read_text())["state"] == "ready", (
                "Closing a short-lived MCP transport stopped the server promised to the next turn")
            directory = Path(started["log_path"]).parent
            assert not (directory / "process_exit.json").exists()
            broker.begin_turn("Stop that managed server now.", None)
            async with variant._APP.base.MCPHost(tmp_path) as host:
                await host.list_tools()
                inspected = await host.call_tool("read_project_mcp_server_log", {"server_id": started["server_id"]})
                assert inspected.structuredContent["meta"]["process_identity"] == started["process_identity"]
                stopped = await host.call_tool("stop_project_mcp_server", {"server_id": started["server_id"]})
                assert not stopped.isError, stopped.structuredContent
                assert stopped.structuredContent["stopped"] is True
                assert stopped.structuredContent["actual_wait"] is True
                assert stopped.structuredContent["supervisor_actual_wait"] is True
            if not native_windows and sys.platform != "win32":
                assert json.loads((tmp_path / "state.json").read_text())["state"] == "stopped"
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    try:
        asyncio.run(exercise())
    finally:
        if started.get("process_owner"):
            session = managed_process.ManagedServerProcess.recover(Path(started["log_path"]).parent,
                                                                   started["process_owner"])
            assert session is not None
            outcome = session.stop()
            assert outcome["stopped"] and outcome["actual_wait"], outcome


@pytest.mark.parametrize("failure", ["permission", "schema"])
def test_lifecycle_dispatch_retains_permission_and_argument_boundaries(variant, runner, monkeypatch, failure):
    broker = receipts.begin(runner, "Start persistent.py as a managed server.")
    host = boundaries.host_for(variant, broker)
    host._qubitz_tool_contracts["start_project_mcp_server"] = SimpleNamespace(
        inputSchema={"type": "object", "required": ["server_script"], "additionalProperties": False,
                     "properties": {"server_script": {"type": "string"}}}, outputSchema=None)
    dispatched = []
    monkeypatch.setattr(variant.LocalMCPServerManager, "start_server", lambda *_args, **_kw: dispatched.append(True))
    if failure == "permission":
        monkeypatch.setattr(broker, "authorize", lambda *_args: (False, {"status": "permission_denied"}))
        arguments = {"server_script": "persistent.py"}
    else:
        arguments = {}
    result = asyncio.run(host.call_tool("start_project_mcp_server", arguments))
    assert result.isError
    assert result.structuredContent["dispatch_attempted"] is False
    assert dispatched == []
    assert host.dispatched == []


@pytest.mark.parametrize("tool,arguments,status", [
    ("start_project_mcp_server", {"server_script": "fixture.py"}, "readiness_timeout"),
    ("stop_project_mcp_server", {"server_id": "a" * 12}, "ownership_unknown"),
    ("list_project_mcp_tools", {"server_id": "a" * 12}, "failed"),
    ("list_project_mcp_tools", {"server_id": "a" * 12}, "mcp_startup_timeout"),
    ("call_project_mcp_tool", {"server_id": "a" * 12, "tool_name": "check"}, "failed"),
])
def test_local_lifecycle_failures_remain_errors_not_transport_success(variant, runner, monkeypatch, tool, arguments, status):
    broker = receipts.begin(runner, "Use the named managed server operation.")
    host = boundaries.host_for(variant, broker)
    monkeypatch.setattr(broker, "authorize", lambda *_args: (True, None))
    monkeypatch.setattr(broker, "reserve_mcp_invocation", lambda *_args: None)
    monkeypatch.setitem(type(host).call_tool.__globals__, "_mcp_dispatch_blocker", lambda *_args: None)
    method = {"start_project_mcp_server": "start_server", "stop_project_mcp_server": "stop_server",
              "list_project_mcp_tools": "list_tools", "call_project_mcp_tool": "call_tool"}[tool]
    calls = []

    def invoke(*_args, **kwargs):
        calls.append(kwargs)
        return {"status": status, "server_id": "a" * 12}

    monkeypatch.setattr(variant.LocalMCPServerManager, method, invoke)
    result = asyncio.run(host.call_tool(tool, arguments))
    assert result.isError
    assert result.structuredContent["status"] == status
    assert calls == [arguments]
    assert host.dispatched == []


def test_unmanaged_stdio_invocation_keeps_its_original_transport(variant, runner, monkeypatch):
    broker = receipts.begin(runner, "Call check via the session-scoped stdio fixture.py.")
    host = boundaries.host_for(variant, broker)
    monkeypatch.setattr(broker, "authorize", lambda *_args: (True, None))
    monkeypatch.setattr(broker, "reserve_mcp_invocation", lambda *_args: None)
    monkeypatch.setitem(type(host).call_tool.__globals__, "_mcp_dispatch_blocker", lambda *_args: None)
    arguments = {"server_reference": "fixture.py", "tool_name": "check"}
    result = asyncio.run(host.call_tool("call_project_mcp_tool", arguments))
    assert not result.isError
    assert host.dispatched == [("call_project_mcp_tool", arguments)]


@pytest.mark.parametrize("metadata_failure", [False, True])
def test_cancelled_start_reconciles_new_session_before_propagating_cancellation(variant, runner, tmp_path, monkeypatch, metadata_failure):
    (tmp_path / "persistent.py").write_text("import time\nprint('READY',flush=True)\ntime.sleep(60)\n")
    broker = receipts.begin(runner, "Start persistent.py as a managed server.")
    host = boundaries.host_for(variant, broker)
    started, stop_calls = {}, []
    ready, release = threading.Event(), threading.Event()
    original_start, original_stop = variant.LocalMCPServerManager.start_server, variant.LocalMCPServerManager.stop_server

    def delayed_start(manager, *args, **kwargs):
        payload = original_start(manager, *args, **kwargs)
        started.update(payload)
        ready.set()
        assert release.wait(10), "Cancellation fixture did not release the completed startup"
        return payload

    def recorded_stop(manager, server_id):
        stop_calls.append(server_id)
        return original_stop(manager, server_id)

    monkeypatch.setattr(variant.LocalMCPServerManager, "start_server", delayed_start)
    monkeypatch.setattr(variant.LocalMCPServerManager, "stop_server", recorded_stop)
    if metadata_failure:
        def failed_write(*_args):
            raise OSError("fixture overlapping post-launch metadata failure")

        monkeypatch.setattr(variant.LocalMCPServerManager, "_write_meta", failed_write)

    async def exercise():
        task = asyncio.create_task(host.call_tool("start_project_mcp_server", {
            "server_script": "persistent.py", "python_path": sys.executable,
            "ready_substring": "READY", "wait_seconds": 10}))
        try:
            assert await asyncio.to_thread(ready.wait, 15)
            assert started["status"] == ("failed" if metadata_failure else "ready")
            task.cancel()
            await asyncio.sleep(.05)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            session = managed_process.ManagedServerProcess.recover(Path(started["log_path"]).parent,
                                                                   started["process_owner"])
            assert session is not None
            assert stop_calls == ([] if metadata_failure else [started["server_id"]])
            assert session.exit_receipt()["actual_wait"] is True
            assert session.supervisor_waited is True
            result = host._qubitz_cancelled_managed_result
            assert result.isError
            assert result.structuredContent["server_id"] == started["server_id"]
            assert result.structuredContent["cancellation_cleanup"]["stopped"] is True
            assert broker.evidence[-1]["success"] is False
        finally:
            release.set()
            if not task.done():
                await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(exercise())
    finally:
        release.set()
        if started.get("process_owner"):
            session = managed_process.ManagedServerProcess.recover(Path(started["log_path"]).parent,
                                                                   started["process_owner"])
            assert session is not None
            assert session.stop()["stopped"] is True


@pytest.mark.parametrize("successful_writes", [0, 1])
def test_postlaunch_metadata_failure_retains_identity_and_verifies_cleanup(variant, tmp_path, monkeypatch, successful_writes):
    (tmp_path / "persistent.py").write_text("import time\ntime.sleep(60)\n")
    manager = variant.LocalMCPServerManager(tmp_path, tmp_path / "runtime", base=variant._APP.base)
    sessions = []
    writes = []
    launch = manager._popen_python
    original_write = manager._write_meta

    def tracked(*args, **kwargs):
        process, command = launch(*args, **kwargs)
        sessions.append(process)
        return process, command

    def failed_write(*args):
        writes.append(args)
        if len(writes) <= successful_writes:
            return original_write(*args)
        raise OSError("fixture post-launch metadata failure")

    monkeypatch.setattr(manager, "_popen_python", tracked)
    monkeypatch.setattr(manager, "_write_meta", failed_write)
    try:
        result = manager.start_server("persistent.py", python_path=sys.executable,
                                      ready_substring="READY", wait_seconds=1)
        assert result["status"] == "failed"
        assert "fixture post-launch metadata failure" in result["error"]
        assert result["process_owner"] == sessions[0].token
        assert result["process_identity"] == sessions[0].receipt["identity"]
        assert result["startup_cleanup"]["stopped"] is True
        assert result["actual_wait"] is True
        assert sessions[0].supervisor_waited is True
    finally:
        for session in sessions:
            assert session.stop()["stopped"] is True
