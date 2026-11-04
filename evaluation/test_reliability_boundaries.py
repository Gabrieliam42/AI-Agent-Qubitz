"""Transport and progress regressions; no model acquisition or inference."""
from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from evaluation import test_followup_matrix as evaluator
from evaluation import test_http_client_startup as startup_tests

startup = startup_tests.startup


def quality(tmp_path, *events):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps({"time": index, "turn": 1, **event}) + "\n"
                            for index, event in enumerate(events)))
    return evaluator.matrix.generation_quality(path, 1)


@pytest.mark.parametrize("tail,expected", [
    ([], "incomplete"),
    ([{"kind": "http_stream_close", "closed": True}], "incomplete"),
    ([{"kind": "http_stream_error", "exception_type": "ReadTimeout"}], "timed_out"),
    ([{"kind": "cancel_request", "origin": "fixture"}], "cancelled"),
    ([{"kind": "http_status", "status": 503}], "rejected"),
])
def test_unfinished_transport_is_never_clean(tmp_path, tail, expected):
    result = quality(tmp_path, {"kind": "http_request", "request_id": "a", "payload": {"stream": True}},
                     *[{"request_id": "a", **event} for event in tail])
    assert result["requests"][0]["transport_status"] == expected
    if expected != "cancelled":
        assert result["status"] != "clean"


def test_nonstreaming_body_supplies_real_finish_evidence(tmp_path):
    result = quality(tmp_path, {"kind": "http_request", "request_id": "a", "payload": {"stream": False}},
                     {"kind": "http_body", "request_id": "a", "body": json.dumps({"choices": [{
                         "message": {"content": "ok"}, "finish_reason": "stop"}]})})
    assert result["requests"][0]["transport_status"] == "completed"
    assert result["requests"][0]["characters"]["content"] == 2
    assert result["status"] == "clean"


@pytest.mark.parametrize("mode", ["missing_finish", "sse_error", "accepted_read_error", "done_only", "bad_finish"])
def test_accepted_stream_failure_never_replays_or_returns_tools(startup, monkeypatch, mode):
    calls = []

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_lines(self):
            if mode == "accepted_read_error":
                raise httpx.ReadError("fixture body failed after acceptance")
            if mode == "sse_error":
                yield 'data: {"error":{"message":"fixture generation failed"}}'
            elif mode == "done_only":
                yield "data: [DONE]"
            elif mode == "bad_finish":
                yield 'data: {"choices":[{"delta":{},"finish_reason":"error"}]}'
            else:
                yield 'data: {"choices":[{"delta":{"content":"unfinished"}}]}'

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *_args, **_kwargs):
            calls.append(True)
            yield Response()

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())
    with pytest.raises((RuntimeError, httpx.ReadError)):
        startup.invoke("answer")
    assert calls == [True]


def test_valid_finish_does_not_wait_for_another_server_chunk(startup, monkeypatch):
    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
            pytest.fail("A complete generation waited for another server chunk")

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *_args, **_kwargs):
            yield Response()

    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: Client())
    assert startup.invoke("answer")["message"]["content"] == "ok"


def test_verified_hash_cycle_is_blocked_but_new_state_is_allowed(tmp_path):
    from runtime.action_progress import ActionProgress

    progress = ActionProgress("Create calculator.py returning the sum.", tmp_path)
    progress.observe_hash("calculator.py", "a")
    assert not progress.patch_cycle("calculator.py", "a", "b")
    progress.observe_hash("calculator.py", "b")
    assert progress.patch_cycle("calculator.py", "b", "a")
    assert not progress.patch_cycle("calculator.py", "b", "c")


@pytest.mark.parametrize("prompt,allowed", [
    ("Edit state.txt, then restore its original contents.", True),
    ("Undo the temporary change to state.txt.", True),
    ("Do not undo or restore state.txt.", False),
    ('Explain the word "restore"; edit state.txt.', False),
    ("The log says `undo the change`. Edit state.txt.", False),
])
def test_explicit_revisit_is_not_inferred_from_quotes_or_negation(tmp_path, prompt, allowed):
    from runtime.action_progress import ActionProgress

    progress = ActionProgress(prompt, tmp_path)
    progress.observe_hash("state.txt", "a")
    progress.observe_hash("state.txt", "b")
    assert progress.patch_cycle("state.txt", "b", "a") is not allowed


def test_command_cycles_require_observed_state_change_or_explicit_repeat(tmp_path):
    from runtime.action_progress import ActionProgress

    progress = ActionProgress("Fix settings.py and run tests.", tmp_path)
    for _ in range(2):
        assert progress.reserve_execution("tests", {"path": "run_tests.py"}, (("settings.py", "a"),))
    assert not progress.reserve_execution("tests", {"path": "run_tests.py"}, (("settings.py", "a"),))
    assert progress.reserve_execution("tests", {"path": "run_tests.py"}, (("settings.py", "b"),))
    # Unknown state is not proof that a command made no progress.
    assert progress.reserve_execution("tests", {"path": "run_tests.py"}, None)


def test_recovery_budget_is_shared_across_paths_and_layers(tmp_path):
    from runtime.action_progress import ActionProgress

    progress = ActionProgress("Edit state.txt.", tmp_path)
    assert progress.reserve_recovery("native")
    assert not progress.reserve_recovery("native")
    assert progress.reserve_recovery("proposal")
    assert not progress.reserve_recovery("creation")


def test_capture_records_transport_failure_with_request_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from evaluation.smoke_create_calculator import Recorder, install_request_capture

    class Model:
        def _call_with_fallback(self):
            pass

    def failed_send(*_args, **_kwargs):
        raise httpx.ReadTimeout("fixture response timeout")

    monkeypatch.setattr(httpx.Client, "send", failed_send)
    # The installer is intentionally scoped to this test; restore every patched method.
    for name in ("send",):
        monkeypatch.setattr(httpx.Client, name, getattr(httpx.Client, name))
    for name in ("iter_lines", "close", "read"):
        monkeypatch.setattr(httpx.Response, name, getattr(httpx.Response, name))
    path = tmp_path / "events.jsonl"
    install_request_capture(SimpleNamespace(LlamaCppClient=Model), Recorder(path))
    with httpx.Client() as client, pytest.raises(httpx.ReadTimeout):
        client.send(httpx.Request("POST", "http://127.0.0.1:8001/v1/chat/completions", json={}))
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert events[-1]["kind"] == "http_transport_error"
    assert events[-1]["exception_type"] == "ReadTimeout"
    assert events[-1]["request_id"] == events[0]["request_id"]


def test_generation_trigger_requires_open_actual_text_and_preserves_partial_tail(tmp_path):
    path = tmp_path / "capture.jsonl"
    state = {}
    rows = [
        {"kind": "http_request", "turn": 1, "request_id": "one"},
        {"kind": "http_stream_line", "turn": 1, "request_id": "one", "line": "data: " + json.dumps({
            "choices": [{"delta": {"reasoning_content": "beginning " * 10}}]})},
    ]
    data = "".join(json.dumps(row) + "\n" for row in rows)
    path.write_text(data[:-4])
    assert evaluator.matrix.generation_cancel_evidence(path, 1, state) is None
    assert state["offset"] == len(json.dumps(rows[0])) + 1
    with path.open("a") as stream:
        stream.write(data[-4:])
    proof = evaluator.matrix.generation_cancel_evidence(path, 1, state)
    assert proof["request_id"] == "one" and proof["characters"] == 100
    with path.open("a") as stream:
        stream.write(json.dumps({"kind": "http_stream_close", "turn": 1, "request_id": "one"}) + "\n")
    assert evaluator.matrix.generation_cancel_evidence(path, 1, state) is None


def test_generation_cancel_is_opt_in_and_does_not_change_default_scope():
    names = [case.name for case in evaluator.matrix.selected_scenarios(None, "expanded-v6")]
    assert len(names) == 27 and "cancel_during_generation" not in names
    selected = evaluator.matrix.selected_scenarios(["cancel_during_generation"], "expanded-v6")
    assert len(selected) == 1 and selected[0].turns[0].cancel_on_generation


def test_cancellation_transport_state_uses_monotonic_not_shifted_wall_clock(tmp_path):
    result = quality(tmp_path,
        {"kind": "http_request", "request_id": "one", "monotonic": 1},
        {"kind": "cancel_request", "time": -100, "monotonic": 2},
        {"kind": "http_stream_close", "request_id": "one", "monotonic": 2.2, "closed": True})
    assert result["requests"][0]["transport_status"] == "cancelled"


def test_summary_axes_do_not_turn_interruption_or_cleanup_into_task_success():
    summary = {"runs": [{"status": "interrupted", "issues": ["coordinator_interrupted"],
                         "turns": [{"status": "interrupted"}]}], "contract": {"suite": "expanded-v6"}}
    evaluator.matrix.update_counts(summary)
    axes = summary["outcome_axes"]
    assert axes["task"]["passed"] == 0 and axes["interrupted_trajectories"] == 1
    assert axes["cleanup"]["unobserved"] == 1 and axes["cleanup"]["recorded_not_audited"] == 0


def test_later_cancellation_does_not_hide_a_prior_transport_failure(tmp_path):
    result = quality(tmp_path,
        {"kind": "http_request", "request_id": "one"},
        {"kind": "http_stream_error", "request_id": "one", "exception_type": "ReadTimeout"},
        {"kind": "cancel_request", "origin": "evaluator_timeout"},
        {"kind": "http_stream_close", "request_id": "one", "closed": True})
    assert result["requests"][0]["transport_status"] == "timed_out"


def test_http_error_body_is_rejection_not_success_or_decoding_error(tmp_path):
    result = quality(tmp_path,
        {"kind": "http_request", "request_id": "one"},
        {"kind": "http_status", "request_id": "one", "status": 400},
        {"kind": "http_body", "request_id": "one", "body": '{"error":{"message":"bad request"}}'})
    assert result["requests"][0]["transport_status"] == "rejected"
