"""Bounded local inputs and atomic storage for repository retrieval."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from zipfile import BadZipFile, ZipFile

MAX_FILE_BYTES = 1024**2
MAX_SCAN_ENTRIES = 12_000
MAX_SOURCE_BYTES = 64 * 1024**2
MAX_CHUNKS = 8192
MAX_CACHE_BYTES = 256 * 1024**2
SCAN_TIMEOUT = 120
SKIP_DIRECTORIES = frozenset({".pytest_cache", "backup", "build", "conversations", "dist"})
BINARY_SUFFIXES = frozenset({
    ".7z", ".bin", ".ckpt", ".dll", ".exe", ".gguf", ".gz", ".iso", ".lib", ".npy",
    ".npz", ".pyd", ".safetensors", ".so", ".tar", ".vhdx", ".whl", ".zip",
})


class RetrievalLimit(RuntimeError):
    """Retrieval stopped before it could obtain an adequately bounded snapshot."""


def read_text_prefix(path: Path, size: int = MAX_FILE_BYTES, *, errors: str = "replace") -> tuple[str, bool]:
    with path.open("rb") as stream:
        data = stream.read(size + 1)
    truncated = len(data) > size
    if truncated and errors == "strict":
        from codecs import getincrementaldecoder

        return getincrementaldecoder("utf-8")().decode(data[:size], final=False), True
    return data[:size].decode("utf-8", errors=errors), truncated


def file_stamp(path: Path) -> dict[str, int] | None:
    info = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
        return None
    return {"mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "size": info.st_size, "inode": info.st_ino}


def cached_model_revision(cache_dir: Path, model_id: str) -> str | None:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", model_id) or any(
        part in {".", ".."} for part in model_id.split("/")
    ):
        return None
    repo = cache_dir / ("models--" + model_id.replace("/", "--"))
    try:
        with (repo / "refs" / "main").open("rb") as ref:
            revision = ref.read(64).decode("ascii").strip()
        if re.fullmatch(r"[a-f0-9]{40}", revision) and (
            repo / "snapshots" / revision / "config.json"
        ).is_file():
            return revision
    except (OSError, UnicodeError):
        pass
    return None


class ScanBudget:
    def __init__(self, check_cancelled, report):
        self._check_cancelled = check_cancelled
        self._report = report
        self._start = self._last_report = time.monotonic()
        self.entries = self.files = self.source_bytes = 0

    def check(self, *, entry: bool = False) -> None:
        self._check_cancelled()
        now = time.monotonic()
        if entry:
            self.entries += 1
        if (self.entries > MAX_SCAN_ENTRIES or self.source_bytes > MAX_SOURCE_BYTES
                or now - self._start > SCAN_TIMEOUT):
            raise RetrievalLimit("repository scan exceeded its entry, byte or time budget; narrow the target scope")
        if now - self._last_report >= 3:
            self._report(f"Retrieval scan: {self.entries} entries, {self.files} eligible files, {now - self._start:.1f}s elapsed.")
            self._last_report = now


def load_generation(path: Path, identity: dict):
    import numpy as np

    try:
        if path.stat().st_size > MAX_CACHE_BYTES:
            return None
        with ZipFile(path) as payload:
            if sum(item.file_size for item in payload.infolist()) > MAX_CACHE_BYTES:
                return None
        with np.load(path, allow_pickle=False) as archive:
            manifest = json.loads(archive["manifest"].item())
            if (manifest["identity"] != identity or not isinstance(manifest["file_state"], dict)
                    or not isinstance(manifest["chunks"], list) or len(manifest["chunks"]) > MAX_CHUNKS):
                return None
            if any(not isinstance(chunk, dict) or chunk.get("path") not in manifest["file_state"]
                   for chunk in manifest["chunks"]):
                return None
            vectors = archive["vectors"] if "vectors" in archive.files else None
            if vectors is not None and (vectors.ndim != 2 or vectors.dtype != np.float32
                                        or not vectors.shape[1] or not np.isfinite(vectors).all()
                                        or len(vectors) != len(manifest["chunks"])):
                return None
            return manifest, vectors
    except (OSError, ValueError, TypeError, KeyError, EOFError, AttributeError, BadZipFile):
        return None


def save_generation(path: Path, manifest: dict, vectors, checkpoint) -> None:
    import numpy as np

    arrays = {"manifest": json.dumps(manifest, ensure_ascii=True, separators=(",", ":"))}
    if vectors is not None:
        arrays["vectors"] = vectors
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix="retrieval-", suffix=".tmp", delete=False) as stream:
            pending = Path(stream.name)
            np.savez(stream, **arrays)
            stream.flush()
            os.fsync(stream.fileno())
        if pending.stat().st_size > MAX_CACHE_BYTES:
            raise RetrievalLimit("retrieval cache generation exceeds its storage budget")
        checkpoint()
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
