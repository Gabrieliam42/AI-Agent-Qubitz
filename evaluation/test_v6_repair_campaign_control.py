"""CPU lifecycle tests for the fresh repaired-suite smoke/full controller."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("v6_repair_control", ROOT / ".cache/qwen_v6_repair_followup_20261018.py")
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


@pytest.fixture
def campaign(tmp_path, monkeypatch):
    (tmp_path / ".cache").mkdir()
    output = tmp_path / "evaluation/results/fixture"
    case = output / "case"
    workspace = case / "fixtures/a"
    workspace.mkdir(parents=True)
    (workspace / "kept.txt").write_text("original", encoding="ascii")
    identity = {"pid": 10001, "creation_ticks": "123", "executable": sys.executable, "command_line": "fixture"}
    supervisor = {**identity, "pid": 10002}
    state = {"stage": "smoke", "status": "awaiting_audit", "output": "evaluation/results/fixture",
             "identity": identity, "variants": ["fixture.py"], "scenarios": ["one"],
             "expected_cases": 1, "expected_turns": 1, "command": [sys.executable, "fixture.py"],
             "stop_command": [sys.executable, "-u", "evaluation/disposable_followup_matrix.py", "--stop",
                              "--output", "evaluation/results/fixture"],
             "limitations": ["fixture limitation"], "supervisor_findings": []}
    monkeypatch.setattr(control, "ROOT", tmp_path)
    monkeypatch.setattr(control, "integrity_findings", lambda _state: [])
    monkeypatch.setattr(control.support, "idle", lambda: {"verified": "idle fixture"})
    monkeypatch.setattr(control, "process_identity", lambda _pid: None)
    files = control.paths(state)
    control.matrix.save_json(control.state_path("smoke"), state)
    control.exclusive(files["supervisor_launch"], {"identity": supervisor})
    control.exclusive(files["supervisor_exit"], {"pid": supervisor["pid"], "actual_wait": True,
                                                "waited_coordinators": [identity["pid"]]})
    control.exclusive(files["coordinator_exit"], {"pid": identity["pid"], "actual_wait": True, "exit_code": 1})
    control.exclusive(output / "coordinator_identity.json", identity)
    row = {"variant": "fixture.py", "scenario": "one", "repeat": 1, "status": "failed", "finished": "fixture",
           "output": str(case), "gui_variant_guard": {"fixed_variant": "fixture.py", "locked_entries": 37,
                                                       "selector_state": "disabled"},
           "turns": [{"status": "failed", "after": {"a": control.workspace_manifest(workspace)},
                      "observed": {"workflow_quality": {"tool_errors": [{"status": "retained_model_error"}]}}}]}
    summary = {"status": "complete", "runs": [row], "contract": {"variants": ["fixture.py"], "scenarios": ["one"]},
               "counts": {"passed": 0, "failed": 1}, "turn_counts": {"failed": 1}}
    control.matrix.save_json(output / "summary.json", summary)
    control.exclusive(case / "cleanup.json", {"exit_code": 0, "stdout": json.dumps({"survivors": []}),
                                              "windows_cleanup": {"verified_idle": True}})
    return state, files, summary, case


def test_complete_audit_retains_original_failed_task(campaign):
    state, files, summary, _ = campaign
    source = control.ROOT / state["output"] / "summary.json"
    before = source.read_bytes()
    assert control.finalize("smoke") == 0
    review = control.read_json(files["terminal_review"])
    assert review["audit_passed"] is True
    assert review["counts"] == summary["counts"]
    assert review["rows"][0]["tool_errors"] == [[{"status": "retained_model_error"}]]
    assert source.read_bytes() == before
    assert control.read_json(control.state_path("smoke"))["status"] == "complete"
    with pytest.raises(RuntimeError, match="not ready"):
        control.finalize("smoke")


def test_infrastructure_partial_becomes_held_without_complete_audit(campaign):
    state, files, summary, _ = campaign
    state["status"] = "hold_pending_finalize"
    summary["status"] = "stopped_infrastructure"
    summary["runs"][0].update(status="harness_error", turns=[], exception="fixture runtime failure")
    control.matrix.save_json(control.state_path("smoke"), state)
    control.matrix.save_json(control.ROOT / state["output"] / "summary.json", summary)
    assert control.finalize("smoke") == 2
    review = control.read_json(files["terminal_review"])
    assert review["audit_attempted"] is False
    assert review["full_coverage"] is False
    assert review["rows"][0]["exception"] == "fixture runtime failure"
    assert control.read_json(control.state_path("smoke"))["status"] == "held"


def test_unknown_supervisor_metadata_does_not_allow_finalization(campaign, monkeypatch):
    _, files, _, _ = campaign
    monkeypatch.setattr(control, "process_identity", lambda pid: {"pid": pid, "creation_ticks": None})
    with pytest.raises(RuntimeError, match="Supervisor still present or metadata unverified"):
        control.finalize("smoke")
    assert not files["finalize_intent"].exists()


def test_reused_pid_establishes_only_original_absence(campaign, monkeypatch):
    state, _, _, _ = campaign
    monkeypatch.setattr(control, "process_identity", lambda pid: {"pid": pid, "creation_ticks": "999"})
    assert control.original_absent(state["identity"])


def test_missing_wait_receipt_is_held_without_inventing_one(campaign):
    state, files, _, _ = campaign
    control.matrix.save_json(files["supervisor_exit"], {"pid": 10002, "actual_wait": False, "waited_coordinators": []})
    assert control.finalize("smoke") == 2
    assert not control.read_json(files["terminal_review"])["audit_passed"]
    assert control.read_json(files["supervisor_exit"])["actual_wait"] is False
    assert control.read_json(control.state_path(state["stage"]))["status"] == "held"


def test_complete_event_tails_are_consumed_and_partial_bytes_preserved(campaign):
    _, files, _, case = campaign
    raw = b'{"sequence":1,"kind":"turn_end","status":"failed"}\n{"partial":'
    (case / "events.jsonl").write_bytes(raw)
    assert control.finalize("smoke") == 2
    assert (case / "events.jsonl").read_bytes() == raw
    review = control.read_json(files["terminal_review"])
    assert "incomplete_event_tail_preserved" in review["findings"]
    assert review["audit_attempted"] is False
    cursor = control.read_json(files["cursor"])
    assert next(iter(cursor["case_offsets"].values()))["event_offset"] == raw.index(b"\n") + 1


def test_duplicate_rows_cannot_replace_missing_expected_case(campaign):
    state, _, summary, _ = campaign
    state["scenarios"].append("two")
    state["expected_cases"] = 2
    summary["contract"]["scenarios"] = state["scenarios"]
    summary["runs"].append(copy.deepcopy(summary["runs"][0]))
    assert not control.complete_coverage(state, summary, 1)


@pytest.mark.parametrize("metadata_failure", [False, True])
def test_supervisor_waits_once_and_enters_terminal_state(campaign, monkeypatch, metadata_failure):
    state, _files, _, _ = campaign
    state.update(status="launching", output="evaluation/results/supervised")
    state.pop("identity")
    control.matrix.save_json(control.state_path("smoke"), state)
    new_files = control.paths(state)
    child = Mock(pid=11000)
    child.wait.return_value = 2
    dispatch = Mock(return_value=child)
    monkeypatch.setattr(control.subprocess, "Popen", dispatch)
    identity = {"pid": child.pid, "creation_ticks": "678", "executable": str(Path(sys.executable)),
                "command_line": control.subprocess.list2cmdline(state["command"])}
    monkeypatch.setattr(control, "process_identity", lambda _pid: {"pid": child.pid} if metadata_failure else identity)
    assert control.supervise("smoke") == 2
    child.wait.assert_called_once()
    assert control.read_json(new_files["coordinator_exit"])["actual_wait"] is True
    assert control.read_json(new_files["supervisor_exit"])["waited_coordinators"] == [child.pid]
    assert control.read_json(control.state_path("smoke"))["status"] == "hold_pending_finalize"
    with pytest.raises(RuntimeError, match="already attempted"):
        control.supervise("smoke")
    assert dispatch.call_count == 1


def test_stop_requires_exact_identity_before_writing_intent(campaign, monkeypatch):
    state, files, _, _ = campaign
    state["status"] = "running"
    control.matrix.save_json(control.state_path("smoke"), state)
    monkeypatch.setattr(control, "process_identity", lambda pid: {"pid": pid})
    with pytest.raises(RuntimeError, match="Exact coordinator"):
        control.stop("smoke")
    assert not files["stop_intent"].exists()


def test_only_running_state_can_be_observed(campaign):
    with pytest.raises(RuntimeError, match="Observe only an active"):
        control.observe("smoke")


def test_ambiguous_launch_never_dispatches_twice(campaign, monkeypatch):
    state, _, _, _ = campaign
    state.update(status="prepared", output="evaluation/results/unlaunched")
    control.matrix.save_json(control.state_path("smoke"), state)
    monkeypatch.setattr(control.support, "storage", lambda: {"available": True})
    monkeypatch.setattr(control.matrix, "wslg_preflight", lambda _args: {"ready": True})
    dispatch = Mock(side_effect=OSError("fixture ambiguous launch"))
    monkeypatch.setattr(control.subprocess, "Popen", dispatch)
    with pytest.raises(OSError, match="ambiguous"):
        control.launch("smoke")
    with pytest.raises(RuntimeError, match="unlaunched prepared"):
        control.launch("smoke")
    dispatch.assert_called_once()
    assert control.read_json(control.state_path("smoke"))["status"] == "launching"


def test_supervisor_complete_summary_enters_awaiting_audit(campaign, monkeypatch):
    state, _, summary, _ = campaign
    state.update(status="launching", output="evaluation/results/completed-supervision")
    state.pop("identity")
    control.matrix.save_json(control.state_path("smoke"), state)
    (control.ROOT / state["output"]).mkdir(parents=True)
    control.matrix.save_json(control.ROOT / state["output"] / "summary.json", summary)
    child = Mock(pid=12000)
    child.wait.return_value = 1
    monkeypatch.setattr(control.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(control, "process_identity", lambda _pid: {
        "pid": child.pid, "creation_ticks": "678", "executable": str(Path(sys.executable)),
        "command_line": control.subprocess.list2cmdline(state["command"])})
    assert control.supervise("smoke") == 1
    assert control.read_json(control.state_path("smoke"))["status"] == "awaiting_audit"


def test_stop_uses_saved_exact_command_and_never_repeats(campaign, monkeypatch):
    state, files, _, _ = campaign
    state["status"] = "running"
    control.matrix.save_json(control.state_path("smoke"), state)
    monkeypatch.setattr(control, "process_identity", lambda _pid: state["identity"])
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(control.subprocess, "run", run)
    assert control.stop("smoke") == 0
    assert run.call_args.args[0] == state["stop_command"]
    assert control.read_json(files["stop_intent"])["command"] == state["stop_command"]
    with pytest.raises(FileExistsError):
        control.stop("smoke")
    run.assert_called_once()


def test_stop_boundary_overrun_is_reported_and_original_row_preserved(campaign):
    state, files, summary, _ = campaign
    summary["runs"][0]["started"] = "2026-10-18T01:01:00+00:00"
    control.matrix.save_json(control.ROOT / state["output"] / "summary.json", summary)
    control.exclusive(files["stop_intent"], {"time": "2026-10-18T01:00:00+00:00"})
    source = control.ROOT / state["output"] / "summary.json"
    before = source.read_bytes()
    assert control.finalize("smoke") == 0
    assert len(control.read_json(files["terminal_review"])["trajectories_started_after_stop"]) == 1
    assert source.read_bytes() == before


def test_public_scope_and_exact_stop_target_without_model_execution():
    variants = control.support.public_selection()
    scenarios = [c.name for c in control.matrix.selected_scenarios(None, "expanded-v6")]
    output = "evaluation/results/fresh-fixture"
    state = {"stage": "full", "variants": variants, "scenarios": scenarios, "expected_cases": 999,
             "expected_turns": 3256, "output": output, "command": control.command(output, variants, scenarios),
             "stop_command": [sys.executable, "-u", "evaluation/disposable_followup_matrix.py", "--stop", "--output", output]}
    control.check_scope(state)
    assert len(variants) == 37
    assert not set(variants) & control.support.EXCLUDED
    state["stop_command"][-1] = "evaluation/results/historical"
    with pytest.raises(RuntimeError, match="stop target"):
        control.check_scope(state)


def test_smoke_is_one_model_times_exactly_three_cases():
    cases = control.matrix.selected_scenarios(control.SMOKE_CASES, "expanded-v6")
    assert [c.name for c in cases] == control.SMOKE_CASES
    assert len(cases[-1].turns) == 3
    args = control.command("evaluation/results/fresh", [control.support.QWEN], control.SMOKE_CASES)
    assert args[args.index("--variants") + 1:args.index("--scenarios")] == [control.support.QWEN]
    assert args[args.index("--scenarios") + 1:] == control.SMOKE_CASES
