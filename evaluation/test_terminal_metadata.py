"""Real HTTP response trailers must be optional, bounded and never replay generation."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_http_client_startup as startup_tests

startup = startup_tests.startup


@pytest.mark.parametrize("mode", ["usage", "silent", "late_action"])
def test_real_stream_finish_retains_bounded_metadata_without_replay(startup, mode):
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n')
            self.wfile.flush()
            if mode == "silent":
                release.wait(5)
            else:
                time.sleep(0.025)
                event = {"choices": [], "usage": {"completion_tokens": 12},
                         "timings": {"predicted_n": 12, "predicted_ms": 100}}
                if mode == "late_action":
                    event["choices"] = [{"delta": {"content": "unsafe late output", "tool_calls": [{
                        "index": 0, "function": {"name": "write_file", "arguments": "{}"},
                    }]}}]
                self.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    startup.client.transport = SimpleNamespace(label="direct", base_url=f"http://127.0.0.1:{server.server_port}")
    try:
        began = time.monotonic()
        answer = startup.invoke("answer")
        assert time.monotonic() - began < 2
        assert answer["message"]["content"] == "ok" and not answer["message"]["tool_calls"]
        assert len(calls) == 1
        if mode == "silent":
            assert answer["terminal_metadata"]["status"] == "bounded_timeout"
        else:
            assert answer["usage"]["completion_tokens"] == 12
            assert answer["timings"]["predicted_n"] == 12
            assert answer["terminal_metadata"]["status"] == "observed"
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_terminal_metadata_does_not_swallow_user_cancellation():
    import asyncio

    from runtime.stream_completion import terminal_metadata

    network = SimpleNamespace(read=lambda *_a, **_k: b"")
    original = network.read
    response = SimpleNamespace(extensions={"network_stream": network})
    def cancel():
        raise asyncio.CancelledError("user")
    with pytest.raises(asyncio.CancelledError):
        terminal_metadata(response, iter(()), cancel)
    assert network.read is original


@pytest.mark.parametrize("error", [httpx.ReadError("socket failed"), ValueError("invalid JSON body")])
def test_nonstreaming_accepted_or_unknown_transport_failure_never_falls_back(startup, error):
    calls = []
    class Transport:
        label = "accepted"
        def post_json(self, *_args):
            calls.append(True)
            raise error
    startup.client.transports = [Transport(), SimpleNamespace(label="fallback", post_json=lambda *_a: pytest.fail("Replayed"))]
    with pytest.raises(RuntimeError, match="was not replayed"):
        startup.client._call_with_fallback("post_json", "/v1/chat/completions", {})
    assert calls == [True]
