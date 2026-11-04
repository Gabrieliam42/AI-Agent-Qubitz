"""CPU-only regressions for observed constraint, recovery and ownership defects."""
from __future__ import annotations

import hashlib
import threading
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts

begin = receipts.begin
runner = receipts.runner
variant = receipts.variant


@pytest.mark.parametrize("constraint", [
    "Do not append a second copy.",
    "Do not insert duplicate content.",
    "Do not prepend another copy of the paragraph.",
])
def test_duplicate_mode_constraint_does_not_ban_authorized_edit(variant, constraint):
    intent = variant._analyze_semantic_intent(
        "In summary.md replace its old paragraph with the revised source paragraph. " + constraint,
    )
    assert "edit" in intent.requested_actions
    assert "edit" not in intent.prohibited_actions
    assert intent.operation_constraints == (constraint,)


@pytest.mark.parametrize("constraint,operation_constraint", [
    ("Do not edit files.", ""),
    ("Do not append text.", ""),
    ("Do not append text or insert duplicate content.", "Do not append text or insert duplicate content."),
    ("Do not append text even if another section contains duplicate content.", ""),
])
def test_blanket_mutation_prohibition_is_still_enforced(variant, constraint, operation_constraint):
    intent = variant._analyze_semantic_intent("Replace the paragraph in summary.md. " + constraint)
    assert "edit" in intent.prohibited_actions
    assert "edit" not in intent.requested_actions
    assert intent.operation_constraints == ((operation_constraint,) if operation_constraint else ())


@pytest.mark.parametrize("prompt", [
    "Discover fixture_mcp.py then call flaky_read with value='first'.",
    "Inspect the test fixture and report its failures.",
])
def test_local_fixture_is_not_an_online_research_request(variant, prompt):
    assert not variant._prompt_requests_general_web_search(prompt)


@pytest.mark.parametrize("prompt", [
    "Search the web for football fixtures.",
    "What are the league standings?",
    "Verify online that this page is current.",
])
def test_explicit_research_and_sport_requests_remain_online(variant, prompt):
    assert variant._prompt_requests_general_web_search(prompt)


@pytest.mark.parametrize("answer,expected", [
    ('I will inspect it. <tool_call>[{"name":"read_file","path":"a.txt"}]</tool_call>', "read_file"),
    ("<write_file>path=draft.txt</write_file>", "write_file"),
    ('{"function":{"name":"read_file","arguments":{"path":"a.txt"}}}', "read_file"),
    ("<tool_call>invalid</tool_call>", "__unparsed__"),
    ('Example:\n```xml\n<write_file>path=a.txt</write_file>\n```', ""),
    ("A tool called read_file can inspect files.", ""),
])
def test_textual_calls_are_detected_but_not_executed(variant, answer, expected):
    assert variant._unexecuted_tool_call_name(answer, ("read_file", "write_file")) == expected


def test_pending_edit_recovers_from_premature_claim_once(variant, runner, tmp_path):
    (tmp_path / "summary.md").write_text("Old paragraph.\n")
    prompt = "In summary.md replace Old with New."
    broker = begin(runner, prompt)
    assert runner._wrapper_no_progress_tool_choice(prompt, "Task completed.")["function"]["name"] == "read_file_snapshot"
    broker._missing_action_recovery_used = True
    assert runner._wrapper_no_progress_tool_choice(prompt, "Task completed.") is None


def test_user_cancel_never_enters_missing_action_recovery(variant, runner):
    prompt = "Create draft.txt containing ready."
    begin(runner, prompt)
    runner.request_cancel()
    assert runner._wrapper_no_progress_tool_choice(prompt, "Stopped.") is None


def test_saved_file_correction_exposes_patch_not_recreation(variant, runner, tmp_path):
    prompt = "Create notes.md with exactly two level-two sections named Purpose and Checks in that order."
    broker = begin(runner, prompt)
    target = tmp_path / "notes.md"
    target.write_text("# Purpose\n\nText.\n\n# Checks\n\nText.\n")
    original = target.read_bytes()
    broker.record_result("write_file", {"path": "notes.md", "content": target.read_text()}, receipts.result({
        "created": True, "new_sha256": hashlib.sha256(original).hexdigest(),
    }))
    assert "saved_file_contract" in broker.completion_report(prompt)[1]
    broker._saved_contract_correction_active = True
    broker.transaction_recovery_required = True
    runner._focused_missing_postconditions = frozenset({"saved_file_contract"})
    tools = [{"name": name} for name in ("write_file", "write_files", "read_file_snapshot", "apply_text_patch")]
    names = {runner._tool_name(tool) for tool in runner._filter_tools_by_intent(prompt, tools)}
    assert {"read_file_snapshot", "apply_text_patch"} <= names
    assert not {"write_file", "write_files"}.intersection(names)
    assert target.read_bytes() == original


def test_mcp_argument_memory_is_server_and_workspace_scoped(variant, runner, tmp_path):
    broker = begin(runner, "Call echo through first.py.")
    first = {"server_reference": "first.py", "tool_name": "echo", "arguments": {"value": "one"}}
    broker.preserve_mcp_call_arguments(first)
    assert broker.preserve_mcp_call_arguments({"server_reference": "second.py", "tool_name": "echo"}).get("arguments") is None
    assert broker.preserve_mcp_call_arguments({"server_reference": "first.py", "tool_name": "echo"})["arguments"] == {"value": "one"}
    other = tmp_path / "other"
    other.mkdir()
    broker.workspace = other
    assert broker.preserve_mcp_call_arguments({"server_reference": "first.py", "tool_name": "echo"}).get("arguments") is None


def test_valid_explicit_empty_mcp_arguments_are_not_replaced(variant, runner):
    broker = begin(runner, "Call echo through first.py.")
    args = {"server_reference": "first.py", "tool_name": "echo", "arguments": {"value": "one"}}
    broker.preserve_mcp_call_arguments(args)
    empty = {**args, "arguments": {}}
    assert broker.preserve_mcp_call_arguments(empty) == empty


def test_mcp_schema_lookup_uses_bound_server_not_last_discovery(variant, runner):
    broker = begin(runner, "Call echo with value=11.")
    integer = {"properties": {"value": {"type": "integer"}}, "required": ["value"]}
    string = {"properties": {"value": {"type": "string"}}, "required": ["value"]}
    broker.current_mcp_bindings = {
        "one.py": {"schemas": {"echo": integer}},
        "two.py": {"schemas": {"echo": string}},
    }
    broker.mcp_tool_schemas["echo"] = string
    assert broker._mcp_schema_for_call("echo", {"server_reference": "one.py"}) == integer
    assert broker._mcp_schema_for_call("echo", {"server_reference": "two.py"}) == string
    assert broker._mcp_schema_for_call("echo") == {}
    assert broker._mcp_schema_for_call("echo", {"server_reference": "unknown.py"}) == {}


def test_same_relative_mcp_reference_with_different_cwd_keeps_both_schemas(variant, runner, tmp_path):
    broker = begin(runner, "Call echo with value=11.")
    first = {"properties": {"value": {"type": "integer"}}, "required": ["value"]}
    second = {"properties": {"value": {"type": "string"}}, "required": ["value"]}
    for directory, schema in (("a", first), ("b", second)):
        (tmp_path / directory).mkdir()
        broker.record_result("list_project_mcp_tools", {
            "server_reference": "bridge.py", "cwd": directory,
        }, receipts.result({"count": 1, "tools": [{"name": "echo", "inputSchema": schema}]}))
    assert broker._mcp_schema_for_call("echo", {"server_reference": "bridge.py", "cwd": "a"}) == first
    assert broker._mcp_schema_for_call("echo", {"server_reference": "bridge.py", "cwd": "b"}) == second
    assert broker._mcp_schema_for_call("echo") == {}


def test_direct_capability_contract_is_cached_before_receipt_validation(variant, runner):
    broker = begin(runner, "Call echo with value=11.")
    integer = {"properties": {"value": {"type": "integer"}}, "required": ["value"]}
    string = {"properties": {"value": {"type": "string"}}, "required": ["value"]}
    broker.current_mcp_bindings = {"bridge.py": {"schemas": {"echo": string}}}
    broker.mcp_tool_schemas["echo"] = string
    context = {"server_reference": "capability:test", "tool_name": "echo", "arguments": {"value": 11}}
    assert broker._explicit_mcp_arguments_satisfied("echo", {"value": 11}, call_context=context, schema=integer)[0]
    assert broker._mcp_schema_for_call("echo", context) == integer
    assert broker._explicit_mcp_arguments_satisfied("echo", {"value": 11}, call_context=context)[0]
    assert not broker._explicit_mcp_arguments_satisfied("echo", {"value": "11"}, call_context=context)[0]


def test_uncertain_mcp_effects_still_block_replay(variant, runner):
    broker = begin(runner, "Retry the operation through bridge.py.")
    args = {"server_reference": "bridge.py", "tool_name": "commit", "arguments": {"value": 3}}
    assert broker.reserve_mcp_invocation(args) is None
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"


def test_annotated_read_retry_is_bounded(variant, runner):
    broker = begin(runner, "Retry the read through bridge.py.")
    args = {"server_reference": "bridge.py", "tool_name": "read", "arguments": {"value": "one"}}
    key = broker._mcp_invocation_key(args, include_arguments=False)
    broker.mcp_tool_traits[key] = {"readOnlyHint": True, "idempotentHint": True}
    assert broker.reserve_mcp_invocation(args) is None
    assert broker.reserve_mcp_invocation(args) is None
    assert broker.reserve_mcp_invocation(args)["status"] == "mcp_replay_blocked"


def test_server_close_and_launch_share_lock_without_starting_a_process(variant):
    entered = threading.Event()
    release = threading.Event()

    class Server:
        def __init__(self, config):
            self.config, self.process = config, None
            self.model_path = None

        def _launch_command(self):
            pytest.fail("CPU test must not launch a server")

        def ensure_started(self):
            pytest.fail("Closing configuration must refuse launch")

        def shutdown(self):
            entered.set()
            assert release.wait(3)

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: False)
    variant._patch_llamacpp_launch_fit(base)
    server = Server(SimpleNamespace(_qubitz_closing=True))
    closing = threading.Thread(target=server.shutdown)
    closing.start()
    assert entered.wait(3)
    rejected = threading.Event()

    def attempt():
        with pytest.raises(RuntimeError, match="closing"):
            server.ensure_started()
        rejected.set()

    launching = threading.Thread(target=attempt)
    launching.start()
    try:
        assert not rejected.wait(0.05)
    finally:
        release.set()
        closing.join(3)
        launching.join(3)
    assert rejected.is_set()


def test_gui_close_blocks_late_launch_and_keeps_tk_on_main_thread(variant):
    base = SimpleNamespace(QubitzGUI=type("GUI", (), {}))
    variant._install_gui_run_controls(SimpleNamespace(base=base))
    gui = base.QubitzGUI.__new__(base.QubitzGUI)
    gui.config = SimpleNamespace()
    owner_thread = threading.get_ident()
    callbacks, calls = [], []
    cleanup = threading.Event()

    def record(name):
        assert threading.get_ident() == owner_thread
        calls.append(name)

    gui.root = SimpleNamespace(
        withdraw=lambda: record("withdraw"), destroy=lambda: record("destroy"),
        after=lambda _delay, callback: callbacks.append(callback),
    )
    gui.pending_prompts = ["Do not execute this queued task"]
    gui.agent = SimpleNamespace(
        config=SimpleNamespace(), request_cancel=lambda: calls.append("cancel"),
        reset_runtime=lambda: cleanup.set(),
    )
    gui._handle_close()
    assert cleanup.wait(3)
    gui._handle_close()
    while callbacks:
        callbacks.pop(0)()
    assert gui.config._qubitz_closing and gui.agent.config._qubitz_closing
    assert not gui.pending_prompts
    assert calls == ["cancel", "withdraw", "destroy"]
