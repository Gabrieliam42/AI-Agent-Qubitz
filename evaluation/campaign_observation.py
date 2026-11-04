"""Read live campaign evidence without denying writers or inventing identities."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from evaluation.evidence_receipts import WindowsProcessIdentity


@contextmanager
def shared_reader(path: Path):
    if os.name != "nt":
        with Path(path).open("rb") as stream:
            yield stream
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    # Share read, write AND delete/rename; a monitor must not lock the workload.
    handle = api.CreateFileW(str(Path(path).resolve()), 0x80000000, 0x1 | 0x2 | 0x4,
                             None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    try:
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


def read_json(path: Path) -> Any:
    with shared_reader(path) as stream:
        data = stream.read()
    return json.loads(data.decode("utf-8-sig"))


def event_delta(path: Path, offset: int, limit: int = 4_000_000) -> tuple[list[dict], int]:
    if offset < 0 or limit <= 0:
        raise ValueError("Invalid bounded event range")
    with shared_reader(path) as stream:
        if os.fstat(stream.fileno()).st_size < offset:
            raise RuntimeError("Raw events shrank; never replay consumed offsets")
        stream.seek(offset)
        delta = stream.read(limit)
    complete = delta[:delta.rfind(b"\n") + 1]
    if not complete and len(delta) == limit:
        raise RuntimeError("A single event exceeds the bounded observation window")
    return [json.loads(line) for line in complete.splitlines()], offset + len(complete)


def referenced_event_delta(path: Path, offset: int, limit: int = 4_000_000) -> tuple[list[dict], int]:
    """Use for new monitors: previews always retain a byte-exact full-payload reference."""
    import hashlib

    if offset < 0 or limit <= 0:
        raise ValueError("Invalid bounded event range")
    with shared_reader(path) as stream:
        if os.fstat(stream.fileno()).st_size < offset:
            raise RuntimeError("Raw events shrank; never replay consumed offsets")
        stream.seek(offset)
        delta = stream.read(limit)
    complete = delta[:delta.rfind(b"\n") + 1]
    if not complete and len(delta) == limit:
        raise RuntimeError("A single event exceeds the bounded observation window")
    rows = []
    for line in complete.splitlines(keepends=True):
        event = json.loads(line)
        preview = json.dumps(event, ensure_ascii=True)
        rows.append({"source": str(path), "offset": offset, "length": len(line),
                     "sha256": hashlib.sha256(line).hexdigest(),
                     "event": event if len(preview) <= 16000 else preview[:16000],
                     "truncated_in_receipt": len(preview) > 16000})
        offset += len(line)
    return rows, offset


def resolve_event_reference(index: Path, reference: dict, consumed: int, root: Path) -> dict:
    """Resolve one hash-bound event from consumed bytes, without moving any cursor."""
    index, root = Path(index).absolute(), Path(root).resolve()
    if (index.resolve() != index or not index.is_relative_to(root) or not index.is_file()
            or index.name != "event_payload_index.jsonl"):
        raise ValueError("Event reference requires an unaliased in-project payload index")
    source = reference.get("source")
    if source != "events.jsonl":
        raise ValueError("Payload index source must be its owned events.jsonl sibling")
    offset, length = reference.get("offset"), reference.get("length")
    if (type(offset) is not int or type(length) is not int or type(consumed) is not int
            or offset < 0 or not 0 < length <= 4_000_000 or offset + length > consumed):
        raise ValueError("Referenced event is outside the complete consumed range")
    path = index.parent / source
    if path.resolve() != path:
        raise ValueError("Referenced event source is aliased")
    with shared_reader(path) as stream:
        stream.seek(offset)
        raw = stream.read(length)
    if (len(raw) != length or not raw.endswith(b"\n") or raw.count(b"\n") != 1
            or hashlib.sha256(raw).hexdigest() != reference.get("sha256")):
        raise ValueError("Referenced event length, completeness or hash differs")
    event = json.loads(raw)
    if not isinstance(event, dict) or any(reference.get(key) != event.get(key)
                                         for key in ("sequence", "turn", "kind", "invocation_id")):
        raise ValueError("Referenced event identity differs from its payload index")
    return event


def process_observation(actual: dict | None, expected: dict, baseline: dict | None = None) -> str:
    if actual is None:
        return "absent"
    for reference in (expected, baseline or {}):
        for key in ("pid", "creation_ticks", "executable", "command_line"):
            value, wanted = actual.get(key), reference.get(key)
            if value in (None, "") or wanted in (None, ""):
                continue
            if key == "command_line":
                value, wanted = value.rstrip(), wanted.rstrip()
            if value != wanted:
                raise RuntimeError(f"Process {key} differs from exact receipt")
    try:
        WindowsProcessIdentity.from_mapping(actual)
    except ValueError:
        return "incomplete_metadata"
    return "matched"


def recover_cursor(history: Path, cursor: dict, save_cursor) -> dict:
    if not history.exists():
        return cursor
    with shared_reader(history) as stream:
        stream.seek(max(0, os.fstat(stream.fileno()).st_size - 16_000_000))
        tail = stream.read()
    if not tail.endswith(b"\n"):
        raise RuntimeError("Incomplete derived history append; preserve it, do not replay")
    last = json.loads(tail.splitlines()[-1])
    if cursor.get("observation_id") != last["observation_id"]:
        cursor = last["cursor_after"]
        save_cursor(cursor)
    return cursor


def commit_observation(history: Path, observation: dict, save_cursor) -> None:
    with history.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(observation, ensure_ascii=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    save_cursor(observation["cursor_after"])
