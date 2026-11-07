"""Keep nested argument contracts visible even in lossy tool-prompt renderers."""
from __future__ import annotations

import copy
import json


def model_tool_schema(schema: dict) -> dict:
    """Annotate only: retain the authoritative wire constraints and host validation."""
    result = copy.deepcopy(schema)
    remaining = [1024]

    def resolve(node, trail=(), depth=0):
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 24:
            raise ValueError("Nested schema annotation exceeds its bounded traversal")
        if isinstance(node, list):
            return [resolve(item, trail, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if ref is not None:
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in trail:
                raise ValueError("Only finite local schema references can be expanded")
            target = schema
            for key in ref[2:].split("/"):
                target = target[key.replace("~1", "/").replace("~0", "~")]
            expanded = resolve(target, (*trail, ref), depth + 1)
            siblings = {key: value for key, value in node.items() if key != "$ref"}
            return {"allOf": [expanded, resolve(siblings, trail, depth + 1)]} if siblings else expanded
        output = {}
        for key, value in node.items():
            if key in {"$defs", "definitions", "title", "description"}:
                continue
            if key in {"properties", "patternProperties"} and isinstance(value, dict):
                output[key] = {name: resolve(item, trail, depth + 1) for name, item in value.items()}
            elif key in {"items", "additionalProperties", "anyOf", "oneOf", "allOf", "prefixItems", "not"}:
                output[key] = resolve(value, trail, depth + 1)
            else:
                output[key] = copy.deepcopy(value)
        return output

    budget = 12000
    for name, spec in result.get("properties", {}).items():
        if not isinstance(spec, dict) or spec.get("type") not in {"array", "object"}:
            continue
        try:
            contract = resolve(schema["properties"][name])
            encoded = json.dumps(contract, ensure_ascii=True, separators=(",", ":"))
        except (KeyError, TypeError, ValueError):
            # Unsupported/recursive contracts remain byte-equivalent, never fetched or weakened.
            continue
        if len(encoded) > min(6000, budget):
            continue
        note = "Nested argument contract (JSON Schema): " + encoded
        description = spec.get("description", "")
        if note not in description:
            spec["description"] = (description + "\n" + note).strip()
            budget -= len(encoded)
    return result
