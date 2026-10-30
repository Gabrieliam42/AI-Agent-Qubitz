"""Collect optional llama.cpp terminal metadata without prolonging generation."""
from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any

import httpx


def terminal_metadata(response: Any, lines: Any, check_cancel: Any, *, seconds: float = 0.25) -> dict:
    """Read only bounded optional trailers from this accepted response, never another request."""
    network = getattr(response, "extensions", {}).get("network_stream")
    original = getattr(network, "read", None)
    if not callable(original):
        return {"status": "unsupported", "usage": {}, "timings": {}}
    deadline = time.monotonic() + seconds
    result = {"status": "metadata_unobserved", "usage": {}, "timings": {}}

    def bounded_read(max_bytes: int, timeout: float | None = None) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise httpx.ReadTimeout("Optional terminal metadata deadline elapsed")
        return original(max_bytes, timeout=min(timeout, remaining) if timeout is not None else remaining)

    try:
        network.read = bounded_read
    except (AttributeError, TypeError):
        result["status"] = "unsupported"
        return result
    try:
        for _ in range(8):
            check_cancel()
            if time.monotonic() >= deadline:
                result["status"] = "bounded_timeout"
                break
            try:
                line = next(lines)
            except StopIteration:
                break
            if len(line) > 32768:
                break
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                break
            event = json.loads(raw)
            if not isinstance(event, dict) or event.get("error"):
                result["status"] = "metadata_error"
                break
            # Later text/tool fragments can never modify the already completed answer.
            for key in ("usage", "timings"):
                if isinstance(event.get(key), dict):
                    result[key] = event[key]
            if result["usage"] or result["timings"]:
                result["status"] = "observed"
    except (httpx.HTTPError, OSError, ValueError):
        result["status"] = "bounded_timeout" if time.monotonic() >= deadline else "metadata_error"
    finally:
        with suppress(AttributeError, TypeError):
            network.read = original
    check_cancel()
    return result
