"""Shared reference-aware history review for newly frozen campaign controls.

Historical cached auditors and their verdicts remain immutable. New controls import
this function instead of copying the old descriptor-as-outcome implementation.
"""
from __future__ import annotations

import json
from pathlib import Path

from evaluation.campaign_observation import (
    read_json,
    resolve_event_reference,
    shared_reader,
)


def history_findings(control, directory: Path) -> tuple[list, list, dict]:
    directory = Path(directory).resolve()
    findings, gpu_unknown, receipts = [], [], {}
    history = directory / "observations.jsonl"
    cursor = read_json(directory / "observation_cursor.json")
    plan = read_json(directory / "plan.json")
    output = control.project_path(plan["output"])
    with shared_reader(history) as stream:
        data = stream.read()
    if not data or not data.endswith(b"\n"):
        return ["missing_or_incomplete_observation_history"], [], receipts
    seen, last = set(), None
    for line in data.splitlines():
        entry = json.loads(line)
        identifier = entry["observation_id"]
        path = control.project_path(entry["receipt"])
        if (path.parent != directory or path.name != f"notable_{identifier}.json" or identifier in seen):
            findings.append("invalid_observation_receipt_lineage")
            continue
        seen.add(identifier)
        receipt = read_json(path)
        receipts[control.relative(path)] = control.digest(path)
        if receipt.get("observation_id") != identifier or receipt.get("stage") != directory.name:
            findings.append("observation_receipt_identity_mismatch")
        if receipt.get("changed_sources") or receipt.get("storage_unchanged") is not True:
            findings.append("observed_source_or_private_inventory_drift")
        for notable in receipt.get("notable", []):
            event = notable.get("event")
            if not isinstance(event, dict):
                if notable.get("kind") in {"managed_server_cleanup_outcome", "owned_server_cleanup_outcome"}:
                    findings.append("observed_event_reference_unverified")
                continue
            key = str(notable.get("file", ""))
            if key.endswith("event_payload_index.jsonl"):
                index = output / key
                source_key = (Path(key).parent / str(event.get("source", ""))).as_posix()
                try:
                    event = resolve_event_reference(index, event,
                        entry.get("cursor_after", {}).get("files", {}).get(source_key, -1), output)
                except (OSError, ValueError, TypeError):
                    findings.append("observed_event_reference_unverified")
                    continue
                receipts[control.relative(index.parent / "events.jsonl")] = control.digest(index.parent / "events.jsonl")
            if key.endswith("gpu.jsonl") and (event.get("error") or not event.get("temperatures")):
                gpu_unknown.append({"observation_id": identifier, "file": key, "event": event})
            kind = event.get("kind")
            if kind == "managed_server_cleanup_outcome":
                outcome = event.get("outcome") or {}
                if outcome.get("stopped") is not True or outcome.get("actual_wait") is not True:
                    findings.append("observed_managed_server_cleanup_unverified")
            if kind == "owned_server_cleanup_outcome" and (event.get("outcome") or {}).get("status") == "failed":
                findings.append("observed_native_server_cleanup_failed")
        last = entry.get("cursor_after")
    if last != cursor:
        findings.append("observation_cursor_does_not_match_durable_history")
    if gpu_unknown:
        findings.append("gpu_observation_unknown")
    for path in (history, directory / "observation_cursor.json"):
        receipts[control.relative(path)] = control.digest(path)
    return sorted(set(findings)), gpu_unknown, receipts
