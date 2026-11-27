"""One append-only Markdown memory per workspace, with bounded retrieval."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

CURRENT_MEMORY_NAME = "MEMORY.md"
_LEGACY_NAME = re.compile(r"MEMORY_\d{8}(?:_\d{6})?\.md")
_ENTRY_TAG = re.compile(r"(?m)^<!-- qubitz-memory-entry:([0-9a-f]{64}) -->$")
_IMPORT_TAG = re.compile(r"(?m)^<!-- qubitz-memory-import:(MEMORY_[0-9_]+\.md):([0-9a-f]{64}) -->$")
_SOURCE_TAG = re.compile(r"^<!-- source: ([^<>]+) -->$")
_HEADINGS = {
    "# Qubitz Memory", "# Qubitz Daily Memory", "# Qubitz Consolidated Memory",
    "## Notes", "## Entries", "## Recent Turns",
    "- No notes recorded yet.", "No conversation turns recorded yet.",
    "Generated from current and archived memory files.",
}


def _timestamp() -> str:
    return datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def _read(path: Path) -> str:
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return ""


def _paragraphs(text: str) -> Iterator[tuple[str, tuple[str, ...]]]:
    for block in re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n")):
        lines, sources = [], []
        for line in block.splitlines():
            if source := _SOURCE_TAG.fullmatch(line):
                sources.extend(name.strip() for name in source[1].split(",") if name.strip())
            elif (line not in _HEADINGS and not _ENTRY_TAG.fullmatch(line)
                  and not _IMPORT_TAG.fullmatch(line)):
                lines.append(line)
        if lines and not all(line.startswith(("Session ID:", "Date:", "Updated:", "Workspace:")) for line in lines):
            content = "\n".join(lines).strip("\n")
            if content.strip():
                yield content, tuple(dict.fromkeys(sources)) or (CURRENT_MEMORY_NAME,)


def _ranked(text: str, query: str) -> list[tuple[int, str, tuple[str, ...]]]:
    tokens = set(re.findall(r"[a-z0-9_./:-]+", query.casefold()))
    seen = set()
    ranked = []
    for content, sources in reversed(tuple(_paragraphs(text))):
        if content in seen:
            continue
        seen.add(content)
        lowered = content.casefold()
        score = sum(lowered.count(token) for token in tokens) if tokens else 1
        ranked.append((score, content, sources))
    return sorted(ranked, key=lambda entry: entry[0], reverse=True)


def search_memory_file(path: Path, query: str, limit: int = 5) -> list[dict[str, str]]:
    return [
        {"path": path.relative_to(path.parent.parent).as_posix(), "score": str(score),
         "snippet": _clip(content, 320)}
        for score, content, _sources in _ranked(_read(path), query)[:max(0, limit)]
        if score > 0
    ]


class MemoryStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.memory_dir = self.workspace / ".memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.current_path = self.memory_dir / CURRENT_MEMORY_NAME
        self.notes: list[dict[str, str]] = []
        self.turns: list[dict[str, str]] = []
        # Share the lock name with older daily-store writers.
        self.lock_path = self.memory_dir / ".daily-memory.lock"
        with self._lock():
            existing = _read(self.current_path)
            self._append(existing, self._import_legacy(existing))

    @contextmanager
    def _lock(self) -> Iterator[None]:
        deadline = time.monotonic() + 20.0
        while True:
            try:
                descriptor = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Memory lock is still held: {self.lock_path}") from None
                time.sleep(0.05)
        try:
            try:
                os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
            finally:
                os.close(descriptor)
            yield
        finally:
            self.lock_path.unlink(missing_ok=True)

    def _append(self, existing: str, additions: list[str]) -> None:
        if existing and not additions:
            return
        if not existing:
            existing = "# Qubitz Memory\n"
        separator = "" if existing.endswith(("\n\n", "\r\n\r\n")) else "\n" if existing.endswith("\n") else "\n\n"
        updated = existing + (separator + "\n\n".join(additions) + "\n" if additions else "")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.memory_dir, prefix=".MEMORY.", suffix=".tmp", delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(updated.encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.current_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _import_legacy(self, existing: str) -> list[str]:
        imported = set(_IMPORT_TAG.findall(existing))
        known = {content for content, _sources in _paragraphs(existing)}
        additions = []
        for path in sorted(self.memory_dir.glob("MEMORY_*.md")):
            if not _LEGACY_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file():
                continue
            text = _read(path)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if (path.name, digest) in imported:
                continue
            additions.append(f"<!-- qubitz-memory-import:{path.name}:{digest} -->")
            for content, _sources in _paragraphs(text):
                if content not in known:
                    known.add(content)
                    additions.append(f"<!-- source: {path.name} -->\n{content}")
        return additions

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append({"role": role, "content": _clip(content.strip(), 4000), "timestamp": _timestamp()})
        self.turns = self.turns[-20:]
        self.flush()

    def add_note(self, note: str, category: str = "note") -> None:
        if not note.strip():
            return
        self.notes.append({"category": category, "text": _clip(note.strip(), 1000), "timestamp": _timestamp()})
        self.notes = self.notes[-40:]
        self.flush()

    def _new_entries(self, existing: str) -> list[str]:
        known = set(_ENTRY_TAG.findall(existing))
        additions = []

        def add(kind: str, text: str, formatted: str) -> None:
            digest = hashlib.sha256(f"{kind}\0{text}".encode()).hexdigest()
            if text and digest not in known:
                known.add(digest)
                additions.append(f"<!-- qubitz-memory-entry:{digest} -->\n{formatted}")

        for note in self.notes:
            category, text = note["category"], note["text"]
            add(f"note:{category}", text, f"- [{note['timestamp']}] ({category}) {text}")
        for turn in self.turns:
            role, text = turn["role"], turn["content"]
            add(f"turn:{role.casefold()}", text, f"### {role.title()} [{turn['timestamp']}]\n{text}")
        return additions

    def flush(self) -> None:
        with self._lock():
            existing = _read(self.current_path)
            self._append(existing, self._new_entries(existing))

    def search(self, query: str, limit: int = 5, include_current: bool = True) -> list[dict[str, str]]:
        return search_memory_file(self.current_path, query, limit) if include_current else []

    def build_context(self, query: str) -> str:
        lines = ["Persistent memory:"]
        remaining = 1200 - len(lines[0])
        ranked = _ranked(_read(self.current_path), query)
        if any(score for score, _content, _sources in ranked):
            ranked = [entry for entry in ranked if entry[0] > 0]
        for _score, content, sources in ranked:
            if remaining < 90:
                break
            prefix = f"- [source: {_clip(', '.join(sources), 60)}] "
            line = prefix + _clip(content, min(320, remaining - len(prefix) - 1))
            lines.append(line)
            remaining -= len(line) + 1
        return "\n".join(lines) if len(lines) > 1 else ""
