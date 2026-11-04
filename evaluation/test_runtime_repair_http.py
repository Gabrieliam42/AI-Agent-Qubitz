"""Read bounded server diagnostics while a real HTTPX response stream is open."""
from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager

import httpx
import pytest

from evaluation import test_http_client_startup as startup_tests

startup = startup_tests.startup


@pytest.mark.parametrize("status,detail,calls_expected", [
    (503, "model loading", 1),
    (400, "invalid prompt exceeds context", 1),
    (400, "response_format json_schema unsupported", 2),
])
def test_error_body_is_captured_before_response_close(startup, monkeypatch, status, detail, calls_expected):
    calls, responses = [], []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                response = httpx.Response(status, request=httpx.Request(method, url), stream=httpx.ByteStream(
                    json.dumps({"error": {"message": detail}}).encode(),
                ))
                responses.append(response)
                try:
                    yield response
                finally:
                    response.close()
            else:
                with super().stream(method, url, **kwargs) as response:
                    yield response

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    if calls_expected == 2:
        assert startup.invoke("constrained_json") == {"ok": True}
    else:
        with pytest.raises(RuntimeError, match=f"HTTP {status}: {detail}"):
            startup.invoke("constrained_json")
    assert len(calls) == calls_expected and all(response.is_closed for response in responses)


def test_cancelled_transport_error_remains_cancellation_without_replay(startup, monkeypatch):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *args, **kwargs):
            calls.append(True)
            startup.owner._cancel_source = "user"
            startup.owner._cancel_event.set()
            raise httpx.ReadError("cancelled socket closed")
            yield  # pragma: no cover

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(asyncio.CancelledError):
        startup.invoke("constrained_json")
    assert calls == [True]
