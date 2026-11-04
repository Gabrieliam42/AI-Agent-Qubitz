"""Model-free worker admission, supervision and persistent-storage contracts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from evaluation import complex_smoke_runtime as ownership
from evaluation import smoke_create_calculator as smoke

TOKEN = "a" * 32
WORKER_CODE = """
import sys, time
from pathlib import Path
from evaluation import complex_smoke_runtime as ownership
output, token, timeout = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
ownership.record_worker_identity(output, token)
ownership.wait_for_worker_admission(output, token, timeout)
(output / 'admitted.txt').write_text('harmless worker, no model imports')
time.sleep(3)
"""


@pytest.mark.parametrize("stdout,available", [
    ("0, GPU-a, 40, 25, 0, 100, 24576\n1, GPU-b, 41, 26, 1, 200, 24576\n", True),
    ("", False),
    ("0, GPU-a, 40, 25, 0, 100, 24576\n0, GPU-b, 41, 26, 1, 200, 24576", False),
    ("0, GPU-a, 40, 25, 0, 100, 24576\n1, GPU-a, 41, 26, 1, 200, 24576", False),
    ("0, GPU-a, [Not Supported], 25, 0, 100, 24576", False),
])
def test_all_gpu_observation_is_complete_or_explicitly_unknown(monkeypatch, stdout, available):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=stdout))
    result = smoke.all_gpu_snapshot()
    assert result["available"] is available
    if available:
        assert [gpu["index"] for gpu in result["gpus"]] == [0, 1]
    else:
        assert result["gpus"] == [] and result["error"]


def test_gpu_command_failure_is_not_a_safe_sample(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=9, stderr="fixture failure"))
    assert smoke.all_gpu_snapshot()["error"] == "fixture failure"


def test_fresh_output_refuses_existing_evidence_without_rewriting_it(tmp_path, monkeypatch):
    summary = tmp_path / "summary.json"
    summary.write_bytes(b'{"status":"held_after_failure"}\n')
    before = summary.read_bytes(), summary.stat().st_mtime_ns
    monkeypatch.setattr(sys, "argv", ["smoke", "--output", str(tmp_path)])
    monkeypatch.setattr(smoke, "coordinator", lambda *_: pytest.fail("Reused prior evidence"))
    with pytest.raises(RuntimeError, match="new or empty"):
        smoke.main()
    assert (summary.read_bytes(), summary.stat().st_mtime_ns) == before


def test_fresh_output_accepts_only_new_or_empty_directories(tmp_path):
    smoke.require_fresh_output(tmp_path)
    smoke.require_fresh_output(tmp_path / "new")
    target = tmp_path / "file"
    target.write_text("keep")
    with pytest.raises(RuntimeError, match="new or empty"):
        smoke.require_fresh_output(target)


class FiniteWorker:
    pid = 999
    returncode = None

    def __init__(self):
        self.waits = 0

    def poll(self):
        return self.returncode

    def wait(self):
        self.waits += 1
        self.returncode = 143
        return self.returncode


@pytest.mark.parametrize("failure", [RuntimeError("fixture inventory failure"), KeyboardInterrupt()])
def test_interrupted_supervision_stops_once_and_reaps_original(tmp_path, monkeypatch, failure):
    worker = FiniteWorker()
    stops = []
    monkeypatch.setattr(smoke, "admit_wsl_worker", lambda *_: None)

    def broken_inventory():
        raise failure

    monkeypatch.setattr(smoke, "model_process_snapshot", broken_inventory)
    monkeypatch.setattr(smoke, "stop_wsl_worker", lambda *a: stops.append(a) or {
        "exit_code": None, "status": "uncertain_do_not_retry"})
    with pytest.raises(type(failure)):
        smoke.supervise_worker(worker, tmp_path, TOKEN, time.monotonic(), 0)
    assert len(stops) == worker.waits == 1
    assert json.loads((tmp_path / "worker_wait.json").read_text())["actual_wait"] is True


def test_timeout_never_repeats_uncertain_stop_and_keeps_wait(tmp_path, monkeypatch):
    worker = FiniteWorker()
    stops = []
    monkeypatch.setattr(smoke, "admit_wsl_worker", lambda *_: None)
    monkeypatch.setattr(smoke, "model_process_snapshot", lambda: ([], []))
    monkeypatch.setattr(smoke, "all_gpu_snapshot", lambda: {"available": False, "error": "unavailable", "gpus": []})
    monkeypatch.setattr(smoke, "stop_wsl_worker", lambda *a: stops.append(a) or {"exit_code": None})
    result = smoke.supervise_worker(worker, tmp_path, TOKEN, time.monotonic() - 1, 0)
    assert result["timed_out"] and result["gpu_observation_errors"] == ["unavailable"]
    assert len(stops) == worker.waits == 1


def test_failed_receipt_write_still_reaps_without_repeating_stop(tmp_path, monkeypatch):
    worker = FiniteWorker()
    stops = []
    monkeypatch.setattr(smoke, "admit_wsl_worker", lambda *_: (_ for _ in ()).throw(RuntimeError("fixture")))
    monkeypatch.setattr(smoke, "stop_wsl_worker", lambda *a: stops.append(a) or {"exit_code": None})
    monkeypatch.setattr(smoke, "save_json", lambda *a: (_ for _ in ()).throw(OSError("fixture disk failure")))
    with pytest.raises(OSError, match="fixture disk failure"):
        smoke.supervise_worker(worker, tmp_path, TOKEN, time.monotonic(), 0)
    assert len(stops) == worker.waits == 1


def test_admission_cannot_authorize_an_exited_worker(tmp_path):
    worker = FiniteWorker()
    worker.returncode = 1
    with pytest.raises(RuntimeError, match="exited before admission"):
        smoke.admit_wsl_worker(tmp_path, TOKEN, worker, timeout=0.1)
    assert not (tmp_path / "launch_admission.json").exists()


def test_cleanup_observer_never_signals_an_observed_server(monkeypatch):
    monkeypatch.setattr(smoke, "model_process_snapshot", lambda: ([], []))
    monkeypatch.setattr(smoke, "stop_verified_server", lambda *_: pytest.fail("Observer signaled a server"))
    monkeypatch.setattr(smoke, "port_free", lambda: True)
    monkeypatch.setattr(smoke, "require_idle", lambda: None)
    result = smoke.wait_for_owned_cleanup([], timeout=6)
    assert result["verified_idle"]


def test_private_storage_preserves_runtime_paths(tmp_path, monkeypatch):
    from runtime.conversations import ConversationJournal

    class Memory:
        def __init__(self, workspace):
            self.workspace = workspace
            workspace.mkdir(parents=True, exist_ok=True)

    base = SimpleNamespace(MemoryStore=Memory)
    monkeypatch.setattr(ConversationJournal, "__init__", ConversationJournal.__init__)
    output = tmp_path / "run"
    ownership.install_private_storage(base, output)
    workspace = tmp_path / "runtime"
    store = base.MemoryStore(workspace)
    assert store.workspace.is_relative_to(output / "private_memory")
    journal = ConversationJournal(workspace, reserve_bytes=0)
    try:
        assert journal.record("system", "private fixture")
        assert "private fixture" in journal.snapshot()
        assert journal.path.is_relative_to(output / "private_transcripts")
    finally:
        journal.close(timeout=5)
    assert not workspace.exists()


def launch_harmless_worker(run, token, timeout):
    if os.name == "nt":
        command = smoke.wsl_worker_command([
            smoke.wsl_path(smoke.ROOT / ".venv/bin/python"), "-B", "-c", WORKER_CODE,
            smoke.wsl_path(run), token, str(timeout),
        ], token)
        return subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return subprocess.Popen([sys.executable, "-B", "-c", WORKER_CODE, str(run), token, str(timeout)],
                            cwd=smoke.ROOT, env={**os.environ, ownership.OWNER_ENV: token},
                            start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


@pytest.mark.parametrize("action", ["admit_stop", "missing_admission", "stale_admission", "stale_stop"])
def test_real_owned_worker_lifecycle(tmp_path, action):
    if os.name == "nt" and action == "stale_stop":
        pytest.skip("Direct Linux identity mismatch is covered by the WSL suite")
    token = uuid.uuid4().hex
    worker = launch_harmless_worker(tmp_path, token, 1.5)
    receipt = tmp_path / "worker_identity.json"
    try:
        deadline = time.monotonic() + 20
        group_receipt = tmp_path / "wsl-process-group.pid"
        while time.monotonic() < deadline:
            try:
                identity = json.loads(receipt.read_text())
                group = int(group_receipt.read_text())
            except (FileNotFoundError, ValueError):
                time.sleep(0.02)
                continue
            break
        else:
            pytest.fail("Worker did not publish complete identity and process-group receipts")
        assert identity["pid"] == identity["group"] == identity["session"]
        assert group == identity["pid"]
        assert identity["executable"] and identity["command_line_base64"] and identity["start_ticks"]
        if action == "missing_admission":
            pass
        elif action == "stale_admission":
            wrong = {**identity, "start_ticks": identity["start_ticks"] - 1}
            ownership._exclusive_json(tmp_path / "launch_admission.json", {"owner": token, "identity": wrong})
        else:
            smoke.admit_wsl_worker(tmp_path, token, worker)
            if action == "stale_stop":
                stale = tmp_path / "stale.json"
                ownership._exclusive_json(stale, {**identity, "start_ticks": identity["start_ticks"] - 1})
                with pytest.raises(RuntimeError, match="changed; cleanup refused"):
                    ownership.stop_recorded_worker(stale, token, tmp_path / "refused")
                assert worker.poll() is None and not (tmp_path / "refused").exists()
            if os.name == "nt":
                result = smoke.stop_wsl_worker(tmp_path, token)
                assert result["exit_code"] == 0, result
                assert not json.loads(result["stdout"])["survivors"]
            else:
                result = ownership.stop_recorded_worker(receipt, token, tmp_path / "stop")
                assert not result["survivors"] and result["signals"]
                worker.wait(timeout=10)
                with pytest.raises(FileExistsError):
                    ownership.stop_recorded_worker(receipt, token, tmp_path / "stop")
    finally:
        # The finite fixture exits by itself even when a guard refuses cleanup.
        worker.wait(timeout=20)
        stdout, stderr = worker.communicate()
    if action in {"missing_admission", "stale_admission"}:
        assert worker.returncode != 0 and "model loading refused" in stderr, (stdout, stderr)
        assert not (tmp_path / "admitted.txt").exists()


@pytest.mark.skipif(os.name == "nt", reason="Qubitz Tkinter runtime uses the project WSL environment")
def test_real_gui_uses_private_memory_and_journal_before_config_build(tmp_path, monkeypatch):
    import socket

    import tmp_smoke_variant as variant_smoke

    before = smoke.persistent_inventory()
    module, _ = variant_smoke._load_module("AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py")
    base = module._APP.base
    monkeypatch.setattr(base, "MemoryStore", base.MemoryStore)
    for journal in ownership.storage_journal_types(base):
        monkeypatch.setattr(journal, "__init__", journal.__init__)
    ownership.install_private_storage(base, tmp_path / "run")

    def deny(*_args, **_kwargs):
        pytest.fail("Guarded UI check attempted model/network activity")

    monkeypatch.setattr(base.AgentRunner, "_get_llm", deny)
    monkeypatch.setattr(base.LlamaCppClient, "ensure_model", deny)
    monkeypatch.setattr(base.LlamaCppClient, "chat", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)
    for name in ("_schedule_selected_model_acquisition", "_maybe_start_background_model_prewarm"):
        if hasattr(base.QubitzGUI, name):
            monkeypatch.setattr(base.QubitzGUI, name, lambda *_args: None)
    config, base = variant_smoke._build_wrapped_config(module, smoke.ROOT)
    assert config.runtime_workspace == smoke.ROOT and config.workspace == smoke.ROOT
    gui = base.QubitzGUI(config)
    try:
        variant_smoke._attach_event_capture(gui)
        variant_smoke._install_messagebox_capture(gui)
        variant_smoke._present_gui(gui)
        variant_smoke._drive(gui.root, lambda: False, 0.25)
        assert gui.agent.memory.memory_dir.is_relative_to(tmp_path / "run/private_memory")
        assert gui.conversation_journal.path.is_relative_to(tmp_path / "run/private_transcripts")
        gui.agent.memory.add_note("private guarded GUI fixture")
        gui.conversation_journal.record("system", "private guarded GUI fixture")
        assert "private guarded GUI fixture" in gui.conversation_journal.snapshot()
        assert not getattr(gui, "_smoke_messageboxes", [])
    finally:
        try:
            gui.agent.reset_runtime()
        finally:
            try:
                gui.conversation_journal.close(timeout=5)
            finally:
                geometry = gui.root.winfo_geometry()
                mapped = bool(gui.root.winfo_ismapped())
                gui.root.destroy()
                smoke.save_json(tmp_path / "gui_review.json", {
                    "geometry": geometry, "mapped": mapped,
                    "events": getattr(gui, "_smoke_events", []),
                    "messageboxes": getattr(gui, "_smoke_messageboxes", []),
                    "diagnostics": variant_smoke._collect_gui_diagnostics(gui),
                    "storage_before": before, "storage_after": smoke.persistent_inventory(),
                    "shared_storage_unchanged": smoke.persistent_inventory() == before,
                })
    assert smoke.persistent_inventory() == before
