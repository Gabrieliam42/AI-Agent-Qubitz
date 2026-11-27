"""Conservative metadata-only suggestions for a missing workspace read."""
from __future__ import annotations

import os
import re
import time
from collections import deque
from pathlib import Path

from .retrieval import FOCUSED_SKIP_DIRECTORIES, SKIP_DIRECTORIES

MAX_LOCATION_CHECKS = 3
MAX_LOCATION_ENTRIES = 512
MAX_LOCATION_SECONDS = 1.5
_EXCLUDED = FOCUSED_SKIP_DIRECTORIES | SKIP_DIRECTORIES | {
    "fixtures", "test-fixtures", "test_fixtures", "tests", "__tests__", "examples", "models", "node_modules",
}


def eligible_basename(value: object) -> bool:
    return bool(isinstance(value, str) and 1 <= len(value) <= 128 and value == value.strip()
                and not value.startswith((".", "~")) and not re.search(r'[\\/:*?<>|"\x00-\x1f\x7f]', value)
                and not re.search(r"(?:^|[-_.])(?:private|credentials?|secrets?)(?:[-_.]|$)", value, re.IGNORECASE)
                and value.casefold() not in {"harness.txt", "harness.enc", "memory.md"}
                and Path(value).suffix.casefold() not in {".pem", ".key", ".pfx", ".p12"})


def check_location(workspace: Path, name: str, excluded, cancelled) -> dict:
    """Collect exact-basename candidates without reading their contents or selecting one."""
    result = {"target": name, "candidates": [], "limited": False}
    if not eligible_basename(name):
        return result
    deadline = time.monotonic() + MAX_LOCATION_SECONDS
    directories = deque([(workspace, 0)])
    seen = 0
    candidates: list[str] = []
    while directories:
        directory, depth = directories.popleft()
        try:
            if directory.resolve(strict=True) != directory or directory.is_junction():
                continue
            with os.scandir(directory) as entries:
                for entry in entries:
                    if seen >= MAX_LOCATION_ENTRIES or time.monotonic() >= deadline or cancelled():
                        return {**result, "candidates": sorted(candidates), "limited": True}
                    seen += 1
                    if entry.name.startswith(".") or entry.name.casefold() in _EXCLUDED or excluded(entry.name):
                        continue
                    if entry.name == name and entry.is_file(follow_symlinks=False):
                        candidate = Path(entry.path)
                        if candidate.resolve(strict=True) == candidate:
                            candidates.append(candidate.relative_to(workspace).as_posix())
                            if len(candidates) == 4:
                                return {**result, "candidates": sorted(candidates), "limited": True}
                    elif depth < 2 and entry.is_dir(follow_symlinks=False):
                        directories.append((Path(entry.path), depth + 1))
        except (OSError, RuntimeError):
            result["limited"] = True
    return {**result, "candidates": sorted(candidates)}
