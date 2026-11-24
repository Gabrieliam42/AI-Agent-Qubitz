"""Find an existing, scoped test target without recursively scanning a workspace."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from threading import Event


class VerificationDiscoveryUnavailable(RuntimeError):
    """Discovery could not settle within its scope or budget."""


def _discover(workspace: Path, checkpoint) -> tuple[str, list[Path]]:
    workspace = workspace.resolve()
    tests = workspace / "tests"
    if tests.is_symlink() or tests.is_junction():
        raise VerificationDiscoveryUnavailable("the conventional tests directory is aliased")
    suite = tests.is_dir()
    pending = [tests if suite else workspace]
    found: list[Path] = []
    pruned = False
    while pending:
        checkpoint()
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                checkpoint()
                leaf = entry.name
                if leaf.startswith(".") or leaf.casefold() in {
                    "__pycache__", "backup", "build", "dist", "node_modules", "results", "venv",
                }:
                    pruned = True
                    continue
                path = Path(entry.path)
                if entry.is_symlink() or path.is_junction():
                    pruned = True
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if suite:
                        pending.append(path)
                elif (leaf.endswith(".py") and (leaf.startswith("test_") or leaf.endswith("_test.py"))
                      and entry.is_file(follow_symlinks=False)):
                    found.append(path)
    if not found:
        return "", []
    found.sort()
    if suite and not pruned:
        return "tests", found
    if len(found) == 1:
        return found[0].relative_to(workspace).as_posix(), found
    raise VerificationDiscoveryUnavailable("multiple test files without a safely scoped suite; select an exact test target")


async def discover_test_target(
    workspace: Path, cancel_event: Event, *, timeout: float = 3.0, max_entries: int = 1024,
) -> tuple[str, list[Path]]:
    stopped = Event()
    deadline = time.monotonic() + timeout
    entries = 0

    def checkpoint() -> None:
        nonlocal entries
        entries += 1
        if stopped.is_set() or cancel_event.is_set() or entries > max_entries or time.monotonic() >= deadline:
            raise VerificationDiscoveryUnavailable("test discovery did not settle within its scope and budget")

    try:
        return await asyncio.wait_for(asyncio.to_thread(_discover, workspace, checkpoint), timeout)
    except TimeoutError as exc:
        raise VerificationDiscoveryUnavailable("test discovery deadline exceeded") from exc
    except OSError as exc:
        raise VerificationDiscoveryUnavailable(f"test discovery failed ({type(exc).__name__})") from exc
    finally:
        # A blocked filesystem read can outlive its await, but it cannot dispatch tests.
        stopped.set()
