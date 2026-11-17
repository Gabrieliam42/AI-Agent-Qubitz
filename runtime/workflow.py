"""Turn-local creation state and evidence-only terminal workflow records."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path


def creation_only(broker) -> bool:
    intent = getattr(broker, "task_intent", None)
    semantic = getattr(intent, "semantic", None)
    return bool(semantic and semantic.requests("create")
                and not semantic.requests("copy", "move", "delete", "execute",
                                          "mcp_call", "mcp_discover", "install", "upgrade", "start", "stop")
                and bool(getattr(broker, "creation_targets", ()))
                and not getattr(broker, "transaction_explicit_file_targets", ()))


def current_creations(broker) -> dict[str, str]:
    """A historical write or a merely existing target is not current progress."""
    targets = getattr(broker, "creation_targets", ())
    hashes = getattr(broker, "verified_creation_hashes", {})
    verified = {}
    for raw in sorted(targets):
        path = Path(raw)
        expected = hashes.get(str(path))
        try:
            if (expected and path.is_file() and not path.is_symlink()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == expected):
                verified[str(path)] = expected
        except OSError:
            continue
    return verified


def workflow_snapshot(broker, prompt: str) -> dict:
    required, missing = broker.completion_report(prompt)
    evidence = list(broker._current_turn_evidence())
    checks = [dict(check) for item in evidence for check in item.get("verification_checks", [])]
    paths = {path: digest for item in evidence if item.get("success")
             for path, digest in item.get("file_hashes", {}).items()}
    current = {}
    for raw, digest in paths.items():
        try:
            path = Path(raw)
            if path.is_file() and not path.is_symlink() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                current[raw] = digest
        except OSError:
            continue
    return {"turn_id": broker.turn_id, "operation": "creation" if creation_only(broker) else "task",
            "required": sorted(required), "missing": sorted(missing), "current_files": current,
            "checks": checks, "unavailable_tools": sorted(broker.unavailable_tool_names),
            "requested_tests_verified": bool("tests" in required and "tests" not in missing),
            "behavior_scope": "Only recorded checks, not universal correctness.",
            "recovery_exhausted": bool(broker.no_progress_recovery_exhausted)}


def verification_checks(tool: str, data: dict, *, success: bool) -> list[dict]:
    if tool == "codeintel_diagnostics":
        status = "unavailable" if data.get("available") is False else data.get("status", "unknown")
        if status not in {"passed", "failed", "error", "unavailable"}:
            status = "unknown"
        if status == "passed" and not success:
            status = "error"
        return [{"kind": "python_type_diagnostics", "status": status, "behavior_verified": False,
                 **{key: data[key] for key in ("path", "sha256", "provider", "returncode", "available") if key in data}}]
    if not success or tool not in {"write_file", "write_files"}:
        return []
    return [{key: check[key] for key in ("kind", "status", "path", "sha256", "behavior_verified") if key in check}
            for check in data.get("verification_checks", []) if isinstance(check, dict)]


def creation_summary(broker) -> str:
    paths = current_creations(broker)
    if not paths:
        return ""
    names = []
    for raw in paths:
        path = Path(raw)
        try:
            names.append(path.relative_to(broker.workspace).as_posix())
        except ValueError:
            names.append(str(path))
    checks = [check for item in broker._current_turn_evidence() if item.get("success")
              for check in item.get("verification_checks", [])
              if check.get("status") == "passed" and check.get("sha256") in paths.values()]
    parts = ["saved paths: " + ", ".join(names)]
    if any(check.get("kind") == "python_syntax" for check in checks):
        parts.append("Python syntax parsed before publication (not behavior)")
    if any(check.get("kind") == "python_type_diagnostics" for check in checks):
        parts.append("optional Python type diagnostics passed for recorded current file hashes (not behavior)")
    if "codeintel_diagnostics" in broker.unavailable_tool_names:
        parts.append("optional Python type diagnostics unavailable; not passed")
    return "; ".join(parts)


@dataclass
class _WorkflowCapture:
    runner: object
    previous_broker: object
    previous_turn: str | None
    previous_route: object
    turns: list[dict] = field(default_factory=list)


_ACTIVE_WORKFLOW: ContextVar[_WorkflowCapture | None] = ContextVar("qubitz_workflow_capture", default=None)


def capture_terminal_workflow(broker) -> None:
    """Detach current invocation evidence before the broker clears its turn state."""
    capture = _ACTIVE_WORKFLOW.get()
    if capture is None or broker is None or broker is not getattr(capture.runner, "_tool_permission_broker", None):
        return
    turn_id = broker.turn_id
    if (not turn_id or broker is capture.previous_broker and turn_id == capture.previous_turn
            or any(turn["turn_id"] == turn_id for turn in capture.turns)):
        return
    turn = {"turn_id": turn_id, "route": None, "workflow": None, "snapshot_error": None}
    capture.turns.append(turn)
    try:
        turn["route"] = getattr(getattr(capture.runner, "_route_decision", None), "selected_route", None)
        # Nested check metadata must not alias collections that teardown clears.
        turn["workflow"] = json.loads(json.dumps(workflow_snapshot(broker, broker.current_prompt), ensure_ascii=True))
    except BaseException as exc:  # noqa: BLE001 - evidence capture must not prevent broker teardown.
        turn["snapshot_error"] = type(exc).__name__


def install_workflow_records(base) -> None:
    """Wrap the final runner, including fast routes, fallback, errors and cancellation."""
    if getattr(base, "_qubitz_workflow_records", False):
        return
    original = base.AgentRunner

    class RecordedRunner(original):
        def __init__(self, *args, **kwargs):
            self._workflow_lock = threading.RLock()
            super().__init__(*args, **kwargs)

        def run_sync(self, prompt, callback=None):
            # The inner foreground lock can be released before this outer finally.
            # Keep the evidence snapshot inside the same invocation's ownership.
            with self._workflow_lock:
                return self._run_with_workflow_record(prompt, callback)

        def _run_with_workflow_record(self, prompt, callback=None):
            self._last_workflow_record = None
            self._workflow_record_error = ""
            self._workflow_record_delivery_error = ""
            invocation = uuid.uuid4().hex
            broker = getattr(self, "_tool_permission_broker", None)
            capture = _WorkflowCapture(self, broker, getattr(broker, "turn_id", None),
                                       getattr(self, "_route_decision", None))
            token = _ACTIVE_WORKFLOW.set(capture)
            started = time.time()
            status, error = "returned", None
            try:
                return super().run_sync(prompt, callback)
            except BaseException as exc:
                status = "cancelled" if (isinstance(exc, asyncio.CancelledError)
                                         or getattr(exc, "origin", None)
                                         or type(exc).__name__ == "RequestCancelled") else "error"
                error = type(exc).__name__
                raise
            finally:
                record = {"schema": 1, "invocation_id": invocation, "started_at": started,
                          "finished_at": time.time(), "status": status, "error_type": error,
                          "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                          "route": None,
                          "workflow": None}
                try:
                    # Early/fast paths may return without calling end_turn.
                    capture_terminal_workflow(getattr(self, "_tool_permission_broker", None))
                    errors = [{"turn_id": turn["turn_id"], "error_type": turn["snapshot_error"]}
                              for turn in capture.turns if turn["snapshot_error"]]
                    record["evidence_status"] = (
                        "partial" if errors and len(errors) < len(capture.turns)
                        else "unavailable" if errors else "captured" if capture.turns else "not_started"
                    )
                    record["snapshot_errors"] = errors
                    record["broker_turns"] = capture.turns
                    if capture.turns:
                        record["route"] = capture.turns[-1]["route"]
                        record["workflow"] = capture.turns[-1]["workflow"]
                    elif getattr(self, "_route_decision", None) is not capture.previous_route:
                        record["route"] = getattr(getattr(self, "_route_decision", None), "selected_route", None)
                    directory = Path(self.runtime_workspace) / ".qubitz" / "workflow_turns"
                    directory.mkdir(parents=True, exist_ok=True)
                    # One immutable file per invocation avoids cross-runner append interleaving.
                    with (directory / f"{invocation}.json").open("x", encoding="utf-8") as stream:
                        json.dump(record, stream, ensure_ascii=True, sort_keys=True)
                        stream.write("\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    self._last_workflow_record = record
                    if errors:
                        self._workflow_record_error = ",".join(sorted({item["error_type"] for item in errors}))
                except Exception as exc:  # noqa: BLE001 - never hide the original request failure.
                    self._workflow_record_error = type(exc).__name__
                finally:
                    _ACTIVE_WORKFLOW.reset(token)
                if self._workflow_record_error and callable(callback):
                    try:
                        callback("status", "Workflow terminal evidence could not be saved completely: "
                                 + self._workflow_record_error)
                    except Exception as delivery_error:  # noqa: BLE001 - preserve the original outcome.
                        self._workflow_record_delivery_error = type(delivery_error).__name__

    base.AgentRunner = RecordedRunner
    base._qubitz_workflow_records = True
