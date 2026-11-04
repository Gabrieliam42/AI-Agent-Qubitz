"""Fail-closed experimental route artifacts follow shared runtime revisions."""
from __future__ import annotations

import hashlib

import pytest

from decision_model import qubitz_route_decider as decider


@pytest.mark.parametrize("relative", ["runtime/wrapper.py.in", "runtime/loader.py",
                                      "AI Agent Qubitz.py", "HARNESS.enc"])
def test_fingerprint_detects_dependency_changes_with_unchanged_launcher(tmp_path, monkeypatch, relative):
    variant = tmp_path / "variants/AI_Agent_Qubitz_fixture.py"
    variant.parent.mkdir()
    variant.write_text("model = 'fixture'\n")
    dependency = tmp_path / relative
    dependency.parent.mkdir(exist_ok=True)
    dependency.write_text("original\n")
    monkeypatch.setattr(decider, "ROOT", tmp_path)
    monkeypatch.setattr(decider, "VARIANT", variant)
    before = decider.variant_fingerprint()
    assert before != hashlib.sha256(variant.read_bytes()).hexdigest()
    assert decider.variant_fingerprint() == before
    dependency.write_text("changed\n")
    assert decider.variant_fingerprint() != before
    assert variant.read_text() == "model = 'fixture'\n"
