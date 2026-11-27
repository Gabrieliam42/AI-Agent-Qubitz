"""Turn-local backend startup status; never changes probes or timeout policy."""
from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path


class Progress:
    def __init__(self, emit, clock=time.monotonic):
        self.emit = emit
        self.clock = clock
        self.started_at = clock()
        self.last_at = self.started_at
        self.last_stage = ""
        self.display_error = ""

    def update(self, stage: str) -> None:
        now = self.clock()
        if stage != self.last_stage or now - self.last_at >= 8:
            self.last_at, self.last_stage = now, stage
            try:
                self.emit(f"{stage} ({now - self.started_at:.0f}s elapsed).")
            except Exception as error:  # noqa: BLE001 - display failures must not affect startup
                self.display_error = type(error).__name__


_CURRENT: ContextVar[Progress | None] = ContextVar("qubitz_model_startup_progress", default=None)


@contextmanager
def capture(emit):
    token = _CURRENT.set(Progress(emit))
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def startup_progress(callback):
    with capture(lambda message: callback("status", message)):
        yield


def report(stage: str) -> None:
    if (progress := _CURRENT.get()) is not None:
        progress.update(stage)


def checked_sha256(stream):
    progress = _CURRENT.get()
    if progress is None:
        return hashlib.file_digest(stream, "sha256").hexdigest()
    size = os.fstat(stream.fileno()).st_size
    if size < 64 * 1024 * 1024:
        return hashlib.file_digest(stream, "sha256").hexdigest()
    label = Path(stream.name).name
    started = last_update = time.monotonic()
    digest = hashlib.sha256()
    block = bytearray(1024 * 1024)
    view = memoryview(block)
    done = 0

    def emit(message):
        with suppress(Exception):
            progress.emit(message)

    emit(f"Validating cached runtime files: hashing {label} ({size / 1048576:.0f} MiB). Model loading has not started.")
    while used := stream.readinto(block):
        digest.update(view[:used])
        done += used
        now = time.monotonic()
        if now - last_update >= 5 and done < size:
            emit(f"Artifact checksum progress: {label}: {done / 1048576:.0f}/{size / 1048576:.0f} MiB.")
            last_update = now
    emit(f"Checksum calculated for {label} in {time.monotonic() - started:.1f}s; checking pinned digest.")
    return digest.hexdigest()
