"""Selection failures never silently expand a CPU validation run."""
from __future__ import annotations

import pytest

from evaluation.variant_test_selection import selected_variant_names


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    monkeypatch.delenv("QUBITZ_TEST_ALL_VARIANTS", raising=False)
    monkeypatch.delenv("QUBITZ_TEST_VARIANT", raising=False)
    directory = tmp_path / "variants"
    directory.mkdir()
    for name in ("AI_Agent_Qubitz_a.py", "AI_Agent_Qubitz_b.py"):
        (directory / name).write_text("", encoding="utf-8")
    return tmp_path


def test_default_and_explicit_all_cover_public_inventory(inventory, monkeypatch):
    defaults = ("AI_Agent_Qubitz_b.py",)
    assert selected_variant_names(defaults, inventory) == (
        "AI_Agent_Qubitz_a.py", "AI_Agent_Qubitz_b.py",
    )
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "0")
    assert selected_variant_names(defaults, inventory) == defaults
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "1")
    assert selected_variant_names(defaults, inventory) == (
        "AI_Agent_Qubitz_a.py", "AI_Agent_Qubitz_b.py",
    )


def test_conflicting_scope_is_rejected(inventory, monkeypatch):
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "1")
    monkeypatch.setenv("QUBITZ_TEST_VARIANT", "AI_Agent_Qubitz_a.py")
    with pytest.raises(ValueError, match="not both"):
        selected_variant_names((), inventory)


@pytest.mark.parametrize("names", [(), ("AI_Agent_Qubitz_a.py",) * 2])
def test_empty_or_duplicate_selection_is_rejected(inventory, names, monkeypatch):
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "0")
    with pytest.raises(ValueError, match="nonempty, unique"):
        selected_variant_names(names, inventory)


def test_invalid_environment_or_missing_target_never_falls_back(inventory, monkeypatch):
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "yes")
    with pytest.raises(ValueError, match="0 or 1"):
        selected_variant_names((), inventory)
    monkeypatch.setenv("QUBITZ_TEST_ALL_VARIANTS", "0")
    monkeypatch.setenv("QUBITZ_TEST_VARIANT", "AI_Agent_Qubitz_missing.py")
    with pytest.raises(FileNotFoundError):
        selected_variant_names(("AI_Agent_Qubitz_a.py",), inventory)
