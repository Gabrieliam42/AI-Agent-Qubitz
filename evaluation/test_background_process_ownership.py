"""Model-free background-job ownership and truthful cancellation regressions.

Only disposable fixture commands may launch; raw PID signaling is intercepted.
Validation evidence (2026-10-25, WSL project .venv, Python 3.12.3):
  Ruff check --fix --no-cache and check --no-cache: passed for this file.
  QUBITZ_TEST_ALL_VARIANTS=0
  QUBITZ_TEST_VARIANT=AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py
  .venv/bin/python -B -m pytest -q -p no:cacheprovider
    evaluation/test_background_process_ownership.py --tb=short
    --basetemp=.cache/background_process_ownership_red_20261025_52cda634c21442aab89c220bd48b67e3
  Result: 14 failed in 17.86s, exit 1 (intentional pre-fix RED baseline).
  Six legacy cases and the false-wait case attempted mocked kill/killpg calls.
  Traversal and absolute IDs reached mocked signals; other invalid IDs raised
  FileNotFoundError instead of ValueError. The real lifecycle case rejected
  direct Popen before spawning; real managed cancellation is not yet verified.
  Offline flags and model-start/load guards were enabled. No model was loaded,
  acquired, or inferred, and no unowned process was signaled.
  This evidence describes the initial run, not a later runtime revision.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import managed_process as managed

variant = receipts.variant


@pytest.fixture
def manager(variant, tmp_path, monkeypatch):
    def forbid_model(*_args, **_kwargs):
        pytest.fail("Background ownership test attempted model loading or startup")

    base = variant._APP.base
    monkeypatch.setattr(base.AgentRunner, "_get_llm", forbid_model)
    monkeypatch.setattr(base.LlamaCppServerProcess, "ensure_started", forbid_model)
    monkeypatch.setattr(variant.LocalBackgroundJobManager, "start", forbid_model)
    return variant.LocalBackgroundJobManager(tmp_path / "runtime", Path(variant.__file__))


@pytest.fixture
def signal_attempts(variant, monkeypatch):
    attempts = []

    def record(name):
        def intercepted(*args, **kwargs):
            attempts.append((name, args, kwargs))
            return SimpleNamespace(returncode=0)

        return intercepted

    monkeypatch.setattr(variant.os, "kill", record("kill"))
    monkeypatch.setattr(variant.os, "killpg", record("killpg"), raising=False)
    monkeypatch.setattr(variant.subprocess, "run", record("subprocess.run"))
    monkeypatch.setattr(variant.subprocess, "Popen", record("subprocess.Popen"))
    monkeypatch.setattr(variant.shutil, "which", lambda _name: "taskkill.exe")
    return attempts


@pytest.mark.parametrize("status", ["running", "completed", "cancelled"])
@pytest.mark.parametrize("job_type", [None, "project_launch"])
def test_legacy_pid_metadata_cannot_authorize_signaling(manager, signal_attempts, status, job_type):
    job_id = "a" * 12
    metadata = {"job_id": job_id, "pid": 12345, "status": status}
    if job_type is not None:
        metadata["job_type"] = job_type
    manager._write_meta(job_id, metadata)

    outcome = manager.cancel_job(job_id)

    assert signal_attempts == [], "Legacy metadata authorized a raw PID operation"
    assert outcome["cancelled"] is False
    assert outcome["actual_wait"] is False
    assert outcome["status"] == "ownership_unknown"
    assert manager._read_meta(job_id) == metadata


@pytest.mark.parametrize("invalid_id", ["../escaped", "..\\escaped", "absolute", "short", "g" * 12, ""])
def test_invalid_job_id_is_rejected_before_metadata_or_signaling(manager, signal_attempts, invalid_id):
    escaped = manager.root.parent / "escaped"
    escaped.mkdir()
    path = escaped / "meta.json"
    original = json.dumps({"job_id": "b" * 12, "pid": 12345, "status": "running"}).encode()
    path.write_bytes(original)
    job_id = str(escaped.resolve()) if invalid_id == "absolute" else invalid_id

    try:
        with pytest.raises(ValueError):
            manager.cancel_job(job_id)
    finally:
        assert signal_attempts == [], "Invalid job ID reached a raw PID operation"
        assert path.read_bytes() == original, "Cancellation changed escaped metadata"


def test_cancel_requires_actual_wait_from_recovered_owner(manager, monkeypatch, signal_attempts):
    job_id, token = "c" * 12, "d" * 32
    identity = {"pid": 12345, "creation": "fixture-creation", "namespace": "fixture-only"}
    metadata = {"job_id": job_id, "job_type": "project_launch", "pid": identity["pid"],
                "status": "running", "process_owner": token, "process_identity": identity,
                "actual_wait": False, "finished_at": None, "returncode": None}
    manager._write_meta(job_id, metadata)
    recoveries, stops = [], []

    def stop(**kwargs):
        stops.append(kwargs)
        return {"stopped": True, "actual_wait": False, "status": "stop_unknown", "returncode": None}

    session = SimpleNamespace(token=token, receipt={"identity": identity}, stop=stop,
                              poll=lambda: None, supervisor_waited=False)

    def recover(_cls, directory, owner):
        recoveries.append((Path(directory).resolve(), owner))
        return session

    monkeypatch.setattr(managed.ManagedServerProcess, "recover", classmethod(recover))
    outcome = manager.cancel_job(job_id)

    assert signal_attempts == []
    assert recoveries == [((manager.root / job_id).resolve(), token)]
    assert len(stops) == 1
    assert outcome["cancelled"] is False
    assert outcome["actual_wait"] is False
    saved = manager._read_meta(job_id)
    assert saved["status"] != "cancelled"
    assert saved.get("finished_at") is None


def test_real_command_cancel_retains_owner_waits_and_locked_terminal_state(manager, variant, tmp_path, monkeypatch):
    launch = getattr(managed, "launch_command_session", None)
    state_lock = getattr(managed, "STATE_LOCK", None)
    assert callable(launch), "The managed command-session launcher is required before spawning a fixture"
    assert state_lock is not None, "Background terminal updates require the module STATE_LOCK"
    original_popen = variant.subprocess.Popen
    original_thread = threading.Thread
    original_recover = managed.ManagedServerProcess.recover
    original_write = manager._write_meta
    launching = threading.local()
    sessions, workers, recoveries, terminal_writes = [], [], [], []

    def tracked_launch(*args, **kwargs):
        launching.allowed = True
        try:
            session = launch(*args, **kwargs)
            sessions.append(session)
            return session
        finally:
            launching.allowed = False

    def owned_popen(*args, **kwargs):
        assert getattr(launching, "allowed", False), "start_command bypassed launch_command_session"
        return original_popen(*args, **kwargs)

    def tracked_thread(*args, **kwargs):
        worker = original_thread(*args, **kwargs)
        workers.append(worker)
        return worker

    def recover(_cls, directory, token):
        recoveries.append((Path(directory).resolve(), token))
        return original_recover(directory, token)

    def write_meta(job_id, payload):
        if payload.get("status") in {"completed", "failed", "cancelled"}:
            available = []

            def probe_lock():
                acquired = state_lock.acquire(blocking=False)
                available.append(acquired)
                if acquired:
                    state_lock.release()

            probe = original_thread(target=probe_lock)
            probe.start()
            probe.join(timeout=2)
            terminal_writes.append((dict(payload), available == [False]))
        return original_write(job_id, payload)

    def forbid_raw_signal(*_args, **_kwargs):
        pytest.fail("Background cancellation bypassed the native owned supervisor")

    monkeypatch.setattr(managed, "launch_command_session", tracked_launch)
    if getattr(variant, "launch_command_session", None) is launch:
        monkeypatch.setattr(variant, "launch_command_session", tracked_launch)
    monkeypatch.setattr(variant.subprocess, "Popen", owned_popen)
    monkeypatch.setattr(variant.subprocess, "run", forbid_raw_signal)
    monkeypatch.setattr(variant.os, "kill", forbid_raw_signal)
    monkeypatch.setattr(variant.os, "killpg", forbid_raw_signal, raising=False)
    monkeypatch.setattr(variant.threading, "Thread", tracked_thread)
    monkeypatch.setattr(managed.ManagedServerProcess, "recover", classmethod(recover))
    monkeypatch.setattr(manager, "_write_meta", write_meta)
    script = tmp_path / "disposable_sleep.py"
    script.write_text("import time\nprint('READY', flush=True)\ntime.sleep(30)\n", encoding="utf-8")

    try:
        started = manager.start_command([sys.executable, "-B", str(script)], workspace=tmp_path,
                                        label="model-free ownership fixture", wait_seconds=0.1)
        assert len(sessions) == 1
        session = sessions[0]
        assert started["status"] == "running"
        assert started["process_owner"] == session.token
        assert started["process_identity"] == session.receipt["identity"]
        assert started["pid"] == session.pid
        job_id = started["job_id"]
        job_directory = (manager.root / job_id).resolve()
        assert session.directory == job_directory

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "READY" in (job_directory / "job.log").read_text():
                break
            time.sleep(.05)
        else:
            pytest.fail("The active-command cancellation fixture never emitted READY")

        outcome = manager.cancel_job(job_id)

        assert recoveries == [(job_directory, session.token)]
        assert outcome["cancelled"] is True
        assert outcome["actual_wait"] is True
        ended = session.exit_receipt()
        assert ended is not None and ended["actual_wait"] is True
        assert ended["token"] == session.token
        assert ended["identity"] == started["process_identity"]
        assert ended["reason"] == "requested_stop"
        assert session.supervisor_waited is True
        assert session.poll() == ended["returncode"]
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive(), "Owned background watcher did not settle"
        saved = manager._read_meta(job_id)
        assert saved["status"] == "cancelled"
        assert saved["actual_wait"] is True
        assert saved["returncode"] == ended["returncode"]
        assert saved.get("finished_at")
        assert saved["process_owner"] == session.token
        assert saved["process_identity"] == started["process_identity"]
        assert terminal_writes
        assert all(locked for _payload, locked in terminal_writes), "Terminal update escaped STATE_LOCK"
        assert all(payload["actual_wait"] is True for payload, _locked in terminal_writes)
    finally:
        for session in sessions:
            cleanup = session.stop()
            assert cleanup["stopped"] is True and cleanup["actual_wait"] is True, cleanup
        for worker in workers:
            worker.join(timeout=5)
            assert not worker.is_alive(), "Fixture left an owned worker thread running"
