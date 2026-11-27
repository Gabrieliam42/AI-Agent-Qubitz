"""Bounded local inputs and atomic storage for repository retrieval."""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
import time
from collections import deque
from dataclasses import dataclass
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
_OVERVIEW_SUBJECT = r"(?:this|the(?: current)?|current)\s+(?:project|repo(?:sitory)?|codebase|workspace)"
_OVERVIEW_PROMPT = re.compile(
    rf"(?:(?:please\s+|(?:can|could|would) you\s+))?"
    rf"(?:(?:(?:tell me|find out|see)\s+)?what\s+(?:is\s+{_OVERVIEW_SUBJECT}(?:\s+about)?"
    rf"|is the purpose of\s+{_OVERVIEW_SUBJECT}|does\s+{_OVERVIEW_SUBJECT}\s+do"
    rf"|{_OVERVIEW_SUBJECT}\s+(?:is about|does))"
    rf"|what's\s+{_OVERVIEW_SUBJECT}(?:\s+about)?"
    rf"|(?:summari[sz]e|explain|describe)\s+{_OVERVIEW_SUBJECT}"
    rf"|(?:(?:give me|provide)\s+)?(?:an?\s+)?(?:overview|summary)\s+of\s+{_OVERVIEW_SUBJECT})",
    re.IGNORECASE,
)
_OVERVIEW_FILES = (
    "README.md", "README.rst", "README.txt", "README", "ARCHITECTURE.md",
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle.kts",
    "build.gradle", "composer.json", "Gemfile", "mix.exs", "pubspec.yaml", "CMakeLists.txt",
)
_OVERVIEW_PREFIX_BYTES = 2048
_OVERVIEW_SCAN_BYTES = 32 * 1024
_OVERVIEW_MAX_FILES = 4
_WORKFLOW_FILES = ("USAGE.md", "WORKFLOW.md", "CLI.md", "docs/usage.md", "docs/workflows.md", "docs/cli.md", *_OVERVIEW_FILES)
_WORKFLOW_REQUEST = re.compile(
    rf"\b(?:use|using|run|invoke|launch)\s+{_OVERVIEW_SUBJECT}\b"
    rf"|\b(?:with|through|via)\s+{_OVERVIEW_SUBJECT}\b",
    re.IGNORECASE,
)
FOCUSED_SKIP_DIRECTORIES = frozenset({
    "cv", "private", "secrets", "credentials", "vendor", "coverage", "generated",
    ".aws", ".gnupg", ".next", ".ssh", ".tox", ".mypy_cache",
})
FOCUSED_SCAN_SECONDS = 5
FOCUSED_READ_SECONDS = 5
FOCUSED_FILES = 48
FOCUSED_FILE_BYTES = 32 * 1024
FOCUSED_CHUNKS = 96
FOCUSED_EMBED_CHUNKS = 24
_NON_SOURCE_SUFFIXES = frozenset({
    ".avi", ".bmp", ".docx", ".gif", ".ico", ".jpeg", ".jpg", ".mov", ".mp3", ".mp4", ".pdf", ".png", ".pptx",
    ".rar", ".sqlite", ".ttf", ".wav", ".webp", ".woff", ".woff2", ".xlsx",
})
_TEST_DIRECTORIES = frozenset({"test", "tests", "spec", "specs", "fixtures", "__tests__"})
_TEST_INTENT = re.compile(r"\b(?:tests?|test suite|specs?|fixtures?|debug|diagnos\w*|fail\w*|regression|verify|assert\w*)\b", re.IGNORECASE)
_ENTRYPOINTS = frozenset({"main", "__main__", "app", "cli", "index", "server"})
_GENERAL_WORDS = frozenset({
    "about", "can", "code", "could", "explain", "find", "from", "have", "help", "how", "into", "look",
    "need", "please", "project", "repo", "repository", "should", "tell", "that", "the", "their", "them", "there",
    "these", "this", "use", "using", "what", "when", "where", "which", "with", "workspace", "would", "you",
})
_INTRO_HEADING = re.compile(r"^(?:overview|summary|introduction|about\b.*|what\b.*\b(?:is|does|do)\b.*|how it works)$", re.IGNORECASE)
_WORKFLOW_HEADING = re.compile(r"^(?:usage|quick start|getting started|workflows?|(?:.*\s)?commands?|how to use\b.*)$", re.IGNORECASE)
_PROJECT_GOAL_REPLY = re.compile(
    r"(?:i(?:'d like| want) (?:it|(?:this|the) (?:project|tool|app|repo(?:sitory)?)) to|"
    r"(?:have|make) (?:it|(?:this|the) (?:project|tool|app))|"
    r"use (?:it|(?:this|the) (?:project|tool|app)) to|the (?:goal|objective|desired result) is(?: to)?)\s+\S",
    re.IGNORECASE,
)


class RetrievalLimit(RuntimeError):
    """Retrieval stopped before it could obtain an adequately bounded snapshot."""


@dataclass(frozen=True)
class PendingProjectGoal:
    workspace: Path
    request: str

    def matches(self, workspace: Path, reply: str) -> bool:
        normalized = " ".join(reply.replace("\u2019", "'").split())
        return self.workspace == workspace and bool(_PROJECT_GOAL_REPLY.match(normalized))


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


def is_project_overview(query: str) -> bool:
    """Match standalone project-wide questions, not named targets or extra instructions."""
    return bool(_OVERVIEW_PROMPT.fullmatch(" ".join(query.replace("\u2019", "'").split()).rstrip("?.!")))


def requests_project_workflow(prompt: str) -> bool:
    return bool(_WORKFLOW_REQUEST.search(prompt)) and not is_project_overview(prompt)


def _open_overview_source(path: Path, expected: os.stat_result, size: int = _OVERVIEW_SCAN_BYTES):
    def opener(name: str, flags: int) -> int:
        return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))

    def unchanged(info: os.stat_result) -> bool:
        return stat.S_ISREG(info.st_mode) and os.path.samestat(info, expected) and (
            info.st_size, info.st_mtime_ns, info.st_ctime_ns
        ) == (expected.st_size, expected.st_mtime_ns, expected.st_ctime_ns)

    with open(path, "rb", opener=opener) as stream:
        if not unchanged(os.fstat(stream.fileno())):
            return None
        data = stream.read(size)
        if not unchanged(path.lstat()) or not unchanged(os.fstat(stream.fileno())):
            return None
    if b"\x00" in data:
        return None
    return data.decode("utf-8-sig", errors="replace").rstrip()


def _overview_excerpt(text: str, name: str, *, workflow: bool, query: str = "") -> tuple[int, str]:
    if name.endswith(".md"):
        lines = text.splitlines(keepends=True)
        candidates = (_WORKFLOW_HEADING, _INTRO_HEADING) if workflow else (_INTRO_HEADING,)
        headings = []
        fence = ""
        for i, line in enumerate(lines):
            if marker := re.match(r"^ {0,3}(`{3,}|~{3,})", line):
                if not fence or marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                    fence = "" if fence else marker[1]
            elif not fence and (match := re.fullmatch(r" {0,3}(#{1,6})\s+(.*?)\s*#*\s*", line)):
                headings.append((i, len(match[1]), match[2]))
        sections = []
        for slot, (i, level, label) in enumerate(headings):
            end = next((j for j, child_level, _ in headings[slot + 1:] if child_level <= level), len(lines))
            excerpt = "".join(lines[i:end])[:_OVERVIEW_PREFIX_BYTES].rstrip()
            if len(excerpt.splitlines()) > 1:
                sections.append((i, end, label, excerpt))
        if workflow and query:
            query_terms = _terms(query[:8192])
            ranked = []
            workflows = [(i, end) for i, end, label, _ in sections if _WORKFLOW_HEADING.fullmatch(label)]
            for i, _, label, excerpt in sections:
                title_hits = len(query_terms & _terms(label))
                workflow_heading = bool(_WORKFLOW_HEADING.fullmatch(label))
                in_workflow = any(start <= i < end for start, end in workflows)
                if title_hits >= 2 or in_workflow:
                    score = 3 * title_hits + len(query_terms & _terms(excerpt)) + 2 * workflow_heading
                    score += 2 if label.casefold() == "usage" else 0
                    ranked.append((score, i, excerpt))
            if ranked:
                _, i, excerpt = max(ranked, key=lambda item: (item[0], -item[1]))
                return i + 1, excerpt
        for pattern in candidates:
            for i, _, label, excerpt in sections:
                if pattern.fullmatch(label):
                    return i + 1, excerpt
    return 1, text[:_OVERVIEW_PREFIX_BYTES].rstrip()


def project_overview(workspace: Path, limit: int, checkpoint, *, workflow: bool = False, query: str = "") -> list[dict]:
    """Read bounded, line-attributed excerpts from conventional project documentation."""
    results = []
    for name in _WORKFLOW_FILES if workflow else _OVERVIEW_FILES:
        if len(results) >= min(limit, _OVERVIEW_MAX_FILES):
            break
        checkpoint()
        path = workspace / name
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
                continue
            if path.resolve(strict=True) != path:
                continue
            text = _open_overview_source(path, info)
            if path.resolve(strict=True) != path:
                continue
        except (OSError, RuntimeError):
            continue
        checkpoint()
        if text:
            line, excerpt = _overview_excerpt(text, name, workflow=workflow, query=query)
            results.append({"path": name, "start_line": line, "end_line": line - 1 + len(excerpt.splitlines()),
                            "score": 1.0, "text": excerpt})
    return results


@dataclass(frozen=True)
class FocusedSources:
    snippets: list[dict]
    stamps: dict[str, dict[str, int]]
    strong_match: bool
    truncated: bool


def _terms(text: str) -> set[str]:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return set(re.findall(r"[a-z0-9]{3,}", text.casefold())) - _GENERAL_WORDS


def _test_path(path: Path) -> bool:
    return (any(part.casefold() in _TEST_DIRECTORIES for part in path.parts[:-1])
            or path.stem.casefold().startswith("test_") or path.stem.casefold().endswith(("_test", ".test", ".spec")))


def focused_paths(workspace: Path, excluded, checkpoint):
    directories = deque([workspace])
    while directories:
        checkpoint()
        directory = directories.popleft()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    checkpoint(entry=True)
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        if not excluded(entry.name) and path != workspace / "evaluation" / "results":
                            directories.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        yield path
        except OSError:
            continue


def focused_sources(workspace: Path, query: str, paths, budget, *, overview: bool = False) -> FocusedSources:
    """Rank paths first, then read and rank a bounded number of source ranges."""
    query_terms = _terms(query[:8192])
    explicit_tests = bool(_TEST_INTENT.search(query[:8192]))
    ranked = []
    truncated = False
    try:
        for path in paths:
            rel = path.relative_to(workspace)
            if any(part.casefold() in FOCUSED_SKIP_DIRECTORIES for part in rel.parts):
                continue
            if rel.suffix.casefold() in (BINARY_SUFFIXES | _NON_SOURCE_SUFFIXES) or re.search(
                r"(?:\.min\.[^.]+$|\.lock$|(?:^|[-_.])(?:cv|resume|private|credentials?|secrets?)(?:[-_.]|$))",
                rel.name, re.IGNORECASE,
            ):
                continue
            entrypoint = rel.stem.casefold() in _ENTRYPOINTS
            purpose_doc = rel.name.casefold() in {"readme.md", "index.md", "overview.md", "architecture.md"}
            test = _test_path(rel)
            if overview and (test or not (entrypoint or purpose_doc)):
                continue
            path_terms = query_terms & _terms(rel.as_posix())
            path_hits = len(path_terms)
            topical = 4 * path_hits + (2 if entrypoint or purpose_doc else 0)
            topical += (3 if explicit_tests else -5) if test else 0
            ranked.append((topical, path_hits, len(rel.parts), rel.as_posix(), path, path_terms))
    except RetrievalLimit:
        truncated = True

    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    snippets = []
    stamps = {}
    chunk_cap = min(FOCUSED_CHUNKS, MAX_CHUNKS)
    byte_cap = min(MAX_SOURCE_BYTES, FOCUSED_FILES * FOCUSED_FILE_BYTES)
    remaining = byte_cap
    deadline = time.monotonic() + min(FOCUSED_READ_SECONDS, SCAN_TIMEOUT)
    truncated |= len(ranked) > FOCUSED_FILES
    for topical, _path_hits, _depth, rel, path, path_terms in ranked[:FOCUSED_FILES]:
        budget.check_cancelled()
        if time.monotonic() > deadline or remaining <= 0 or chunk_cap <= 0:
            truncated = True
            break
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
                continue
            if path.resolve(strict=True) != path:
                continue
            size = min(FOCUSED_FILE_BYTES, remaining)
            remaining -= min(info.st_size, size)
            stamp = file_stamp(path)
            text = _open_overview_source(path, info, size)
            if not text or file_stamp(path) != stamp or path.resolve(strict=True) != path:
                continue
        except (OSError, RuntimeError):
            continue
        truncated |= info.st_size > size
        lines = text.splitlines()
        windows = []
        for start in range(0, len(lines), 32):
            budget.check_cancelled()
            end = min(start + 40, len(lines))
            excerpt = "\n".join(lines[start:end]).strip("\n")
            if not excerpt.strip():
                continue
            terms = query_terms & _terms(excerpt)
            score = 3 * len(terms) + topical
            windows.append((score, len(terms | path_terms), start + 1, end, excerpt))
        windows.sort(key=lambda item: (-item[0], item[2]))
        if windows and stamp is not None:
            hold = windows[:1 if overview else 3]
            truncated |= len(windows) > len(hold)
            stamps[rel] = stamp
            snippets.extend({"path": rel, "start_line": start, "end_line": end, "text": excerpt,
                             "score": float(score), "matched": matches}
                            for score, matches, start, end, excerpt in hold)
    snippets.sort(key=lambda item: (-item["score"], item["path"], item["start_line"]))
    truncated |= len(snippets) > chunk_cap
    snippets = snippets[:chunk_cap]
    strong_threshold = max(2, min(3, len(query_terms)))
    return FocusedSources(snippets, stamps, any(item["matched"] >= strong_threshold for item in snippets), truncated)


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
    def __init__(self, check_cancelled, report, timeout: float | None = None):
        self.check_cancelled = check_cancelled
        self._report = report
        self._timeout = SCAN_TIMEOUT if timeout is None else min(timeout, SCAN_TIMEOUT)
        self._start = self._last_report = time.monotonic()
        self.entries = self.files = self.source_bytes = 0

    def check(self, *, entry: bool = False) -> None:
        self.check_cancelled()
        now = time.monotonic()
        if entry:
            self.entries += 1
        if (self.entries > MAX_SCAN_ENTRIES or self.source_bytes > MAX_SOURCE_BYTES
                or now - self._start > self._timeout):
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
