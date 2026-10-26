"""Canonical project-local variant discovery, separate from user file routing."""
from __future__ import annotations

from pathlib import Path

from .variant_policy import user_variant_allowed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VARIANT_GLOB = "AI_Agent_Qubitz_*.py"
DEFAULT_VARIANT = "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"


def variant_directory(root: Path | None = None) -> Path:
    root = (PROJECT_ROOT if root is None else Path(root)).resolve()
    directory = root / "variants"
    try:
        aliased = directory.resolve() != directory
    except RuntimeError as exc:
        raise ValueError("The variants directory contains a cyclic alias") from exc
    if aliased:
        raise ValueError("The variants directory must not alias another location")
    return directory


def variant_path(filename: str, root: Path | None = None) -> Path:
    if (not isinstance(filename, str) or "/" in filename or "\\" in filename
            or Path(filename).name != filename or not filename.startswith("AI_Agent_Qubitz_")
            or not filename.endswith(".py")):
        raise ValueError("Select an exact Qubitz variant basename, not a file path")
    path = variant_directory(root) / filename
    try:
        aliased = path.resolve() != path
    except RuntimeError as exc:
        raise ValueError(f"Variant aliases are not accepted: {filename}") from exc
    if aliased:
        raise ValueError(f"Variant aliases are not accepted: {filename}")
    return path


def variant_paths(root: Path | None = None, *, include_developer: bool = False) -> list[Path]:
    directory = variant_directory(root)
    result = []
    for path in sorted(directory.glob(VARIANT_GLOB), key=lambda item: item.name.casefold()):
        if not include_developer and not user_variant_allowed(path.name):
            continue
        candidate = variant_path(path.name, root)
        if candidate.is_file():
            result.append(candidate)
    return result


def all_variant_paths(root: Path | None = None) -> list[Path]:
    """Maintenance inventory, not a test or release selection."""
    return variant_paths(root, include_developer=True)
