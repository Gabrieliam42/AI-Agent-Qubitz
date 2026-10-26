"""Lossless migration, variant edits, and standalone export for shared Qubitz source."""
from __future__ import annotations

import argparse
import ast
import difflib
import pprint
from pathlib import Path
from typing import Any

from .paths import variant_paths
from .source import (
    BASE_MARKER,
    LAUNCHER_BOOTSTRAP,
    RUNTIME_DIRECTORY,
    SECTION_PATTERN,
    SPEC_NAME,
    compose_variant,
    embedded_source,
    launcher_spec,
    read_variant_source,
    template_source,
)
from .symbols import REFERENCE_PATTERN, expand_symbols, symbol_sources

ROOT = RUNTIME_DIRECTORY.parent


def _project_path(path: Path) -> Path:
    root = ROOT.resolve()
    path = Path(path)
    resolved = (path if path.is_absolute() else root / path).resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError(f"Path must remain inside the Qubitz project root: {path}")
    return resolved


def backup(path: Path, description: str) -> Path:
    path = _project_path(path)
    relative = path.relative_to(ROOT.resolve()).as_posix()
    prefix = relative.replace("/", "__")
    directory = _project_path(Path("backup"))
    changelog = _project_path(Path("changelog.txt"))
    directory.mkdir(exist_ok=True)
    number = 1
    while ((directory / f"{prefix}.{number:03}.bak").exists()
           or (directory / f"{prefix}.{number:03}.bak").is_symlink()):
        number += 1
    destination = directory / f"{prefix}.{number:03}.bak"
    with destination.open("xb") as stream:
        stream.write(path.read_bytes())
    if destination.read_bytes() != path.read_bytes():
        raise RuntimeError(f"Backup verification failed: {relative}")
    with changelog.open("a", encoding="utf-8") as stream:
        stream.write(f"backup/{destination.name} | {relative} | {description}\n")
    return destination


def launcher_text(spec: dict[str, Any]) -> str:
    return (
        '"""Variant-specific configuration and source for the shared Qubitz runtime."""\n'
        "from __future__ import annotations\n\n"
        + LAUNCHER_BOOTSTRAP
        + "from qubitz_runtime import initialize_variant as _initialize_variant\n\n"
        + SPEC_NAME + " = " + pprint.pformat(spec, width=120, sort_dicts=False) + "\n\n"
        + "_initialize_variant(globals(), " + SPEC_NAME + ")\n"
    )


def source_layers(text: str) -> dict[str, str]:
    base, (start, end) = embedded_source(text)
    return {"wrapper": text[:start] + BASE_MARKER + text[end:], "base": base}


def _mapped_intervals(reference: list[str], other: list[str], intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    blocks = difflib.SequenceMatcher(None, reference, other, autojunk=False).get_matching_blocks()
    result = []
    for start, end in intervals:
        block = next((block for block in blocks if block.a <= start and end <= block.a + block.size), None)
        if block is None:
            raise ValueError("A shared source interval changed; edit shared runtime source instead")
        result.append((block.b + start - block.a, block.b + end - block.a))
    return result


def split_sections(lines: list[str], intervals: list[tuple[int, int]], layer: str) -> tuple[str, dict[str, str]]:
    pieces = []
    sections = {}
    cursor = 0
    for start, end in intervals:
        key = f"{len(sections):03}"
        sections[key] = "".join(lines[cursor:start])
        pieces.append(f"# <QUBITZ_VARIANT_SECTION:{layer}:{key}>\n")
        pieces.append("".join(lines[start:end]))
        cursor = end
    key = f"{len(sections):03}"
    sections[key] = "".join(lines[cursor:])
    pieces.append(f"# <QUBITZ_VARIANT_SECTION:{layer}:{key}>\n")
    return "".join(pieces), sections


def _template_intervals(layer: str, sections: dict[str, str], schema_version: int = 2) -> tuple[list[str], list[tuple[int, int]]]:
    template = template_source(layer, schema_version)
    parts = []
    intervals = []
    cursor = 0
    total = 0
    for match in SECTION_PATTERN.finditer(template):
        common = template[cursor:match.start()]
        if common:
            count = len(common.splitlines(keepends=True))
            intervals.append((total, total + count))
            total += count
            parts.append(common)
        specific = sections[match[2]]
        total += len(specific.splitlines(keepends=True))
        parts.append(specific)
        cursor = match.end()
    common = template[cursor:]
    if common:
        intervals.append((total, total + len(common.splitlines(keepends=True))))
        parts.append(common)
    return "".join(parts).splitlines(keepends=True), intervals


def replace_variant_source(path: Path, candidate: str) -> bool:
    path = _project_path(path)
    current = read_variant_source(path)
    if current == candidate:
        return False
    spec = launcher_spec(path.read_text(encoding="utf-8"))
    if spec is None:
        raise ValueError("Expected a modular Qubitz launcher")
    layers = source_layers(candidate)
    updated = {"schema_version": spec["schema_version"], "filename": path.name}
    for layer in ("wrapper", "base"):
        previous, intervals = _template_intervals(layer, spec[layer], spec["schema_version"])
        candidate_layer = layers[layer]
        values = symbol_sources(layer, RUNTIME_DIRECTORY)
        old_tokens = "".join(previous)
        old_expanded = expand_symbols(old_tokens, layer, values).splitlines(keepends=True)
        intervals_to_restore = []
        references = []
        cursor = 0
        line = 0
        for match in REFERENCE_PATTERN.finditer(old_tokens):
            line += len(old_tokens[cursor:match.start()].splitlines(keepends=True))
            count = len(values[match[2]].splitlines(keepends=True))
            intervals_to_restore.append((line, line + count))
            references.append(match[0])
            line += count
            cursor = match.end()
        candidate_lines = candidate_layer.splitlines(keepends=True)
        restored = _mapped_intervals(old_expanded, candidate_lines, intervals_to_restore)
        for (start, end), reference in reversed(list(zip(restored, references, strict=True))):
            candidate_lines[start:end] = [reference]
        candidate_layer = "".join(candidate_lines)
        lines = candidate_layer.splitlines(keepends=True)
        mapped = _mapped_intervals(previous, lines, intervals)
        template = template_source(layer, spec["schema_version"])
        matches = list(SECTION_PATTERN.finditer(template))
        common = SECTION_PATTERN.split(template)
        # split() includes the pattern's capture groups; keep literal pieces only.
        common = [common[0], *(common[index] for index in range(3, len(common), 3))]
        if any(not item for item in common[1:-1]):
            raise ValueError("Adjacent source slots require an explicit boundary before editing")
        shared = iter(mapped)
        positions = [next(shared) if item else None for item in common]
        if positions[0] is not None and positions[0][0] != 0:
            raise ValueError("Variant edit inserted code before shared source")
        if positions[-1] is not None and positions[-1][1] != len(lines):
            raise ValueError("Variant edit appended code after shared source")
        sections = {}
        for index, match in enumerate(matches):
            start = positions[index][1] if positions[index] is not None else 0
            end = positions[index + 1][0] if positions[index + 1] is not None else len(lines)
            sections[match[2]] = "".join(lines[start:end])
        updated[layer] = sections
    if compose_variant(updated, path.name) != candidate:
        raise ValueError("Variant edit could not be represented losslessly")
    ast.parse(candidate)
    backup(path, "Update only variant-specific source while retaining shared runtime sections.")
    path.write_text(launcher_text(updated), encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    args = parser.parse_args()
    output = args.export.resolve()
    if not output.is_relative_to(ROOT) or output == ROOT:
        raise ValueError("Standalone export must use a separate project-local directory")
    output.mkdir(parents=True, exist_ok=False)
    for path in variant_paths(ROOT):
        (output / path.name).write_text(read_variant_source(path), encoding="utf-8", newline="\n")
    (output / "HARNESS.enc").write_bytes((ROOT / "HARNESS.enc").read_bytes())
    (output / "README.txt").write_text(
        "Standalone Qubitz Python exports, without a shared-runtime dependency.\n"
        "The encrypted harness is included; no plaintext harness or private key is exported.\n"
        "Provide the private harness key through the existing runtime mechanism and use the project dependencies.\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
