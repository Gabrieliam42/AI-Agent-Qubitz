"""Owned-stream isolation, mutable versions and actual-wait integration; no models."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from evaluation import campaign_lifecycle as lifecycle
from evaluation import campaign_monitoring
from evaluation import disposable_followup_matrix as matrix
from evaluation.campaign_monitoring import CampaignMonitor


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(campaign_monitoring.shutil, "disk_usage", lambda *_: SimpleNamespace(free=128 * 1024**3))
    output, directory = tmp_path / "run", tmp_path / "run_supervision"
    output.mkdir()
    directory.mkdir()
    return CampaignMonitor(output, directory)


def test_mutable_artifact_versions_do_not_use_stream_offsets(monitor):
    artifact = monitor.output / "progress.json"
    artifact.write_text(json.dumps({"counts": {"passed": 12345}}))
    monitor.tick()
    original = artifact.read_bytes()
    artifact.write_text("{}")
    observed = monitor.tick()
    assert not observed["errors"]
    assert artifact.as_posix() not in monitor.cursor["streams"]
    assert len(list((monitor.directory / "artifact_versions").glob("*.bin"))) == 2
    assert original in [p.read_bytes() for p in (monitor.directory / "artifact_versions").glob("*.bin")]


def test_large_mutable_artifact_uses_bounded_io_not_a_total_size_cutoff(monitor):
    artifact = monitor.output / "progress.json"
    artifact.write_text('{"data":"' + "x" * 4_000_001 + '"}')
    assert not monitor.tick()["errors"]
    version = next((monitor.directory / "artifact_versions").glob("*.bin"))
    assert version.stat().st_size == artifact.stat().st_size


def test_archive_preserves_disk_stop_headroom(monitor, monkeypatch):
    monkeypatch.setattr(campaign_monitoring.shutil, "disk_usage", lambda *_: SimpleNamespace(free=22 * 1024**3))
    (monitor.output / "progress.json").write_text("{}")
    assert monitor.tick()["errors"]
    assert not list((monitor.directory / "artifact_versions").glob("*.bin"))


def test_one_bad_stream_does_not_starve_other_evidence_or_progress(monitor):
    bad, good = monitor.directory / "stdout.log", monitor.output / "coordinator_events.jsonl"
    bad.write_text("old long record\n")
    good.write_text("first\n")
    monitor.tick()
    old = monitor.cursor["streams"][str(bad)].copy()
    bad.write_text("x\n")
    with good.open("a") as stream:
        stream.write("second\n")
    (monitor.output / "progress.json").write_text('{"counts":{"failed":1}}')
    observed = monitor.tick()
    assert observed["errors"] and any(item["path"] == str(good) for item in observed["ranges"])
    assert monitor.cursor["streams"][str(bad)] == old
    assert monitor.cursor["streams"][str(good)]["offset"] == good.stat().st_size
    state = json.loads((monitor.directory / "monitor_state.json").read_text())
    assert state["counts"] == {"failed": 1} and state["status"] == "observation_degraded"


def test_mutable_workspace_text_is_not_discovered_as_a_log(monitor):
    case = monitor.output / "example/repeat_001/scenario/attempt_001"
    case.mkdir(parents=True)
    (case / "launch.json").write_text(json.dumps({"owner": "fixture", "variant": "example.py", "scenario": "scenario"}))
    (case / "events.jsonl").write_text('{"kind":"fixture"}\n')
    fixture = case / "fixtures/a/state.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("long state\n")
    monitor.tick()
    fixture.write_text("x")
    assert not monitor.tick()["errors"]
    assert str(fixture) not in monitor.cursor["streams"]


def test_partial_tail_retained_then_drained_at_terminal(monitor):
    path = monitor.directory / "stdout.log"
    path.write_bytes(b"complete\npartial")
    monitor.tick()
    assert monitor.cursor["streams"][str(path)]["offset"] == 9
    monitor.finish()
    assert monitor.cursor["streams"][str(path)]["offset"] == path.stat().st_size


def test_same_length_stream_rewrite_is_not_silently_accepted(monitor):
    path = monitor.directory / "stdout.log"
    path.write_text("original\n")
    monitor.tick()
    original = monitor.cursor["streams"][str(path)].copy()
    path.write_text("modified\n")
    assert monitor.tick()["errors"]
    assert monitor.cursor["streams"][str(path)] == original


def test_existing_stop_request_is_not_repeated(monitor):
    (monitor.output / "stop_request.json").write_text("{}")
    monitor.boundary = lambda: pytest.fail("Existing stop must not acquire a new stop boundary")
    monitor.stop = lambda *_: pytest.fail("Existing stop must not be dispatched again")
    monitor.escalate([{"error": "fixture"}])
    assert json.loads((monitor.directory / "monitor_escalation_result.json").read_text())["status"] == "existing_stop_request_not_repeated"


def test_discovery_failure_is_isolated_from_other_owned_streams(monitor):
    case = monitor.output / "example/repeat_001/scenario/attempt_001"
    case.mkdir(parents=True)
    (case / "launch.json").write_text("{")
    good = monitor.output / "coordinator_events.jsonl"
    good.write_text("still observed\n")
    result = monitor.tick()
    assert result["errors"] and result["ranges"][0]["path"] == str(good)


@pytest.mark.parametrize("valid_boundary", [True, False])
def test_persistent_errors_escalate_once_and_never_force_or_retry(monitor, valid_boundary):
    expected = {"pid": 424242, "creation_ticks": "123456", "executable": "fixture.exe", "command_line": "fixture"}
    (monitor.directory / "launch_receipt.json").write_text(json.dumps({"identity": expected}))
    (monitor.output / "coordinator_identity.json").write_text(json.dumps(expected))
    snapshot = {key: {"actual_exit_code": 0, "stdout": "fixture", "stderr": ""}
                for key in ("native", "wsl", "wsl_ports", "wsl_namespace", "gpu")}
    snapshot["native_processes"] = [{**expected, "parent_pid": os.getpid(), "owner": "same"},
                                     {**expected, "pid": os.getpid(), "parent_pid": 1, "owner": "same"}]
    if not valid_boundary:
        snapshot["native_processes"][0]["creation_ticks"] = "changed"
    monitor.boundary = lambda: snapshot
    calls = []
    monitor.stop = lambda output: calls.append(output) or {"status": "paused"}
    path = monitor.directory / "stdout.log"
    path.write_text("original record\n")
    monitor.tick()
    path.write_text("x")
    for _ in range(5):
        monitor.tick()
    assert len(calls) == int(valid_boundary)
    assert (monitor.directory / "monitor_alert.json").exists()
    assert (monitor.directory / "monitor_stop_intent.json").exists() is valid_boundary
    outcome = json.loads((monitor.directory / "monitor_escalation_result.json").read_text())
    assert outcome["status"] == ("paused" if valid_boundary else "unknown_do_not_retry_or_force")


def test_terminal_evidence_failure_cannot_suppress_actual_wait(tmp_path, monkeypatch):
    output = tmp_path / "run"
    monkeypatch.setattr(CampaignMonitor, "finish", lambda *_: (_ for _ in ()).throw(OSError("fixture drain failed")))
    source = "import pathlib,sys;pathlib.Path(sys.argv[1]).mkdir();raise SystemExit(7)"
    code = lifecycle.supervise([sys.executable, "-c", source, str(output)], output,
                               identity=lambda pid: {"pid": pid})
    directory = output.with_name("run_supervision")
    assert code == 7 and json.loads((directory / "coordinator_exit.json").read_text())["actual_wait"]
    assert json.loads((directory / "work_state.json").read_text())["status"] == "coordinator_exited"
    assert json.loads((directory / "monitor_terminal_failure.json").read_text())["exit_code"] == 7


def test_timeout_poll_uses_monitor_without_redispatch(tmp_path, monkeypatch):
    calls, ticks = [], []

    class Process:
        pid = 123

        def wait(self, timeout=None):
            calls.append(timeout)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    monkeypatch.setattr(CampaignMonitor, "tick", lambda self, **kwargs: ticks.append(kwargs) or {"ranges": []})
    output = tmp_path / "run"
    assert lifecycle.supervise(["fixture"], output, identity=lambda pid: {"pid": pid},
                               popen=lambda *args, **kwargs: Process()) == 0
    assert calls == [60, 60] and ticks[0] == {}


def test_repeated_monitor_state_publication_failure_escalates_without_losing_wait(tmp_path, monkeypatch):
    output = tmp_path / "run"
    polls, alerts = [], []
    original_save = matrix.save_json

    def save(path, value):
        if path.name == "monitor_state.json":
            raise OSError("fixture monitor-state publication failed")
        return original_save(path, value)

    class Process:
        pid = 123

        def wait(self, timeout=None):
            polls.append(timeout)
            if len(polls) <= 3:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    def escalate(self, errors):
        self.escalated = True
        alerts.append(errors)

    monkeypatch.setattr(matrix, "save_json", save)
    monkeypatch.setattr(CampaignMonitor, "escalate", escalate)
    assert lifecycle.supervise(["fixture"], output, identity=lambda pid: {"pid": pid},
                               popen=lambda *args, **kwargs: Process()) == 0
    assert len(alerts) == 1 and len(polls) == 4
    assert json.loads((output.with_name("run_supervision") / "coordinator_exit.json").read_text())["actual_wait"]


@pytest.mark.parametrize("failed_file", ["monitor_failure.json", "monitor_alert.json"])
def test_monitor_alert_recording_failure_cannot_release_owned_wait(tmp_path, monkeypatch, failed_file):
    output = tmp_path / "run"
    polls = []
    original = lifecycle.exclusive_json

    def record(path, value):
        if path.name == failed_file:
            raise OSError("fixture alert recording failed")
        return original(path, value)

    class Process:
        pid = 123

        def wait(self, timeout=None):
            polls.append(timeout)
            if len(polls) <= 4:
                raise subprocess.TimeoutExpired("fixture", timeout)
            return 0

    monkeypatch.setattr(lifecycle, "exclusive_json", record)
    monkeypatch.setattr(CampaignMonitor, "tick", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("fixture tick failed")))
    monkeypatch.setattr(CampaignMonitor, "finish", lambda *_: None)
    assert lifecycle.supervise(["fixture"], output, identity=lambda pid: {"pid": pid},
                               popen=lambda *args, **kwargs: Process()) == 0
    receipt = json.loads((output.with_name("run_supervision") / "coordinator_exit.json").read_text())
    assert receipt["actual_wait"] and len(polls) == 5
    field = "recording_error" if failed_file == "monitor_failure.json" else "escalation_error"
    assert "fixture alert recording failed" in receipt["monitor_failure"][field]
