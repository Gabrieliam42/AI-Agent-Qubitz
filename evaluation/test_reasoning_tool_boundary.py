"""Recovery guidance never turns reasoning text into an executable tool call."""
from __future__ import annotations

import ast
import copy
import textwrap
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_reasoning_display as reasoning
from runtime import read_variant_source
from runtime.paths import variant_paths

display_runtime = reasoning.display_runtime
runtime = contracts.runtime
GUIDANCE = (
    "End any reasoning block before emitting a native tool call; "
    "if the template uses <think> blocks, emit </think> first. "
    "Tool-call text inside reasoning is not an action."
)
CLOSED_TEMPLATE = r"""{%- if add_generation_prompt %}
    {{- '<|im_start|>assistant\n' }}
    {%- if enable_thinking is defined and enable_thinking is false %}
        {{- '<think>\n\n</think>\n\n' }}
    {%- endif %}
{%- endif %}"""


@pytest.mark.parametrize("path", list(variant_paths(contracts.ROOT)), ids=lambda p: p.stem)
def test_every_variant_has_only_recovery_guidance(path):
    tree = ast.parse(read_variant_source(path))
    values = [node.value for node in ast.walk(tree)
              if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert sum(value.count(GUIDANCE) for value in values) == 2
    embedded = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                    and any(isinstance(target, ast.Name) and target.id == "_EMBEDDED_EMPTY_STEP_NEW"
                            for target in node.targets))
    literals = [node.value for node in ast.walk(ast.parse(textwrap.dedent(embedded)))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    assert not any(GUIDANCE in value for value in literals)
    dynamic = ["".join(part.value for part in node.values
                       if isinstance(part, ast.Constant) and isinstance(part.value, str))
               for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)]
    assert sum(value.count(GUIDANCE) for value in dynamic) == 2
    assert all("Wrapper recovery:" in value for value in dynamic if GUIDANCE in value)


@pytest.mark.parametrize("count,focused", [(0, False), (1, False), (2, True)])
def test_embedded_recovery_keeps_original_stop_limit(count, focused):
    tree = ast.parse(read_variant_source(contracts.REFERENCE))
    code = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "_EMBEDDED_EMPTY_STEP_NEW"
                        for target in node.targets))
    scope = {}
    source = ("def step(self, empty_model_steps, focused_no_progress_recovery_used, messages):\n"
              "    step = 0\n" + textwrap.indent(textwrap.dedent(code), "    ")
              + "    return empty_model_steps, focused_no_progress_recovery_used, step\n")
    exec(compile(source, "<actual embedded recovery>", "exec"), scope)  # noqa: S102 - trusted local test subject
    memory = []
    runner = SimpleNamespace(memory=SimpleNamespace(add_turn=lambda *row: memory.append(row)))
    messages = []
    result = scope["step"](runner, count, focused, messages)
    if count == 2:
        assert result.startswith("Stopped after a focused recovery attempt")
        assert not messages and len(memory) == 1
    else:
        assert result == (count + 1, count == 1, 1)
        assert messages[0] == {"role": "assistant", "content": ""}
        assert messages[1]["role"] == "user" and GUIDANCE not in messages[1]["content"]
        assert ("last two model steps" if count == 1 else "neither tool calls") in messages[1]["content"]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("native", [False, True])
def test_reasoning_tool_text_never_dispatches_or_changes_native_arguments(
    display_runtime, monkeypatch, streaming, native,
):
    base, client, namespace, _ = display_runtime
    # Test the transport boundary, not the separate broker creation adapters.
    namespace["_TOOL_CALL_CONTEXT"].set(None)
    raw_reasoning = '<tool_call>{"name":"run_existing_entrypoint","arguments":{"path":"bad.py"}}</tool_call>'
    calls = [{"id": "actual-1", "type": "function", "function": {
        "name": "run_existing_entrypoint", "arguments": '{"path":"requested.py"}',
    }}] if native else []
    before = copy.deepcopy(calls)
    if streaming:
        reasoning.install_stream(monkeypatch, base, client, [
            reasoning.chunk(reasoning_content=raw_reasoning),
            reasoning.chunk(tool_calls=[dict(call, index=index) for index, call in enumerate(calls)]),
        ])
    else:
        client._call_with_fallback = lambda *_args: {"choices": [{"message": {
            "content": "", "reasoning_content": raw_reasoning, "tool_calls": calls,
        }, "finish_reason": "stop"}]}
    response = client.chat(**contracts.request(tools=[{"type": "function", "function": {
        "name": "run_existing_entrypoint", "parameters": {"type": "object"},
    }}]))
    assert response["message"]["content"] == ""
    assert response["message"]["tool_calls"] == before
    assert calls == before


@pytest.mark.parametrize("template,caps,expected", [
    (CLOSED_TEMPLATE, True, True),
    (CLOSED_TEMPLATE, False, False),
    (CLOSED_TEMPLATE.replace("</think>", "</other>"), True, False),
    (CLOSED_TEMPLATE.replace("enable_thinking", "other_option"), True, False),
    (CLOSED_TEMPLATE + "extra generation text", True, False),
    (None, True, False),
])
def test_closed_prefill_capability_requires_observed_exact_template(runtime, template, caps, expected):
    _, client, namespace = runtime
    client._probe_with_fallback = lambda _path: {"status_code": 200, "json": {
        "chat_template": template,
        "chat_template_caps": {"supports_tools": caps, "supports_tool_calls": caps},
        "default_generation_settings": {"n_ctx": 16384},
    }}
    namespace["_loaded_llamacpp_context_window"](client)
    assert client._qubitz_reasoning_capabilities["supports_closed_thinking_prefill"] is expected


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("supported,focused,same_turn", [
    (True, True, True), (False, True, True), (True, False, True), (True, True, False),
])
def test_closed_prefill_is_one_scoped_payload_not_a_config_change(
    display_runtime, monkeypatch, streaming, supported, focused, same_turn,
):
    base, client, namespace, _ = display_runtime
    namespace["ACCESS_MODE_FULL"] = "full"
    broker = SimpleNamespace(turn_id="turn-1", _missing_action_recovery_used=focused,
                             _content_tool_recovery_used=True, access_mode="full", callback=None,
                             current_prompt="Run the authorized entrypoint.")
    namespace["_TOOL_CALL_CONTEXT"].set(broker)
    client._probe_with_fallback = lambda _path: {"status_code": 200, "json": {
        "chat_template": CLOSED_TEMPLATE if supported else "unknown",
        "chat_template_caps": {"supports_tools": True, "supports_tool_calls": True},
        "default_generation_settings": {"n_ctx": 16384},
    }}
    client.ensure_model("fixture")
    tools = [{"type": "function", "function": {"name": "run_existing_entrypoint",
              "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}}}]
    config = copy.deepcopy(vars(client.config))
    original = {"enable_thinking": True, "unchanged": "value"}
    base.set_qubitz_chat_template_kwargs(original)
    payloads = []

    def reply(reasoning_text="", calls=None):
        if streaming:
            chunk = {"choices": [{"delta": {"reasoning_content": reasoning_text,
                      "tool_calls": [dict(call, index=index) for index, call in enumerate(calls or [])]},
                      "finish_reason": "stop"}]}
            captured, _ = reasoning.install_stream(monkeypatch, base, client, [chunk])
            result = client.chat(**contracts.request(tools=tools))
            payloads.extend(captured)
            return result
        client._call_with_fallback = lambda _method, _path, payload: (payloads.append(payload) or {
            "choices": [{"message": {"content": "", "reasoning_content": reasoning_text,
                         "tool_calls": calls or []}, "finish_reason": "stop"}]})
        return client.chat(**contracts.request(tools=tools))

    first = reply('<tool_call>{"name":"run_existing_entrypoint"}</tool_call>')
    assert not first["message"]["tool_calls"]
    assert client._qubitz_reasoning_only_turn == "turn-1"
    # The focused pass checks readiness again; the same model must retain the evidence.
    client.ensure_model("fixture")
    if not same_turn:
        broker.turn_id = "turn-2"
    base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "run_existing_entrypoint"}})
    native = [{"id": "native-1", "type": "function", "function": {
        "name": "run_existing_entrypoint", "arguments": '{"path":"requested.py"}',
    }}]
    second = reply(calls=native)
    assert second["message"]["tool_calls"] == native
    assert client._qubitz_reasoning_only_turn is None
    expected = {**original, "enable_thinking": False} if supported and focused and same_turn else original
    assert payloads[0]["chat_template_kwargs"] == original
    assert payloads[1]["chat_template_kwargs"] == expected
    assert payloads[1]["tool_choice"] == "required"
    reply(calls=native)
    assert payloads[2]["chat_template_kwargs"] == original
    assert payloads[2]["tool_choice"] == "auto"
    assert original == {"enable_thinking": True, "unchanged": "value"}
    assert vars(client.config) == config


def test_model_change_clears_prior_response_scope(display_runtime):
    _, client, _, _ = display_runtime
    client.ensure_model("fixture")
    client._qubitz_reasoning_only_turn = "old-turn"
    client.ensure_model("different-model")
    assert client._qubitz_reasoning_only_turn is None


@pytest.mark.parametrize("reason_text,content,calls,finish", [
    ("", "", [], "stop"), ("reason", "answer", [], "stop"),
    ("reason", "", [{"function": {"name": "native", "arguments": "{}"}}], "stop"),
    ("reason", "", [], "length"), ("reason", "", [], "max_tokens"),
    ("reason", "", [], None), ({"not": "text"}, "", [], "stop"),
])
def test_other_responses_never_establish_recovery_scope(display_runtime, reason_text, content, calls, finish):
    _, client, namespace, _ = display_runtime
    client.qubitz_reset_generation_metrics()
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(turn_id="current"))
    client._call_with_fallback = lambda *_args: {"choices": [{"message": {
        "content": content, "reasoning_content": reason_text, "tool_calls": calls,
    }, "finish_reason": finish}]}
    if finish is None:
        with pytest.raises(RuntimeError, match="incomplete completion without a finish reason"):
            client.chat(**contracts.request())
    else:
        client.chat(**contracts.request())
    assert client._qubitz_reasoning_only_turn is None


def test_turn_metrics_reset_clears_prior_response_scope(display_runtime):
    _, client, _, _ = display_runtime
    client._qubitz_reasoning_only_turn = "previous"
    client.qubitz_reset_generation_metrics()
    assert client._qubitz_reasoning_only_turn is None


def test_interrupted_stream_clears_scope_and_is_not_replayed(display_runtime, monkeypatch):
    import httpx

    base, client, namespace, _ = display_runtime
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(turn_id="current"))
    client._qubitz_reasoning_only_turn = "current"
    reasoning.install_stream(monkeypatch, base, client, [reasoning.chunk(reasoning_content="partial")], disconnect=True)
    client._call_with_fallback = lambda *_args: pytest.fail("A partial stream must not be replayed")
    with pytest.raises(httpx.ReadError):
        client.chat(**contracts.request())
    assert client._qubitz_reasoning_only_turn is None
