"""Explicit, model-free variant selection for CPU contract fixtures."""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from runtime.paths import PROJECT_ROOT, variant_path, variant_paths
from runtime.variant_policy import require_user_variants_allowed


def selected_variant_names(defaults: Sequence[str], root: Path = PROJECT_ROOT) -> tuple[str, ...]:
    single = os.environ.get("QUBITZ_TEST_VARIANT")
    all_variants = os.environ.get("QUBITZ_TEST_ALL_VARIANTS", "0" if single is not None else "1")
    if all_variants not in {"0", "1"}:
        raise ValueError("QUBITZ_TEST_ALL_VARIANTS must be 0 or 1")
    if all_variants == "1" and single is not None:
        raise ValueError("Select all variants or one variant, not both")
    names = (tuple(path.name for path in variant_paths(root)) if all_variants == "1"
             else (single,) if single is not None else tuple(defaults))
    if not names or len(set(names)) != len(names):
        raise ValueError("CPU validation requires a nonempty, unique variant selection")
    require_user_variants_allowed(names)
    for name in names:
        path = variant_path(name, root)
        if not path.is_file():
            raise FileNotFoundError(path)
    return names
