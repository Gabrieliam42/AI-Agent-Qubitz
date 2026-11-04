"""Model-free exact remaining-work selection and provenance checks."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.followup_trajectory_selection import (
    read_selection,
    remaining_plan,
    require_unchanged,
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


@pytest.fixture
def prior(tmp_path):
    names, cases = ["A.py", "B.py"], ["one", "two", "three"]
    output = tmp_path / "evaluation/results/old"
    plan = write(tmp_path / "old_plan.json", {"variants": names, "scenarios": cases,
                                            "output": "evaluation/results/old"})
    rows = []
    for name, case, status in [("A.py", "one", "passed"), ("A.py", "two", "failed"),
                               ("B.py", "one", "harness_error")]:
        directory = output / Path(name).stem / "repeat_001" / case / "attempt_001"
        row = {"variant": name, "scenario": case, "repeat": 1, "attempt": 1,
               "status": status, "output": str(directory)}
        write(directory / "result.json", {**row, "finished": "terminal", "turns": [{"status": status}]})
        rows.append(row)
    summary = write(output / "summary.json", {"contract": {"variants": names, "scenarios": cases,
                                                          "repeats": 1, "suite": "expanded-v6"},
                                               "status": "stopped_infrastructure", "runs": rows})
    return tmp_path, plan, summary, {case: 1 for case in cases}


def test_exact_pair_complement_retains_failed_interrupted_and_unrun(prior):
    root, _plan, summary, _counts = prior
    result = remaining_plan(*prior)
    assert result["expected_trajectories"] == 5 and result["expected_turns"] == 5
    assert [(row["variant"], row["scenario"]) for row in result["trajectories"]] == [
        ("A.py", "two"), ("A.py", "three"), ("B.py", "one"), ("B.py", "two"), ("B.py", "three")]
    assert [row["status"] for row in result["retained_prior_nonpasses"]] == ["failed", "harness_error"]
    assert result["historical_results_are_new_passes"] is False
    path = write(root / "selection.json", result)
    selection = read_selection(path, result["variants"], result["scenarios"], 1, root)
    require_unchanged(selection, root)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(RuntimeError, match="changed"):
        require_unchanged(selection, root)
    assert json.loads(summary.read_text())["runs"][0]["status"] == "passed"


@pytest.mark.parametrize("change", ["duplicate", "extra", "reordered", "bool_repeat", "wrong_scope", "empty"])
def test_invalid_explicit_scope_fails_closed(prior, change):
    root, *_ = prior
    result = remaining_plan(*prior)
    if change == "duplicate":
        result["trajectories"].append(result["trajectories"][0])
    elif change == "extra":
        result["trajectories"][0]["scenario"] = "not-a-case"
    elif change == "reordered":
        result["trajectories"].reverse()
    elif change == "bool_repeat":
        result["trajectories"][0]["repeat"] = True
    elif change == "wrong_scope":
        result["variants"].reverse()
    else:
        result["trajectories"] = []
    path = write(root / "selection.json", result)
    with pytest.raises(ValueError):
        read_selection(path, ["A.py", "B.py"], ["one", "two", "three"], 1, root)


@pytest.mark.parametrize("change", ["orphan", "duplicate", "incomplete_pass", "false_pass", "wrong_path", "active"])
def test_unreconciled_history_is_not_exclusion_authority(prior, change):
    root, plan, summary, counts = prior
    value = json.loads(summary.read_text())
    row = value["runs"][0]
    result_path = Path(row["output"]) / "result.json"
    result = json.loads(result_path.read_text())
    if change == "orphan":
        write(summary.parent / "orphan/result.json", {})
    elif change == "duplicate":
        value["runs"].append(row)
    elif change == "incomplete_pass":
        result["turns"] = []
    elif change == "false_pass":
        result["status"] = "failed"
    elif change == "wrong_path":
        row["output"] = str(root / "other")
    else:
        value["status"] = "running"
    write(summary, value)
    write(result_path, result)
    with pytest.raises(ValueError):
        remaining_plan(root, plan, summary, counts)


def test_no_selection_preserves_ordinary_matrix_scope(tmp_path):
    assert read_selection(None, ["A.py"], ["one"], 1, tmp_path) is None
    require_unchanged(None, tmp_path)


@pytest.mark.parametrize("mutate", [False, True])
def test_coordinator_executes_only_selected_pairs_without_historical_rows(tmp_path, monkeypatch, mutate):
    from evaluation import disposable_followup_matrix as matrix
    from evaluation import smoke_complex_tasks, smoke_create_calculator

    cases = matrix.selected_scenarios(None, "original-v1")[:2]
    names = ["A.py", "B.py"]
    rows = [{"variant": "A.py", "scenario": cases[1].name, "repeat": 1},
            {"variant": "B.py", "scenario": cases[0].name, "repeat": 1}]
    path = write(tmp_path / "selection.json", {"schema_version": 1, "variants": names,
                                             "scenarios": [case.name for case in cases], "repeats": 1,
                                             "trajectories": rows})
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    monkeypatch.setattr(matrix, "variants", lambda *_: names)
    monkeypatch.setattr(matrix, "selected_scenarios", lambda *_: cases)
    monkeypatch.setattr(matrix, "validate_output", lambda path: path)
    monkeypatch.setattr(matrix, "freeze_sources", dict)
    monkeypatch.setattr(matrix, "coverage_plan", lambda *_: {})
    monkeypatch.setattr(matrix, "grouped_results", lambda *_: {})
    monkeypatch.setattr(smoke_complex_tasks, "protected_source_manifest", lambda *_: {})
    monkeypatch.setattr(smoke_create_calculator, "persistent_inventory", dict)
    control = importlib.import_module("followup_campaign_control")
    monkeypatch.setattr(control, "process_identity", lambda _: {"creation_ticks": "123", "command_line": "fixture"})
    monkeypatch.setattr(control, "stop_requested", lambda _: False)
    seen = []

    def attempt(args, name, case, output):
        seen.append((name, case.name))
        if mutate:
            path.write_text(path.read_text() + "\n")
        return {"variant": name, "scenario": case.name, "status": "passed", "turns": [{"status": "passed"}],
                "finished": "fixture", "output": str(output)}

    monkeypatch.setattr(matrix, "run_attempt", attempt)
    args = SimpleNamespace(variants=names, tier="all", scenarios=[case.name for case in cases], suite="original-v1",
                           repeats=1, gui=True, timeout=1800, startup_allowance=7200, distro="Ubuntu",
                           resume=False, output=tmp_path / "new_output", trajectory_plan=path)
    if mutate:
        with pytest.raises(RuntimeError, match="trajectory plan changed"):
            matrix._coordinate(args)
        assert seen == [("A.py", cases[1].name)]
    else:
        assert matrix._coordinate(args) == 0
        assert seen == [(row["variant"], row["scenario"]) for row in rows]
        summary = json.loads((args.output / "summary.json").read_text())
        assert len(summary["runs"]) == 2 and summary["counts"]["passed"] == 2
        assert summary["contract"]["trajectory_selection"]["trajectories"] == rows
