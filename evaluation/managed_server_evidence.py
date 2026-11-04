"""Capture and audit managed MCP process evidence separately from model servers."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from evaluation.campaign_observation import read_json


def artifact_path(attempt: Path, value: str) -> Path:
    raw = str(value).replace("\\", "/")
    linux = re.match(r"^/mnt/([a-zA-Z])/(.*)$", raw)
    windows = re.match(r"^([a-zA-Z]):/(.*)$", raw)
    if os.name == "nt" and linux:
        raw = linux[1].upper() + ":/" + linux[2]
    elif os.name != "nt" and windows:
        raw = "/mnt/" + windows[1].lower() + "/" + windows[2]
    resolved = Path(raw).resolve()
    if resolved.parent != attempt.resolve():
        raise ValueError("Captured artifact is outside its exact attempt")
    return resolved


def starts(tools: list[dict]) -> dict[str, dict]:
    found = {}
    for tool in tools:
        result = tool.get("result") or {}
        data = result.get("structured_content") or {}
        if (tool.get("name") == "start_project_mcp_server"
                and isinstance(data, dict) and data.get("server_id")):
            found[str(data["server_id"])] = data
    return found


def finalize(tools: list[dict], root: Path) -> list[dict]:
    from runtime.managed_process import ManagedServerProcess

    outcomes = []
    for server_id, launched in starts(tools).items():
        outcome = {"server_id": server_id, "stopped": False, "actual_wait": False}
        if re.fullmatch(r"[a-f0-9]{12}", server_id):
            session = ManagedServerProcess.recover(root / ".cache/mcp_servers" / server_id,
                                                   str(launched.get("process_owner") or ""))
            if session is not None and session.receipt.get("identity") == launched.get("process_identity"):
                outcome.update(session.stop())
        outcomes.append(outcome)
    return outcomes


def capture(tools: list[dict], output: Path, root: Path) -> list[dict]:
    captures = []
    for server_id, launched in starts(tools).items():
        row: dict[str, Any] = {"scope": "managed_mcp_server_log", "server_id": server_id,
                               "source": str(launched.get("log_path") or "")}
        try:
            if not re.fullmatch(r"[a-f0-9]{12}", server_id):
                raise ValueError("Invalid managed server id")
            directory = (root / ".cache/mcp_servers" / server_id).resolve()
            source = Path(row["source"]).resolve()
            if source != directory / "server.log":
                raise ValueError("Managed log path differs from the original server directory")
            ownership = {name: read_json(directory / name) for name in
                         ("process_launch.json", "process_started.json", "process_exit.json", "supervisor_wait.json", "meta.json")}
            token, identity = launched.get("process_owner"), launched.get("process_identity")
            if (not isinstance(identity, dict) or any(not identity.get(key) for key in
                    ("pid", "parent_pid", "namespace", "creation", "executable", "command_line"))):
                raise ValueError("Managed process identity is incomplete")
            if not re.fullmatch(r"[a-f0-9]{32}", str(token or "")) or any(ownership[name].get("token") != token for name in
                                                ("process_launch.json", "process_started.json", "process_exit.json", "supervisor_wait.json")):
                raise ValueError("Managed process ownership token is missing or changed")
            if ownership["process_launch.json"].get("command") != launched.get("command"):
                raise ValueError("Managed process command differs from the authorized launch")
            if any(ownership[name].get("identity") != identity for name in
                   ("process_started.json", "process_exit.json")):
                raise ValueError("Managed process identity differs from the original launch")
            ended = ownership["process_exit.json"]
            if ended.get("actual_wait") is not True or ended.get("error") or ended.get("tree_verified") is not True:
                raise ValueError("Managed process has no successful actual exit wait")
            if ownership["supervisor_wait.json"].get("actual_wait") is not True:
                raise ValueError("Managed supervisor has no actual exit wait")
            metadata = ownership["meta.json"]
            if metadata.get("process_owner") != token or metadata.get("process_identity") != identity:
                raise ValueError("Managed metadata lost the original process identity")
            data = source.read_bytes()
            artifact = output / f"managed_mcp_{server_id}.log"
            with artifact.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            proof = output / f"managed_mcp_{server_id}_ownership.json"
            with proof.open("x", encoding="utf-8") as stream:
                json.dump(ownership, stream, indent=2, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            if source.read_bytes() != data:
                raise ValueError("Managed server log changed during capture")
            row.update(capture=str(artifact), bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                       ownership_capture=str(proof), ownership_sha256=hashlib.sha256(proof.read_bytes()).hexdigest(),
                       identity=identity, process_owner=token, actual_wait=True)
        except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
            row["not_captured"] = repr(exc)
        captures.append(row)
    return captures


def complete(tools: list[dict], logs: list[dict]) -> bool:
    expected = set(starts(tools))
    captured = [row["server_id"] for row in logs if row.get("scope") == "managed_mcp_server_log"
                and row.get("actual_wait") is True and row.get("capture") and not row.get("not_captured")]
    return len(captured) == len(expected) and set(captured) == expected


def audit(attempt: Path, result: dict) -> list[str]:
    tools = [tool for turn in result.get("turns", []) for tool in
             (turn.get("observed") or {}).get("tools", [])]
    logs = result.get("server_logs") or []
    launched_servers = starts(tools)
    findings = []
    if not complete(tools, logs):
        findings.append("managed_mcp_log_coverage_incomplete")
    for row in logs:
        if row.get("scope") != "managed_mcp_server_log":
            continue
        try:
            launched = launched_servers.get(row["server_id"])
            if (launched is None or launched.get("process_owner") != row["process_owner"]
                    or launched.get("process_identity") != row["identity"]):
                raise ValueError("Captured managed evidence differs from the recorded tool launch")
            for key, digest_key in (("capture", "sha256"), ("ownership_capture", "ownership_sha256")):
                path = artifact_path(attempt, row[key])
                if hashlib.sha256(path.read_bytes()).hexdigest() != row[digest_key]:
                    raise ValueError("Managed evidence artifact missing, outside attempt, or changed")
            if artifact_path(attempt, row["capture"]).stat().st_size != row["bytes"]:
                raise ValueError("Managed log byte count changed")
            proof = read_json(artifact_path(attempt, row["ownership_capture"]))
            if (proof["process_launch.json"].get("command") != launched.get("command")
                    or proof["meta.json"].get("command") != launched.get("command")
                    or proof["meta.json"].get("process_owner") != row["process_owner"]
                    or proof["meta.json"].get("process_identity") != row["identity"]):
                raise ValueError("Managed launch command or final metadata changed")
            for name in ("process_launch.json", "process_started.json", "process_exit.json", "supervisor_wait.json"):
                if proof[name].get("token") != row["process_owner"]:
                    raise ValueError("Managed receipt token changed")
            if (proof["process_started.json"].get("identity") != row["identity"]
                    or proof["process_exit.json"].get("identity") != row["identity"]
                    or proof["process_exit.json"].get("actual_wait") is not True
                    or proof["supervisor_wait.json"].get("actual_wait") is not True
                    or proof["process_exit.json"].get("tree_verified") is not True
                    or proof["process_exit.json"].get("error")):
                raise ValueError("Managed receipt identity or actual wait is incomplete")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            findings.append("managed_mcp_evidence_invalid:" + repr(exc))
    return findings
