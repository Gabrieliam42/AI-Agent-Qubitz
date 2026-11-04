"""CPU-only regressions for source-bound repair, MCP dispatch and GUI exits."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

import tmp_smoke_variant as smoke
from evaluation import test_explicit_creation_contracts as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

begin = receipts.begin
runner = receipts.runner
variant = receipts.variant

TRANSFER = (
    "Insert only the complete paragraph under ## Selected from origin.md into guide.md "
    "between <!-- begin:part --> and <!-- end:part -->. Copy it verbatim, followed by one newline. "
    "Preserve both markers and every byte outside them. Do not modify origin.md."
)


def snapshot(broker, target):
    data = target.read_bytes()
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "status": "text_snapshot", "sha256": hashlib.sha256(data).hexdigest(),
        "start_line": 1, "end_line": len(data.splitlines()),
    }))


def transfer_setup(runner, *, encoding="utf-8", bom=b"", newline="\n"):
    source = runner.workspace / "origin.md"
    target = runner.workspace / "guide.md"
    paragraph = "This exact paragraph belongs to the generated source, not to its heading."
    source.write_bytes(("## Other\n\nUnselected.\n\n## Selected\n\n" + paragraph + "\n\n## End\n\nUntouched.\n").encode())
    prefix = "# Guide" + newline + "<!-- begin:part -->" + newline
    suffix = "<!-- end:part -->" + newline + "Keep this footer."
    original = bom + (prefix + suffix).encode(encoding)
    target.write_bytes(original)
    broker = begin(runner, TRANSFER)
    snapshot(broker, source)
    snapshot(broker, target)
    target.write_bytes(bom + (prefix + "## Selected" + newline + newline + paragraph + newline * 2 + suffix).encode(encoding))
    snapshot(broker, target)
    return broker, source, target, bom + (prefix + paragraph + newline + suffix).encode(encoding), original


@pytest.mark.parametrize("encoding,bom,newline", [
    ("utf-8", b"", "\n"),
    ("utf-8", b"\xef\xbb\xbf", "\r\n"),
    ("utf-16-le", b"\xff\xfe", "\r\n"),
])
def test_exact_transfer_uses_atomic_tool_and_preserves_outer_bytes(variant, runner, encoding, bom, newline):
    broker, source, target, expected, _original = transfer_setup(runner, encoding=encoding, bom=bom, newline=newline)
    before_source = source.read_bytes()
    plan = variant._exact_saved_fragment_patch(broker, TRANSFER)
    assert plan is not None
    server = variant._build_local_mcp_server(variant._APP.base, broker.workspace, broker.workspace, creation.REFERENCE)
    host = boundaries.host_for(variant, broker, server)
    result = asyncio.run(host.call_tool("apply_text_patch", {
        key: plan[key] for key in ("path", "expected_sha256", "replacements")
    }))
    assert not result.isError, result.structuredContent
    assert target.read_bytes() == expected
    assert source.read_bytes() == before_source
    assert not variant._saved_text_contract_issues(broker, TRANSFER, broker._current_turn_evidence())


@pytest.mark.parametrize("defect", ["ambiguous_source", "duplicate_heading", "duplicate_marker", "outside_changed", "stale_source", "stale_target"])
def test_unsafe_transfer_is_not_automatically_repaired(variant, runner, defect):
    broker, source, target, _expected, _original = transfer_setup(runner)
    if defect == "ambiguous_source":
        source.write_bytes(source.read_bytes().replace(b"## End", b"A second paragraph.\n\n## End"))
        snapshot(broker, source)
    elif defect == "duplicate_heading":
        source.write_bytes(source.read_bytes() + b"\n## Selected\n\nAnother.\n")
        snapshot(broker, source)
    elif defect == "duplicate_marker":
        target.write_bytes(target.read_bytes() + b"<!-- end:part -->")
        snapshot(broker, target)
    elif defect == "outside_changed":
        target.write_bytes(target.read_bytes().replace(b"Keep this footer", b"Unexpected footer"))
        snapshot(broker, target)
    elif defect == "stale_source":
        source.write_bytes(source.read_bytes() + b"Changed externally.\n")
    else:
        target.write_bytes(target.read_bytes().replace(b"## Selected", b"## Changed"))
    before = (source.read_bytes(), target.read_bytes())
    assert variant._exact_saved_fragment_patch(broker, TRANSFER) is None
    assert (source.read_bytes(), target.read_bytes()) == before


@pytest.mark.parametrize("constraint", ["Do not edit files.", "Do not read files."])
def test_transfer_respects_explicit_prohibitions(variant, runner, constraint):
    broker, _source, _target, _expected, _original = transfer_setup(runner)
    assert variant._exact_saved_fragment_patch(broker, TRANSFER + " " + constraint) is None


def test_optional_digest_is_bound_by_host_and_explicit_stale_digest_is_not_replaced(variant, runner):
    target = runner.workspace / "state.txt"
    target.write_bytes(b"alpha\n")
    broker = begin(runner, "Edit state.txt, replacing alpha with beta.")
    snapshot(broker, target)
    server = variant._build_local_mcp_server(variant._APP.base, broker.workspace, broker.workspace, creation.REFERENCE)
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "apply_text_patch")
    assert "expected_sha256" not in tool.inputSchema.get("required", ())
    host = boundaries.host_for(variant, broker, server)
    host._qubitz_tool_contracts["apply_text_patch"] = tool
    arguments = {"path": "state.txt", "replacements": [{"old_text": "alpha", "new_text": "beta"}]}
    stale = asyncio.run(host.call_tool("apply_text_patch", {**arguments, "expected_sha256": "0" * 64}))
    assert stale.isError and stale.structuredContent["status"] == "stale_snapshot"
    assert not host.dispatched and target.read_bytes() == b"alpha\n"
    result = asyncio.run(host.call_tool("apply_text_patch", arguments))
    assert not result.isError, result.structuredContent
    assert target.read_bytes() == b"beta\n"
    assert host.dispatched[-1][1]["expected_sha256"] == hashlib.sha256(b"alpha\n").hexdigest()


def discover(broker, reference="bridge.py", cwd="."):
    broker.record_result("list_project_mcp_tools", {"server_reference": reference, "cwd": cwd}, receipts.result({
        "count": 2, "tools": [
            {"name": name, "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}}}
            for name in ("echo", "wait_for_release")
        ],
    }))


def test_wrong_tool_is_blocked_before_dispatch(variant, runner):
    broker = begin(runner, "Discover bridge.py and invoke its echo tool with value='b-only'.")
    args = {"server_reference": "bridge.py", "tool_name": "wait_for_release", "arguments": {"value": "b-only"}}
    host = boundaries.host_for(variant, broker)
    result = asyncio.run(host.call_tool("call_project_mcp_tool", args))
    assert result.isError and result.structuredContent["status"] == "mcp_discovery_required"
    assert not host.dispatched and not broker.mcp_invocations
    discover(broker)
    result = asyncio.run(host.call_tool("call_project_mcp_tool", args))
    assert result.isError and result.structuredContent["status"] == "mcp_tool_mismatch"
    assert not host.dispatched and not broker.mcp_invocations
    assert variant._mcp_dispatch_blocker(broker, {**args, "tool_name": "echo"}) is None


@pytest.mark.parametrize("routing", [
    {"server_reference": "folder with spaces/bridge.py", "cwd": "folder with spaces"},
    {"server_id": "012345abcdef", "cwd": "."},
])
def test_discovery_guidance_names_direct_tool_and_preserves_only_routing(variant, runner, routing):
    broker = begin(runner, "Discover bridge.py and invoke echo with value='exact'.")
    args = {
        **routing, "tool_name": "list_project_mcp_tools", "arguments": {"value": "exact"},
        "python_path": ".venv/bin/python", "timeout_seconds": 17,
    }
    original = copy.deepcopy(args)
    host = boundaries.host_for(variant, broker)
    result = asyncio.run(host.call_tool("call_project_mcp_tool", args))
    assert result.isError and result.structuredContent["status"] == "mcp_discovery_required"
    assert result.structuredContent["next_tool"] == "list_project_mcp_tools"
    assert result.structuredContent["next_arguments"] == {
        **routing, "python_path": ".venv/bin/python", "timeout_seconds": 17,
    }
    assert "do not wrap discovery inside call_project_mcp_tool" in result.structuredContent["instruction"]
    assert args == original and not host.dispatched and not broker.mcp_invocations


def write_stdio_fixture(workspace, reference="bridge.py"):
    target = workspace / reference
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fixture')\nserver.run(transport='stdio')\n",
        encoding="utf-8",
    )
    return target


def install_echo_host(variant, runner, monkeypatch):
    schema = {
        "type": "object", "properties": {"value": {"type": "string"}, "label": {"type": "string"}},
        "required": ["value", "label"],
    }

    def handler(name, args):
        assert name in {"list_project_mcp_tools", "call_project_mcp_tool"}
        assert variant._APP.base.resolve_workspace_path(
            runner.workspace, args["server_reference"], allow_missing=False, allow_external=False,
        ).is_relative_to(runner.workspace.resolve())
        assert args["cwd"] == "."
        if name == "list_project_mcp_tools":
            return {"count": 2, "tools": [
                {"name": tool, "inputSchema": schema} for tool in ("echo", "unrequested")
            ]}
        assert args["tool_name"] == "echo"
        return {"result": {"structuredContent": dict(args["arguments"])}}

    return receipts.install_host(variant, runner, monkeypatch, handler=handler)


@pytest.mark.parametrize("reference,prompt", [
    ("bridge.py", "Discover the CURRENT workspace's bridge.py and invoke its echo tool with value='b-only' and label='workspace-b'. Do not reuse a server from another workspace."),
    ("bridge.py", "Discover the project's \"bridge.py\" and invoke echo with value='b-only' and label='workspace-b'."),
    ("bridge.py", "Discover `bridge.py` and invoke echo with value=\"don't change this\" and label='workspace-b'."),
    ("folder with spaces/bridge.py", "Discover the workspace's 'folder with spaces/bridge.py' and invoke echo with value='b-only' and label='workspace-b'."),
])
def test_mcp_preflight_handles_apostrophes_and_quoted_arguments(variant, runner, monkeypatch, reference, prompt):
    write_stdio_fixture(runner.workspace, reference)
    calls = install_echo_host(variant, runner, monkeypatch)
    answer = asyncio.run(runner._run_async(prompt))
    assert [name for name, _args in calls] == ["list_project_mcp_tools", "call_project_mcp_tool"]
    assert all(args["server_reference"] == reference for _name, args in calls)
    expected_value = "don't change this" if "don't change this" in prompt else "b-only"
    assert calls[-1][1]["arguments"] == {"value": expected_value, "label": "workspace-b"}
    assert "[Completion status: verified]" in answer


def test_mcp_workspace_a_b_a_rediscovers_current_server(variant, runner, monkeypatch):
    # Workspace switching recreates the runner; retain the old keys to challenge dispatch isolation.
    previous_scoped_schemas = {}
    for workspace_name, value in (("a", "a-first"), ("b", "b-only"), ("a", "a-return")):
        workspace = runner.workspace / workspace_name
        target = write_stdio_fixture(workspace)
        config = variant._APP.base.AgentConfig(workspace=workspace, runtime_workspace=receipts.ROOT)
        current = variant._APP.base.AgentRunner(config)
        current.set_access_mode(variant.ACCESS_MODE_FULL)
        try:
            calls = install_echo_host(variant, current, monkeypatch)
            prompt = (
                "Discover the CURRENT workspace's bridge.py and invoke its echo tool "
                f"with value='{value}' and label='workspace-{workspace_name}'. "
                "Do not reuse a server from another workspace."
            )
            broker = begin(current, prompt)
            broker.mcp_scoped_tool_schemas = copy.deepcopy(previous_scoped_schemas)
            if workspace_name == "b":
                blocked = variant._mcp_dispatch_blocker(broker, {
                    "server_reference": "bridge.py", "cwd": ".", "tool_name": "echo", "arguments": {},
                })
                assert blocked["status"] == "mcp_discovery_required"
            answer = asyncio.run(current._run_async(prompt))
            assert [name for name, _args in calls] == ["list_project_mcp_tools", "call_project_mcp_tool"]
            assert calls[-1][1]["arguments"] == {"value": value, "label": f"workspace-{workspace_name}"}
            assert all((workspace / args["server_reference"]).resolve() == target.resolve() for _name, args in calls)
            assert "[Completion status: verified]" in answer
            previous_scoped_schemas = copy.deepcopy(broker.mcp_scoped_tool_schemas)
        finally:
            current.reset_runtime()


def test_dispatch_schema_is_not_reused_from_another_server_or_cwd(variant, runner):
    broker = begin(runner, "Invoke echo through bridge.py with value='exact'.")
    discover(broker)
    for overrides in ({"server_reference": "other.py"}, {"cwd": "other"}):
        args = {"server_reference": "bridge.py", "tool_name": "echo", "arguments": {"value": "exact"}, **overrides}
        assert variant._mcp_dispatch_blocker(broker, args)["status"] == "mcp_discovery_required"


def test_dispatch_preserves_arguments_and_allows_explicit_multiple_tools(variant, runner):
    broker = begin(runner, "Invoke echo then call wait_for_release through bridge.py.")
    discover(broker)
    for name in ("echo", "wait_for_release"):
        args = {"server_reference": "bridge.py", "tool_name": name, "arguments": {"value": "exact"}}
        original = copy.deepcopy(args)
        assert variant._mcp_dispatch_blocker(broker, args) is None
        assert args == original


def test_argument_value_and_negation_do_not_authorize_unrequested_tool(variant, runner):
    broker = begin(runner, "Invoke echo with value='wait_for_release'. Do not call wait_for_release.")
    discover(broker)
    args = {"server_reference": "bridge.py", "tool_name": "wait_for_release", "arguments": {"value": "wait_for_release"}}
    assert variant._mcp_dispatch_blocker(broker, args)["status"] == "mcp_tool_mismatch"


@pytest.mark.parametrize("prompt,exact,expected", [
    ("Invoke echo with value='first'. Verify the returned value.", False, '"value": "first"'),
    ("Invoke echo with value='first'. Output only value.", True, "first"),
    ("Invoke echo with value='first'. Output only JSON.", True, '{"value": "first"}'),
])
def test_completion_retains_current_returned_value(variant, runner, prompt, exact, expected):
    broker = begin(runner, prompt)
    discover(broker)
    broker.record_result("call_project_mcp_tool", {
        "server_reference": "bridge.py", "tool_name": "echo", "arguments": {"value": "first"},
    }, receipts.result({"result": {"structuredContent": {"value": "first"}}}))
    rendered, is_exact = variant._current_mcp_completion_result(broker, prompt)
    assert expected in rendered and is_exact is exact
    if exact:
        assert rendered == expected


def test_completion_does_not_use_stale_or_failed_results(variant, runner):
    broker = begin(runner, "Invoke echo through bridge.py.")
    broker.last_mcp_result = {"success": True, "tool": "echo", "value": {"value": "previous"}}
    assert variant._current_mcp_completion_result(broker, broker.current_prompt) is None
    broker.last_mcp_result = {"success": False}
    assert variant._current_mcp_completion_result(broker, broker.current_prompt) is None


def test_large_mcp_report_is_bounded_and_marked_truncated(variant, runner):
    broker = begin(runner, "Invoke echo through bridge.py.")
    discover(broker)
    broker.record_result("call_project_mcp_tool", {
        "server_reference": "bridge.py", "tool_name": "echo", "arguments": {},
    }, receipts.result({"result": {"structuredContent": {"value": "z" * 8000}}}))
    rendered, exact = variant._current_mcp_completion_result(broker, broker.current_prompt)
    assert "truncated" in rendered and len(rendered) < 4100 and not exact
    assert "value" not in broker.last_mcp_result


class FakeRoot:
    def __init__(self, *, early=False, alive=True):
        self.early, self.alive = early, alive
        self.callback = None

    def after(self, _delay, callback):
        self.callback = callback
        return "timer"

    def after_cancel(self, _timer):
        self.callback = None

    def mainloop(self):
        if not self.early and self.callback is not None:
            self.callback()

    def quit(self):
        pass

    def winfo_exists(self):
        return self.alive


@pytest.mark.parametrize("early,alive,timeout,expected", [
    (True, True, 300, "mainloop_returned_early"),
    (True, False, 300, "window_destroyed"),
    (False, True, 0, "deadline"),
])
def test_gui_exit_classification_does_not_invent_timeouts(early, alive, timeout, expected):
    root = FakeRoot(early=early, alive=alive)
    assert not smoke._drive(root, lambda: False, timeout)
    assert root._smoke_drive_exit_reason == expected


def test_gui_completed_predicate_is_success():
    root = FakeRoot()
    assert smoke._drive(root, lambda: True, 300)
    assert root._smoke_drive_exit_reason == "completed"


def test_smoke_prompt_reports_early_window_loss_as_error_not_timeout():
    root = FakeRoot(early=True, alive=False)
    box = SimpleNamespace(delete=lambda *_: None, insert=lambda *_: None)
    cancelled = []
    gui = SimpleNamespace(root=root, prompt_box=box, busy=True, _smoke_events=[],
                          agent=SimpleNamespace(request_cancel=lambda: cancelled.append(True)))

    def submit():
        gui._smoke_events.append((smoke.time.monotonic(), "user", "prompt"))

    gui.send_prompt = submit
    result = smoke._send_prompt(gui, "prompt", 300)
    assert result["gui_early_exit"] and not result["timed_out"] and not result["completed"]
    assert result["errors"] and cancelled == [True]
    json.dumps(result)
