"""Durable supervision and compact read-only progress for new follow-up campaigns."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path


def exclusive_json(path: Path, value: dict):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=True)
        stream.flush()
        os.fsync(stream.fileno())


def progress_record(summary: dict, serialized_summary: str) -> dict:
    cases = []
    for row in summary.get("runs", []):
        cases.append({key: row.get(key) for key in (
            "variant", "scenario", "repeat", "attempt", "status", "finished", "issues", "result_reference",
        )})
    return {"schema_version": 1, "status": summary["status"], "counts": summary.get("counts", {}),
            "turn_counts": summary.get("turn_counts", {}), "quality_counts": summary.get("quality_counts", {}),
            "contract_sha256": hashlib.sha256(json.dumps(summary["contract"], sort_keys=True).encode()).hexdigest(),
            "summary_sha256": hashlib.sha256(serialized_summary.encode("utf-8")).hexdigest(),
            "cases": cases, "updated_unix": time.time(),
            "limits": "Compact recorded evidence, not complete-scope acceptance or native GUI health."}


def read_progress(output: Path) -> dict:
    from evaluation.campaign_observation import read_json

    value = read_json(output / "progress.json")
    if value.get("schema_version") != 1 or not isinstance(value.get("cases"), list):
        raise ValueError("Unsupported compact campaign progress")
    return value


def result_reference(output: Path, row: dict) -> dict:
    path = Path(row["output"]) / "result.json"
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.resolve().is_relative_to(output.resolve()) or path.is_symlink():
        raise ValueError("Case reference escapes the owned campaign")
    if not path.is_file():
        return {"path": path.relative_to(output.resolve()).as_posix(), "status": "unavailable"}
    raw = path.read_bytes()
    return {"path": path.resolve().relative_to(output.resolve()).as_posix(), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "evidence_manifest": row.get("evidence_manifest")}


def resolve_result(output: Path, reference: dict) -> dict:
    relative = Path(reference["path"])
    path = output / relative
    if relative.is_absolute() or not path.resolve().is_relative_to(output.resolve()) or any(
        part.is_symlink() for part in (path, *path.parents)
    ):
        raise ValueError("Invalid case reference")
    raw = path.read_bytes()
    if len(raw) != reference["bytes"] or hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("Case reference hash or length mismatch")
    return json.loads(raw)


def coordinator_command(executable: str, script: Path, arguments: list[str], output: Path) -> list[str]:
    # The child must not recompute a timestamped default or inherit a relative cwd.
    return [executable, "-u", "-B", str(script.resolve()),
            *(value for value in arguments if value != "--supervise"), "--output", str(output.resolve())]


def reconcile_control_state(control_state: Path, *, identity) -> dict:
    """Project durable actual-wait evidence into outer state without any dispatch."""
    from evaluation.campaign_observation import read_json
    from evaluation.disposable_followup_matrix import ROOT, save_json

    if any(p.is_symlink() for p in (control_state, *control_state.parents)):
        raise ValueError("Control state must be an unaliased project-local file")
    control_state = control_state.resolve()
    if not control_state.is_relative_to(ROOT.resolve()):
        raise ValueError("Control state must be an unaliased project-local file")
    previous = read_json(control_state)
    output = ROOT / previous["output"]
    if any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError("Campaign output must not be aliased")
    output = output.resolve()
    if not output.is_relative_to(ROOT.resolve()):
        raise ValueError("Campaign output escapes the project")
    directory = output.with_name(output.name + "_supervision")
    launch = read_json(directory / "launch_receipt.json")
    waited = read_json(directory / "coordinator_exit.json")
    expected = launch["identity"]
    if (waited.get("actual_wait") is not True or type(waited.get("exit_code")) is not int
            or waited.get("identity") != expected or previous.get("identity") != expected):
        raise ValueError("Actual wait does not bind the exact saved coordinator")
    if identity(expected["pid"]) == expected:
        raise ValueError("Saved coordinator still appears live; hold reconciliation")
    summary_path = output / "summary.json"
    try:
        summary_bytes = summary_path.read_bytes()
        summary = json.loads(summary_bytes)
    except (OSError, ValueError, UnicodeError):
        summary_bytes = None
        summary = {}
    status = summary.get("status") if isinstance(summary, dict) else None
    if status not in {"complete", "paused", "stopped_infrastructure", "invalid_source_drift"}:
        status = "exit_recorded_checkpoint_unknown"
    receipt_path = control_state.with_name("terminal_reconciliation.json")
    receipt = {"schema_version": 1, "previous_state": previous,
               "exit_receipt_sha256": hashlib.sha256((directory / "coordinator_exit.json").read_bytes()).hexdigest(),
               "summary_sha256": hashlib.sha256(summary_bytes).hexdigest() if summary_bytes is not None else None,
               "actual_wait": True, "exit_code": waited["exit_code"], "status": status,
               "terminal_time": waited.get("time"), "cleanup_review_required": True,
               "identity": expected, "no_dispatch": True}
    if receipt_path.exists():
        recorded = read_json(receipt_path)
        if any(recorded.get(k) != receipt[k] for k in ("exit_receipt_sha256", "summary_sha256", "identity")):
            raise ValueError("Reconciliation receipt conflicts; no uncertain action replay")
    else:
        exclusive_json(receipt_path, receipt)
    updated = {**previous, "status": status, "supervisor_status": "coordinator_exited",
               "actual_wait_recorded": True, "exit_code": waited["exit_code"],
               "terminal_time": waited.get("time"), "cleanup_review_required": True,
               "terminal_reconciliation": str(receipt_path.relative_to(ROOT)),
               "next_action": "Review the terminal evidence; never redispatch or resume this stopped output."}
    save_json(control_state, updated)
    return updated


def supervise(command: list[str], output: Path, *, identity, popen=subprocess.Popen,
              control_state: Path | None = None) -> int:
    """Own one exact coordinator handle; an existing intent forbids redispatch."""
    output = output.resolve()
    directory = output.with_name(output.name + "_supervision")
    directory.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("Aliased supervision directory")
    if output.exists():
        raise ValueError("Supervision requires a fresh output; stopped outputs cannot be reused")
    if control_state is not None:
        from evaluation.campaign_observation import read_json
        from evaluation.disposable_followup_matrix import ROOT

        if any(p.is_symlink() for p in (control_state, *control_state.parents)):
            raise ValueError("Control state must not be aliased")
        control_state = control_state.resolve()
        if not control_state.is_relative_to(ROOT.resolve()):
            raise ValueError("Control state escapes the project")
        outer = read_json(control_state)
        if (ROOT / outer["output"]).resolve() != output or outer.get("identity"):
            raise ValueError("Control state is not a fresh exact-output authorization")
    exclusive_json(directory / "launch_intent.json", {"command": command, "output": str(output),
                   "status": "launch_intent", "supervisor_pid": os.getpid(), "time": time.time()})
    from evaluation.disposable_followup_matrix import save_json

    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
        try:
            process = popen(command, stdout=stdout, stderr=stderr, **flags)
        except OSError as exc:
            exclusive_json(directory / "launch_failure.json", {"error": repr(exc), "dispatched": False})
            raise
        captured = None
        try:
            captured = identity(process.pid)
            if not captured or captured.get("pid") != process.pid:
                raise RuntimeError("Dispatched coordinator identity is unknown; no retry allowed")
            exclusive_json(directory / "launch_receipt.json", {"identity": captured, "command": command})
            if control_state is not None:
                save_json(control_state, {**outer, "status": "running", "identity": captured,
                                          "actual_wait_recorded": False})
            save_json(directory / "work_state.json", {"status": "running", "identity": captured,
                                                      "command": command, "output": str(output)})
        except Exception as exc:  # noqa: BLE001 - retain the owned handle after any post-dispatch failure
            exclusive_json(directory / "post_dispatch_failure.json", {"pid": process.pid, "error": repr(exc),
                           "outcome": "dispatched_do_not_retry", "identity": captured})
        finally:
            while True:
                try:
                    code = process.wait()
                    break
                except KeyboardInterrupt:
                    interrupted = directory / "supervisor_interrupt.json"
                    if not interrupted.exists():
                        exclusive_json(interrupted, {"status": "still_owning_coordinator", "pid": process.pid,
                                                     "no_signal_or_redispatch": True})
            exclusive_json(directory / "coordinator_exit.json", {"pid": process.pid, "identity": captured,
                           "actual_wait": True, "exit_code": code, "waited_by_pid": os.getpid(), "time": time.time()})
        try:
            from evaluation.campaign_observation import read_json

            final = read_json(output / "summary.json")["status"]
        except (OSError, ValueError, KeyError):
            final = "exit_recorded_checkpoint_unknown"
        save_json(directory / "work_state.json", {"status": "coordinator_exited", "checkpoint_status": final,
                                                  "review_required": True, "identity": captured, "command": command,
                                                  "actual_wait": True, "exit_code": code, "output": str(output)})
        if control_state is not None:
            reconcile_control_state(control_state, identity=identity)
    return code
