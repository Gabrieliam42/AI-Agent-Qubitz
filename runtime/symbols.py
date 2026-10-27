"""Expand exact, named universal source blocks into a private variant namespace."""
from __future__ import annotations

import re
from pathlib import Path

REFERENCE_PATTERN = re.compile(r"(?m)^# <QUBITZ_SHARED_REFERENCE:(wrapper|base):([a-f0-9]{16})>\n")
BLOCK_PATTERN = re.compile(
    r"(?ms)^# <QUBITZ_SHARED_SYMBOL:(wrapper|base):([a-f0-9]{16})>[^\n]*\n"
    r"(.*?)^# </QUBITZ_SHARED_SYMBOL>\n",
)
MODULE_PATTERN = re.compile(
    r"(?m)^# <QUBITZ_SHARED_MODULE:(wrapper|base):([a-f0-9]{16}):([a-z][a-z0-9_]*\.py)>\n"
)


def symbol_sources(layer: str, directory: Path) -> dict[str, str]:
    text = (directory / f"{layer}.shared.py.in").read_text(encoding="utf-8")
    blocks = list(BLOCK_PATTERN.finditer(text))
    modules = list(MODULE_PATTERN.finditer(text))
    values = {block[2]: block[3] for block in blocks}
    if (len({match[2] for match in [*blocks, *modules]}) != len(blocks) + len(modules)
            or any(match[1] != layer for match in [*blocks, *modules])
            or MODULE_PATTERN.sub("", BLOCK_PATTERN.sub("", text)).strip()):
        raise ValueError(f"Malformed universal source registry: {layer}")
    for match in modules:
        path = directory / match[3]
        if path.is_symlink() or path.resolve() != directory.resolve() / match[3]:
            raise ValueError("Universal module source must be a real runtime-package file")
        values[match[2]] = path.read_text(encoding="utf-8")
        if not values[match[2]].endswith("\n"):
            raise ValueError("Universal module source must end with a newline")
    return values


def expand_symbols(text: str, layer: str, values: dict[str, str]) -> str:
    def expand(match: re.Match[str]) -> str:
        if match[1] != layer or match[2] not in values:
            raise ValueError("Unknown or cross-layer universal source reference")
        return values[match[2]]

    result = REFERENCE_PATTERN.sub(expand, text)
    if REFERENCE_PATTERN.search(result):
        raise ValueError("Recursive universal source references are forbidden")
    return result
