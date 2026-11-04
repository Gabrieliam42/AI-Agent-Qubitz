"""Separate developer-only follow-up matrix, using real persistent Qubitz sessions.

Without --execute, print the selected trajectory plan without importing a variant.
Execution is coordinated on Windows with project-local Python workers under WSL.
Sampling, runtime selection, and tool implementations stay ordinary. The explicit
structured-error containment fixture replaces one model pass and its JSON proposal
only for its injected turn, restores them, and never counts that turn as model quality.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import ntpath
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from followup_matrix_cases import (
    capture_behavior_baselines,
    changed_paths,
    prepare_scenario,
    scenarios,
    verify_turn,
    workspace_manifest,
    write_fixture,
)

from evaluation.coverage_groups import (
    coverage_plan,
    grouped_results,
    runtime_parser_observation,
)
from evaluation.evidence_receipts import chain_pass_issues
from evaluation.verification_outcomes import verification_status
from qubitz_live_eval_policy import require_live_variants_allowed
from runtime import runtime_dependency_paths
from runtime.paths import variant_paths as public_variant_paths

SCHEMA_VERSION = 1
MARKER = ".qubitz-followup-matrix"
# The first two user-prioritized variants stay pinned; the remainder descend by declared total size.
PINNED_VARIANTS = (
    "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py",
    "AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py",
)
SMALL_VARIANTS = (
    "AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py",
    "AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py",
    "AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py",
)
DECLARED_TOTAL_PARAMETER_B_OVERRIDES = {
    "AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py": 284,
}
STATUS_CODES = {"passed": 0, "failed": 1, "blocked": 2, "harness_error": 3, "interrupted": 4}
LIMITATIONS = [
    "Only exposed state is recorded; hidden reasoning is not observable.",
    "Local stdio MCP fixtures exercise protocol/orchestration, not real provider accounts or APIs.",
    "The ordinary configured generation/embedding backend, context, sampling and thermal gates remain active.",
    "Memory and conversation journals are redirected to this scenario's private directories.",
    "Disposable workspaces are not an OS security sandbox; source checks detect and stop unexpected changes.",
    "Browser rendering and browser DevTools are not captured by this filesystem/MCP suite.",
]


def suite_limitations(suite: str) -> list[str]:
    if suite == "mini-user-v2":
        from followup_matrix_user_solar_v2 import LIMITATION

        return [*LIMITATIONS[:-1], LIMITATION]
    if suite == "mini-user-v1":
        from followup_matrix_user_solar import LIMITATION

        return [*LIMITATIONS[:-1], LIMITATION]
    if suite == "solar-visual-v1":
        from followup_matrix_solar_visual import LIMITATION

        return [*LIMITATIONS[:-1], LIMITATION]
    if suite == "expanded-v5":
        from followup_matrix_solar import LIMITATION

        return [*LIMITATIONS[:-1], LIMITATION,
                "Browser evidence is captured only for the solar-system scenario, not the other trajectories."]
    if suite == "expanded-v6":
        from followup_matrix_solar_user_v3 import LIMITATION

        return [*LIMITATIONS[:-1], LIMITATION,
                "The corrected user-prompt solar trajectory receives no numerical or browser-visual oracle."]
    return list(LIMITATIONS)


def save_json(path: Path, payload: Any) -> None:
    serialized = json.dumps(payload, indent=2, ensure_ascii=True, default=str)
    temporary = path.with_suffix(path.suffix + "." + uuid.uuid4().hex + ".pending")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(serialized)
        stream.flush()
        os.fsync(stream.fileno())
    for attempt in range(8):
        try:
            temporary.replace(path)
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33}:
                raise
            if attempt == 7:
                exc.add_note(f"Atomic JSON replace exhausted 8 attempts; staged data retained at {temporary}")
                raise
            if attempt == 0:
                print(f"[warning] Atomic JSON replace retry for {path}: {exc}", file=sys.stderr)
            time.sleep(min(0.05 * 2**attempt, 0.25))
        else:
            if attempt:
                print(f"[warning] Atomic JSON replace recovered after {attempt} retries for {path}", file=sys.stderr)
            if path.name == "summary.json" and isinstance(payload, dict) and "contract" in payload and "runs" in payload:
                from evaluation.campaign_lifecycle import progress_record

                save_json(path.with_name("progress.json"), progress_record(payload, serialized))
            return


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def windows_creation_ticks(created: str) -> str:
    """Preserve all seven fractional digits from the product's UTC CIM receipt."""
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d{7})Z", created)
    if match is None:
        raise ValueError("Invalid captured Windows process creation time")
    instant = datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc)
    elapsed = instant - datetime(1, 1, 1, tzinfo=timezone.utc)
    return str((elapsed.days * 86400 + elapsed.seconds) * 10_000_000 + int(match[2]))


def declared_total_parameters(filename: str) -> int:
    if filename in DECLARED_TOTAL_PARAMETER_B_OVERRIDES:
        return DECLARED_TOTAL_PARAMETER_B_OVERRIDES[filename]
    match = re.search(r"[-_](\d+)B", filename)
    if match is None:
        raise ValueError(f"No declared total parameter count for {filename}")
    return int(match[1])


_PUBLIC_VARIANTS = tuple(path.name for path in public_variant_paths(ROOT))
_PINNED_VARIANTS = tuple(name for name in PINNED_VARIANTS if name in _PUBLIC_VARIANTS)
ALL_VARIANTS = _PINNED_VARIANTS + tuple(sorted(
    (name for name in _PUBLIC_VARIANTS if name not in _PINNED_VARIANTS),
    key=lambda name: (-declared_total_parameters(name), name.casefold()),
))
LARGE_VARIANTS = tuple(name for name in ALL_VARIANTS if name not in SMALL_VARIANTS)
VARIANT_TIERS = {"all": ALL_VARIANTS, "large": LARGE_VARIANTS, "8b-9b": SMALL_VARIANTS}


def variants(requested: list[str] | None, tier: str = "all") -> list[str]:
    if tier not in VARIANT_TIERS:
        raise ValueError("Select a known variant tier")
    selected = list(VARIANT_TIERS[tier]) if requested is None else requested
    require_live_variants_allowed(selected)
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Select unique variant filenames")
    outside = set(selected) - set(VARIANT_TIERS[tier])
    if outside:
        raise ValueError(f"Variants outside the {tier} tier: {', '.join(sorted(outside))}")
    from runtime.paths import variant_path

    missing = [name for name in selected if not variant_path(name, ROOT).is_file()]
    if missing:
        raise ValueError(f"Missing current variants-directory launchers: {', '.join(missing)}")
    return selected


def suite_module(name: str = "original-v1") -> Any:
    if name == "mini-user-v2":
        import followup_matrix_user_solar_v2

        return followup_matrix_user_solar_v2
    if name == "mini-user-v1":
        import followup_matrix_user_solar

        return followup_matrix_user_solar
    if name == "solar-visual-v1":
        import followup_matrix_solar_visual

        return followup_matrix_solar_visual
    if name == "original-v1":
        return None
    if name == "expanded-v6":
        import followup_matrix_solar_user_v3

        return followup_matrix_solar_user_v3
    if name in {"expanded-v2", "expanded-v3", "expanded-v4", "expanded-v5"}:
        import followup_matrix_expanded

        return followup_matrix_expanded
    raise ValueError("Unknown follow-up suite")


def selected_scenarios(requested: list[str] | None, suite: str = "original-v1") -> list[Any]:
    expanded = suite_module(suite)
    cases = list(expanded.catalog() if expanded else scenarios())
    if expanded:
        required = {"catalog", "prepare", "install_capture", "snapshot_text", "verify"}
        if any(getattr(case, "source_url", "") for case in cases):
            required.update({"preflight_source", "source_unavailable", "only_source_blockers"})
        missing = sorted(name for name in required if not callable(getattr(expanded, name, None)))
        if missing:
            raise ValueError(f"Suite {suite} lacks required runtime callbacks: {', '.join(missing)}")
    if suite in {"expanded-v3", "expanded-v4", "expanded-v5", "expanded-v6"}:
        from followup_matrix_cases import strict_behavior_scenarios

        cases = strict_behavior_scenarios(cases)
    if suite in {"expanded-v4", "expanded-v5", "expanded-v6"}:
        from followup_matrix_cases import source_fidelity_scenarios

        cases = source_fidelity_scenarios(cases)
    if suite == "expanded-v5":
        from followup_matrix_solar import scenario

        cases.append(scenario())
    if suite == "expanded-v6":
        from followup_matrix_solar_user_v3 import scenario

        cases.append(scenario())
    catalog = {case.name: case for case in cases}
    if requested and "cancel_during_generation" in requested:
        from followup_matrix_cases import generation_cancellation_scenario

        case = generation_cancellation_scenario()
        catalog[case.name] = case
    if requested and "structured_error_then_new_task" in requested:
        from followup_matrix_cases import structured_error_scenario

        case = structured_error_scenario()
        catalog[case.name] = case
    names = list(catalog) if requested is None else requested
    if not names or len(set(names)) != len(names) or set(names) - set(catalog):
        raise ValueError("Select unique, known follow-up scenarios")
    return [catalog[name] for name in names]


def freeze_sources() -> dict[str, str]:
    from runtime.paths import variant_paths

    files = list(ROOT.glob("*.py")) + list((ROOT / "evaluation").glob("*.py"))
    files += variant_paths(ROOT)
    files += runtime_dependency_paths(ROOT)
    files += list((ROOT / "evaluation/fixtures/solar_assets").glob("*"))
    files += [ROOT / "evaluation/fixtures/solar_system_reference.json"]
    files += [ROOT / name for name in (
        "HARNESS.txt", "HARNESS.enc", "qubitz_variant_profiles.json",
        "qubitz_common_wrapper_template.json", "requirements.txt", "requirements-ci.txt",
    )]
    return {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(files) if path.is_file()}


def validate_output(path: Path) -> Path:
    output = path.absolute()
    expected = ROOT / "evaluation" / "results"
    if not output.resolve().is_relative_to(expected.resolve()) or output.resolve() == expected.resolve():
        raise ValueError("Output must be a new campaign directory under evaluation/results")
    for parent in (output, *output.parents):
        if parent.is_symlink():
            raise ValueError("Output cannot traverse a symlink")
        if parent == ROOT:
            break
    return output.resolve()


def validate_child(path: Path, owner: str) -> Path:
    directory = validate_output(path)
    campaign = next((parent for parent in directory.parents if (parent / MARKER).is_file()), None)
    if campaign is None:
        raise ValueError("Child has no marked campaign ancestor")
    launch = json.loads((directory / "launch.json").read_text(encoding="utf-8"))
    if launch.get("owner") != owner or len(owner) != 32 or any(c not in "0123456789abcdef" for c in owner):
        raise ValueError("Child ownership does not match its launch receipt")
    return directory


class EvidenceRecordingError(RuntimeError):
    """The evaluator could not preserve an event; task quality is unmeasurable."""


def mark_recording_failure(report: dict[str, Any], observer: Any) -> bool:
    failure = getattr(observer, "recording_failure", None)
    if not failure:
        return False
    report.update(status="harness_error", evidence_recording_failure=copy.deepcopy(failure))
    if "evaluator_recording_failure" not in report.setdefault("issues", []):
        report["issues"].append("evaluator_recording_failure")
    return True


class Observer:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.turn = 0
        self.lock = threading.Lock()
        self.sequence = 0
        self.tool_invocation: ContextVar[dict[str, Any] | None] = ContextVar("followup_tool_invocation", default=None)
        self.events: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self.owned_windows_servers: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.server_object_identity: dict[Any, tuple[int, str, str]] = {}
        self.server_log_captures: dict[tuple[int, str, str], dict[str, Any]] = {}
        self.recording_failure: dict[str, Any] | None = None
        self.clock_domain = {"process_id": os.getpid(), "platform": sys.platform, "clock": "time.monotonic"}

    def emit(self, kind: str, **data: Any) -> None:
        with self.lock:
            if self.recording_failure is not None:
                raise EvidenceRecordingError("Event capture already failed; no successful continuation is recorded")
            self.sequence += 1
            row = {"time": time.time(), "monotonic": time.monotonic(), "sequence": self.sequence,
                   "clock_domain": self.clock_domain,
                   "turn": self.turn, "kind": kind, **copy.deepcopy(data)}
            serialized = json.dumps(row, ensure_ascii=True, default=str) + "\n"
            try:
                with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(serialized)
            except OSError as exc:
                self.recording_failure = {
                    "path": str(self.output / "events.jsonl"), "failed_event": row,
                    "serialized_event": serialized, "exception": traceback.format_exc(),
                    "capture_complete": False, "append_outcome": "unknown_do_not_replay",
                }
                try:
                    save_json(self.output / "evidence_recording_failure.json", self.recording_failure)
                except OSError as receipt_error:
                    self.recording_failure["receipt_error"] = repr(receipt_error)
                print(json.dumps({"evaluator_recording_failure": self.recording_failure}, default=str),
                      file=sys.stderr, flush=True)
                raise EvidenceRecordingError("Evaluator event recording failed; preserve partial evidence") from exc
            # Keep selected events only AFTER the durable-file write succeeded.
            if kind in {"callback", "turn_start", "turn_end"} and not str(data.get("role", "")).endswith("_delta"):
                self.events.append(row)

    def callback(self, kind: str, message: str) -> None:
        budget = getattr(self, "turn_budget", None)
        if budget is not None:
            budget.observe(kind, message)
        self.emit("callback", role=kind, message=message)

    def capture_owned_windows_server(self, base: Any, server: Any) -> None:
        from smoke_create_calculator import server_identity

        receipt = getattr(server, "_qubitz_owned_windows_receipt", {})
        if not receipt:
            return
        launch = json.loads((self.output / "launch.json").read_text(encoding="utf-8"))
        row = {
            "ProcessId": receipt["pid"],
            "CreationDate": windows_creation_ticks(receipt["created"]),
            "ExecutablePath": ntpath.normpath(base.wsl_path_to_windows_path(server.resolve_executable())),
            "product_owner": server._qubitz_owner_token,
            "model_path": str(server.model_path),
            "model_alias": server.config.model_name,
        }
        log_path = Path(server.log_path)
        if not log_path.resolve().is_relative_to((ROOT / ".cache").resolve()):
            raise EvidenceRecordingError("Owned server log is outside the project cache")
        with log_path.open("rb") as stream:
            prefix = stream.read(256)
        if not prefix:
            raise EvidenceRecordingError("Owned server log has no startup header")
        row.update(log_path=str(log_path), log_prefix_bytes=len(prefix),
                   log_prefix_sha256=hashlib.sha256(prefix).hexdigest())
        identity = server_identity(row)
        with self.lock:
            if identity in self.owned_windows_servers:
                self.server_object_identity[server] = identity
                return
            # Persist at startup, not at worker exit: forced WSL cleanup can skip finally.
            save_json(self.output / "owned_windows_servers.json", {
                "schema_version": SCHEMA_VERSION, "owner": launch["owner"],
                "servers": [*self.owned_windows_servers.values(), row],
            })
            self.owned_windows_servers[identity] = row
            self.server_object_identity[server] = identity
        self.emit("owned_windows_server", process=row)

    def capture_owned_server_log(self, identity: tuple[int, str, str], phase: str) -> None:
        if identity in self.server_log_captures:
            return
        row = self.owned_windows_servers[identity]
        source = Path(row["log_path"])
        destination = self.output / f"server_{len(self.server_log_captures):03}.log"
        receipt = {"scope": "owned_server_log", "process": row, "identity": identity,
                   "source": str(source), "phase": phase}
        try:
            if not source.resolve().is_relative_to((ROOT / ".cache").resolve()):
                raise ValueError("Owned server log left the project cache")
            with source.open("rb") as stream:
                prefix = stream.read(row["log_prefix_bytes"])
            if hashlib.sha256(prefix).hexdigest() != row["log_prefix_sha256"]:
                raise ValueError("Owned server log was replaced before capture")
            before = source.stat()
            with source.open("rb") as stream, destination.open("xb") as captured:
                shutil.copyfileobj(stream, captured, length=1024 * 1024)
                captured.flush()
                os.fsync(captured.fileno())
            after = source.stat()
            stable = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            with source.open("rb") as stream:
                source_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            final = source.stat()
            with destination.open("rb") as stream:
                captured_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if stable(before) != stable(after) or stable(after) != stable(final) or source_sha256 != captured_sha256:
                raise ValueError("Owned server log changed during capture")
            receipt.update(capture=str(destination), bytes=after.st_size, sha256=captured_sha256)
        except (OSError, RuntimeError, ValueError) as exc:
            receipt["not_captured"] = repr(exc)
            if destination.exists():
                receipt["partial_capture"] = str(destination)
        self.server_log_captures[identity] = receipt
        self.emit("owned_server_log_capture", capture=receipt)

    def install(self, base: Any) -> None:
        from smoke_create_calculator import install_request_capture

        install_request_capture(base, self)
        original_start = base.LlamaCppServerProcess.ensure_started

        def ensure_started(server: Any) -> Any:
            previous = self.server_object_identity.get(server)
            process = getattr(server, "process", None)
            if previous is not None and (process is None or process.poll() is not None):
                self.capture_owned_server_log(previous, "before_restart")
            try:
                result = original_start(server)
            except BaseException:
                try:
                    self.capture_owned_windows_server(base, server)
                except Exception as exc:  # noqa: BLE001 - retain the original startup exception and capture failure
                    self.emit("owned_server_capture_error", exception=repr(exc))
                raise
            self.capture_owned_windows_server(base, server)
            return result

        base.LlamaCppServerProcess.ensure_started = ensure_started
        original_shutdown = getattr(base.LlamaCppServerProcess, "shutdown", None)

        def shutdown(server: Any) -> Any:
            identity = self.server_object_identity.get(server)
            try:
                return original_shutdown(server)
            finally:
                if identity is not None:
                    self.capture_owned_server_log(identity, "shutdown")
                outcome = getattr(server, "_qubitz_cleanup_outcome", None)
                if outcome:
                    self.emit("owned_server_cleanup_outcome", outcome=outcome)

        if callable(original_shutdown):
            base.LlamaCppServerProcess.shutdown = shutdown
        original = base.MCPHost.call_tool
        original_batch = getattr(base.MCPHost, "_consume_parallel_read_only_batch", None)

        if callable(original_batch):
            async def consume_batch(host: Any, name: str, arguments: Any, broker: Any) -> Any:
                handled, result = await original_batch(host, name, arguments, broker)
                invocation = self.tool_invocation.get()
                if handled and invocation is not None:
                    invocation["execution_kind"] = "parallel_result_consumption"
                return handled, result

            base.MCPHost._consume_parallel_read_only_batch = consume_batch

        async def call_tool(host: Any, name: str, arguments: Any) -> Any:
            from runtime.invocation_receipts import INVOCATION_ID

            parent = self.tool_invocation.get()
            row = {"turn": self.turn, "capture_origin": "mcp_host", "name": name,
                   "arguments": copy.deepcopy(arguments), "started": time.time(),
                   "started_monotonic": time.monotonic(), "invocation_id": uuid.uuid4().hex,
                   "parent_invocation_id": parent["invocation_id"] if parent else None,
                   "execution_kind": "parallel_dispatch" if getattr(host, "_qubitz_parallel_dispatching", False)
                   else "host_invocation"}
            token = self.tool_invocation.set(row)
            lineage_token = INVOCATION_ID.set(row["invocation_id"])
            self.emit("tool_request", **row)
            try:
                result = await original(host, name, arguments)
                lineage = getattr(result, "_qubitz_invocation_receipt", None)
                if isinstance(lineage, dict):
                    row["dispatch_receipt"] = copy.deepcopy(lineage)
                row["result"] = base.serialize_mcp_result(result)
                return result
            except BaseException as exc:
                row["exception"] = repr(exc)
                raise
            finally:
                row["finished"] = time.time()
                row["finished_monotonic"] = time.monotonic()
                row["duration_seconds"] = row["finished_monotonic"] - row["started_monotonic"]
                row["duration_clock"] = "time.monotonic"
                row["clock_domain"] = self.clock_domain
                row["wall_clock_reversed"] = row["finished"] < row["started"]
                self.tools.append(row)
                self.emit("tool_result", **row)
                self.tool_invocation.reset(token)
                INVOCATION_ID.reset(lineage_token)

        base.MCPHost.call_tool = call_tool


def runner_snapshot(runner: Any) -> dict[str, Any]:
    decision = getattr(runner, "_route_decision", None)
    broker = getattr(runner, "_tool_permission_broker", None)
    evidence = getattr(broker, "_current_turn_evidence", None)
    return {
        "runner_identity": id(runner),
        "completion_state": copy.deepcopy(getattr(runner, "_qubitz_completion_state", None)),
        "parser_observation": runtime_parser_observation(runner),
        "route": asdict(decision) if hasattr(decision, "__dataclass_fields__") else str(decision),
        "history": copy.deepcopy(getattr(runner, "history", [])),
        "last_verified_edit_receipt": copy.deepcopy(getattr(runner, "_last_verified_edit_receipt", {})),
        "verification_receipts": copy.deepcopy(getattr(runner, "_last_verification_receipts", [])),
        "recovery_receipts": copy.deepcopy(getattr(runner, "_last_recovery_receipts", [])),
        "structured_proposal_receipts": copy.deepcopy(getattr(runner, "_last_structured_proposal_receipts", [])),
        "relation": str(getattr(runner, "_turn_relation", "")),
        "exposed_completion_evidence": copy.deepcopy(evidence()) if callable(evidence) else [],
        "turn_control": copy.deepcopy(getattr(runner, "_last_turn_control", {})),
        "cancel_source": str(getattr(runner, "_cancel_source", "")),
        "cancel_origin": str(getattr(runner, "_cancel_origin", "")),
        "default_behavior_receipts": copy.deepcopy(getattr(broker, "_default_behavior_receipts", {})),
        "creation_section_receipts": copy.deepcopy(getattr(broker, "_creation_section_receipts", [])),
        "generation_metrics": copy.deepcopy(getattr(runner, "_last_generation_metrics", {})),
        "request_failure": copy.deepcopy(getattr(runner, "_last_request_failure", None)),
        "applied_steering": list(getattr(runner, "_qubitz_applied_side_notes", [])),
        "config": {name: str(getattr(getattr(runner, "config", None), name, "")) for name in (
            "workspace", "runtime_workspace", "model_name", "num_ctx", "num_predict", "thinking_effort", "access_mode",
        )},
    }


def apply_gui_diagnostics(report: dict[str, Any], diagnostics: dict[str, Any]) -> None:
    """Worker faults are infrastructure failures, not ordinary model verdicts."""
    report["gui_diagnostics"] = diagnostics
    issues = set(diagnostics.get("behavior_issues", ()))
    report["gui_status"] = "failed" if issues else "passed"
    if issues.intersection({"gui_worker_exception", "gui_logged_error"}):
        report["status_before_runtime_fault"] = report["status"]
        report["status"] = "harness_error"
        if "runtime_worker_error" not in report.setdefault("issues", []):
            report["issues"].append("runtime_worker_error")
    elif report["status"] != "interrupted" and issues:
        report["status"] = "failed"
    if diagnostics.get("request_failures"):
        if report["status"] not in {"interrupted", "harness_error"}:
            report["status"] = "failed"
        if "generation_rejected" not in report.setdefault("issues", []):
            report["issues"].append("generation_rejected")


def generation_quality(path: Path, turn: int, offset: int = 0) -> dict[str, Any]:
    """Summarize raw transport evidence independently of task completion claims."""
    requests: dict[str, dict[str, Any]] = {}
    current = ""
    cancellations: list[dict[str, Any]] = []
    guard_stops: list[str] = []
    continuation_extensions = 0
    native_argument_rejections = 0
    with path.open("rb") as stream:
        stream.seek(offset)
        for line in stream:
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if row.get("turn") != turn:
                continue
            kind, instant = row.get("kind"), float(row.get("time", 0))
            if kind == "cancel_request":
                cancellations.append({"time": instant, "monotonic": row.get("monotonic"),
                                      "origin": row.get("origin", "unobserved")})
            elif kind == "callback" and row.get("role") == "native_tool_call_rejected":
                native_argument_rejections += 1
            elif kind == "callback" and row.get("role") == "status":
                message = str(row.get("message", ""))
                if message.startswith("Stopped repetitive"):
                    guard_stops.append(message)
                if message.startswith("Progressive tool-step budget extended"):
                    continuation_extensions += 1
            elif kind == "http_request":
                current = str(row.get("request_id") or len(requests))
                payload = row.get("payload") or {}
                requests[current] = {
                    "started": instant, "last_stream": None, "closed": None, "finish_reason": None,
                    "started_monotonic": row.get("monotonic"), "last_stream_monotonic": None,
                    "closed_monotonic": None,
                    "characters": {"content": 0, "reasoning_content": 0}, "tails": {},
                    "heads": {}, "protected_channels": set(),
                    "protected": bool(isinstance(payload, dict) and payload.get("response_format")),
                    "candidates": {},
                    "tool_arguments": {},
                    "http_status": None, "transport_error": "", "stream_error": False,
                    "error_time": None, "error_monotonic": None,
                }
            elif kind == "http_status":
                key = str(row.get("request_id") or current)
                if key in requests:
                    requests[key]["http_status"] = row.get("status")
            elif kind in {"http_transport_error", "http_stream_error"}:
                key = str(row.get("request_id") or current)
                if key in requests:
                    requests[key]["transport_error"] = str(row.get("exception_type") or "unknown")
                    requests[key]["error_time"] = instant
                    requests[key]["error_monotonic"] = row.get("monotonic")
            elif kind == "http_stream_close":
                key = str(row.get("request_id") or current)
                if key in requests and row.get("closed"):
                    requests[key]["closed"] = instant
                    requests[key]["closed_monotonic"] = row.get("monotonic")
            elif kind in {"http_stream_line", "http_body"}:
                key = str(row.get("request_id") or current)
                request = requests.get(key)
                raw = str(row.get("body" if kind == "http_body" else "line", ""))
                if request is None:
                    continue
                if kind == "http_stream_line":
                    if not raw.startswith("data:") or raw[5:].strip() == "[DONE]":
                        continue
                    raw = raw[5:].strip()
                try:
                    event = json.loads(raw)
                except ValueError:
                    request["stream_error"] = True
                    continue
                if not isinstance(event, dict) or event.get("error"):
                    request["stream_error"] = True
                    continue
                request["last_stream"] = instant
                request["last_stream_monotonic"] = row.get("monotonic")
                for choice in event.get("choices", []):
                    if choice.get("finish_reason"):
                        request["finish_reason"] = choice["finish_reason"]
                    delta = choice.get("message" if kind == "http_body" else "delta") or {}
                    for call in delta.get("tool_calls", []):
                        function = call.get("function") or {}
                        fragment = function.get("arguments")
                        if not isinstance(fragment, str):
                            continue
                        index = str(call.get("index", 0))
                        argument = request["tool_arguments"].setdefault(index, {
                            "characters": 0, "tail": "", "name": "", "sha256": hashlib.sha256(),
                            "delimiter_markers_present": False,
                        })
                        argument["name"] = str(function.get("name") or argument["name"])
                        argument["characters"] += len(fragment)
                        argument["sha256"].update(fragment.encode("utf-8"))
                        argument["tail"] = (argument["tail"] + fragment)[-12000:]
                        tail = argument["tail"]
                        argument["delimiter_markers_present"] |= bool(re.search(r"</?arg_(?:key|value)>", tail))
                        if len(tail) >= 8192 and len(re.findall(r"\\n", tail)) * 2 >= len(tail) * 0.90:
                            request["candidates"]["tool_arguments:" + index] = {
                                "tool": argument["name"], "characters": argument["characters"],
                                "reason": "escaped_newline_dominated_arguments", "review_only": True,
                            }
                    for channel in ("content", "reasoning_content"):
                        text = delta.get(channel)
                        if not isinstance(text, str) or not text:
                            continue
                        before = request["characters"][channel]
                        request["characters"][channel] += len(text)
                        tail = (request["tails"].get(channel, "") + text)[-12000:]
                        request["tails"][channel] = tail
                        head = (request["heads"].get(channel, "") + text)[:256]
                        request["heads"][channel] = head
                        if (any(marker in tail for marker in ("```", "<tool_call", "<html", "<script"))
                                or re.match(r"\s*(?:[\[{<]|def\s|class\s|function\s|const\s|let\s|import\s)", head)):
                            request["protected_channels"].add(channel)
                            request["candidates"].pop(channel, None)
                        if (request["protected"] or channel in request["protected_channels"]
                                or before // 1024 == request["characters"][channel] // 1024):
                            continue
                        lines = Counter(re.sub(r"\s+", " ", item).strip() for item in tail.splitlines()
                                        if len(item.strip()) >= 16)
                        repeated = [(item, count) for item, count in lines.items() if count >= 10]
                        if repeated:
                            item, count = max(repeated, key=lambda entry: entry[1])
                            request["candidates"][channel] = {
                                "maximum_repeated_line_count": count,
                                "line_sha256": hashlib.sha256(item.encode()).hexdigest(),
                                "preview": item[:160],
                            }
    problems = ["repetition_guard_triggered"] if guard_stops else []
    if native_argument_rejections:
        problems.append("invalid_native_tool_arguments")
    if any(request["finish_reason"] in {"length", "max_tokens"} for request in requests.values()):
        problems.append("generation_length_termination")
    for request in requests.values():
        cancelled = False
        for item in cancellations:
            monotonic = all(isinstance(value, (int, float)) for value in
                            (item.get("monotonic"), request["started_monotonic"]))
            instant = item["monotonic"] if monotonic else item["time"]
            started = request["started_monotonic"] if monotonic else request["started"]
            closed = request["closed_monotonic"] if monotonic else request["closed"]
            failed = request["error_monotonic"] if monotonic else request["error_time"]
            cancelled |= (instant >= started and (closed is None or closed >= instant)
                          and (failed is None or failed >= instant))
        status = request["http_status"]
        error = request["transport_error"]
        if type(status) is int and status >= 400:
            transport_status = "rejected"
            problems.append("generation_http_rejection")
        elif request["stream_error"]:
            transport_status = "failed"
            problems.append("generation_stream_error")
        elif cancelled and not request["finish_reason"]:
            transport_status = "cancelled"
        elif error:
            transport_status = "timed_out" if "Timeout" in error else "failed"
            problems.append("generation_transport_" + transport_status)
        elif request["finish_reason"]:
            transport_status = "completed"
            if request["finish_reason"] not in {"stop", "tool_calls", "length", "max_tokens"}:
                transport_status = "failed"
                problems.append("generation_terminal_reason_rejected")
        else:
            transport_status = "incomplete"
            problems.append("generation_finish_unobserved")
        request["transport_status"] = transport_status
    problems = list(dict.fromkeys(problems))
    delays: list[float] = []
    for cancellation in cancellations:
        for request in requests.values():
            monotonic = isinstance(cancellation.get("monotonic"), (int, float)) and isinstance(
                request.get("started_monotonic"), (int, float),
            )
            cancelled_at = cancellation["monotonic"] if monotonic else cancellation["time"]
            started_at = request["started_monotonic"] if monotonic else request["started"]
            if started_at > cancelled_at:
                continue
            for field in ("last_stream", "closed"):
                observed = request[field + "_monotonic"] if monotonic else request[field]
                if observed is not None and observed >= cancelled_at:
                    delays.append(observed - cancelled_at)
    if delays and max(delays) > 2:
        problems.append("generation_cancellation_delayed")
    candidates = [{"request_id": key, "channel": channel, **value}
                  for key, request in requests.items() for channel, value in request["candidates"].items()]
    external = any(item["origin"] == "external" for item in cancellations)
    native_calls = [{"request_id": key, "index": index, "tool": argument["name"],
                     "characters": argument["characters"], "arguments_sha256": argument["sha256"].hexdigest(),
                     "delimiter_markers_present": argument["delimiter_markers_present"]}
                    for key, request in requests.items() for index, argument in request["tool_arguments"].items()]
    duplicates = Counter((call["tool"], call["arguments_sha256"]) for call in native_calls)
    raw_repetitions = [{"tool": name, "arguments_sha256": digest, "count": count, "review_only": True}
                       for (name, digest), count in duplicates.items() if count > 1]
    empty_responses = sum(request["finish_reason"] == "stop" and not request["characters"]["content"]
                          and not request["tool_arguments"] for request in requests.values())
    workflow_reasons = ["repeated_progressive_continuation"] if continuation_extensions > 2 else []
    if any(call["delimiter_markers_present"] for call in native_calls):
        workflow_reasons.append("native_argument_delimiter_markers")
    if raw_repetitions:
        workflow_reasons.append("identical_native_arguments_repeated")
    if empty_responses > 2:
        workflow_reasons.append("repeated_empty_final_responses")
    return {
        "schema_version": 1, "model_requests": len(requests), "problems": problems,
        "repetition_candidates": candidates, "guard_stops": guard_stops,
        "cancellations": cancellations, "external_intervention": external,
        "maximum_client_cancellation_delay_seconds": round(max(delays), 3) if delays else None,
        "status": "problem" if problems else "review_required" if candidates else "clean",
        "requests": [{"request_id": key, **{field: request[field] for field in
                     ("started", "last_stream", "closed", "started_monotonic", "last_stream_monotonic",
                      "closed_monotonic", "finish_reason", "characters", "http_status",
                      "transport_status", "transport_error", "error_time", "error_monotonic")}}
                     for key, request in requests.items()],
        "backend_generation_stop_independently_observed": False,
        "workflow": {
            "native_argument_rejected_batches": native_argument_rejections,
            "continuation_extensions": continuation_extensions,
            "review_reasons": workflow_reasons, "native_tool_calls": native_calls,
            "raw_argument_repetitions": raw_repetitions, "empty_final_responses": empty_responses,
        },
    }


def workflow_quality(observed: dict[str, Any], turn: Any) -> dict[str, Any]:
    """Keep workflow evidence separate from generation quality and fixture errors."""
    reasons = list(observed.get("generation_quality", {}).get("workflow", {}).get("review_reasons", []))
    expected_errors = {check.expected["tool"] for check in turn.checks if check.kind == "failure_report"}
    tool_errors = []
    for row in observed.get("tools", []):
        if row.get("execution_kind") == "parallel_result_consumption":
            continue
        payload = row.get("result") or {}
        structured = (payload.get("structured_content") or {}) if isinstance(payload, dict) else {}
        structured = structured if isinstance(structured, dict) else {}
        nested = structured.get("result") or {}
        arguments = row.get("arguments") or {}
        arguments = arguments if isinstance(arguments, dict) else {}
        protocol_error = isinstance(nested, dict) and nested.get("isError") is True
        failed = (isinstance(payload, dict) and payload.get("is_error") is True) or protocol_error
        expected_fixture_error = (row.get("name") == "call_project_mcp_tool" and protocol_error
                                  and arguments.get("tool_name") in expected_errors)
        if failed and not expected_fixture_error:
            tool_errors.append({"tool": row.get("name"), "status": structured.get("status", "protocol_error")})
    if tool_errors:
        reasons.append("recovered_or_rejected_tool_attempts")
    answer = observed.get("assistant", "")
    completion_unverified = bool(re.search(r"(?im)^\s*\[Completion status:\s*(?:partial|unverified)\]", answer))
    behavior_unverified = "[Behavior status: unverified]" in answer or completion_unverified
    if behavior_unverified:
        reasons.append("code_behavior_not_runtime_verified")
    tool_rows = observed.get("tools", [])
    return {"review_reasons": list(dict.fromkeys(reasons)), "tool_errors": tool_errors,
            "behavior_unverified": behavior_unverified,
            "completion_unverified": completion_unverified,
            "host_call_attempts": sum(row.get("execution_kind") != "parallel_result_consumption" for row in tool_rows),
            "parallel_result_consumptions": sum(row.get("execution_kind") == "parallel_result_consumption" for row in tool_rows)}


def whitespace_failure_categories(root: Path, turn: Any, issues: list[str]) -> list[str]:
    """Describe byte differences without weakening any independent task oracle."""
    categories = []
    for check in turn.checks:
        if check.kind != "text" or f"postcondition_failed:text:{check.path}" not in issues:
            continue
        try:
            from followup_matrix_cases import fixture_path

            actual = fixture_path(root, check.path).read_text()
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(check.expected, str) and re.sub(r"\s+", " ", actual).strip() == re.sub(r"\s+", " ", check.expected).strip():
            categories.append("whitespace_only_text_mismatch:" + check.path)
    return categories


def install_private_memory(base: Any, parent: Path, separate_workspaces: bool = False) -> None:
    from complex_smoke_runtime import install_private_storage

    # Keep the legacy call signature; every suite now isolates both storage types.
    install_private_storage(base, parent)


class WorkerInterrupted(Exception):
    """The coordinator requested cancellation, not a model/task failure."""


def worker_stop_request(output: Path, owner: str) -> dict[str, Any] | None:
    from evaluation.campaign_observation import read_json

    path = output / "worker_stop_request.json"
    if not path.exists():
        return None
    try:
        request = read_json(path)
    except json.JSONDecodeError:
        # An exclusive writer can still be flushing its small immutable request.
        return None
    launch = read_json(output / "launch.json")
    if request.get("owner") != owner or launch.get("owner") != owner:
        raise RuntimeError("Worker stop ownership differs from its launch receipt")
    if request.get("reason") not in {"coordinator_stop", "evaluation_timeout", "thermal_abort", "coordinator_exception"}:
        raise RuntimeError("Unknown evaluator stop reason")
    return request


class CooperativeWorkerStop:
    """Poll only the attempt-owned request and invoke the normal cancellation API once."""

    def __init__(self, output: Path, owner: str, runner: Any, gui: Any, observer: Any) -> None:
        self.output, self.owner, self.runner, self.gui, self.observer = output, owner, runner, gui, observer
        self.request: dict[str, Any] | None = None
        self.failure: BaseException | None = None
        self.timer = None
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.thread = None
        if gui is not None:
            self.timer = gui.root.after(50, self._poll_gui)
        else:
            self.thread = threading.Thread(target=self._watch, name="qubitz-evaluator-stop", daemon=True)
            self.thread.start()

    def poll(self) -> None:
        if self.request is not None or self.failure is not None:
            return
        request = worker_stop_request(self.output, self.owner)
        if request is None:
            return
        self.request = request
        try:
            self.observer.emit("worker_stop_observed", request=request)
        except EvidenceRecordingError:
            pass
        hook = getattr(self.gui, "_smoke_before_cancel", None)
        if callable(hook):
            hook("coordinator_stop")
        from runtime.cancellation import request_cancellation

        request_cancellation(self.runner, request["reason"])
        from complex_smoke_runtime import _exclusive_json

        _exclusive_json(self.output / "worker_stop_ack.json", {
            "owner": self.owner, "request": request, "time": stamp(), "pid": os.getpid(),
            "cancellation_requested": True,
        })

    def _safe_poll(self) -> None:
        with self.lock:
            try:
                self.poll()
            except BaseException as exc:  # noqa: BLE001 - surface Tk/thread callback failures at the next checkpoint
                self.failure = exc

    def _poll_gui(self) -> None:
        self.timer = None
        self._safe_poll()
        if not self.closed.is_set() and self.request is None and self.failure is None:
            self.timer = self.gui.root.after(50, self._poll_gui)

    def _watch(self) -> None:
        while not self.closed.wait(0.05):
            self._safe_poll()
            if self.request is not None or self.failure is not None:
                return

    def checkpoint(self) -> None:
        self._safe_poll()
        if self.failure is not None:
            raise RuntimeError("Cooperative worker cancellation failed") from self.failure
        if self.request is not None:
            raise WorkerInterrupted(self.request["reason"])

    def close(self) -> None:
        self.closed.set()
        if self.timer is not None and gui_root_exists(self.gui):
            self.gui.root.after_cancel(self.timer)
        if self.thread is not None:
            self.thread.join(timeout=2)


def wait_for_task_idle(runner: Any, gui: Any, *, timeout: float = 120.0) -> None:
    """Never reset a runtime concurrently with its still-running foreground task."""
    if gui is None or not gui.busy:
        return
    from runtime.cancellation import request_cancellation

    if not getattr(getattr(runner, "_cancel_event", None), "is_set", lambda: False)():
        request_cancellation(runner, "shutdown")
    deadline = time.monotonic() + timeout
    while gui.busy:
        if not gui_root_exists(gui):
            raise RuntimeError("GUI closed while its foreground task is still active")
        gui.root.update()
        if time.monotonic() >= deadline:
            raise TimeoutError("Foreground cancellation did not settle; runtime shutdown is unverified")
        time.sleep(0.02)


def wait_for_worker_exit(process: Any, output: Path, owner: str, reason: str | None,
                         *, timeout: float = 300.0) -> dict[str, Any]:
    """A cooperative request is not process-exit evidence; always wait on the original Popen."""
    from complex_smoke_runtime import _exclusive_json

    request = None
    if reason is not None and process.poll() is None:
        from evaluation.campaign_observation import read_json

        if read_json(output / "launch.json").get("owner") != owner:
            raise RuntimeError("Coordinator cannot cancel a worker with a different launch owner")
        request = {"owner": owner, "reason": reason, "time": stamp()}
        _exclusive_json(output / "worker_stop_request.json", request)
    try:
        code = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        receipt = {"pid": process.pid, "actual_wait": False, "status": "unresolved_do_not_retry",
                   "stop_request": request, "timeout_seconds": timeout}
        _exclusive_json(output / "worker_wait.json", receipt)
        raise RuntimeError("Worker did not exit after cooperative cancellation; no force-stop or retry permitted") from None
    receipt = {"pid": process.pid, "actual_wait": True, "exit_code": code, "stop_request": request}
    _exclusive_json(output / "worker_wait.json", receipt)
    return receipt


def generation_cancel_evidence(path: Path, turn: int, state: dict[str, Any]) -> dict[str, Any] | None:
    """Retry only transient read failures, from the last complete committed row."""
    import errno

    try:
        result = _generation_cancel_evidence(path, turn, state)
    except OSError as exc:
        if exc.errno not in {errno.ENODATA, errno.EAGAIN, errno.EINTR}:
            raise
        failures = state.get("transient_read_failures", 0) + 1
        state["transient_read_failures"] = failures
        if failures > 3:
            raise
        return None
    state["transient_read_failures"] = 0
    return result


def _generation_cancel_evidence(path: Path, turn: int, state: dict[str, Any]) -> dict[str, Any] | None:
    """Consume only complete new capture rows; never cancel an MCP wait by inference."""
    if not path.exists():
        return None
    offset = state.get("offset", 0)
    if path.stat().st_size < offset:
        raise RuntimeError("Generation-cancel evidence shrank; cancellation authority is unknown")
    requests = state.setdefault("requests", {})
    with path.open("rb") as stream:
        stream.seek(offset)
        while line := stream.readline():
            if not line.endswith(b"\n"):
                break
            row = json.loads(line)
            state["offset"] = stream.tell()
            if row.get("turn") != turn:
                continue
            key = row.get("request_id")
            if row.get("kind") == "http_request":
                requests.clear()
                requests[key] = {"request_id": key, "characters": 0, "finished": False}
            elif key in requests and row.get("kind") == "http_stream_line":
                raw = row.get("line", "")
                if not raw.startswith("data:"):
                    continue
                if raw[5:].strip() == "[DONE]":
                    requests[key]["finished"] = True
                    continue
                event = json.loads(raw[5:].strip())
                if event.get("error"):
                    requests[key]["finished"] = True
                for choice in event.get("choices", []):
                    requests[key]["finished"] |= bool(choice.get("finish_reason"))
                    delta = choice.get("delta") or {}
                    requests[key]["characters"] += sum(len(delta.get(channel) or "")
                                                       for channel in ("content", "reasoning_content"))
            elif key in requests and row.get("kind") in {"http_stream_close", "http_stream_error", "http_transport_error"}:
                requests[key]["finished"] = True
    return next((dict(item) for item in requests.values()
                 if not item["finished"] and item["characters"] >= 64), None)


def intervene(turn: Any, runner: Any, gui: Any, smoke: Any, root: Path, result: dict[str, Any]) -> None:
    """Use the real public steering/cancel surface while a real MCP call waits."""
    if result.get("attempted"):
        return
    if getattr(turn, "cancel_on_generation", False):
        if not result.get("generation_evidence"):
            return
    elif not (root / "waiting.json").exists():
        return
    result["attempted"] = True
    result["time"] = time.time()
    try:
        if turn.cancel:
            result["turn_id"] = getattr(runner._tool_permission_broker, "turn_id", "")
            if gui is not None:
                gui._cancel_active_prompt()
            else:
                from runtime.cancellation import request_cancellation

                request_cancellation(runner, "fixture")
            result["accepted"] = getattr(runner, "_cancel_source", "") == "user" and runner._cancel_event.is_set()
        elif gui is not None:
            start = len(gui._smoke_events)
            gui.prompt_box.delete("1.0", "end")
            gui.prompt_box.insert("1.0", "/btw " + turn.steering)
            gui.send_prompt()
            result["accepted"] = any(
                role in {"steer", "btw"} and str(message).strip() == str(turn.steering).strip()
                for _, role, message in gui._smoke_events[start:]
            )
        else:
            result["accepted"] = bool(runner.submit_side_note(turn.steering))
    except Exception:  # noqa: BLE001 - record failures on the real user-control surface
        result["exception"] = traceback.format_exc()
        result["accepted"] = False
    finally:
        # Release even rejected updates; the oracle still fails their acceptance.
        if not getattr(turn, "cancel_on_generation", False):
            write_fixture(root, ".cache/followup_controls/release.flag", "release\n")


def gui_root_exists(gui: Any) -> bool:
    import tkinter

    try:
        return bool(gui.root.winfo_exists())
    except tkinter.TclError as exc:
        if "application has been destroyed" in str(exc) or "invalid command name" in str(exc):
            return False
        raise


def destroy_gui_once(gui: Any) -> None:
    import tkinter

    if gui_root_exists(gui):
        try:
            gui.root.destroy()
        except tkinter.TclError:
            if gui_root_exists(gui):
                raise


def guard_variant_selection(gui: Any, variant: str, observer: Observer) -> dict[str, Any]:
    """Keep this evaluator-owned GUI on its contracted variant, without changing product classes."""
    selector = gui.variant_selector
    selection = gui.variant_selector_var
    menu = gui._qubitz_variant_menu
    if selection.get() != variant or gui._qubitz_variant_switch_in_progress:
        raise RuntimeError("GUI variant differs from the fixed evaluation contract")
    end = menu.index("end")
    entries = [(index, str(menu.entrycget(index, "label")))
               for index in range(int(end) + 1) if menu.type(index) == "radiobutton"] if end is not None else []
    if not entries or variant not in {name for _, name in entries}:
        raise RuntimeError("Cannot guard the fixed evaluation variant selector")

    def reject_switch(filename: str) -> None:
        selection.set(variant)
        if filename != variant:
            observer.emit("variant_switch_rejected", requested_variant=filename, fixed_variant=variant,
                          reason="fixed_evaluation_contract")

    # Rebind both entry commands and the direct callback before presenting the GUI.
    gui._qubitz_select_variant = reject_switch
    selector.configure(state="disabled")
    for index, filename in entries:
        menu.entryconfigure(index, state="disabled", command=lambda name=filename: reject_switch(name))
    receipt = {"fixed_variant": variant, "locked_entries": len(entries),
               "selector_state": str(selector.cget("state")), "scope": "evaluator_owned_gui_only"}
    observer.emit("variant_selection_locked", **receipt)
    return receipt


def shutdown_runtime_responsively(runner: Any, gui: Any, observer: Any, *, timeout: float = 120.0) -> None:
    """Keep the real Tk watchdog running while the existing owned shutdown completes."""
    if gui is None:
        runner.reset_runtime()
        return
    finished = threading.Event()
    failure: list[BaseException] = []

    def cleanup() -> None:
        try:
            runner.reset_runtime()
        except BaseException as exc:  # noqa: BLE001 - Propagate cleanup failures after pumping Tk.
            failure.append(exc)
        finally:
            finished.set()

    def record(kind: str, **data: Any) -> None:
        try:
            observer.emit(kind, **data)
        except EvidenceRecordingError:
            # The separate failure receipt is authoritative; cleanup must still run.
            pass

    record("runtime_cleanup_start")
    threading.Thread(target=cleanup, name="qubitz-evaluator-owned-cleanup", daemon=True).start()
    deadline = time.monotonic() + timeout
    while not finished.is_set():
        if gui_root_exists(gui):
            gui.root.update()
        if time.monotonic() >= deadline:
            record("runtime_cleanup_timeout", timeout=timeout)
            raise TimeoutError("Owned runtime shutdown did not finish; no replacement workload may start")
        finished.wait(0.02)
    record("runtime_cleanup_end", succeeded=not failure)
    if failure:
        raise failure[0]


def execute_turn(turn: Any, runner: Any, gui: Any, smoke: Any, root: Path, observer: Observer,
                 timeout: float, *, base: Any = None, startup_allowance: float = 0) -> dict[str, Any]:
    from evaluation.phase_budget import TurnBudget

    budget = TurnBudget(timeout, startup_allowance)
    observer.turn_budget = budget
    budget_saved = [0.0]

    def checkpoint_budget():
        now = time.monotonic()
        if now - budget_saved[0] >= 1:
            try:
                save_json(observer.output / "turn_progress.json", {"turn": observer.turn, **budget.snapshot()})
            except OSError:
                result["evaluation_control_failure"] = {"error": "Phase progress publication failed", "traceback": traceback.format_exc()}
                result["evaluation_cancel_requested"] = True
                result["evaluation_cancel_origin"] = "evaluation_control_error"
                from runtime.cancellation import request_cancellation

                request_cancellation(runner, "evaluation_control_error")
                raise
            budget_saved[0] = now

    deadline_stop = threading.Event()
    deadline_thread = None
    started = time.monotonic()
    result: dict[str, Any] = {"prompt": turn.prompt, "intervention": {}, "completed": False}
    audit = root / "mcp_audit.jsonl"
    result["mcp_audit_start"] = len(audit.read_text(encoding="utf-8").splitlines()) if audit.exists() else 0
    if gui is not None:
        # Send re-applies the GUI selector; changing only the runner would be undone.
        gui.access_mode_var.set({"full": "Full Access", "read_only": "Read-only", "plan": "Plan Mode"}[turn.access])
        gui._apply_access_mode()
    else:
        runner.set_access_mode(turn.access)
    process_start = len(getattr(observer, "processes", []))
    timer = None
    event_path = observer.output / "events.jsonl"
    event_offset = event_path.stat().st_size if event_path.exists() else 0
    original_cancel = getattr(runner, "request_cancel", None)
    previous_cancel_observer = getattr(gui, "_smoke_before_cancel", None) if gui is not None else None
    previous_snapshot = getattr(gui, "_smoke_snapshot_runner", None) if gui is not None else None
    structured_fixture = None
    previous_error_identity = getattr(observer, "structured_error_identity", None)

    def evaluation_cancel_origin(origin: str) -> None:
        result["evaluation_cancel_requested"] = True
        result["evaluation_cancel_origin"] = origin

    if gui is not None:
        gui._smoke_before_cancel = evaluation_cancel_origin
        gui._smoke_snapshot_runner = lambda: runner_snapshot(runner)
        gui._smoke_turn_budget = budget
        gui._smoke_budget_checkpoint = checkpoint_budget

    def observed_cancel(origin: str = "user") -> None:
        origin = (result.get("evaluation_cancel_origin", "evaluator_timeout") if result.get("evaluation_cancel_requested") else
                  "fixture" if turn.cancel and result["intervention"].get("attempted") else origin)
        try:
            observer.emit("cancel_request", origin=origin)
        finally:
            from types import SimpleNamespace

            from runtime.cancellation import request_cancellation

            request_cancellation(SimpleNamespace(request_cancel=original_cancel), origin)

    if callable(original_cancel):
        runner.request_cancel = observed_cancel
    stop = threading.Event()
    monitor = None
    generation_cancel_state = {"offset": event_offset}

    def check_generation_trigger() -> None:
        if getattr(turn, "cancel_on_generation", False) and not result["intervention"].get("attempted"):
            proof = generation_cancel_evidence(event_path, observer.turn, generation_cancel_state)
            if proof:
                result["intervention"]["generation_evidence"] = proof

    def control_step() -> bool:
        submitted_run = getattr(gui, "_smoke_submitted_run_id", None) if gui is not None else None
        if submitted_run is not None and submitted_run != getattr(gui, "_active_gui_run_id", None):
            result["external_input"] = True
            return False  # Never direct evaluator cancellation at a later user-submitted task.
        try:
            check_generation_trigger()
            intervene(turn, runner, gui, smoke, root, result["intervention"])
        except Exception as exc:  # noqa: BLE001 - A control failure is an infrastructure fault, not a silent Tk callback.
            failure = {"exception_type": type(exc).__name__, "error": str(exc), "traceback": traceback.format_exc()}
            result["evaluation_control_failure"] = failure
            try:
                observer.emit("evaluation_control_error", **failure)
            finally:
                result["evaluation_cancel_requested"] = True
                result["evaluation_cancel_origin"] = "evaluation_control_error"
                from runtime.cancellation import request_cancellation

                request_cancellation(runner, "evaluation_control_error")
            return False
        return not stop.is_set() and not result["intervention"].get("attempted")

    if turn.steering or turn.cancel:
        if gui is not None:
            def poll() -> None:
                nonlocal timer
                if control_step():
                    timer = gui.root.after(50, poll)

            timer = gui.root.after(50, poll)
        else:
            def watch() -> None:
                while not stop.wait(0.05):
                    if not control_step():
                        return

            monitor = threading.Thread(target=watch, daemon=True)
            monitor.start()
    try:
        if getattr(turn, "structured_error", False):
            from evaluation.structured_failure_fixture import StructuredFailureFixture

            if gui is None:
                raise RuntimeError("Structured error follow-up acceptance requires the same visible GUI")
            if base is None:
                raise RuntimeError("Structured error fixture requires the actual worker runtime base")
            structured_fixture = StructuredFailureFixture(runner, base, root, observer, gui)
            structured_fixture.install()
        if gui is not None:
            result.update(smoke._send_prompt(gui, turn.prompt, timeout))
            if not gui_root_exists(gui) and not result.get("completed"):
                result.update(gui_closed=True, timed_out=False, interruption="gui_closed")
        else:
            def timed_cancel() -> None:
                result["evaluation_cancel_requested"] = True
                result["evaluation_cancel_origin"] = "evaluator_timeout"
                from runtime.cancellation import request_cancellation

                request_cancellation(runner, "evaluator_timeout")

            def watch_deadline():
                while not deadline_stop.wait(0.05):
                    checkpoint_budget()
                    if budget.expired():
                        timed_cancel()
                        return

            deadline_thread = threading.Thread(target=watch_deadline, daemon=True)
            deadline_thread.start()
            result["assistant"] = runner.run_sync(turn.prompt, observer.callback)
            result["completed"] = True
            result["timed_out"] = budget.expired()
    except Exception:  # noqa: BLE001 - retain every variant exception as observed evidence
        result["exception"] = traceback.format_exc()
        if gui is not None and not gui_root_exists(gui):
            result.update(gui_closed=True, timed_out=False, interruption="gui_closed")
    finally:
        deadline_stop.set()
        if deadline_thread is not None:
            deadline_thread.join(timeout=2)
        result["phase_budget"] = budget.snapshot()
        if result["phase_budget"]["expired"] and not result.get("completed"):
            result["timed_out"] = True
        observer.turn_budget = None
        if gui is not None:
            gui._smoke_turn_budget = None
            gui._smoke_budget_checkpoint = None
        if structured_fixture is not None:
            structured_fixture.restore()
            result["structured_failure_fixture"] = copy.deepcopy(structured_fixture.state)
            observer.structured_error_identity = {
                "identity": structured_fixture.state["identity"],
                "turn_id": (getattr(runner, "_last_turn_control", {}) or {}).get("turn_id"),
            }
        elif previous_error_identity is not None:
            from evaluation.structured_failure_fixture import gui_identity

            result["same_gui_error_followup"] = {
                "original_identity": previous_error_identity["identity"],
                "current_identity": gui_identity(runner, gui),
                "error_turn_id": previous_error_identity["turn_id"],
            }
        stop.set()
        if monitor is not None:
            monitor.join(timeout=2)
        if timer is not None:
            if gui is not None:
                if gui_root_exists(gui):
                    gui.root.after_cancel(timer)
            else:
                timer.cancel()
        result["elapsed_seconds"] = time.monotonic() - started
        result["turn"] = observer.turn
        result["tools"] = copy.deepcopy([row for row in observer.tools if row["turn"] == observer.turn])
        result["processes"] = copy.deepcopy(getattr(observer, "processes", [])[process_start:])
        observed_runtime = runner_snapshot(runner)
        result["runtime"] = result.get("submitted_runtime") or observed_runtime
        result["request_failure"] = copy.deepcopy(observed_runtime.get("request_failure"))
        if result.get("submitted_runtime_error"):
            result["evaluation_control_failure"] = result["submitted_runtime_error"]
        if result.get("external_input"):
            result["runtime_at_observation"] = observed_runtime
        if callable(original_cancel):
            runner.request_cancel = original_cancel
        if gui is not None:
            if previous_snapshot is None:
                del gui._smoke_snapshot_runner
            else:
                gui._smoke_snapshot_runner = previous_snapshot
            if previous_cancel_observer is None:
                del gui._smoke_before_cancel
            else:
                gui._smoke_before_cancel = previous_cancel_observer
        if getattr(observer, "recording_failure", None):
            result["evidence_recording_failure"] = copy.deepcopy(observer.recording_failure)
            result["generation_quality"] = {"status": "incomplete_capture", "problems": None,
                                            "model_requests": None, "capture_complete": False}
        elif event_path.exists():
            result["generation_quality"] = generation_quality(event_path, observer.turn, event_offset)
        control = result["runtime"].get("turn_control") or {}
        unexpected_cancel = (
            result["runtime"].get("cancel_source") == "user" or bool(control.get("cancel_requested"))
        ) and not turn.cancel and not result.get("timed_out") and not result.get("evaluation_cancel_requested")
        result["external_intervention"] = bool(result.get("external_input") or unexpected_cancel or
            (result.get("generation_quality", {}).get("external_intervention") and not result.get("timed_out")))
        if result.get("external_input") and "generation_quality" in result:
            result["generation_quality"]["external_intervention"] = True
            result["generation_quality"]["capture_scope"] = "externally_contaminated_gui_runs"
        result["warnings"] = [row for row in observer.events if row["turn"] == observer.turn and
                              row.get("role") in {"warning", "error"}]
        statuses = [row["message"] for row in observer.events if row["turn"] == observer.turn and
                    row.get("role") == "status"]
        parse_timings = getattr(smoke, "_parse_phase_timings", None)
        result["phase_timings"] = parse_timings(statuses) if callable(parse_timings) else {}
    return result


def collect_logs(observer: Observer) -> list[dict[str, Any]]:
    from evaluation.managed_server_evidence import capture as capture_managed_logs

    for identity in observer.owned_windows_servers:
        observer.capture_owned_server_log(identity, "worker_finalization")
    collected = list(observer.server_log_captures.values())
    owned_sources = set()
    managed_logs = capture_managed_logs(observer.tools, observer.output, ROOT)
    collected.extend(managed_logs)
    for row in managed_logs:
        if row.get("source"):
            owned_sources.add(Path(row["source"]).resolve())
    for row in observer.owned_windows_servers.values():
        try:
            owned_sources.add(Path(row["log_path"]).resolve())
        except (OSError, RuntimeError):
            pass
    candidates = set()
    for row in observer.events:
        message = str(row.get("message", ""))
        prefix = "llama.cpp server log: "
        if message.startswith(prefix):
            candidates.add(Path(message.removeprefix(prefix).strip()))
    def collect_paths(payload: Any) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"log_path", "stderr_log", "stdout_log", "log_file"} and isinstance(value, str):
                    candidates.add(Path(value))
                collect_paths(value)
        elif isinstance(payload, list):
            for value in payload:
                collect_paths(value)

    collect_paths(observer.tools)
    for index, path in enumerate(sorted(candidates), start=len(collected)):
        try:
            if path.resolve() in owned_sources:
                continue
            if path.resolve().is_relative_to(ROOT / ".cache") and path.is_file():
                destination = observer.output / f"server_{index:03}.log"
                destination.write_bytes(path.read_bytes())
                collected.append({"source": str(path), "capture": str(destination), "scope": "complete_log_may_include_earlier_entries"})
        except (OSError, RuntimeError) as exc:
            collected.append({"source": str(path), "not_captured": repr(exc)})
    return collected


def owned_server_logs_complete(observer: Observer, logs: list[dict[str, Any]]) -> bool:
    from evaluation.managed_server_evidence import complete as managed_logs_complete

    captured = {tuple(row["identity"]) for row in logs
                if row.get("scope") == "owned_server_log" and row.get("capture") and not row.get("not_captured")}
    return captured == set(observer.owned_windows_servers) and managed_logs_complete(observer.tools, logs)


def select_workspace(gui: Any, base: Any, smoke: Any, roots: dict[str, Path], active: str,
                     target: str, number: int, guarded: bool = False) -> dict[str, Any]:
    before = {key: workspace_manifest(root) for key, root in roots.items()} if guarded else {}
    receipt = smoke._switch_workspace(gui, base, roots[target])
    if Path(gui.config.workspace).resolve() != roots[target].resolve():
        raise RuntimeError("GUI workspace switch failed; follow-up not submitted")
    receipt.update(from_workspace=active, to_workspace=target, before_turn=number)
    if guarded:
        after = {key: workspace_manifest(root) for key, root in roots.items()}
        receipt["mutations"] = {key: changed_paths(before[key], after[key]) for key in roots}
        if any(receipt["mutations"].values()) or not receipt["runtime_preserved"]:
            raise RuntimeError("GUI workspace change mutated fixtures or changed the product runtime root")
    return receipt


def child(args: argparse.Namespace) -> int:
    from complex_smoke_runtime import propagate_owner

    import tmp_smoke_variant as smoke

    output = validate_child(args.output, args.owner)
    suite = getattr(args, "suite", "original-v1")
    expanded = suite_module(suite)
    case = selected_scenarios([args.scenario], suite)[0]
    report: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "variant": args.variant, "scenario": case.name,
                              "variant_tier": args.tier,
                              "started": stamp(), "status": "harness_error", "turns": [], "issues": [],
                              "limitations": [*LIMITATIONS, "GUI not exercised in headless mode." if not args.gui else
                                               "Tk transcript/events/diagnostics captured; pixels are not independently visually inspected."]}
    if case.category == "error_containment":
        report["evaluation_axes"] = {"injected_containment_turns": [1], "real_model_followup_turns": [2],
                                     "injected_turn_is_model_quality_evidence": False}
    observer = Observer(output)
    observer.processes = []
    observer.source_fetches = []
    observer.retrieval_contexts = []
    source_receipts: dict[str, Any] = {}
    propagate_owner(args.owner, observer.processes)
    gui = runner = stop_control = None
    try:
        if case.gui_only and not args.gui:
            report.update(status="blocked", issues=["scenario_requires_visible_gui_workspace_switch"])
            return STATUS_CODES["blocked"]
        roots = expanded.prepare(case, output / "fixtures") if expanded else prepare_scenario(case, output / "fixtures")
        if case.name == "solar_system_3d_followup":
            from followup_matrix_solar import LIMITATION, browser_preflight

            report["limitations"] = [item for item in report["limitations"] if item != LIMITATIONS[-1]] + [LIMITATION]
            report["browser_preflight"] = browser_preflight(output)
            if report["browser_preflight"]["status"] != "available":
                report.update(status="blocked", issues=["browser_infrastructure_unavailable"])
                report["turns"] = [
                    {"turn": number, "status": "blocked" if number == 1 else "not_reached",
                     "reason": "browser_infrastructure_unavailable"}
                    for number in range(1, len(case.turns) + 1)
                ]
                return STATUS_CODES["blocked"]
        module, _ = smoke._load_module(args.variant)
        if worker_stop_request(output, args.owner) is not None:
            raise WorkerInterrupted("coordinator_stop_before_gui")
        initial_workspace = roots["a"]
        if expanded:
            initial_workspace = output / "fixtures" / "landing"
            initial_workspace.mkdir(exist_ok=False)
            report["suite"] = suite
        config, base = smoke._build_wrapped_config(module, initial_workspace)
        # Runtime/model configuration stays ordinary; persistent evaluation storage is private.
        config.access_mode = "full"
        install_private_memory(base, output, separate_workspaces=bool(expanded))
        observer.install(base)
        if expanded:
            expanded.install_capture(module, base, observer)
        if args.gui:
            gui = base.QubitzGUI(config)
            report["gui_variant_guard"] = guard_variant_selection(gui, args.variant, observer)
            smoke._attach_event_capture(gui)
            smoke._install_messagebox_capture(gui)
            original_append = gui._append_transcript

            def append(role: str, message: str) -> None:
                try:
                    observer.callback(role, message)
                except EvidenceRecordingError:
                    # Preserve the visible error even after raw capture has failed.
                    pass
                original_append(role, message)

            gui._append_transcript = append
            smoke._present_gui(gui)
            smoke._drive(gui.root, lambda: False, 0.2)
            report["gui"] = {"mapped": bool(gui.root.winfo_ismapped()), "geometry": gui.root.winfo_geometry()}
            if not report["gui"]["mapped"]:
                report.update(status="blocked", issues=["gui_not_mapped"])
                return STATUS_CODES["blocked"]
            runner = gui.agent
        else:
            runner = base.AgentRunner(config)
        stop_control = CooperativeWorkerStop(output, args.owner, runner, gui, observer)
        stop_control.checkpoint()
        active = "landing" if expanded else "a"
        historical_baselines: dict[str, dict[str, list[dict[str, str]]]] = {}
        report["status"] = "passed"
        for number, turn in enumerate(case.turns, 1):
            observer.turn = number
            stop_control.checkpoint()
            if report["status"] != "passed":
                report["turns"].append({"turn": number, "prompt": turn.prompt, "status": "not_reached",
                                        "reason": "previous_turn_prerequisite_failed"})
                continue
            if turn.workspace != active:
                switch = select_workspace(gui, base, smoke, roots, active, turn.workspace, number, guarded=bool(expanded))
                report.setdefault("workspace_switches", []).append(switch)
                active = turn.workspace
                runner = gui.agent
                stop_control.runner = runner
            source_url = getattr(case, "source_url", "")
            if source_url and number == 1:
                availability = expanded.preflight_source(source_url)
                report["source_availability"] = [availability]
                save_json(output / "source_availability.json", report["source_availability"])
                if availability["status"] != "available":
                    report["status"] = "blocked"
                    report["issues"].append("external_source_" + availability["status"])
                    report["turns"].append({"turn": number, "prompt": turn.prompt, "status": "blocked",
                                            "reason": "independent_source_preflight_failed"})
                    continue
                gui.network_mode_var.set("Online")
                gui._apply_network_mode()
                report["source_network_mode"] = str(gui.config.network_mode)
            for name, content in turn.before.items():
                write_fixture(roots[active], name, content)
            before = {key: workspace_manifest(root) for key, root in roots.items()}
            behavior_baselines = capture_behavior_baselines(
                roots[active], turn, prior_baselines=historical_baselines.get(active, {}),
            ) if suite in {"expanded-v4", "expanded-v5", "expanded-v6"} else capture_behavior_baselines(roots[active], turn)
            if suite in {"expanded-v4", "expanded-v5", "expanded-v6"}:
                retained = historical_baselines.setdefault(active, {})
                for path, item in behavior_baselines.items():
                    snapshots = retained.setdefault(path, [])
                    snapshots.append({key: item[key] for key in ("sha256", "source_base64")})
                    retained[path] = snapshots[-6:]
            texts = expanded.snapshot_text(roots[active], turn) if expanded else {}
            observer.emit("turn_start", prompt=turn.prompt, fixture_interventions=turn.before, access=turn.access)
            allowance = args.startup_allowance if number == 1 else 0
            stop_control.checkpoint()
            observed = execute_turn(turn, runner, gui, smoke, roots[active], observer, args.timeout,
                                    base=base, startup_allowance=allowance)
            if stop_control.request is not None:
                report["turns"].append({"turn": number, "status": "interrupted", "observed": observed,
                                        "reason": stop_control.request["reason"]})
            stop_control.checkpoint()
            if expanded:
                observed["source_fetches"] = copy.deepcopy([row for row in observer.source_fetches if row["turn"] == number])
                observed["retrieval_contexts"] = copy.deepcopy([row for row in observer.retrieval_contexts if row["turn"] == number])
            observed["startup_allowance_seconds"] = allowance
            observed["workflow_quality"] = workflow_quality(observed, turn)
            after = {key: workspace_manifest(root) for key, root in roots.items()}
            if expanded:
                issues, evidence = expanded.verify(roots[active], turn, observed, before[active], after[active], texts, source_receipts,
                                                   behavior_baselines=behavior_baselines)
            else:
                issues, evidence = verify_turn(roots[active], turn, observed, before[active], after[active],
                                               behavior_baselines=behavior_baselines)
            for key in roots.keys() - {active}:
                if changed_paths(before[key], after[key]):
                    issues.append(f"inactive_workspace_mutation:{key}")
            if not observed.get("completed") or observed.get("exception") or observed.get("errors"):
                issues.append("turn_execution_error")
            if observed.get("request_failure"):
                issues.append("generation_rejected")
            if observed.get("timed_out"):
                issues.append("evaluation_timeout_not_proven_hang")
            if not turn.allow_partial and "[Completion status: partial]" in observed.get("assistant", ""):
                issues.append("partial_completion_report")
            if observed.get("external_intervention"):
                issues.append("unexpected_external_input" if observed.get("external_input") else "unexpected_user_cancellation")
            status = verification_status(issues, interrupted=bool(observed.get("gui_closed") or observed.get("external_intervention")))
            if source_url and status == "failed" and expanded.source_unavailable(observed):
                availability = expanded.preflight_source(source_url)
                report["source_availability"].append(availability)
                save_json(output / "source_availability.json", report["source_availability"])
                if availability["status"] != "available" and expanded.only_source_blockers(issues, observed, before, after):
                    status = "blocked"
                    issues.append("external_source_" + availability["status"])
            if mark_recording_failure(report, observer):
                status = "harness_error"
                issues.append("evaluator_recording_failure")
            if observed.get("evaluation_control_failure"):
                status = "harness_error"
                issues.append("evaluator_control_failure")
            report["turns"].append({"turn": number, "status": status, "issues": issues, "observed": observed,
                                    "before": before, "after": after, "verification": evidence,
                                    "behavior_baselines": behavior_baselines,
                                    "failure_categories": whitespace_failure_categories(roots[active], turn, issues)})
            report["status"] = status
            if not observer.recording_failure:
                observer.emit("turn_end", status=status, issues=issues)
            save_json(output / "result.json", report)
            if status == "interrupted":
                report["issues"].append("gui_closed_during_turn" if observed.get("gui_closed") else "unexpected_user_cancellation")
                break
    except WorkerInterrupted as exc:
        report["status"] = "interrupted"
        report["issues"].append("cooperative_worker_stop")
        report["stop_reason"] = str(exc)
    except KeyboardInterrupt:
        report["status"] = "interrupted"
        report["issues"].append("worker_interrupted")
        observer.emit("worker_interrupted")
    except Exception:  # noqa: BLE001 - publish an infrastructure receipt before worker shutdown
        report.update(status="harness_error", exception=traceback.format_exc())
        traceback.print_exc()
    finally:
        mark_recording_failure(report, observer)
        if report["status"] == "interrupted":
            recorded = {turn["turn"] for turn in report["turns"]}
            for number, turn in enumerate(case.turns, 1):
                if number not in recorded:
                    report["turns"].append({
                        "turn": number, "prompt": turn.prompt,
                        "status": "interrupted" if number == observer.turn else "not_reached",
                        "reason": "worker_interrupted_before_verification",
                    })
        if report["status"] == "passed":
            completion_issues = chain_pass_issues(report, len(case.turns), finalized=False)
            if completion_issues:
                report["status"] = "harness_error"
                report["issues"].extend(completion_issues)
        report["task_status"] = report["status"]
        save_json(output / "result.json", report)
        try:
            if runner is not None:
                wait_for_task_idle(runner, gui)
                shutdown_runtime_responsively(runner, gui, observer)
            if gui is not None:
                report["gui_events"] = gui._smoke_events
                report["messageboxes"] = getattr(gui, "_smoke_messageboxes", [])
                apply_gui_diagnostics(report, smoke._collect_gui_diagnostics(gui))
        except Exception:  # noqa: BLE001 - cleanup failure must stop the campaign, not disappear
            report["issues"].append("runtime_shutdown_error")
            report["shutdown_exception"] = traceback.format_exc()
            report["status"] = "harness_error"
        finally:
            if stop_control is not None:
                stop_control.close()
            if gui is not None:
                try:
                    destroy_gui_once(gui)
                except Exception:  # noqa: BLE001 - preserve GUI teardown failures alongside runtime cleanup evidence
                    report["issues"].append("gui_destroy_error")
                    report["gui_destroy_exception"] = traceback.format_exc()
                    report["status"] = "harness_error"
        from evaluation.managed_server_evidence import (
            finalize as finalize_managed_servers,
        )

        report["managed_server_cleanup"] = finalize_managed_servers(observer.tools, ROOT)
        for outcome in report["managed_server_cleanup"]:
            observer.emit("managed_server_cleanup_outcome", outcome=outcome)
        report["server_logs"] = collect_logs(observer)
        if not owned_server_logs_complete(observer, report["server_logs"]):
            report["issues"].append("owned_server_log_capture_failed")
            report["status"] = "harness_error"
        report["process_receipts"] = observer.processes
        mark_recording_failure(report, observer)
        report["finished"] = stamp()
        try:
            from evaluation.case_evidence import publish_manifest

            report["evidence_manifest"] = publish_manifest(
                output, report, getattr(gui, "conversation_journal", None), require_journal=bool(args.gui),
            )
            if report["evidence_manifest"]["status"] != "complete":
                report["issues"].append("case_evidence_manifest_incomplete")
                report["status"] = "harness_error"
        except (OSError, ValueError, RuntimeError):
            report["issues"].append("case_evidence_manifest_failed")
            report["evidence_manifest_exception"] = traceback.format_exc()
            report["status"] = "harness_error"
        save_json(output / "result.json", report)
    return STATUS_CODES[report["status"]]


@contextmanager
def campaign_locks():
    import msvcrt

    with ExitStack() as stack:
        for name in ("qubitz-campaign.lock", "qubitz-disposable-eval.lock", "complex-smoke.lock", "followup-matrix.lock"):
            path = ROOT / ".cache" / name
            path.parent.mkdir(exist_ok=True)
            stream = stack.enter_context(path.open("a+b"))
            stream.seek(0)
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            stack.callback(lambda handle=stream: (handle.seek(0), msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)))
        yield


def gpu_snapshot() -> dict[str, Any]:
    command = ["nvidia-smi", "--query-gpu=index,temperature.gpu,memory.used,memory.total,utilization.gpu,power.draw",
               "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
        rows = [line.split(",") for line in result.stdout.strip().splitlines()]
        return {"time": time.time(), "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr,
                "temperatures": [float(row[1].strip()) for row in rows if len(row) == 6]}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"time": time.time(), "unavailable": repr(exc), "temperatures": []}


def require_port_free() -> None:
    with socket.socket() as sock:
        sock.settimeout(1)
        if sock.connect_ex(("127.0.0.1", 8001)) == 0:
            raise RuntimeError("Port 8001 is occupied; no unrelated process will be stopped")


def require_model_servers_clear_after_cleanup(timeout: float = 12.0) -> None:
    """Wait briefly for a just-exited server's Windows process metadata to settle."""
    from smoke_create_calculator import IncompleteProcessIdentityError, model_processes

    deadline = time.monotonic() + timeout
    while True:
        try:
            servers = model_processes()
        except IncompleteProcessIdentityError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.25)
            continue
        if servers:
            raise RuntimeError("A model server remains after owned-process cleanup")
        return


def cleanup_captured_windows_servers(output: Path, owner: str) -> dict[str, Any]:
    """Use only product-captured PID, creation time and executable identities."""
    from smoke_create_calculator import server_identity, wait_for_owned_cleanup

    launch = json.loads((output / "launch.json").read_text(encoding="utf-8"))
    if launch.get("owner") != owner:
        raise RuntimeError("Cleanup owner differs from the launch receipt")
    path = output / "owned_windows_servers.json"
    owned = []
    if path.exists():
        captured = json.loads(path.read_text(encoding="utf-8"))
        if captured.get("owner") != owner or captured.get("schema_version") != SCHEMA_VERSION:
            raise RuntimeError("Invalid owned Windows server receipt")
        owned = captured.get("servers")
        if not isinstance(owned, list):
            raise RuntimeError("Invalid owned Windows server identities")
        for row in owned:
            server_identity(row)
    # An empty receipt never authorizes cleanup of a merely similar server.
    result = wait_for_owned_cleanup(owned)
    return {**result, "captured_identities": owned}


def wslg_preflight(args: argparse.Namespace) -> dict[str, Any] | None:
    """Reject degraded GUI infrastructure without launching or restarting a workload."""
    if not getattr(args, "gui", False):
        return None
    command = ["wsl.exe", "-d", args.distro, "--exec", "sh", "-lc",
               ("grep -E 'RDP backend: use_gfxredir =|rdp_allocate_shared_memory:.*(Failed|Input/output error)|WARN:COPY MODE' "
                "/mnt/wslg/weston.log | tail -n 20")]
    receipt: dict[str, Any] = {"time": stamp(), "pixels_observed": False, "automatic_restart": False}
    try:
        checked = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        lines = checked.stdout.replace("\x00", "").splitlines()
        modes = [(index, re.search(r"use_gfxredir\s*=\s*([01])", line))
                 for index, line in enumerate(lines) if "use_gfxredir" in line]
        mode = modes[-1][1] if modes else None
        ready = bool(checked.returncode == 0 and mode and mode[1] == "1")
        if ready:
            ready = not any("Input/output error" in line or "WARN:COPY MODE" in line
                            for line in lines[modes[-1][0] + 1:])
        receipt.update(status="gfxredir_ready" if ready else "degraded_or_unobservable",
                       exit_code=checked.returncode, stdout=checked.stdout[-8192:], stderr=checked.stderr[-4096:])
    except (OSError, subprocess.SubprocessError) as exc:
        ready = False
        receipt.update(status="unobservable", exception=f"{type(exc).__name__}: {exc}")
    if not ready:
        path = args.output / f"wslg_blocker_{uuid.uuid4().hex[:12]}.json"
        save_json(path, receipt)
        raise RuntimeError(f"WSLg is degraded or cannot be verified; no trajectory was launched. Receipt: {path}")
    return receipt


def run_attempt(args: argparse.Namespace, variant: str, case: Any, output: Path) -> dict[str, Any]:
    from followup_campaign_control import stop_requested
    from smoke_create_calculator import model_processes, require_idle, wsl_path

    require_idle()
    require_port_free()
    if model_processes():
        raise RuntimeError("An unowned Windows llama-server is active; refusing a contaminated run")
    gui_preflight = wslg_preflight(args)
    output.mkdir(parents=True, exist_ok=False)
    if gui_preflight is not None:
        save_json(output / "wslg_preflight.json", gui_preflight)
    owner = uuid.uuid4().hex
    save_json(output / "launch.json", {"owner": owner, "variant": variant, "scenario": case.name,
                                      "variant_tier": args.tier})
    command = [wsl_path(ROOT / ".venv/bin/python"), "-u", wsl_path(Path(__file__).resolve()),
               "--child", "--variant", variant, "--scenario", case.name, "--output", wsl_path(output),
               "--owner", owner, "--tier", args.tier, "--timeout", str(args.timeout),
               "--startup-allowance", str(args.startup_allowance)]
    if getattr(args, "suite", "original-v1") != "original-v1":
        command.extend(["--suite", args.suite])
    if args.gui:
        command.append("--gui")
    invocation = ["wsl.exe", "-d", args.distro, "--cd", wsl_path(ROOT), "env",
                  "QUBITZ_SKIP_WSLG_VISIBILITY_PROBE=1", "PYTHONUNBUFFERED=1",
                  "QUBITZ_COMPLEX_SMOKE_OWNER=" + owner, *command]
    save_json(output / "command.json", invocation)
    process = None
    timed_out = thermal_abort = interrupted = False
    cleanup = {}
    started = time.monotonic()
    # The timeout belongs only to this evaluator, never to the product.
    deadline = started + args.startup_allowance + args.timeout * len(case.turns)
    try:
        with (output / "stdout.log").open("wb") as stdout, (output / "stderr.log").open("wb") as stderr:
            process = subprocess.Popen(invocation, cwd=ROOT, stdout=stdout, stderr=stderr)
            next_sample = 0.0
            while process.poll() is None:
                if stop_requested(args.output):
                    interrupted = True
                    break
                now = time.monotonic()
                if now >= next_sample:
                    sample = gpu_snapshot()
                    with (output / "gpu.jsonl").open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(sample) + "\n")
                    if any(value >= 86.0 for value in sample["temperatures"]):
                        thermal_abort = True
                        break
                    next_sample = now + 5.0
                if now >= deadline:
                    timed_out = True
                    break
                time.sleep(0.2)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        # Let the worker cancel its task and close its Windows server before reaping it.
        cleanup_command = ["wsl.exe", "-d", args.distro, "--cd", wsl_path(ROOT),
                           wsl_path(ROOT / ".venv/bin/python"), "evaluation/complex_smoke_runtime.py", "--cleanup-owned", owner]
        try:
            if process is not None:
                reason = ("thermal_abort" if thermal_abort else "evaluation_timeout" if timed_out else
                          "coordinator_stop" if interrupted else
                          "coordinator_exception" if process.poll() is None else None)
                cleanup["worker_wait"] = wait_for_worker_exit(process, output, owner, reason)
            cleaned = subprocess.run(cleanup_command, capture_output=True, text=True, timeout=45, check=False)
            cleanup.update(exit_code=cleaned.returncode, stdout=cleaned.stdout, stderr=cleaned.stderr)
            cleanup["windows_cleanup"] = cleanup_captured_windows_servers(output, owner)
            require_idle()
            require_port_free()
            require_model_servers_clear_after_cleanup()
        except Exception:  # noqa: BLE001 - never continue with uncertain process cleanup
            cleanup["exception"] = traceback.format_exc()
        save_json(output / "cleanup.json", cleanup)
    path = output / "result.json"
    if path.exists():
        row = json.loads(path.read_text(encoding="utf-8"))
    else:
        row = {"variant": variant, "scenario": case.name, "status": "harness_error",
               "turns": [], "issues": ["missing_child_result"]}
    row.update(exit_code=process.returncode if process is not None else None,
               elapsed_seconds=time.monotonic() - started, cleanup=cleanup, output=str(output))
    if interrupted:
        row.update(child_status_at_stop=row.get("status"), child_finished_at_stop=row.get("finished"),
                   interrupted_stage="coordinator_finalization" if row.get("finished") else "active_turn")
        recorded = {item["turn"] for item in row["turns"]}
        missing = [number for number in range(1, len(case.turns) + 1) if number not in recorded]
        for number in missing:
            row["turns"].append({"turn": number, "status": "interrupted" if number == missing[0] else "not_reached",
                                 "reason": "coordinator_stop"})
    for flag, name in ((timed_out, "evaluation_timeout_not_proven_hang"), (thermal_abort, "thermal_abort_86C"),
                       (interrupted, "coordinator_interrupted")):
        if flag:
            row.setdefault("issues", []).append(name)
            row["status"] = "failed" if not interrupted else "interrupted"
    if cleanup.get("exception") or cleanup.get("exit_code") != 0:
        row.setdefault("issues", []).append("cleanup_blocked")
        row["status"] = "harness_error"
    if row.get("status") == "passed":
        completion_issues = chain_pass_issues(row, len(case.turns), finalized=True)
        if completion_issues:
            row["status"] = "harness_error"
            row.setdefault("issues", []).extend(completion_issues)
    return row


def update_counts(summary: dict[str, Any]) -> None:
    summary["counts"] = {status: sum(row["status"] == status for row in summary["runs"])
                         for status in STATUS_CODES}
    turn_statuses = ("passed", "failed", "not_reached", "harness_error")
    if summary.get("contract", {}).get("suite") in {"expanded-v2", "expanded-v3", "expanded-v4", "expanded-v5", "expanded-v6"}:
        turn_statuses += ("blocked", "interrupted")
    summary["turn_counts"] = {status: sum(turn["status"] == status for row in summary["runs"] for turn in row["turns"])
                              for status in turn_statuses}
    quality_counts = {key: 0 for key in ("passed_clean", "passed_with_generation_problems",
                     "passed_review_required", "passed_with_intervention", "passed_quality_unobserved",
                     "passed_with_workflow_review")}
    for row in summary["runs"]:
        if row["status"] != "passed":
            continue
        observed = [turn.get("observed") or {} for turn in row.get("turns", [])]
        qualities = [turn.get("generation_quality") for turn in observed]
        if any(turn.get("external_intervention") for turn in observed):
            category = "passed_with_intervention"
        elif not qualities or any(quality is None for quality in qualities):
            category = "passed_quality_unobserved"
        elif any(quality.get("problems") for quality in qualities):
            category = "passed_with_generation_problems"
        elif any(quality.get("repetition_candidates") for quality in qualities) or any(check.get("browser_receipt", {}).get("independent_visual_review") == "required"
                 for turn in row.get("turns", []) for check in turn.get("verification", [])):
            category = "passed_review_required"
        elif any(turn.get("workflow_quality", {}).get("review_reasons") for turn in observed):
            category = "passed_with_workflow_review"
        else:
            category = "passed_clean"
        quality_counts[category] += 1
    summary["quality_counts"] = quality_counts
    summary["outcome_axes"] = {
        "task": dict(summary["counts"]),
        "quality": dict(quality_counts),
        "interrupted_trajectories": summary["counts"].get("interrupted", 0),
        "workflow_review_trajectories": sum(any((turn.get("observed") or {}).get("workflow_quality", {}).get("review_reasons")
                                               for turn in row.get("turns", [])) for row in summary["runs"]),
        "cleanup": {
            "blocked": sum("cleanup_blocked" in row.get("issues", []) for row in summary["runs"]),
            "recorded_not_audited": sum(bool(row.get("cleanup")) and "cleanup_blocked" not in row.get("issues", [])
                                        for row in summary["runs"]),
            "unobserved": sum(not row.get("cleanup") for row in summary["runs"]),
        },
        "evidence_capture_incomplete": sum(any((turn.get("observed") or {}).get("evidence_recording_failure")
                                               for turn in row.get("turns", [])) for row in summary["runs"]),
        "coverage": {"recorded_trajectories": len(summary["runs"]),
                     "recorded_turn_slots": sum(summary["turn_counts"].values())},
        "acceptance_note": "Recorded coverage, task results, generation quality and cleanup are independent; "
                           "a human interruption is not an accepted full matrix or a spontaneous model failure.",
    }
    if "coverage_plan" in summary:
        summary["coverage_groups"] = grouped_results(summary["coverage_plan"], summary["runs"])


def coordinator_event(campaign_output: Path, kind: str, **data: Any) -> None:
    """Append durable lifecycle evidence; never replace attempt logs or verdicts."""
    row = {"time": stamp(), "kind": kind, "pid": os.getpid(), "executable": sys.executable,
           "argv": sys.argv, **data}
    with (campaign_output / "coordinator_events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def manifest_differences(expected: dict[str, Any], observed: dict[str, Any]) -> list[dict[str, Any]]:
    """Return exact path-level metadata differences without reading file contents."""
    return [
        {"path": path, "expected": expected.get(path), "observed": observed.get(path)}
        for path in sorted(set(expected) | set(observed))
        if expected.get(path) != observed.get(path)
    ]


def record_source_drift(
    output: Path, summary: dict[str, Any], expected: dict[str, Any], observed: dict[str, Any],
    *, boundary: str, **context: Any,
) -> list[dict[str, Any]]:
    differences = manifest_differences(expected, observed)
    evidence = {"time": stamp(), "boundary": boundary, "differences": differences, **context}
    summary["status"] = "invalid_source_drift"
    summary.setdefault("source_drift_events", []).append(evidence)
    save_json(output / "summary.json", summary)
    coordinator_event(output, "source_drift", **evidence)
    return differences


def coordinate(args: argparse.Namespace) -> int:
    try:
        return _coordinate(args)
    except BaseException:
        output = validate_output(args.output)
        if (output / MARKER).is_file():
            coordinator_event(output, "coordinator_exception", exception=traceback.format_exc())
        raise


def _coordinate(args: argparse.Namespace) -> int:
    from followup_campaign_control import process_identity, stop_requested
    from smoke_complex_tasks import protected_source_manifest
    from smoke_create_calculator import persistent_inventory

    from evaluation.followup_trajectory_selection import (
        read_selection,
        require_unchanged,
        trajectory_key,
    )

    names = variants(args.variants, args.tier)
    suite = getattr(args, "suite", "original-v1")
    cases = selected_scenarios(args.scenarios, suite)
    selection = read_selection(getattr(args, "trajectory_plan", None), names,
                               [case.name for case in cases], args.repeats, ROOT)
    selected = {trajectory_key(row) for row in selection["trajectories"]} if selection else None
    output = validate_output(args.output)
    contract = {"variants": names, "scenarios": [case.name for case in cases], "repeats": args.repeats,
                "gui": args.gui, "timeout": args.timeout, "startup_allowance": args.startup_allowance,
                "distro": args.distro, "schema_version": SCHEMA_VERSION, "variant_tier": args.tier,
                "observation_schema_version": 1}
    if suite != "original-v1":
        contract.update(suite=suite, suite_version=int(suite.rsplit("v", 1)[1]), initial_workspace_selection="normal_gui")
    if selection:
        contract["trajectory_selection"] = selection
    if args.resume:
        if not (output / MARKER).is_file():
            raise RuntimeError("Resume requires a marked follow-up campaign")
        summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
        if summary["contract"] != contract or summary["source_hashes"] != freeze_sources():
            raise RuntimeError("Checkpoint/source mismatch; resume cannot mix different campaigns")
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / MARKER).write_text("developer-only follow-up matrix\n", encoding="utf-8")
        summary = {"contract": contract, "source_hashes": freeze_sources(), "source_snapshot": protected_source_manifest(output),
                   "status": "running", "started": stamp(), "runs": [], "limitations": suite_limitations(suite),
                   "scenarios": [asdict(case) for case in cases], "coverage_plan": coverage_plan(ROOT, names),
                   "persistent_inventory": persistent_inventory()}
    if summary.get("persistent_inventory") != persistent_inventory():
        raise RuntimeError("Shared persistent inventory changed or legacy checkpoint lacks isolation evidence")
    current_snapshot = protected_source_manifest(output)
    if summary["source_snapshot"] != current_snapshot:
        differences = record_source_drift(
            output, summary, summary["source_snapshot"], current_snapshot,
            boundary="before_coordinator_start",
        )
        raise RuntimeError(f"Protected source changed; checkpoint is invalid ({len(differences)} path(s))")
    identity = process_identity(os.getpid())
    if not identity or not identity.get("creation_ticks") or not identity.get("command_line"):
        raise RuntimeError("Cannot capture exact coordinator identity; no trajectory launched")
    save_json(output / "coordinator_identity.json", identity)
    coordinator_event(output, "coordinator_start", resume=args.resume, registered_rows=len(summary["runs"]),
                      contract_sha256=hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest())
    completed = {(row["variant"], row["scenario"], row["repeat"]) for row in summary["runs"]
                 if row["status"] in {"passed", "failed", "blocked"}}
    summary["status"] = "running"
    save_json(output / "summary.json", summary)
    for name in names:
        for repeat in range(1, args.repeats + 1):
            for case in cases:
                if selected is not None and (name, case.name, repeat) not in selected:
                    continue
                require_unchanged(selection, ROOT)
                if stop_requested(output):
                    summary.update(status="paused", paused_at=stamp(), pause_reason="human_requested_stop")
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status="paused", reason="human_requested_stop")
                    return 0
                current_inventory = persistent_inventory()
                if current_inventory != summary["persistent_inventory"]:
                    raise RuntimeError("Shared memory/conversation inventory changed before trajectory launch")
                if (name, case.name, repeat) in completed:
                    continue
                current_hashes = freeze_sources()
                if summary["source_hashes"] != current_hashes:
                    differences = record_source_drift(
                        output, summary, summary["source_hashes"], current_hashes,
                        boundary="before_trajectory_launch", variant=name, scenario=case.name,
                    )
                    raise RuntimeError(f"Source changed before trajectory launch ({len(differences)} path(s))")
                attempts = 1 + sum(row["variant"] == name and row["scenario"] == case.name and row["repeat"] == repeat
                                   for row in summary["runs"])
                directory = output / name.removesuffix(".py") / f"repeat_{repeat:03}" / case.name / f"attempt_{attempts:03}"
                try:
                    coordinator_event(output, "trajectory_launch", variant=name, scenario=case.name,
                                      repeat=repeat, attempt=attempts, output=str(directory))
                    row = run_attempt(args, name, case, directory)
                except Exception:  # noqa: BLE001 - persist launch/preflight failures without blaming the variant
                    summary["status"] = "stopped_infrastructure"
                    summary.setdefault("infrastructure_errors", []).append({
                        "variant": name, "scenario": case.name, "repeat": repeat,
                        "time": stamp(), "exception": traceback.format_exc(),
                    })
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status=summary["status"],
                                      exception=summary["infrastructure_errors"][-1]["exception"])
                    print(json.dumps({"status": summary["status"], "variant": name, "scenario": case.name,
                                      "error": summary["infrastructure_errors"][-1]["exception"]}), flush=True)
                    return 2
                row.update(repeat=repeat, attempt=attempts)
                from evaluation.campaign_lifecycle import result_reference

                row["result_reference"] = result_reference(output, {**row, "output": str(directory)})
                coordinator_event(output, "trajectory_return", variant=name, scenario=case.name,
                                  repeat=repeat, attempt=attempts, status=row["status"], finished=row.get("finished"))
                summary["runs"].append(row)
                update_counts(summary)
                current_inventory = persistent_inventory()
                if current_inventory != summary["persistent_inventory"]:
                    summary.update(status="stopped_infrastructure", persistent_inventory_findings=manifest_differences(
                        summary["persistent_inventory"], current_inventory))
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status=summary["status"],
                                      reason="shared_persistent_inventory_changed")
                    return 2
                current_snapshot = protected_source_manifest(output)
                if summary["source_snapshot"] != current_snapshot:
                    differences = record_source_drift(
                        output, summary, summary["source_snapshot"], current_snapshot,
                        boundary="after_trajectory", variant=name, scenario=case.name,
                        repeat=repeat, attempt=attempts,
                    )
                    raise RuntimeError(
                        f"Protected source changed during trajectory; campaign invalidated "
                        f"({len(differences)} path(s))"
                    )
                save_json(output / "summary.json", summary)
                if stop_requested(output):
                    cleanup_blocked = row["status"] == "harness_error"
                    summary.update(status="stopped_infrastructure" if cleanup_blocked else "paused",
                                   paused_at=stamp(), pause_reason="human_requested_stop")
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status=summary["status"], reason="human_requested_stop")
                    return 2 if cleanup_blocked else 0
                if row["status"] != "passed":
                    print(json.dumps({"variant": name, "scenario": case.name, "repeat": repeat,
                                      "status": row["status"], "issues": row.get("issues", []),
                                      "failed_turns": [turn["turn"] for turn in row["turns"] if turn["status"] == "failed"]}), flush=True)
                if row["status"] in {"harness_error", "interrupted"} or "thermal_abort_86C" in row.get("issues", []):
                    summary["status"] = "stopped_infrastructure" if row["status"] != "interrupted" else "paused"
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status=summary["status"])
                    return 2
                from evaluation.generation_failures import repeated_rejection_hold

                hold = repeated_rejection_hold(summary["runs"])
                if hold is not None:
                    summary.update(status="paused", pause_reason="repeated_generation_rejection", generation_failure_hold=hold)
                    save_json(output / "summary.json", summary)
                    coordinator_event(output, "coordinator_stop", status="paused", reason="repeated_generation_rejection", hold=hold)
                    return 2
    require_unchanged(selection, ROOT)
    summary.update(status="complete", finished=stamp())
    save_json(output / "summary.json", summary)
    coordinator_event(output, "coordinator_stop", status="complete", registered_rows=len(summary["runs"]))
    return 1 if summary["counts"]["failed"] else 2 if summary["counts"]["blocked"] else 0


def main(default_tier: str = "all") -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--stop", action="store_true", help="Request exact-identity cooperative stop and durable checkpoint")
    parser.add_argument("--tier", choices=tuple(VARIANT_TIERS), default=default_tier,
                        help="All public variants by default; size-specific subsets require an explicit tier")
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--scenarios", nargs="+")
    parser.add_argument("--trajectory-plan", type=Path, help="Exact per-variant/scenario/repeat selection for a fresh output")
    parser.add_argument("--suite", choices=("original-v1", "expanded-v2", "expanded-v3", "expanded-v4", "expanded-v5", "expanded-v6", "solar-visual-v1", "mini-user-v1", "mini-user-v2"), default="original-v1",
                        help="Original definitions or the separately versioned expanded GUI suite")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--gui", action="store_true", help="Use visible Tk and normal GUI Send for every turn")
    parser.add_argument("--timeout", type=float, default=1800, help="Evaluator-only timeout per turn, seconds")
    parser.add_argument("--startup-allowance", type=float, default=7200, help="Extra child wall time for startup/download")
    parser.add_argument("--distro", default="Ubuntu")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--supervise", action="store_true", help="Own one fresh coordinator and durably wait for its actual exit")
    parser.add_argument("--control-state", type=Path, help="Outer durable state bound to the supervised output")
    parser.add_argument("--reconcile-state", type=Path, help="Reconcile a terminal state from actual wait receipts; never launch")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--variant", help=argparse.SUPPRESS)
    parser.add_argument("--scenario", help=argparse.SUPPRESS)
    parser.add_argument("--owner", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.reconcile_state is not None:
        if args.execute or args.supervise or args.stop or args.resume or args.child:
            parser.error("--reconcile-state cannot execute, supervise, stop or resume")
        from evaluation.campaign_lifecycle import reconcile_control_state
        from evaluation.followup_campaign_control import process_identity

        print(json.dumps(reconcile_control_state(args.reconcile_state, identity=process_identity), indent=2))
        return 0
    if args.stop:
        if args.supervise:
            parser.error("--stop cannot launch a supervisor")
        if args.output is None or args.execute or args.resume or args.child:
            parser.error("--stop requires an explicit --output and cannot launch or resume")
        from followup_campaign_control import stop_campaign

        print(json.dumps(stop_campaign(args.output), indent=2))
        return 0
    if args.output is None:
        tier_label = args.tier.replace("-", "_")
        prefix = f"followups_{tier_label}_" + (args.suite.removeprefix("expanded-") + "_" if args.suite != "original-v1" else "")
        args.output = ROOT / "evaluation/results" / (prefix + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    if args.repeats < 1 or args.timeout <= 0 or args.startup_allowance < 0:
        parser.error("Positive repeats/timeout and nonnegative startup allowance are required")
    if args.child:
        variants([args.variant], args.tier)
        return child(args)
    names = variants(args.variants, args.tier)
    cases = selected_scenarios(args.scenarios, args.suite)
    from evaluation.followup_trajectory_selection import read_selection

    selection = read_selection(args.trajectory_plan, names, [case.name for case in cases], args.repeats, ROOT)
    if not args.execute:
        print(json.dumps({"variants": names, "scenarios": [asdict(case) for case in cases],
                          "suite": args.suite, "variant_tier": args.tier,
                          "trajectory_count": len(selection["trajectories"]) if selection else len(names) * len(cases) * args.repeats,
                          "turn_count": sum(len(next(case for case in cases if case.name == row["scenario"]).turns)
                                            for row in selection["trajectories"]) if selection else
                                        len(names) * sum(len(case.turns) for case in cases) * args.repeats,
                          "trajectory_selection": selection,
                          "execution_started": False, "limitations": suite_limitations(args.suite),
                          "coverage_plan": coverage_plan(ROOT, names)}, indent=2))
        return 0
    if os.name != "nt":
        parser.error("Run --execute coordinator on Windows; WSL is used only for owned children")
    if args.suite in {"expanded-v2", "expanded-v3", "expanded-v4", "expanded-v5", "expanded-v6", "mini-user-v1", "mini-user-v2"} and not args.gui:
        parser.error("Expanded suites require --gui; workspace selection is part of every trajectory")
    if args.supervise:
        if args.resume:
            parser.error("Supervised launch requires a fresh output; cannot resume a stopped segment")
        from evaluation.campaign_lifecycle import coordinator_command, supervise
        from evaluation.followup_campaign_control import process_identity

        command = coordinator_command(sys.executable, Path(__file__), sys.argv[1:], args.output)
        return supervise(command, validate_output(args.output), identity=process_identity, control_state=args.control_state)
    with campaign_locks():
        return coordinate(args)


if __name__ == "__main__":
    raise SystemExit(main())
