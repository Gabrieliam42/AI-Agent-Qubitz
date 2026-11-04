"""CPU-only file-grammar and HTTP error contracts; no model or user files."""
from __future__ import annotations

import ast
import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
import test_context_and_campaign_regressions as contracts
import test_file_content_recovery as content

from runtime import read_variant_source
from runtime.paths import variant_path

runtime = contracts.runtime
variant = content.variant
broker = content.broker
LING = variant_path("AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py", contracts.ROOT)


def raw_response(text='{"path":"draft.txt","content":"hello"}'):
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def schema():
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "path": {"type": "string", "const": "draft.txt"},
            "content": {"type": "string", "minLength": 1, "maxLength": 3072},
            "small": {"type": "string", "maxLength": 1536},
            "nested": {"type": "array", "items": {
                "type": "object", "properties": {
                    "patch": {"type": "string", "minLength": 1, "maxLength": 4096},
                    "at_limit": {"type": "string", "maxLength": 2000},
                    "below_limit": {"type": "string", "maxLength": 1999},
                },
            }},
        },
        "required": ["path", "content"],
    }


@pytest.fixture
def ling_runtime(runtime):
    base, client, namespace = runtime
    # Match the embedded client's static normalization contract in both transports.
    base.LlamaCppClient._normalize_tool_calls = staticmethod(lambda calls: calls or [])
    base.LlamaCppClient._qubitz_streaming_patched = False
    contracts.functions(LING, {"_patch_streaming_chat"}, namespace)
    namespace["_patch_streaming_chat"](base)
    base.QUBITZ_FILE_CREATION_GRAMMAR_REPETITION_LIMIT = 2000
    return base, client, namespace


@pytest.mark.parametrize("limited", [False, True])
def test_transport_schema_is_runtime_specific_and_does_not_mutate_source(ling_runtime, limited):
    base, client, _ = ling_runtime
    base.QUBITZ_FILE_CREATION_GRAMMAR_REPETITION_LIMIT = 2000 if limited else 0
    original = schema()
    before = copy.deepcopy(original)
    captured = []
    client._call_with_fallback = lambda *args: (captured.append(args[-1]) or raw_response())

    client.chat(**contracts.request(num_predict=64, _qubitz_file_creation_schema=original))

    sent = captured[0]["response_format"]["json_schema"]["schema"]
    expected = copy.deepcopy(before)
    if limited:
        expected["properties"]["content"].pop("maxLength")
        nested = expected["properties"]["nested"]["items"]["properties"]
        nested["patch"].pop("maxLength")
        nested["at_limit"].pop("maxLength")
    assert sent == expected
    assert original == before


def test_only_ling_declares_the_pinned_grammar_limit():
    from runtime.variant_policy import user_variant_allowed

    profiles = json.loads((contracts.ROOT / "qubitz_variant_profiles.json").read_text())
    declared = []
    for profile in profiles["variants"]:
        if not user_variant_allowed(profile["filename"]):
            continue
        path = variant_path(profile["filename"], contracts.ROOT)
        tree = ast.parse(read_variant_source(path))
        assignments = [
            node for node in ast.walk(tree) if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Attribute)
                    and target.attr == "QUBITZ_FILE_CREATION_GRAMMAR_REPETITION_LIMIT"
                    for target in node.targets)
        ]
        if assignments:
            assert len(assignments) == 1
            assert ast.literal_eval(assignments[0].value) == 2000
            declared.append(path.name)
    assert declared == [LING.name]


def test_ling_streaming_and_nonstreaming_schema_payloads_match(ling_runtime, monkeypatch):
    base, client, _ = ling_runtime
    original = schema()
    before = copy.deepcopy(original)
    captured = []
    emitted = []
    client._call_with_fallback = lambda *args: (captured.append(args[-1]) or raw_response())

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, method, url, json, **_options):
            captured.append(json)
            event = {"choices": [{"delta": {"content": '{"path":"draft.txt","content":"hello"}'},
                                  "finish_reason": "stop"}]}
            body = "data: " + content.json.dumps(event) + "\n\ndata: [DONE]\n\n"
            yield httpx.Response(200, content=body, request=httpx.Request(method, url))

    client.chat(**contracts.request(num_predict=64, _qubitz_file_creation_schema=original))
    client.transport = SimpleNamespace(label="direct", base_url="http://fixture")
    base.set_qubitz_stream_callback(emitted.append)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: HTTPClient())
    client.chat(**contracts.request(num_predict=64, _qubitz_file_creation_schema=original))

    left, right = (
        {key: value for key, value in payload.items() if key not in {"stream", "stream_options"}}
        for payload in captured
    )
    assert left == right
    assert "maxLength" not in right["response_format"]["json_schema"]["schema"]["properties"]["content"]
    assert not emitted
    assert original == before


@pytest.mark.parametrize("streaming", [False, True])
def test_bad_request_preserves_bounded_detail_without_identical_retry(ling_runtime, monkeypatch, streaming):
    base, client, _ = ling_runtime
    attempts = []
    detail = "Failed to parse grammar: ReadError\n" + "x" * 12000

    def error_response():
        request = httpx.Request("POST", "http://fixture/v1/chat/completions")
        return httpx.Response(400, json={"error": {"message": detail}}, request=request)

    def post_json(path, payload):
        attempts.append((path, payload))
        error_response().raise_for_status()

    def alternate(*_args):
        pytest.fail("A deterministic HTTP 400 must not be sent through another transport")

    client.transports = [SimpleNamespace(label="direct", base_url="http://fixture", post_json=post_json),
                         SimpleNamespace(label="bridge", post_json=alternate)]
    client.transport = client.transports[0]

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, method, url, json, **_options):
            attempts.append((url, json))
            yield error_response()

    if streaming:
        base.set_qubitz_stream_callback(lambda _text: None)
        monkeypatch.setattr(httpx, "Client", lambda **_kwargs: HTTPClient())
    monkeypatch.setattr(contracts.time, "sleep", lambda _delay: pytest.fail("HTTP 400 was retried"))

    with pytest.raises(base.QubitzChatRequestError) as caught:
        client.chat(**contracts.request(num_predict=64))

    assert len(attempts) == 1
    assert caught.value.status_code == 400
    assert "Failed to parse grammar" in str(caught.value)
    assert "HTTP 400" in str(caught.value)
    assert "\n" not in str(caught.value)
    assert len(str(caught.value)) < 1200


def test_streamed_error_body_is_read_with_a_bound(ling_runtime, monkeypatch):
    base, client, _ = ling_runtime
    read_chunks = []
    attempts = []

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(20):
                read_chunks.append(index)
                yield b"x" * 1024

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, method, url, json, **_options):
            attempts.append(json)
            response = httpx.Response(400, stream=Body(), request=httpx.Request(method, url))
            try:
                yield response
            finally:
                response.close()

    client.transport = SimpleNamespace(label="direct", base_url="http://fixture")
    base.set_qubitz_stream_callback(lambda _text: None)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: HTTPClient())
    with pytest.raises(base.QubitzChatRequestError):
        client.chat(**contracts.request(num_predict=64))
    assert len(attempts) == 1
    assert len(read_chunks) == 8


def test_backend_status_is_not_replayed_on_another_transport(ling_runtime):
    base, client, _ = ling_runtime
    attempts = []

    def failed(path, _payload):
        attempts.append("direct")
        response = httpx.Response(503, request=httpx.Request("POST", "http://fixture" + path))
        response.raise_for_status()

    def success(_path, _payload):
        attempts.append("bridge")
        return raw_response()

    client.transports = [SimpleNamespace(label="direct", post_json=failed),
                         SimpleNamespace(label="bridge", post_json=success)]
    client.transport = client.transports[0]
    with pytest.raises(base.QubitzChatRequestError) as failure:
        client.chat(**contracts.request(num_predict=64))
    assert failure.value.status_code == 503
    assert attempts == ["direct"]
    assert client.transport.label == "direct"


def test_pre_dispatch_connection_failure_can_use_another_transport(ling_runtime):
    _, client, _ = ling_runtime
    attempts = []

    def failed(_path, _payload):
        attempts.append("direct")
        raise httpx.ConnectError("Connection refused before dispatch")

    def success(_path, _payload):
        attempts.append("bridge")
        return raw_response()

    client.transports = [SimpleNamespace(label="direct", post_json=failed),
                         SimpleNamespace(label="bridge", post_json=success)]
    client.transport = client.transports[0]
    result = client.chat(**contracts.request(num_predict=64))
    assert attempts == ["direct", "bridge"]
    assert result["message"]["content"]
    assert client.transport.label == "bridge"


def test_ling_chunked_creation_keeps_atomic_staging(runtime, variant, broker, monkeypatch):
    monkeypatch.setattr(content, "QWEN", LING)
    base, client, _ = content.wire(runtime, variant, broker)
    base.QUBITZ_FILE_CREATION_GRAMMAR_REPETITION_LIMIT = 2000
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    first = "<html><body>" + "x" * 3500
    replies = content.scoped_replies(
        content.raw(json.dumps({"path": "scene.html", "content": first, "complete": False})),
        content.raw(json.dumps({"path": "scene.html", "content": "</body></html>", "complete": True})),
    )
    captured = []

    def reply(*args):
        assert not (broker.workspace / "scene.html").exists()
        captured.append(args[-1])
        return next(replies)

    client._call_with_fallback = reply
    result = client.qubitz_file_creation_chat(**content.request(tools=content.TOOLS))
    assert all("maxLength" not in item["response_format"]["json_schema"]["schema"]["properties"]["content"]
               for item in captured[2:])
    calls = result["message"]["tool_calls"]
    assert len(calls) == 1
    assert calls[0]["function"]["arguments"]["content"] == first + "</body></html>"
    assert not (broker.workspace / "scene.html").exists()


def test_ling_patch_size_limit_remains_enforced(runtime, variant, broker, monkeypatch):
    monkeypatch.setattr(content, "QWEN", LING)
    _, client, _ = content.wire(runtime, variant, broker)
    runtime[0].QUBITZ_FILE_CREATION_GRAMMAR_REPETITION_LIMIT = 2000
    broker.begin_turn("Create a basic Python file named basic.py with a greeting", None)
    target = str((broker.workspace / "basic.py").resolve())
    replies = iter([
        content.raw(json.dumps({"path": target, "content": "def broken(:\n"})),
        content.raw(json.dumps({"patches": [{"old_text": "def broken(:", "new_text": "x" * 4097}]})),
    ])
    captured = []
    client._call_with_fallback = lambda *args: (captured.append(args[-1]) or next(replies))
    result = client.qubitz_file_creation_chat(**content.request(tools=content.TOOLS))
    schema = captured[-1]["response_format"]["json_schema"]["schema"]
    assert schema["required"] == ["patches"]
    patch = schema["properties"]["patches"]["items"]["properties"]
    assert "maxLength" not in patch["old_text"]
    assert "maxLength" not in patch["new_text"]
    assert not result["message"]["tool_calls"]
    assert "invalid or unchanged fields" in result["message"]["content"]
    assert not (broker.workspace / "basic.py").exists()
