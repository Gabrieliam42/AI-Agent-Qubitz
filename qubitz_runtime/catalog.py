"""Read-only default-model file-set indicators; no model imports or downloads."""
from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .source import launcher_spec


@dataclass(frozen=True)
class ModelFiles:
    repo: str
    filename: str
    shards: tuple[str, ...]
    parts: tuple[str, ...] = ()
    subdirectories: tuple[str, ...] = ()


def _safe_relative(value: str, *, basename: bool = False) -> bool:
    path = PurePosixPath(value)
    return bool(value) and "\\" not in value and ":" not in value and not path.is_absolute() and (
        all(part not in {".", "..", ""} for part in value.split("/"))
        and (not basename or path.name == value)
    )


def _constants(fragments: dict[str, str]) -> dict[str, object]:
    values: dict[str, object] = {}
    pattern = re.compile(r"(?m)^([A-Z][A-Z0-9_]*(?:GGUF_REPO_ID|GGUF_MODEL_FILENAME|GGUF_SHARDS|GGUF_PARTS|GGUF_SUBDIR))\s*=")
    for fragment in fragments.values():
        for match in pattern.finditer(fragment):
            lines = fragment[match.start():].splitlines(keepends=True)
            # Read one literal assignment, including bounded tuple/list continuations.
            for count in range(1, min(len(lines), 256) + 1):
                try:
                    tree = ast.parse("".join(lines[:count]))
                except SyntaxError:
                    continue
                if len(tree.body) != 1 or not isinstance(tree.body[0], ast.Assign):
                    break
                try:
                    value = ast.literal_eval(tree.body[0].value)
                except (ValueError, TypeError):
                    break
                name = match[1]
                if name in values and values[name] != value:
                    raise ValueError("Conflicting literal model metadata")
                values[name] = value
                break
    return values


def model_files(path: Path) -> ModelFiles | None:
    try:
        spec = launcher_spec(path.read_text(encoding="utf-8"))
        if spec is None or spec.get("filename") != path.name:
            return None
        if any(not isinstance(spec.get(layer), dict)
               or any(not isinstance(value, str) for value in spec[layer].values())
               for layer in ("base", "wrapper")):
            return None
        base = _constants(spec["base"])
        wrapper = _constants(spec["wrapper"])
        repo = base.get("DEFAULT_HF_GGUF_REPO_ID")
        filename = base.get("DEFAULT_GGUF_MODEL_FILENAME")
        if (not isinstance(repo, str) or not isinstance(filename, str)
                or not _safe_relative(repo) or len(repo.split("/")) != 2
                or not _safe_relative(filename, basename=True)):
            return None
        split = re.fullmatch(r"(.+)-00001-of-(\d{5})\.gguf", filename)
        if split:
            count = int(split[2])
            if not 1 <= count <= 256:
                return None
            shards = tuple(f"{split[1]}-{number:05}-of-{count:05}.gguf" for number in range(1, count + 1))
        else:
            shards = (filename,)
        parts: tuple[str, ...] = ()
        for key, value in wrapper.items():
            if (key.endswith("GGUF_SHARDS") and isinstance(value, (list, tuple))
                    and filename in value and tuple(value) != shards):
                return None
            if key.endswith("GGUF_PARTS") and isinstance(value, (list, tuple)) and value:
                expected = tuple(f"{filename}.part{number}of{len(value)}" for number in range(1, len(value) + 1))
                if len(value) <= 256 and tuple(value) == expected:
                    parts = expected
        subdirectories = tuple(value for key, value in wrapper.items()
                               if key.endswith("GGUF_SUBDIR") and isinstance(value, str) and _safe_relative(value))
        return ModelFiles(repo, filename, shards, parts, subdirectories)
    except (OSError, UnicodeError, SyntaxError, ValueError, TypeError, KeyError):
        return None


def _complete(directory: Path, names: tuple[str, ...], root: Path, *, multipart: bool = False) -> bool:
    if not names:
        return False
    for index, name in enumerate(names):
        path = directory / name
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        if not path.resolve().is_relative_to(root) or not path.is_file() or path.stat().st_size <= 0:
            return False
        if not multipart or index == 0:
            if path.stat().st_size < 24:
                return False
            with path.open("rb") as stream:
                if stream.read(4) != b"GGUF":
                    return False
    return True


def cached_model_states(paths: Iterable[Path], runtime_root: Path) -> dict[str, bool | None]:
    """True means all named local files look available, not verified model integrity."""
    root = runtime_root.resolve()
    states: dict[str, bool | None] = {}
    unresolved: dict[str, ModelFiles] = {}
    search_roots = {root / "models"}
    unknown = False
    missing_directories: set[Path] = set()
    directory_availability: dict[Path, bool] = {}

    def available(directory: Path, metadata: ModelFiles) -> bool:
        if directory in missing_directories:
            return False
        if directory not in directory_availability:
            directory_availability[directory] = directory.is_dir()
        if not directory_availability[directory]:
            return False
        return (_complete(directory, metadata.shards, root)
                or _complete(directory, metadata.parts, root, multipart=True))

    # Local to this scan: later refreshes see new files/links. Every present
    # model file still resolves independently before its header is read.
    resolved_directories: dict[Path, bool] = {}

    def inside_directory(directory: Path) -> bool:
        if directory not in resolved_directories:
            if directory == root:
                inside = True
            else:
                try:
                    directory.lstat()
                except FileNotFoundError:
                    # An absent non-link cannot redirect resolution. Its nearest
                    # existing ancestor still needs the normal containment check.
                    missing_directories.add(directory)
                    inside = inside_directory(directory.parent)
                else:
                    inside = directory.resolve().is_relative_to(root)
            resolved_directories[directory] = inside
        return resolved_directories[directory]

    for path in paths:
        metadata = model_files(path)
        states[path.name] = None
        if metadata is None:
            continue
        repository = root / ".cache" / "models" / metadata.repo
        preferred = [*(repository / value for value in metadata.subdirectories), repository, root / "models", root]
        try:
            for directory in preferred:
                if not inside_directory(directory):
                    unknown = True
                    continue
                if available(directory, metadata):
                    states[path.name] = True
                    break
            else:
                unresolved[path.name] = metadata
                search_roots.add(repository)
        except (OSError, RuntimeError):
            unknown = True
    if not unresolved:
        return states

    wanted: dict[str, set[str]] = {}
    for name, metadata in unresolved.items():
        for first in (metadata.filename, *(metadata.parts[:1])):
            wanted.setdefault(first, set()).add(name)
    seen: set[Path] = set()
    visited = 0

    def walk_error(error: OSError) -> None:
        nonlocal unknown
        if not isinstance(error, FileNotFoundError):
            unknown = True

    # Accommodate nested local model folders without walking the project,
    # virtual environment, datasets, or unrelated caches. Never follow links.
    for search_root in sorted(search_roots):
        try:
            if search_root in missing_directories:
                continue
            try:
                search_root.lstat()
            except FileNotFoundError:
                continue
            inside = search_root.resolve().is_relative_to(root)
        except (OSError, RuntimeError):
            unknown = True
            continue
        if not inside:
            unknown = True
            continue
        for current, directories, files in os.walk(search_root, followlinks=False, onerror=walk_error):
            visited += len(directories) + len(files) + 1
            if visited > 4096:
                unknown = True
                break
            directories[:] = [name for name in directories if name not in {".cache", ".git", "__pycache__"}]
            directory = Path(current)
            if directory in seen:
                continue
            seen.add(directory)
            candidates = set().union(*(wanted[name] for name in files if name in wanted))
            for name in candidates:
                try:
                    if available(directory, unresolved[name]):
                        states[name] = True
                except (OSError, RuntimeError):
                    unknown = True
        if visited > 4096:
            break
    for name in unresolved:
        if states[name] is not True:
            states[name] = None if unknown else False
    return states


def cached_model_state(path: Path, runtime_root: Path) -> bool | None:
    return cached_model_states((path,), runtime_root)[path.name]
