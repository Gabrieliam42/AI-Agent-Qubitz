"""Model-free storage, cancellation, wait and real Windows dummy-server regressions."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import disposable_followup_matrix as matrix
from evaluation.campaign_observation import read_json
from evaluation.complex_smoke_runtime import _exclusive_json

OWNER = "c" * 32


@pytest.mark.parametrize("separate", [False, True])
def test_matrix_private_storage_includes_composed_journals(tmp_path, monkeypatch, separate):
    from runtime.conversations import ConversationJournal

    class Memory:
        def __init__(self, workspace):
            self.workspace = workspace
            workspace.mkdir(parents=True, exist_ok=True)

    # Reproduce a journal class captured in a composed GUI's globals, not just its module.
    class EmbeddedJournal(ConversationJournal):
        pass

    namespace = {"ConversationJournal": EmbeddedJournal}
    exec("def __init__(self):\n self.journal = ConversationJournal(self.workspace, reserve_bytes=0)\n", namespace)  # noqa: S102 - test-owned composed class
    gui_type = type("ComposedGUI", (), {"__init__": namespace["__init__"]})
    base = SimpleNamespace(MemoryStore=Memory, QubitzGUI=gui_type)
    for cls in [ConversationJournal, EmbeddedJournal]:
        monkeypatch.setattr(cls, "__init__", cls.__init__)
    runtime = tmp_path / "runtime"
    output = tmp_path / "attempt"
    matrix.install_private_memory(base, output, separate_workspaces=separate)
    stores = [base.MemoryStore(tmp_path / name) for name in ["workspace_a", "workspace_b", "workspace_a"]]
    assert stores[0].workspace == stores[2].workspace != stores[1].workspace
    assert all(store.workspace.is_relative_to(output / "private_memory") for store in stores)
    for cls in [ConversationJournal, EmbeddedJournal]:
        journal = cls(runtime, reserve_bytes=0)
        try:
            assert journal.record("system", "fixture-only journal")
            assert "fixture-only journal" in journal.snapshot()
            assert journal.path.is_relative_to(output / "private_transcripts")
        finally:
            journal.close(timeout=5)
    assert not runtime.exists()


def launch_receipt(directory):
    _exclusive_json(directory / "launch.json", {"owner": OWNER})


class Root:
    def __init__(self):
        self.callbacks = {}

    def after(self, _delay, callback):
        self.callbacks[len(self.callbacks)] = callback
        return len(self.callbacks) - 1

    def after_cancel(self, identifier):
        self.callbacks.pop(identifier, None)

    def winfo_exists(self):
        return True


def test_cooperative_cancel_is_once_and_before_shutdown(tmp_path):
    launch_receipt(tmp_path)
    calls = []
    gui = SimpleNamespace(root=Root(), busy=True)
    runner = SimpleNamespace(request_cancel=lambda: calls.append("cancel"))
    observer = matrix.Observer(tmp_path)
    stop = matrix.CooperativeWorkerStop(tmp_path, OWNER, runner, gui, observer)
    try:
        _exclusive_json(tmp_path / "worker_stop_request.json", {"owner": OWNER, "reason": "coordinator_stop"})
        for _ in range(2):
            with pytest.raises(matrix.WorkerInterrupted):
                stop.checkpoint()
        assert calls == ["cancel"]
        assert read_json(tmp_path / "worker_stop_ack.json")["cancellation_requested"]
    finally:
        stop.close()


def test_wrong_owner_never_cancels_runner(tmp_path):
    launch_receipt(tmp_path)
    _exclusive_json(tmp_path / "worker_stop_request.json", {"owner": "d" * 32, "reason": "coordinator_stop"})
    runner = SimpleNamespace(request_cancel=lambda: pytest.fail("Unowned worker cancellation"))
    stop = matrix.CooperativeWorkerStop(tmp_path, OWNER, runner, SimpleNamespace(root=Root()), matrix.Observer(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="cancellation failed"):
            stop.checkpoint()
        assert not (tmp_path / "worker_stop_ack.json").exists()
    finally:
        stop.close()


def test_partially_written_stop_request_is_not_authority(tmp_path):
    launch_receipt(tmp_path)
    (tmp_path / "worker_stop_request.json").write_bytes(b'{"owner":')
    assert matrix.worker_stop_request(tmp_path, OWNER) is None


def test_coordinator_refuses_wrong_launch_owner_before_request(tmp_path):
    launch_receipt(tmp_path)
    with pytest.raises(RuntimeError, match="different launch owner"):
        matrix.wait_for_worker_exit(SimpleNamespace(poll=lambda: None), tmp_path, "d" * 32, "coordinator_stop")
    assert not (tmp_path / "worker_stop_request.json").exists()


def test_recording_failure_does_not_skip_cancel(tmp_path):
    launch_receipt(tmp_path)
    _exclusive_json(tmp_path / "worker_stop_request.json", {"owner": OWNER, "reason": "coordinator_stop"})
    calls = []
    observer = SimpleNamespace(emit=lambda *a, **k: (_ for _ in ()).throw(matrix.EvidenceRecordingError("fixture")))
    stop = matrix.CooperativeWorkerStop(tmp_path, OWNER, SimpleNamespace(request_cancel=lambda: calls.append(True)),
                                        SimpleNamespace(root=Root()), observer)
    try:
        with pytest.raises(matrix.WorkerInterrupted):
            stop.checkpoint()
        assert calls == [True]
    finally:
        stop.close()


@pytest.mark.parametrize("exits", [True, False])
def test_worker_wait_preserves_uncertainty_without_signals_or_retries(tmp_path, exits):
    launch_receipt(tmp_path)

    class Process:
        pid = 123

        def poll(self):
            return None

        def wait(self, timeout):
            request = read_json(tmp_path / "worker_stop_request.json")
            assert request["owner"] == OWNER and request["reason"] == "coordinator_stop"
            if not exits:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 4

    if exits:
        receipt = matrix.wait_for_worker_exit(Process(), tmp_path, OWNER, "coordinator_stop", timeout=0.1)
        assert receipt["actual_wait"] and receipt["exit_code"] == 4
    else:
        with pytest.raises(RuntimeError, match="no force-stop or retry"):
            matrix.wait_for_worker_exit(Process(), tmp_path, OWNER, "coordinator_stop", timeout=0.1)
        assert read_json(tmp_path / "worker_wait.json")["actual_wait"] is False
    before = (tmp_path / "worker_stop_request.json").read_bytes()
    with pytest.raises(FileExistsError):
        matrix.wait_for_worker_exit(Process(), tmp_path, OWNER, "coordinator_stop", timeout=0.1)
    assert (tmp_path / "worker_stop_request.json").read_bytes() == before


def test_busy_task_is_drained_before_runtime_reset():
    calls = []
    gui = SimpleNamespace(busy=True, root=Root())
    gui.root.update = lambda: (calls.append("drain"), setattr(gui, "busy", False))
    runner = SimpleNamespace(request_cancel=lambda: calls.append("cancel"), reset_runtime=lambda: calls.append("reset"))
    matrix.wait_for_task_idle(runner, gui, timeout=1)
    matrix.shutdown_runtime_responsively(runner, None, None)
    assert calls == ["cancel", "drain", "reset"]


def test_unresolved_busy_task_never_resets_runtime():
    gui = SimpleNamespace(busy=True, root=Root())
    gui.root.update = lambda: None
    runner = SimpleNamespace(request_cancel=lambda: None)
    with pytest.raises(TimeoutError, match="shutdown is unverified"):
        matrix.wait_for_task_idle(runner, gui, timeout=0)


def test_child_installs_private_storage_before_gui_and_waits_before_cleanup():
    source = Path(matrix.__file__).read_text()
    tree = ast.parse(source)
    child = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "child")
    text = ast.get_source_segment(source, child)
    assert text.index("install_private_memory(") < text.index("base.QubitzGUI(")
    assert text.index("wait_for_task_idle(") < text.index("shutdown_runtime_responsively(")
    attempt = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run_attempt")
    text = ast.get_source_segment(source, attempt)
    assert text.index("wait_for_worker_exit(") < text.index("subprocess.run(cleanup_command")


def test_shared_storage_drift_holds_coordinator_before_next_attempt(tmp_path, monkeypatch):
    import followup_campaign_control as control
    import smoke_complex_tasks
    import smoke_create_calculator as smoke

    inventory = {"conversations/existing.txt": "original"}
    monkeypatch.setattr(smoke, "persistent_inventory", lambda: dict(inventory))
    monkeypatch.setattr(matrix, "validate_output", lambda p: p)
    monkeypatch.setattr(matrix, "variants", lambda *_: ["fixture.py"])
    case = SimpleNamespace(name="fixture", turns=(None,))
    monkeypatch.setattr(matrix, "selected_scenarios", lambda *_: [case, SimpleNamespace(name="next", turns=(None,))])
    monkeypatch.setattr(matrix, "asdict", lambda _: {})
    monkeypatch.setattr(matrix, "freeze_sources", dict)
    monkeypatch.setattr(matrix, "coverage_plan", lambda *_: {})
    monkeypatch.setattr(matrix, "grouped_results", lambda *_: {})
    monkeypatch.setattr(smoke_complex_tasks, "protected_source_manifest", lambda *_: {})
    monkeypatch.setattr(control, "process_identity", lambda _: {
        "pid": 1, "creation_ticks": "100", "executable": "python.exe", "command_line": "fixture"})
    calls = []

    def attempt(*args):
        calls.append(args)
        inventory["conversations/leaked.txt"] = "unexpected"
        return {"variant": "fixture.py", "scenario": "fixture", "status": "failed", "turns": [], "issues": []}

    monkeypatch.setattr(matrix, "run_attempt", attempt)
    args = SimpleNamespace(output=tmp_path / "output", variants=None, tier="all", scenarios=None,
                           repeats=1, gui=True, timeout=1, startup_allowance=0, distro="Ubuntu",
                           suite="original-v1", resume=False)
    assert matrix._coordinate(args) == 2
    assert len(calls) == 1
    summary = read_json(args.output / "summary.json")
    assert summary["status"] == "stopped_infrastructure"
    assert summary["persistent_inventory_findings"] == [
        {"path": "conversations/leaked.txt", "expected": None, "observed": "unexpected"}]


def test_stop_wait_does_not_treat_incomplete_metadata_as_absence(tmp_path, monkeypatch):
    import disposable_followup_matrix as cli_matrix
    import followup_campaign_control as control

    identity = {"pid": 123, "creation_ticks": "100", "executable": "python.exe", "command_line": "fixture"}
    (tmp_path / matrix.MARKER).write_text("developer-only follow-up matrix\n")
    matrix.save_json(tmp_path / "summary.json", {"status": "running"})
    matrix.save_json(tmp_path / "coordinator_identity.json", identity)
    samples = iter([identity, {"pid": 123, "creation_ticks": None}, None])
    calls = []

    def observe(_pid):
        value = next(samples)
        calls.append(value)
        return value

    monkeypatch.setattr(cli_matrix, "validate_output", lambda p: p)
    monkeypatch.setattr(control, "process_identity", observe)
    monkeypatch.setattr(control.time, "sleep", lambda _: matrix.save_json(tmp_path / "summary.json", {"status": "paused"}))
    assert control.stop_campaign(tmp_path, wait_seconds=1)["coordinator_stopped"]
    assert len(calls) == 3


def test_existing_stop_request_is_never_overwritten(tmp_path, monkeypatch):
    import disposable_followup_matrix as cli_matrix
    import followup_campaign_control as control

    identity = {"pid": 123, "creation_ticks": "100", "executable": "python.exe", "command_line": "fixture"}
    (tmp_path / matrix.MARKER).write_text("developer-only follow-up matrix\n")
    matrix.save_json(tmp_path / "summary.json", {"status": "running"})
    matrix.save_json(tmp_path / "coordinator_identity.json", identity)
    (tmp_path / "stop_request.json").write_bytes(b"uncertain previous request\n")
    monkeypatch.setattr(cli_matrix, "validate_output", lambda p: p)
    monkeypatch.setattr(control, "process_identity", lambda _: identity)
    with pytest.raises(FileExistsError):
        control.stop_campaign(tmp_path)
    assert (tmp_path / "stop_request.json").read_bytes() == b"uncertain previous request\n"


@pytest.mark.skipif(os.name != "nt", reason="Real Windows process identity/cleanup fixture")
@pytest.mark.parametrize("mode", ["normal", "cancel"])
def test_real_windows_worker_shuts_down_dummy_server_before_exit(tmp_path, mode):
    from evaluation.followup_campaign_control import process_identity, same_identity

    launch_receipt(tmp_path)
    fixture = matrix.ROOT / "evaluation/matrix_shutdown_fixture.py"
    server = subprocess.Popen([sys.executable, "-B", str(fixture), "server", str(tmp_path), OWNER],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    worker = None
    try:
        identity = process_identity(server.pid)
        assert same_identity(identity, identity)
        _exclusive_json(tmp_path / "server_identity.json", identity)
        worker = subprocess.Popen([sys.executable, "-B", str(fixture), "worker", str(tmp_path), OWNER,
                                   "--mode", mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 25
        while not (tmp_path / "fixture_ready.json").exists():
            assert worker.poll() is None, worker.communicate()
            assert time.monotonic() < deadline
            time.sleep(0.05)
        waited = matrix.wait_for_worker_exit(worker, tmp_path, OWNER,
                                             "coordinator_stop" if mode == "cancel" else None, timeout=50)
        stdout, stderr = worker.communicate()
        assert waited["actual_wait"] and waited["exit_code"] == 0, (stdout, stderr)
        assert server.wait(timeout=10) == 0
        assert process_identity(identity["pid"]) is None
        calls = read_json(tmp_path / "fixture_finished.json")["calls"]
        assert calls == (["cancel", "shutdown"] if mode == "cancel" else ["shutdown"])
    finally:
        if not (tmp_path / "server_stop.json").exists():
            _exclusive_json(tmp_path / "server_stop.json", {"owner": OWNER})
        if worker is not None:
            worker.wait(timeout=75)
            worker.communicate()
        server.wait(timeout=75)
        server.communicate()
