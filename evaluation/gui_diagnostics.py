"""Read acknowledged GUI evidence in FIFO order without rewriting raw events."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GuiDiagnosticsReceipt:
    source_log_path: str | None
    events: list[dict[str, Any]]
    event_order: str
    ordering_errors: list[str]
    stall_count: int
    recovery_count: int
    recovered_stall_count: int
    unresolved_stall_count: int
    behavior_issues: list[str]
    warnings: list[str]
    read_errors: list[str]
    worker_exceptions: list[dict[str, Any]]
    request_failures: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self),
                "presentation_scope": "Tk event-consumption evidence; native presentation/input unobserved",
                "native_presentation_verified": False}


def collect_gui_diagnostics(gui: Any) -> GuiDiagnosticsReceipt:
    issues: list[str] = []
    warnings: list[str] = []
    worker_exceptions = list(getattr(gui, "_smoke_worker_exceptions", ()))
    if worker_exceptions:
        issues.append("gui_worker_exception")
    flush = getattr(gui, "_qubitz_flush_gui_log", None)
    if callable(flush):
        if not flush(timeout=2.0):
            issues.append("gui_log_flush_incomplete")
    else:
        warnings.append("gui_log_flush_unobservable")
    if getattr(gui, "_qubitz_gui_log_error", ""):
        issues.append("gui_log_write_error")
    if getattr(gui, "_qubitz_gui_log_dropped", 0):
        issues.append("gui_log_events_dropped")
    log_path = getattr(gui, "_qubitz_gui_log_path", None)
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    if log_path is None:
        issues.append("gui_diagnostics_missing")
    else:
        try:
            captured = Path(log_path).read_bytes()
            boundary = getattr(gui, "_qubitz_gui_log_flushed_bytes", None)
            if isinstance(boundary, int):
                captured = captured[:boundary]
            for number, line in enumerate(captured.decode("utf-8").splitlines(), 1):
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict):
                        raise ValueError("GUI event is not an object")  # noqa: TRY004 - preserve existing log diagnostics
                    events.append(event)
                except (ValueError, TypeError) as exc:
                    errors.append(f"Line {number}: {exc}")
        except (OSError, UnicodeError) as exc:
            errors.append(str(exc))
    if errors:
        issues.append("gui_log_read_error")
    ordering_errors: list[str] = []
    session_positions: dict[str, tuple[int, float]] = {}
    serialized_count = 0
    pending_stalls: dict[str | None, int] = {}
    recovered_stalls = 0
    # Wall clocks are correlation evidence, never an ordering key.
    for index, event in enumerate(events, 1):
        session = event.get("gui_log_session")
        sequence = event.get("sequence")
        monotonic = event.get("monotonic_seconds")
        has_ordering = any(key in event for key in ("gui_log_session", "sequence", "monotonic_seconds"))
        if has_ordering:
            valid = (isinstance(session, str) and bool(session)
                     and type(sequence) is int and sequence > 0
                     and type(monotonic) in (int, float) and 0 <= monotonic < math.inf)
            if not valid:
                ordering_errors.append(f"Event {index}: invalid session ordering metadata")
            else:
                previous_sequence, previous_monotonic = session_positions.get(session, (0, 0.0))
                if sequence != previous_sequence + 1 or monotonic < previous_monotonic:
                    ordering_errors.append(f"Event {index}: sequence gap/backstep or monotonic backstep")
                session_positions[session] = (sequence, monotonic)
                serialized_count += 1
        session_key = session if isinstance(session, str) and session else None
        pending = pending_stalls.get(session_key, 0)
        event_name = event.get("event")
        if event_name == "gui_heartbeat_stalled":
            pending_stalls[session_key] = pending + 1
        elif (event_name == "gui_heartbeat_recovered"
              or (event_name == "gui_event" and event.get("kind") in {"answer", "done"})) and pending:
            recovered_stalls += pending
            pending_stalls[session_key] = 0
    if ordering_errors:
        issues.append("gui_log_order_error")
    active_stalls = sum(pending_stalls.values())
    if active_stalls:
        issues.append("gui_heartbeat_stalled")
    if recovered_stalls:
        warnings.append("gui_heartbeat_stall_recovered")
    if any(event.get("kind") == "error" for event in events):
        issues.append("gui_logged_error")
    if not events and not issues:
        issues.append("gui_diagnostics_empty")
    return GuiDiagnosticsReceipt(
        source_log_path=str(log_path) if log_path is not None else None,
        events=events,
        event_order="serialized_fifo" if events and serialized_count == len(events) and not ordering_errors else "recorded_fifo",
        ordering_errors=ordering_errors,
        stall_count=sum(event.get("event") == "gui_heartbeat_stalled" for event in events),
        recovery_count=sum(event.get("event") == "gui_heartbeat_recovered" for event in events),
        recovered_stall_count=recovered_stalls,
        unresolved_stall_count=active_stalls,
        behavior_issues=issues, warnings=warnings, read_errors=errors,
        worker_exceptions=worker_exceptions,
        request_failures=[event["receipt"] for event in events
                          if event.get("event") == "generation_request_failure"
                          and isinstance(event.get("receipt"), dict)],
    )
