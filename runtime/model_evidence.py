"""Bounded model-facing evidence, separate from display previews and audit storage."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path


def compact_tool_content(content: str, limit: int = 4096) -> str:
    """Keep short results intact; mark large excerpts rather than clipping JSON."""
    if len(content) <= limit:
        return content
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        value = json.loads(content)
    except (ValueError, RecursionError):
        value = None
    data = value.get("structured_content", value) if isinstance(value, dict) else None
    if isinstance(data, dict):
        # Duplicate display renderings and verbose ownership trees are audit data,
        # not a reason to remove the small operative file/assertion from the model.
        primary = {key: data[key] for key in (
            "path", "sha256", "status", "start_line", "end_line", "size", "content",
            "returncode", "exit_code", "stdout", "stderr", "reason", "errors", "issues",
        ) if key in data}
        if primary:
            result = json.dumps({"is_error": value.get("is_error", False),
                                 "structured_content": primary,
                                 "audit_envelope_omitted": True,
                                 "source_result_sha256": digest}, ensure_ascii=True)
            if len(result) <= limit:
                return result
            if isinstance(primary.get("content"), str) and primary.get("path"):
                # A file read must be useful or fail the context-capacity check.
                # Never tell the host's reread guard that hidden lines were delivered.
                return content
    # Explicit character coverage is not a claim of complete file-line coverage.
    result = {"status": "result_excerpt", "source_result_sha256": digest,
              "total_characters": len(content), "returned_character_range": [0, 0],
              "omitted": True,
              "instruction": "Request the needed bounded range; this is not a complete result."}
    excerpt = content[:max(0, limit - len(json.dumps(result)) - 120)]
    result.update(excerpt=excerpt, returned_character_range=[0, len(excerpt)])
    encoded = json.dumps(result, ensure_ascii=True)
    while len(encoded) > limit and excerpt:
        excerpt = excerpt[:len(excerpt) // 2]
        result.update(excerpt=excerpt, returned_character_range=[0, len(excerpt)])
        encoded = json.dumps(result, ensure_ascii=True)
    return encoded


def exact_read_receipt(target, arguments: dict, structured: dict, *, decoder=None) -> dict | None:
    """Corroborate returned read bytes against the current exact file and range."""
    if target is None or not target.is_file() or not isinstance(structured, dict):
        return None
    first, last = structured.get("start_line"), structured.get("end_line")
    returned = structured.get("content")
    if type(first) is not int or type(last) is not int or not isinstance(returned, str):
        return None
    if first < 1 or last < first or first != arguments.get("start_line", 1):
        return None
    if last > arguments.get("end_line", 4000):
        return None
    try:
        raw = target.read_bytes()
        text = decoder(raw) if decoder is not None else raw.decode("utf-8-sig")
    except (OSError, UnicodeError, ValueError):
        return None
    lines = text.splitlines()
    if last > len(lines) or returned.splitlines() != lines[first - 1:last]:
        return None
    return {"path": str(target), "sha256": hashlib.sha256(raw).hexdigest(),
            "start_line": first, "end_line": last, "complete": first == 1 and last == len(lines)}


def static_output_paths(script: Path, workspace: Path) -> set[str]:
    """Resolve only literal pathlib writes in an already authorized entrypoint."""
    try:
        tree = ast.parse(script.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    constructors = {alias.asname or alias.name for node in tree.body if isinstance(node, ast.ImportFrom)
                    and node.module == "pathlib" for alias in node.names if alias.name == "Path"}
    outputs = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        value = node.func.value
        if (node.func.attr not in {"write_text", "write_bytes"} or not isinstance(value, ast.Call)
                or not isinstance(value.func, ast.Name) or value.func.id not in constructors
                or len(value.args) != 1 or not isinstance(value.args[0], ast.Constant)
                or not isinstance(value.args[0].value, str)):
            continue
        target = (workspace / value.args[0].value).resolve()
        if target.is_relative_to(workspace.resolve()):
            outputs.add(str(target))
    return outputs
