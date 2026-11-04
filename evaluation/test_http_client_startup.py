"""Model-free client-construction cancellation, disposal and bounded concurrency."""
from __future__ import annotations

import ast
import asyncio
import contextvars
import hashlib
import inspect
import json
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from runtime import read_variant_source
from runtime.paths import variant_paths


@pytest.fixture
def startup(monkeypatch):
    base, client, namespace = contracts.runtime.__wrapped__()

    class Transport:
        label = "direct"
        base_url = "http://127.0.0.1:1"

    base.DirectHTTPTransport = Transport
    base.LlamaCppClient._qubitz_streaming_patched = False
    namespace["_patch_streaming_chat"](base)
    owner = SimpleNamespace(_cancel_source="", _cancel_event=threading.Event(),
                            _tool_permission_broker=SimpleNamespace(turn_id="first"))

    class Stop:
        def stop(self, _reason):
            pass

    stop = Stop()
    stop.__dict__.update(vars(owner))
    owner = stop
    namespace["_TOOL_CALL_CONTEXT"] = contextvars.ContextVar("startup_fixture", default=None)
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(stop_callback=owner.stop, current_prompt="Explain."))
    client.transport = Transport()
    helper = inspect.getclosurevars(Transport.post_json).nonlocals["_http_client"]
    requests = []
    closed = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

        def read(self):
            return b""

        def json(self):
            return {"choices": [{"message": {"content": '{"ok":true}', "tool_calls": []}, "finish_reason": "stop"}]}

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

        def close(self):
            closed.append(self)

        @contextmanager
        def stream(self, *_args, **kwargs):
            requests.append(kwargs)
            yield Response()

    def invoke(mode):
        if mode == "answer":
            return client.chat(**contracts.request())
        if mode == "constrained_json":
            return client.qubitz_json_chat(model_name="fixture", messages=contracts.request()["messages"],
                                           schema_name="result", schema={"type": "object"}, num_predict=256)
        return client.transport.post_json("/v1/chat/completions", {"stream": False})

    return SimpleNamespace(base=base, owner=owner, client=client, helper=helper, HTTPClient=HTTPClient,
                           invoke=invoke, requests=requests, closed=closed)


@pytest.mark.parametrize("mode", ["answer", "constrained_json", "nonstreaming"])
@pytest.mark.parametrize("reason", ["user", "new_turn"])
def test_slow_construction_is_abandoned_without_dispatch(startup, monkeypatch, mode, reason):
    began, release, disposed = threading.Event(), threading.Event(), threading.Event()
    clients = []

    class Delayed(startup.HTTPClient):
        def __init__(self, **kwargs):
            clients.append(self)
            began.set()
            assert release.wait(10), "Test did not release its initializer"

        def close(self):
            super().close()
            disposed.set()

    monkeypatch.setattr(httpx, "Client", Delayed)

    def cancel():
        assert began.wait(3)
        if reason == "user":
            startup.owner._cancel_source = "user"
            startup.owner._cancel_event.set()
        else:
            startup.owner._tool_permission_broker.turn_id = "second"

    canceller = threading.Thread(target=cancel)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(asyncio.CancelledError):
            startup.invoke(mode)
        assert time.monotonic() - started < 1
        assert not release.is_set() and not startup.requests
    finally:
        release.set()
        canceller.join(timeout=3)
    assert disposed.wait(3)
    assert startup.closed == clients and len(clients) == 1 and not startup.requests


def test_cancelled_constructor_does_not_spawn_another_initializer(startup, monkeypatch):
    began, release, disposed = threading.Event(), threading.Event(), threading.Event()
    initialized = []

    class Client(startup.HTTPClient):
        def __init__(self, **kwargs):
            initialized.append(self)
            if len(initialized) == 1:
                began.set()
                assert release.wait(10)

        def close(self):
            super().close()
            disposed.set()

    monkeypatch.setattr(httpx, "Client", Client)

    def cancel_first():
        assert began.wait(3)
        startup.owner._cancel_source = "user"
        startup.owner._cancel_event.set()

    thread = threading.Thread(target=cancel_first)
    thread.start()
    try:
        with pytest.raises(asyncio.CancelledError), startup.helper():
            pytest.fail("Abandoned client was handed off")
        startup.owner._cancel_source = ""
        startup.owner._cancel_event.clear()
        startup.owner._tool_permission_broker.turn_id = "second"
        timer = threading.Timer(0.1, lambda: setattr(startup.owner, "_cancel_source", "user"))
        timer.start()
        try:
            with pytest.raises(asyncio.CancelledError), startup.helper():
                pytest.fail("An overlapping initializer was admitted")
            assert len(initialized) == 1
        finally:
            timer.join(timeout=3)
    finally:
        release.set()
        thread.join(timeout=3)
    assert disposed.wait(3)
    startup.owner._cancel_source = ""
    startup.owner._cancel_event.clear()
    startup.owner._tool_permission_broker.turn_id = "third"
    startup.invoke("nonstreaming")
    assert len(initialized) == 2 and len(startup.requests) == 1 and len(startup.closed) == 2


def test_constructor_failure_releases_slot_and_preserves_exception(startup, monkeypatch):
    failure = ValueError("fixture client construction failure")

    def fail(**_kwargs):
        raise failure

    monkeypatch.setattr(httpx, "Client", fail)
    with pytest.raises(ValueError) as caught, startup.helper():
        pytest.fail("Failed construction was handed off")
    assert caught.value is failure
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: startup.HTTPClient())
    startup.invoke("nonstreaming")
    assert len(startup.requests) == len(startup.closed) == 1


def test_timeouts_and_transport_defaults_are_not_overridden(startup, monkeypatch):
    seen = []
    startup.base.QUBITZ_LLAMACPP_READ_TIMEOUT_SECONDS = 1800
    monkeypatch.setattr(httpx, "Client", lambda **kw: seen.append(kw) or startup.HTTPClient())
    startup.invoke("nonstreaming")
    assert len(seen) == 1 and set(seen[0]) == {"timeout"}
    assert seen[0]["timeout"].as_dict() == {"connect": 600.0, "read": 1800.0, "write": 600.0, "pool": 600.0}


def test_unstarted_worker_releases_its_slot(startup, monkeypatch):
    original_start = threading.Thread.start

    def fail_start(self):
        raise RuntimeError("fixture thread start failure")

    monkeypatch.setattr(httpx, "Client", lambda **_kw: startup.HTTPClient())
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="fixture thread start failure"), startup.helper():
        pytest.fail("An unstarted worker was handed off")
    monkeypatch.setattr(threading.Thread, "start", original_start)
    startup.invoke("nonstreaming")
    assert len(startup.requests) == len(startup.closed) == 1


def test_interruption_after_worker_start_disposes_without_dispatch(startup, monkeypatch):
    original_start = threading.Thread.start
    disposed = threading.Event()

    class Client(startup.HTTPClient):
        def close(self):
            super().close()
            disposed.set()

    def interrupted_start(self):
        original_start(self)
        raise KeyboardInterrupt("fixture interrupted start acknowledgement")

    monkeypatch.setattr(httpx, "Client", lambda **_kw: Client())
    monkeypatch.setattr(threading.Thread, "start", interrupted_start)
    with pytest.raises(KeyboardInterrupt), startup.helper():
        pytest.fail("An interrupted worker was handed off")
    assert disposed.wait(3) and not startup.requests and len(startup.closed) == 1
    monkeypatch.setattr(threading.Thread, "start", original_start)
    startup.invoke("nonstreaming")
    assert len(startup.requests) == 1 and len(startup.closed) == 2


def test_abandoned_client_cleanup_failure_is_logged_and_releases_slot(startup, monkeypatch, caplog):
    began, release, attempted = threading.Event(), threading.Event(), threading.Event()

    class Client(startup.HTTPClient):
        def __init__(self, **_kwargs):
            began.set()
            assert release.wait(10)

        def close(self):
            attempted.set()
            raise OSError("fixture close failure")

    def cancel():
        assert began.wait(3)
        startup.owner._cancel_source = "user"

    monkeypatch.setattr(httpx, "Client", Client)
    canceller = threading.Thread(target=cancel)
    canceller.start()
    try:
        with pytest.raises(asyncio.CancelledError), startup.helper():
            pytest.fail("An abandoned worker was handed off")
    finally:
        release.set()
        canceller.join(timeout=3)
    assert attempted.wait(3)
    startup.owner._cancel_source = ""
    monkeypatch.setattr(httpx, "Client", lambda **_kw: startup.HTTPClient())
    startup.invoke("nonstreaming")
    assert "Abandoned HTTP client cleanup failed" in caplog.text and "fixture close failure" in caplog.text
    assert len(startup.requests) == len(startup.closed) == 1


def test_all_public_variants_share_the_repaired_function():
    hashes = {}
    for path in variant_paths():
        text = read_variant_source(path)
        node = next(node for node in ast.parse(text).body
                    if isinstance(node, ast.FunctionDef) and node.name == "_patch_streaming_chat")
        block = "".join(text.splitlines(keepends=True)[node.lineno - 1:node.end_lineno])
        assert "def _http_client()" in block and "client_initializers = threading.BoundedSemaphore(1)" in block
        hashes[path.name] = hashlib.sha256(block.encode()).hexdigest()
    assert len(hashes) == 40 and len(set(hashes.values())) == 1, json.dumps(hashes, indent=2)
