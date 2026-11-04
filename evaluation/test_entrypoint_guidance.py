"""Registered MCP schemas explain scope without changing execution semantics."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


def test_real_mcp_schema_explains_active_workspace_and_program_arguments(variant, runner):
    server = variant._build_local_mcp_server(
        variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__),
    )
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "run_existing_entrypoint")
    assert "active workspace" in tool.description
    assert "never Python interpreter options" in tool.description
    assert "plain run request omit arguments" in tool.description
    properties = tool.inputSchema["properties"]
    assert "active workspace" in properties["path"]["description"]
    assert "not interpreter flags" in properties["arguments"]["description"]
    assert "'.' means that workspace" in properties["cwd"]["description"]
    assert properties["arguments"]["default"] is None
    assert properties["cwd"]["default"] == "."


def test_guidance_keeps_exact_no_argument_receipt_and_supplied_program_arguments(variant, runner, monkeypatch):
    target = runner.workspace / "calculator.py"
    target.write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(
        variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__),
    )
    for arguments in (None, ["--label", "value with spaces"], ["-B"]):
        supplied = {"path": "calculator.py"}
        if arguments is not None:
            supplied["arguments"] = arguments
        result = asyncio.run(server.call_tool("run_existing_entrypoint", supplied))[1]
        assert result["command"] == [sys.executable, str(target), *(arguments or [])]
        assert result["returncode"] == 0
        assert json.loads(result["stdout"]) == (arguments or [])
        assert result["resolved_cwd"] == str(runner.workspace.resolve())
