"""Model-free completion, worker-boundary and constrained readiness regressions."""
from __future__ import annotations

import ast
import asyncio
import queue
import traceback
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_creation_completion_boundary as boundary
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_http_client_startup as startup_tests
from runtime.source import read_variant_source

variant = receipts.variant
runner = receipts.runner
startup = startup_tests.startup


def parent_runner(runner):
    return next(c for c in type(runner).__mro__ if c.__name__ == "HardenedAgentRunner").__mro__[1]


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("concurrent_edit", [False, True])
def test_completion_interrupt_is_consumed_at_every_model_pass(variant, runner, monkeypatch, cancel, concurrent_edit):
    target = runner.workspace / "result.txt"
    runner._route_decision = None
    runner._qubitz_last_route_decision = None
    calls = []

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        broker = self._tool_permission_broker
        target.write_text("after\n")
        broker.record_result("write_file", {"path": str(target), "content": "after\n"}, receipts.result({
            "path": str(target), "created": True,
            "new_sha256": receipts.hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        assert self._cancel_source == "wrapper_verified_completion"
        if concurrent_edit:
            target.write_text("external edit\n")
        if cancel:
            self._cancel_source = "user"
            self._cancel_event.set()
        self._raise_if_cancelled()

    monkeypatch.setattr(parent_runner(runner), "_run_async", model_pass)
    prompt = "Create result.txt containing exactly after followed by a newline."
    receipts.begin(runner, prompt)
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_model_pass")
    factory = ast.parse("def factory():\n    last_model_pass_steps = 0\n").body[0]
    factory.body.extend([method, ast.Return(value=ast.Name(id="run_model_pass", ctx=ast.Load()))])

    async def checked(operation, *args):
        return await operation(*args)

    def check_cancel():
        if runner._cancel_source == "user":
            raise asyncio.CancelledError("user cancelled")

    namespace = dict(vars(variant), self=runner, base=variant._APP.base, callback=None,
                     selected_route="tool_loop", classification_payload={}, extension_block=4,
                     memory_checkpoint=len(runner.memory.turns), history_checkpoint=len(runner.history),
                     check_user_cancellation=check_cancel, checked_operation=checked,
                     refresh_route_after_steering=lambda: None,
                     verified_completion_answer=boundary.actual_completion_answer(variant, runner, prompt),
                     HardenedAgentRunner=next(c for c in type(runner).__mro__ if c.__name__ == "HardenedAgentRunner"))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[factory], type_ignores=[])),  # noqa: S102 - trusted source
                 "<actual model pass boundary>", "exec"), namespace)
    if cancel or concurrent_edit:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(namespace["factory"]()(prompt))
        answer = "cancelled"
    else:
        answer = asyncio.run(namespace["factory"]()(prompt))
    assert target.read_text() == ("external edit\n" if concurrent_edit else "after\n") and len(calls) == 1
    assert not runner._wrapper_interrupt_event.is_set()
    assert ("[Completion status: verified]" in answer) is (not cancel and not concurrent_edit)


def worker_method(variant):
    tree = ast.parse(read_variant_source(Path(variant.__file__)))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "HardenedGUI")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_worker_run_tagged")
    namespace = dict(vars(variant), base=SimpleNamespace(time=__import__("time"), traceback=traceback))
    exec(compile(ast.Module(body=[method], type_ignores=[]), "<real worker boundary>", "exec"), namespace)  # noqa: S102
    return namespace["_worker_run_tagged"]


@pytest.mark.parametrize("source", ["user", "wrapper_verified_completion", ""])
def test_worker_reports_unexpected_cancellation_without_losing_done(variant, source):
    def run(*_args):
        raise asyncio.CancelledError("fixture boundary cancellation")

    gui = SimpleNamespace(event_queue=queue.Queue(), agent=SimpleNamespace(
        run_sync=run, _cancel_source=source, _cancel_partial_answer="Cancelled with saved work.",
    ))
    worker_method(variant)(gui, "request", 7)
    events = list(gui.event_queue.queue)
    assert events[-1] == (7, "done", "")
    assert sum(kind == "done" for _, kind, _ in events) == 1
    assert any(kind == ("answer" if source == "user" else "error") for _, kind, _ in events)


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_json_http_failures_never_trigger_format_replay(startup, monkeypatch, status):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            yield httpx.Response(status, request=httpx.Request(method, url), json={"error": {"message": "fixture unavailable"}})

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(RuntimeError, match=f"HTTP {status}.*fixture unavailable"):
        startup.invoke("constrained_json")
    assert len(calls) == 1


def test_json_readiness_precedes_context_fit_and_dispatch(startup, monkeypatch):
    order = []

    def ready(name):
        order.append("ready")
        startup.client.config.num_ctx = 8192
        return "resolved-fixture"

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *args, **kwargs):
            order.append("post")
            assert kwargs["json"]["model"] == "resolved-fixture"
            with super().stream(*args, **kwargs) as response:
                yield response

    monkeypatch.setattr(startup.client, "ensure_model", ready)
    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    assert startup.invoke("constrained_json") == {"ok": True}
    assert order == ["ready", "post"]


def test_json_transport_failure_is_not_replayed(startup, monkeypatch):
    calls = []

    class Client(startup.HTTPClient):
        @contextmanager
        def stream(self, *args, **kwargs):
            calls.append(True)
            raise httpx.ReadError("fixture ambiguous response")
            yield  # pragma: no cover

    monkeypatch.setattr(httpx, "Client", lambda **_: Client())
    with pytest.raises(httpx.ReadError, match="ambiguous"):
        startup.invoke("constrained_json")
    assert calls == [True]
