"""Exercise the no-suite verification handoff through the real runner, without inference."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

from evaluation import test_followup_state_receipts as receipts
from evaluation.test_snapshot_behavior_recovery import (
    AFTER,
    BEFORE,
    CASES,
    PROMPT,
    wire_checks,
)

variant = receipts.variant
runner = receipts.runner


def test_no_suite_numeric_receipt_finishes_real_orchestrator(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "calculator.py"
    target.write_text(BEFORE)
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]
    passes, proposals, events = [], [], []

    def propose(**kwargs):
        proposals.append(kwargs)
        assert kwargs["schema_name"] == "qubitz_numeric_behavior_checks"
        return {"checks": wire_checks([{"path": target.name, **case} for case in CASES])}

    async def model_pass(self, prompt, callback=None):
        passes.append(prompt)
        broker = self._tool_permission_broker
        broker.required_postconditions.add("tests")
        broker.transaction_originals[str(target)] = {"data": BEFORE.encode()}
        target.write_text(AFTER)
        broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
            "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        self._raise_if_cancelled()
        return "The requested edit was saved."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch)
    answer = asyncio.run(runner._run_async(PROMPT, callback=lambda *event: events.append(event)))
    assert len(passes) == len(proposals) == 1, {"answer": answer, "events": events[-10:]}
    assert "[Completion status: verified]" in answer and "[Completion status: partial]" not in answer
    assert "bounded numeric" in answer and "full test suite" in answer
    assert not any(name == "run_existing_entrypoint" for name, _ in calls)
    assert target.read_text() == AFTER
