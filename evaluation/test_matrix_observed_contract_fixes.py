"""CPU-only regressions for clarification, advertised schemas and cleanup errors."""
from __future__ import annotations

import asyncio
import base64
import copy
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

begin = receipts.begin
runner = receipts.runner
variant = receipts.variant


@pytest.mark.parametrize("selection", ["I mean draft.txt.", "Use draft.txt.", "Select draft.txt."])
def test_explicit_clarification_binds_whole_content(variant, runner, selection):
    target = runner.workspace / "draft.txt"
    other = runner.workspace / "other.txt"
    target.write_bytes(b"before\n")
    other.write_bytes(b"untouched\n")
    prompt = selection + " Replace its content with ready followed by a newline; leave other.txt unchanged."
    broker = begin(runner, prompt)
    assert broker.transaction_explicit_file_targets == {str(target)}
    assert broker.named_operation_contract["target"] == str(target)
    assert broker.named_operation_contract["exists"]
    assert variant._explicit_saved_file_contracts(broker, prompt) == {str(target): {"text": "ready\n"}}
    target.write_bytes(b"ready\n\n")
    assert variant._saved_text_contract_issues(broker, prompt, [])
    target.write_bytes(b"ready\n")
    assert not variant._saved_text_contract_issues(broker, prompt, [])
    assert other.read_bytes() == b"untouched\n"


@pytest.mark.parametrize("prompt", [
    "Replace the content of draft.txt with ready followed by a newline.",
    "Replace draft.txt's content with ready followed by a newline.",
])
def test_named_whole_content_literal_contract(variant, runner, prompt):
    target = runner.workspace / "draft.txt"
    target.write_bytes(b"before\n")
    broker = begin(runner, prompt)
    assert variant._explicit_saved_file_contracts(broker, prompt) == {str(target): {"text": "ready\n"}}


@pytest.mark.parametrize("prompt", [
    "I mean a.txt and b.txt. Replace its content with ready followed by a newline.",
    "I mean a.txt. Why replace its content with ready followed by a newline?",
    "Replace the word before with ready in a.txt; preserve the rest of the file.",
])
def test_ambiguous_questions_and_fragment_edits_do_not_bind_whole_content(variant, runner, prompt):
    for name in ("a.txt", "b.txt"):
        (runner.workspace / name).write_bytes(b"before\n")
    broker = begin(runner, prompt)
    assert not variant._explicit_saved_file_contracts(broker, prompt)


def test_invalid_mcp_routing_returns_advertised_contract_without_dispatch(variant, runner):
    broker = begin(runner, "Read the current log for an already-managed MCP server.")
    host = boundaries.host_for(variant, broker)
    schema = {"type": "object", "additionalProperties": False, "required": ["server_id"],
              "properties": {"server_id": {"type": "string"}, "max_chars": {"type": "integer"}}}
    host._qubitz_tool_contracts["read_project_mcp_server_log"] = SimpleNamespace(inputSchema=schema)
    arguments = {"server_reference": "bridge.py"}
    original = copy.deepcopy(arguments)
    result = asyncio.run(host.call_tool("read_project_mcp_server_log", arguments))
    assert result.isError and result.structuredContent["status"] == "invalid_arguments"
    hint = result.structuredContent["argument_contract"]
    assert hint["required"] == ["server_id"]
    assert hint["properties"]["server_id"]["type"] == "string"
    assert "server_reference" not in hint["properties"]
    assert arguments == original and not host.dispatched and not broker.mcp_invocations


def test_cleanup_errors_are_plain_and_ownership_checks_remain(variant, monkeypatch, capsys):
    class Server:
        def __init__(self, config):
            self.config = config
            self.process = None
            self.model_path = "/mnt/d/models/target.gguf"
            self.base_url = "http://127.0.0.1:8001"

        def _launch_command(self):
            pytest.fail("CPU cleanup test must not launch a server")

        def ensure_started(self):
            pytest.fail("CPU cleanup test must not launch a server")

        def resolve_executable(self):
            return "/mnt/d/runtime/llama-server.exe"

        def shutdown(self):
            self.process = None

    scripts = []

    def run(command, **_kwargs):
        scripts.append(base64.b64decode(command[-1]).decode("utf-16-le"))
        return SimpleNamespace(returncode=1, stderr="Ownership check failed", stdout="")

    monkeypatch.setattr(variant.subprocess, "run", run)
    monkeypatch.setattr(variant, "_build_powershell_management_command", lambda *_args: ["powershell.exe", "-Command", ""])
    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: True,
                           normalize_base_url=lambda url: url, DEFAULT_LLAMACPP_PORT=8001,
                           wsl_path_to_windows_path=lambda path: str(path))
    variant._patch_llamacpp_launch_fit(base)
    server = Server(SimpleNamespace(model_name="target"))
    server._qubitz_owned_launch_epoch = 1.0
    server._qubitz_owner_token = "fixture-owner"
    server.shutdown()
    assert scripts and "Ownership check failed" in capsys.readouterr().err
    for script in scripts:
        assert "$ProgressPreference = 'SilentlyContinue'" in script
        assert "[Console]::Error.WriteLine($_.Exception.Message)" in script
        assert "no verifiable ownership; it was not terminated" in script
        assert "CreationDate" in script and "# QUBITZ_OWNER=" in script
    assert server._qubitz_owner_token == "fixture-owner"
