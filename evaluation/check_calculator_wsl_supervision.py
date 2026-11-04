"""Windows-to-WSL model-free supervision check using only the standard library."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluation import smoke_create_calculator as smoke

WORKER = """
import sys, time
from pathlib import Path
from evaluation import complex_smoke_runtime as ownership
output, token, timeout = Path(sys.argv[1]), sys.argv[2], float(sys.argv[3])
ownership.record_worker_identity(output, token)
ownership.wait_for_worker_admission(output, token, timeout)
(output / 'admitted.txt').write_text('model-free worker')
time.sleep(float(sys.argv[4]))
"""


def check_case(root: Path, action: str) -> dict:
    run = root / action
    run.mkdir()
    token = uuid.uuid4().hex
    command = smoke.wsl_worker_command([
        smoke.wsl_path(ROOT / ".venv/bin/python"), "-B", "-c", WORKER,
        smoke.wsl_path(run), token, "1.5" if action == "missing_admission" else "30",
        "20" if action == "timeout" else "2",
    ], token)
    smoke.save_json(run / "launch_command.json", {"command": command, "owner_token": token})
    with (run / "stdout.log").open("x") as stdout, (run / "stderr.log").open("x") as stderr:
        worker = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                  creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        try:
            receipt = run / "worker_identity.json"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    identity = json.loads(receipt.read_text())
                    group = int((run / "wsl-process-group.pid").read_text())
                except (FileNotFoundError, ValueError):
                    time.sleep(0.02)
                    continue
                break
            else:
                raise RuntimeError("Harmless worker did not publish its identity")
            if not identity["pid"] == identity["group"] == identity["session"] == group:
                raise RuntimeError("Recorded worker is not the real session leader")
            if action == "missing_admission":
                pass
            elif action == "stale_admission":
                smoke.ownership._exclusive_json(run / "launch_admission.json", {
                    "owner": token, "identity": {**identity, "start_ticks": identity["start_ticks"] - 1},
                })
            elif action == "timeout":
                state = smoke.supervise_worker(worker, run, token, time.monotonic() - 1, 0)
                if not state["timed_out"] or state["stop"].get("exit_code") != 0:
                    raise RuntimeError(f"Owned timeout cleanup failed: {state}")
                stopped = json.loads(state["stop"]["stdout"])
                if stopped["survivors"] or not any(row["pid"] == identity["pid"] for row in stopped["signals"]):
                    raise RuntimeError(f"Timeout check did not actually stop the exact owned worker: {stopped}")
            else:
                smoke.admit_wsl_worker(run, token, worker)
            exit_code = worker.wait(timeout=40)
        except BaseException:
            # A finite no-model fixture may finish naturally; never retry a stop on uncertainty.
            worker.wait(timeout=40)
            raise
    stderr_text = (run / "stderr.log").read_text()
    admitted = (run / "admitted.txt").exists()
    if action in {"missing_admission", "stale_admission"}:
        if exit_code == 0 or admitted or "model loading refused" not in stderr_text:
            raise RuntimeError(f"Admission guard failed: {action}, {exit_code}, {stderr_text}")
    elif action == "normal" and (exit_code != 0 or not admitted or stderr_text):
        raise RuntimeError(f"Normal harmless worker failed: {exit_code}, {stderr_text}")
    result = {"action": action, "identity": identity, "actual_wait": True,
              "pid": worker.pid, "exit_code": exit_code, "admitted": admitted,
              "expected_refusal": action in {"missing_admission", "stale_admission"}}
    smoke.save_json(run / "check.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Run this bridge check with Windows Python")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        parser.error("Output must remain inside the project")
    smoke.require_fresh_output(output)
    output.mkdir(parents=True, exist_ok=True)
    before = smoke.persistent_inventory()
    smoke.require_idle()
    cases = [check_case(output, action) for action in ("normal", "missing_admission", "stale_admission", "timeout")]
    smoke.require_idle()
    unchanged = smoke.persistent_inventory() == before
    result = {"passed": unchanged, "cases": cases, "shared_storage_unchanged": unchanged,
              "model_loads": 0, "inference_requests": 0}
    smoke.save_json(output / "review.json", result)
    if not unchanged:
        raise RuntimeError("Shared storage changed during the harmless worker checks")
    print("Four model-free Windows-to-WSL lifecycle checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
