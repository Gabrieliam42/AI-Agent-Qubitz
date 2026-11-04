"""Private, host-owned original-to-dispatched argument lineage."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import uuid
from contextvars import ContextVar
from pathlib import Path

INVOCATION_ID = ContextVar("qubitz_host_invocation_id", default=None)


def arguments_hash(arguments) -> str:
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def dispatched_receipt(name, original, dispatched, *, turn_id=None, generation=None,
                       normalized=None, repairs=()) -> dict:
    normalized = original if normalized is None else normalized
    return {
        "schema_version": 1, "origin": "authorized_mcp_host",
        "invocation_id": INVOCATION_ID.get() or uuid.uuid4().hex,
        "tool": name, "turn_id": turn_id, "workspace_generation": generation,
        "original_arguments": copy.deepcopy(original), "dispatched_arguments": copy.deepcopy(dispatched),
        "normalized_arguments": copy.deepcopy(normalized), "normalization_steps": copy.deepcopy(list(repairs)),
        "normalized_sha256": arguments_hash(normalized),
        "original_sha256": arguments_hash(original), "dispatched_sha256": arguments_hash(dispatched),
        "arguments_changed": original != dispatched, "authorized": True, "dispatched": True,
    }


def canonical_arguments(row: dict) -> dict | None:
    """No normalization is inferred from a successful result or assistant text."""
    original = row.get("arguments")
    lineage = row.get("dispatch_receipt")
    if lineage is None:
        return original if isinstance(original, dict) else None
    if not isinstance(lineage, dict):
        return None
    dispatched = lineage.get("dispatched_arguments")
    try:
        valid = (
            lineage.get("schema_version") == 1 and lineage.get("origin") == "authorized_mcp_host"
            and lineage.get("authorized") is True and lineage.get("dispatched") is True
            and lineage.get("tool") == row.get("name")
            and lineage.get("invocation_id") == row.get("invocation_id")
            and isinstance(dispatched, dict) and lineage.get("original_arguments") == original
            and lineage.get("original_sha256") == arguments_hash(original)
            and lineage.get("normalized_sha256") == arguments_hash(lineage.get("normalized_arguments"))
            and lineage.get("dispatched_sha256") == arguments_hash(dispatched)
        )
    except (ValueError, TypeError):
        return None
    return dispatched if valid else None


def retain_process_output(directory: Path, stdout: bytes, stderr: bytes) -> dict:
    """Retain known completed output once; recording failure never replays the command."""
    result = {"complete": False, "files": []}
    try:
        root = Path(__file__).resolve().parents[1] / ".cache" / "owned_commands"
        if directory.is_symlink() or not directory.resolve().is_relative_to(root.resolve()):
            raise ValueError("Output retention requires the original owned command directory")
        if shutil.disk_usage(directory).free <= 20 * 1024**3 + len(stdout) + len(stderr):
            raise OSError("20 GiB reserve prevents full command-output retention")
        for name, data in (("stdout.log", stdout), ("stderr.log", stderr)):
            path = directory / name
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            result["files"].append({"path": str(path), "bytes": len(data),
                                    "sha256": hashlib.sha256(data).hexdigest()})
        result["complete"] = True
    except (OSError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result
