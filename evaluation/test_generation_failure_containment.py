"""Independent next-case continuation and repeated-failure hold, without inference."""
from __future__ import annotations

import copy
import importlib
import json
from types import SimpleNamespace

import pytest

from evaluation import disposable_followup_matrix as matrix
from evaluation.generation_failures import repeated_rejection_hold


def rejection(request_id):
    return {"schema": "qubitz_generation_rejection_v1", "category": "completion_format_rejected",
            "request_id": request_id, "backend": {"build": "fixture-build", "template_sha256": "a" * 64},
            "uncertain_prior_action": False, "completion_accepted": False}


@pytest.mark.parametrize("second_failure", [False, True])
def test_coordinator_continues_once_after_isolated_failure_and_holds_repetition(tmp_path, monkeypatch, second_failure):
    names = ["fixture.py"]
    cases = matrix.selected_scenarios(None, "original-v1")[:3]
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    monkeypatch.setattr(matrix, "variants", lambda *_: names)
    monkeypatch.setattr(matrix, "selected_scenarios", lambda *_: cases)
    monkeypatch.setattr(matrix, "validate_output", lambda path: path)
    monkeypatch.setattr(matrix, "freeze_sources", dict)
    monkeypatch.setattr(matrix, "coverage_plan", lambda *_: {})
    monkeypatch.setattr(matrix, "grouped_results", lambda *_: {})
    complex_tasks = importlib.import_module("smoke_complex_tasks")
    calculator = importlib.import_module("smoke_create_calculator")
    control = importlib.import_module("followup_campaign_control")
    monkeypatch.setattr(complex_tasks, "protected_source_manifest", lambda *_: {})
    monkeypatch.setattr(calculator, "persistent_inventory", dict)
    monkeypatch.setattr(control, "process_identity", lambda _: {"pid": 123, "creation_ticks": "100", "command_line": "fixture"})
    monkeypatch.setattr(control, "stop_requested", lambda _: False)
    seen = []

    def attempt(_args, name, case, output):
        seen.append(case.name)
        failed = len(seen) == 1 or second_failure
        return {"variant": name, "scenario": case.name, "status": "failed" if failed else "passed",
                "turns": [{"turn": 1, "status": "failed" if failed else "passed",
                           "observed": {"request_failure": rejection(str(len(seen)))} if failed else {}}],
                "cleanup": {"exit_code": 0}, "finished": "fixture", "output": str(output)}

    monkeypatch.setattr(matrix, "run_attempt", attempt)
    args = SimpleNamespace(variants=names, tier="all", scenarios=[case.name for case in cases], suite="original-v1",
                           repeats=1, gui=True, timeout=1800, startup_allowance=7200, distro="Ubuntu",
                           resume=False, output=tmp_path / "new_output", trajectory_plan=None)
    assert matrix._coordinate(args) == (2 if second_failure else 1)
    assert seen == [case.name for case in cases[:2 if second_failure else 3]]
    summary = json.loads((args.output / "summary.json").read_text())
    assert summary["status"] == ("paused" if second_failure else "complete")
    assert summary["runs"][0]["status"] == "failed"
    if second_failure:
        assert summary["generation_failure_hold"]["retry_permitted"] is False
        assert len(summary["runs"]) == 2


@pytest.mark.parametrize("damage", ["backend", "prior_uncertainty", "duplicate_request", "duplicate_tuple", "cleanup"])
def test_hold_requires_distinct_bound_failures_not_counts_alone(damage):
    rows = [{"variant": "fixture.py", "scenario": str(i), "repeat": 1, "status": "failed",
             "cleanup": {"exit_code": 0}, "turns": [{"observed": {"request_failure": rejection(str(i))}}]}
            for i in (1, 2)]
    assert repeated_rejection_hold(rows)
    altered = copy.deepcopy(rows)
    last = altered[-1]["turns"][0]["observed"]["request_failure"]
    if damage == "backend":
        last["backend"]["template_sha256"] = "b" * 64
    elif damage == "prior_uncertainty":
        last["uncertain_prior_action"] = True
    elif damage == "duplicate_request":
        last["request_id"] = "1"
    elif damage == "duplicate_tuple":
        altered[-1]["scenario"] = "1"
    else:
        altered[-1]["cleanup"]["exception"] = "cleanup unknown"
    assert repeated_rejection_hold(altered) is None
