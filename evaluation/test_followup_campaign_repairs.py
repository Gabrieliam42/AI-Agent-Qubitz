"""CPU-only regressions for observed multi-turn routing and saved-state failures."""
from __future__ import annotations

import asyncio
import copy
import hashlib
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation.followup_matrix_cases import (
    Turn,
    verify_turn,
    workspace_manifest,
    write_fixture,
)

begin = receipts.begin
runner = receipts.runner
variant = receipts.variant


@pytest.mark.parametrize("prefix", ["this workspace's", "our project's", "the user's"])
def test_possessives_do_not_hide_named_files(variant, prefix):
    prompt = f"Use {prefix} bridge.py to invoke echo with value='first' and label='local'."
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt) == ["bridge.py"]


def test_prohibited_creation_is_not_a_destination(variant, runner):
    prompt = "New task after cancellation: create allowed.txt containing exactly ready followed by a newline. Do not create forbidden.txt."
    request = variant._named_file_operation_request(variant._APP.base, runner.workspace, variant.ACCESS_MODE_FULL, prompt)
    assert request is not None and request["operation"] == "create"
    assert request["target"].endswith("allowed.txt")
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt, for_creation=True) == ["allowed.txt"]


@pytest.mark.parametrize("connector", ["then", "but", "instead"])
def test_creation_after_prohibition_keeps_its_positive_destination(variant, connector):
    prompt = f"Do not create blocked.txt {connector} create allowed.txt containing ready."
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt, for_creation=True) == ["allowed.txt"]


@pytest.mark.parametrize("prompt,needs_context", [
    ("Create allowed.txt containing ready. Do not create forbidden.txt.", False),
    ("Create allowed.txt containing ready. Leave other.txt unchanged.", False),
    ("Read source.txt, then create allowed.txt from it. Do not edit source.txt.", True),
    ("Create allowed.txt based on source.txt. Do not edit source.txt.", True),
    ("Read source.txt, then create allowed.txt from it; keep source.txt unchanged.", True),
])
def test_protected_targets_do_not_become_inputs_but_actual_sources_remain(variant, prompt, needs_context):
    assert variant._creation_requires_existing_workspace_context(variant._APP.base, prompt) is needs_context


def test_test_runner_role_is_bound_to_actual_entrypoint(variant, runner, tmp_path):
    (tmp_path / "run_tests.py").write_text("raise SystemExit(0)\n")
    prompt = "Fix engine.py, then rerun the same run_tests.py command. Do not change the test or test runner."
    broker = begin(runner, prompt)
    assert ("edit", "run_tests.py") in broker.task_intent.semantic.scoped_prohibitions
    allowed, result = broker.authorize("apply_text_patch", {
        "path": "run_tests.py", "expected_sha256": "0" * 64,
        "replacements": [{"old_text": "0", "new_text": "1"}],
    })
    assert not allowed and result["status"] == "scope_denied"


def test_readonly_reconciliation_does_not_require_tests(variant, runner):
    prompt = "Check the previous operation through list_records, without repeating commit_then_error or creating a record. Verify exactly one record with label one and value 4."
    broker = begin(runner, prompt)
    broker.mcp_tool_schemas = {"list_records": {"properties": {}}, "commit_then_error": {"properties": {}}}
    intent = broker.bind_mcp_action_intent(broker.task_intent)
    assert intent.semantic.requests("mcp_call")
    assert "tests" not in broker._completion_requirements(prompt, intent)
    assert variant._requested_mcp_tool_names(prompt, tuple(broker.mcp_tool_schemas)) == {"list_records"}


def test_retry_binding_is_workspace_scoped_and_keeps_arguments(variant, runner):
    request = {"server_reference": "bridge.py", "tool_name": "echo", "arguments": {"value": "first"}}
    previous = {"workspace": str(runner.workspace.resolve()),
                "mcp_bindings": {"bridge.py": {"schemas": {"echo": {"properties": {"value": {"type": "string"}}}}}},
                "mcp_invocations": {"key": {"state": "uncertain", "request": request}}}
    prompt = "Retry that same read through bridge.py with value='first'. Verify the returned value."
    assert variant._follow_up_mcp_request(prompt, previous, runner.workspace) == request
    assert variant._follow_up_mcp_request(prompt, {**previous, "workspace": "/other"}, runner.workspace) is None
    assert variant._follow_up_mcp_request("Retry that same read through other.py.", previous, runner.workspace) is None
    multiple = {**previous, "mcp_invocations": {**previous["mcp_invocations"], "other": {
        "state": "uncertain", "request": {**request, "arguments": {"value": "second"}},
    }}}
    assert variant._follow_up_mcp_request(prompt, multiple, runner.workspace) is None


def test_uncertain_mutation_is_still_not_replayable(variant, runner):
    broker = begin(runner, "Retry that same operation through bridge.py.")
    request = {"server_reference": "bridge.py", "tool_name": "commit", "arguments": {"value": 3}}
    assert broker.reserve_mcp_invocation(request) is None
    assert broker.mcp_invocations[broker._mcp_invocation_key(request)]["request"] == request
    assert broker.reserve_mcp_invocation(request)["status"] == "mcp_replay_blocked"


def test_recovery_keeps_pending_mcp_and_file_tools(variant, runner):
    prompt = "Call the MCP typed_echo with value=11, then update output.json from the result."
    broker = begin(runner, prompt)
    broker.mcp_tool_schemas["typed_echo"] = {"properties": {"value": {"type": "integer"}}}
    broker.transaction_recovery_required = True
    tools = [{"name": name} for name in ("read_file_snapshot", "apply_text_patch", "apply_text_patches",
             "write_file", "write_files", "list_project_mcp_tools", "call_project_mcp_tool")]
    names = {runner._tool_name(tool) for tool in runner._filter_tools_by_intent(prompt, tools)}
    assert {"list_project_mcp_tools", "call_project_mcp_tool", "read_file_snapshot", "apply_text_patch"} <= names


def test_singleton_batch_tools_act_on_only_the_requested_file(variant, runner, tmp_path):
    broker = begin(runner, "Create draft.txt containing ready.")
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, receipts.ROOT,
                                           Path(variant.__file__))
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        asyncio.run(server.call_tool("write_files", {"files": [{"path": "draft.txt", "content": "ready\n"}]}))
        assert (tmp_path / "draft.txt").read_text() == "ready\n"
        broker.begin_turn("In draft.txt replace ready with done.", None)
        asyncio.run(server.call_tool("apply_text_patches", {"patches": [{"path": "draft.txt", "replacements": [
            {"old_text": "ready", "new_text": "done"},
        ]}]}))
        assert (tmp_path / "draft.txt").read_text() == "done\n"
        assert not (tmp_path / "dummy.txt").exists()
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)


def test_adjacent_user_messages_merge_without_altering_tools(variant):
    messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "original"},
                {"role": "user", "content": "latest steering"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "a"}]},
                {"role": "tool", "tool_call_id": "a", "content": "result"}]
    original = copy.deepcopy(messages)
    normalized = variant._normalize_adjacent_user_messages(messages)
    assert messages == original
    assert normalized[1]["content"] == "original\n\nlatest steering"
    assert normalized[2:] == original[3:]


def test_latest_steering_values_are_verified_not_original_json(variant, runner, tmp_path):
    prompt = 'Create config.json containing exactly {"color":"blue","limit":3}.'
    prompt += "\nChange the requested color in config.json to green instead of blue. Preserve limit=3."
    broker = begin(runner, prompt)
    target = tmp_path / "config.json"
    broker.creation_targets.add(str(target))
    target.write_text('{"color":"blue","limit":3}\n')
    assert variant._saved_text_contract_issues(broker, prompt, [])
    target.write_text('{"color":"green","limit":3}\n')
    assert not variant._saved_text_contract_issues(broker, prompt, [])
    target.write_text('{"color":"green","limit":4}\n')
    assert variant._saved_text_contract_issues(broker, prompt, [])


@pytest.mark.parametrize("directive", ["Do not change the requested color in config.json to green instead of blue.",
                                      "Why change the requested color in config.json to green instead of blue?"])
def test_constraints_and_questions_do_not_override_json_contracts(variant, runner, directive):
    prompt = 'Create config.json containing exactly {"color":"blue","limit":3}.\n' + directive
    contracts = variant._explicit_saved_file_contracts(begin(runner, prompt), prompt)
    assert contracts[str(runner.workspace / "config.json")] == {"json": {"color": "blue", "limit": 3}}


def test_mcp_completion_rechecks_latest_steering_arguments(variant, runner):
    prompt = "Call the MCP echo with value='old'."
    broker = begin(runner, prompt)
    broker.mcp_tool_schemas["echo"] = {"properties": {"value": {"type": "string"}}}
    broker.record_result("call_project_mcp_tool", {"tool_name": "echo", "arguments": {"value": "old"}},
                         receipts.result({"result": {"structuredContent": {"value": "old"}}}))
    broker.current_prompt += "\nUse value='new' for the pending call."
    assert "mcp_tool_called" in broker.completion_report(prompt)[1]
    assert broker._explicit_mcp_arguments_satisfied("echo", {"value": "new"})[0]


@pytest.mark.parametrize("bad", ["ready", "ready\n\n", "ready\\n\n"])
def test_exact_newline_contract_rejects_near_matches(variant, runner, tmp_path, bad):
    prompt = "Create draft.txt containing exactly ready followed by a newline."
    broker = begin(runner, prompt)
    target = tmp_path / "draft.txt"
    broker.creation_targets.add(str(target))
    target.write_text(bad)
    assert variant._saved_text_contract_issues(broker, prompt, [])
    target.write_text("ready\n")
    assert not variant._saved_text_contract_issues(broker, prompt, [])


def test_exact_read_returns_fresh_information(variant, runner, tmp_path):
    target = tmp_path / "config.json"
    target.write_text('{"owner":"current","limit":9}\n')
    broker = begin(runner, "What is owner in config.json now?")
    broker.transaction_snapshot_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert variant._verified_file_read_answer(broker, broker.current_prompt, ()) == "current"
    target.write_text('{"owner":"external","limit":9}\n')
    assert variant._verified_file_read_answer(broker, broker.current_prompt, ()) is None
    assert variant._verified_file_read_answer(broker, "Explain config.json", ()) is None


@pytest.mark.parametrize("prompt", ["Is limit too low in config.json?", "Read config.json and explain whether limit is valid.",
                                   "Show only the last line of config.json."])
def test_read_fast_path_does_not_answer_interpretation_or_partial_selection(variant, runner, tmp_path, prompt):
    target = tmp_path / "config.json"
    target.write_text('{"limit":9}\n')
    broker = begin(runner, prompt)
    broker.transaction_snapshot_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert variant._verified_file_read_answer(broker, prompt, ()) is None


def test_incidental_protected_backup_requires_observed_snapshot_and_unchanged_original(tmp_path):
    write_fixture(tmp_path, "run_tests.py", "raise SystemExit(0)\n")
    before = workspace_manifest(tmp_path)
    backup = "backup/run_tests.py.001.bak"
    write_fixture(tmp_path, backup, "raise SystemExit(0)\n")
    write_fixture(tmp_path, "changelog.txt", f"{backup} | run_tests.py | Preserved the pre-edit snapshot.\n")
    turn = Turn("Fix engine.py; do not change the runner.", (), ("engine.py",))
    observed = {"turn": 1, "tools": [{"turn": 1, "capture_origin": "mcp_host", "name": "read_file_snapshot",
                                     "arguments": {"path": "run_tests.py"}}]}
    assert not verify_turn(tmp_path, turn, observed, before, workspace_manifest(tmp_path))[0]
    assert verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0]
    write_fixture(tmp_path, "run_tests.py", "raise SystemExit(1)\n")
    assert verify_turn(tmp_path, turn, observed, before, workspace_manifest(tmp_path))[0]


def test_creation_contracts_bind_each_file_independently(variant, runner):
    prompt = "Create a.txt containing exactly alpha followed by a newline and b.txt containing exactly beta followed by a newline."
    contracts = variant._explicit_saved_file_contracts(begin(runner, prompt), prompt)
    assert contracts == {str(runner.workspace / "a.txt"): {"text": "alpha\n"},
                         str(runner.workspace / "b.txt"): {"text": "beta\n"}}


@pytest.mark.parametrize("literal", ["ready", "`ready`", '"ready"'])
def test_literal_edit_and_newline_contract(variant, runner, tmp_path, literal):
    path = tmp_path / "state.txt"
    path.write_bytes(b"before\r\n")
    prompt = f"Change state.txt to {literal} followed by a newline."
    broker = begin(runner, prompt)
    assert variant._saved_text_contract_issues(broker, prompt, [])
    path.write_bytes(b"ready\r\n")
    assert not variant._saved_text_contract_issues(broker, prompt, [])
    path.write_bytes(b"ready\r\n\r\n")
    assert variant._saved_text_contract_issues(broker, prompt, [])


def test_bound_content_replacement_preserves_other_targets(variant, runner, tmp_path):
    target = tmp_path / "draft.txt"
    other = tmp_path / "other.txt"
    target.write_text("before\n")
    other.write_text("preserved\n")
    prompt = "Replace its content with ready followed by a newline; leave other.txt unchanged."
    broker = begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    contracts = variant._explicit_saved_file_contracts(broker, prompt)
    assert contracts == {str(target): {"text": "ready\n"}}


def test_latest_numeric_mcp_steering_supersedes_quoted_value(variant, runner):
    broker = begin(runner, "Call typed_echo with value='3'.\nUse value=9 for the pending call.")
    broker.mcp_tool_schemas["typed_echo"] = {"properties": {"value": {"type": "integer"}}}
    assert broker._explicit_mcp_arguments_satisfied("typed_echo", {"value": 9}) == (True, {"value": 9})
    assert not broker._explicit_mcp_arguments_satisfied("typed_echo", {"value": 3})[0]


@pytest.mark.parametrize("directive", ["Do not use value='wrong' for the pending call.",
                                      "Why use value='wrong' for the pending call?"])
def test_mcp_constraints_and_questions_are_not_required_values(variant, runner, directive):
    broker = begin(runner, "Call echo with value='requested'.\n" + directive)
    broker.mcp_tool_schemas["echo"] = {"properties": {"value": {"type": "string"}}}
    assert broker._explicit_mcp_arguments_satisfied("echo", {"value": "requested"}) == (True, {"value": "requested"})
    assert not broker._explicit_mcp_arguments_satisfied("echo", {"value": "wrong"})[0]


@pytest.mark.parametrize("label", ["what if", "do not use", "Note. Do not use", "Note; what if"])
def test_argument_literal_data_does_not_change_instruction_meaning(variant, runner, label):
    broker = begin(runner, f"Call echo with label={label!r} and value='requested'.")
    broker.mcp_tool_schemas["echo"] = {"properties": {"label": {"type": "string"}, "value": {"type": "string"}}}
    assert broker._explicit_mcp_arguments_satisfied("echo", {"label": label, "value": "requested"})[0]
    assert not broker._explicit_mcp_arguments_satisfied("echo", {"label": label, "value": "wrong"})[0]


def test_verified_scalar_read_does_not_generate_model_prose(variant, runner, tmp_path, monkeypatch):
    (tmp_path / "settings.json").write_text('{"limit":7}\n')
    receipts.install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    index = next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner")

    async def unexpected_model(*args, **kwargs):
        pytest.fail("Verified literal reads must not start a model pass")

    monkeypatch.setattr(mro[index + 1], "_run_async", unexpected_model)
    answer = asyncio.run(runner._run_async("What is limit in settings.json? Answer only the number. Do not modify it."))
    assert answer == "7"


def test_literal_read_does_not_confuse_filename_with_json_field(variant, runner, tmp_path):
    target = tmp_path / "config.json"
    text = '{"config":"nested","owner":"current","limit":9}\n'
    target.write_text(text)
    broker = begin(runner, "Read config.json.")
    broker.transaction_snapshot_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    assert variant._verified_file_read_answer(broker, broker.current_prompt, ()) == text
    assert variant._verified_file_read_answer(broker, "What is owner doing in config.json?", ()) is None
    assert variant._verified_file_read_answer(broker, "What are owner and limit in config.json?", ()) is None


def test_explicit_test_check_is_recognized(variant):
    assert variant._requests_test_execution("Check the tests before reporting.")
