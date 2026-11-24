"""Locate usable Tcl/Tk data without importing Tk or assuming a minor version."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

_OVERRIDES = ("TCL_LIBRARY", "TK_LIBRARY")
_ONEFILE_DIRECTORY = re.compile(r"_MEI[a-z0-9_]{6,}", re.IGNORECASE)


def _bundled_override(value: str, bundle_root: Path | None) -> bool:
    path = Path(value)
    if not path.is_absolute():
        return False
    if any(_ONEFILE_DIRECTORY.fullmatch(part) for part in path.parts):
        return True
    if bundle_root is None or not bundle_root.is_absolute():
        return False
    try:
        return path.resolve().is_relative_to(bundle_root.resolve())
    except (OSError, RuntimeError, ValueError):
        return False


def external_child_environment(environment: Mapping[str, str] | None = None, *,
                               bundle_root: Path | None = None) -> dict[str, str]:
    """Remove parent-bundle Tcl/Tk overrides for an external runtime only."""
    clean = dict(os.environ if environment is None else environment)
    for key, value in tuple(clean.items()):
        name = key.upper() if os.name == "nt" else key
        if name in _OVERRIDES and _bundled_override(value, bundle_root):
            del clean[key]
    return clean


def _data_dir(value: str | None, marker: str) -> Path | None:
    if not value:
        return None
    path = Path(value)
    try:
        data = path / marker
        return path if path.is_absolute() and data.is_file() and data.stat().st_size else None
    except (OSError, ValueError):
        return None


def _prefix_pair(prefix: Path) -> tuple[Path, Path] | None:
    pairs = []
    for tcl_data in sorted((prefix / "tcl").glob("tcl*")):
        version = re.fullmatch(r"tcl(\d+\.\d+)", tcl_data.name)
        if version:
            tk_data = tcl_data.with_name(f"tk{version[1]}")
            if _data_dir(str(tcl_data), "init.tcl") and _data_dir(str(tk_data), "tk.tcl"):
                pairs.append((tcl_data, tk_data))
    # The runtime knows its compiled version; ordering directory names does not.
    return pairs[0] if len(pairs) == 1 else None


def configure_tk_environment() -> None:
    keys = _OVERRIDES
    frozen = getattr(sys, "frozen", False)
    if not frozen:
        clean = external_child_environment()
        for key in keys:
            if key not in clean:
                os.environ.pop(key, None)
    markers = ("init.tcl", "tk.tcl")
    explicit = tuple(_data_dir(os.environ.get(key), marker) for key, marker in zip(keys, markers))
    if all(explicit):
        return

    pair = None
    if frozen:
        bundled = getattr(sys, "_MEIPASS", None)
        if isinstance(bundled, (str, os.PathLike)) and Path(bundled).is_absolute():
            prefix = Path(bundled)
            tcl_data = _data_dir(str(prefix / "_tcl_data"), markers[0])
            tk_data = _data_dir(str(prefix / "_tk_data"), markers[1])
            pair = (tcl_data, tk_data) if tcl_data and tk_data else _prefix_pair(prefix)
    else:
        for value in dict.fromkeys((sys.prefix, getattr(sys, "base_prefix", None))):
            if value and Path(value).is_absolute():
                pair = _prefix_pair(Path(value))
                if pair:
                    break

    for index, key in enumerate(keys):
        if pair and (not any(explicit) or explicit[index] is None and pair[1 - index] == explicit[1 - index]):
            os.environ[key] = str(pair[index])
        elif explicit[index] is None:
            # An invalid override must not prevent the interpreter's native discovery.
            os.environ.pop(key, None)
