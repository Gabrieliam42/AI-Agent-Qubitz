"""Safe retry admission and cancellation identity, without inference."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from evaluation import disposable_followup_matrix as matrix
from evaluation import test_http_client_startup as startup_tests
from evaluation.followup_matrix_cases import Turn

startup = startup_tests.startup


def test_explicit_model_loading_rejection_has_one_readiness_checked_retry(startup, monkeypatch):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            if len(calls) == 1:
                response = httpx.Response(503, request=httpx.Request(method, url),
                                          json={"error": {"message": "model loading"}})
                try:
                    yield response
                finally:
                    response.close()
            else:
                with super().stream(method, url, **kwargs) as response:
                    yield response

    startup.client._probe_with_fallback = lambda path: {"status_code": 200,
        "json": {"data": [{"id": "fixture"}]} if path == "/v1/models" else {}}
    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    assert startup.invoke("answer")["message"]["content"] == "ok"
    assert len(calls) == 2 and calls[0] == calls[1]


def test_evaluator_never_cancels_a_later_queued_gui_run(tmp_path, monkeypatch):
    callbacks, cancelled = [], []
    root = SimpleNamespace(after=lambda _ms, callback: callbacks.append(callback) or len(callbacks),
                           after_cancel=lambda _: None, winfo_exists=lambda: True)
    runner = SimpleNamespace(history=[], config=SimpleNamespace(), request_cancel=lambda: cancelled.append(True),
                             _last_turn_control={"turn_id": "later", "cancelled": False})
    gui = SimpleNamespace(root=root, _active_gui_run_id=2, _smoke_submitted_run_id=1,
                          access_mode_var=SimpleNamespace(set=lambda _: None), _apply_access_mode=lambda: None)
    observer = matrix.Observer(tmp_path)
    observer.turn = 1

    def send(*_args):
        observer.emit("http_request", request_id="later", payload={"stream": True})
        callbacks.pop(0)()
        return {"completed": True, "external_input": True,
                "submitted_runtime": {"turn_control": {"turn_id": "original", "cancelled": True}}}

    monkeypatch.setattr(matrix, "generation_cancel_evidence", lambda *_: pytest.fail("Later run was polled"))
    result = matrix.execute_turn(Turn("Explain.", (), cancel=True, cancel_on_generation=True), runner, gui,
                                 SimpleNamespace(_send_prompt=send), tmp_path, observer, 10)
    assert not cancelled and not callbacks
    assert result["runtime"]["turn_control"]["turn_id"] == "original"
    assert result["runtime_at_observation"]["turn_control"]["turn_id"] == "later"
    assert result["external_intervention"]
    assert result["generation_quality"]["external_intervention"]
