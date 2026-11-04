"""Exact-identity cooperative stop/checkpoint support for disposable campaigns."""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from evaluation.campaign_observation import read_json
from evaluation.evidence_receipts import WindowsProcessIdentity


def process_identity(pid: int) -> dict[str, Any] | None:
    if os.name != "nt":
        raise RuntimeError("Campaign control requires the Windows coordinator host")
    script = (
        "$ErrorActionPreference='Stop';"
        f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}';"
        "if($p){[ordered]@{pid=[int]$p.ProcessId;"
        "creation_ticks=[string]$p.CreationDate.ToUniversalTime().Ticks;"
        "executable=$p.ExecutablePath;command_line=$p.CommandLine}|ConvertTo-Json -Compress}"
    )
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, check=True, timeout=15)
    return json.loads(result.stdout) if result.stdout.strip() else None


def same_identity(actual: dict[str, Any] | None, expected: dict[str, Any]) -> bool:
    if actual is None:
        return False
    try:
        return WindowsProcessIdentity.from_mapping(actual).matches(WindowsProcessIdentity.from_mapping(expected))
    except ValueError:
        return False


def stop_requested(output: Path) -> bool:
    request = output / "stop_request.json"
    coordinator = output / "coordinator_identity.json"
    if not request.is_file() or not coordinator.is_file():
        return False
    return same_identity(json.loads(request.read_text())["coordinator"], json.loads(coordinator.read_text()))


def stop_campaign(output: Path, *, wait_seconds: float = 360) -> dict[str, Any]:
    # Import lazily so the CLI and tests share one implementation without a cycle.
    import disposable_followup_matrix as matrix

    output = matrix.validate_output(output)
    if (output / matrix.MARKER).read_text(encoding="utf-8") != "developer-only follow-up matrix\n":
        raise ValueError("Stop requires an authentic marked disposable campaign")
    summary = read_json(output / "summary.json")
    initial_contract = summary.get("contract")
    if summary["status"] in {"paused", "complete"}:
        return {"status": summary["status"], "already_checkpointed": True,
                "note": "No process was signalled; checkpoint status alone is not fresh server-exit evidence."}
    identity_path = output / "coordinator_identity.json"
    if not identity_path.is_file():
        raise RuntimeError("No exact coordinator receipt; refusing PID-only stop of a legacy campaign")
    identity = json.loads(identity_path.read_text())
    actual = process_identity(identity["pid"])
    if not same_identity(actual, identity):
        raise RuntimeError("Coordinator is absent or identity changed; no process was signalled")
    request = {"request_id": uuid.uuid4().hex, "time": matrix.stamp(), "coordinator": actual,
               "reason": "human_requested_stop"}
    from evaluation.complex_smoke_runtime import _exclusive_json

    _exclusive_json(output / "stop_request.json", request)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        progress_path = output / "progress.json"
        if progress_path.is_file():
            import hashlib

            from evaluation.campaign_lifecycle import read_progress

            progress = read_progress(output)
            contract_hash = hashlib.sha256(json.dumps(initial_contract, sort_keys=True).encode()).hexdigest()
            if progress["contract_sha256"] != contract_hash:
                raise RuntimeError("Compact progress belongs to a different campaign contract")
            if progress["status"] == "running":
                time.sleep(0.5)
                continue
        summary = read_json(output / "summary.json")
        if summary["status"] == "stopped_infrastructure":
            raise RuntimeError("Stop checkpoint reports an infrastructure/cleanup error; inspect preserved attempt receipts")
        if summary["status"] in {"paused", "complete"}:
            remaining = process_identity(identity["pid"])
            original_absent = remaining is None or (
                same_identity(remaining, remaining) and remaining["creation_ticks"] != identity["creation_ticks"])
            if original_absent:
                result = {"status": summary["status"], "request": request, "coordinator_stopped": True,
                          "actual_exit_code": None, "scope": "Original coordinator absence, not an invented wait exit code"}
                _exclusive_json(output / "stop_result.json", result)
                return result
        time.sleep(0.5)
    raise RuntimeError("Cooperative stop not finalized within bounded wait; request preserved, no unverified process killed")
