"""Picker tiers are catalog labels, not changes to model launch configuration."""
from __future__ import annotations

import ast
import json
from collections import Counter

import pytest

from runtime import read_variant_source
from runtime.paths import DEFAULT_VARIANT, variant_path, variant_paths

LARGE_VARIANT = "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"
LARGE_TIER = "48 GB VRAM+"


@pytest.mark.parametrize("name", [DEFAULT_VARIANT, LARGE_VARIANT, "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py",
                                 "AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py"])
def test_tk_picker_has_three_tiers_with_large_models_in_the_large_tier(name):
    tree = ast.parse(read_variant_source(variant_path(name)))
    selectors = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                 and node.name == "_install_variant_selector"]
    assert len(selectors) == 1
    assignments = {}
    for node in selectors[0].body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            key = node.targets[0].id
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            key = node.target.id
        else:
            continue
        if key in {"variant_tier_overrides", "grouped"}:
            assignments[key] = ast.literal_eval(node.value)
    overrides = assignments["variant_tier_overrides"]
    assert overrides[LARGE_VARIANT] == LARGE_TIER
    assert set(assignments["grouped"]) == {"12 GB VRAM", "24 GB VRAM", LARGE_TIER}
    counts = Counter(overrides.get(path.name, "12 GB VRAM" if path.stem.casefold().endswith("_12g")
                                   else "24 GB VRAM") for path in variant_paths())
    assert counts == {"12 GB VRAM": 4, "24 GB VRAM": 27, LARGE_TIER: 10}


def test_common_template_and_model_profiles_remain_in_sync():
    import qubitz_variant_builder as builder

    paths = [variant_path(DEFAULT_VARIANT), variant_path(LARGE_VARIANT)]
    assert builder.check_template(paths, builder.load_template()) == []
    profiles = {row["filename"]: row for row in json.loads(builder.PROFILES_PATH.read_text())["variants"]}
    for path in paths:
        assert builder.profile_from_text(path, read_variant_source(path)) == profiles[path.name]
