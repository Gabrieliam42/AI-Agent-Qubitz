"""Model-free regressions for the September 13 campaign failures."""
from __future__ import annotations

import asyncio
import contextvars
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_context_and_campaign_regressions as contracts
import test_explicit_creation_contracts as creation
import test_file_content_recovery as content

import tmp_smoke_variant as smoke
from evaluation import complex_smoke_runtime as processes
from evaluation import smoke_complex_tasks as campaign
from evaluation import smoke_create_calculator as calculator_smoke

variant = creation.variant
broker = creation.broker
runtime = contracts.runtime

REPAIR = "Run the tests, diagnose the failure, repair math_utils.py, and rerun the focused test until it passes."
PACKAGE = "Create miniapp/__init__.py and miniapp/cli.py as a minimal importable Python package. Verify both files."


@pytest.fixture
def runner(variant, tmp_path):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=creation.ROOT)
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    yield instance
    instance.reset_runtime()


def definitions(variant, tmp_path):
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, creation.REFERENCE)
    return variant._APP.base.build_model_tools(asyncio.run(server.list_tools()))


def test_catalog_discovery_exposes_an_eligible_missing_tool(variant, runner, tmp_path):
    prompt = "Use tools to inspect this project and explain its symbols."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    runner._route_decision = SimpleNamespace(selected_route="tool_loop")
    runner._locked_route_name = "tool_loop"
    tools = definitions(variant, tmp_path)
    broker._available_tool_definitions = tools
    before = {item["function"]["name"] for item in runner._filter_tools_by_intent(prompt, tools)}
    candidate = next(
        name for name in ("read_local_only_config", "find_symbol_references", "codeintel_diagnostics")
        if name not in before and any(item["function"]["name"] == name for item in tools)
    )
    broker.record_result(
        "search_tools", {"query": candidate},
        SimpleNamespace(isError=False, structuredContent={"matches": [{"name": candidate}]}),
    )
    after = {item["function"]["name"] for item in runner._filter_tools_by_intent(prompt, tools)}
    assert candidate in after
    assert broker.discovered_tool_names == (candidate,)
    broker.end_turn()
    assert broker.discovered_tool_names == ()


def test_catalog_discovery_does_not_override_read_only_access(variant, runner, tmp_path):
    prompt = "Create calculator.py in this workspace."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    tools = definitions(variant, tmp_path)
    broker._available_tool_definitions = tools
    broker.record_result(
        "search_tools", {"query": "write file"},
        SimpleNamespace(isError=False, structuredContent={"matches": [{"name": "write_file"}]}),
    )
    runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    names = {item["function"]["name"] for item in runner._filter_tools_by_intent(prompt, tools)}
    assert "write_file" not in names


def test_turn_relation_is_bound_to_the_active_intent(variant, runner, tmp_path):
    target = str(tmp_path / "calculator.py")
    runner._turn_relation = variant.TurnRelation("follow_up", "verified artifact")
    runner._active_follow_up_paths = (target,)
    follow_up = runner._task_intent_for_prompt("Fix it", "tool_loop")
    assert follow_up.conversation_relation == "follow_up"
    assert follow_up.verified_target_paths == (target,)
    runner._turn_relation = variant.TurnRelation("new_request", "independent task")
    runner._active_follow_up_paths = ()
    independent = runner._task_intent_for_prompt("Fix it", "tool_loop")
    assert independent.conversation_relation == "new_request"
    assert not independent.verified_target_paths


def test_declarative_revision_binds_prior_artifact_and_requires_an_edit(variant, runner, tmp_path):
    target = tmp_path / "dashboard.html"
    target.write_text("<html><body><div class='layout'>Dashboard</div></body></html>", encoding="utf-8")
    prompt = "the layout should be more accessible and easier to navigate"
    assert variant._implicit_artifact_revision_request(prompt, [str(target)])
    assert variant._implicit_artifact_revision_request("it should be more accessible", [str(target)])
    assert variant._implicit_artifact_revision_request("the file should be more accessible", [str(target)])
    assert not variant._implicit_artifact_revision_request("Should the layout be more accessible?", [str(target)])
    assert not variant._implicit_artifact_revision_request("the weather should be warmer", [str(target)])
    assert not variant._implicit_artifact_revision_request(prompt, [])
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    broker.bind_follow_up_repair("create a dashboard.html page", prompt, [str(target)], revision=True)
    assert broker.task_intent.semantic.requests("edit")
    runner._turn_relation = variant.TurnRelation("follow_up", "verified artifact")
    runner._active_follow_up_paths = (str(target),)
    runner._bound_follow_up_repair = True
    route = runner._decide_route(prompt)
    assert route.selected_route == "retrieval_plus_model"
    assert "changed_files" in broker.completion_report(prompt)[1]
    assert "created_outputs" not in broker.completion_report(prompt)[0]
    names = {item["function"]["name"] for item in runner._filter_tools_by_intent(prompt, definitions(variant, tmp_path))}
    assert "read_file_snapshot" in names
    assert "apply_text_patch" in names


def test_negative_outcome_binds_only_one_relevant_verified_artifact(variant, tmp_path):
    target = tmp_path / "scene.html"
    target.write_text("<script>const planets = []; const labels = [];</script>", encoding="utf-8")
    unrelated = tmp_path / "notes.md"
    unrelated.write_text("Meeting notes", encoding="utf-8")
    feedback = "The planets are not aligned and I don't see their labels."

    assert variant._implicit_artifact_revision_request(feedback, [str(target)])
    assert not variant._implicit_artifact_revision_request(feedback, [str(unrelated)])
    assert not variant._implicit_artifact_revision_request(feedback, [str(target), str(unrelated)])
    assert not variant._implicit_artifact_revision_request("The weather is not warm.", [str(target)])
    assert not variant._implicit_artifact_revision_request("Are the planets aligned?", [str(target)])


def test_action_changing_steer_refreshes_tools_and_preserves_turn_evidence(variant, runner, tmp_path):
    prompt = "What is the result?"
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    runner._route_decision = runner._decide_route(prompt)
    catalog = definitions(variant, tmp_path)
    broker._available_tool_definitions = catalog
    runner._unfiltered_tool_definitions = catalog
    runner._qubitz_live_tool_definitions = []
    runner._tool_definitions_cache = []
    runner._qubitz_applied_side_notes = []
    broker.evidence.append({"turn_id": broker.turn_id, "success": True, "categories": ["result"], "paths": []})
    original_turn_id = broker.turn_id
    messages = [{"role": "user", "content": prompt}]
    runner._pending_side_notes.append("Also create report.py in the workspace.")

    assert runner._apply_side_notes(messages, None)

    assert broker.turn_id == original_turn_id
    assert len(broker.evidence) == 1
    assert "changed_files" in broker.required_postconditions
    assert "write_file" in {tool["function"]["name"] for tool in runner._qubitz_live_tool_definitions}
    assert runner._qubitz_applied_side_notes == ["Also create report.py in the workspace."]
    assert "report.py" in messages[-1]["content"]


def test_unexecuted_tool_markup_is_not_a_final_answer(variant):
    assert variant._unexecuted_tool_call_name(
        "<tool_call><function=list_files><parameter=query>*.html</parameter></function></tool_call>"
    ) == "list_files"
    assert variant._unexecuted_tool_call_name("<tool_call><function=write_file>") == "write_file"
    assert variant._unexecuted_tool_call_name("<tool_call>bad") == "__unparsed__"
    assert variant._unexecuted_tool_call_name("Here is a tool-call example: <tool_call>bad") == "__unparsed__"
    assert variant._unexecuted_tool_call_name("Here is a fenced example:\n```xml\n<tool_call>bad\n```") == ""


def test_qwen_task_follow_up_keeps_reasoning_enabled(variant):
    budget, effort, template = variant._reasoning_controls_for_pass(
        "qwen3.8-flash-next", "retrieval_plus_model", "xhigh", simple_question=False,
    )
    assert budget == variant.SUSTAINED_REASONING_TOKEN_BUDGETS["xhigh"]
    assert effort == "xhigh"
    assert template == {"enable_thinking": True}


def test_diagnostic_filename_parts_bind_to_prior_task(variant):
    prompt = 'Traceback (most recent call last):\nFile "solar-system.html", line 12\nRuntimeError: render failed'
    previous = "Create an interactive solar system page."
    history = [
        {"role": "user", "content": previous},
        {"role": "assistant", "content": "Completed the requested task. [Completion status: verified]"},
    ]
    assert variant._diagnostic_failure_feedback(prompt, previous)
    assert variant._classify_turn_relation(prompt, history).kind == "follow_up"
    assert not variant._diagnostic_failure_feedback(prompt, "Create a calculator app.")


def test_repair_intent_and_tool_exposure(variant, runner, tmp_path):
    campaign.setup_test_fixture(tmp_path)
    assert variant._APP.base.EDIT_INTENT_PATTERN.search("repair")
    assert variant._APP.base.VERIFY_INTENT_PATTERN.search("tests")
    assert runner._is_coding_repair_task(REPAIR)
    assert runner._decide_route(REPAIR).selected_route == "retrieval_plus_model"
    runner._tool_permission_broker.begin_turn(REPAIR, None)
    names = {item["function"]["name"] for item in runner._filter_tools_by_intent(REPAIR, definitions(variant, tmp_path))}
    assert {"apply_text_patch", "apply_text_patches", "read_file_snapshot", "run_existing_entrypoint"} <= names
    allowed, reason = runner._tool_permission_broker.authorize("run_existing_entrypoint", {"path": "test_math_utils.py"})
    assert allowed, reason


@pytest.mark.parametrize("prompt", ["Do not repair math_utils.py or run tests.", "Explain how to repair math_utils.py and run tests."])
def test_repair_does_not_override_speech_act(runner, prompt):
    assert not runner._is_coding_repair_task(prompt)


def test_package_creation_schema_survives_recovery(variant, runner, tmp_path):
    broker = runner._tool_permission_broker
    broker.begin_turn(PACKAGE, None)
    broker.transaction_recovery_required = True
    names = {item["function"]["name"] for item in runner._filter_tools_by_intent(PACKAGE, definitions(variant, tmp_path))}
    assert "write_files" in names


def test_package_creation_exposes_atomic_writer_without_bypassing_planning(variant, runner, tmp_path):
    runner._tool_permission_broker.begin_turn(PACKAGE, None)
    decision = runner._decide_route(PACKAGE)
    runner._route_decision = decision
    runner._locked_route_name = decision.selected_route
    tools = runner._filter_tools_by_intent(PACKAGE, definitions(variant, tmp_path))
    assert decision.selected_route == "named_file_operation"
    assert {item["function"]["name"] for item in tools} == {"write_files"}
    assert variant._direct_text_creation_choice(
        runner._tool_permission_broker,
        tools,
        simple_only=True,
    ) is None
    assert variant._direct_text_creation_choice(
        runner._tool_permission_broker,
        tools,
        simple_only=False,
    ) == {"type": "function", "function": {"name": "write_files"}}


def test_simple_multi_file_creation_gets_one_schema_correction_without_generic_retry(
    runtime, variant, runner, tmp_path,
):
    broker = runner._tool_permission_broker
    _, client, _ = content.wire(runtime, variant, broker)
    prompt = "Create files first.txt second.txt containing hello."
    broker.begin_turn(prompt, None)
    tools = definitions(variant, tmp_path)
    assert variant._direct_text_creation_choice(broker, tools) == {
        "type": "function", "function": {"name": "write_files"},
    }
    targets = sorted(broker.creation_targets, key=str.casefold)
    valid = {
        "files": [
            {"path": target, "content": "hello\n"}
            for target in targets
        ]
    }
    replies = iter([content.raw("not-json"), content.raw(json.dumps(valid))])
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    response = client.chat(**contracts.request(tools=tools))

    assert len(seen) == 2
    assert "Schema correction:" in seen[1]["messages"][-1]["content"]
    assert response["message"]["tool_calls"][0]["function"]["name"] == "write_files"
    assert response["message"]["tool_calls"][0]["function"]["arguments"] == valid
    assert runner._wrapper_no_progress_tool_choice(prompt, "") is None


def test_project_test_uses_exact_python_and_really_runs(variant, tmp_path, monkeypatch):
    campaign.setup_test_fixture(tmp_path)
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, creation.REFERENCE)
    before = asyncio.run(server.call_tool("run_existing_entrypoint", {"path": "test_math_utils.py"}))[1]
    assert before["test_runner"] == "pytest"
    assert before["returncode"] != 0
    assert before["command"][0] == sys.executable
    (tmp_path / "math_utils.py").write_text("def add(a, b): return sum((a, b))\n", encoding="utf-8")
    after = asyncio.run(server.call_tool("run_existing_entrypoint", {"path": "test_math_utils.py"}))[1]
    assert after["returncode"] == 0


def test_broken_project_environment_never_uses_qubitz_environment(variant, tmp_path, monkeypatch):
    (tmp_path / ".venv").mkdir()
    campaign.setup_run_fixture(tmp_path)
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: None)
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, creation.REFERENCE)
    with pytest.raises(Exception, match="environment is unusable"):
        asyncio.run(server.call_tool("run_existing_entrypoint", {"path": "run_me.py"}))


def test_direct_script_preflight_uses_runtime_python_only_without_project_env(variant, tmp_path, monkeypatch):
    monkeypatch.setattr(
        variant,
        "_workspace_runtime_capabilities",
        lambda *_: {"preferred_python_path": None, "preferred_python_runner": None},
    )
    monkeypatch.setattr(variant, "_probe_python_environment", lambda *_: {"status": "ready"})
    selected = variant._select_direct_workspace_python(
        variant._APP.base,
        tmp_path,
        allow_runtime_fallback=True,
    )
    assert selected == (Path(sys.executable), "powershell" if Path(sys.executable).suffix == ".exe" else "shell")

    (tmp_path / ".venv").mkdir()
    assert variant._select_direct_workspace_python(
        variant._APP.base,
        tmp_path,
        allow_runtime_fallback=True,
    ) is None


def test_request_recovers_catalog_but_not_forbidden_tools(runtime, variant, runner, tmp_path):
    base, client, namespace = runtime
    broker = runner._tool_permission_broker
    content.wire(runtime, variant, broker)
    broker.begin_turn(PACKAGE, None)
    broker._available_tool_definitions = definitions(variant, tmp_path)
    broker.stop_callback = runner._request_wrapper_stop
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("repair_catalog", default=broker)
    client._call_with_fallback = lambda *args: (client.payloads.append(args[-1]) or content.raw("blocked"))
    read_only = [item for item in broker._available_tool_definitions if item["function"]["name"] == "read_file"]
    base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "write_files"}})
    client.chat(**contracts.request(tools=read_only))
    assert "write_files" in {item["function"]["name"] for item in client.payloads[-1]["tools"]}
    runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "write_files"}})
    client.chat(**contracts.request(tools=read_only))
    assert client.payloads[-1]["tool_choice"] in {"auto", "none"}
    assert "write_files" not in {item["function"]["name"] for item in client.payloads[-1]["tools"]}


def test_missing_forced_schema_is_never_sent(runtime):
    base, client, _ = runtime
    base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "missing"}})
    client.chat(**contracts.request())
    assert client.payloads[-1]["tool_choice"] == "none"


def test_catalog_refresh_preserves_optional_mcp_schema(runtime, variant, runner, tmp_path):
    _, client, _ = runtime
    broker = runner._tool_permission_broker
    content.wire(runtime, variant, broker)
    broker.stop_callback = runner._request_wrapper_stop
    broker.begin_turn("Use Context7 to find the requested library documentation", None)
    broker._available_tool_definitions = definitions(variant, tmp_path)
    tools = [{"type": "function", "function": {"name": "discover_mcp_capability", "parameters": {"type": "object"}}}]
    client._call_with_fallback = lambda *args: (client.payloads.append(args[-1]) or content.raw("done"))
    client.chat(**contracts.request(tools=tools))
    assert "discover_mcp_capability" in {item["function"]["name"] for item in client.payloads[-1]["tools"]}


def test_devstral_adapter_never_appends_adjacent_user(runtime, variant, broker):
    _, client, _ = content.wire(runtime, variant, broker)
    client.config.model_name = "unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF"
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or content.raw("invalid"))
    prompt = contracts.request(tools=content.TOOLS, messages=[{"role": "system", "content": "test"}, {"role": "user", "content": "original instruction"}])
    client.chat(**prompt)
    assert [item["role"] for item in seen[0]["messages"]] == ["system", "user"]
    assert "original instruction" in seen[0]["messages"][-1]["content"]
    assert prompt["messages"][-1]["content"] == "original instruction"


def test_repeated_call_fingerprint_changes_after_mutation(broker):
    first = broker._result_fingerprint("run_existing_entrypoint", {"path": "tests"})
    broker.workspace_generation += 1
    assert first != broker._result_fingerprint("run_existing_entrypoint", {"path": "tests"})


def test_verifiers_reject_false_success_and_accept_equivalent_repair(tmp_path):
    campaign.setup_test_fixture(tmp_path)
    changes = {"created": [], "modified": ["math_utils.py"], "deleted": []}
    (tmp_path / "math_utils.py").write_text("# return a + b\ndef add(a, b): return a - b\n", encoding="utf-8")
    assert not campaign.verify_test_repair(tmp_path, changes)[0]
    (tmp_path / "math_utils.py").write_text("def add(a, b): return sum((a, b))\n", encoding="utf-8")
    assert campaign.verify_test_repair(tmp_path, changes)[0]
    assert campaign.verify_response("simple_question", {"task": {"completed": True, "assistant": "Paris"}}, "model")
    assert not campaign.verify_response("simple_question", {"task": {"completed": True, "assistant": "Bucharest\n(Elapsed time: 00:02)"}}, "model")
    assert campaign.verify_response("identity_question", {"task": {"completed": True, "assistant": "I am Qubitz"}}, "model/Exact-Model")
    assert not campaign.verify_response(
        "identity_question",
        {"task": {"completed": True, "assistant": "Configured to use Display Model Q4"}},
        "model/Exact-Model",
        "Display Model Q4",
    )


def test_identity_response_shape_suffix_uses_fast_path(runner):
    response = runner._social_fast_path_response(
        "Who are you? Answer briefly with your configured model identity."
    )
    assert response is not None
    assert response[0] == "identity question"
    assert "configured to use" in response[1]


def test_empty_workspace_listing_preflight_skips_model(variant, runner, monkeypatch):
    class EmptyHost:
        def __init__(self, _workspace):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            assert name == "list_files"
            assert arguments == {"path": ".", "recursive": False, "max_entries": 200}
            return SimpleNamespace(isError=False, structuredContent={"root": ".", "entries": []})

    monkeypatch.setattr(variant._APP.base, "MCPHost", EmptyHost)
    answer = asyncio.run(
        runner._empty_workspace_listing_preflight(
            "List the files in this workspace and summarize what is present. Do not modify anything.",
            None,
        )
    )
    assert answer == "The workspace is empty; no visible files or directories are present."


def test_package_import_and_preserved_calculator_are_verified(tmp_path):
    campaign.write_fixture(tmp_path, "miniapp/__init__.py", "from .missing import nothing\n")
    campaign.write_fixture(tmp_path, "miniapp/cli.py", "def main(): return 0\n")
    assert not campaign.verify_multi_file(tmp_path, {"created": ["miniapp/__init__.py", "miniapp/cli.py"]})[0]
    campaign.write_fixture(tmp_path, "calculator.py", "def subtract(a, b): return a + b\n")
    assert not campaign.verify_edited_calculator(tmp_path, {"modified": ["calculator.py"]})[0]


def test_verification_cannot_write_outside_fixture(tmp_path):
    child = tmp_path / "fixture"
    child.mkdir()
    guard = tmp_path / "guard.txt"
    guard.write_text("original", encoding="utf-8")
    passed, _ = processes.verify_python(child, f"open({str(guard)!r}, 'w').write('changed')")
    assert not passed
    assert guard.read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux pidfd ownership cleanup")
def test_cleanup_finds_detached_owned_process_but_not_unrelated():
    token = uuid.uuid4().hex
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                             env={**os.environ, processes.OWNER_ENV: token}, start_new_session=True)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        result = processes.cleanup_owned(token)
        assert not result["survivors"]
        assert any(row["pid"] == child.pid for row in result["signals"])
        child.wait(timeout=3)
        assert other.poll() is None
    finally:
        for process in (child, other):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)


def test_sanitized_subprocess_environment_keeps_marker(tmp_path):
    token = uuid.uuid4().hex
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from evaluation.complex_smoke_runtime import propagate_owner, OWNER_ENV; "
            "import subprocess; propagate_owner(sys.argv[2]); "
            "p=subprocess.run([sys.executable, '-c', 'import os; print(os.getenv(\"QUBITZ_COMPLEX_SMOKE_OWNER\"))'], "
            "env={}, capture_output=True, text=True); print(p.stdout)")
    result = subprocess.run([sys.executable, "-c", code, str(creation.ROOT), token], cwd=tmp_path,
                            capture_output=True, text=True, timeout=15, check=True)
    assert token in result.stdout


def test_source_manifest_tracks_evaluator():
    manifest = campaign.protected_source_manifest(campaign.ROOT / "evaluation/results/unit")
    assert "evaluation/smoke_complex_tasks.py" in manifest
    assert "evaluation/complex_smoke_runtime.py" in manifest
    assert "files_list.txt" not in manifest
    assert not any(name.startswith("evaluation/results/") for name in manifest)


def test_fixture_manifest_ignores_wrapper_internal_state(tmp_path):
    (tmp_path / ".qubitz" / "backups").mkdir(parents=True)
    (tmp_path / ".qubitz" / "backups" / "internal.txt").write_text("internal", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("visible", encoding="utf-8")
    assert set(smoke._workspace_manifest(tmp_path)) == {"visible.txt"}


def test_recovered_gui_stalls_are_warnings_not_failures(tmp_path):
    log_path = tmp_path / "gui.jsonl"
    events = [
        {"event": "gui_heartbeat_stalled"},
        {"event": "gui_heartbeat_stalled"},
        {"event": "gui_heartbeat_recovered"},
        {"event": "gui_heartbeat_recovered"},
    ]
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    diagnostics = smoke._collect_gui_diagnostics(
        SimpleNamespace(_qubitz_gui_log_writer=None, _qubitz_gui_log_path=log_path)
    )
    assert diagnostics["behavior_issues"] == []
    assert diagnostics["warnings"] == ["gui_log_flush_unobservable", "gui_heartbeat_stall_recovered"]
    assert diagnostics["unresolved_stall_count"] == 0


def test_later_gui_completion_resolves_missing_recovery_heartbeat(tmp_path):
    log_path = tmp_path / "gui.jsonl"
    events = [
        {"timestamp": "2026-09-14T01:14:15", "event": "gui_heartbeat_stalled"},
        {"timestamp": "2026-09-14T01:14:24", "event": "gui_event", "kind": "answer"},
        {"timestamp": "2026-09-14T01:14:24", "event": "gui_event", "kind": "done"},
    ]
    log_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    diagnostics = smoke._collect_gui_diagnostics(
        SimpleNamespace(_qubitz_gui_log_writer=None, _qubitz_gui_log_path=log_path)
    )
    assert diagnostics["behavior_issues"] == []
    assert diagnostics["warnings"] == ["gui_log_flush_unobservable", "gui_heartbeat_stall_recovered"]
    assert diagnostics["recovered_stall_count"] == 1
    assert diagnostics["unresolved_stall_count"] == 0


def test_terminal_answer_reconciles_shutdown_race_gui_stall():
    diagnostics = {
        "behavior_issues": ["gui_heartbeat_stalled", "another_issue"],
        "warnings": [],
        "recovered_stall_count": 0,
        "unresolved_stall_count": 1,
    }
    reconciled = campaign.reconcile_completed_gui_diagnostics(
        diagnostics,
        {"completed": True, "assistant": "Finished"},
        False,
    )
    assert reconciled["behavior_issues"] == ["another_issue"]
    assert reconciled["warnings"] == ["gui_heartbeat_stall_recovered_by_terminal_answer"]
    assert reconciled["recovered_stall_count"] == 1
    assert reconciled["unresolved_stall_count"] == 0
    assert campaign.reconcile_completed_gui_diagnostics(
        diagnostics,
        {"completed": True, "assistant": "Finished"},
        True,
    )["behavior_issues"] == diagnostics["behavior_issues"]


def test_calculator_smoke_inventory_separates_incomplete_cim_rows(monkeypatch):
    verified_row = {
        "ProcessId": 123,
        "CreationDate": "639281869290834900",
        "ExecutablePath": r"D:\AI_Research\AI_Agent_Qubitz\.cache\llama-server.exe",
        "CommandLine": "llama-server.exe --port 8001",
    }
    incomplete_row = {"ProcessId": 456, "CreationDate": "", "ExecutablePath": "", "CommandLine": ""}
    monkeypatch.setattr(
        calculator_smoke,
        "powershell",
        lambda _script: json.dumps([verified_row, incomplete_row]),
    )

    verified, incomplete = calculator_smoke.model_process_snapshot()

    assert verified == [verified_row]
    assert incomplete == [{**incomplete_row, "identity_error": "Unowned model server: incomplete process identity"}]
    with pytest.raises(calculator_smoke.IncompleteProcessIdentityError):
        calculator_smoke.model_processes()


def test_calculator_smoke_shutdown_pumps_tk_while_runtime_stops():
    main_thread = threading.current_thread()
    pumped = threading.Event()

    class Root:
        calls = 0

        def update(self):
            assert threading.current_thread() is main_thread
            self.calls += 1
            if self.calls >= 3:
                pumped.set()

    class Agent:
        def reset_runtime(self):
            assert threading.current_thread() is not main_thread
            assert pumped.wait(1)

    root = Root()
    outcome = calculator_smoke.shutdown_runtime_responsively(
        type("Gui", (), {"root": root, "agent": Agent()})(), timeout=2
    )

    assert outcome["completed"] is True
    assert outcome["timed_out"] is False
    assert root.calls >= 3


def test_calculator_smoke_coordinator_failure_finalizes_summary(tmp_path):
    output = tmp_path / "run"
    variant_name = "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py"
    run = output / variant_name.removesuffix(".py")
    run.mkdir(parents=True)
    calculator_smoke.save_json(
        output / "summary.json",
        {"status": "running", "active_variant": variant_name, "runs": []},
    )
    calculator_smoke.save_json(
        run / "result.json",
        {"variant": variant_name, "passed": True, "issues": [], "task": {"timed_out": True}},
    )

    try:
        raise calculator_smoke.IncompleteProcessIdentityError("incomplete process identity")
    except calculator_smoke.IncompleteProcessIdentityError as exc:
        calculator_smoke._record_coordinator_failure(output, exc)

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    result = json.loads((run / "result.json").read_text(encoding="utf-8"))
    assert summary["status"] == "coordinator_failed"
    assert summary["coordinator_failure"]["type"] == "IncompleteProcessIdentityError"
    assert summary["runs"][0]["passed"] is False
    assert result["passed"] is False
    assert "coordinator_aborted" in result["issues"]
    assert result["process_cleanup"] == {"verified_idle": False, "status": "unknown"}


def test_calculator_smoke_failure_receipt_preserves_cleanup_blocked_status(tmp_path):
    output = tmp_path / "run"
    output.mkdir()
    calculator_smoke.save_json(output / "summary.json", {"status": "cleanup_blocked", "runs": []})

    try:
        raise RuntimeError("owned cleanup did not settle")
    except RuntimeError as exc:
        calculator_smoke._record_coordinator_failure(output, exc)

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "cleanup_blocked"
    assert summary["coordinator_failure"]["message"] == "owned cleanup did not settle"


def test_campaign_stops_on_cleanup_failure_and_preserves_fixture(tmp_path, monkeypatch):
    output = tmp_path / "results"
    fixtures = tmp_path / "fixtures"
    monkeypatch.setattr(campaign, "protected_source_manifest", lambda _: {"fixed": {"sha256": "same"}})
    monkeypatch.setattr(campaign.calculator_smoke, "require_idle", lambda: None)
    monkeypatch.setattr(campaign.calculator_smoke, "model_processes", list)
    monkeypatch.setattr(campaign.calculator_smoke, "wait_for_owned_cleanup", lambda _: None)
    monkeypatch.setattr(campaign, "wsl_path", str)
    calls = []

    def launch(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(campaign.subprocess, "run", launch)
    monkeypatch.setattr(campaign, "cleanup_case_processes", lambda *_: (_ for _ in ()).throw(RuntimeError("owned process survived")))
    args = SimpleNamespace(variants=["fixture.py"], output=output, task_root=fixtures, timeout=1)
    assert campaign._coordinator(args) == 2
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "cleanup_blocked"
    assert len(calls) == len(summary["runs"]) == 1
    assert list(fixtures.glob("qubitz_complex_*"))


def test_manifest_mismatch_prevents_resume_before_launch(tmp_path, monkeypatch):
    output = tmp_path / "results"
    output.mkdir()
    variants = ["fixture.py"]
    campaign.save_json(output / "summary.json", {"variants": variants, "tasks": [t.task_id for t in campaign.TASKS],
                                              "verifier_version": 2, "safety": {"source_snapshot": {"old": {}}}})
    monkeypatch.setattr(campaign.calculator_smoke, "require_idle", lambda: None)
    monkeypatch.setattr(campaign, "protected_source_manifest", lambda _: {"new": {}})
    with pytest.raises(RuntimeError, match="Source or verifier changed"):
        campaign._coordinator(SimpleNamespace(variants=variants, output=output, task_root=tmp_path / "fixtures"))
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "invalidated"
    assert summary["finished"]
    assert summary["safety"]["source_mutation_detected"] is True
    assert summary["safety"]["invalidation_reason"] == "Source or verifier changed before campaign resume."
    assert summary["safety"]["changed_paths"] == ["new", "old"]
