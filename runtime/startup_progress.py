"""Report artifact validation without changing which bytes are hashed."""

import hashlib
import os
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from pathlib import Path

_callback = ContextVar("qubitz_startup_progress", default=None)


@contextmanager
def startup_progress(callback):
    token = _callback.set(callback)
    try:
        yield
    finally:
        _callback.reset(token)


def checked_sha256(stream):
    callback = _callback.get()
    if callback is None:
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
            callback("status", message)

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
