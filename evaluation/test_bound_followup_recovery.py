"""Exact prior-file binding, without widening scope from a conversational pronoun."""
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("prompt,allowed", [
    ("Add divide(a, b) to it, raising ValueError for a zero divisor. Preserve add.", True),
    ("Please extend it with a divide function.", True),
    ("Do not add divide to it.", False),
    ("Explain how to add divide to it.", False),
    ("Add divide to other.py instead of it.", False),
    ("New task: add divide to it.", False),
])
def test_pronoun_revision_requires_one_existing_local_text_artifact(variant, tmp_path, prompt, allowed):
    path = tmp_path / "calculator.py"
    path.write_text("def add(a,b): return a+b\n")
    assert variant._implicit_artifact_revision_request(prompt, [str(path)]) is allowed
    assert not variant._implicit_artifact_revision_request(prompt, [str(path), str(tmp_path / "other.py")])


def test_local_python_syntax_rejection_is_proven_before_write(variant, tmp_path):
    import asyncio

    from evaluation import test_tool_argument_boundaries as boundary

    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode(variant.ACCESS_MODE_FULL)
    broker.begin_turn("Create calculator.py with add(a,b) returning the sum.", None)
    host = boundary.host_for(variant, broker)
    result = asyncio.run(host.call_tool("write_file", {"path": "calculator.py", "content": "def broken(\n"}))
    assert result.isError and result.structuredContent["status"] == "invalid_file_content"
    assert result.structuredContent["dispatch_attempted"] is False
    assert result.structuredContent["qubitz_recovery"]["outcome"] == "rejected_before_dispatch"
    assert not host.dispatched and not Path(tmp_path / "calculator.py").exists()


def test_bound_pronoun_edit_uses_existing_snapshot_checked_one_shot_proposal(variant, runner, tmp_path, monkeypatch):
    import asyncio
    import hashlib
    from types import SimpleNamespace

    target = tmp_path / "shared.txt"
    target.write_text("workspace=A\nstate=original\n")
    prompt = "Extend it with a state=ready line, preserving existing content."
    broker = receipts.begin(runner, prompt)
    broker.bind_follow_up_repair("Create shared.txt containing initial state.", prompt, [str(target)], revision=True)
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _p: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _p: "")
    proposals = []
    def propose(**kwargs):
        proposals.append(kwargs)
        return {"patches": [{"path": "shared.txt", "replacements": [{
            "old_text": "state=original", "new_text": "state=ready",
        }]}]}
    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    def handle(name, args):
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if name == "read_file_snapshot":
            return {"status": "text_snapshot", "content": target.read_text(), "sha256": digest}
        assert name == "apply_text_patch" and args["expected_sha256"] == digest
        allowed, error = broker.authorize(name, args)
        assert allowed, error
        target.write_text(target.read_text().replace("original", "ready"))
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        broker.transaction_changed_hashes[str(target)] = digest
        return {"status": "completed", "path": str(target), "new_sha256": digest}
    calls = receipts.install_host(variant, runner, monkeypatch, handler=handle)
    answer = asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
    assert answer and "ready" in target.read_text()
    assert len(proposals) == 1 and [name for name, _a in calls] == ["read_file_snapshot", "apply_text_patch"]
    assert asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None)) is None
