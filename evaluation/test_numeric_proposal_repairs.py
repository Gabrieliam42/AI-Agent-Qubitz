"""Generic verification proposal boundaries; no inference or Python module execution."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation.test_snapshot_behavior_recovery import wire_checks
from runtime import behavioral_checks as numeric

variant = receipts.variant
runner = receipts.runner
BEFORE = "def keeper(value):\n    return value\n"
ADDED = "\ndef rescale(value, multiplier):\n    if multiplier < 0:\n        raise ValueError('negative multiplier')\n    return value * multiplier\n"
PROMPT = "Add rescale(value, multiplier) to signals.py: return their product and reject negative multipliers with ValueError. Preserve keeper."
CASES = [
    {"path": "signals.py", "function": "rescale", "args": [0, 2], "expected": 0, "raises": ""},
    {"path": "signals.py", "function": "rescale", "args": [3, -1], "expected": None, "raises": "ValueError"},
]


def test_scope_distinguishes_required_changes_from_named_preservation():
    contract = numeric.verification_contract(BEFORE, BEFORE + ADDED, PROMPT)
    assert contract["rescale"]["required"] is True
    assert contract["keeper"]["required"] is False
    assert "keeper" not in numeric.verification_contract(BEFORE, BEFORE + ADDED, "Add rescale.")
    assert "keeper" not in numeric.verification_contract(BEFORE, BEFORE + ADDED, "Do not preserve keeper.")
    cases = [{k: v for k, v in case.items() if k != "path"} for case in CASES]
    assert numeric.check_numeric_cases(BEFORE + ADDED, contract, cases + [
        {"function": "keeper", "args": [7], "expected": 7, "raises": ""},
    ]) == 3


def test_entrypoint_example_is_not_executed(capsys):
    source = BEFORE + '\nif __name__ == "__main__":\n    print(keeper(999))\n' + ADDED
    assert set(numeric.functions(source)) == {"keeper", "rescale"}
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("body", [
    "keeper = lambda x: x", "import os", "open('unowned', 'w')", "print(unknown())",
    "print(keeper(external))", "print(keeper(1), file=external)", "raise ValueError('no')",
    "def keeper(x): return 9", "print(keeper(1)); rescale = keeper",
])
def test_entrypoint_global_or_effectful_layout_is_rejected(body):
    with pytest.raises((ValueError, TypeError)):
        numeric.functions(BEFORE + ADDED + '\nif __name__ == "__main__":\n    ' + body + '\n')


def test_typed_expectations_preserve_legitimate_none_and_bound_the_signature():
    contract = numeric.verification_contract("", "def optional(value):\n    return None\n", "Add optional.")
    schema = numeric.proposal_schema({"signals.py": contract})
    item = schema["properties"]["checks"]["items"]["anyOf"][0]
    assert item["properties"]["function"]["enum"] == ["optional"]
    assert item["properties"]["args"]["minItems"] == item["properties"]["args"]["maxItems"] == 1
    cases = wire_checks([{"path": "signals.py", "function": "optional", "args": [value],
                          "expected": None, "raises": ""} for value in [1, 2]])
    assert numeric.decode_numeric_cases({"checks": cases})[0]["expected"] is None
    cases[0]["expectation"] = {"kind": "value", "basis": "Missing expected value"}
    with pytest.raises(ValueError):
        numeric.decode_numeric_cases({"checks": cases})


@pytest.mark.parametrize("mode", [
    "preserved", "corrected", "budget", "still_wrong", "main_example", "unsupported",
    "cancel_correction", "drift_correction", "permission_correction", "uncertain_correction",
])
def test_real_runner_verification_is_bounded_read_only_and_state_bound(
    variant, runner, tmp_path, monkeypatch, mode,
):
    target = tmp_path / "signals.py"
    before = BEFORE + ('\nif __name__ == "__main__":\n    print(keeper(3))\n' if mode == "main_example" else "")
    if mode == "unsupported":
        before += "external = 9\n"
    after = before + ADDED
    broker = receipts.begin(runner, PROMPT)
    broker.required_postconditions.add("tests")
    broker.transaction_originals[str(target)] = {"data": before.encode()}
    target.write_text(after)
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    if mode == "budget":
        assert broker.action_progress.reserve_recovery("edit")
    requests = []

    def propose(**kwargs):
        requests.append(kwargs)
        cases = [dict(case) for case in CASES]
        if mode == "preserved":
            cases.append({"path": target.name, "function": "keeper", "args": [8], "expected": 8, "raises": ""})
        if mode not in {"preserved", "main_example"} and (len(requests) == 1 or mode in {"still_wrong", "budget"}):
            cases[0]["expected"] = 913
        if len(requests) == 2:
            message = str(kwargs["messages"])
            assert "observed" not in message.lower() and "return value * multiplier" not in message
            if mode == "cancel_correction":
                runner.request_cancel()
            if mode == "drift_correction":
                target.write_text(after + "# external drift\n")
            if mode == "permission_correction":
                broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
            if mode == "uncertain_correction":
                broker.evidence.append({"tool": "apply_text_patch", "success": False, "wrapper_rejected": False})
        return {"checks": wire_checks(cases)}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch, handler=lambda *_: pytest.fail("Verification dispatched a tool"))
    if mode == "cancel_correction":
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_verify_committed_transaction(PROMPT, None))
    else:
        result = asyncio.run(runner._wrapper_verify_committed_transaction(PROMPT, None))
        success = mode in {"preserved", "corrected", "main_example"}
        assert (result[0] is True) == success
        evidence = broker.evidence[-1]
        assert evidence["tool"] == "wrapper_bounded_numeric_probe"
        assert evidence["success"] == success and evidence["module_executed"] is False
        assert bool(evidence["reason"]) if not success else evidence["checked_cases"] >= 2
        if mode == "unsupported":
            assert evidence["phase"] == "preflight" and evidence["checks"] == []
        if mode == "still_wrong":
            assert evidence["outcome"] == "verification_disagreement" and len(evidence["attempts"]) == 2
    expected = 0 if mode == "unsupported" else 1 if mode in {"preserved", "main_example", "budget"} else 2
    assert len(requests) == expected
    assert target.read_text() == after + ("# external drift\n" if mode == "drift_correction" else "")
    assert not calls and len(broker.action_progress.recoveries) <= 2
    if mode != "cancel_correction":
        asyncio.run(runner._wrapper_verify_committed_transaction(PROMPT, None))
        assert len(requests) == expected
