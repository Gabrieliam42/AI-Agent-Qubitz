"""CPU regressions for independent baselines and trustworthy observation metadata."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from evaluation import disposable_followup_matrix as matrix
from evaluation.followup_matrix_cases import (
    Check,
    Turn,
    capture_behavior_baselines,
    verify_turn,
    workspace_manifest,
)


def test_unchanged_function_needs_no_new_backup(tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("def total(prices):\n return sum(prices)\n")
    turn = Turn("Extend pricing.py.", (Check("default_behavior", "pricing.py", "total"),), ("pricing.py",))
    before = workspace_manifest(tmp_path)
    issues, evidence = verify_turn(tmp_path, turn, {}, before, before)
    assert not issues
    assert evidence[0]["baseline_origin"] == "unchanged_source"
    assert not (tmp_path / "backup").exists()


@pytest.mark.parametrize("formula,regressed", [
    ("sum(prices) if discount == 0 else sum(prices)*(100-discount)/100", False),
    ("sum(prices)*(100-discount)/100", True),
])
def test_captured_baseline_is_independent_of_agent_backup(tmp_path, formula, regressed):
    target = tmp_path / "pricing.py"
    target.write_text("def total(prices):\n return sum(prices)\n")
    turn = Turn("Extend pricing.py.", (Check("default_behavior", "pricing.py", "total"),), ("pricing.py",))
    before = workspace_manifest(tmp_path)
    baseline = capture_behavior_baselines(tmp_path, turn)
    target.write_text("def total(prices, discount=0):\n return " + formula + "\n")
    issues, evidence = verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path), behavior_baselines=baseline)
    assert ("postcondition_failed:default_behavior:pricing.py" in issues) is regressed
    assert "python_backup_policy_failed:pricing.py" in issues
    assert evidence[0]["baseline_origin"] == "evaluator_snapshot"


def test_corrupted_baseline_fails_before_verification(tmp_path, monkeypatch):
    import complex_smoke_runtime

    target = tmp_path / "pricing.py"
    target.write_text("def total(prices):\n return sum(prices)\n")
    turn = Turn("Extend pricing.py.", (Check("default_behavior", "pricing.py", "total"),), ("pricing.py",))
    before = workspace_manifest(tmp_path)
    baseline = capture_behavior_baselines(tmp_path, turn)
    baseline["pricing.py"]["sha256"] = "0" * 64
    monkeypatch.setattr(complex_smoke_runtime, "verify_python", lambda *_: pytest.fail("Corrupt baseline reached execution"))
    issues, evidence = verify_turn(tmp_path, turn, {}, before, before, behavior_baselines=baseline)
    assert "postcondition_failed:default_behavior:pricing.py" in issues
    assert "does not match the before manifest" in evidence[0]["error"]


def test_baseline_capture_rejects_workspace_escape(tmp_path):
    turn = Turn("Compare behavior.", (Check("default_behavior", "../pricing.py", "total"),))
    with pytest.raises(ValueError, match="Invalid fixture path"):
        capture_behavior_baselines(tmp_path, turn)


def test_event_sequence_and_monotonic_time_survive_wall_clock_reversal(tmp_path, monkeypatch):
    walls, clocks = iter((100.0, 90.0)), iter((10.0, 11.0))
    monkeypatch.setattr(matrix.time, "time", lambda: next(walls))
    monkeypatch.setattr(matrix.time, "monotonic", lambda: next(clocks))
    observer = matrix.Observer(tmp_path)
    observer.emit("first")
    observer.emit("second")
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["time"] > rows[1]["time"]
    assert rows[0]["monotonic"] < rows[1]["monotonic"]


def test_generation_cancellation_delay_uses_monotonic_clock(tmp_path):
    rows = [
        {"kind": "http_request", "turn": 1, "time": 100, "monotonic": 1, "request_id": "a", "payload": {}},
        {"kind": "cancel_request", "turn": 1, "time": -100, "monotonic": 2, "origin": "evaluator_timeout"},
        {"kind": "http_stream_close", "turn": 1, "time": 500, "monotonic": 2.2, "request_id": "a", "closed": True},
    ]
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    quality = matrix.generation_quality(path, 1)
    assert quality["maximum_client_cancellation_delay_seconds"] == 0.2
    assert not quality["external_intervention"]
    assert "generation_cancellation_delayed" not in quality["problems"]


@pytest.mark.parametrize("origin", ["evaluator_timeout", "gui_early_exit", "external"])
def test_gui_cancel_origin_is_recorded_before_cancel(tmp_path, origin):
    root = SimpleNamespace(winfo_exists=lambda: True)
    runner = SimpleNamespace(history=[], config=SimpleNamespace())

    def cancel():
        runner._cancel_source = "user"
        runner._last_turn_control = {"cancel_requested": True}

    runner.request_cancel = cancel
    gui = SimpleNamespace(root=root, access_mode_var=SimpleNamespace(set=lambda _: None), _apply_access_mode=lambda: None)

    def send(_gui, _prompt, _timeout):
        if origin != "external":
            _gui._smoke_before_cancel(origin)
        runner.request_cancel()
        return {"completed": False, "timed_out": origin == "evaluator_timeout"}

    observer = matrix.Observer(tmp_path)
    observer.turn = 1
    observed = matrix.execute_turn(Turn("Read pricing.py.", ()), runner, gui, SimpleNamespace(_send_prompt=send),
                                   tmp_path, observer, 10)
    assert observed["generation_quality"]["cancellations"][0]["origin"] == origin
    assert observed["external_intervention"] is (origin == "external")
    assert not hasattr(gui, "_smoke_before_cancel")


def test_real_gui_send_announces_timeout_before_cancelling(monkeypatch):
    import tmp_smoke_variant as smoke

    calls = []
    root = SimpleNamespace(winfo_exists=lambda: True, _smoke_drive_exit_reason="deadline")
    gui = SimpleNamespace(root=root, busy=False, _smoke_events=[],
                          prompt_box=SimpleNamespace(delete=lambda *_: None, insert=lambda *_: None),
                          agent=SimpleNamespace(request_cancel=lambda: calls.append("cancel")),
                          _smoke_before_cancel=lambda origin: calls.append(origin))
    gui.send_prompt = lambda: setattr(gui, "busy", True)
    drives = iter((False, True, True))
    monkeypatch.setattr(smoke, "_drive", lambda *_: next(drives))
    observed = smoke._send_prompt(gui, "Read the workspace.", 1)
    assert observed["timed_out"]
    assert calls == ["evaluator_timeout", "cancel"]


def test_parallel_dispatches_and_consumed_results_have_distinct_receipts(tmp_path, monkeypatch):
    import smoke_create_calculator

    monkeypatch.setattr(smoke_create_calculator, "install_request_capture", lambda *_: None)
    dispatched = []

    class Server:
        def ensure_started(self):
            return None

    class Host:
        def __init__(self):
            self._qubitz_parallel_dispatching = False
            self.cached = {}

        async def _consume_parallel_read_only_batch(self, name, arguments, broker):
            if name in self.cached:
                return True, self.cached.pop(name)
            if self._qubitz_parallel_dispatching:
                return False, None
            self._qubitz_parallel_dispatching = True
            try:
                results = await asyncio.gather(self.call_tool("a", {}), self.call_tool("b", {}))
            finally:
                self._qubitz_parallel_dispatching = False
            self.cached = dict(zip(("a", "b"), results, strict=True))
            return True, self.cached.pop(name)

        async def call_tool(self, name, arguments):
            handled, result = await self._consume_parallel_read_only_batch(name, arguments, None)
            if handled:
                return result
            dispatched.append(name)
            return {"value": name}

    observer = matrix.Observer(tmp_path)
    base = SimpleNamespace(MCPHost=Host, LlamaCppServerProcess=Server, serialize_mcp_result=lambda value: value)
    observer.install(base)
    host = Host()

    async def run():
        assert await host.call_tool("a", {}) == {"value": "a"}
        assert await host.call_tool("b", {}) == {"value": "b"}

    asyncio.run(run())
    assert dispatched == ["a", "b"]
    assert len(observer.tools) == 4
    assert len({row["invocation_id"] for row in observer.tools}) == 4
    assert sum(row["execution_kind"] == "parallel_dispatch" for row in observer.tools) == 2
    assert sum(row["execution_kind"] == "parallel_result_consumption" for row in observer.tools) == 2
    assert all(row["duration_seconds"] >= 0 for row in observer.tools)
    assert all(row["parent_invocation_id"] for row in observer.tools if row["execution_kind"] == "parallel_dispatch")
    quality = matrix.workflow_quality({"tools": observer.tools}, Turn("Read a and b.", ()))
    assert quality["host_call_attempts"] == 2 and quality["parallel_result_consumptions"] == 2
    assert observer.tool_invocation.get() is None
