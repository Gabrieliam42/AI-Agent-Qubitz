"""Real loopback backend failures must not poison a later independent task."""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from evaluation import test_creation_recovery_provenance as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation import test_verified_recovery_handoff as handoff
from evaluation.disposable_followup_matrix import runner_snapshot
from evaluation.test_snapshot_behavior_recovery import AFTER, BEFORE
from runtime.structured_response import StructuredBackendError

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("status,detail", [
    (200, "backend generation failed"),
    (400, "grammar compilation failed"),
    (503, "backend unavailable"),
])
@pytest.mark.parametrize("committed", [False, True])
def test_backend_failure_preserves_state_and_allows_new_task(
    variant, runner, tmp_path, monkeypatch, status, detail, committed,
):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append({"path": self.path, "body": json.loads(raw),
                             "request_id": self.headers.get("X-Qubitz-Request-Id")})
            response = json.dumps({"error": {"message": detail}}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = variant._APP.base
        client = object.__new__(base.LlamaCppClient)
        client.config = runner.config
        client.transport = SimpleNamespace(label="direct", base_url=f"http://127.0.0.1:{server.server_port}")
        monkeypatch.setattr(client, "ensure_model", lambda name: name)
        monkeypatch.setattr(client, "_probe_with_fallback", lambda _path: {
            "status_code": 200, "json": {"default_generation_settings": {"n_ctx": runner.config.num_ctx}},
        })
        monkeypatch.setattr(runner, "_get_llm", lambda: client)
        calls = receipts.install_host(variant, runner, monkeypatch)
        target = tmp_path / "calculator.py"
        target.write_text(BEFORE)
        model_passes = []

        async def model_pass(self, prompt, callback=None):
            model_passes.append(prompt)
            broker = self._tool_permission_broker
            if len(model_passes) == 2:
                creation.record_creation(broker, tmp_path / "recovered.txt", "ready")
                return "Saved recovered.txt."
            assert len(model_passes) == 1
            if committed:
                broker.required_postconditions.add("tests")
                broker.transaction_originals[str(target)] = {"data": BEFORE.encode()}
                target.write_text(AFTER)
                broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
                    "status": "completed", "path": str(target),
                    "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                }))
                self._raise_if_cancelled()
                return "The edit was saved."
            boundaries.record_snapshot(broker, target)
            host = boundaries.host_for(variant, broker)
            rejected = await host.call_tool("apply_text_patch", {
                "path": target.name, "replacements": [{"old_text": "not current", "new_text": AFTER}],
            })
            assert rejected.isError and not host.dispatched
            self._raise_if_cancelled()
            return "Not changed."

        monkeypatch.setattr(handoff.parent_runner(runner), "_run_async", model_pass)
        with pytest.raises(StructuredBackendError) as raised:
            asyncio.run(runner._run_async(
                "Edit calculator.py to add divide(a, b), raising ValueError for a zero divisor. Preserve add.",
            ))
        assert target.read_text() == (AFTER if committed else BEFORE)
        assert len(requests) == len(model_passes) == 1
        request = requests[0]
        assert request["path"] == "/v1/chat/completions"
        assert raised.value.receipt["http_status"] == status
        assert raised.value.receipt["request_id"] == request["request_id"]
        canonical = json.dumps(request["body"], sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        assert raised.value.receipt["request_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
        assert runner._last_turn_control["finished"] is True
        assert not runner._qubitz_wrapper_turn_active
        assert not runner._cancel_event.is_set()
        assert runner._active_callback is None
        first_turn = runner._last_turn_control["turn_id"]
        following = asyncio.run(runner._run_async("Create recovered.txt containing exactly ready."))
        assert "[Completion status: verified]" in following
        assert (tmp_path / "recovered.txt").read_text() == "ready"
        assert target.read_text() == (AFTER if committed else BEFORE)
        assert len(requests) == 1 and len(model_passes) == 2
        assert not any(name == "apply_text_patch" for name, _args in calls)
        assert runner._last_turn_control["turn_id"] != first_turn
        assert runner_snapshot(runner)["recovery_receipts"] == []
        assert not runner._tool_permission_broker.evidence
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()
