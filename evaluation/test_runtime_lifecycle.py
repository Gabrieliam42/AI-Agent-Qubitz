"""Model-free response cancellation, timeout, reaping and evaluator cleanup checks."""
from __future__ import annotations

import ast
import asyncio
import json
import select
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_reasoning_display as reasoning
from runtime import read_variant_source
from runtime.paths import DEFAULT_VARIANT, variant_path
from runtime.source import embedded_source

runtime = contracts.runtime
display_runtime = reasoning.display_runtime
variant = receipts.variant
ROOT = Path(__file__).resolve().parents[1]
NAME = DEFAULT_VARIANT


@pytest.mark.parametrize("configured", [None, 1800.0])
@pytest.mark.parametrize("mode", ["answer", "constrained_json"])
def test_response_timeout_is_scoped_and_never_replays(display_runtime, monkeypatch, configured, mode):
    base, client, _namespace, _events = display_runtime
    if configured is not None:
        base.QUBITZ_LLAMACPP_READ_TIMEOUT_SECONDS = configured
    timeouts = []
    calls = []

    class HTTPClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            raise httpx.ReadTimeout("fixture prefill exceeded read deadline")
            yield  # pragma: no cover - contextmanager must fail during entry

    def construct(**kwargs):
        timeouts.append(kwargs["timeout"])
        return HTTPClient()

    monkeypatch.setattr(httpx, "Client", construct)
    client.transport = SimpleNamespace(label="direct", base_url="http://fixture")
    client._call_with_fallback = lambda *_args: pytest.fail("Ambiguous generation was replayed")
    base.set_qubitz_stream_callback(lambda _text: None)
    with pytest.raises(RuntimeError, match="not replayed"):
        if mode == "answer":
            client.chat(**contracts.request())
        else:
            client.qubitz_json_chat(model_name="fixture", messages=contracts.request()["messages"],
                                    schema_name="result", schema={"type": "object"}, num_predict=256)
    assert len(calls) == len(timeouts) == 1
    assert timeouts[0].read == (configured or 600.0)
    assert timeouts[0].connect == 600.0


def test_nonstreaming_timeout_never_tries_another_transport(display_runtime):
    _base, client, _namespace, _events = display_runtime
    calls = []

    def timeout(*_args):
        calls.append(True)
        raise httpx.ReadTimeout("fixture ambiguous completion")

    client.transports = [SimpleNamespace(label="direct", post_json=timeout),
                         SimpleNamespace(label="alternate", post_json=lambda *_: pytest.fail("Replayed"))]
    with pytest.raises(RuntimeError, match="not replayed"):
        client._call_with_fallback("post_json", "/v1/chat/completions", {})
    assert calls == [True]


@pytest.mark.parametrize("status", [200, 400])
def test_direct_transport_preserves_response_body_and_timeout(display_runtime, monkeypatch, status):
    base, client, namespace, _events = display_runtime

    class Transport:
        label = "direct"
        base_url = "http://fixture"

    base.DirectHTTPTransport = Transport
    base.LlamaCppClient._qubitz_streaming_patched = False
    namespace["_patch_streaming_chat"](base)
    base.QUBITZ_LLAMACPP_READ_TIMEOUT_SECONDS = 1800
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json={"error": {"message": "fixture grammar detail"}} if status == 400 else {"ok": True})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    client.transports = [Transport(), SimpleNamespace(label="other", post_json=lambda *_: pytest.fail("Replayed"))]
    if status == 400:
        with pytest.raises(base.QubitzChatRequestError, match="fixture grammar detail"):
            client._call_with_fallback("post_json", "/v1/chat/completions", {})
    else:
        assert client._call_with_fallback("post_json", "/v1/chat/completions", {}) == {"ok": True}
    assert len(calls) == 1
    assert calls[0].extensions["timeout"] == {"connect": 600.0, "read": 1800.0, "write": 600.0, "pool": 600.0}


@pytest.mark.parametrize("mode", ["answer", "constrained_json", "nonstreaming"])
def test_cancel_interrupts_wait_for_response_headers_without_replay(display_runtime, mode, monkeypatch):
    base, client, namespace, _events = display_runtime
    received = threading.Event()
    disconnected = threading.Event()
    release = threading.Event()
    calls = []
    phases = {}
    # Header cancellation and cold constructor cancellation have separate contracts.
    # Warm only dependency imports here; the production call still creates its own client.
    cold_setup = time.monotonic()
    with httpx.Client():
        pass
    phases["dependency_setup_seconds"] = time.monotonic() - cold_setup
    original_client_init = httpx.Client.__init__

    def client_init(client, *args, **kwargs):
        phases["client_init_started"] = time.monotonic()
        original_client_init(client, *args, **kwargs)
        phases["client_init_finished"] = time.monotonic()

    monkeypatch.setattr(httpx.Client, "__init__", client_init)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            phases["request_received"] = time.monotonic()
            received.set()
            # Deliberately withhold headers, as the native server does in prefill.
            while not release.wait(0.02):
                try:
                    if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                        disconnected.set()
                        return
                except OSError:
                    disconnected.set()
                    return

    class Owner:
        def stop(self, _reason):
            return

    owner = Owner()
    owner._cancel_source = ""
    owner._cancel_event = threading.Event()
    owner._tool_permission_broker = SimpleNamespace(turn_id="active-turn")
    namespace["_TOOL_CALL_CONTEXT"].set(SimpleNamespace(stop_callback=owner.stop, current_prompt="Explain."))
    if mode == "nonstreaming":
        # Install the production direct transport method into this model-free base.
        class Transport:
            pass

        base.DirectHTTPTransport = Transport
        base.LlamaCppClient._qubitz_streaming_patched = False
        namespace["_patch_streaming_chat"](base)
        transport = Transport()
    else:
        transport = SimpleNamespace()
        client._call_with_fallback = lambda *_: pytest.fail("Cancelled generation was replayed")
    transport.label = "direct"
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    transport.base_url = f"http://127.0.0.1:{server.server_port}"
    client.transport = transport
    base.QUBITZ_LLAMACPP_READ_TIMEOUT_SECONDS = 1800
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def cancel():
        phases["received_in_time"] = received.wait(5)
        # A slow fixture setup must fail promptly, not leave a 1800-second request uncancelled.
        phases["cancel_requested"] = time.monotonic()
        owner._cancel_source = "user"
        owner._cancel_event.set()

    canceller = threading.Thread(target=cancel, daemon=True)
    canceller.start()
    started = time.monotonic()
    try:
        with pytest.raises(asyncio.CancelledError):
            if mode == "answer":
                client.chat(**contracts.request())
            elif mode == "constrained_json":
                client.qubitz_json_chat(model_name="fixture", messages=contracts.request()["messages"],
                                        schema_name="result", schema={"type": "object"}, num_predict=256)
            else:
                transport.post_json("/v1/chat/completions", {"stream": False})
        phases["cancel_observed"] = time.monotonic()
        assert phases["received_in_time"], "Loopback request did not arrive within the fixture startup deadline"
        assert disconnected.wait(2)
        assert time.monotonic() - started < 3
        assert calls == ["/v1/chat/completions"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        canceller.join(timeout=2)
        print("cancellation_phase_times=" + json.dumps({"mode": mode, "started": started, **phases}))


@pytest.fixture(scope="module")
def shutdown_method():
    tree = ast.parse(embedded_source(read_variant_source(variant_path(NAME)))[0])
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "LlamaCppServerProcess")
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "shutdown")
    namespace = {"subprocess": subprocess}
    exec(compile(ast.Module(body=[method], type_ignores=[]), NAME, "exec"), namespace)  # noqa: S102
    return namespace["shutdown"]


@pytest.mark.parametrize("already_exited", [False, True])
def test_shutdown_reaps_without_escalating_after_success(shutdown_method, already_exited):
    calls = []
    process = SimpleNamespace(poll=lambda: 0 if already_exited else None,
                              terminate=lambda: calls.append("terminate"),
                              kill=lambda: pytest.fail("Successful shutdown escalated"),
                              wait=lambda timeout: calls.append(("wait", timeout)))
    server = SimpleNamespace(process=process, _qubitz_shutdown_grace_seconds=60)
    shutdown_method(server)
    assert calls == ([("wait", 0)] if already_exited else ["terminate", ("wait", 60)])
    assert server.process is None


@pytest.mark.parametrize("terminal", [True, False])
def test_shutdown_has_one_bounded_escalation_and_retains_uncertain_handle(shutdown_method, terminal):
    calls = []

    def wait(timeout):
        calls.append(("wait", timeout))
        if timeout == 60 or not terminal:
            raise subprocess.TimeoutExpired("owned fixture child", timeout)
        return -9

    process = SimpleNamespace(poll=lambda: None, wait=wait,
                              terminate=lambda: calls.append("terminate"), kill=lambda: calls.append("kill"))
    server = SimpleNamespace(process=process, _qubitz_shutdown_grace_seconds=60,
                             _qubitz_shutdown_kill_wait_seconds=30)
    if terminal:
        shutdown_method(server)
        assert server.process is None
    else:
        with pytest.raises(subprocess.TimeoutExpired):
            shutdown_method(server)
        assert server.process is process
    assert calls == ["terminate", ("wait", 60), "kill", ("wait", 30)]


def test_real_model_free_child_is_waited_and_reaped(shutdown_method):
    process = subprocess.Popen([sys.executable, "-u", "-c", "import time; print('ready',flush=True); time.sleep(60)"],
                               stdout=subprocess.PIPE, text=True)
    server = SimpleNamespace(process=process, _qubitz_shutdown_grace_seconds=2,
                             _qubitz_shutdown_kill_wait_seconds=2)
    try:
        assert process.stdout.readline().strip() == "ready"
        shutdown_method(server)
        assert server.process is None and process.returncode is not None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        process.stdout.close()


def test_failed_native_shutdown_records_failure_and_retains_owner(variant):
    class Server:
        def __init__(self, config):
            self.config = config
            self.process = SimpleNamespace(poll=lambda: None)
            self.model_path = Path("/fixture/model")

        def resolve_executable(self):
            return "/fixture/llama-server"

        def _launch_command(self):
            pytest.fail("Cleanup launched a server")

        def ensure_started(self):
            pytest.fail("Cleanup launched a server")

        def shutdown(self):
            raise subprocess.TimeoutExpired("owned fixture", 30)

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: True)
    variant._patch_llamacpp_launch_fit(base)
    server = Server(SimpleNamespace())
    server._qubitz_owned_launch_epoch = 123.0
    server._qubitz_owner_token = "original-owner"
    server._qubitz_owned_windows_receipt = {"pid": 123, "created": "original-created"}
    with pytest.raises(subprocess.TimeoutExpired):
        server.shutdown()
    assert server._qubitz_cleanup_outcome["status"] == "failed"
    assert "owned fixture" in server._qubitz_cleanup_outcome["final_detail"]
    assert server._qubitz_owner_token == "original-owner"
    assert server._qubitz_owned_launch_epoch == 123.0
    assert server.process is not None


@pytest.mark.parametrize("failure", [False, True])
def test_evaluator_destroys_gui_even_when_shutdown_fails(failure):
    from evaluation.disposable_followup_matrix import apply_gui_diagnostics

    tree = ast.parse((ROOT / "evaluation/disposable_followup_matrix.py").read_text())
    child = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "child")
    cleanup = next(node for node in ast.walk(child) if isinstance(node, ast.Try)
                   and any(isinstance(item, ast.Assign) and any(isinstance(target, ast.Subscript)
                           and isinstance(target.slice, ast.Constant) and target.slice.value == "shutdown_exception"
                           for target in item.targets) for handler in node.handlers for item in handler.body))
    destroyed = []
    import traceback

    def shutdown(*_args):
        if failure:
            raise RuntimeError("fixture owned shutdown failure")

    report = {"status": "failed", "issues": []}
    gui = SimpleNamespace(busy=False, _smoke_events=[], _smoke_messageboxes=[])
    namespace = {"report": report, "gui": gui, "runner": object(), "observer": object(),
                 "stop_control": None, "wait_for_task_idle": lambda *_args: None,
                 "apply_gui_diagnostics": apply_gui_diagnostics,
                 "shutdown_runtime_responsively": shutdown, "traceback": traceback,
                 "smoke": SimpleNamespace(_collect_gui_diagnostics=lambda _: {"behavior_issues": []}),
                 "destroy_gui_once": lambda value: destroyed.append(value)}
    exec(compile(ast.Module(body=[cleanup], type_ignores=[]), "evaluator cleanup", "exec"), namespace)  # noqa: S102
    assert destroyed == [gui]
    assert report["status"] == ("harness_error" if failure else "failed")
    assert report["issues"] == (["runtime_shutdown_error"] if failure else [])
    if failure:
        assert "fixture owned shutdown failure" in report["shutdown_exception"]
