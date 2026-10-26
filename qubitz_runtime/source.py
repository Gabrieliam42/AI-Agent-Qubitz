"""Resolve effective runtime source without importing or executing a variant."""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .symbols import expand_symbols, symbol_sources

RUNTIME_DIRECTORY = Path(__file__).resolve().parent
SPEC_NAME = "_QUBITZ_VARIANT_SPEC"
BASE_MARKER = "__QUBITZ_EMBEDDED_SOURCE_SLOT__"
SECTION_PATTERN = re.compile(r"(?m)^# <QUBITZ_VARIANT_SECTION:(wrapper|base):(\d{3})>\n")
LAUNCHER_BOOTSTRAP = (
    "import sys as _qubitz_bootstrap_sys\n"
    "from pathlib import Path as _QubitzBootstrapPath\n\n"
    "_qubitz_bootstrap_root = _QubitzBootstrapPath(__file__).resolve().parent\n"
    "if _qubitz_bootstrap_root.name == 'variants':\n"
    "    _qubitz_bootstrap_root = _qubitz_bootstrap_root.parent\n"
    "if str(_qubitz_bootstrap_root) not in _qubitz_bootstrap_sys.path:\n"
    "    _qubitz_bootstrap_sys.path.insert(0, str(_qubitz_bootstrap_root))\n\n"
)


def launcher_spec(text: str) -> dict[str, Any] | None:
    """Read a literal configuration, never execute a launcher to inspect it."""
    if SPEC_NAME not in text:
        return None
    tree = ast.parse(text)
    values = [node.value for node in tree.body
              if isinstance(node, ast.Assign) and any(
                  isinstance(target, ast.Name) and target.id == SPEC_NAME for target in node.targets)]
    if not values:
        return None
    if len(values) != 1:
        raise ValueError("Expected exactly one Qubitz variant specification")
    # A launcher is declarative. Extra executable statements must not disappear
    # from static validation when its effective runtime source is expanded.
    expected_text = (
        '\"\"\"Variant-specific configuration and source for the shared Qubitz runtime.\"\"\"\n'
        "from __future__ import annotations\n"
        "from qubitz_runtime import initialize_variant as _initialize_variant\n"
        + SPEC_NAME + " = None\n"
        + "_initialize_variant(globals(), " + SPEC_NAME + ")\n"
    )
    original = values[0]
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.value is original:
            node.value = ast.Constant(value=None)
    expected_bootstrap = expected_text.replace(
        "from qubitz_runtime import", LAUNCHER_BOOTSTRAP + "from qubitz_runtime import", 1,
    )
    allowed = {ast.dump(ast.parse(text), include_attributes=False) for text in (expected_text, expected_bootstrap)}
    if ast.dump(tree, include_attributes=False) not in allowed:
        raise ValueError("Unexpected executable code or altered initialization in Qubitz launcher")
    value = ast.literal_eval(original)
    if not isinstance(value, dict):
        raise TypeError("Qubitz variant specification must be a dictionary")
    return value


def template_source(layer: str, schema_version: int = 2) -> str:
    if layer not in {"wrapper", "base"}:
        raise ValueError("Unknown Qubitz source layer")
    if schema_version != 2:
        raise ValueError("Only schema-2 Qubitz source is supported")
    return (RUNTIME_DIRECTORY / f"{layer}.py.in").read_text(encoding="utf-8")


def compose_layer(layer: str, sections: dict[str, str], *, template: str | None = None,
                  schema_version: int = 2, symbols: dict[str, str] | None = None) -> str:
    if schema_version != 2:
        raise ValueError("Only schema-2 Qubitz source is supported")
    template = template_source(layer, schema_version) if template is None else template
    matches = list(SECTION_PATTERN.finditer(template))
    keys = [match[2] for match in matches]
    if (any(match[1] != layer for match in matches) or len(keys) != len(set(keys))
            or not isinstance(sections, dict) or set(sections) != set(keys)
            or any(not isinstance(value, str) for value in sections.values())):
        raise ValueError(f"Invalid or incomplete {layer} variant sections")
    result = SECTION_PATTERN.sub(lambda match: sections[match[2]], template)
    values = symbols if symbols is not None else symbol_sources(layer, RUNTIME_DIRECTORY)
    return expand_symbols(result, layer, values)


def compose_variant(spec: dict[str, Any], filename: str, *, templates: dict[str, str] | None = None,
                    symbols: dict[str, dict[str, str]] | None = None) -> str:
    if (not isinstance(spec, dict) or set(spec) != {"schema_version", "filename", "wrapper", "base"}
            or type(spec["schema_version"]) is not int or spec["schema_version"] != 2
            or spec["filename"] != filename or Path(filename).name != filename):
        raise ValueError("Variant specification does not match its launcher")
    base = compose_layer("base", spec["base"], template=templates.get("base") if templates else None,
                         schema_version=spec["schema_version"], symbols=symbols.get("base") if symbols else None)
    wrapper = compose_layer("wrapper", spec["wrapper"], template=templates.get("wrapper") if templates else None,
                            schema_version=spec["schema_version"], symbols=symbols.get("wrapper") if symbols else None)
    if wrapper.count(BASE_MARKER) != 1:
        raise ValueError("Shared wrapper must contain exactly one embedded-base source slot")
    return wrapper.replace(BASE_MARKER, repr(base))


def read_variant_source(path: Path) -> str:
    """Return real effective source, or ordinary source for a legacy/non-variant file."""
    text = path.read_text(encoding="utf-8")
    spec = launcher_spec(text)
    return compose_variant(spec, path.name) if spec is not None else text


def runtime_dependency_paths(root: Path) -> list[Path]:
    directory = root / "qubitz_runtime"
    paths = [path for path in directory.rglob("*")
             if path.is_file() and "__pycache__" not in path.parts
             and (path.suffix in {".py", ".json"} or path.name.endswith(".py.in"))]
    launcher = root / "AI Agent Qubitz.py"
    if launcher.is_file():
        paths.append(launcher)
    return sorted(paths)


def embedded_source(text: str) -> tuple[str, tuple[int, int]]:
    """Find the literal base source using UTF-8-aware AST offsets."""
    values = [node.value for node in ast.parse(text).body
              if isinstance(node, ast.Assign) and any(
                  isinstance(target, ast.Name) and target.id == "_EMBEDDED_BASE_SOURCE"
                  for target in node.targets)
              and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)]
    if len(values) != 1:
        raise ValueError("Expected exactly one literal embedded base source")
    node = values[0]
    lines = text.splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + len(
        lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8"))
    end = sum(map(len, lines[:node.end_lineno - 1])) + len(
        lines[node.end_lineno - 1].encode("utf-8")[:node.end_col_offset].decode("utf-8"))
    return node.value, (start, end)
