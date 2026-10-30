"""Pre-dispatch validation only; never grants tool authority or replays requests."""
from __future__ import annotations

import re
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any


class ConnectionUnavailable(RuntimeError):
    """All attempted connections failed before a request could be accepted."""


def snapshot_line_replacements(content: str, spans: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Resolve inclusive original-snapshot lines; never reconstruct old text from a model."""
    lines = content.splitlines(keepends=True)
    if not content or not isinstance(spans, list) or not 1 <= len(spans) <= 32:
        raise ValueError("A nonempty snapshot and 1-32 line spans are required")
    intervals = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"start_line", "end_line", "new_text"}:
            raise ValueError("Each span requires only start_line, end_line and new_text")
        start, end, text = span["start_line"], span["end_line"], span["new_text"]
        if (type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines)
                or not isinstance(text, str) or len(text) > 24000):
            raise ValueError("Invalid snapshot line span or unbounded replacement")
        intervals.append((start - 1, end, text))
    intervals.sort()
    if any(left[1] > right[0] for left, right in pairwise(intervals)):
        raise ValueError("Snapshot line spans must not overlap")
    updated = list(lines)
    for start, end, text in reversed(intervals):
        updated[start:end] = [text]
    # A single exact replacement also handles repeated/blank lines unambiguously.
    return [{"old_text": content, "new_text": "".join(updated)}]


def validate_tool_history(messages: Sequence[dict[str, Any]]) -> None:
    """Require exact, unique call/result pairs without inventing missing receipts."""
    pending: set[str] = set()
    for message in messages:
        role = message.get("role")
        if role == "tool":
            key = message.get("tool_call_id")
            if not isinstance(key, str) or key not in pending:
                raise ValueError("Invalid tool history: unmatched or duplicate tool result")
            pending.remove(key)
            continue
        if pending:
            raise ValueError("Invalid tool history: missing tool result before the next message")
        calls = message.get("tool_calls") or []
        if calls and role != "assistant":
            raise ValueError("Invalid tool history: only an assistant may supply tool calls")
        for call in calls:
            key = call.get("id") if isinstance(call, dict) else None
            if not isinstance(key, str) or not key or key in pending:
                raise ValueError("Invalid tool history: missing or duplicate tool call ID")
            pending.add(key)
    if pending:
        raise ValueError("Invalid tool history: missing terminal tool results")


def append_recovery_instruction(messages: Sequence[dict[str, Any]], instruction: str) -> list[dict[str, Any]]:
    """Keep tool results byte-for-byte and avoid a user turn immediately after them."""
    validate_tool_history(messages)
    result = [dict(message) for message in messages]
    if result and result[-1].get("role") == "tool":
        index = next((i for i in range(len(result) - 1, -1, -1) if result[i].get("role") == "user"), None)
        if index is None:
            raise ValueError("Cannot place recovery guidance without an existing user task")
    elif result and result[-1].get("role") == "user":
        index = len(result) - 1
    else:
        result.append({"role": "user", "content": instruction})
        return result
    content = result[index].get("content", "")
    if isinstance(content, str):
        result[index]["content"] = content + "\n\n" + instruction
    elif isinstance(content, list):
        result[index]["content"] = [*content, {"type": "text", "text": instruction}]
    else:
        raise TypeError("Unsupported user-message content for wrapper recovery")
    return result


def python_edit_error(content: str, path: Path, prompt: str) -> dict[str, Any] | None:
    """Compile the complete candidate, without execution or escape conversion."""
    if path.suffix.casefold() != ".py":
        return None
    # Only a positive, explicit user request may intentionally keep malformed code.
    unquoted = re.sub(r"```.*?```|`[^`]*`|\"[^\"]*\"|'[^']*'", "", prompt, flags=re.DOTALL)
    for clause in re.split(r"[;\n]+|[.!?](?=\s)", unquoted):
        if re.search(r"\b(?:do\s+not|don't|never|without|explain|says?|quoted?)\b", clause, re.IGNORECASE):
            continue
        if re.search(r"^\s*(?:please\s+)?(?:create|write|make|keep|leave)\b.{0,200}"
                     r"\bintentionally\s+(?:invalid|malformed|incomplete)\s+Python\b", clause, re.IGNORECASE):
            return None
    try:
        compile(content, str(path), "exec", dont_inherit=True)
    except (SyntaxError, ValueError) as exc:
        return {"status": "invalid_patch_content", "path": str(path),
                "reason": getattr(exc, "msg", str(exc)), "line": getattr(exc, "lineno", None) or 1,
                "offset": getattr(exc, "offset", None), "files_unchanged": True,
                "instruction": "The complete candidate is invalid Python; no patch was dispatched. "
                               "Correct only the authorized edit from its exact current snapshot within the "
                               "remaining recovery budget. Do not unescape text automatically or replay a mutation."}
    return None
