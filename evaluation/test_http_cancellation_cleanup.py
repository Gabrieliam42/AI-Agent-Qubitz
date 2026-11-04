"""Model-free cancellation tracing through real HTTPcore teardown and finalizers."""
from __future__ import annotations

import gc
import inspect
import logging
import sys
from types import SimpleNamespace

import httpcore
import pytest
from httpcore._sync.http11 import HTTP11ConnectionByteStream
from httpcore._trace import Trace

from evaluation import test_http_client_startup as client_startup

startup = client_startup.startup


@pytest.fixture
def request_scope(startup):
    helper = inspect.getclosurevars(type(startup.client.transport).post_json).nonlocals["_cancellable_request"]
    return SimpleNamespace(helper=helper, owner=startup.owner)


@pytest.mark.parametrize("event", [
    "http11.response_closed.started", "http11.response_closed.complete", "http11.response_closed.failed",
    "http2.response_closed.started", "http2.response_closed.complete", "http2.response_closed.failed",
    "connection.close.started", "connection.close.complete", "connection.close.failed",
    "connection.connect_tcp.failed", "http11.receive_response_body.failed", "http2.receive_response_headers.failed",
])
def test_cancellation_never_raises_from_teardown_or_failure_trace(request_scope, event):
    with request_scope.helper() as options:
        request_scope.owner._cancel_source = "user"
        options["extensions"]["trace"](event, {"exception": ValueError("original failure")})


@pytest.mark.parametrize("event", [
    "connection.connect_tcp.started", "connection.connect_tcp.complete", "connection.start_tls.complete",
    "http11.send_request_headers.started", "http11.receive_response_headers.complete",
    "http11.receive_response_body.started", "http2.receive_response_body.complete",
])
def test_active_request_trace_still_raises_cancellation(request_scope, event):
    import asyncio

    with request_scope.helper() as options:
        request_scope.owner._cancel_source = "user"
        with pytest.raises(asyncio.CancelledError, match="Model request cancelled"):
            options["extensions"]["trace"](event, {"return_value": object()})


def test_late_trace_is_inert_after_request_scope_finishes(request_scope):
    with request_scope.helper() as options:
        trace = options["extensions"]["trace"]
    request_scope.owner._cancel_source = "user"
    trace("http11.receive_response_body.complete", {})
    trace("connection.connect_tcp.complete", {})


@pytest.mark.parametrize("operation", ["receive_response_body", "response_closed"])
def test_trace_does_not_replace_original_body_or_cleanup_exception(request_scope, operation):
    failure = OSError("original body or cleanup failure")
    with request_scope.helper() as options:
        request = httpcore.Request("GET", "http://fixture.invalid/", extensions=options["extensions"])
        with pytest.raises(OSError) as caught, Trace(operation, logging.getLogger("httpcore.http11"), request):
            request_scope.owner._cancel_source = "user"
            raise failure
        assert caught.value is failure


@pytest.mark.parametrize("finalize", ["close", "gc_active", "gc_after_scope"])
def test_httpcore_generator_finalization_closes_connection_without_unraisable(request_scope, monkeypatch, finalize):
    closed, unraisable = [], []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)

    class Connection:
        def _receive_response_body(self, **_kwargs):
            yield b"first"
            yield b"second"

        def _response_closed(self):
            closed.append(True)

    with request_scope.helper() as options:
        request = httpcore.Request("GET", "http://fixture.invalid/", extensions=options["extensions"])
        stream = HTTP11ConnectionByteStream(Connection(), request)
        iterator = iter(stream)
        assert next(iterator) == b"first"
        request_scope.owner._cancel_source = "user"
        if finalize == "close":
            iterator.close()
        elif finalize == "gc_active":
            del iterator
            gc.collect()
    if finalize == "gc_after_scope":
        del iterator
        gc.collect()
    assert closed == [True]
    assert not unraisable


def test_real_loopback_response_close_preserves_cancelled_connection_cleanup(request_scope, monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx

    calls, unraisable = [], []
    release = threading.Event()
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b"first\n")
            self.wfile.flush()
            release.wait(5)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with httpx.Client(trust_env=False, timeout=2) as client, request_scope.helper() as options:
            with client.stream("GET", f"http://127.0.0.1:{server.server_port}/", **options) as response:
                iterator = response.iter_lines()
                assert next(iterator) == "first"
                request_scope.owner._cancel_source = "user"
                iterator.close()
                response.close()
                assert response.is_closed
            gc.collect()
        assert calls == ["/"] and not unraisable
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
