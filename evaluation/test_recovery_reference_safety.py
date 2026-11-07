"""Read-only recovery references must remain available through admission."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("mode", ["missing", "oversized", "late_change"])
def test_unavailable_or_late_changed_reference_never_admits_a_patch(variant, runner, monkeypatch, mode):
    target = runner.workspace / "guide.md"
    source = runner.workspace / "reference.md"
    target.write_text("Original destination.\n")
    source.write_text("Protected reference.\n")
    prompt = "Update guide.md using reference.md. Do not edit reference.md."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    boundaries.record_snapshot(broker, target)
    boundaries.record_snapshot(broker, source)
    if mode == "missing":
        source.unlink()
    elif mode == "oversized":
        source.write_text("x" * 96005)
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _: "")
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        assert mode == "late_change"
        source.write_text("Later external reference.\n")
        return {"patches": [{"path": target.name, "replacements": [
            {"start_line": 1, "end_line": 1, "new_text": "Changed destination.\n"},
        ]}]}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch,
                                  handler=lambda *_: pytest.fail("An unsafe reference admitted a patch"))
    asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
    assert len(proposals) == (1 if mode == "late_change" else 0)
    assert not calls and target.read_text() == "Original destination.\n"


def test_local_refresh_with_named_url_does_not_fetch(variant, runner, monkeypatch):
    target = runner.workspace / "guide.md"
    target.write_text("Current local value.\n")
    prompt = "Update guide.md using https://example.org/reference."
    receipts.begin(runner, prompt)
    receipts.install_host(variant, runner, monkeypatch)
    monkeypatch.setattr(variant, "_fetch_named_source_for_grounding",
                        lambda *_: pytest.fail("A continuation refetched a source"))
    context = asyncio.run(runner._prefetch_named_source_context(prompt, None, files_only=True))
    assert "Current local value." in context and "Source:" not in context
