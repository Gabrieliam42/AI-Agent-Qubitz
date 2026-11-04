"""Snapshot spans and bounded numeric verification, without models or module execution."""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner
BEFORE = "def add(a, b):\n    return a + b\n"
AFTER = BEFORE + "\ndef divide(a, b):\n    if b == 0:\n        raise ValueError('zero divisor')\n    return a / b\n"
PROMPT = "Add divide(a, b) to calculator.py, raising ValueError for a zero divisor. Preserve add."
CASES = [
    {"function": "divide", "args": [8, 2], "expected": 4, "raises": ""},
    {"function": "divide", "args": [1, 0], "expected": None, "raises": "ValueError"},
]


def wire_checks(cases):
    return [{k: v for k, v in case.items() if k not in {"expected", "raises"}} | {
        "expectation": ({"kind": "exception", "name": case["raises"], "basis": "Requested exception condition"}
                        if case["raises"] else
                        {"kind": "value", "value": case["expected"], "basis": "Requested scalar behavior"}),
    } for case in cases]


def test_snapshot_spans_preserve_repeated_lines_crlf_and_literal_escapes():
    from runtime.recovery_protocol import snapshot_line_replacements

    text = 'a = "\\n"\r\n\r\nb = 2\r\n\r\n'
    value = snapshot_line_replacements(text, [
        {"start_line": 4, "end_line": 4, "new_text": "# trailing\r\n"},
        {"start_line": 2, "end_line": 2, "new_text": "# middle\r\n"},
    ])
    assert value == [{"old_text": text, "new_text": 'a = "\\n"\r\n# middle\r\nb = 2\r\n# trailing\r\n'}]


@pytest.mark.parametrize("spans", [
    [], [{"start_line": True, "end_line": 1, "new_text": "x"}],
    [{"start_line": 0, "end_line": 1, "new_text": "x"}],
    [{"start_line": 1, "end_line": 9, "new_text": "x"}],
    [{"start_line": 2, "end_line": 1, "new_text": "x"}],
    [{"start_line": 1, "end_line": 2, "new_text": "x"},
     {"start_line": 2, "end_line": 2, "new_text": "y"}],
])
def test_invalid_or_overlapping_spans_are_not_guessed(spans):
    from runtime.recovery_protocol import snapshot_line_replacements

    with pytest.raises(ValueError):
        snapshot_line_replacements(BEFORE, spans)


def test_numeric_probe_checks_task_cases_and_does_not_import_module():
    from runtime.behavioral_checks import check_numeric_cases, numeric_contract

    contract = numeric_contract(BEFORE, AFTER)
    assert set(contract) == {"divide"}
    assert check_numeric_cases(AFTER, contract, CASES) == 2
    with pytest.raises(ValueError, match="check failed"):
        check_numeric_cases(AFTER.replace("return a / b", "return a * b"), contract, CASES)


@pytest.mark.parametrize("source", [
    AFTER + "\nopen('unowned.txt', 'w').write('no')\n",
    AFTER.replace("return a / b", "return __import__('os').getpid()"),
    AFTER.replace("return a / b", "while True: pass"),
    AFTER.replace("return a / b", "return a ** b"),
    AFTER.replace("return a / b", "return [a] * b"),
    AFTER.replace("return a / b", "return a.real"),
    AFTER + "\ndef ValueError(message):\n    return 1\n",
])
def test_unsupported_code_is_never_executed(source, tmp_path, monkeypatch):
    from runtime.behavioral_checks import numeric_contract

    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        numeric_contract(BEFORE, source)
    assert not (tmp_path / "unowned.txt").exists()


@pytest.mark.parametrize("cases", [CASES[:1], CASES[1:], CASES + CASES,
    [{**CASES[0], "args": [float("inf"), 2]}, CASES[1]],
    [{**CASES[0], "function": "add"}, CASES[1]],
])
def test_missing_duplicate_or_foreign_cases_do_not_verify(cases):
    from runtime.behavioral_checks import check_numeric_cases, numeric_contract

    with pytest.raises(ValueError):
        check_numeric_cases(AFTER, numeric_contract(BEFORE, AFTER), cases)


@pytest.mark.parametrize("value", [True, None, "8", {}, 10**20])
def test_probe_argument_bounds_are_enforced(value):
    from runtime.behavioral_checks import check_numeric_cases, numeric_contract

    with pytest.raises(ValueError):
        check_numeric_cases(AFTER, numeric_contract(BEFORE, AFTER), [{**CASES[0], "args": [value, 2]}, CASES[1]])


@pytest.mark.parametrize("mode", ["symlink", "oversized", "missing", "changed"])
def test_receipt_hash_checks_do_not_read_unsafe_replacements(tmp_path, monkeypatch, mode):
    from runtime.behavioral_checks import numeric_hashes_current

    target = tmp_path / "function.py"
    target.write_text(BEFORE)
    hashes = {str(target): hashlib.sha256(target.read_bytes()).hexdigest()}
    if mode == "symlink":
        target.unlink()
        target.symlink_to(tmp_path.parent / "unowned.py")
    elif mode == "missing":
        target.unlink()
    elif mode == "oversized":
        target.write_text("x" * 24001)
    else:
        target.write_text(AFTER)
    if mode != "changed":
        monkeypatch.setattr(type(target), "read_bytes", lambda *_: pytest.fail("Unsafe replacement was read"))
    assert not numeric_hashes_current(tmp_path, hashes)


@pytest.mark.parametrize("source", [
    "def added(a):\n    return a * a\n",
    "def added(a):\n    if a:\n        1\n    return a\n",
    "def added(a):\n    raise ValueError\n",
    "def added(a):\n    return external\n",
    "@external\ndef added(a):\n    return a\n",
])
def test_bounds_and_unsupported_semantics_cannot_be_successful_checks(source):
    from runtime.behavioral_checks import check_numeric_cases, numeric_contract

    with pytest.raises((ValueError, TypeError)):
        check_numeric_cases(source, numeric_contract("", source), [
            {"function": "added", "args": [10**12], "expected": 10**12, "raises": ""},
            {"function": "added", "args": [2], "expected": 2, "raises": ""},
        ])


@pytest.mark.parametrize("mode", ["passed", "failed", "changed", "cancelled", "uncertain", "forbidden", "explicit_tests",
                                 "missing_case", "foreign_path", "exhausted", "read_only", "generation_changed",
                                 "turn_changed", "late_read_only", "late_uncertain"])
def test_no_suite_fallback_has_real_scoped_receipts_and_no_replay(variant, runner, tmp_path, monkeypatch, mode):
    target = tmp_path / "calculator.py"
    prompt = PROMPT + (" Do not execute anything." if mode == "forbidden" else
                       " Run missing_tests.py." if mode == "explicit_tests" else "")
    broker = receipts.begin(runner, prompt)
    broker.required_postconditions.add("tests")
    broker.transaction_originals[str(target)] = {"data": BEFORE.encode()}
    target.write_text(AFTER)
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    if mode == "uncertain":
        broker.evidence.append({"tool": "apply_text_patch", "success": False, "wrapper_rejected": False})
    if mode == "exhausted":
        broker.no_progress_recovery_exhausted = True
    if mode == "read_only":
        broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        assert "Return a / b" not in str(kwargs["messages"])
        if mode == "changed":
            target.write_text(AFTER + "# external change\n")
        if mode == "cancelled":
            runner.request_cancel()
        if mode == "generation_changed":
            broker.workspace_generation += 1
        if mode == "turn_changed":
            broker.turn_id = "another-turn"
        if mode == "late_read_only":
            broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
        if mode == "late_uncertain":
            broker.evidence.append({"tool": "apply_text_patch", "success": False, "wrapper_rejected": False})
        cases = [{"path": target.name, **case} for case in CASES]
        if mode == "failed":
            cases[0]["expected"] = 999
        if mode == "missing_case":
            cases.pop()
        if mode == "foreign_path":
            cases[0]["path"] = "other.py"
        return {"checks": wire_checks(cases)}

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch,
                                  handler=lambda *_: pytest.fail("Fallback dispatched a tool or process"))
    if mode == "cancelled":
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
    else:
        outcome = asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
        if mode == "passed":
            assert outcome[0] is True and "bounded numeric" in outcome[1]
            assert "tests" not in broker.completion_report(prompt)[1]
            assert "not a full test suite" in broker.completion_evidence_summary(prompt)
            evidence = broker.evidence[-1]
            assert evidence["verification_kind"] == "bounded_ast_numeric_probe"
            assert evidence["checked_cases"] == 2 and not evidence.get("execution_verified")
            target.write_text(AFTER + "# later drift\n")
            assert "tests" in broker.completion_report(prompt)[1]
        else:
            assert "tests" in broker.completion_report(prompt)[1]
    assert not calls
    asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None)) if mode != "cancelled" else None
    assert len(proposals) == (0 if mode in {"uncertain", "forbidden", "explicit_tests", "exhausted", "read_only"}
                              else 2 if mode in {"failed", "missing_case", "foreign_path"} else 1)


@pytest.mark.parametrize("mode", ["passed", "changed", "cancelled", "wrong_target", "overlap"])
def test_exact_recovery_uses_snapshot_lines_and_normal_hash_bound_broker(variant, runner, tmp_path, monkeypatch, mode):
    target = tmp_path / "notes.txt"
    target.write_bytes(b"same\r\nsame\r\n")
    other = tmp_path / "other.txt"
    other.write_text("untouched\n")
    prompt = "Edit notes.txt to replace only the second line with revised."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    broker.named_operation_contract = {"operation": "edit", "target": str(target)}
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.transaction_snapshot_hashes[str(target)] = digest
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        branch = kwargs["schema"]["properties"]["patches"]["items"]["anyOf"][0]
        assert branch["properties"]["path"]["enum"] == [target.name]
        schema = branch["properties"]["replacements"]["items"]
        assert schema["properties"]["end_line"]["maximum"] == 2
        assert schema["required"] == ["start_line", "end_line", "new_text"]
        assert "1: same" in str(kwargs["messages"]) and "2: same" in str(kwargs["messages"])
        result = {"patches": [{"path": target.name, "replacements": [
            {"start_line": 2, "end_line": 2, "new_text": "revised\r\n"},
        ]}]}
        if mode == "changed":
            target.write_bytes(b"external\r\n")
        if mode == "cancelled":
            runner.request_cancel()
        if mode == "wrong_target":
            result["patches"][0]["path"] = other.name
        if mode == "overlap":
            result["patches"][0]["replacements"] *= 2
        return result

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))

    def handle(name, arguments):
        if name == "read_file_snapshot":
            return {"status": "text_snapshot", "sha256": digest, "content": target.read_bytes().decode()}
        assert name == "apply_text_patch" and arguments["expected_sha256"] == digest
        allowed, error = broker.authorize(name, arguments)
        assert allowed, error
        replacement = arguments["replacements"][0]
        assert replacement["old_text"] == "same\r\nsame\r\n"
        target.write_bytes(replacement["new_text"].encode())
        new_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        broker.transaction_changed_hashes[str(target)] = new_hash
        return {"status": "completed", "path": str(target), "new_sha256": new_hash}

    calls = receipts.install_host(variant, runner, monkeypatch, handler=handle)
    if mode == "cancelled":
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
    else:
        answer = asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
        assert bool(answer) is (mode == "passed")
    assert len(proposals) == 1
    assert [name for name, _ in calls] == (["read_file_snapshot", "apply_text_patch"] if mode == "passed" else [])
    assert target.read_bytes() == (b"same\r\nrevised\r\n" if mode == "passed" else
                                   b"external\r\n" if mode == "changed" else b"same\r\nsame\r\n")
    assert other.read_text() == "untouched\n"
