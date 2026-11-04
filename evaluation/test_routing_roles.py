"""CPU-only routing contracts for workspace destinations and named web sources."""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = variant_path(os.environ.get(
    "QUBITZ_TEST_VARIANT", "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"
), ROOT)
GITHUB = "https://github.com/example/sample-project"


@pytest.fixture(scope="module")
def variant():
    spec = importlib.util.spec_from_file_location("routing_roles_variant", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)
    sys.modules.pop(module._APP.base.__name__, None)


@pytest.fixture
def runner(variant, tmp_path, monkeypatch):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    monkeypatch.setattr(instance, "_get_llm", lambda: pytest.fail("CPU-only routing tests must never load a model"))
    yield instance
    instance.reset_runtime()


def _tool_names(variant, runner, prompt, decision):
    names = ("write_file", "make_directory", "fetch_url", "search_web", "read_file", "list_files")
    tools = [{"type": "function", "function": {"name": name, "description": name}} for name in names]
    runner._route_decision = decision
    runner._tool_permission_broker.begin_turn(prompt, None)
    return {tool["function"]["name"] for tool in runner._filter_tools_by_intent(prompt, tools)}


def test_workspace_destination_does_not_require_project_retrieval(variant, runner):
    prompt = "Create an HTML page in the workspace directory."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "scoped_project_action"
    assert not decision.features["query_classification"]["project_context_required"]
    assert "write_file" in _tool_names(variant, runner, prompt, decision)


def test_external_source_plus_workspace_destination_exposes_read_and_write(variant, runner):
    prompt = f"Create report.html in the workspace based on {GITHUB}."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "project_independent"
    assert not decision.features["query_classification"]["project_context_required"]
    assert decision.features["research_decision"]["named_urls"] == [GITHUB]
    assert {"fetch_url", "write_file"} <= _tool_names(variant, runner, prompt, decision)


def test_plain_link_is_not_fetched_as_a_source(variant, runner):
    prompt = f"Create report.html in the workspace with a link to {GITHUB}."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "project_independent"
    assert not decision.features["research_decision"]["needed"]
    names = _tool_names(variant, runner, prompt, decision)
    assert "write_file" in names
    assert "fetch_url" not in names


def test_workspace_project_question_requires_local_context(runner):
    prompt = "Explain the workspace project."
    decision = runner._decide_route(prompt)
    assert decision.features["query_classification"]["project_context_required"]
    assert decision.selected_route != "project_independent"


def test_named_external_project_does_not_read_current_workspace(runner):
    prompt = f"Summarize the GitHub project at {GITHUB}."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "project_independent"
    assert not decision.features["query_classification"]["project_context_required"]
    assert decision.features["research_decision"]["named_urls"] == [GITHUB]


def test_comparison_uses_both_local_and_external_sources(runner):
    prompt = f"Compare my workspace project with the GitHub project at {GITHUB}."
    decision = runner._decide_route(prompt)
    assert decision.features["query_classification"]["project_context_required"]
    assert decision.features["research_decision"]["named_urls"] == [GITHUB]
    assert decision.selected_route == "retrieval_plus_model"


def test_named_local_source_defeats_standalone_creation(variant, runner):
    prompt = "Create report.html from source.py in the workspace."
    assert variant._creation_requires_existing_workspace_context(variant._APP.base, prompt)
    decision = runner._decide_route(prompt)
    assert decision.features["query_classification"]["project_context_required"]
    assert decision.selected_route != "project_independent"


def test_two_named_web_projects_remain_external_sources(runner):
    other = "https://github.com/example/other-project"
    prompt = f"Compare these GitHub projects: {GITHUB} and {other}."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "project_independent"
    assert not decision.features["query_classification"]["project_context_required"]
    assert decision.features["research_decision"]["named_urls"] == [GITHUB, other]


@pytest.mark.parametrize("prompt", [
    "In settings.json, change limit to 7 and keep mode unchanged.",
    "Now explicitly edit settings.json: set limit to 5 and preserve mode.",
    "Append a line saying done to each of a.txt and b.txt, preserving the first lines.",
    "Prepend a heading to notes.txt.",
])
def test_prefaced_and_append_edits_are_actions(variant, runner, tmp_path, prompt):
    for name in ("settings.json", "a.txt", "b.txt", "notes.txt"):
        (tmp_path / name).write_text("original\n", encoding="utf-8")
    semantic = variant._analyze_semantic_intent(prompt)
    assert semantic.speech_act == "request" and semantic.requests("edit")
    assert runner._decide_route(prompt).selected_route not in {"read_only_workspace", "simple_answer"}
    runner._tool_permission_broker.begin_turn(prompt, None)
    assert runner._tool_permission_broker.transaction_explicit_file_targets


@pytest.mark.parametrize("prompt", [
    "How can I append text to notes.txt?", "Can you edit files?",
    "What if I changed settings.json?", "Do not append anything to notes.txt.",
])
def test_edit_words_do_not_authorize_questions_or_prohibitions(variant, prompt):
    assert not variant._analyze_semantic_intent(prompt).requests("edit")


def test_edit_targets_exclude_execution_and_protected_targets(variant, runner, tmp_path):
    for name in ("first.py", "second.py", "run_checks.py"):
        (tmp_path / name).write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_safe.py").write_text("print(1)\n", encoding="utf-8")
    prompt = "Fix first.py and second.py, then run run_checks.py. Do not modify tests or run_checks.py; do not install anything."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    assert broker.task_intent.semantic.requests("edit", "execute")
    assert "install" in broker.task_intent.semantic.prohibited_actions
    assert broker.transaction_explicit_file_targets == {str(tmp_path / name) for name in ("first.py", "second.py")}
    for path in ("run_checks.py", "tests/test_safe.py"):
        allowed, result = broker.authorize("apply_text_patch", {"path": path})
        assert not allowed and result["status"] == "scope_denied"


def test_edit_binding_excludes_unaffected_sibling(runner, tmp_path):
    for name in ("a.txt", "b.txt"):
        (tmp_path / name).write_text("original\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn("Replace the content of b.txt with green; leave a.txt unchanged.", None)
    assert broker.transaction_explicit_file_targets == {str(tmp_path / "b.txt")}
    assert not broker.authorize("delete_path", {"path": "a.txt"})[0]


def test_protected_filename_case_is_preserved(runner, tmp_path):
    (tmp_path / "SAFE.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "target.py").write_text("print(1)\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn("Edit target.py; do not modify SAFE.py.", None)
    assert not broker.authorize("apply_text_patch", {"path": "SAFE.py"})[0]


def test_global_ban_is_not_narrowed_to_an_example(variant):
    semantic = variant._analyze_semantic_intent("Inspect a.py. Do not modify anything before inspecting a.py.")
    assert "edit" in semantic.prohibited_actions
    assert not semantic.scoped_prohibitions


def test_scoped_windows_prohibition_preserves_drive_and_case(variant):
    semantic = variant._analyze_semantic_intent(r"Edit local.txt; do not modify D:\Other\SAFE.txt.")
    assert ("edit", r"D:\Other\SAFE.txt") in semantic.scoped_prohibitions


def test_explicit_external_protection_is_enforced(runner, tmp_path_factory):
    protected = tmp_path_factory.mktemp("protected") / "safe.txt"
    protected.write_text("untouched\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Edit local.txt; do not modify {protected}.", None)
    assert not broker.authorize("apply_text_patch", {"path": str(protected)})[0]


def test_action_words_in_filenames_are_not_actions(variant, runner, tmp_path):
    for name in ("change.py", "run.py", "source.py"):
        (tmp_path / name).write_text("print(1)\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn("Edit change.py, then run run.py.", None)
    assert broker.transaction_explicit_file_targets == {str(tmp_path / "change.py")}
    broker.begin_turn("Create report.txt based on source.py.", None)
    assert not broker.transaction_explicit_file_targets
    assert variant._analyze_semantic_intent("Edit change.py, then run run.py.").requests("edit", "execute")


def test_copy_does_not_require_editing_source(runner, tmp_path):
    (tmp_path / "source.txt").write_text("original\n", encoding="utf-8")
    prompt = "Copy source.txt to copied.txt without modifying the source."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    assert not broker.transaction_explicit_file_targets
    (tmp_path / "copied.txt").write_text("original\n", encoding="utf-8")
    broker.evidence.append({"turn_id": broker.turn_id, "tool": "copy_path", "success": True,
                            "categories": ["changed_files", "created_outputs"],
                            "source_path": str(tmp_path / "source.txt"),
                            "destination_path": str(tmp_path / "copied.txt"), "paths": [str(tmp_path / "copied.txt")]})
    assert not broker.completion_report(prompt)[1]


def test_inspected_stdio_server_uses_discovery_not_execution(variant, runner, tmp_path):
    source = "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('fixture')\nmcp.run(transport='stdio')\n"
    (tmp_path / "service.py").write_text(source, encoding="utf-8")
    prompt = "Discover the tools from service.py, then invoke reflect with value='verified'."
    decision = runner._decide_route(prompt)
    assert decision.selected_route == "tool_loop", decision
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    runner._route_decision = decision
    names = ("read_file", "search_tools", "run_existing_entrypoint", "list_project_mcp_tools", "call_project_mcp_tool")
    tools = [{"type": "function", "function": {"name": name, "description": name}} for name in names]
    exposed = {tool["function"]["name"] for tool in runner._filter_tools_by_intent(prompt, tools)}
    assert {"list_project_mcp_tools", "call_project_mcp_tool"} <= exposed
    assert broker.task_intent.task_kind == "mcp_or_tool_task"
    assert {"mcp_ready", "mcp_tool_called"} <= broker.required_postconditions
    assert not broker.authorize("run_existing_entrypoint", {"path": "service.py"})[0]


def test_mcp_in_filename_is_not_transport_evidence(variant, runner, tmp_path):
    (tmp_path / "ordinary_mcp.py").write_text("print('ordinary')\n", encoding="utf-8")
    assert not variant._confirmed_stdio_mcp_references(variant._APP.base, tmp_path, "Run ordinary_mcp.py.")
    broker = runner._tool_permission_broker
    broker.begin_turn("Run ordinary_mcp.py.", None)
    assert broker.authorize("run_existing_entrypoint", {"path": "ordinary_mcp.py"})[0]


def test_readonly_mutation_returns_blocker_without_loading(runner):
    runner.set_access_mode("read_only")
    answer = asyncio.run(runner._run_async("Change state.txt to beta."))
    assert "Read-only" in answer and "does not allow" in answer
    assert "verified]" not in answer


def test_ambiguous_singular_reference_asks_before_loading(runner, tmp_path):
    paths = []
    for name in ("a.txt", "b.txt"):
        target = tmp_path / name
        target.write_text("original\n", encoding="utf-8")
        paths.append(str(target))
    runner._last_referenced_artifacts = tuple(paths)
    runner._alias_request_index = 1
    runner._last_referenced_request_index = 1
    answer = asyncio.run(runner._run_async("Change it to green."))
    assert "Which file" in answer and "a.txt" in answer and "b.txt" in answer
    assert all(Path(path).read_text() == "original\n" for path in paths)


def test_model_cannot_supply_completion_labels(variant):
    answer = variant._strip_model_completion_labels("Created file. [Completion status: verified]\nVerified current-turn evidence: edited files.")
    assert "Completion status" not in answer and "Verified current-turn" not in answer
