"""CPU-only generation, intent, literal-edit and observation regressions."""
from __future__ import annotations

import asyncio
import hashlib
import json
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest

from evaluation import smoke_create_calculator as smoke
from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_reasoning_display as reasoning
from evaluation.disposable_followup_matrix import (
    generation_quality,
    update_counts,
    whitespace_failure_categories,
)
from evaluation.followup_matrix_cases import Check, Turn

variant = receipts.variant
runner = receipts.runner
runtime = contracts.runtime
display_runtime = reasoning.display_runtime


@pytest.mark.parametrize("prompt,success", [
    ("Run run_tests.py and report the failing test. Do not fix or edit files yet.", False),
    ("Run pytest and show the errors, without repairing anything.", False),
    ("Fix pricing.py, then run run_tests.py and report the failures.", True),
    ("Ensure all tests pass and report the results.", True),
    ("Run tests successfully and report the results.", True),
    ("Run run_tests.py.", True),
])
def test_diagnostic_execution_goal_preserves_repairs(variant, prompt, success):
    assert variant._requests_test_execution(prompt)
    assert variant._requests_test_execution(prompt, require_success=True) is success


def test_failed_test_receipt_completes_reporting_without_retry(variant, runner):
    (runner.workspace / "run_tests.py").write_text("raise SystemExit(1)\n")
    prompt = "Run run_tests.py and report the failing test. Do not fix or edit files yet."
    broker = receipts.begin(runner, prompt)
    broker.record_result("run_existing_entrypoint", {"path": "run_tests.py"}, receipts.result({
        "returncode": 1, "command": ["python", "run_tests.py"],
        "stderr": "test_known_behavior ... FAIL\n", "status": "completed",
    }))
    required, missing = broker.completion_report(prompt)
    assert "test_command_executed" in required and "tests" not in required
    assert not missing
    assert runner._wrapper_no_progress_tool_choice(prompt, "The captured test failed.") is None
    assert broker.evidence[-1]["success"] is False


def test_wrong_test_target_does_not_complete_diagnostic(variant, runner):
    for name in ("run_tests.py", "other_tests.py"):
        (runner.workspace / name).write_text("raise SystemExit(1)\n")
    prompt = "Run run_tests.py and report its errors."
    broker = receipts.begin(runner, prompt)
    broker.record_result("run_existing_entrypoint", {"path": "other_tests.py"}, receipts.result({
        "returncode": 1, "command": ["python", "other_tests.py"], "stderr": "FAIL", "status": "completed",
    }))
    assert "test_command_executed" in broker.completion_report(prompt)[1]


def owned_edit(broker, path, before, after):
    original = before.encode()
    broker.transaction_explicit_file_targets.add(str(path))
    broker.transaction_originals[str(path)] = {"data": original}
    path.write_bytes(after.encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    broker.transaction_changed_hashes[str(path)] = digest
    broker.transaction_snapshot_hashes[str(path)] = digest


def test_literal_append_uses_originals_for_every_alias_target(variant, runner):
    prompt = "Append a line saying finished to each of qfiles, preserving the first lines."
    broker = receipts.begin(runner, prompt)
    for name, content in (("first.txt", "north\n"), ("second.txt", "south\n")):
        path = runner.workspace / name
        owned_edit(broker, path, content, content + "\nfinished\n")
    plans = variant._exact_saved_literal_patches(broker, prompt)
    assert len(plans) == 2
    assert {patch["replacements"][0]["new_text"] for patch in plans} == {"north\nfinished\n", "south\nfinished\n"}
    assert len(variant._saved_text_contract_issues(broker, prompt, [])) == 2


def test_literal_replacement_is_exact_and_rejects_external_edits(variant, runner):
    path = runner.workspace / "state.txt"
    prompt = "Change state.txt to ready followed by a newline now."
    broker = receipts.begin(runner, prompt)
    owned_edit(broker, path, "original\n", "ready\n\n")
    plans = variant._exact_saved_literal_patches(broker, prompt)
    assert plans[0]["replacements"] == [{"old_text": "ready\n\n", "new_text": "ready\n"}]
    path.write_text("an external edit\n")
    broker.transaction_snapshot_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    assert not variant._exact_saved_literal_patches(broker, prompt)
    assert path.read_text() == "an external edit\n"


def test_literal_append_preserves_original_blank_lines_and_crlf(variant, runner):
    path = runner.workspace / "notes.txt"
    prompt = "Append a line saying complete to notes.txt."
    broker = receipts.begin(runner, prompt)
    owned_edit(broker, path, "original\r\n\r\n", "original\r\n\r\n\r\ncomplete\r\n")
    plans = variant._exact_saved_literal_patches(broker, prompt)
    assert plans[0]["replacements"][0]["new_text"] == "original\r\n\r\ncomplete\r\n"
    broker.access_mode = variant.ACCESS_MODE_READ_ONLY
    assert not variant._exact_saved_literal_patches(broker, prompt)


def test_mounted_path_display_preserves_actual_data_and_urls(variant):
    source = "Workspace: /mnt/d/Work Folder/project"
    assert variant._format_gui_mounted_paths("system", source, windows_display=True) == "Workspace: D:\\Work Folder\\project"
    assert source == "Workspace: /mnt/d/Work Folder/project"
    assert variant._format_gui_mounted_paths("status", 'Loading "/mnt/c/Folder Name/file.txt"', windows_display=True) == 'Loading "C:\\Folder Name\\file.txt"'
    for role in ("user", "assistant", "tool", "reasoning"):
        assert variant._format_gui_mounted_paths(role, source, windows_display=True) == source
    for text in ("Workspace: /home/user/project", "URL: https://example.test/mnt/d/path", "URL: https://example.test/?path=/mnt/d/path"):
        assert variant._format_gui_mounted_paths("status", text, windows_display=True) == text


@pytest.mark.parametrize("channel", ["content", "reasoning_content"])
def test_short_conversation_repetition_is_bounded(display_runtime, monkeypatch, channel):
    base, client, namespace, events = display_runtime
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        current_prompt="Report the captured error. Do not repeat yourself.",
        callback=lambda *event: events.append(event), _emit=lambda _message: None, stop_callback=stops.append,
    ))
    block = "### User [timestamp]\nReport the result without performing another operation.\n"
    payloads, _ = reasoning.install_stream(monkeypatch, base, client, [
        reasoning.chunk(**{channel: "The captured operation failed.\n\n" + block * 50}),
    ])
    response = client.chat(**contracts.request())
    assert response["finish_reason"] == "repetition_guard"
    assert len(payloads) == 1 and len(stops) == 1
    assert "### User" not in response["message"]["content"]


def test_explicit_requested_repetition_is_preserved(display_runtime, monkeypatch):
    base, client, namespace, events = display_runtime
    stops = []
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(
        current_prompt="Repeat this sentence twenty times.", callback=lambda *event: events.append(event),
        _emit=lambda _message: None, stop_callback=stops.append,
    ))
    output = "This is the sentence to repeat. " * 20
    reasoning.install_stream(monkeypatch, base, client, [reasoning.chunk(content=output)])
    response = client.chat(**contracts.request())
    assert response["message"]["content"] == output and not stops


@pytest.mark.parametrize("mode", ["answer", "constrained_json"])
def test_user_cancel_closes_an_idle_response_without_replay(display_runtime, mode):
    _base, client, namespace, _events = display_runtime
    received = threading.Event()
    disconnected = threading.Event()
    release = threading.Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if mode == "answer" else "application/json")
            self.end_headers()
            self.wfile.write(b'data: {"choices":[{"delta":{"content":"Beginning."},"finish_reason":null}]}\n\n'
                             if mode == "answer" else b'{"choices":[')
            self.wfile.flush()
            received.set()
            while not release.wait(0.05):
                try:
                    if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                        disconnected.set()
                        break
                except OSError:
                    disconnected.set()
                    break

    owner = SimpleNamespace(_cancel_source="", _cancel_event=threading.Event(),
                            _tool_permission_broker=SimpleNamespace(turn_id="active"))
    # Bound method ownership is what the ordinary runtime broker supplies.
    class StopOwner:
        def stop(self, _reason):
            return

    owned = StopOwner()
    owned.__dict__.update(owner.__dict__)
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(stop_callback=owned.stop, current_prompt="Explain the result."))
    client._call_with_fallback = lambda *_args: pytest.fail("Cancelled generation was replayed")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    client.transport = SimpleNamespace(label="direct", base_url=f"http://127.0.0.1:{server.server_port}")

    def cancel_after_headers():
        assert received.wait(5)
        owned._cancel_source = "user"
        owned._cancel_event.set()

    cancel = threading.Thread(target=cancel_after_headers, daemon=True)
    cancel.start()
    started = time.monotonic()
    try:
        with pytest.raises(asyncio.CancelledError):
            if mode == "answer":
                client.chat(**contracts.request())
            else:
                client.qubitz_json_chat(model_name="fixture", messages=contracts.request()["messages"],
                                        schema_name="result", schema={"type": "object"}, num_predict=256)
        assert disconnected.wait(2)
        assert time.monotonic() - started < 3
        assert calls == ["/v1/chat/completions"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        cancel.join(timeout=2)


def test_quality_receipts_keep_intervention_and_delay_out_of_clean_passes(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = [{"time": 1, "turn": 2, "kind": "http_request", "request_id": "r", "payload": {}},
            {"time": 2, "turn": 2, "kind": "cancel_request", "origin": "external"},
            {"time": 8, "turn": 2, "kind": "http_stream_close", "request_id": "r", "closed": True}]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    quality = generation_quality(path, 2)
    assert quality["external_intervention"]
    assert quality["maximum_client_cancellation_delay_seconds"] == 6
    assert quality["problems"] == ["generation_cancellation_delayed"]
    summary = {"runs": [{"status": "passed", "turns": [{"status": "passed", "observed": {
        "generation_quality": quality, "external_intervention": True,
    }}]}]}
    update_counts(summary)
    assert summary["quality_counts"]["passed_with_intervention"] == 1
    assert summary["quality_counts"]["passed_clean"] == 0


def test_quality_repetition_review_is_independent_of_task_pass(tmp_path):
    path = tmp_path / "events.jsonl"
    text = ("### User [timestamp]\nReport the same outcome without any more action.\n" * 30)
    rows = [{"time": 1, "turn": 1, "kind": "http_request", "request_id": "r", "payload": {}},
            {"time": 2, "turn": 1, "kind": "http_stream_line", "request_id": "r", "line": "data: " +
             json.dumps({"choices": [{"delta": {"content": text}, "finish_reason": "stop"}]})}]
    path.write_text("\n".join(json.dumps(row) for row in rows))
    quality = generation_quality(path, 1)
    assert quality["status"] == "review_required" and quality["repetition_candidates"]


def test_whitespace_classification_preserves_failed_oracle(tmp_path):
    (tmp_path / "note.txt").write_text("original\n\nappended\n")
    turn = Turn("Append a line.", (Check("text", "note.txt", "original\nappended\n"),))
    issues = ["postcondition_failed:text:note.txt"]
    assert whitespace_failure_categories(tmp_path, turn, issues) == ["whitespace_only_text_mismatch:note.txt"]
    assert issues == ["postcondition_failed:text:note.txt"]


@pytest.mark.parametrize("prefix", ["```python\n", '{"document":"'])
def test_observer_retains_artifact_protection_beyond_window(tmp_path, prefix):
    path = tmp_path / "events.jsonl"
    rows = [{"time": 1, "turn": 1, "kind": "http_request", "request_id": "r", "payload": {}}]
    for index, text in enumerate((prefix, "preface " * 2000, "legitimate repeated artifact content\n" * 400), 2):
        rows.append({"time": index, "turn": 1, "kind": "http_stream_line", "request_id": "r",
                     "line": "data: " + json.dumps({"choices": [{"delta": {"content": text},
                         "finish_reason": "stop" if index == 4 else None}]})})
    path.write_text("\n".join(json.dumps(row) for row in rows))
    quality = generation_quality(path, 1)
    assert quality["status"] == "clean" and not quality["repetition_candidates"]


@pytest.mark.parametrize("streamed_read", [False, True])
def test_observer_captures_non_sse_json_body_once(monkeypatch, streamed_read):
    events = []
    recorder = SimpleNamespace(turn=7, emit=lambda kind, **data: events.append({"kind": kind, **data}))
    base = SimpleNamespace(LlamaCppClient=type("Client", (), {"_call_with_fallback": lambda *_args: None}))
    # Restore every class method the capture helper installs at fixture teardown.
    for cls, name in ((httpx.Client, "send"), (httpx.Response, "iter_lines"),
                      (httpx.Response, "close"), (httpx.Response, "read")):
        monkeypatch.setattr(cls, name, getattr(cls, name))
    smoke.install_request_capture(base, recorder)
    payload = {"choices": [{"message": {"content": '{"result":"captured"}'}}]}
    def response_after_turn_advances(request):
        recorder.turn = 8
        return httpx.Response(200, json=payload, request=request)

    transport = httpx.MockTransport(response_after_turn_advances)
    with httpx.Client(transport=transport) as client:
        url = "http://127.0.0.1:8001/v1/chat/completions"
        if streamed_read:
            with client.stream("POST", url, json={"stream": False}) as response:
                response.read()
                response.read()
                assert response.json() == payload
        else:
            assert client.post(url, json={"stream": False}).json() == payload
    bodies = [event for event in events if event["kind"] == "http_body"]
    assert len(bodies) == 1 and json.loads(bodies[0]["body"]) == payload
    assert bodies[0]["turn"] == 7
    assert bodies[0]["request_id"] == next(event["request_id"] for event in events if event["kind"] == "http_request")
