"""CPU-only regressions for the observed follow-up failures; never start a model."""
from __future__ import annotations

import asyncio
import hashlib
import tkinter
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

begin = receipts.begin
result = receipts.result
runner = receipts.runner
variant = receipts.variant


def invocation(reference="fixture_mcp.py", **extra):
    return {"server_reference": reference, "tool_name": "update_record",
            "arguments": {"record_id": "bound-id", "value": 11}, **extra}


@pytest.mark.parametrize("prompt", [
    "Fix pricing.py, then rerun the same run_tests.py command. Do not change the test runner.",
    "Rerun run_tests.py without editing it.",
])
def test_explicit_rerun_is_authorized(variant, runner, tmp_path, prompt):
    (tmp_path / "run_tests.py").write_text("raise SystemExit(0)\n")
    broker = begin(runner, prompt)
    assert broker.task_intent.semantic.requests("execute")
    assert broker.authorize("run_existing_entrypoint", {"path": "run_tests.py"})[0]


def test_rerun_prohibition_and_read_only_mode_remain_effective(variant, runner, tmp_path):
    (tmp_path / "run_tests.py").write_text("raise SystemExit(0)\n")
    broker = begin(runner, "Fix pricing.py. Do not rerun run_tests.py.")
    assert not broker.authorize("run_existing_entrypoint", {"path": "run_tests.py"})[0]
    runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    broker = begin(runner, "Rerun run_tests.py.")
    assert not broker.authorize("run_existing_entrypoint", {"path": "run_tests.py"})[0]


def test_unknown_mutation_replay_is_blocked_across_path_aliases(variant, runner, tmp_path):
    broker = begin(runner, "Call the MCP update_record tool once.")
    first = invocation()
    assert broker.reserve_mcp_invocation(first) is None
    broker.record_result("call_project_mcp_tool", first, result({"result": {"isError": True}}))
    equivalent = invocation(str(tmp_path / "fixture_mcp.py"), cwd=".", timeout_seconds=90)
    assert broker.reserve_mcp_invocation(equivalent)["status"] == "mcp_replay_blocked"
    assert len(broker.mcp_invocations) == 1


def test_inflight_mutation_and_wrapper_recovery_share_dispatch_guard(variant, runner, monkeypatch):
    broker = begin(runner, "Call the MCP update_record tool exactly once with value=11.")
    broker.record_result("list_project_mcp_tools", {"server_reference": "fixture_mcp.py"}, result({
        "count": 1, "tools": [{"name": "update_record", "inputSchema": {"type": "object"}}],
    }))
    host_class = variant._APP.base.MCPHost
    guarded = next(index for index, cls in enumerate(host_class.__mro__) if cls.__name__ == "HardenedMCPHost")
    parent = next(cls for cls in host_class.__mro__[guarded + 1:] if "call_tool" in cls.__dict__)
    dispatched = []

    async def call(_host, name, arguments):
        dispatched.append((name, arguments))
        return result({"result": {"isError": True}})

    monkeypatch.setattr(parent, "call_tool", call)
    host = host_class(runner.workspace)
    asyncio.run(host.call_tool("call_project_mcp_tool", invocation()))
    denied = asyncio.run(host.call_tool("call_project_mcp_tool", invocation()))
    assert denied.structuredContent["status"] == "mcp_replay_blocked"
    assert len(dispatched) == 1
    assert broker.last_mcp_result == {"success": False}


def test_safe_read_retry_respects_explicit_one_attempt(variant, runner):
    broker = begin(runner, "Call the MCP update_record tool. If it fails, report the failure.")
    args = invocation()
    trait_key = broker._mcp_invocation_key(args, include_arguments=False)
    broker.mcp_tool_traits[trait_key] = {"readOnlyHint": True, "idempotentHint": True}
    assert broker.reserve_mcp_invocation(args) is None
    broker.record_result("call_project_mcp_tool", args, result({"result": {"isError": True}}))
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"
    broker.current_prompt = "Call the MCP update_record tool; retry a transient read failure once."
    assert broker.reserve_mcp_invocation(args) is None
    broker.record_result("call_project_mcp_tool", args, result({"result": {"isError": True}}))
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"


def test_successful_once_call_is_not_replayed(variant, runner):
    broker = begin(runner, "Call the MCP update_record tool once.")
    args = invocation()
    assert broker.reserve_mcp_invocation(args) is None
    broker.record_result("call_project_mcp_tool", args, result({"result": {"value": 11}}))
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"


def test_mcp_followup_action_cannot_be_verified_by_old_or_missing_receipt(variant, runner):
    broker = begin(runner, "Update that same record with update_record to value=11.")
    broker.mcp_tool_schemas["update_record"] = {"type": "object"}
    broker.follow_up_mcp_bindings = {"fixture_mcp.py": {"schemas": broker.mcp_tool_schemas}}
    assert "mcp_tool_called" in broker.completion_report(broker.current_prompt)[1]
    assert broker.authorize("call_project_mcp_tool", invocation())[0]
    broker.record_result("call_project_mcp_tool", invocation(), result({
        "result": {"structuredContent": {"record_id": "bound-id", "value": 11}},
    }))
    assert not broker.completion_report(broker.current_prompt)[1]


def test_mcp_binding_does_not_drop_a_separate_file_edit(variant, runner, tmp_path):
    (tmp_path / "settings.json").write_text('{}\n')
    broker = begin(runner, "Update the record with update_record to value=11, then edit settings.json to match.")
    broker.mcp_tool_schemas["update_record"] = {"type": "object"}
    required, missing = broker.completion_report(broker.current_prompt)
    assert {"mcp_tool_called", "changed_files"} <= required
    assert {"mcp_tool_called", "changed_files"} <= missing


@pytest.mark.parametrize("path", ["update_record.py", "docs/update_record", "docs\\update_record"])
def test_mcp_tool_names_inside_file_paths_are_not_invocation_requests(variant, runner, path):
    broker = begin(runner, f"Update {path} to fix its contents.")
    broker.mcp_tool_schemas["update_record"] = {"type": "object"}
    assert not variant._requested_mcp_tool_names(broker.current_prompt, tuple(broker.mcp_tool_schemas))
    assert "mcp_tool_called" not in broker.completion_report(broker.current_prompt)[0]


def test_mcp_reference_is_not_bound_as_an_edit_destination(variant, runner, tmp_path):
    (tmp_path / "fixture_mcp.py").write_text('# MCP source, not an edit target\n')
    broker = begin(runner, "Update that record with update_record to value=11. MCP server reference: fixture_mcp.py")
    assert broker.named_operation_contract is None
    broker.mcp_tool_schemas["update_record"] = {"type": "object"}
    required, missing = broker.completion_report(broker.current_prompt)
    assert "mcp_tool_called" in missing
    assert "changed_files" not in required


def test_rename_contract_survives_changed_path_state(variant, runner, tmp_path):
    original = tmp_path / "copy.txt"
    final = tmp_path / "final.txt"
    original.write_text("copy\n")
    broker = begin(runner, "Rename copy.txt to final.txt.")
    final.write_text(original.read_text())
    broker.record_result("write_file", {"path": "final.txt", "content": "copy\n"}, result({
        "created": True, "new_sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
    }))
    assert "changed_files" in broker.completion_report(broker.current_prompt)[1]
    original.rename(tmp_path / "ignored.txt")
    assert "changed_files" in broker.completion_report(broker.current_prompt)[1]
    broker.record_result("move_path", {"source": "copy.txt", "destination": "final.txt"}, result({"status": "moved"}))
    assert "changed_files" not in broker.completion_report(broker.current_prompt)[1]


def prior_receipt(runner, **value):
    return {"workspace": str(runner.workspace.resolve()), "prompt": "Call echo.",
            "mcp_bindings": {"fixture_mcp.py": {"schemas": {}, "results": []}},
            "last_mcp_result": {"success": True, "tool": "echo", "value": value}}


def test_historical_scalar_answer_needs_no_model_or_retrieval(variant, runner, monkeypatch):
    runner._last_actionable_task = prior_receipt(runner, value="followup", label="second")
    monkeypatch.setattr(runner, "_prefetch_named_source_context", lambda *_args: pytest.fail("receipt answer attempted a tool read"))
    monkeypatch.setattr(runner.retriever, "format_context", lambda *_args: pytest.fail("receipt answer attempted embeddings"))
    prompt = "What value did the last tool invocation return? Answer only that value; do not call a tool again."
    assert asyncio.run(runner._run_async(prompt)) == "followup"


@pytest.mark.parametrize("prompt", [
    "What is the current value of the last tool result? Answer only the value.",
    "What value did the last tool return? Also inspect settings.json. Answer only the value.",
    "Call echo again, then answer only the value from the last tool.",
    "What value and label did the last tool return? Answer only the value.",
    "What value did the last tool return, and why did it fail? Answer only the value.",
    "What value did the last tool not return? Answer only the value.",
    "What value did the last tool return? Answer only the value in uppercase.",
    "What value did the first tool return? Answer only the value.",
])
def test_receipt_fast_path_does_not_hide_new_work(variant, runner, prompt):
    assert runner._verified_mcp_result_answer(prompt, prior_receipt(runner, value="followup", label="second")) is None


def test_failed_receipt_and_other_workspace_are_not_reused(variant, runner):
    prompt = "What value did the last tool return? Answer only the value."
    previous = prior_receipt(runner, value="followup")
    previous["last_mcp_result"]["success"] = False
    assert runner._verified_mcp_result_answer(prompt, previous) is None
    previous = prior_receipt(runner, value="followup")
    previous["workspace"] = str(runner.workspace / "other")
    assert runner._verified_mcp_result_answer(prompt, previous) is None


@pytest.mark.parametrize("field,value", [("label", "exact label"), ("value", 11), ("record_id", "bound-id")])
def test_historical_fields_are_returned_exactly(variant, runner, field, value):
    prompt = f"Which {field} did the last MCP tool call return? Answer only the {field}."
    assert runner._verified_mcp_result_answer(prompt, prior_receipt(runner, **{field: value})) == str(value)


def test_evaluator_gui_teardown_is_idempotent_without_hiding_other_errors():
    from evaluation.disposable_followup_matrix import destroy_gui_once, gui_root_exists

    class Root:
        alive = True
        destroys = 0

        def winfo_exists(self):
            if not self.alive:
                raise tkinter.TclError('can\'t invoke "winfo" command: application has been destroyed')
            return True

        def destroy(self):
            self.destroys += 1
            self.alive = False

    root = Root()
    gui = SimpleNamespace(root=root)
    destroy_gui_once(gui)
    destroy_gui_once(gui)
    assert root.destroys == 1 and not gui_root_exists(gui)

    def fail():
        raise tkinter.TclError("unrelated GUI failure")

    root.winfo_exists = fail
    with pytest.raises(tkinter.TclError, match="unrelated GUI failure"):
        destroy_gui_once(gui)


def test_closed_gui_turn_is_interrupted_not_timed_out(tmp_path):
    from evaluation.disposable_followup_matrix import execute_turn

    root = SimpleNamespace(winfo_exists=lambda: False)
    gui = SimpleNamespace(root=root, access_mode_var=SimpleNamespace(set=lambda _mode: None),
                          _apply_access_mode=lambda: None)
    runner = SimpleNamespace()
    smoke = SimpleNamespace(_send_prompt=lambda *_args: {"completed": False, "timed_out": True})
    from evaluation.disposable_followup_matrix import Observer

    observer = Observer(tmp_path)
    observer.turn = 1
    turn = SimpleNamespace(prompt="Read the workspace.", access="full", steering=None, cancel=False)
    observed = execute_turn(turn, runner, gui, smoke, tmp_path, observer, 30)
    assert observed["gui_closed"] and observed["interruption"] == "gui_closed"
    assert not observed["timed_out"]
