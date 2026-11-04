"""Explicit fresh-campaign selection; historical passes are exclusions, never new rows."""
from __future__ import annotations

import hashlib
from pathlib import Path

from evaluation.campaign_observation import read_json


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trajectory_key(row: dict) -> tuple[str, str, int]:
    if (not isinstance(row, dict) or set(row) != {"variant", "scenario", "repeat"}
            or not isinstance(row["variant"], str) or not isinstance(row["scenario"], str)
            or type(row["repeat"]) is not int):
        raise ValueError("A trajectory requires exactly variant, scenario and integer repeat")
    return row["variant"], row["scenario"], row["repeat"]


def read_selection(path: Path | None, names: list[str], cases: list[str], repeats: int,
                   root: Path) -> dict | None:
    if path is None:
        return None
    root = root.resolve()
    path = Path(path).absolute()
    if path.resolve() != path or not path.is_relative_to(root):
        raise ValueError("Trajectory plan must be an unaliased in-project file")
    before = digest(path)
    value = read_json(path)
    if (value.get("schema_version") != 1 or value.get("variants") != list(names)
            or value.get("scenarios") != list(cases) or value.get("repeats") != repeats):
        raise ValueError("Trajectory plan does not match the explicit CLI scope")
    rows = value.get("trajectories")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Trajectory selection must be a nonempty list")
    keys = [trajectory_key(row) for row in rows]
    ordered = [(name, case, repeat) for name in names for repeat in range(1, repeats + 1) for case in cases]
    selected = set(keys)
    if len(keys) != len(selected) or keys != [key for key in ordered if key in selected]:
        raise ValueError("Unknown, duplicate or reordered trajectory selection")
    if digest(path) != before:
        raise RuntimeError("Trajectory plan changed while being read")
    return {"path": path.relative_to(root).as_posix(), "sha256": before, "trajectories": rows}


def require_unchanged(selection: dict | None, root: Path) -> None:
    if selection and digest(root / selection["path"]) != selection["sha256"]:
        raise RuntimeError("Frozen trajectory plan changed; no next launch")


def remaining_plan(root: Path, plan_path: Path, summary_path: Path, turn_counts: dict[str, int]) -> dict:
    """Reconcile complete original result files before excluding exact passed pairs."""
    root = root.resolve()
    plan_path, summary_path = plan_path.resolve(), summary_path.resolve()
    if not plan_path.is_relative_to(root) or not summary_path.is_relative_to(root):
        raise ValueError("Historical evidence must remain inside the project")
    plan, summary = read_json(plan_path), read_json(summary_path)
    names, cases = plan["variants"], plan["scenarios"]
    contract = summary["contract"]
    if (contract["variants"] != names or contract["scenarios"] != cases or contract["repeats"] != 1
            or set(turn_counts) != set(cases) or contract.get("suite") != "expanded-v6"
            or summary.get("status") in {"running", "paused"}
            or (root / plan["output"]).resolve() != summary_path.parent):
        raise ValueError("Historical scope, terminal state or output lineage differs")
    ordered = [(name, case, 1) for name in names for case in cases]
    universe = set(ordered)
    rows, results, evidence, excluded = {}, set(), {}, []
    for path in (plan_path, summary_path):
        evidence[path.relative_to(root).as_posix()] = digest(path)
    for row in summary["runs"]:
        key = (row["variant"], row["scenario"], row["repeat"])
        if key not in universe or key in rows or row.get("attempt") != 1:
            raise ValueError("Historical duplicate, extra or repeated result cannot be silently excluded")
        directory = (summary_path.parent / Path(key[0]).stem / "repeat_001" / key[1] / "attempt_001").resolve()
        if Path(row["output"]).resolve() != directory:
            raise ValueError("Historical row output lineage differs")
        path = directory / "result.json"
        value = read_json(path)
        if ((value.get("variant"), value.get("scenario")) != key[:2] or not value.get("finished")
                or row.get("status") not in {"passed", "failed", "blocked", "harness_error", "interrupted"}):
            raise ValueError("Historical result is incomplete or mismatched")
        result_hash = digest(path)
        evidence[path.relative_to(root).as_posix()] = result_hash
        results.add(path)
        rows[key] = row["status"]
        if row["status"] == "passed":
            if (value.get("status") != "passed" or len(value["turns"]) != turn_counts[key[1]]
                    or any(turn.get("status") != "passed" for turn in value["turns"])):
                raise ValueError("A recorded pass lacks complete passing result evidence")
            excluded.append({"variant": key[0], "scenario": key[1], "repeat": 1,
                             "prior_result": path.relative_to(root).as_posix(), "sha256": result_hash})
    if set(summary_path.parent.rglob("result.json")) != results:
        raise ValueError("Orphan or unregistered historical result evidence requires reconciliation")
    trajectories = [{"variant": name, "scenario": case, "repeat": repeat}
                    for name, case, repeat in ordered if rows.get((name, case, repeat)) != "passed"]
    if len(trajectories) + len(excluded) != len(ordered):
        raise RuntimeError("Remaining-work partition is incomplete")
    return {"schema_version": 1, "suite": "expanded-v6", "variants": names, "scenarios": cases, "repeats": 1,
            "trajectories": trajectories, "excluded_historical_passes": excluded,
            "retained_prior_nonpasses": [{"variant": name, "scenario": case, "repeat": repeat, "status": status}
                                          for (name, case, repeat), status in rows.items() if status != "passed"],
            "expected_trajectories": len(trajectories),
            "expected_turns": sum(turn_counts[row["scenario"]] for row in trajectories),
            "historical_evidence_sha256": evidence,
            "historical_results_are_new_passes": False,
            "selection_note": "Only exact previously recorded successes are excluded; new sources are a separate baseline."}
