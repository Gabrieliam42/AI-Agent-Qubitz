"""Managed server stop authority must come from the original launch, never a PID."""
from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import managed_process

variant = receipts.variant
runner = receipts.runner


def legacy_manager(variant, tmp_path):
    manager = variant.LocalMCPServerManager(tmp_path, tmp_path / "runtime",
                                          base=SimpleNamespace(in_wsl=lambda: True))
    manager._write_meta("a" * 12, {"server_id": "a" * 12, "pid": 12345, "status": "running"})
    return manager


def test_legacy_pid_cannot_authorize_any_signal(variant, tmp_path, monkeypatch):
    manager = legacy_manager(variant, tmp_path)
    signals = []
    monkeypatch.setattr(variant.shutil, "which", lambda _name: "taskkill.exe")
    monkeypatch.setattr(variant.subprocess, "run", lambda command, **_kw:
                        signals.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(variant.os, "kill", lambda *args: signals.append(args))
    result = manager.stop_server("a" * 12)
    assert signals == []
    assert result["stopped"] is False
    assert result["status"] == "ownership_unknown"
    assert manager._read_meta("a" * 12)["status"] != "stopped"


def test_refresh_never_uses_a_signal_as_an_identity_probe(variant, tmp_path, monkeypatch):
    manager = legacy_manager(variant, tmp_path)
    signals = []
    monkeypatch.setattr(variant.os, "kill", lambda *args: signals.append(args))
    result = manager._refresh_status(manager._read_meta("a" * 12))
    assert signals == []
    assert result["status"] == "ownership_unknown"


@pytest.mark.parametrize("actual_wait", [False, True])
def test_completion_requires_both_stop_and_actual_wait(variant, runner, actual_wait):
    broker = receipts.begin(runner, "Stop the managed server.")
    broker.record_result("stop_project_mcp_server", {"server_id": "a" * 12},
                         receipts.result({"stopped": True, "actual_wait": actual_wait}))
    assert broker.evidence[-1]["success"] is actual_wait
    assert ("service_stopped" in broker.evidence[-1]["categories"]) is actual_wait


def test_real_manager_preserves_identity_and_waits_before_stopped(variant, tmp_path):
    manager = variant.LocalMCPServerManager(tmp_path, tmp_path / "runtime")
    script = tmp_path / "server.py"
    script.write_text("import time\nprint('READY', flush=True)\ntime.sleep(60)\n")
    started = manager.start_server("server.py", python_path=sys.executable,
                                   ready_substring="READY", wait_seconds=10)
    session = manager._owned_session(started)
    try:
        assert started["status"] == "ready"
        assert started["process_identity"]["pid"] == started["pid"]
        assert session is not None
        changed = dict(started)
        changed["process_identity"] = {**started["process_identity"], "pid": started["pid"] + 1}
        manager._write_meta(started["server_id"], changed)
        assert manager.stop_server(started["server_id"])["status"] == "ownership_unknown"
        assert session.poll() is None
        manager._write_meta(started["server_id"], started)
        stopped = manager.stop_server(started["server_id"])
        assert stopped["stopped"] is True
        assert stopped["actual_wait"] is True
        assert stopped["supervisor_actual_wait"] is True
        assert manager._read_meta(started["server_id"])["actual_wait"] is True
    finally:
        if session is not None and session.poll() is None:
            session.stop()


@pytest.mark.parametrize("clock_jump,ready", [(3600, True), (-3600, False)])
def test_readiness_deadline_ignores_wall_clock_jumps(variant, tmp_path, monkeypatch, clock_jump, ready):
    manager = variant.LocalMCPServerManager(tmp_path, tmp_path / "runtime")
    (tmp_path / "server.py").write_text("# No process is launched by this clock fixture.\n")
    elapsed, wall_reads = [0.0], [0]
    log_file = [None]

    def wall_time():
        wall_reads[0] += 1
        return 0.0 if wall_reads[0] == 1 else float(clock_jump)

    def sleep(seconds):
        elapsed[0] += seconds
        assert elapsed[0] <= 2, "A backwards wall-clock jump extended the readiness budget"
        if ready:
            log_file[0].write_text("READY\n")

    def launch(*_args, log_path, **_kwargs):
        log_file[0] = log_path
        log_path.parent.mkdir(parents=True)
        log_path.write_text("")
        process = SimpleNamespace(pid=12345, token="a" * 32,
                                  receipt={"identity": {"pid": 12345}, "containment": "fixture"},
                                  poll=lambda: None)
        return process, ["fixture-only"]

    monkeypatch.setattr(manager, "_popen_python", launch)
    monkeypatch.setattr(variant, "time", SimpleNamespace(time=wall_time, sleep=sleep, strftime=variant.time.strftime,
                                                       monotonic=lambda: elapsed[0]))
    started = manager.start_server("server.py", python_path=sys.executable,
                                   ready_substring="READY", wait_seconds=1)
    assert started["status"] == ("ready" if ready else "readiness_timeout")
    assert 0 < elapsed[0] <= 1


@pytest.mark.skipif(sys.platform != "linux", reason="Actual WSL to Windows interpreter fixture")
def test_wsl_windows_managed_server_uses_native_receipt_and_wait(variant):
    interpreter = Path("/mnt/c/Program Files/Python312/python.exe")
    if not interpreter.is_file() or not variant._APP.base.in_wsl():
        pytest.skip("Native Windows Python interop is unavailable")
    workspace = receipts.ROOT / ".cache/managed_server_lifecycle_repair_20261025_01/interop" / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    script = workspace / "server.py"
    script.write_text("import time\nprint('READY', flush=True)\ntime.sleep(60)\n")
    manager = variant.LocalMCPServerManager(workspace, workspace / "runtime", base=variant._APP.base)
    started = manager.start_server("server.py", python_path=str(interpreter),
                                   ready_substring="READY", wait_seconds=15)
    session = manager._owned_session(started)
    try:
        assert started["process_identity"]["namespace"] == "windows"
        assert started["process_containment"] == "windows_job"
        assert started["status"] == "ready"
        result = manager.stop_server(started["server_id"])
        assert result["stopped"] is True
        assert result["actual_wait"] is True
        assert result["supervisor_actual_wait"] is True
    finally:
        if session is not None and session.poll() is None:
            session.stop()


def test_unknown_listener_and_token_cannot_authorize_cleanup(variant, monkeypatch):
    actions = []
    monkeypatch.setattr(variant.subprocess, "run", lambda *args, **kwargs: actions.append((args, kwargs)))
    monkeypatch.setattr(variant.os, "kill", lambda *args: actions.append(args))
    assert variant._terminate_llamacpp_listener_processes(SimpleNamespace(), "http://127.0.0.1:8001") == []
    with pytest.raises(RuntimeError, match="token alone"):
        variant._terminate_windows_process_token("QUBITZ_fixture")
    assert actions == []


@pytest.mark.parametrize("cancelled", [False, True])
def test_owned_command_timeout_and_cancellation_wait_for_the_tree(variant, tmp_path, monkeypatch, cancelled):
    launch = managed_process.launch_command_session
    sessions = []

    def tracked(*args, **kwargs):
        session = launch(*args, **kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr(managed_process, "launch_command_session", tracked)
    cancel = threading.Event()
    if cancelled:
        cancel.set()
    command = [sys.executable, "-c", "import time;print('started',flush=True);time.sleep(60)"]
    expected = InterruptedError if cancelled else variant.subprocess.TimeoutExpired
    with pytest.raises(expected):
        variant._run_owned_process(command, cwd=tmp_path, timeout_seconds=1, cancel_event=cancel)
    if cancelled:
        assert sessions == []
        return
    assert len(sessions) == 1
    ended = sessions[0].exit_receipt()
    assert ended["actual_wait"] is True
    assert ended["tree_verified"] is True
    assert sessions[0].supervisor_waited is True


@pytest.mark.skipif(sys.platform != "linux", reason="Actual WSL to Windows PowerShell cleanup fixture")
def test_wsl_windows_command_cancellation_uses_the_native_job(variant):
    if not Path("/mnt/c/Program Files/Python312/python.exe").is_file() or not variant._APP.base.in_wsl():
        pytest.skip("Native Windows Python interop is unavailable")
    workspace = receipts.ROOT / ".cache/managed_server_lifecycle_repair_20261025_01/interop" / uuid.uuid4().hex
    workspace.mkdir(parents=True)
    cancel = threading.Event()
    marker = workspace / "ready.txt"

    def cancel_after_started():
        import time

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(.05)
        cancel.set()

    watcher = threading.Thread(target=cancel_after_started)
    watcher.start()
    try:
        with pytest.raises(InterruptedError):
            variant._run_owned_process(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                        "Set-Content -LiteralPath ready.txt -Value ready; Start-Sleep -Seconds 60"],
                                       cwd=workspace, timeout_seconds=30, cancel_event=cancel,
                                       windows_token="QUBITZ_fixture_owned_native")
        assert marker.exists()
    finally:
        watcher.join(timeout=22)


@pytest.mark.parametrize("source,ready,status", [
    ("raise SystemExit(3)\n", "", "failed"),
    ("import time\ntime.sleep(60)\n", "never-ready", "readiness_timeout"),
])
def test_failed_start_preserves_launch_identity_and_never_claims_ready(variant, tmp_path, source, ready, status):
    manager = variant.LocalMCPServerManager(tmp_path, tmp_path / "runtime")
    (tmp_path / "server.py").write_text(source)
    started = manager.start_server("server.py", python_path=sys.executable,
                                   ready_substring=ready, wait_seconds=.1)
    session = manager._owned_session(started)
    try:
        assert started["status"] == status
        assert started["server_id"]
        assert started["process_owner"]
        assert started["process_identity"]["pid"] == started["pid"]
        assert started["error"]
        if status == "failed":
            assert started["actual_wait"] is True
    finally:
        if session is not None:
            assert session.stop()["stopped"]
