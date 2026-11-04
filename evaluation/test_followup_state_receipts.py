"""CPU-only checks for turn ownership, exact evidence and bounded recovery."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.variant_test_selection import selected_variant_names
from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = selected_variant_names((
    "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py",
    "AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py",
    "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py",
), ROOT)


@pytest.fixture(scope="module", params=VARIANTS)
def variant(request):
    name = "followup_receipts_" + str(request.param).replace(".", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, variant_path(request.param, ROOT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)
    sys.modules.pop(module._APP.base.__name__, None)


@pytest.fixture
def runner(variant, tmp_path, monkeypatch):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    monkeypatch.setattr(variant._APP.base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("CPU test attempted to load a model"))
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    yield instance
    instance.reset_runtime()


def result(data, error=False):
    return SimpleNamespace(isError=error, structuredContent=data, content=[])


def begin(runner, prompt):
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    return broker


def install_host(variant, runner, monkeypatch, tools=(), handler=None):
    calls = []

    class Host:
        def __init__(self, _workspace):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            if handler:
                data = handler(name, arguments)
            elif name == "read_file_snapshot":
                target = Path(arguments["path"])
                if not target.is_absolute():
                    target = runner.workspace / target
                data = {"status": "text_snapshot", "content": target.read_text(),
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "start_line": 1, "end_line": len(target.read_text().splitlines())}
            elif name == "list_project_mcp_tools":
                data = {"tools": list(tools), "count": len(tools)}
            else:
                data = {"result": {"structuredContent": {"value": arguments["arguments"]}}}
            receipt = result(data)
            runner._tool_permission_broker.record_result(name, arguments, receipt)
            return receipt

    monkeypatch.setattr(variant._APP.base, "MCPHost", Host)
    return calls


def test_fix_then_same_runner_requires_execution_and_pass(variant, runner, tmp_path):
    target = tmp_path / "run_tests.py"
    target.write_text("raise SystemExit(0)\n")
    prompt = "Fix pricing.py, then rerun the same run_tests.py command. Do not change the test runner."
    broker = begin(runner, prompt)
    required, missing = broker.completion_report(prompt)
    assert {"changed_files", "entrypoint_executed", "tests"} <= required
    broker.record_result("run_existing_entrypoint", {"path": "run_tests.py"}, result({
        "returncode": 1, "command": [sys.executable, "run_tests.py"], "status": "completed",
    }))
    missing = broker.completion_report(prompt)[1]
    assert "entrypoint_executed" not in missing
    assert "tests" in missing
    assert broker.evidence[-1]["execution_verified"] is True


def test_tests_before_edit_do_not_verify_after_edit(variant, runner):
    prompt = "Fix pricing.py then run tests."
    broker = begin(runner, prompt)
    broker.record_result("run_existing_entrypoint", {"path": "tests"}, result({
        "returncode": 0, "command": [sys.executable, "-m", "pytest"], "test_runner": "pytest",
    }))
    assert "tests" not in broker.completion_report(prompt)[1]
    broker.workspace_generation += 1
    assert "tests" in broker.completion_report(prompt)[1]


def test_each_requested_test_runner_must_pass_after_changes(variant, runner, tmp_path):
    for name in ("run_tests.py", "check_tests.py"):
        (tmp_path / name).write_text("raise SystemExit(0)\n")
    prompt = "Run run_tests.py and execute check_tests.py."
    broker = begin(runner, prompt)
    for name, code in (("run_tests.py", 0), ("check_tests.py", 1)):
        broker.record_result("run_existing_entrypoint", {"path": name}, result({
            "returncode": code, "command": [sys.executable, name], "status": "completed",
        }))
    assert "entrypoint_executed" not in broker.completion_report(prompt)[1]
    assert "tests" in broker.completion_report(prompt)[1]
    broker.record_result("run_existing_entrypoint", {"path": "check_tests.py"}, result({
        "returncode": 0, "command": [sys.executable, "check_tests.py"], "status": "completed",
    }))
    assert "tests" not in broker.completion_report(prompt)[1]
    broker.record_result("run_existing_entrypoint", {"path": "check_tests.py"}, result({
        "returncode": 1, "command": [sys.executable, "check_tests.py"], "status": "completed",
    }))
    assert "tests" in broker.completion_report(prompt)[1]


@pytest.mark.parametrize("envelope", [
    {"result": {"isError": True, "content": []}},
    {"result": {"structuredContent": {"is_error": True}}},
])
def test_nested_mcp_error_never_verifies_call(variant, runner, envelope):
    prompt = "Call the MCP echo tool with value='verified'."
    broker = begin(runner, prompt)
    broker.record_result("call_project_mcp_tool", {"tool_name": "echo", "arguments": {"value": "verified"}}, result(envelope))
    assert "mcp_tool_called" in broker.completion_report(prompt)[1]
    assert broker.evidence[-1]["success"] is False


def test_business_status_is_not_an_mcp_protocol_error(variant):
    assert not variant._mcp_result_has_error(result({"result": {"structuredContent": {"status": "failed"}}}))


def test_server_binding_fills_only_missing_routing(variant, runner):
    broker = begin(runner, "Call the MCP typed_echo tool with value=11 and label='phase2'.")
    schema = {"type": "object", "properties": {"value": {"type": "integer"}, "label": {"type": "string"}}}
    broker.follow_up_mcp_bindings = {"fixture_mcp.py": {"schemas": {"typed_echo": schema}}}
    supplied = {"tool_name": "typed_echo", "arguments": {"value": 11, "label": "phase2"}}
    repaired = broker.preserve_mcp_call_arguments(supplied)
    assert repaired["arguments"] == supplied["arguments"]
    assert repaired["server_reference"] == "fixture_mcp.py"
    explicit = {**supplied, "server_reference": "other.py"}
    assert broker.preserve_mcp_call_arguments(explicit)["server_reference"] == "other.py"
    broker.follow_up_mcp_bindings["other.py"] = {"schemas": {"typed_echo": schema}}
    assert "server_reference" not in broker.preserve_mcp_call_arguments(supplied)


def test_current_state_question_reads_fresh_exact_target(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"owner":"external-editor","limit":9}\n')
    begin(runner, "What is owner now? Do not change anything.")
    runner._active_follow_up_paths = (str(target),)
    calls = install_host(variant, runner, monkeypatch)
    context = asyncio.run(runner._prefetch_named_source_context("What is owner now? Do not change anything.", None))
    assert "external-editor" in context
    assert [name for name, _args in calls] == ["read_file_snapshot"]
    assert target.read_text() == '{"owner":"external-editor","limit":9}\n'


@pytest.mark.parametrize("prompt,speech_act,requested,prohibited", [
    ("What is owner now? Do not change anything.", "question", set(), {"edit"}),
    ("What is owner now? Please do not modify anything.", "question", set(), {"edit"}),
    ("How can I edit settings.json? Do not change anything.", "explanation", set(), {"edit"}),
    ("Do not change anything.", "prohibition", set(), {"edit"}),
    ("Please do not modify anything.", "prohibition", set(), {"edit"}),
    ("Do not change anything or install dependencies.", "prohibition", set(), {"edit", "install"}),
    ("Why must you never modify settings.json?", "question", set(), set()),
    ("What is owner now? Do not read settings.json.", "question", set(), {"read"}),
    ("Update settings.json. Do not install dependencies.", "request", {"edit"}, {"install"}),
])
def test_constraints_preserve_query_intent_and_permissions(variant, prompt, speech_act, requested, prohibited):
    intent = variant._analyze_semantic_intent(prompt)
    assert intent.speech_act == speech_act
    assert set(intent.requested_actions) == requested
    assert set(intent.prohibited_actions) == prohibited
    if prompt == "Why must you never modify settings.json?":
        assert ("edit", "settings.json") in intent.scoped_prohibitions
    if "edit" not in requested:
        assert "changed_files" not in intent.required_postconditions


def test_question_prefetch_respects_explicit_read_prohibition(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    original = '{"owner":"private","limit":9}\n'
    target.write_text(original)
    prompt = "What is owner now? Do not read settings.json. Do not modify anything."
    broker = begin(runner, prompt)
    runner._active_follow_up_paths = (str(target),)
    calls = install_host(variant, runner, monkeypatch)
    assert broker.task_intent.semantic.speech_act == "question"
    assert "read" in broker.task_intent.semantic.prohibited_actions
    assert asyncio.run(runner._prefetch_named_source_context(prompt, None)) == ""
    assert calls == []
    assert target.read_text() == original


def test_scalar_correction_survives_parent_route_cleanup(variant, runner, tmp_path, monkeypatch):
    # Exercise model-format recovery, independently of the verified scalar-read fast path.
    monkeypatch.setattr(variant, "_verified_file_read_answer", lambda *args: None)
    target = tmp_path / "settings.json"
    target.write_text('{"limit":7}\n')
    install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    hardened_index = next(index for index, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner")
    calls = []

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        self._route_decision = None
        return "7" if "Format correction:" in prompt else "The current limit is 7."

    monkeypatch.setattr(mro[hardened_index + 1], "_run_async", model_pass)
    answer = asyncio.run(runner._run_async("What is limit in settings.json? Answer only the number. Do not modify it."))
    assert answer == "7"
    assert len(calls) == 2
    assert "Current file:" in calls[0]


def test_followup_rename_ignores_preserve_only_reference(variant, tmp_path):
    (tmp_path / "copy.txt").write_text("copy\n")
    (tmp_path / "source.txt").write_text("source\n")
    request = variant._named_file_operation_request(
        variant._APP.base, tmp_path, variant.ACCESS_MODE_FULL,
        "Rename copy.txt to final.txt. Keep source.txt.",
    )
    assert request is not None and request["operation"] == "move"
    assert Path(request["source"]).name == "copy.txt"
    assert Path(request["destination"]).name == "final.txt"


def test_mcp_returned_identifier_is_reused_without_guessing(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "fixture_mcp.py").write_text("from mcp.server.fastmcp import FastMCP\nserver = FastMCP('fixture')\nserver.run(transport='stdio')\n")
    prompt = "Call the MCP update_record tool with the returned record_id and value=11."
    broker = begin(runner, prompt)
    schema = {"type": "object", "properties": {"record_id": {"type": "string"}, "value": {"type": "integer"}},
              "required": ["record_id", "value"]}
    broker.follow_up_mcp_bindings = {"fixture_mcp.py": {
        "schemas": {"update_record": schema}, "results": [{"tool": "create_record", "value": {"record_id": "verified-id"}}],
    }}
    broker.mcp_tool_schemas["update_record"] = schema
    calls = install_host(variant, runner, monkeypatch, tools=({"name": "update_record", "inputSchema": schema},))
    answer = asyncio.run(runner._wrapper_mcp_lifecycle_recovery(prompt, "", None))
    invocation = next(args for name, args in calls if name == "call_project_mcp_tool")
    assert invocation["arguments"] == {"record_id": "verified-id", "value": 11}
    assert "verified MCP result" in answer
    assert asyncio.run(runner._wrapper_mcp_lifecycle_recovery(prompt, "", None)) is None


def test_steering_is_accepted_during_wrapper_preflight(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "fixture_mcp.py").write_text("from mcp.server.fastmcp import FastMCP\nserver = FastMCP('fixture')\nserver.run(transport='stdio')\n")
    accepted = []
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

    def handler(name, arguments):
        if name == "list_project_mcp_tools":
            return {"tools": [{"name": "echo", "inputSchema": schema}], "count": 1}
        accepted.append(runner.submit_side_note("Also create note.txt containing exactly done."))
        return {"result": {"structuredContent": {"value": arguments["arguments"]["value"]}}}

    calls = install_host(variant, runner, monkeypatch, handler=handler)
    mro = type(runner).__mro__
    parent = mro[next(index for index, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]
    seen = []

    async def model_pass(self, prompt, callback=None):
        seen.append(prompt)
        return "The remaining creation was not completed."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    asyncio.run(runner._run_async("Call the MCP echo tool in fixture_mcp.py with value='verified'.", lambda *_args: None))
    assert accepted == [True]
    assert seen and "note.txt" in seen[0]
    assert all("verified MCP result" in prompt for prompt in seen)
    assert all('"verified_actions"' in prompt and '"mcp_tool_called"' in prompt for prompt in seen)
    assert all('"remaining_postconditions"' in prompt for prompt in seen)
    assert sum(name == "call_project_mcp_tool" for name, _arguments in calls) == 1
    assert any(item.get("content", "").find("/btw Also create note.txt") >= 0 for item in runner.history)


@pytest.mark.parametrize("outcome", ["verified", "partial", "cancelled"])
def test_completion_does_not_reuse_stale_wrapper_stop(variant, runner, tmp_path, monkeypatch, outcome):
    target = tmp_path / "report.txt"
    target.write_text("old\n")
    prompt = "Edit report.txt, replacing old with new. Do not run tests."
    install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    parent = mro[next(index for index, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]
    stale_stop = (
        "Stopped by the wrapper after repeated transactional recovery made no verified progress. "
        "No requested file change was committed."
    )

    async def no_preflight(*_args):
        return None

    for name in ("_named_file_operation_preflight", "_wrapper_verify_committed_transaction",
                 "_wrapper_docx_edit_recovery"):
        monkeypatch.setattr(runner, name, no_preflight)

    async def stale_recovery(*_args):
        return stale_stop if outcome == "verified" else None

    monkeypatch.setattr(runner, "_wrapper_transaction_proposal_recovery", stale_recovery)

    async def model_pass(self, _prompt, callback=None):
        if outcome != "partial":
            target.write_text("new\n")
            self._tool_permission_broker.record_result("apply_text_patch", {
                "path": str(target), "replacements": [{"old_text": "old\n", "new_text": "new\n"}],
                "expected_sha256": hashlib.sha256(b"old\n").hexdigest(),
            }, result({"path": str(target), "new_sha256": hashlib.sha256(b"new\n").hexdigest()}))
        if outcome == "cancelled":
            self._cancel_source = "user"
            self._cancel_partial_answer = "Stopped at the user's request; saved work was preserved."
            raise asyncio.CancelledError
        return stale_stop

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(prompt))
    if outcome == "verified":
        assert "[Completion status: verified]" in answer
        assert "Completed the requested task after verified wrapper recovery." in answer
        assert "No requested file change was committed" not in answer
        assert "made no verified progress" not in answer
    else:
        assert "[Completion status: verified]" not in answer
        assert "Completed the requested task" not in answer
    assert target.read_text() == ("old\n" if outcome == "partial" else "new\n")


def test_managed_service_uses_lifecycle_not_foreground_runner(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "managed_server.py").write_text("print('READY')\n")
    prompt = "Start managed_server.py as a managed service with ready_substring='READY', then stop the service."
    begin(runner, prompt)

    def handler(name, arguments):
        if name == "start_project_mcp_server":
            assert arguments["ready_substring"] == "READY"
            return {"server_id": "verified-server", "status": "ready"}
        assert name == "stop_project_mcp_server" and arguments["server_id"] == "verified-server"
        return {"stopped": True, "actual_wait": True}

    calls = install_host(variant, runner, monkeypatch, handler=handler)
    answer = asyncio.run(runner._wrapper_mcp_lifecycle_recovery(prompt, "", None))
    assert [name for name, _args in calls] == ["start_project_mcp_server", "stop_project_mcp_server"]
    assert "Stopped managed server" in answer


def test_evaluator_accepts_byte_verified_backup_after_rejected_patch(tmp_path):
    from evaluation.followup_matrix_cases import Turn, verify_turn, workspace_manifest

    target = tmp_path / "settings.json"
    target.write_text('{"limit":3}\n')
    before = workspace_manifest(tmp_path)
    backup = tmp_path / ".qubitz" / "backups" / "settings.json.001.bak"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(target.read_bytes())
    turn = Turn("Edit settings.json", (), ("settings.json",))
    assert verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0] == []
    backup.write_text("unrelated content\n")
    assert "unexpected_mutation:" in verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0][0]


def test_evaluator_uses_same_turn_cancellation_receipt_not_prose(tmp_path):
    from evaluation.followup_matrix_cases import Check, Turn, verify_turn

    turn = Turn("Cancel the active task", (Check("cancelled"),), cancel=True)
    observed = {"assistant": "Stopped at the user's request.",
                "intervention": {"accepted": True, "turn_id": "current"},
                "runtime": {"turn_control": {"turn_id": "current", "cancel_requested": True,
                                             "cancelled": True, "finished": True}}}
    assert verify_turn(tmp_path, turn, observed, {}, {})[0] == []
    observed["runtime"]["turn_control"]["turn_id"] = "old"
    assert "postcondition_failed:cancelled:" in verify_turn(tmp_path, turn, observed, {}, {})[0]
