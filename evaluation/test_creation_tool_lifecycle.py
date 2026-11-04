"""Current-turn route ownership and format-scoped creation tools; no inference."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner
PROMPT = (
    "create a Python script named calculator.py that would be a basic calculator "
    "in the workspace directory"
)
TOOL_NAMES = (
    "write_file", "write_files", "list_files", "read_file", "search_text",
    "read_file_snapshot", "apply_text_patch", "apply_text_patches",
    "apply_docx_text_patch", "apply_notebook_text_patch", "apply_spreadsheet_cell_patch",
    "codeintel_diagnostics", "list_local_plugins", "search_tools", "run_existing_entrypoint",
)


def tools():
    return [{"type": "function", "function": {"name": name, "description": name}}
            for name in TOOL_NAMES]


def select(runner, prompt=PROMPT):
    return [runner._tool_name(tool) for tool in runner._filter_tools_by_intent(prompt, tools())]


def start(runner, prompt=PROMPT):
    broker = receipts.begin(runner, prompt)
    runner._route_decision = runner._decide_route(prompt)
    # Legacy wrappers lock this lower-level route inside their model loop.
    runner._locked_route_name = "retrieval_plus_model"
    return broker


def record_creation(runner, broker, name="calculator.py"):
    path = runner.workspace / name
    content = "print(2 + 3)\n"
    path.write_text(content, encoding="utf-8")
    broker.record_result("write_file", {"path": name, "content": content}, receipts.result({
        "path": name, "created": True,
        "new_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }))
    return path


def test_canonical_route_overrides_legacy_model_loop_lock(runner):
    start(runner)
    assert runner._route_decision.selected_route == "named_file_operation"
    assert runner._selected_route_name(PROMPT) == "named_file_operation"
    assert select(runner) == ["write_file"]


def test_created_python_review_is_format_scoped_and_schema_stable(runner):
    broker = start(runner)
    record_creation(runner, broker)
    expected = ["read_file_snapshot", "apply_text_patch"]
    assert select(runner) == expected
    broker.discovered_tool_names = ["codeintel_diagnostics", "list_local_plugins"]
    assert select(runner) == expected
    broker.unavailable_tool_names.add("codeintel_diagnostics")
    assert select(runner) == expected
    broker.transaction_recovery_required = True
    assert select(runner) == expected


@pytest.mark.parametrize(("suffix", "patch"), [
    (".txt", "apply_text_patch"), (".docx", "apply_docx_text_patch"),
    (".ipynb", "apply_notebook_text_patch"), (".xlsx", "apply_spreadsheet_cell_patch"),
])
def test_named_existing_edit_keeps_format_tools(runner, suffix, patch):
    name = "artifact" + suffix
    (runner.workspace / name).write_bytes(b"fixture")
    prompt = f"Edit {name} to change its title to ready."
    start(runner, prompt)
    assert set(select(runner, prompt)) == {"read_file_snapshot", patch}


@pytest.mark.parametrize("mode", ["read_only", "plan"])
def test_access_downgrade_still_overrides_execution_route(runner, variant, mode):
    runner.set_access_mode(mode)
    runner._route_decision = SimpleNamespace(selected_route="direct_existing_entrypoint")
    assert runner._selected_route_name("Run calculator.py.") == "retrieval_plus_model"
    broker = receipts.begin(runner, "Run calculator.py.")
    assert not broker.authorize("run_existing_entrypoint", {"path": "calculator.py"})[0]


def test_new_turn_does_not_reuse_previous_creation_route(runner):
    start(runner)
    broker = receipts.begin(runner, "What is 2 + 3? Do not call any tools.")
    runner._route_decision = runner._decide_route(broker.current_prompt)
    assert runner._route_decision.selected_route != "named_file_operation"
    assert select(runner, broker.current_prompt) == []


@pytest.mark.parametrize("tool", [
    "apply_docx_text_patch", "apply_notebook_text_patch", "apply_spreadsheet_cell_patch",
])
def test_wrong_format_is_rejected_before_snapshot_recovery(runner, tool):
    broker = start(runner)
    path = record_creation(runner, broker)
    before = path.read_bytes()
    arguments = {"path": "calculator.py", "replacements": [{"old_text": "2", "new_text": "4"}]}
    allowed, notice = broker.authorize(tool, arguments)
    assert not allowed
    assert notice["status"] == "unsupported_format"
    assert notice["file_unchanged"] is True
    assert "expected_sha256" not in arguments
    assert not broker.transaction_recovery_required
    assert path.read_bytes() == before


def test_correct_text_editor_still_requires_current_snapshot(runner):
    broker = start(runner)
    path = record_creation(runner, broker)
    allowed, notice = broker.authorize("apply_text_patch", {
        "path": str(path), "replacements": [{"old_text": "2", "new_text": "4"}],
    })
    assert not allowed and notice["status"] == "transaction_required"
    assert broker.transaction_recovery_required
