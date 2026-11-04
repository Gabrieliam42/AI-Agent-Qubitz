"""Ling's captured envelope failure and generic dispatch contracts; no model loads."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import copy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import read_variant_source

variant = receipts.variant

SHA = "f5d56d7d3023d97c5d4417edfc7b5fcd80a801a84bc1181f16923fe9c0d776c7"
PATCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["path", "expected_sha256", "replacements"],
    "properties": {
        "path": {"type": "string"}, "expected_sha256": {"type": "string"},
        "replacements": {"type": "array", "items": {
            "type": "object", "required": ["old_text", "new_text"],
            "properties": {"old_text": {"type": "string"}, "new_text": {"type": "string"}},
        }},
    },
}


@pytest.fixture
def captured():
    # Verbatim arguments from pass 2's audit event at 2026-10-02T15:46:56.
    return json.loads((Path(__file__).parent / "fixtures/ling_tool_argument_envelope.json").read_text())


def embedded_node(variant, kind, name, namespace):
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    node = next(item for item in ast.walk(tree) if isinstance(item, kind) and item.name == name)
    module = ast.Module(body=[ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0,
    ), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), variant.__file__, "exec"), namespace)  # noqa: S102 - local test subject
    return namespace[name]


@pytest.fixture
def broker(variant, tmp_path):
    class Owner:
        _cancel_event = threading.Event()

        def stop(self, *_args):
            pass

    instance = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    instance.begin_turn("Edit state.txt, replacing alpha with beta.", None)
    instance.stop_callback = Owner().stop
    return instance


def record_snapshot(broker, target):
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("read_file_snapshot", {"path": target.name}, SimpleNamespace(
        isError=False, structuredContent={"path": target.name, "sha256": digest}, content=[],
    ))
    return digest


def host_for(variant, broker, server=None):
    class Parent:
        def __init__(self):
            self.dispatched = []

        async def call_tool(self, name, arguments):
            self.dispatched.append((name, copy.deepcopy(arguments)))
            if server is None:
                return variant._APP.base.mcp_types.CallToolResult(content=[], structuredContent={"status": "ok"})
            result = await server.call_tool(name, arguments)
            structured = result[1] if isinstance(result, tuple) else json.loads(
                next(block.text for block in result if block.type == "text")
            )
            return variant._APP.base.mcp_types.CallToolResult(content=[], structuredContent=structured)

    namespace = dict(vars(variant), base=variant._APP.base, original_host=Parent)
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("boundary_host_broker", default=broker)
    host = embedded_node(variant, ast.ClassDef, "HardenedMCPHost", namespace)()
    host._qubitz_tool_contracts = {
        "apply_text_patch": SimpleNamespace(inputSchema=PATCH_SCHEMA, outputSchema=None),
    }
    if server is not None:
        host._qubitz_local_patch_server = (broker.workspace.resolve(), server)
    local_dispatch = host._call_broker_bound_patch_tool

    async def record_local_dispatch(name, arguments, active_broker):
        host.dispatched.append((name, copy.deepcopy(arguments)))
        token = variant._TOOL_CALL_CONTEXT.set(active_broker)
        try:
            return await local_dispatch(name, arguments, active_broker)
        finally:
            variant._TOOL_CALL_CONTEXT.reset(token)

    host._call_broker_bound_patch_tool = record_local_dispatch
    return host


def test_exact_captured_arguments_decode_without_mutating_input(variant, captured):
    before = copy.deepcopy(captured)
    repaired, changes, errors = variant._repair_tool_argument_transport(captured, PATCH_SCHEMA)
    assert not errors
    assert len(changes) == 2
    assert repaired == {"path": "slug.py", "expected_sha256": SHA, "replacements": captured["replacements"]}
    assert captured == before
    assert not variant._repair_tool_argument_transport(repaired, PATCH_SCHEMA)[1]


def test_nested_batch_and_referenced_schema_decode(variant, captured):
    schema = {"type": "object", "properties": {"patches": {
        "type": "array", "items": {"$ref": "#/$defs/patch"},
    }}, "$defs": {"patch": PATCH_SCHEMA}}
    repaired, _, errors = variant._repair_tool_argument_transport({"patches": [captured]}, schema)
    assert not errors
    assert repaired["patches"][0]["path"] == "slug.py"


@pytest.mark.parametrize("damage", ["conflict", "missing", "unknown", "duplicate"])
def test_ambiguous_envelopes_are_not_silently_stripped(variant, captured, damage):
    if damage == "conflict":
        captured["replacements"][0]["new_text"] = "different requested replacement"
    elif damage == "missing":
        captured.pop("replacements")
    elif damage == "unknown":
        captured["path"] = captured["path"].replace("<arg_key>expected_sha256", "<arg_key>not_a_field")
    else:
        captured["path"] += "</arg_value><arg_key>expected_sha256</arg_key><arg_value>" + SHA
    repaired, _, errors = variant._repair_tool_argument_transport(captured, PATCH_SCHEMA)
    assert errors
    assert repaired["path"] == captured["path"]


def test_literal_envelope_markup_in_content_and_replacements_is_unchanged(variant):
    text = "</arg_value><arg_key>path</arg_key><arg_value>example.html"
    creation_schema = {"type": "object", "properties": {
        "path": {"type": "string"}, "content": {"type": "string"},
    }}
    arguments = {"path": "example.html", "content": text}
    assert variant._repair_tool_argument_transport(arguments, creation_schema) == (arguments, [], [])
    arguments = {"path": "state.txt", "expected_sha256": SHA,
                 "replacements": [{"old_text": text, "new_text": text + "!"}]}
    assert variant._repair_tool_argument_transport(arguments, PATCH_SCHEMA) == (arguments, [], [])


@pytest.mark.parametrize("tool,suffix", [
    ("apply_text_patch", ".txt"), ("apply_docx_text_patch", ".docx"),
    ("apply_notebook_text_patch", ".ipynb"), ("apply_spreadsheet_cell_patch", ".xlsx"),
])
def test_explicit_stale_hash_is_rejected_and_preserved(variant, broker, tool, suffix):
    target = broker.workspace / ("state" + suffix)
    target.write_bytes(b"alpha\n")
    broker.begin_turn(f"Edit {target.name}, replacing alpha with beta.", None)
    record_snapshot(broker, target)
    arguments = {"path": target.name, "expected_sha256": "0" * 64,
                 "replacements": [{"old_text": "alpha", "new_text": "beta"}]}
    before = copy.deepcopy(arguments)
    allowed, notice = broker.authorize(tool, arguments)
    assert not allowed and notice["status"] == "stale_snapshot"
    assert arguments == before and target.read_bytes() == b"alpha\n"


def test_real_dispatch_repairs_envelope_then_rejects_stale_hash_without_write(variant, broker, captured):
    target = broker.workspace / "state.txt"
    target.write_bytes(b"alpha\n")
    current = record_snapshot(broker, target)
    arguments = {"path": captured["path"].replace("slug.py", "state.txt").replace(SHA, "0" * 64),
                 "expected_sha256": captured["expected_sha256"].replace(SHA, "0" * 64),
                 "replacements": captured["replacements"]}
    host = host_for(variant, broker)
    result = asyncio.run(host.call_tool("apply_text_patch", arguments))
    assert result.isError and result.structuredContent["status"] == "stale_snapshot"
    assert result.structuredContent["expected_sha256"] == "0" * 64
    assert result.structuredContent["current_sha256"] == current
    assert not host.dispatched and target.read_bytes() == b"alpha\n"


def test_unrepairable_path_never_reaches_dispatch(variant, broker, captured):
    captured["replacements"][0]["new_text"] = "conflicting replacement"
    host = host_for(variant, broker)
    result = asyncio.run(host.call_tool("apply_text_patch", captured))
    assert result.isError and result.structuredContent["status"] == "invalid_arguments"
    assert not host.dispatched


def test_clean_native_patch_reaches_real_local_tool_and_changes_file(variant, broker):
    target = broker.workspace / "state.txt"
    target.write_bytes(b"alpha\n")
    current = record_snapshot(broker, target)
    server = variant._build_local_mcp_server(variant._APP.base, broker.workspace, broker.workspace, Path(variant.__file__))
    host = host_for(variant, broker, server)
    arguments = {"path": "state.txt", "expected_sha256": current,
                 "replacements": [{"old_text": "alpha", "new_text": "beta"}]}
    result = asyncio.run(host.call_tool("apply_text_patch", arguments))
    assert not result.isError
    assert result.structuredContent["new_sha256"] == hashlib.sha256(b"beta\n").hexdigest()
    assert target.read_bytes() == b"beta\n"
    assert host.dispatched[0][1]["expected_sha256"] == current


def test_captured_envelope_reaches_real_patch_and_fixes_content(variant, broker, captured):
    target = broker.workspace / "slug.py"
    source = "import re\n\ndef slugify(value):\n" + captured["replacements"][0]["old_text"] + "\n"
    target.write_text(source, encoding="utf-8")
    broker.begin_turn("Fix slug.py and run the tests.", None)
    current = record_snapshot(broker, target)
    arguments = json.loads(json.dumps(captured).replace(SHA, current))
    server = variant._build_local_mcp_server(variant._APP.base, broker.workspace, broker.workspace, Path(variant.__file__))
    host = host_for(variant, broker, server)
    result = asyncio.run(host.call_tool("apply_text_patch", arguments))
    assert not result.isError
    assert target.read_text() == source.replace(
        captured["replacements"][0]["old_text"], captured["replacements"][0]["new_text"],
    )
    assert host.dispatched[0][1]["path"] == "slug.py"
    assert host.dispatched[0][1]["expected_sha256"] == current


@pytest.mark.parametrize("serialized", [False, True])
def test_native_chat_repairs_capture_without_second_generation(variant, broker, captured, serialized):
    calls = [{"id": "captured", "function": {"name": "apply_text_patch", "arguments":
              json.dumps(captured) if serialized else captured}}]
    original = {"message": {"content": "", "tool_calls": calls}}
    seen = []

    def chat(_client, **kwargs):
        seen.append(kwargs)
        return original

    namespace = dict(vars(variant), base=variant._APP.base, original_chat=chat)
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("boundary_chat_broker", default=broker)
    function = embedded_node(variant, ast.FunctionDef, "one_tool_chat", namespace)
    response = function(None, tools=[{"function": {"name": "apply_text_patch", "parameters": PATCH_SCHEMA}}])
    arguments = response["message"]["tool_calls"][0]["function"]["arguments"]
    if serialized:
        arguments = json.loads(arguments)
    assert arguments["path"] == "slug.py" and arguments["expected_sha256"] == SHA
    assert len(seen) == 1 and calls[0]["function"]["arguments"] != arguments


@pytest.mark.parametrize("result_kind", ["valid", "changed_content", "changed_hash", "truncated", "cancelled"])
def test_schema_correction_is_bounded_and_cannot_change_valid_arguments(variant, broker, captured, result_kind):
    # A bound callback exposes its owner, exactly as a runner does.
    owner_type = type("Owner", (), {"stop": lambda self, *args: None})
    owner = owner_type()
    owner._cancel_event = threading.Event()
    broker.stop_callback = owner.stop
    captured["replacements"][0]["new_text"] = "explicit different replacement"
    clean = {"path": "slug.py", "expected_sha256": SHA, "replacements": captured["replacements"]}
    correction = copy.deepcopy(clean)
    if result_kind == "changed_content":
        correction["replacements"] = []
    elif result_kind == "changed_hash":
        correction["expected_sha256"] = "0" * 64
    elif result_kind == "cancelled":
        owner._cancel_event.set()
    broken = {"message": {"tool_calls": [{"id": "a", "function": {
        "name": "apply_text_patch", "arguments": captured,
    }}]}}
    seen = []

    def chat(_client, **kwargs):
        seen.append(kwargs)
        if kwargs.get("_qubitz_file_creation_schema") is not None:
            return {"message": {"content": json.dumps(correction)},
                    "finish_reason": "length" if result_kind == "truncated" else "stop"}
        return broken

    namespace = dict(vars(variant), base=variant._APP.base, original_chat=chat)
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("bounded_boundary_broker", default=broker)
    function = embedded_node(variant, ast.FunctionDef, "one_tool_chat", namespace)
    kwargs = {"messages": [{"role": "user", "content": "Edit slug.py"}], "num_predict": 4096,
              "tools": [{"function": {"name": "apply_text_patch", "parameters": PATCH_SCHEMA}}]}
    response = function(None, **kwargs)
    count = 1 if result_kind == "cancelled" else 2
    assert len(seen) == count
    if count == 2:
        expected_schema = copy.deepcopy(PATCH_SCHEMA)
        expected_schema["properties"]["replacements"]["items"]["additionalProperties"] = False
        assert seen[1]["tools"] == [] and seen[1]["_qubitz_file_creation_schema"] == expected_schema
        assert "additionalProperties" not in PATCH_SCHEMA["properties"]["replacements"]["items"]
        assert 0 < seen[1]["num_predict"] <= 4096
    assert response["message"]["tool_calls"][0]["function"]["arguments"] == (
        clean if result_kind == "valid" else captured
    )
    assert not variant._preserves_valid_tool_arguments(captured, {**clean, "expected_sha256": "0" * 64})
    assert not variant._preserves_valid_tool_arguments(captured, {**clean, "replacements": []})
    function(None, **kwargs)
    assert len(seen) == count + 1
    broker.begin_turn("Edit slug.py", None)
    assert not broker._tool_argument_recovery_used
