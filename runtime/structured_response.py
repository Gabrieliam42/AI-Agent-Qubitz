"""Expected structured-generation failures, without payload logging or automatic replay."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any


def non_thinking_template_contract(template: Any) -> str:
    """Recognize verified literal generation-prompt contracts, never model identities."""
    if not isinstance(template, str) or len(template) > 262144:
        return "unverified"
    complete_source = re.sub(r"\{#.*?#\}", "", template, flags=re.DOTALL)
    source = complete_source[-8192:]

    def statement(body):
        return r"\{%-?\s*" + body + r"\s*-?%\}"

    def literal(value):
        value = re.escape(value)
        return r"\{\{-?\s*(?:'" + value + "'|\"" + value + r"\")\s*-?\}\}"

    def joined(*parts):
        return r"\s*".join(parts)

    start = statement(r"if\s+add_generation_prompt")
    end = statement("endif")
    normalization = joined(
        statement(r"if\s+enable_thinking\s+is\s+defined"),
        statement(r"if\s+enable_thinking"), statement(r"set\s+thinking_option\s*=\s*['\"]on['\"]"),
        statement("else"), statement(r"set\s+thinking_option\s*=\s*['\"]off['\"]"), end,
        statement(r"elif\s+thinking_option\s+is\s+not\s+defined"),
        statement(r"set\s+thinking_option\s*=\s*['\"]on['\"]"), end,
    )
    normalized = re.match(r"\s*" + normalization, complete_source)
    prefill = joined(
        start, literal("<role>ASSISTANT</role>"),
        statement(r"if\s+thinking_option\s*==\s*['\"]on['\"]"), literal("\\n<think>"),
        statement(r"elif\s+thinking_option\s*==\s*['\"]off['\"]"), literal("\\n<think></think>"), end, end,
    )
    if normalized:
        remainder = complete_source[normalized.end():]
        rebound = re.search(r"\{%-?\s*(?:set|for|with|macro)\b[^%]*\b(?:thinking_option|enable_thinking)\b", remainder)
        if not rebound and not re.search(r"\{%-?\s*raw\b", remainder) and re.search(prefill + r"\s*\Z", remainder):
            return "normalized_thinking_option_prefill"
    closed = literal("<think>\\n\\n</think>\\n\\n")
    head = joined(start, literal("<|im_start|>assistant\\n"))
    for branch in (
        joined(statement(r"if\s+enable_thinking\s+is\s+defined\s+and\s+enable_thinking\s+is\s+false"), closed),
        joined(statement(r"if\s+enable_thinking\s+is\s+defined\s+and\s+enable_thinking\s+is\s+true"),
               literal("<think>\\n"), statement("else"), closed),
    ):
        if re.search(joined(head, branch, end, end) + r"\s*\Z", source):
            return "closed_think_prefill"
    channel = joined(
        start,
        statement(r"if\s+ns\.prev_message_type\s*!=\s*['\"]tool_response['\"]\s+and\s+"
                  r"ns\.prev_message_type\s*!=\s*['\"]tool_call['\"]"),
        literal("<|turn>model\\n"), statement(r"if\s+not\s+enable_thinking"),
        literal("<|channel>thought\\n<channel|>"), end,
        statement(r"elif\s+ns\.prev_message_type\s*==\s*['\"]tool_response['\"]\s+and\s+enable_thinking"),
        literal("<|channel>thought\\n"), end, end,
    )
    return "closed_channel_prefill" if re.search(channel + r"\s*\Z", source) else "unverified"


def apply_structured_request_policy(payload: dict, capabilities: Any) -> str:
    """Change this internal request only; unknown capabilities retain existing behavior."""
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    template = capabilities.get("structured_thinking_control") in {
        "closed_think_prefill", "closed_channel_prefill", "normalized_thinking_option_prefill",
    }
    effort = capabilities.get("supports_reasoning_effort") is True
    if not template and not effort:
        return "unchanged_unverified_template"
    options = payload.get("chat_template_kwargs")
    payload["chat_template_kwargs"] = {**(options if isinstance(options, dict) else {}), "enable_thinking": False}
    if effort:
        payload["reasoning_effort"] = "none"
    return "template_non_thinking" if template else "advertised_reasoning_none"


class StructuredBackendError(RuntimeError):
    """A backend reported failure, not an ordinary unusable model proposal."""

    def __init__(self, message: str, *, receipt: dict | None = None):
        self.receipt = dict(receipt or {})
        super().__init__(message)


class StructuredProposal(dict):
    """A normal JSON object with request-local provenance outside its JSON fields."""

    def __init__(self, value: dict, receipt: dict):
        super().__init__(value)
        self.receipt = dict(receipt)


def proposal_evidence(proposal: Any) -> dict:
    """Keep bounded proposal data, never prompts, source context, or transport credentials."""
    allowed = {"request_id", "response_id", "schema_name", "request_policy", "response_format",
               "request_sha256", "http_status", "finish_reason", "content_characters",
               "reasoning_characters", "duration_seconds"}
    request = {key: value[:160] if isinstance(value, str) else value
               for key, value in (proposal.receipt.items() if isinstance(proposal, StructuredProposal) else [])
               if key in allowed and (value is None or type(value) in {str, int, float, bool})}
    try:
        serialized = json.dumps(proposal, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError, RecursionError):
        return {"request": request, "proposal": None, "proposal_truncated": True,
                "proposal_unavailable": "not_serializable"}
    bounded = len(serialized) <= 24000
    return {"request": request, "proposal": json.loads(serialized) if bounded else None,
            "proposal_sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            "proposal_characters": len(serialized), "proposal_truncated": not bounded}


def record_proposal(broker, proposal: Any, *, phase: str, accepted: bool, reason: str = "") -> dict:
    """Receipt acceptance is scoped to this proposal, never a task or mutation claim."""
    evidence = proposal_evidence(proposal)
    broker.evidence.append({
        **evidence, "turn_id": broker.turn_id, "workspace_generation": broker.workspace_generation,
        "tool": "wrapper_structured_response", "success": accepted, "categories": [],
        "wrapper_rejected": not accepted, "receipt_kind": "structured_proposal",
        "phase": phase, "outcome": "proposal_validated" if accepted else "proposal_rejected",
        "reason": str(reason)[:320], "mutation_dispatched": False,
    })
    return evidence


class StructuredResponseError(ValueError):
    """A model response is unusable; this does not authorize another generation or mutation."""

    def __init__(self, outcome: str, *, schema_name: str, finish_reason: Any = None,
                 request_id: str = "", content_characters: int = 0, reasoning_characters: int = 0,
                 detail: str = ""):
        self.receipt = {
            "outcome": outcome, "schema_name": str(schema_name)[:160],
            "finish_reason": finish_reason if isinstance(finish_reason, str) else None,
            "request_id": str(request_id)[:160], "content_characters": content_characters,
            "reasoning_characters": reasoning_characters, "retry_permitted": False,
            "mutation_dispatched": False, "detail": str(detail)[:320],
        }
        super().__init__(f"Structured response unavailable: {outcome}; no response was replayed.")


def parse_completion(raw: Any, *, schema_name: str, parse: Callable[[str], dict],
                     normalized: bool = False) -> dict:
    """Require a normal terminal response before accepting even syntactically valid JSON."""
    if not isinstance(raw, dict):
        raise StructuredResponseError("invalid_response_envelope", schema_name=schema_name)
    if raw.get("error"):
        raise StructuredBackendError("Constrained JSON backend reported an error; no response was replayed.")
    if normalized:
        choice = raw
    else:
        choices = raw.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise StructuredResponseError("invalid_response_envelope", schema_name=schema_name)
        choice = choices[0]
    message = choice.get("message")
    if not isinstance(message, dict):
        raise StructuredResponseError("invalid_response_envelope", schema_name=schema_name)
    content = message.get("content")
    reasoning = message.get("reasoning_content")
    finish = choice.get("finish_reason")
    metadata = {"schema_name": schema_name, "finish_reason": finish, "request_id": raw.get("id", ""),
                "content_characters": len(content) if isinstance(content, str) else 0,
                "reasoning_characters": len(reasoning) if isinstance(reasoning, str) else 0}
    if not finish:
        raise StructuredResponseError("incomplete_response", **metadata)
    if not isinstance(finish, str):
        raise StructuredResponseError("invalid_response_envelope", **metadata)
    if finish in {"length", "max_tokens"}:
        raise StructuredResponseError("output_limit", **metadata)
    if finish != "stop":
        raise StructuredResponseError("unsupported_termination", **metadata)
    if content is None or isinstance(content, str) and not content.strip():
        raise StructuredResponseError("empty_content", **metadata)
    if not isinstance(content, str):
        raise StructuredResponseError("invalid_json", **metadata)
    try:
        value = parse(content)
    except (ValueError, RecursionError) as exc:
        raise StructuredResponseError("invalid_json", **metadata) from exc
    if not isinstance(value, dict):
        raise StructuredResponseError("invalid_json", **metadata)
    return value


def record_failure(broker, error: StructuredResponseError, *, phase: str, proposal: Any = None) -> None:
    evidence = proposal_evidence(proposal) if proposal is not None else {}
    metadata = dict(error.receipt)
    for key, value in evidence.get("request", {}).items():
        if key not in {"outcome", "detail", "retry_permitted", "mutation_dispatched"}:
            metadata[key] = value
    broker.evidence.append({**evidence, **metadata, "turn_id": broker.turn_id,
                            "workspace_generation": broker.workspace_generation,
                            "tool": "wrapper_structured_response", "success": False, "categories": [],
                            "receipt_kind": "structured_proposal",
                            "wrapper_rejected": True, "recovery_kind": "structured_response_failure",
                            "phase": phase})
