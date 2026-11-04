"""Exclusions must reject before any product import, model, process, or fixture startup."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

import qubitz_disposable_eval as disposable
import tmp_run_smoke_suite as suite
import tmp_smoke_variant as smoke
from evaluation import smoke_complex_tasks as complex_smoke
from evaluation import smoke_create_calculator as calculator
from evaluation import smoke_mcp_integrations as integrations
from qubitz_live_eval_policy import (
    EXCLUDED_LIVE_VARIANTS,
    live_variant_allowed,
    require_live_variants_allowed,
)
from runtime.paths import variant_path, variant_paths

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
def test_excluded_names_and_paths_are_rejected(name):
    for value in (name, name.upper(), str(variant_path(name, ROOT)), "C:\\project\\" + name):
        assert not live_variant_allowed(value)
        with pytest.raises(ValueError, match="user policy"):
            require_live_variants_allowed([value])


def test_disposable_and_smoke_discovery_cover_exactly_the_allowed_wrappers(monkeypatch):
    actual = {path.name for path in variant_paths(ROOT)}
    monkeypatch.setattr(suite, "REPO_ROOT", ROOT)
    expected = actual - EXCLUDED_LIVE_VARIANTS
    assert set(disposable._discover_variants()) == expected
    assert {name for name, _ in suite._discover_variants()} == expected
    assert len(expected) == 40


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
def test_explicit_smoke_environment_selection_cannot_override_policy(name, monkeypatch):
    monkeypatch.setenv("QUBITZ_SMOKE_VARIANTS", name)
    with pytest.raises(ValueError, match="user policy"):
        suite._configured_variants()


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
def test_product_loaders_reject_before_import(name, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Excluded live test attempted a product import")

    monkeypatch.setattr(importlib.util, "spec_from_file_location", forbidden)
    with pytest.raises(ValueError, match="user policy"):
        smoke._load_module(name)
    with pytest.raises(ValueError, match="user policy"):
        disposable._load_variant(variant_path(name, ROOT))


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
def test_smoke_workers_reject_before_fixture_or_process_setup(name):
    # Deliberately omit output/workspace fields: policy must run before accessing them.
    with pytest.raises(ValueError, match="user policy"):
        calculator.child(SimpleNamespace(child=name))
    with pytest.raises(ValueError, match="user policy"):
        complex_smoke.case_child(SimpleNamespace(variant=name))
    with pytest.raises(ValueError, match="user policy"):
        asyncio.run(integrations.worker(SimpleNamespace(variant=name), None))


@pytest.mark.parametrize("name", sorted(EXCLUDED_LIVE_VARIANTS))
def test_complex_coordinator_rejects_before_creating_output(name):
    with pytest.raises(ValueError, match="user policy"):
        complex_smoke._coordinator(SimpleNamespace(variants=[name]))
