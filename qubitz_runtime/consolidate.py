"""Centralize universal AST statements, including declarations moved between variants."""
from __future__ import annotations

import ast
import difflib
import hashlib
import json
from collections import Counter

from .development import (
    PROFILE_NAMES,
    ROOT,
    _mapped_intervals,
    backup,
    launcher_text,
    source_layers,
    split_sections,
)
from .source import RUNTIME_DIRECTORY, compose_variant, read_variant_source

REFERENCE = "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"


def statements(text: str, layer: str) -> dict[str, tuple[int, int, str]]:
    lines = text.splitlines(keepends=True)
    result = {}

    def visit(nodes: list[ast.stmt], scope: str) -> None:
        counts = Counter()
        for node in nodes:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {target.id for target in targets if isinstance(target, ast.Name)}
                if "_EMBEDDED_BASE_SOURCE" in names or layer == "base" and names & PROFILE_NAMES:
                    continue
                name = ",".join(ast.dump(target, include_attributes=False) for target in targets)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                name = ast.dump(node, include_attributes=False)
            else:
                name = type(node).__name__
            kind = type(node).__name__ + ":" + name
            index = counts[kind]
            counts[kind] += 1
            key = f"{scope}/{kind}:{index}"
            start = min([node.lineno, *(item.lineno for item in getattr(node, "decorator_list", []))]) - 1
            end = node.end_lineno
            result[key] = (start, end, "".join(lines[start:end]))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(node.body, key)

    visit(ast.parse(text).body, "module")
    return result


def tokenize(text: str, nodes: dict[str, tuple[int, int, str]], universal: set[str], layer: str,
             registry: dict[str, tuple[str, str]]) -> str:
    lines = text.splitlines(keepends=True)
    spans = sorted((nodes[key][0], nodes[key][1], key) for key in universal)
    chosen = []
    cursor = 0
    for start, end, key in sorted(spans, key=lambda item: (item[0], -item[1])):
        if start < cursor:
            continue
        value = nodes[key][2]
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
        if digest in registry and registry[digest][0] != value:
            raise ValueError("Universal source identifier collision")
        registry[digest] = (value, key)
        chosen.append((start, end, f"# <QUBITZ_SHARED_REFERENCE:{layer}:{digest}>\n"))
        cursor = end
    for start, end, replacement in reversed(chosen):
        lines[start:end] = [replacement]
    return "".join(lines)


def common_intervals(reference: list[str], others: list[list[str]]) -> list[tuple[int, int]]:
    changed = [False] * len(reference)
    boundaries = set()
    for other in others:
        for tag, start, end, _a, _b in difflib.SequenceMatcher(None, reference, other, autojunk=False).get_opcodes():
            if tag != "equal":
                changed[start:end] = [True] * (end - start)
                boundaries.update((start, end))
    intervals = []
    start = None
    for index, differs in enumerate(changed):
        if index in boundaries and start is not None:
            intervals.append((start, index))
            start = None
        if differs:
            if start is not None:
                intervals.append((start, index))
                start = None
        elif start is None:
            start = index
    if start is not None:
        intervals.append((start, len(reference)))
    return intervals


def consolidate() -> None:
    from .paths import all_variant_paths

    paths = all_variant_paths(ROOT)
    sources = {path.name: read_variant_source(path) for path in paths}
    fingerprints = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    layers = {name: source_layers(text) for name, text in sources.items()}
    templates = {}
    registries = {}
    specs = {path.name: {"schema_version": 2, "filename": path.name} for path in paths}
    counts = {}
    for layer in ("wrapper", "base"):
        by_variant = {name: statements(item[layer], layer) for name, item in layers.items()}
        universal = set.intersection(*(set(item) for item in by_variant.values()))
        universal = {key for key in universal if len({item[key][2] for item in by_variant.values()}) == 1}
        registry = {}
        tokenized = {name: tokenize(item[layer], by_variant[name], universal, layer, registry)
                     for name, item in layers.items()}
        reference = tokenized[REFERENCE].splitlines(keepends=True)
        intervals = common_intervals(reference, [text.splitlines(keepends=True) for text in tokenized.values()])
        template, _ = split_sections(reference, intervals, layer)
        templates[layer] = template
        registries[layer] = {key: value[0] for key, value in registry.items()}
        counts[layer] = len(registry)
        for path in paths:
            lines = tokenized[path.name].splitlines(keepends=True)
            mapped = _mapped_intervals(reference, lines, intervals)
            candidate, sections = split_sections(lines, mapped, layer)
            if candidate != template:
                raise ValueError(f"AST source layout mismatch: {path.name}:{layer}")
            specs[path.name][layer] = sections
        registry_text = "".join(
            f"# <QUBITZ_SHARED_SYMBOL:{layer}:{key}> {label}\n{value}# </QUBITZ_SHARED_SYMBOL>\n"
            for key, (value, label) in sorted(registry.items())
        )
        templates[layer + "_symbols"] = registry_text
    # All source and syntax checks complete before any live layout is changed.
    for path in paths:
        candidate = compose_variant(specs[path.name], path.name, templates=templates, symbols=registries)
        if candidate != sources[path.name]:
            raise ValueError(f"AST consolidation changed effective source: {path.name}")
        compile(candidate, str(path), "exec")
        ast.parse(launcher_text(specs[path.name]))
    directory = RUNTIME_DIRECTORY / "v2"
    directory.mkdir(exist_ok=False)
    for layer in ("wrapper", "base"):
        (directory / f"{layer}.py.in").write_text(templates[layer], encoding="utf-8", newline="\n")
        (directory / f"{layer}.shared.py.in").write_text(templates[layer + "_symbols"], encoding="utf-8", newline="\n")
    receipt = []
    for path in paths:
        if hashlib.sha256(path.read_bytes()).hexdigest() != fingerprints[path.name]:
            raise ValueError(f"Concurrent launcher change: {path.name}")
        preserved = backup(path, "Centralize universal AST declarations while preserving exact variant runtime source.")
        path.write_text(launcher_text(specs[path.name]), encoding="utf-8", newline="\n")
        if read_variant_source(path) != sources[path.name]:
            raise RuntimeError(f"AST post-write source verification failed: {path.name}")
        receipt.append({"variant": path.name, "backup": preserved.relative_to(ROOT).as_posix(),
                        "source_sha256": hashlib.sha256(sources[path.name].encode()).hexdigest(),
                        "launcher_bytes": path.stat().st_size})
    (ROOT / ".cache/shared_runtime_ast_consolidation_20261013.json").write_text(
        json.dumps({"universal_symbols": counts, "variants": receipt}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"variants": len(paths), "universal_symbols": counts,
                      "launcher_bytes": sum(path.stat().st_size for path in paths)}), flush=True)


if __name__ == "__main__":
    consolidate()
