"""Compact answer routes retain tier-independent evidence-scope safeguards."""
from __future__ import annotations

import pytest
import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("mode", ["legacy", "compact"])
def test_compact_contract_preserves_evidence_scope(variant, runner, monkeypatch, mode):
    monkeypatch.setenv("QUBITZ_SIMPLE_PROMPT_MODE", mode)
    runner._simple_direct_mode = True
    system = runner._system_prompt("", "", "")
    assert "Error messages and tool annotations are hints, not proof" in system
    assert "no writes occurred or that retry is safe" in system
    assert "Do not invent previous or current source definitions" in system
    assert "compatibility beyond the verified requested cases" in system
    assert "distinguish signature compatibility from behavioral compatibility" in system
    assert "Do not infer preserved numeric types, precision, overflow or exceptions" in system
    assert "does not require workspace context" not in system
