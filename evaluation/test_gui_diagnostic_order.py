"""CPU-only diagnostic ordering and ownership safety regressions."""
from __future__ import annotations

import ast
import json
import queue
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import tmp_smoke_variant as smoke
from evaluation import test_followup_state_receipts as receipts
from runtime import gui_diagnostics, read_variant_source

variant = receipts.variant


def diagnostics(tmp_path, events):
    log = tmp_path / "gui.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return smoke._collect_gui_diagnostics(SimpleNamespace(
        _qubitz_gui_log_path=log, _qubitz_flush_gui_log=lambda **_: True,
    ))


@pytest.mark.parametrize("names,timestamps,unresolved,recovered", [
    (["gui_heartbeat_stalled", "gui_heartbeat_recovered"], ["20", "10"], 0, 1),
    (["gui_heartbeat_recovered", "gui_heartbeat_stalled"], ["20", "10"], 1, 0),
    (["gui_heartbeat_recovered", "gui_heartbeat_stalled"], ["10", "10"], 1, 0),
])
def test_wall_clocks_never_reorder_legacy_events(tmp_path, names, timestamps, unresolved, recovered):
    events = [{"event": name, "timestamp": stamp} for name, stamp in zip(names, timestamps, strict=True)]
    result = diagnostics(tmp_path, events)
    assert result["events"] == events
    assert result["unresolved_stall_count"] == unresolved
    assert result["recovered_stall_count"] == recovered
    assert ("gui_heartbeat_stalled" in result["behavior_issues"]) == bool(unresolved)


def ordered_event(name, sequence, session="session-a", monotonic=None, **details):
    return {"event": name, "sequence": sequence, "gui_log_session": session,
            "monotonic_seconds": float(sequence) if monotonic is None else monotonic,
            "timestamp": str(100 - sequence), **details}


def test_serialized_sequence_is_verified_without_changing_raw_order(tmp_path):
    events = [ordered_event("gui_heartbeat_stalled", 1),
              ordered_event("gui_event", 2, kind="answer"),
              ordered_event("gui_heartbeat_stalled", 3)]
    result = diagnostics(tmp_path, events)
    assert result["event_order"] == "serialized_fifo"
    assert result["events"] == events
    assert result["ordering_errors"] == []
    assert result["recovered_stall_count"] == result["unresolved_stall_count"] == 1


@pytest.mark.parametrize("sequence,monotonic", [(1, 2.0), (3, 2.0), (2, 0.0), (True, 2.0), (2, float("nan"))])
def test_invalid_ordering_metadata_is_not_a_clean_pass(tmp_path, sequence, monotonic):
    events = [ordered_event("gui_heartbeat_stalled", 1),
              ordered_event("gui_heartbeat_recovered", sequence, monotonic=monotonic)]
    result = diagnostics(tmp_path, events)
    assert "gui_log_order_error" in result["behavior_issues"]
    assert result["ordering_errors"]
    assert json.dumps(result["events"]) == json.dumps(events)


def test_recovery_cannot_clear_another_logger_session(tmp_path):
    events = [ordered_event("gui_heartbeat_stalled", 1),
              ordered_event("gui_heartbeat_recovered", 1, session="session-b")]
    result = diagnostics(tmp_path, events)
    assert result["unresolved_stall_count"] == 1
    assert result["recovered_stall_count"] == 0


@pytest.fixture(scope="module")
def logger_code(variant):
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    gui = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "HardenedGUI")
    initializer = next(node for node in gui.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    names = {"enqueue_gui_log", "write_gui_log", "flush_gui_log", "stop_gui_diagnostics"}
    nested = [node for node in initializer.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nested} == names
    return compile(ast.Module(body=nested, type_ignores=[]), "<actual-shared-gui-logger>", "exec")


@pytest.fixture
def logger(logger_code, tmp_path, variant):
    from contextlib import suppress

    owner = SimpleNamespace(
        root=object(), _qubitz_gui_log_path=tmp_path / "gui.jsonl",
        _qubitz_gui_log_queue=queue.Queue(maxsize=4096), _qubitz_gui_log_lock=threading.Lock(),
        _qubitz_gui_log_sequence=0, _qubitz_gui_log_session=uuid.uuid4().hex,
        _qubitz_gui_log_error="", _qubitz_gui_log_dropped=0, _qubitz_gui_log_flushed_bytes=None,
        _qubitz_gui_log_closed=threading.Event(), _qubitz_gui_watchdog_stop=threading.Event(),
    )
    scope = {"self": owner, "base": SimpleNamespace(queue=queue), "json": json,
             "threading": threading, "time": time, "Path": Path, "suppress": suppress,
             "os": variant.os, "__file__": variant.__file__, "Any": object}
    scope.update({name: value for name, value in vars(gui_diagnostics).items()
                  if name.startswith("_qubitz_gui_log_")})
    exec(logger_code, scope)  # noqa: S102 - execute only the project's trusted logger AST.
    owner._qubitz_flush_gui_log = scope["flush_gui_log"]
    owner._qubitz_gui_log_writer = threading.Thread(target=scope["write_gui_log"], daemon=True)
    yield owner, scope
    if owner._qubitz_gui_log_writer.is_alive():
        owner._qubitz_gui_watchdog_stop.set()
        try:
            owner._qubitz_gui_log_queue.put(None, timeout=2)
        except queue.Full:
            pass
        owner._qubitz_gui_log_writer.join(2)
        assert not owner._qubitz_gui_log_writer.is_alive()


def test_concurrent_loggers_serialize_sequences_and_flush_boundaries(logger):
    owner, scope = logger
    owner._qubitz_gui_log_writer.start()
    start = threading.Barrier(8)

    def produce():
        start.wait(timeout=5)
        for _ in range(25):
            scope["enqueue_gui_log"]("fixture")

    workers = [threading.Thread(target=produce) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert scope["flush_gui_log"]()
    result = smoke._collect_gui_diagnostics(owner)
    events = result["events"]
    assert len(events) == 200
    assert [event["sequence"] for event in events] == list(range(1, 201))
    assert {event["gui_log_session"] for event in events} == {owner._qubitz_gui_log_session}
    assert [event["monotonic_seconds"] for event in events] == sorted(event["monotonic_seconds"] for event in events)
    assert all(event["timestamp_utc"].endswith("Z") for event in events)
    assert result["behavior_issues"] == []
    assert owner._qubitz_gui_log_flushed_bytes == owner._qubitz_gui_log_path.stat().st_size
    assert owner._qubitz_gui_log_writer.is_alive()


def test_details_cannot_replace_trusted_logger_metadata(logger):
    owner, scope = logger
    scope["enqueue_gui_log"]("fixture", sequence=999, gui_log_session="forged", pid=-1,
                             event="forged", variant="forged", monotonic_seconds=-1, timestamp_utc="forged")
    item = owner._qubitz_gui_log_queue.get_nowait()
    assert item["event"] == "fixture" and item["sequence"] == 1
    assert item["gui_log_session"] == owner._qubitz_gui_log_session
    assert item["variant"] == Path(scope["__file__"]).name
    assert item["pid"] > 0 and item["monotonic_seconds"] >= 0
    assert item["timestamp_utc"].endswith("Z")


def test_full_queue_shutdown_drains_and_does_not_strand_writer(logger):
    owner, scope = logger
    owner._qubitz_gui_log_queue = queue.Queue(maxsize=1)
    scope["enqueue_gui_log"]("fixture")
    scope["stop_gui_diagnostics"](SimpleNamespace(widget=object()))
    assert not owner._qubitz_gui_watchdog_stop.is_set()
    scope["stop_gui_diagnostics"](SimpleNamespace(widget=owner.root))
    scope["enqueue_gui_log"]("after_close")
    owner._qubitz_gui_log_writer.start()
    owner._qubitz_gui_log_writer.join(2)
    assert not owner._qubitz_gui_log_writer.is_alive()
    assert scope["flush_gui_log"]()
    assert [event["event"] for event in smoke._collect_gui_diagnostics(owner)["events"]] == ["fixture"]


def test_overflow_is_retained_as_an_error_not_hidden(logger):
    owner, scope = logger
    owner._qubitz_gui_log_queue = queue.Queue(maxsize=1)
    scope["enqueue_gui_log"]("first")
    scope["enqueue_gui_log"]("dropped")
    owner._qubitz_gui_log_writer.start()
    assert scope["flush_gui_log"]()
    scope["enqueue_gui_log"]("third")
    assert scope["flush_gui_log"]()
    result = smoke._collect_gui_diagnostics(owner)
    assert owner._qubitz_gui_log_dropped == 1
    assert [event["sequence"] for event in result["events"]] == [1, 3]
    assert {"gui_log_events_dropped", "gui_log_order_error"} <= set(result["behavior_issues"])


def test_event_arriving_at_empty_wait_boundary_is_drained(logger):
    owner, scope = logger

    class BoundaryQueue(queue.Queue):
        injected = False

        def get(self, *args, **kwargs):
            if not self.injected:
                self.injected = True
                scope["enqueue_gui_log"]("accepted_at_boundary")
                scope["stop_gui_diagnostics"](SimpleNamespace(widget=owner.root))
                raise queue.Empty
            return super().get(*args, **kwargs)

    owner._qubitz_gui_log_queue = BoundaryQueue(maxsize=1)
    owner._qubitz_gui_log_writer.start()
    owner._qubitz_gui_log_writer.join(2)
    assert not owner._qubitz_gui_log_writer.is_alive()
    assert scope["flush_gui_log"]()
    events = smoke._collect_gui_diagnostics(owner)["events"]
    assert [event["event"] for event in events] == ["accepted_at_boundary"]


def test_normal_gui_constructor_uses_private_session_log(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "HARNESS.enc").write_bytes((root / "HARNESS.enc").read_bytes())
    monkeypatch.setenv("QUBITZ_HARNESS_KEY", base.harness_key_bytes(root).decode("ascii"))
    monkeypatch.setattr(base.AgentRunner, "_get_llm", lambda *_: pytest.fail("CPU test attempted inference"))
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=tmp_path))
    try:
        gui.root.withdraw()
        assert gui._qubitz_gui_log_path.is_relative_to(tmp_path)
        assert gui._qubitz_gui_log_session in gui._qubitz_gui_log_path.name
        gui._qubitz_gui_log_event("fixture")
        assert gui._qubitz_flush_gui_log()
        events = smoke._collect_gui_diagnostics(gui)["events"]
        assert events[0]["event"] == "gui_started"
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()
        assert gui._qubitz_flush_gui_log()


def test_standalone_smoke_leaves_unowned_browsers_untouched(tmp_path, monkeypatch, capsys):
    calls, cleanup = [], []
    gui = SimpleNamespace(
        config=SimpleNamespace(workspace=tmp_path),
        agent=SimpleNamespace(runtime_workspace=tmp_path, reset_runtime=lambda: cleanup.append("runtime")),
        root=SimpleNamespace(destroy=lambda: cleanup.append("gui")), _smoke_events=[],
    )
    module = SimpleNamespace(QubitzGUI=lambda _: gui)
    monkeypatch.setattr(smoke.sys, "argv", ["smoke", "fixture.py"])
    monkeypatch.setattr(smoke, "TASK_PROMPT", "Fixture task.")
    monkeypatch.setattr(smoke, "_prepare_disposable_workspace", lambda _: (tmp_path, {"cleanup": "completed"}, lambda: None))
    monkeypatch.setattr(smoke, "_load_module", lambda _: (module, "fixture"))
    monkeypatch.setattr(smoke, "variant_path", lambda *_: tmp_path / "fixture.py")
    monkeypatch.setattr(smoke, "_build_base_config", lambda *_: None)
    for name in ("_attach_event_capture", "_install_messagebox_capture", "_present_gui"):
        monkeypatch.setattr(smoke, name, lambda *_: None)
    for name in ("_idle_before_first_prompt", "_background_prewarm_snapshot", "_workspace_manifest"):
        monkeypatch.setattr(smoke, name, lambda *_: {})
    monkeypatch.setattr(smoke, "_prepare_model_for_smoke", lambda *_: {"completed": False, "failure_class": "fixture_no_backend"})
    monkeypatch.setattr(smoke, "_analyze_question", lambda *_: {})
    monkeypatch.setattr(smoke, "_analyze_task", lambda *_: {})
    monkeypatch.setattr(smoke, "_collect_gui_diagnostics", lambda *_: {"behavior_issues": [], "warnings": []})
    monkeypatch.setattr(smoke.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    assert smoke.main() == 1
    assert json.loads(capsys.readouterr().out)["runner_outcome"] == "model_preflight_failure"
    assert cleanup == ["runtime", "gui"]
    assert calls == []
