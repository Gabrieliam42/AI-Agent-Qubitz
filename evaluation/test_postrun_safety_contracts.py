"""CPU regressions derived from the stopped October/November follow-up evidence."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from evaluation.process_inventory import is_qubitz_worker
from runtime.cancellation import (
    RequestCancelled,
    request_cancellation,
    stop_description,
)
from runtime.invocation_receipts import (
    INVOCATION_ID,
    canonical_arguments,
    dispatched_receipt,
)


@pytest.mark.parametrize("origin", ["user", "fixture", "coordinator_stop", "evaluator_timeout", "thermal_abort"])
def test_typed_cancellation_is_not_a_thermal_runtime_error(origin):
    error = RequestCancelled(origin)
    assert isinstance(error, asyncio.CancelledError) and error.origin == origin
    assert not isinstance(error, RuntimeError)
    assert ("user's request" in stop_description(origin)) is (origin == "user")


def test_cancel_callback_is_never_retried_after_type_error():
    calls = []

    def cancel(origin="user"):
        calls.append(origin)
        raise TypeError("failure after dispatch")

    with pytest.raises(TypeError, match="after dispatch"):
        request_cancellation(SimpleNamespace(request_cancel=cancel), "coordinator_stop")
    assert calls == ["coordinator_stop"]


@pytest.mark.parametrize("command,windows,expected", [
    ('"C:\\Program Files\\Python312\\python.exe" -u -B "D:\\work\\AI_Agent_Qubitz_test.py"', True, True),
    ('"C:\\Program Files\\Python312\\python.exe" -c "name=\'AI_Agent_Qubitz_test.py\'"', True, False),
    ('python -B observer.py AI_Agent_Qubitz_test.py', True, False),
    ('/project/.venv/bin/python -B /workspace/AI_Agent_Qubitz_test.py', False, True),
    ('python -c "print(\'AI_Agent_Qubitz_test.py\')"', False, False),
    ('python -uc "AI_Agent_Qubitz_test.py"', False, False),
    ('python -m pytest AI_Agent_Qubitz_test.py', False, False),
])
def test_only_executed_script_can_be_a_worker(command, windows, expected):
    assert is_qubitz_worker(command, windows=windows) is expected


def test_canonical_dispatch_is_bound_to_original_request_and_invocation():
    original = {"path": "run_tests.py</arg_value>"}
    dispatched = {"path": "run_tests.py"}
    token = INVOCATION_ID.set("owned-invocation")
    try:
        receipt = dispatched_receipt("run_existing_entrypoint", original, dispatched)
    finally:
        INVOCATION_ID.reset(token)
    row = {"name": "run_existing_entrypoint", "invocation_id": "owned-invocation",
           "arguments": original, "dispatch_receipt": receipt}
    assert canonical_arguments(row) == dispatched
    dispatched["path"] = "other.py"
    assert canonical_arguments(row) == {"path": "run_tests.py"}
    row["invocation_id"] = "other-turn"
    assert canonical_arguments(row) is None
    row["invocation_id"] = "owned-invocation"
    receipt["dispatched_arguments"]["path"] = "forged.py"
    assert canonical_arguments(row) is None
