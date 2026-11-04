"""Pre-dispatch validation only; never grants tool authority or replays requests."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from itertools import pairwise
from pathlib import Path
from typing import Any


class ConnectionUnavailable(RuntimeError):
    """All attempted connections failed before a request could be accepted."""


def snapshot_proposal_schema(snapshots: dict[str, str], max_replacements: int = 32) -> dict:
    """Constrain each path to its own original snapshot, not another file's bounds."""
    if not isinstance(snapshots, dict) or not 1 <= len(snapshots) <= 6:
        raise ValueError("One to six exact snapshot targets are required")
    if type(max_replacements) is not int or not 1 <= max_replacements <= 32:
        raise ValueError("Invalid snapshot replacement budget")
    branches = []
    for path, content in sorted(snapshots.items()):
        if not isinstance(path, str) or not path or not isinstance(content, str) or not content:
            raise ValueError("Every snapshot target requires a path and nonempty original text")
        lines = len(content.splitlines(keepends=True))
        span = {"type": "object", "properties": {
            "start_line": {"type": "integer", "minimum": 1, "maximum": lines},
            "end_line": {"type": "integer", "minimum": 1, "maximum": lines},
            # Independent subrules avoid a single 24,000-fold grammar expansion.
            # Both encodings describe the same bounded replacement text.
            "new_text": {"anyOf": [
                {"type": "string", "maxLength": 1000},
                {"type": "array", "maxItems": 24,
                 "items": {"type": "string", "maxLength": 1000}},
            ]},
        }, "required": ["start_line", "end_line", "new_text"], "additionalProperties": False}
        branches.append({"type": "object", "properties": {
            "path": {"type": "string", "enum": [path]},
            "replacements": {"type": "array", "minItems": 1,
                             "maxItems": max_replacements, "items": span},
        }, "required": ["path", "replacements"], "additionalProperties": False})
    return {"type": "object", "properties": {"patches": {
        "type": "array", "minItems": 1, "maxItems": len(branches), "items": {"anyOf": branches},
    }}, "required": ["patches"], "additionalProperties": False}


def snapshot_line_replacements(
    content: str, spans: list[dict[str, Any]], *, preserve_line_endings: bool = True,
) -> list[dict[str, str]]:
    """Resolve inclusive original-snapshot lines; never reconstruct old text from a model."""
    lines = content.splitlines(keepends=True)
    if not content or not isinstance(spans, list) or not 1 <= len(spans) <= 32:
        raise ValueError("A nonempty snapshot and 1-32 line spans are required")
    intervals = []
    for span in spans:
        if not isinstance(span, dict) or set(span) != {"start_line", "end_line", "new_text"}:
            raise ValueError("Each span requires only start_line, end_line and new_text")
        start, end, text = span["start_line"], span["end_line"], span["new_text"]
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise ValueError(f"Invalid snapshot line span: require 1 <= start_line <= end_line <= {len(lines)}")
        if isinstance(text, list):
            if len(text) > 24 or any(not isinstance(part, str) or len(part) > 1000 for part in text):
                raise ValueError("Snapshot replacement requires at most 24 text parts of 1000 characters")
            text = "".join(text)
        if not isinstance(text, str) or len(text) > 24000:
            raise ValueError("Snapshot replacement must be text of at most 24000 characters")
        ending = re.search(r"(?:\r\n|\n|\r)$", lines[end - 1])
        if preserve_line_endings and text and ending and not text.endswith(("\r", "\n")):
            text += ending[0]
        intervals.append((start - 1, end, text))
    intervals.sort()
    if any(left[1] > right[0] for left, right in pairwise(intervals)):
        raise ValueError("Snapshot line spans must not overlap")
    updated = list(lines)
    for start, end, text in reversed(intervals):
        updated[start:end] = [text]
    # A single exact replacement also handles repeated/blank lines unambiguously.
    return [{"old_text": content, "new_text": "".join(updated)}]


def importmap_issues(content: str) -> list[str]:
    """Diagnose literal prefix mismatches without guessing URLs or fetching assets."""
    regions = _staged_script_regions(content)
    modules = [content[r["start_offset"]:r["end_offset"]] for r in regions if r["script_type"] == "module"]
    imports = set(re.findall(r"\b(?:from\s*|import\s*\(?\s*)['\"]([^'\"]+)['\"]", "\n".join(modules)))
    issues = []
    for region in regions:
        if region["script_type"] != "importmap":
            continue
        try:
            mapping = json.loads(content[region["start_offset"]:region["end_offset"]])
        except (ValueError, TypeError):
            continue
        if not isinstance(mapping, dict):
            continue
        maps = [mapping.get("imports")]
        scopes = mapping.get("scopes")
        if isinstance(scopes, dict):
            maps.extend(scopes.values())
        for entries in maps:
            if not isinstance(entries, dict):
                continue
            for key, url in entries.items():
                if not isinstance(url, str) or key.endswith("/") or not url.endswith("/"):
                    continue
                unresolved = sorted(item for item in imports if item.startswith(key + "/")
                                    and item not in entries and not any(
                                        k.endswith("/") and item.startswith(k) for k in entries))
                if unresolved:
                    issues.append(f"Import-map key {key!r} targets {url[:500]!r} and is exact, not a prefix for {unresolved[0]!r}; "
                                  "a subpath prefix requires trailing slashes on both key and URL.")
    return issues


def staged_repair_schema(content_length: int) -> dict:
    """Describe one bounded repair response; aggregate text limits are host-enforced."""
    if type(content_length) is not int or not 1 <= content_length <= 160000:
        raise ValueError("Invalid staged draft length")
    item = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "old_text": {"type": "string", "minLength": 1, "maxLength": 4096},
            "new_text": {"type": "string", "minLength": 1, "maxLength": 4096},
            "old_offset": {"type": "integer", "minimum": 0, "maximum": content_length},
        },
        "required": ["old_text", "new_text"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {"patches": {"type": "array", "minItems": 1, "maxItems": 8, "items": item}},
        "required": ["patches"],
    }


def _staged_script_regions(content: str) -> list[dict]:
    starts = [0, *(match.end() for match in re.finditer("\n", content))]

    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.regions = []
            self.active = None

        def source_offset(self):
            line, column = self.getpos()
            return starts[line - 1] + column

        def handle_starttag(self, tag, attrs):
            if tag != "script":
                return
            attrs = dict(attrs)
            script_type = (attrs.get("type") or "").strip().casefold()
            if script_type in {"", "module", "text/javascript", "application/javascript",
                               "text/ecmascript", "application/ecmascript"}:
                language = "JavaScript"
            elif script_type in {"importmap", "application/json", "application/ld+json", "speculationrules"}:
                language = "JSON"
            else:
                language = "data"
            self.active = {
                "language": language, "script_type": (script_type or "classic")[:160],
                "src": attrs.get("src", "")[:240] if attrs.get("src") else None,
                "src_was_truncated": len(attrs.get("src") or "") > 240,
                "start_offset": self.source_offset() + len(self.get_starttag_text()),
                "end_offset": len(content),
            }
            self.regions.append(self.active)

        def handle_endtag(self, tag):
            if tag == "script" and self.active is not None:
                self.active["end_offset"] = max(self.active["start_offset"], self.source_offset())
                self.active = None

    parser = Parser()
    parser.feed(content)
    return parser.regions


def staged_repair_context(content: str, failure: Exception, suffix: str) -> dict:
    """Keep bounded exact excerpts and supplied coordinates; never infer missing locations."""
    diagnostic = str(failure)[:500]
    browser = [item for item in getattr(failure, "html_browser_diagnostics", ())
               if isinstance(item, dict)][:4]
    syntax = [item for item in browser if item.get("name") == "SyntaxError"]
    primary = (syntax or browser or [{}])[0]
    error_class = primary.get("name") or type(failure).__name__
    line = getattr(failure, "lineno", None) if isinstance(failure, SyntaxError) else primary.get("line")
    column = getattr(failure, "offset", None) if isinstance(failure, SyntaxError) else primary.get("column")
    url = primary.get("url", "")
    lines = content.splitlines(keepends=True)
    anchor = -1
    if (isinstance(failure, SyntaxError) or url == "about:blank") and type(line) is int and 1 <= line <= len(lines):
        anchor = sum(len(text) for text in lines[:line - 1])
    if anchor < 0:
        for match in re.finditer(r"https?://[^\s\"'<>]+", diagnostic):
            anchor = content.find(match.group().rstrip(".,;:)"))
            if anchor >= 0:
                break
    if anchor >= 0:
        start = max(0, anchor - 3000)
        excerpt = content[start:start + 6000]
    else:
        start = 0
        excerpt = content if len(content) <= 12000 else content[:6000] + "\n[omitted]\n" + content[-6000:]
    ranges = [{"draft_offset": start, "excerpt_offset": 0, "length": len(excerpt)}]
    if anchor < 0 and len(content) > 12000:
        ranges = [{"draft_offset": 0, "excerpt_offset": 0, "length": 6000},
                  {"draft_offset": len(content) - 6000,
                   "excerpt_offset": 6000 + len("\n[omitted]\n"), "length": 6000}]
    artifact_language = {".py": "Python", ".html": "HTML", ".htm": "HTML"}.get(suffix.casefold(), "text")
    regions = _staged_script_regions(content) if suffix.casefold() in {".html", ".htm"} else []
    source = None
    if (isinstance(failure, SyntaxError) or url == "about:blank") and (
            type(line) is int and 1 <= line <= len(lines)
            and type(column) is int and 1 <= column <= len(lines[line - 1]) + 1
            # Browser columns can count UTF-16 units, unlike Python string offsets.
            and (isinstance(failure, SyntaxError) or lines[line - 1].isascii())):
        location = sum(len(text) for text in lines[:line - 1]) + column - 1
        source = next((region for region in regions if not region["src"]
                       and region["start_offset"] <= location < region["end_offset"]), None)
    return {
        "diagnostic": diagnostic, "error_class": error_class,
        "validation_phase": "syntax" if isinstance(failure, SyntaxError) or syntax else "runtime_or_artifact",
        "artifact_language": artifact_language,
        "expected_language": source["language"] if source else artifact_language,
        "reported_location": {"url": url, "line": line, "column": column},
        "located_script": source,
        "script_regions": regions[:16], "script_regions_truncated": len(regions) > 16,
        "importmap_hints": importmap_issues(content) if suffix.casefold() in {".html", ".htm"} else [],
        "browser_diagnostics": browser, "rejected_draft_excerpt": excerpt,
        "excerpt_start": start, "excerpt_ranges": ranges,
        "draft_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "draft_was_truncated": sum(region["length"] for region in ranges) < len(content),
    }


def repair_staged_text(content: str, patch: dict, context: dict) -> str:
    """Apply original-snapshot edits in memory atomically; never write or grant authority."""
    if not isinstance(patch, dict):
        raise ValueError("the bounded patch has invalid fields")  # noqa: TRY004 - Public validation exception contract.
    patches = patch["patches"] if set(patch) == {"patches"} else [patch]
    if not isinstance(patches, list) or not 1 <= len(patches) <= 8:
        raise ValueError("the bounded patch set requires one to eight patches")
    if hashlib.sha256(content.encode()).hexdigest() != context.get("draft_sha256"):
        raise ValueError("the staged draft hash changed")
    intervals = []
    old_size = new_size = 0
    for item in patches:
        if not isinstance(item, dict) or set(item) not in (
                {"old_text", "new_text"}, {"old_text", "new_text", "old_offset"}):
            raise ValueError("the bounded patch has invalid fields")
        old, new = item["old_text"], item["new_text"]
        if (not isinstance(old, str) or not isinstance(new, str) or not old or not new
                or len(old) > 4096 or len(new) > 4096 or old == new):
            raise ValueError("the bounded patch has invalid or unchanged fields")
        old_size += len(old)
        new_size += len(new)
        if old_size > 4096 or new_size > 4096:
            raise ValueError("the bounded patch set exceeds the aggregate text limit")
        offset = item.get("old_offset")
        if "old_offset" not in item:
            offset = content.find(old)
            if offset < 0 or content.find(old, offset + 1) >= 0:
                raise ValueError("the patch target is not unique in the staged file; an exact old_offset is required")
        if type(offset) is not int or offset < 0 or content[offset:offset + len(old)] != old:
            raise ValueError("the staged patch offset does not match its exact original text")
        excerpt = context["rejected_draft_excerpt"]
        for region in context["excerpt_ranges"]:
            relative = offset - region["draft_offset"]
            start = region["excerpt_offset"] + relative
            if (0 <= relative and relative + len(old) <= region["length"]
                    and excerpt[start:start + len(old)] == old):
                break
        else:
            raise ValueError("the staged patch occurrence is outside the diagnosed excerpt")
        intervals.append((offset, offset + len(old), new))
    intervals.sort()
    if any(left[1] > right[0] for left, right in pairwise(intervals)):
        raise ValueError("the staged patch ranges overlap")
    result = content
    for start, end, new in reversed(intervals):
        result = result[:start] + new + result[end:]
    if len(result) > 160000:
        raise ValueError("the repaired file exceeds the staging capacity")
    return result


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
