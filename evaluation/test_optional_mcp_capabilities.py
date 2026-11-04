"""Optional MCP contracts with real stdio fixtures; no models or external services."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
VARIANT = variant_path("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py", ROOT)


@pytest.fixture(scope="module")
def variant():
    spec = importlib.util.spec_from_file_location("optional_mcp_contract_variant", VARIANT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(variant, tmp_path, monkeypatch):
    registry_path = variant._APP.runtime_workspace / ".qubitz" / "mcp_capabilities.json"
    registry = {"version": 1, "capabilities": {}}
    original_read = Path.read_text
    original_exists = Path.exists
    original_stat = Path.stat

    def read_text(path, *args, **kwargs):
        if path == registry_path:
            return json.dumps(registry)
        return original_read(path, *args, **kwargs)

    def exists(path):
        return True if path == registry_path else original_exists(path)

    def stat(path, *args, **kwargs):
        if path == registry_path:
            return original_stat(tmp_path / "registry.json", *args, **kwargs)
        return original_stat(path, *args, **kwargs)

    (tmp_path / "registry.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(variant._APP, "runtime_workspace", tmp_path)
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda *args: True)
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.current_prompt = "Use Serena to inspect symbol references."
    broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    return registry, broker, tmp_path


def execute(variant, broker, workspace, operation):
    async def main():
        host = variant._APP.base.MCPHost(workspace)
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        try:
            return await operation(host)
        finally:
            await host.__aexit__(None, None, None)
            variant._TOOL_CALL_CONTEXT.reset(token)

    return asyncio.run(main())


def nested_result(result):
    payload = result.structuredContent.get("result", {})
    if payload.get("structuredContent") is not None:
        return payload["structuredContent"]
    for item in payload.get("content", []):
        if item.get("type") == "text":
            return json.loads(item["text"])
    raise AssertionError("MCP fixture returned neither structured nor text content")


def test_disabled_catalog_never_starts_a_server(variant, setup):
    _, broker, workspace = setup

    async def operation(host):
        catalog = await host.call_tool("discover_mcp_capability", {})
        assert len(catalog.structuredContent["capabilities"]) == 8
        assert not any(item["enabled"] for item in catalog.structuredContent["capabilities"])
        result = await host.call_tool("list_project_mcp_tools", {"server_reference": "capability:serena"})
        assert result.isError
        assert "disabled" in result.structuredContent["reason"]
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)


def test_offline_blocks_locally_hosted_network_provider(variant, setup, monkeypatch):
    registry, broker, workspace = setup
    registry["capabilities"]["context7"] = {
        "enabled": True, "cost_policy": "free", "transport": "streamable_http",
        "url": "http://127.0.0.1:9/mcp",
    }
    broker.current_prompt = "Use Context7 to verify online documentation."
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda *args: False)

    async def operation(host):
        result = await host.call_tool("list_project_mcp_tools", {"server_reference": "capability:context7"})
        assert result.isError
        assert "Offline" in result.structuredContent["reason"]
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)


@pytest.mark.parametrize("provider,tool", [
    ("serena", "replace_symbol_body"), ("serena", "execute_shell_command"),
    ("playwright", "browser_evaluate"), ("github", "invented_tool"),
])
def test_unclassified_tools_denied_even_in_full_access(variant, setup, provider, tool):
    _, broker, workspace = setup
    broker.current_prompt = f"Use {provider} to execute this task."
    broker.set_access_mode(variant.ACCESS_MODE_FULL)

    async def operation(host):
        result = await host.call_tool("call_project_mcp_tool", {
            "server_reference": f"capability:{provider}", "tool_name": tool, "arguments": {},
        })
        assert result.isError
        assert result.structuredContent["effect"] == "UNKNOWN"
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)


def test_browser_action_denied_in_plan_mode(variant, setup):
    _, broker, workspace = setup
    broker.current_prompt = "Use Playwright to click the submit button in this browser."
    broker.set_access_mode(variant.ACCESS_MODE_PLAN)

    async def operation(host):
        result = await host.call_tool("call_project_mcp_tool", {
            "server_reference": "capability:playwright", "tool_name": "browser_click",
            "arguments": {"ref": "button1"},
        })
        assert result.isError
        assert "Plan" in result.structuredContent["reason"]
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)


def test_paid_policy_denied_before_spawn(variant, setup):
    registry, broker, workspace = setup
    registry["capabilities"]["serena"] = {"enabled": True, "cost_policy": "paid"}

    async def operation(host):
        result = await host.call_tool("list_project_mcp_tools", {"server_reference": "capability:serena"})
        assert result.isError
        assert "paid fallback" in result.structuredContent["reason"]
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)


def test_real_stdio_reuses_session_repairs_schema_and_cleans_up(variant, setup, monkeypatch):
    registry, broker, workspace = setup
    script = workspace / "stdio_fixture.py"
    script.write_text(
        "import os\nfrom mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('fixture')\ncount = 0\n"
        "@server.tool()\ndef get_symbols_overview(value: int, label: str) -> dict:\n"
        "    global count\n    count += 1\n"
        "    return {'count': count, 'value': value, 'label': label, 'pid': os.getpid(),\n"
        "            'home': os.getenv('SERENA_HOME'), 'secret': os.getenv('UNRELATED_TEST_SECRET'),\n"
        "            'mapped': os.getenv('MAPPED_TEST_VALUE')}\n"
        "server.run()\n", encoding="utf-8",
    )
    registry["capabilities"]["serena"] = {
        "enabled": True, "cost_policy": "self_hosted", "command": sys.executable,
        "args": [str(script)], "timeout_seconds": 60,
        "runtime_env": {"SERENA_HOME": "{runtime}/.cache/serena"},
        "env": {"MAPPED_TEST_VALUE": "QUBITZ_TEST_VALUE"},
    }
    monkeypatch.setenv("UNRELATED_TEST_SECRET", "must-not-leak")
    monkeypatch.setenv("QUBITZ_TEST_VALUE", "explicitly-mapped")
    session_ids = []
    pids = []

    async def operation(host):
        catalogs = await asyncio.gather(*(
            host.call_tool("discover_mcp_capability", {"capability": "serena"})
            for _ in range(2)
        ))
        assert all(not item.isError for item in catalogs), [item.model_dump() for item in catalogs]
        assert catalogs[0].structuredContent == catalogs[1].structuredContent
        catalog = catalogs[0]
        assert not catalog.isError
        assert [t["name"] for t in catalog.structuredContent["tools"]] == ["get_symbols_overview"]
        schema = await host.call_tool("discover_mcp_capability", {
            "capability": "serena", "tool_name": "get_symbols_overview",
        })
        assert "inputSchema" in schema.structuredContent["tool"]
        for count, value in enumerate(["7", 9], 1):
            result = await host.call_tool("call_project_mcp_tool", {
                "server_reference": "capability:serena", "tool_name": "get_symbols_overview",
                "arguments": {"value": value, "label": "preserve me"},
            })
            assert not result.isError, result
            data = nested_result(result)
            assert data["count"] == count
            assert data["value"] == int(value)
            assert data["label"] == "preserve me"
            assert data["home"] == str(workspace / ".cache/serena")
            assert data["secret"] is None
            assert data["mapped"] == "explicitly-mapped"
            session_ids.append(id(host.capability_sessions["serena"]))
            pids.append(data["pid"])

    execute(variant, broker, workspace, operation)
    assert len(set(session_ids)) == 1
    assert len(set(pids)) == 1
    assert broker.schema_repairs
    if os.name != "nt":
        with pytest.raises(ProcessLookupError):
            os.kill(pids[0], 0)


@pytest.mark.parametrize("mode", ["read_only", "plan"])
def test_github_process_forced_readonly_in_restricted_mode(variant, setup, mode):
    registry, broker, workspace = setup
    script = workspace / "github_fixture.py"
    script.write_text(
        "import os, sys\nfrom mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('github-fixture')\n"
        "@server.tool()\ndef get_me() -> dict:\n"
        "    return {'argv': sys.argv, 'readonly': os.getenv('GITHUB_READ_ONLY')}\n"
        "server.run()\n", encoding="utf-8",
    )
    registry["capabilities"]["github"] = {
        "enabled": True, "cost_policy": "free", "command": sys.executable,
        "args": [str(script)], "timeout_seconds": 60, "allow_remote_mutations": True,
    }
    broker.current_prompt = "Use GitHub to inspect my account."
    broker.set_access_mode(variant.ACCESS_MODE_READ_ONLY if mode == "read_only" else variant.ACCESS_MODE_PLAN)

    async def operation(host):
        result = await host.call_tool("call_project_mcp_tool", {
            "server_reference": "capability:github", "tool_name": "get_me", "arguments": {},
        })
        assert not result.isError, {
            "result": result.model_dump(),
            "startup_error": str(host.capability_sessions["github"].ready.exception())
            if host.capability_sessions.get("github") and host.capability_sessions["github"].ready.done()
            else None,
        }
        data = nested_result(result)
        assert "--read-only" in data["argv"]
        assert "--lockdown-mode" in data["argv"]
        assert data["readonly"] == "1"

    execute(variant, broker, workspace, operation)


def test_direct_external_mcp_url_is_network_operation(variant):
    for name in ("list_project_mcp_tools", "call_project_mcp_tool"):
        assert variant._tool_arguments_request_external_network(name, {"server_reference": "https://example.org/mcp"})
        assert not variant._tool_arguments_request_external_network(name, {"server_reference": "http://127.0.0.1:8123/mcp"})


@pytest.mark.parametrize("settings,reason", [
    ({"runtime_env": {"GITHUB_TOKEN": "literal-secret"}}, "runtime_env"),
    ({"runtime_env": {"SERENA_HOME": "{runtime}/../outside"}}, "runtime directory"),
    ({"runtime_env": {"DISABLE_THOUGHT_LOGGING": "true"}}, "runtime_env"),
    ({"runtime_env": []}, "runtime_env"),
    ({"env": {"INVALID=NAME": "PATH"}}, "child variable"),
])
def test_invalid_runtime_environment_rejected_before_spawn(variant, setup, settings, reason):
    registry, broker, workspace = setup
    registry["capabilities"]["serena"] = {
        "enabled": True, "cost_policy": "self_hosted", "command": sys.executable,
        **settings,
    }

    async def operation(host):
        result = await host.call_tool("discover_mcp_capability", {"capability": "serena"})
        assert result.isError
        assert reason in result.structuredContent["reason"]
        assert not host.capability_sessions

    execute(variant, broker, workspace, operation)
