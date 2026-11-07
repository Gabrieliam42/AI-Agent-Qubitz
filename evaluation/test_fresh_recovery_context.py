"""Current local context and read-only dependencies survive bounded recovery."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("mode", ["current", "changed", "unsnapshotted", "source_proposal"])
def test_recovery_retains_bound_reference_without_extending_write_authority(
    variant, runner, monkeypatch, mode,
):
    target = runner.workspace / "guide.md"
    source = runner.workspace / "reference.md"
    target.write_text("Original destination.\n")
    source.write_text("Protected current reference.\n")
    prompt = "Update guide.md using reference.md. Do not edit reference.md."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    boundaries.record_snapshot(broker, target)
    if mode != "unsnapshotted":
        boundaries.record_snapshot(broker, source)
    if mode == "changed":
        source.write_text("Changed outside the recorded snapshot.\n")
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _: "")
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs["messages"][1]["content"])
        if mode == "source_proposal":
            return {"patches": [{"path": source.name, "replacements": [
                {"start_line": 1, "end_line": 1, "new_text": "Unauthorized.\n"},
            ]}]}
        raise RuntimeError("fixture stops before proposing any mutation")

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch,
                                  handler=lambda *_: pytest.fail("Unexpected tool dispatch"))
    asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
    if mode in {"current", "source_proposal"}:
        assert proposals
        assert "Protected current reference." in proposals[0]
        assert "Read-only reference" in proposals[0]
    else:
        assert not proposals
    assert broker.transaction_explicit_file_targets == {str(target)}
    assert target.read_text() == "Original destination.\n"
    assert source.read_text() != "Unauthorized.\n"
    assert not calls


def test_local_refresh_replaces_old_files_without_fetching_external_sources(variant, runner, monkeypatch):
    target = runner.workspace / "record.txt"
    target.write_text("Original mutable value.\n")
    prompt = "Edit record.txt to update its value."
    receipts.begin(runner, prompt)
    calls = receipts.install_host(variant, runner, monkeypatch)
    initial = asyncio.run(runner._prefetch_named_source_context(prompt, None))
    assert "Original mutable value." in initial
    target.write_text("Updated mutable value.\n")
    monkeypatch.setattr(variant, "_fetch_named_source_for_grounding",
                        lambda *_: pytest.fail("A file refresh fetched a remote source"))
    refreshed = asyncio.run(runner._prefetch_named_source_context(prompt, None, files_only=True))
    assert "Updated mutable value." in refreshed
    assert "Original mutable value." not in refreshed
    assert all(name == "read_file_snapshot" for name, _ in calls)


def test_progressive_pass_refreshes_owned_file_context_after_a_commit(variant, runner, monkeypatch):
    import hashlib

    target = runner.workspace / "record.txt"
    target.write_text("Original mutable value.\n")
    (runner.workspace / "verify.py").write_text("raise SystemExit(0)\n")
    prompt = "Edit record.txt to update its value, then run verify.py. Do not edit verify.py."
    passes = []
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]

    async def model_pass(self, pass_prompt, callback=None):
        passes.append(pass_prompt)
        broker = self._tool_permission_broker
        if len(passes) == 1:
            target.write_text("Updated mutable value.\n")
            broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
                "status": "completed", "path": str(target),
                "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }))
            return "Stopped after reaching the maximum tool steps for this request."
        broker.no_progress_recovery_exhausted = True
        return "Remaining execution is unverified."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    receipts.install_host(variant, runner, monkeypatch)
    answer = asyncio.run(runner._run_async(prompt))
    assert len(passes) == 2
    assert "Original mutable value." in passes[0]
    assert "Updated mutable value." in passes[1]
    assert "Original mutable value." not in passes[1]
    assert target.read_text() == "Updated mutable value.\n"
    assert "partial" in answer
