"""Developer inventory stays maintainable but never joins public validation."""
from __future__ import annotations

import json
import re

import pytest

from evaluation.variant_test_selection import selected_variant_names
from runtime.paths import (
    PROJECT_ROOT,
    all_variant_paths,
    variant_path,
    variant_paths,
)
from runtime.variant_policy import DEVELOPER_ONLY_VARIANTS, user_variant_allowed


def test_public_and_maintenance_discovery_have_separate_exact_inventories():
    public = {path.name for path in variant_paths()}
    maintenance = {path.name for path in all_variant_paths()}
    assert len(public) == 40 and len(maintenance) == 43
    assert maintenance - public == DEVELOPER_ONLY_VARIANTS
    assert not public & DEVELOPER_ONLY_VARIANTS


@pytest.mark.parametrize("name", sorted(DEVELOPER_ONLY_VARIANTS))
def test_cpu_selection_rejects_developer_variants_before_opening_them(name, monkeypatch):
    monkeypatch.delenv("QUBITZ_TEST_ALL_VARIANTS", raising=False)
    monkeypatch.setenv("QUBITZ_TEST_VARIANT", name)
    with pytest.raises(ValueError, match="user policy"):
        selected_variant_names(())


def test_default_cpu_and_release_selection_cover_exactly_public_inventory(monkeypatch):
    import qubitz_release_gate as gate
    import qubitz_variant_builder as builder

    monkeypatch.delenv("QUBITZ_TEST_ALL_VARIANTS", raising=False)
    monkeypatch.delenv("QUBITZ_TEST_VARIANT", raising=False)
    expected = {path.name for path in variant_paths()}
    assert set(selected_variant_names(())) == expected
    assert {path.name for path in gate.variant_paths()} == expected
    assert {path.name for path in builder.variant_paths()} == expected
    assert set(gate.VARIANT_SAMPLING_FAMILIES) == expected


def test_profiles_retain_developer_settings_without_releasing_them():
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    assert len(profiles) == 43
    assert {row["filename"] for row in profiles if user_variant_allowed(row["filename"])} == {
        path.name for path in variant_paths()
    }
    assert {row["filename"] for row in profiles if not user_variant_allowed(row["filename"])} == DEVELOPER_ONLY_VARIANTS


def test_public_guides_and_catalogs_match_the_surviving_launchers():
    expected = {path.name for path in variant_paths()}
    documentation = PROJECT_ROOT / "documentation"
    assert {path.with_suffix(".py").name for path in documentation.glob("AI_Agent_Qubitz_*.md")} == expected
    for name in ("qubitz_variants_list.md", "num_ctx_values.md"):
        catalog = documentation.joinpath(name).read_text(encoding="utf-8")
        names = re.findall(r"`(AI_Agent_Qubitz_[^`/]+\.py)`", catalog)
        assert len(names) == len(expected) and set(names) == expected


def test_runtime_contract_gate_requires_all_public_profiles_and_no_developer_launchers():
    import qubitz_release_gate as gate

    paths = gate.variant_paths()
    gate.check_variant_runtime_contracts(paths)
    with pytest.raises(RuntimeError, match="profiles do not match the active variant set"):
        gate.check_variant_runtime_contracts(paths[:-1])


@pytest.mark.parametrize("name", sorted(DEVELOPER_ONLY_VARIANTS))
def test_explicit_developer_paths_remain_resolvable_without_import(name):
    assert variant_path(name).name == name
    assert variant_path(name).parent == PROJECT_ROOT / "variants"


def test_public_gpt_oss_source_does_not_depend_on_developer_model_replacements():
    from runtime.source import launcher_spec

    path = variant_path("AI_Agent_Qubitz_GPT-OSS-120B_F16.py")
    physical = path.read_text()
    assert "XORTRON" not in physical and "Xorton" not in physical
    spec = launcher_spec(physical)
    assert spec["wrapper"]["068"] == ""
    assert '"unsloth"' in spec["base"]["004"] and '"gpt-oss-120b-GGUF"' in spec["base"]["005"]


def test_public_policy_is_embedded_without_a_runtime_package_dependency():
    from runtime import read_variant_source

    module = (PROJECT_ROOT / "runtime/variant_policy.py").read_text()
    for path in variant_paths():
        assert read_variant_source(path).count(module) == 1


def test_default_wrapper_listing_never_imports_a_developer_variant(monkeypatch, capsys):
    from runtime import launcher

    monkeypatch.setattr(launcher.runpy, "run_path", lambda *_args, **_kwargs: pytest.fail("Listing imported a model"))
    assert launcher.main(["--list-variants"]) == 0
    assert set(capsys.readouterr().out.splitlines()) == {path.name for path in variant_paths()}
