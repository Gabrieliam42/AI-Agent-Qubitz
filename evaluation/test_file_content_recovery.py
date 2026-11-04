"""Native generation recovery contracts; disposable targets, no GPU or model server."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
import smoke_create_calculator as smoke
import test_context_and_campaign_regressions as contracts
import test_explicit_creation_contracts as creation

from runtime.paths import variant_path

runtime = contracts.runtime
variant = creation.variant
broker = creation.broker
request = contracts.request


TOOLS = [{"type": "function", "function": {"name": "write_file", "parameters": {
    "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"],
}}}]
QWEN = variant_path("AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py", contracts.ROOT)


@pytest.mark.parametrize("prompt", [
    "create a Python script named convert.py that would be a basic unit converter",
    "create a text file named notes.txt containing hello",
])
def test_simple_creation_is_not_bound_to_calculator(variant, broker, prompt):
    broker.begin_turn(prompt, None)
    assert variant._direct_text_creation_choice(broker, TOOLS)["function"]["name"] == "write_file"


@pytest.mark.parametrize("prompt", [
    "Explain how to create a simple script named convert.py",
    "Do not create a simple script named convert.py",
    "create a basic script named convert.py after reading instructions",
    "create a basic script named convert.py after you inspect requirements",
    "create a basic script named convert.py before running the checks",
    "create a basic script named convert.py according to https://example.com",
    "create a simple script named convert.py and run it",
    "create two simple files convert.py and report.py",
    "create a basic report.docx",
    "create a basic script if appropriate",
])
def test_early_creation_does_not_leak(variant, broker, prompt):
    broker.begin_turn(prompt, None)
    assert variant._direct_text_creation_choice(broker, TOOLS) is None


@pytest.mark.parametrize("mode", ["plan", "read_only"])
def test_creation_does_not_change_permission(variant, broker, mode):
    broker.set_access_mode(mode)
    assert variant._direct_text_creation_choice(broker, TOOLS) is None


def test_existing_target_and_missing_tool_are_not_forced(variant, broker):
    assert variant._direct_text_creation_choice(broker, []) is None
    target = next(iter(broker.creation_targets))
    Path(target).write_text("original", encoding="utf-8")
    assert variant._direct_text_creation_choice(broker, TOOLS) is None


def test_budget_grows_only_within_configured_ceiling(variant, broker):
    budget = variant._file_content_output_budget
    assert budget(broker, 128) == 128
    assert 512 < budget(broker, 8192) < 8192
    assert budget(broker, 8192, 2048) == 4096
    assert budget(broker, 3000, 2048) == 3000
    broker.current_prompt += "x" * 2500
    assert budget(broker, 8192) > 1024


def test_complex_new_file_keeps_reasoning_but_can_recover(variant, broker):
    broker.begin_turn("create converter.py that converts Celsius to Fahrenheit", None)
    assert variant._direct_text_creation_choice(broker, TOOLS) is None
    assert variant._direct_text_creation_choice(broker, TOOLS, simple_only=False) is not None
    broker.begin_turn("create converter.py", None)
    assert variant._direct_text_creation_choice(broker, TOOLS, simple_only=False) is None


def test_detailed_named_creation_does_not_use_simple_shortcut(variant, broker):
    prompt = (
        "Create a basic dashboard.py with interactive charts, authentication, detailed "
        "error handling and multiple live data sources in the workspace."
    )
    broker.begin_turn(prompt, None)
    assert variant._direct_text_creation_choice(broker, TOOLS, simple_only=True) is None


def test_llama_general_tool_task_preserves_reasoning(variant, broker):
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"tool_calls": []}, "finish_reason": "stop"}

    class Runner:
        pass

    base = SimpleNamespace(AgentRunner=Runner, LlamaCppClient=Client,
                           set_qubitz_forced_tool_choice=lambda _choice: None)
    state = contextvars.ContextVar("ladder_general", default=None)
    state.set({"callback": None})
    namespace = {
        "_TOOL_CALL_CONTEXT": contextvars.ContextVar("broker_general", default=broker),
        "_direct_text_creation_choice": variant._direct_text_creation_choice,
        "_LLAMA_3_3_TOOL_GENERATION_CONTEXT": state,
        "LLAMA_3_3_NEMOTRON_SUPER_SIMPLE_REASONING_DIRECTIVE": "/no_think",
    }
    contracts.functions(contracts.LLAMA, {
        "_install_llama_3_3_tool_generation_ladder",
        "_llama_3_3_messages_with_reasoning_directive",
    }, namespace)
    namespace["_install_llama_3_3_tool_generation_ladder"](SimpleNamespace(base=base))
    broker.begin_turn(
        "Create a basic dashboard.py with interactive charts, authentication, detailed "
        "error handling and multiple live data sources in the workspace.",
        None,
    )

    client = Client()
    client.chat(**request(tools=TOOLS))

    assert len(calls) == 1
    assert "/no_think" not in str(calls[0]["messages"])
    assert calls[0]["num_predict"] == 16384


def test_llama_forced_mutation_preserves_reasoning(variant, broker):
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"tool_calls": []}, "finish_reason": "stop"}

    class Runner:
        pass

    base = SimpleNamespace(
        AgentRunner=Runner,
        LlamaCppClient=Client,
        set_qubitz_forced_tool_choice=lambda _choice: None,
    )
    state = contextvars.ContextVar("ladder_forced_mutation", default=None)
    state.set({"callback": None})
    namespace = {
        "_TOOL_CALL_CONTEXT": contextvars.ContextVar("broker_forced_mutation", default=broker),
        "_direct_text_creation_choice": variant._direct_text_creation_choice,
        "_LLAMA_3_3_TOOL_GENERATION_CONTEXT": state,
        "LLAMA_3_3_NEMOTRON_SUPER_SIMPLE_REASONING_DIRECTIVE": "/no_think",
    }
    contracts.functions(contracts.LLAMA, {
        "_install_llama_3_3_tool_generation_ladder",
        "_llama_3_3_messages_with_reasoning_directive",
    }, namespace)
    namespace["_install_llama_3_3_tool_generation_ladder"](SimpleNamespace(base=base))
    broker.begin_turn("Edit dashboard.py to add authentication and live data validation", None)
    base.set_qubitz_forced_tool_choice({
        "type": "function", "function": {"name": "apply_text_patch"},
    })
    tools = [{"type": "function", "function": {"name": "apply_text_patch"}}]

    Client().chat(**request(tools=tools))

    assert len(calls) == 1
    assert "/no_think" not in str(calls[0]["messages"])
    assert calls[0]["num_predict"] == 16384


def wire(runtime, variant, broker):
    base, client, namespace = runtime
    namespace.update(_TOOL_CALL_CONTEXT=contextvars.ContextVar("test_broker", default=broker),
                     ACCESS_MODE_FULL=variant.ACCESS_MODE_FULL,
                     _direct_text_creation_choice=variant._direct_text_creation_choice,
                     _scoped_text_creation_choice=variant._scoped_text_creation_choice,
                     _resolve_named_workspace_path=variant._resolve_named_workspace_path,
                     _json_schema_errors=variant._json_schema_errors,
                     _file_content_output_budget=variant._file_content_output_budget,
                     _validate_html_artifact=lambda _content: {"browser_load": "unavailable"},
                      _check_graphical_html_visibility=lambda _content: {"visual_preview": "unavailable"},
                      Path=Path, os=os, ast=ast)
    base.LlamaCppClient._qubitz_streaming_patched = False
    contracts.functions(QWEN, {"_patch_streaming_chat"}, namespace)
    namespace["_patch_streaming_chat"](base)
    client.config.num_predict = 4096

    class Owner:
        _cancel_event = threading.Event()

        def stop(self):
            pass

        def _wrapper_no_progress_tool_choice(self, prompt, answer):
            return {"type": "function", "function": {"name": "read_file_snapshot"}}

    owner = Owner()
    broker.stop_callback = owner.stop
    return base, client, owner


def raw(content="", calls=None, finish="stop"):
    return {"choices": [{"message": {"content": content, "tool_calls": calls or []}, "finish_reason": finish}]}


def scoped_replies(*sections):
    return iter([raw("Outline: make a complete, self-contained artifact."),
                 raw('{"path":', finish="length"), *sections])


def test_simple_creation_uses_schema_without_executing_prose(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    captures = []

    def reply(method, path, payload):
        captures.append(payload)
        return raw("Here is the code: print(4)")

    client._call_with_fallback = reply
    original = request(tools=TOOLS, num_predict=512)
    client.chat(**original)
    assert len(captures) == 2
    assert captures[0]["tool_choice"] == "none"
    assert captures[0]["tools"] == []
    assert captures[0]["response_format"]["type"] == "json_schema"
    assert captures[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in captures[0]
    assert captures[0]["max_tokens"] > 512
    assert captures[0]["max_tokens"] <= client.config.num_predict
    assert "Schema correction" in str(captures[1]["messages"])
    assert len(original["messages"]) == 1
    client.chat(**original)
    assert len(captures) == 3
    assert captures[-1]["tool_choice"] == "auto"
    assert "response_format" not in captures[-1]
    assert "chat_template_kwargs" not in captures[-1]
    assert not any(Path(path).exists() for path in broker.creation_targets)
    broker.begin_turn(broker.current_prompt, None)
    assert not broker._content_tool_recovery_used


def test_complex_creation_keeps_normal_reasoning_payload(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("create converter.py that converts Celsius to Fahrenheit", None)
    calls = [{"function": {"name": "write_file", "arguments": {
        "path": "converter.py", "content": "def convert(value):\n    return value * 9 / 5 + 32\n",
    }}}]
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or raw(calls=calls, finish="tool_calls"))

    response = client.chat(**request(tools=TOOLS))

    assert response["message"]["tool_calls"] == calls
    assert len(seen) == 1
    assert "response_format" not in seen[0]
    assert "chat_template_kwargs" not in seen[0]


def test_named_creation_recovers_after_reasoning_only_tool_text(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    prompt = (
        "New task after cancellation: create wanted.txt containing exactly fresh followed by a newline. "
        "Do not create unwanted.txt."
    )
    broker.begin_turn(prompt, None)
    target = str((broker.workspace / "wanted.txt").resolve())
    assert broker.creation_targets == {target}
    assert variant._direct_text_creation_choice(broker, TOOLS, simple_only=True) is None
    assert variant._direct_text_creation_choice(broker, TOOLS, simple_only=False) is not None
    reasoning_only = raw()
    reasoning_only["choices"][0]["message"]["reasoning_content"] = (
        '<tool_call>{"name":"write_file","arguments":{"path":"unwanted.txt",'
        '"content":"wrong"}}</tool_call>'
    )
    replies = iter([reasoning_only, raw(json.dumps({"path": target, "content": "fresh\n"}))])
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    response = client.chat(**request(tools=TOOLS))

    assert len(seen) == 2
    assert "response_format" not in seen[0]
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert seen[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen[1]["tools"] == []
    assert response["message"]["tool_calls"][0]["function"]["arguments"] == {
        "path": target, "content": "fresh\n",
    }
    assert not (broker.workspace / "wanted.txt").exists()
    assert not (broker.workspace / "unwanted.txt").exists()


def test_native_success_is_not_retried(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("create converter.py that converts Celsius to Fahrenheit", None)
    calls = [{"function": {"name": "write_file", "arguments": {"path": "calculator.py", "content": "print(4)"}}}]
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args) or raw(calls=calls, finish="tool_calls"))
    assert client.chat(**request(tools=TOOLS))["message"]["tool_calls"] == calls
    assert len(seen) == 1


def test_schema_native_call_reaches_real_tool_and_verified_file(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    prompt = "create a simple greeting script named greet.py"
    broker.begin_turn(prompt, None)
    arguments = {"path": str(broker.workspace / "greet.py"), "content": "print('Hello')\n"}
    replies = iter([raw(json.dumps(arguments))])
    client._call_with_fallback = lambda *args: next(replies)
    response = client.chat(**request(tools=TOOLS))
    call = response["message"]["tool_calls"][0]["function"]
    allowed, notice = broker.authorize(call["name"], call["arguments"])
    assert allowed, notice
    server = variant._build_local_mcp_server(variant._APP.base, broker.workspace, broker.workspace, creation.REFERENCE)
    result = asyncio.run(server.call_tool(call["name"], call["arguments"]))
    structured = result[1] if isinstance(result, tuple) else json.loads(next(block.text for block in result if block.type == "text"))
    broker.record_result(call["name"], call["arguments"], SimpleNamespace(isError=False, structuredContent=structured, content=[]))
    assert (broker.workspace / "greet.py").read_text(encoding="utf-8") == arguments["content"]
    assert not broker.completion_report(prompt)[1]


@pytest.mark.parametrize("cancelled,mode", [(True, "full"), (False, "plan"), (False, "read_only")])
def test_no_retry_after_cancel_or_permission_restriction(runtime, variant, broker, cancelled, mode):
    _, client, owner = wire(runtime, variant, broker)
    if cancelled:
        owner._cancel_event.set()
    else:
        broker.set_access_mode(mode)
    client.chat(**request(tools=TOOLS))
    assert len(client.payloads) == 1


def test_truncated_native_arguments_never_escape_even_after_retry(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    seen = []
    partial = [{"function": {"name": "write_file", "arguments": '{"path":"calculator.py","content":"'}}]
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or raw(calls=partial, finish="length"))
    response = client.chat(**request(tools=TOOLS, num_predict=1024))
    assert len(seen) == 4
    assert seen[0]["max_tokens"] == 1024
    assert seen[1]["max_tokens"] == 2048
    assert seen[2]["response_format"]["type"] == "json_schema"
    assert seen[3]["response_format"]["type"] == "json_schema"
    assert response["message"]["tool_calls"] == []
    assert "nothing was written" in response["message"]["content"]


def test_unknown_reasoning_tags_are_never_parsed_as_tools(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    response = raw()
    response["choices"][0]["message"]["reasoning_content"] = '<tool_call>{"name":"write_file"}</tool_call>'
    client._call_with_fallback = lambda *args: response
    assert client.chat(**request(tools=TOOLS))["message"]["tool_calls"] == []


def test_edit_recovery_inspects_before_mutating(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    (broker.workspace / "convert.py").write_text("print(1)\n", encoding="utf-8")
    broker.begin_turn("edit convert.py to print 2 instead", None)
    tools = [{"type": "function", "function": {"name": "read_file_snapshot"}}]
    client.chat(**request(tools=tools))
    assert len(client.payloads) == 2
    assert client.payloads[-1]["tool_choice"] == "required"
    assert [item["function"]["name"] for item in client.payloads[-1]["tools"]] == ["read_file_snapshot"]


@pytest.mark.parametrize("finish", ["length", "max_tokens", "tool_calls"])
def test_streaming_also_discards_truncated_calls(runtime, monkeypatch, finish):
    import httpx

    base, client, _ = runtime
    client.transport = SimpleNamespace(label="direct", base_url="http://test.invalid")
    monkeypatch.setattr(base.LlamaCppClient, "_normalize_tool_calls", staticmethod(lambda calls: calls or []))
    base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "write_file"}})

    class Stream:
        status_code = 200

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            return self

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "write_file", "arguments": "{}"}},
            ]}, "finish_reason": finish}]})
            yield "data: [DONE]"

    monkeypatch.setattr(httpx, "Client", Stream)
    response = client.chat(**request(tools=TOOLS))
    assert bool(response["message"]["tool_calls"]) == (finish == "tool_calls")


def test_specific_creation_installer_skips_small_probe(variant, broker):
    calls = []
    forced = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"tool_calls": [{"function": {"name": "write_file", "arguments": {}}}]}}

        def qubitz_json_chat(self, **kwargs):
            return {}

        def qubitz_file_creation_chat(self, **kwargs):
            calls.append({**kwargs, "schema_creation": True})
            return {"message": {"tool_calls": []}}

    class Runner:
        pass

    base = SimpleNamespace(AgentRunner=Runner, LlamaCppClient=Client,
                           set_qubitz_forced_tool_choice=forced.append)
    state = contextvars.ContextVar("ladder", default=None)
    state.set({"remaining": 1})
    namespace = {
        "_TOOL_CALL_CONTEXT": contextvars.ContextVar("broker", default=broker),
        "_direct_text_creation_choice": variant._direct_text_creation_choice,
        "_file_content_output_budget": variant._file_content_output_budget,
    }
    installer = "_install_llama_3_3_tool_generation_ladder"
    namespace.update(_LLAMA_3_3_TOOL_GENERATION_CONTEXT=state,
                     LLAMA_3_3_NEMOTRON_SUPER_SIMPLE_REASONING_DIRECTIVE="/no_think")
    contracts.functions(contracts.LLAMA, {installer, "_llama_3_3_messages_with_reasoning_directive"}, namespace)
    namespace[installer](SimpleNamespace(base=base))
    client = Client()
    client.chat(**request(tools=TOOLS, num_predict=4096))
    assert len(calls) == 1
    assert 512 < calls[0]["num_predict"] <= 4096
    assert calls[0]["temperature"] == 0.6
    assert forced[0]["function"]["name"] == "write_file"
    assert forced[-1] is None
    assert "/no_think" in str(calls[0]["messages"])


@pytest.mark.parametrize("bad", ["wrong_path", "extra_field", "duplicate", "markdown", "reasoning", "empty", "unfinished"])
def test_schema_creation_rejects_invalid_content(runtime, variant, broker, bad):
    _, client, _ = wire(runtime, variant, broker)
    target = next(iter(broker.creation_targets))
    data = {"path": target, "content": "print(4)\n"}
    if bad == "wrong_path":
        data["path"] = "../unrequested.py"
    elif bad == "extra_field":
        data["overwrite"] = True
    elif bad == "empty":
        data["content"] = ""
    content = json.dumps(data)
    if bad == "duplicate":
        content = content[:-1] + ', "content": "print(8)"}'
    elif bad == "markdown":
        content = "```json\n" + content + "\n```"
    reply = raw(content, finish=None if bad == "unfinished" else "stop")
    if bad == "reasoning":
        reply["choices"][0]["message"] = {"content": "", "reasoning_content": content}
    client._call_with_fallback = lambda *args: reply
    if bad == "unfinished":
        with pytest.raises(RuntimeError, match="without a finish reason"):
            client.qubitz_file_creation_chat(**request(tools=TOOLS))
        assert not Path(target).exists()
        return
    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))
    assert not result["message"]["tool_calls"]
    assert not Path(target).exists()


def test_schema_creation_grows_on_truncation_and_restores_state(runtime, variant, broker):
    base, client, _ = wire(runtime, variant, broker)
    target = next(iter(broker.creation_targets))
    replies = iter([raw('{"path":', finish="length"),
                    raw(json.dumps({"path": target, "content": "print(4)\n"}))])
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))
    base.set_qubitz_forced_tool_choice("auto")
    response = client.qubitz_file_creation_chat(**request(tools=TOOLS))
    assert len(response["message"]["tool_calls"]) == 1
    assert [item["max_tokens"] for item in seen] == [1024, 2048]
    assert all(item["tool_choice"] == "none" for item in seen)
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or raw("ok"))
    client.chat(**request(tools=TOOLS))
    assert "response_format" not in seen[-1]
    assert seen[-1]["tool_choice"] == "auto"


def test_scoped_creation_reasons_then_validates_one_complete_draft(runtime, variant, broker):
    base, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    base.set_qubitz_reasoning_budget(2048)
    base.set_qubitz_reasoning_mode("xhigh")
    base.set_qubitz_chat_template_kwargs({"enable_thinking": True})
    content = "<html><body><canvas></canvas></body></html>"
    replies = iter([raw("Plan the scene and verify its browser load."),
                    raw(json.dumps({"path": "interactive_scene.html", "content": content}))])
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert len(seen) == 2
    assert "response_format" not in seen[0]
    assert seen[0]["reasoning_effort"] == "xhigh"
    assert seen[0]["thinking_budget_tokens"] == 1024
    assert seen[0]["max_tokens"] <= 2048
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert seen[1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in seen[1]
    assert seen[1]["max_tokens"] <= 4096
    assert "Plan the scene" in str(seen[1]["messages"])
    assert result["message"]["tool_calls"][0]["function"]["arguments"]["content"] == content
    assert not (broker.workspace / "interactive_scene.html").exists()
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or raw("ok"))
    client.chat(**request())
    assert seen[-1]["thinking_budget_tokens"] == 2048


def test_scoped_one_shot_repairs_invalid_draft_before_write(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    namespace["_validate_html_artifact"] = lambda content: (
        (_ for _ in ()).throw(ValueError("invalid JavaScript token"))
        if "bad()" in content else {"browser_load": "passed"}
    )
    bad = "<script>bad()</script>"
    good = "<script>good()</script>"
    replies = iter([raw("Plan the implementation."),
                     raw(json.dumps({"path": "interactive_scene.html", "content": bad})),
                     raw(json.dumps({"old_text": "bad()", "new_text": "good()"}))])
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert len(seen) == 3
    assert "invalid JavaScript token" in str(seen[2]["messages"])
    assert "bad()" in str(seen[2]["messages"])
    schema = seen[2]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"patches"}
    item = schema["properties"]["patches"]["items"]
    assert set(item["properties"]) == {"old_text", "new_text", "old_offset"}
    assert set(item["required"]) == {"old_text", "new_text"}
    assert result["message"]["tool_calls"][0]["function"]["arguments"]["content"] == good
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_named_one_shot_repairs_invalid_draft_before_write(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a basic HTML file named page.html", None)
    target = str((broker.workspace / "page.html").resolve())
    assert variant._direct_text_creation_choice(broker, TOOLS) is not None
    namespace["_validate_html_artifact"] = lambda content: (
        (_ for _ in ()).throw(ValueError("invalid JavaScript token"))
        if "bad()" in content else {"browser_load": "passed"}
    )
    replies = iter([
        raw(json.dumps({"path": target, "content": "<script>bad()</script>"})),
        raw(json.dumps({"old_text": "bad()", "new_text": "good()"})),
    ])
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert result["message"]["tool_calls"][0]["function"]["arguments"] == {
        "path": target, "content": "<script>good()</script>",
    }
    assert not Path(target).exists()


def test_scoped_one_shot_blocks_second_invalid_draft(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    namespace["_validate_html_artifact"] = lambda _content: (
        _ for _ in ()
    ).throw(ValueError("invalid JavaScript token"))
    bad = raw(json.dumps({"path": "interactive_scene.html", "content": "<script>bad()</script>"}))
    replies = iter([raw("Plan the implementation."), bad,
                    raw(json.dumps({"old_text": "bad()", "new_text": "bad()"}))])
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert not result["message"]["tool_calls"]
    assert "local repair failed" in result["message"]["content"]
    assert broker.creation_targets == set()
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_stages_sections_then_returns_one_write(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": "<html><body>", "complete": False})),
        raw(json.dumps({"path": "interactive_scene.html", "content": "</body></html>", "complete": True})),
    )
    seen = []

    def reply(*args):
        assert not (broker.workspace / "interactive_scene.html").exists()
        seen.append(args[-1])
        return next(replies)

    client._call_with_fallback = reply
    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))
    calls = result["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "write_file"
    assert calls[0]["function"]["arguments"]["content"] == "<html><body></body></html>"
    assert len(seen) == 4
    assert "response_format" not in seen[0]
    assert seen[1]["response_format"]["type"] == "json_schema"
    assert all(item["max_tokens"] <= 4096 for item in seen[2:])
    assert all(item["response_format"]["json_schema"]["schema"]["properties"]["content"]["maxLength"] == 3072
               for item in seen[2:])
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_accepts_complete_oversized_section(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    content = "<html><body>" + "x" * 3500 + "</body></html>"
    seen = []
    replies = scoped_replies(raw(json.dumps({
        "path": "interactive_scene.html", "content": content, "complete": True,
    })))
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert len(seen) == 3
    assert seen[2]["response_format"]["json_schema"]["schema"]["properties"]["content"]["maxLength"] == 3072
    assert result["message"]["tool_calls"][0]["function"]["arguments"]["content"] == content
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_accepts_oversized_intermediate_section(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    first = "<html><body>" + "x" * 3500
    second = "</body></html>"
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": first, "complete": False})),
        raw(json.dumps({"path": "interactive_scene.html", "content": second, "complete": True})),
    )
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert result["message"]["tool_calls"][0]["function"]["arguments"]["content"] == first + second
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_keeps_total_staging_cap(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    client.loaded = 131072
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": "x" * 120000, "complete": False})),
        raw(json.dumps({"path": "interactive_scene.html", "content": "y" * 40001, "complete": True})),
    )
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert not result["message"]["tool_calls"]
    assert "bounded staging capacity exceeded" in result["message"]["content"]
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_retries_only_truncated_section(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    replies = scoped_replies(
        raw('{"path":"interactive_scene.html","content":"', finish="length"),
        raw(json.dumps({"path": "interactive_scene.html", "content": "<canvas></canvas>", "complete": True})),
    )
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert result["message"]["tool_calls"][0]["function"]["name"] == "write_file"
    assert len(seen) == 4
    assert seen[3]["response_format"]["json_schema"]["schema"]["properties"]["content"]["maxLength"] == 1536
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_repairs_failed_artifact_before_write(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)

    def validate(content):
        if "missing.js" in content:
            raise ValueError("HTML dependency is missing (HTTP 404): https://example.test/missing.js")
        return {"browser_load": "unavailable"}

    namespace["_validate_html_artifact"] = validate
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": '<script src="missing.js"></script>', "complete": True})),
        raw(json.dumps({"old_text": '<script src="missing.js"></script>', "new_text": "<canvas></canvas>"})),
    )
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    call = result["message"]["tool_calls"][0]["function"]
    assert call["arguments"]["content"] == "<canvas></canvas>"
    assert len(seen) == 4
    assert "HTTP 404" in str(seen[3]["messages"])
    assert "missing.js" in str(seen[3]["messages"])
    assert broker._file_creation_blocker == ""
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_creation_rejects_second_invalid_artifact(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    namespace["_validate_html_artifact"] = lambda _content: (_ for _ in ()).throw(ValueError("invalid HTML"))
    reply = raw(json.dumps({"path": "interactive_scene.html", "content": "<canvas></canvas>", "complete": True}))
    replies = scoped_replies(reply, raw(json.dumps({"old_text": "<canvas>", "new_text": "<script>"})))
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert not result["message"]["tool_calls"]
    assert "local repair failed" in result["message"]["content"]
    assert "complete repaired file failed validation" in broker._file_creation_blocker
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_repair_targets_middle_of_large_draft_without_rewriting_it(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    broken = "https://cdn.example.test/missing.js"
    fixed = "https://cdn.example.test/working.js"
    draft = "<html>" + "a" * 9000 + broken + "b" * 9000 + "</html>"

    def validate(content):
        if broken in content:
            raise ValueError(f"HTML dependency is missing (HTTP 404): {broken}")
        return {"browser_load": "passed"}

    namespace["_validate_html_artifact"] = validate
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": draft, "complete": True})),
        raw(json.dumps({"old_text": broken, "new_text": fixed})),
    )
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert result["message"]["tool_calls"][0]["function"]["arguments"]["content"] == draft.replace(broken, fixed)
    assert broken in str(seen[-1]["messages"])
    assert len(str(seen[-1]["messages"])) < len(draft)
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_scoped_repair_rejects_ambiguous_patch_target(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    namespace = runtime[2]
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    namespace["_validate_html_artifact"] = lambda _content: (
        _ for _ in ()
    ).throw(ValueError("invalid JavaScript token"))
    replies = scoped_replies(
        raw(json.dumps({"path": "interactive_scene.html", "content": "<script>bad();bad()</script>", "complete": True})),
        raw(json.dumps({"old_text": "bad()", "new_text": "good()"})),
    )
    client._call_with_fallback = lambda *args: next(replies)

    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))

    assert "patch target is not unique" in result["message"]["content"]
    assert not (broker.workspace / "interactive_scene.html").exists()


def test_graphical_preview_rejects_blank_canvas_but_not_unavailable(variant, monkeypatch):
    seen = []

    def probe(*args, **kwargs):
        seen.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps({"status": "blank"}), returncode=0)

    monkeypatch.setattr(variant.subprocess, "run", probe)
    with pytest.raises(ValueError, match="blank visible canvas"):
        variant._check_graphical_html_visibility("<canvas></canvas>")
    assert "canvas.screenshot" in seen[0][0][0][2]

    monkeypatch.setattr(
        variant.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps({"status": "unavailable"}), returncode=0),
    )
    assert variant._check_graphical_html_visibility("<canvas></canvas>")["visual_preview"] == "unavailable"


def test_scoped_creation_never_writes_truncated_or_changed_path(runtime, variant, broker):
    _, client, _ = wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    first = raw(json.dumps({"path": "interactive_scene.html", "content": "<html>", "complete": False}))
    for second in (
        raw('{"path":"interactive_scene.html","content":"<body>"', finish="length"),
        raw(json.dumps({"path": "other.html", "content": "</html>", "complete": True})),
    ):
        broker.begin_turn(broker.current_prompt, None)
        replies = scoped_replies(first, second, second) if second["choices"][0]["finish_reason"] == "length" \
            else scoped_replies(first, second)

        def reply(*args, iterator=replies):
            return next(iterator)

        client._call_with_fallback = reply
        result = client.qubitz_file_creation_chat(**request(tools=TOOLS))
        assert not result["message"]["tool_calls"]
        assert not (broker.workspace / "interactive_scene.html").exists()


@pytest.mark.parametrize("change", ["cancel", "permission", "file_appeared"])
def test_schema_result_rechecks_live_permissions_and_target(runtime, variant, broker, change):
    _, client, owner = wire(runtime, variant, broker)
    target = Path(next(iter(broker.creation_targets)))

    def reply(*args):
        if change == "cancel":
            owner._cancel_event.set()
        elif change == "permission":
            broker.set_access_mode("read_only")
        else:
            target.write_text("user content", encoding="utf-8")
        return raw(json.dumps({"path": str(target), "content": "print(4)\n"}))

    client._call_with_fallback = reply
    assert not client.qubitz_file_creation_chat(**request(tools=TOOLS))["message"]["tool_calls"]
    if change == "file_appeared":
        assert target.read_text(encoding="utf-8") == "user content"


def test_streamed_schema_payload_matches_nonstreaming_and_hides_partial_json(runtime, variant, broker, monkeypatch):
    import httpx

    base, client, _ = wire(runtime, variant, broker)
    target = next(iter(broker.creation_targets))
    encoded = json.dumps({"path": target, "content": "print(4)\n"})
    captured = []
    client._call_with_fallback = lambda *args: (captured.append(args[-1]) or raw(encoded))
    client.qubitz_file_creation_chat(**request(tools=TOOLS))
    broker.begin_turn(broker.current_prompt, None)
    deltas = []
    base.set_qubitz_stream_callback(deltas.append)
    client.transport = SimpleNamespace(label="direct", base_url="http://test.invalid")
    monkeypatch.setattr(base.LlamaCppClient, "_normalize_tool_calls", staticmethod(lambda calls: calls or []))

    class Stream:
        status_code = 200

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def stream(self, *args, **kwargs):
            captured.append(kwargs["json"])
            return self

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": encoded}, "finish_reason": "stop"}]})
            yield "data: [DONE]"

    monkeypatch.setattr(httpx, "Client", Stream)
    result = client.qubitz_file_creation_chat(**request(tools=TOOLS))
    assert result["message"]["tool_calls"][0]["function"]["arguments"]["path"] == target
    assert not deltas
    left, right = ({key: value for key, value in payload.items() if key not in {"stream", "stream_options"}}
                   for payload in captured)
    assert left == right


def test_cleanup_waits_and_refuses_unobserved_server(monkeypatch):
    server = {"ProcessId": 42, "CreationDate": "123456", "ExecutablePath": "unowned-server.exe"}
    monkeypatch.setattr(smoke, "model_processes", lambda: [server])
    monkeypatch.setattr(smoke, "stop_verified_server", lambda p: pytest.fail("Must not stop unowned PID"))
    with pytest.raises(RuntimeError, match="Unowned"):
        smoke.wait_for_owned_cleanup([])


@pytest.mark.parametrize("outcome", ["gone", "unknown", "persistent"])
def test_cleanup_retries_incomplete_metadata_without_terminating_it(monkeypatch, outcome):
    clock = [0.0]
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(smoke, "port_free", lambda: True)
    monkeypatch.setattr(smoke, "require_idle", lambda: None)
    monkeypatch.setattr(smoke, "stop_verified_server", lambda row: pytest.fail("Unverified identity"))

    def inventory():
        if clock[0] < 1 or outcome == "persistent":
            raise smoke.IncompleteProcessIdentityError("incomplete snapshot")
        if outcome == "unknown":
            return [{"ProcessId": 42, "CreationDate": "123456", "ExecutablePath": "unowned-server.exe"}]
        return []

    monkeypatch.setattr(smoke, "model_processes", inventory)
    if outcome == "gone":
        result = smoke.wait_for_owned_cleanup([], timeout=6)
        assert result == {"verified_idle": True, "metadata_retries": 2}
        assert clock[0] >= 5
    else:
        with pytest.raises(RuntimeError, match="Unowned" if outcome == "unknown" else "did not settle"):
            smoke.wait_for_owned_cleanup([], timeout=3)
        assert clock[0] <= 3


@pytest.mark.parametrize("method", ["chat", "qubitz_json_chat"])
def test_user_cancellation_prevents_transport_calls_even_if_event_was_cleared(runtime, variant, broker, method):
    _, client, owner = wire(runtime, variant, broker)
    owner._cancel_source = "user"
    owner._cancel_event.clear()
    with pytest.raises(asyncio.CancelledError):
        if method == "chat":
            client.chat(**request(tools=TOOLS))
        else:
            client.qubitz_json_chat(model_name="fixture", messages=[], schema_name="test",
                                   schema={"type": "object"}, num_predict=512)
    assert not client.payloads


@pytest.mark.parametrize("cancel_at", ["model", "recovery"])
def test_real_runner_cancellation_prevents_following_recovery(variant, broker, monkeypatch, cancel_at):
    base = variant._APP.base
    hardened = next(cls for cls in base.AgentRunner.__mro__ if cls.__name__ == "HardenedAgentRunner")
    runner = hardened(base.AgentConfig(workspace=broker.workspace, runtime_workspace=creation.ROOT))
    runner.workspace = broker.workspace
    runner.history = []
    runner.memory = SimpleNamespace(turns=[], add_turn=lambda *args: None, build_context=lambda _prompt: "")
    runner.retriever = SimpleNamespace(format_context=lambda _prompt: "")
    runner.discovery_memory = SimpleNamespace(context_for=lambda prompt: "", observe_tool_evidence=lambda *args: None)
    runner._tool_permission_broker = broker
    runner._cancel_event = threading.Event()
    runner._wrapper_interrupt_event = threading.Event()
    runner.config = SimpleNamespace(model_name="fixture", thinking_effort="medium")
    runner.access_mode = variant.ACCESS_MODE_FULL
    runner._decide_route = lambda _prompt: SimpleNamespace(selected_route="retrieval_plus_model", features={})
    runner._build_focused_file_context = lambda _prompt: ""
    runner._build_metadata_or_lexical_context = lambda _prompt: ""
    runner._should_skip_repo_retrieval = lambda _prompt: False
    runner._selected_route_name = lambda prompt: "retrieval_plus_model"
    runner._social_fast_path_response = lambda prompt: None
    runner._effort_step_budget_profile = lambda: (16, 8, 48)
    runner._effective_max_steps = lambda: 48
    runner._read_only_external_path_preflight = lambda prompt: None
    runner._enforce_research_response_urls = lambda prompt, answer, callback: answer
    runner._compact_history = lambda: None
    observed = []

    def cancel():
        runner._cancel_source = "user"
        runner._cancel_partial_answer = "Cancelled with existing progress preserved."
        runner._cancel_event.set()

    async def empty(*args):
        return None

    async def model(self, prompt, callback):
        observed.append("model")
        if cancel_at == "model":
            cancel()
        return ""

    async def verify(*args):
        observed.append("verification")
        if cancel_at == "recovery":
            cancel()

    async def forbidden(*args):
        pytest.fail("A recovery pass ran after explicit cancellation")

    monkeypatch.setattr(hardened.__bases__[0], "_run_async", model)
    runner._dependency_install_preflight = empty
    runner._empty_workspace_listing_preflight = empty
    runner._named_file_operation_preflight = empty
    runner._wrapper_mcp_lifecycle_recovery = empty
    runner._prefetch_named_source_context = empty
    runner._wrapper_verify_committed_transaction = verify
    runner._wrapper_docx_edit_recovery = forbidden
    runner._wrapper_transaction_proposal_recovery = forbidden
    try:
        result = asyncio.run(hardened._run_async(runner, creation.CREATE))
    finally:
        runner.reset_runtime()
    assert "not completed" in result
    assert observed == (["model"] if cancel_at == "model" else ["model", "verification"])


def test_cleanup_requires_stable_quiet_interval(monkeypatch):
    clock = [0.0]
    observed = []
    monkeypatch.setattr(smoke.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(smoke, "model_processes", list)
    monkeypatch.setattr(smoke, "port_free", lambda: clock[0] >= 1)
    monkeypatch.setattr(smoke, "require_idle", lambda: observed.append(clock[0]))
    smoke.wait_for_owned_cleanup([])
    assert observed and observed[0] >= 5


@pytest.mark.parametrize("changed", ["pid", "created", "path"])
def test_same_model_or_cache_location_does_not_prove_ownership(monkeypatch, changed):
    original = {"ProcessId": 42, "CreationDate": "123456", "ExecutablePath": str(smoke.ROOT / ".cache" / "llama-server.exe")}
    other = dict(original)
    other[{"pid": "ProcessId", "created": "CreationDate", "path": "ExecutablePath"}[changed]] = {
        "pid": 43, "created": "123457", "path": str(smoke.ROOT / ".cache" / "other" / "llama-server.exe"),
    }[changed]
    monkeypatch.setattr(smoke, "model_processes", lambda: [other])
    monkeypatch.setattr(smoke, "stop_verified_server", lambda row: pytest.fail("unowned process"))
    with pytest.raises(RuntimeError, match="Unowned"):
        smoke.wait_for_owned_cleanup([original])


def test_process_inventory_is_explicitly_an_array(monkeypatch):
    commands = []
    monkeypatch.setattr(smoke, "powershell", lambda script: (commands.append(script) or "[]"))
    assert smoke.model_processes() == []
    assert "ConvertTo-Json -InputObject $rows" in commands[0]
    monkeypatch.setattr(smoke, "powershell", lambda script: "null")
    with pytest.raises(RuntimeError, match="inventory"):
        smoke.model_processes()


def test_termination_rechecks_creation_time_in_powershell(monkeypatch):
    server = {"ProcessId": 42, "CreationDate": "123456", "ExecutablePath": str(smoke.ROOT / ".cache" / "llama-server.exe")}
    commands = []
    monkeypatch.setattr(smoke, "model_processes", lambda: [server])
    monkeypatch.setattr(smoke, "powershell", commands.append)
    smoke.stop_verified_server(server)
    assert "Ticks.ToString() -ne '123456'" in commands[0]
    assert "ReturnValue -ne 0" in commands[0]


def test_cutoff_diagnostics_do_not_claim_hang(tmp_path):
    path = tmp_path / "requests.jsonl"
    path.write_text(json.dumps({"time": 95, "kind": "http_stream_line"}) + "\n", encoding="utf-8")
    assert smoke.cutoff_activity(path, 100) == "recent_generation_output_at_cutoff_not_proven_hang"
    assert smoke.cutoff_activity(path, 200) == "no_recent_generation_output_hang_not_established"
