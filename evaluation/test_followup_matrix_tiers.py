"""CPU-only tier selection and dispatch; never import product variants."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from runtime.paths import variant_paths

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix

from qubitz_live_eval_policy import EXCLUDED_LIVE_VARIANTS


@pytest.fixture
def variant_root(tmp_path, monkeypatch):
    directory = tmp_path / "variants"
    directory.mkdir()
    for names in matrix.VARIANT_TIERS.values():
        for name in names:
            (directory / name).touch()
    (directory / "AI_Agent_Qubitz_Unselected-30B.py").touch()
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    return tmp_path


def test_default_tiers_cover_public_catalog_and_exclude_unregistered_files(variant_root):
    assert matrix.variants(None) == list(matrix.ALL_VARIANTS)
    assert matrix.variants(None)[0] == "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"
    assert len(matrix.variants(None)) == 40
    assert matrix.variants(None)[1] == "AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py"
    assert matrix.variants(None, "large") == list(matrix.LARGE_VARIANTS)
    assert len(matrix.LARGE_VARIANTS) == 37
    assert matrix.variants(None, "8b-9b") == list(matrix.SMALL_VARIANTS)
    assert len(matrix.SMALL_VARIANTS) == 3
    assert not set(matrix.LARGE_VARIANTS) & set(matrix.SMALL_VARIANTS)


def test_remaining_large_models_descend_by_declared_total_size(variant_root):
    sizes = [matrix.declared_total_parameters(name) for name in matrix.variants(None)[2:]]
    assert sizes == sorted(sizes, reverse=True)


def test_matrix_public_catalog_covers_every_user_variant():
    root = Path(__file__).resolve().parents[1]
    actual = {path.name for path in variant_paths(root)}
    selected = set(matrix.LARGE_VARIANTS) | set(matrix.SMALL_VARIANTS)
    assert selected == actual - EXCLUDED_LIVE_VARIANTS == set(matrix.ALL_VARIANTS)
    assert len(selected) == 40
    assert not selected & EXCLUDED_LIVE_VARIANTS
    assert len(matrix.LARGE_VARIANTS) == 37
    assert "AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py" in selected


def test_declared_parameter_count_never_guesses():
    assert matrix.declared_total_parameters("AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py") == 284
    with pytest.raises(ValueError, match="No declared"):
        matrix.declared_total_parameters("AI_Agent_Qubitz_Unknown.py")


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
@pytest.mark.parametrize("tier", matrix.VARIANT_TIERS)
def test_user_excluded_variants_cannot_be_explicitly_selected(name, tier):
    with pytest.raises(ValueError, match="user policy"):
        matrix.variants([name], tier)


@pytest.mark.parametrize("tier,requested", [
    ("large", [matrix.SMALL_VARIANTS[0]]),
    ("8b-9b", [matrix.LARGE_VARIANTS[0]]),
    ("large", [matrix.LARGE_VARIANTS[0], matrix.SMALL_VARIANTS[0]]),
    ("large", ["AI_Agent_Qubitz_Unselected-30B.py"]),
])
def test_cross_tier_and_unselected_models_are_rejected(variant_root, tier, requested):
    with pytest.raises(ValueError, match="outside"):
        matrix.variants(requested, tier)


@pytest.mark.parametrize("tier", matrix.VARIANT_TIERS)
def test_subset_order_is_preserved_and_missing_models_fail(variant_root, tier):
    requested = list(reversed(matrix.VARIANT_TIERS[tier][:2]))
    assert matrix.variants(requested, tier) == requested
    with pytest.raises(ValueError, match="unique"):
        matrix.variants([requested[0], requested[0]], tier)
    (variant_root / "variants" / requested[0]).unlink()
    with pytest.raises(ValueError, match="Missing"):
        matrix.variants(None, tier)


@pytest.mark.parametrize("tier", matrix.VARIANT_TIERS)
def test_dry_plans_keep_full_scenarios_without_starting_execution(variant_root, monkeypatch, capsys, tier):
    monkeypatch.setattr(sys, "argv", ["matrix", "--suite", "expanded-v2", "--gui"])

    def forbidden(*args, **kwargs):
        pytest.fail("Dry planning attempted execution")

    monkeypatch.setattr(matrix, "coordinate", forbidden)
    monkeypatch.setattr(matrix, "child", forbidden)
    assert matrix.main(default_tier=tier) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["variant_tier"] == tier
    assert plan["variants"] == list(matrix.VARIANT_TIERS[tier])
    assert len(plan["scenarios"]) == 26
    assert plan["trajectory_count"] == len(matrix.VARIANT_TIERS[tier]) * 26
    assert plan["execution_started"] is False
    assert not (variant_root / "evaluation/results").exists()


def test_small_tier_reaches_worker_without_large_tier_validation(variant_root, monkeypatch):
    monkeypatch.setattr(sys, "argv", [
        "matrix", "--child", "--tier", "8b-9b", "--variant", matrix.SMALL_VARIANTS[0],
    ])
    received = []
    monkeypatch.setattr(matrix, "child", lambda args: received.append(args.tier) or 0)
    assert matrix.main() == 0
    assert received == ["8b-9b"]
