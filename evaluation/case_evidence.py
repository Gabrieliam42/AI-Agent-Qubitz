"""Attempt-private transcript and byte-exact raw-event lineage; no log replay or mutation."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def file_receipt(path: Path, root: Path) -> dict:
    path = path.resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Evidence path must be an existing attempt-owned file")
    before = path.stat()
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("Evidence changed during final hashing")
    return {"path": path.relative_to(root.resolve()).as_posix(), "bytes": after.st_size, "sha256": digest}


def publish_manifest(output: Path, report: dict, journal=None, *, require_journal: bool = True) -> dict:
    output = output.resolve()
    issues = []
    transcripts = []
    if journal is not None:
        journal.close(timeout=15)
        if journal.error or journal._thread.is_alive():
            issues.append("transcript_retention_incomplete")
        if not journal.path.resolve().is_relative_to(output / "private_transcripts"):
            raise ValueError("Refusing to attribute a shared or unrelated conversation to this attempt")
        transcripts.append(file_receipt(journal.path, output))
    elif require_journal:
        issues.append("transcript_unavailable")
    events = output / "events.jsonl"
    index = output / "event_payload_index.jsonl"
    count, offset = 0, 0
    with index.open("x", encoding="utf-8") as destination, events.open("rb") as source:
        for raw in source:
            if count % 1000 == 0 and shutil.disk_usage(output).free <= 20 * 1024**3 + 1024**2:
                raise OSError("20 GiB reserve prevents evidence-index growth")
            if not raw.endswith(b"\n"):
                issues.append("incomplete_event_tail_preserved")
                break
            event = json.loads(raw)
            if not isinstance(event, dict):
                issues.append("invalid_event_record_preserved")
                event = {"kind": "invalid_record"}
            record = {"source": "events.jsonl", "offset": offset, "length": len(raw),
                      "sha256": hashlib.sha256(raw).hexdigest(),
                      **{key: event.get(key) for key in ("sequence", "turn", "kind", "invocation_id")}}
            destination.write(json.dumps(record, ensure_ascii=True) + "\n")
            count += 1
            offset += len(raw)
    manifest = {
        "schema_version": 1, "variant": report["variant"], "scenario": report["scenario"],
        "started": report.get("started"), "finished": report.get("finished"),
        "result_path": "result.json", "transcripts": transcripts,
        "direct_logs": [file_receipt(path, output) for path in sorted(output.glob("*.log"))],
        "graphical_captures": [file_receipt(path, output) for path in sorted(
            output.glob("fixtures/*/.cache/solar_behavior_evidence/*/*.png"))],
        "graphical_receipts": [file_receipt(path, output) for path in sorted(
            output.glob("fixtures/*/.cache/solar_behavior_evidence/*/receipt.json"))],
        "events": file_receipt(events, output), "payload_index": file_receipt(index, output),
        "complete_events_indexed": count, "complete_bytes_indexed": offset,
        "incomplete_tail_bytes": events.stat().st_size - offset,
        "display_tool_messages": "previews only; use result.json and the byte-exact payload index",
        "status": "incomplete" if issues else "complete", "issues": issues,
    }
    path = output / "evidence_manifest.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    return {**file_receipt(path, output), "status": manifest["status"], "issues": issues,
            "transcripts": transcripts, "started_utc": report.get("started"),
            "finished_utc": report.get("finished"),
            "display_tool_messages": manifest["display_tool_messages"]}
