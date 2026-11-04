"""Existing entrypoints must never be substituted by basename or shell text."""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import pytest
import test_followup_state_receipts as receipts
import test_tool_argument_boundaries as boundaries

variant = receipts.variant
runner = receipts.runner


def make_probe(workspace: Path) -> Path:
    target = workspace / "job.py"
    target.write_text(
        "from pathlib import Path\n"
        "with Path('executions.txt').open('a') as log: log.write('ran\\n')\n",
        encoding="utf-8",
    )
    return target


@pytest.mark.parametrize("supplied", ["missing/job.py", "python job.py", "pytest"])
def test_server_rejects_nonexistent_exact_target(variant, tmp_path, monkeypatch, supplied):
    make_probe(tmp_path)
    (tmp_path / "test_probe.py").write_text("def test_probe(): assert True\n")
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(
        variant._APP.base, tmp_path, tmp_path, Path(variant.__file__),
    )
    with pytest.raises(Exception, match=re.escape(supplied)):
        asyncio.run(server.call_tool("run_existing_entrypoint", {"path": supplied}))
    assert not (tmp_path / "executions.txt").exists()


@pytest.mark.parametrize("absolute", [False, True])
def test_server_executes_exact_target_once(variant, tmp_path, monkeypatch, absolute):
    target = make_probe(tmp_path)
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(
        variant._APP.base, tmp_path, tmp_path, Path(variant.__file__),
    )
    result = asyncio.run(server.call_tool(
        "run_existing_entrypoint", {"path": str(target) if absolute else "job.py"},
    ))[1]
    assert result["command"] == [sys.executable, str(target)]
    assert result["returncode"] == 0
    assert (tmp_path / "executions.txt").read_text() == "ran\n"


def test_host_rejects_bad_prefix_before_dispatch(variant, runner):
    make_probe(runner.workspace)
    broker = receipts.begin(runner, "Run job.py and report its exit code.")
    host = boundaries.host_for(variant, broker)
    result = asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "missing/job.py"}))
    assert result.isError
    assert result.structuredContent["status"] == "path_resolution_error"
    assert result.structuredContent["dispatch_attempted"] is False
    assert not host.dispatched
    assert "entrypoint_executed" in broker.completion_report(broker.current_prompt)[1]


def test_host_binds_valid_relative_target(variant, runner):
    target = make_probe(runner.workspace)
    broker = receipts.begin(runner, "Run job.py and report its exit code.")
    host = boundaries.host_for(variant, broker)
    asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "job.py"}))
    assert host.dispatched[-1] == ("run_existing_entrypoint", {"path": str(target.resolve())})


def test_real_stdio_host_recovers_without_substitution_or_replay(variant, runner):
    target = make_probe(runner.workspace)
    prompt = "Run job.py and report its exit code."
    broker = receipts.begin(runner, prompt)

    async def execute():
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            async with variant._APP.base.MCPHost(runner.workspace) as host:
                await host.list_tools()
                rejected = await host.call_tool("run_existing_entrypoint", {"path": "missing/job.py"})
                assert rejected.isError
                assert rejected.structuredContent["dispatch_attempted"] is False
                assert not (runner.workspace / "executions.txt").exists()
                accepted = await host.call_tool("run_existing_entrypoint", {"path": "job.py"})
                assert not accepted.isError, accepted.structuredContent
                assert accepted.structuredContent["command"][1] == str(target)
                assert accepted.structuredContent["returncode"] == 0
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    asyncio.run(execute())
    assert (runner.workspace / "executions.txt").read_text() == "ran\n"
    assert "entrypoint_executed" not in broker.completion_report(prompt)[1]
    executed = [row for row in broker.evidence if row.get("execution_verified")]
    assert len(executed) == 1
    assert executed[0]["evidence_path"] == str(target.resolve())
