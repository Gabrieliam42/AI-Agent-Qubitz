"""CPU-only contracts for the user-facing qspace, qfile, and qfiles aliases."""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from evaluation.variant_test_selection import selected_variant_names
from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = selected_variant_names(("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py",), ROOT)


@pytest.fixture(scope="module", params=VARIANTS)
def variant(request):
    name = "context_alias_" + request.param.replace(".", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, variant_path(request.param, ROOT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)
    sys.modules.pop(module._APP.base.__name__, None)


@pytest.fixture
def runner(variant, tmp_path):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    yield instance
    instance.reset_runtime()


def test_qspace_destination_skips_existing_project_context(variant, runner):
    prompt, aliases, qfile, blocker = variant._resolve_user_context_aliases(
        "Create an HTML page in QSPACE.", runner.workspace, ()
    )
    assert aliases == {"qspace"}
    assert not qfile and not blocker
    assert "the workspace" in prompt
    decision = runner._decide_route(prompt)
    assert not decision.features["query_classification"]["project_context_required"]
    assert decision.selected_route == "scoped_project_action"


def test_qspace_project_is_a_local_source(variant, runner):
    prompt, aliases, _, blocker = variant._resolve_user_context_aliases(
        "Explain the QSPACE project.", runner.workspace, ()
    )
    assert aliases == {"qspace"} and not blocker
    decision = runner._decide_route(prompt)
    assert decision.features["query_classification"]["project_context_required"]
    assert decision.selected_route != "project_independent"


def test_qspace_path_resolves_to_current_workspace(variant, tmp_path):
    prompt, aliases, _, blocker = variant._resolve_user_context_aliases(
        "Read qspace/docs/guide.md.", tmp_path, ()
    )
    assert aliases == {"qspace"} and not blocker
    assert f"{tmp_path.as_posix()}/docs/guide.md" in prompt
    arguments = variant._expand_alias_tool_paths(
        "read_file", {"path": "qspace/docs/guide.md"}, tmp_path, "", aliases
    )
    assert Path(arguments["path"]) == tmp_path / "docs/guide.md"


def test_qfile_uses_only_verified_created_file(variant, runner, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("print(2 + 2)\n", encoding="utf-8")
    evidence = [{
        "success": True,
        "tool": "write_file",
        "categories": ["created_outputs", "changed_files"],
        "paths": [str(target)],
    }]
    created = variant._verified_created_alias_paths(evidence, tmp_path)
    assert created == (str(target),)
    prompt, aliases, qfile, blocker = variant._resolve_user_context_aliases(
        "Read QFILE and explain it.", tmp_path, created
    )
    assert aliases == {"qfile"} and not blocker and qfile == str(target)
    assert "`calculator.py`" in prompt
    decision = runner._decide_route(prompt)
    assert decision.features["query_classification"]["project_context_required"]
    assert Path(variant._expand_alias_tool_paths(
        "read_file", {"path": "qfile"}, tmp_path, qfile, aliases
    )["path"]) == target


def test_qfile_binds_verified_edit_and_explicit_discussion(variant, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("print(4)\n", encoding="utf-8")
    changed = variant._verified_recent_alias_paths(
        variant._APP.base,
        "Fix the calculator.",
        [{
            "success": True,
            "tool": "apply_text_patch",
            "categories": ["changed_files"],
            "paths": [str(target)],
        }],
        tmp_path,
    )
    discussed = variant._verified_recent_alias_paths(
        variant._APP.base, "Explain calculator.py.", [], tmp_path
    )
    assert changed == discussed == (str(target),)


def test_qfiles_binds_one_recent_verified_set(variant, tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    older = tmp_path / "older.py"
    for path in (first, second, older):
        path.write_text("pass\n", encoding="utf-8")
    recent = variant._verified_recent_alias_paths(
        variant._APP.base,
        "Create these files.",
        [{
            "success": True,
            "tool": "write_files",
            "categories": ["changed_files", "created_outputs"],
            "paths": [str(first), str(second)],
        }],
        tmp_path,
        (str(older),),
    )
    assert recent == (str(first), str(second))
    prompt, aliases, qfile, blocker = variant._resolve_user_context_aliases(
        "Compare QFILES.", tmp_path, recent
    )
    assert aliases == {"qfiles"} and not qfile and not blocker
    assert "`first.py`, `second.py`" in prompt
    expanded = variant._expand_alias_tool_paths(
        "read_file", {"paths": ["qfiles"]}, tmp_path, "", aliases, recent
    )
    assert expanded["paths"] == [str(first), str(second)]
    _, _, _, singular_blocker = variant._resolve_user_context_aliases(
        "Read qfile.", tmp_path, recent
    )
    assert "multiple files" in singular_blocker


def test_discussed_file_does_not_bind_external_or_unrelated_paths(variant, tmp_path):
    local = tmp_path / "local.py"
    local.write_text("pass\n", encoding="utf-8")
    assert not variant._verified_recent_alias_paths(
        variant._APP.base,
        "Use https://example.com/local.py as a reference.",
        [],
        tmp_path,
    )
    assert not variant._verified_recent_alias_paths(
        variant._APP.base, "Say hello.", [], tmp_path
    )


def test_qfile_does_not_guess_from_unverified_or_multiple_paths(variant, tmp_path):
    first = tmp_path / "first.py"
    second = tmp_path / "second.py"
    first.write_text("pass\n", encoding="utf-8")
    second.write_text("pass\n", encoding="utf-8")
    assert not variant._verified_created_alias_paths([{
        "success": False, "tool": "write_file", "categories": ["created_outputs"],
        "paths": [str(first)],
    }], tmp_path)
    _, _, _, missing = variant._resolve_user_context_aliases("read qfile", tmp_path, ())
    _, _, _, ambiguous = variant._resolve_user_context_aliases(
        "read qfile", tmp_path, (str(first), str(second))
    )
    assert "Please name the file" in missing
    assert "multiple files" in ambiguous
    first.unlink()
    _, _, _, deleted = variant._resolve_user_context_aliases(
        "read qfile", tmp_path, (str(first),)
    )
    assert "no longer" in deleted


def test_unbound_qfile_returns_before_model_loading(runner):
    answer = asyncio.run(runner._run_async("Read qfile."))
    assert "Please name the file" in answer
    assert getattr(runner, "_llm", None) is None


def test_unbound_qfiles_returns_before_model_loading(runner):
    answer = asyncio.run(runner._run_async("Compare qfiles."))
    assert "Please name the files" in answer
    assert getattr(runner, "_llm", None) is None


def test_alias_does_not_cross_an_unrelated_turn(runner, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("pass\n", encoding="utf-8")
    runner._last_referenced_artifacts = (str(target),)
    runner._last_referenced_request_index = 1
    runner._alias_request_index = 2
    answer = asyncio.run(runner._run_async("Read qfile."))
    assert "Please name the file" in answer
    assert getattr(runner, "_llm", None) is None


def test_aliases_in_fenced_data_and_filenames_remain_literal(variant, tmp_path):
    prompt = "Explain this sample:\n```text\nqspace qfile qfiles\n```\nThen read qspace.py."
    assert not variant._user_context_aliases(prompt)
    unchanged, aliases, _, blocker = variant._resolve_user_context_aliases(prompt, tmp_path, ())
    assert unchanged == prompt and not aliases and not blocker


def test_tool_alias_expansion_never_rewrites_content_or_commands(variant, tmp_path):
    args = {"path": "qspace/demo.py", "content": "qspace, qfile, and qfiles are words"}
    expanded = variant._expand_alias_tool_paths(
        "write_file", args, tmp_path, "", frozenset({"qspace"})
    )
    assert expanded["path"] == str(tmp_path / "demo.py")
    assert expanded["content"] == args["content"]
    command = {"command": "echo qspace"}
    assert variant._expand_alias_tool_paths(
        "run_command", command, tmp_path, "", frozenset({"qspace"})
    ) == command
