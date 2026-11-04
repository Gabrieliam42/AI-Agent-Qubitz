"""Characterize the held smoke findings without changing their acceptance gate."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries

variant = receipts.variant
runner = receipts.runner


def test_script_arguments_are_not_interpreter_options(variant, runner, monkeypatch, record_property):
    target = runner.workspace / "calculator.py"
    target.write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    prompt = "Now run calculator.py using the active Python interpreter and report its exit code. Do not edit it."
    broker = receipts.begin(runner, prompt)
    supplied = {"path": "calculator.py", "arguments": ["-B"], "cwd": "."}
    allowed, denial = broker.authorize("run_existing_entrypoint", dict(supplied))
    record_property("unrequested_script_argument_authorized", str(allowed))
    record_property("authorization_denial", json.dumps(denial))
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(
        variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__),
    )
    result = asyncio.run(server.call_tool("run_existing_entrypoint", supplied))[1]
    assert result["command"] == [sys.executable, str(target), "-B"]
    assert result["command"] != [sys.executable, str(target)]
    assert result["returncode"] == 0
    assert json.loads(result["stdout"]) == ["-B"]
    assert result["resolved_cwd"] == str(runner.workspace.resolve())


def test_root_based_relative_path_is_rejected_without_basename_substitution(variant, runner):
    target = runner.workspace / "calculator.py"
    target.write_text("pass\n", encoding="utf-8")
    broker = receipts.begin(runner, "Run calculator.py and report its exit code. Do not edit it.")
    host = boundaries.host_for(variant, broker)
    rejected = asyncio.run(host.call_tool("run_existing_entrypoint", {
        "path": "evaluation/results/campaign/fixtures/a/calculator.py", "cwd": ".",
    }))
    assert rejected.isError
    assert rejected.structuredContent["status"] == "path_resolution_error"
    assert rejected.structuredContent["dispatch_attempted"] is False
    assert host.dispatched == []
    accepted = asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "calculator.py", "cwd": "."}))
    assert not accepted.isError
    assert len(host.dispatched) == 1
    assert host.dispatched[0][1]["path"] == str(target.resolve())
    # Recovery does not turn the earlier rejected result into a successful receipt.
    assert rejected.isError and rejected.structuredContent["dispatch_attempted"] is False
