"""Known request-local generation rejection; never authorizes request replay."""
from __future__ import annotations

import hashlib
import json

from .structured_response import StructuredBackendError


class GenerationRejectedError(StructuredBackendError):
    """A positively identified format rejection, not an arbitrary server/worker error."""


def generation_rejection(error, payload: dict, *, http_status=None, request_id="", backend=None):
    if not isinstance(error, dict):
        return None
    # This protocol failure says the generated completion could not be parsed.
    # Other 500s, transport failures and unknown messages remain infrastructure-unknown.
    if (error.get("code") != 500 or error.get("type") != "server_error"
            or error.get("message") != "The model produced output that does not match the expected peg-native format"):
        return None
    receipt = {
        "schema": "qubitz_generation_rejection_v1", "scope": "request",
        "category": "completion_format_rejected", "request_id": request_id,
        "request_sha256": hashlib.sha256(json.dumps(
            payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
        ).encode()).hexdigest(),
        "http_status": http_status, "stream_error": dict(error), "backend": backend,
        "completion_accepted": False, "tool_call_accepted": False, "retry_permitted": False,
    }
    return GenerationRejectedError(
        "The backend rejected this completion's format; no call from this response was accepted or replayed.",
        receipt=receipt,
    )


def request_failure_answer(agent, error: GenerationRejectedError) -> str:
    """Preserve previous work and terminal uncertainty; do not turn rejection into success."""
    agent._last_request_failure = dict(error.receipt)
    broker = getattr(agent, "_tool_permission_broker", None)
    evidence = broker._current_turn_evidence() if broker is not None else []
    committed = sorted({str(p) for item in evidence if item.get("success")
                        and "changed_files" in item.get("categories", []) for p in item.get("paths", [])})
    mutation_tools = {"apply_text_patch", "apply_text_patches", "write_file", "write_files",
                      "run_command", "run_existing_entrypoint", "call_project_mcp_tool",
                      "apply_docx_text_patch", "apply_notebook_text_patch", "apply_spreadsheet_cell_patch"}
    if broker is not None:
        mutation_tools.update(getattr(broker, "_MUTATION_TOOLS", ()))
        mutation_tools.update(getattr(broker, "_EXECUTION_TOOLS", ()))
    uncertain = any(item.get("mcp_outcome") == "uncertain" or item.get("uncertain") is True
                    or (item.get("tool") in mutation_tools and item.get("success") is False
                        and item.get("wrapper_rejected") is not True
                        and item.get("execution_verified") is not True) for item in evidence)
    agent._last_request_failure.update(committed_paths=committed, uncertain_prior_action=uncertain)
    return (
        str(error) + " Earlier committed changes were preserved."
        + (" Prior action outcome is uncertain; reconciliation is required." if uncertain else "")
        + ("\nSaved paths: " + ", ".join(committed) if committed else "")
        + "\n\n[Completion status: partial] Unverified postconditions: rejected generation."
    )


def deliver_request_failure(gui, error, run_id: int) -> bool:
    if not isinstance(error, GenerationRejectedError):
        return False
    import traceback

    answer = request_failure_answer(gui.agent, error)
    try:
        gui._qubitz_gui_log_event("generation_request_failure", run_id=run_id,
                                 receipt=gui.agent._last_request_failure,
                                 traceback="".join(traceback.format_exception(error)))
    except Exception:  # noqa: BLE001 - failed evidence publication must retain the terminal error path
        return False
    if gui.agent._last_request_failure["uncertain_prior_action"]:
        return False
    gui.event_queue.put((run_id, "answer", answer))
    return True
