"""Bounded exact-set recovery through the native adapter and real orchestrator."""
from __future__ import annotations

import asyncio
import hashlib
import textwrap
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as contracts
from evaluation import test_file_content_recovery as native
from evaluation import test_followup_state_receipts as receipts

runtime = contracts.runtime
variant = receipts.variant
runner = receipts.runner
TOOLS = [{"type": "function", "function": {"name": "apply_text_patches"}}]
PROMPT = "In shared.txt and related.txt replace state=original with state=revised-b in both files, preserving each workspace line."


def targets(runner):
    paths = [runner.workspace / name for name in ("shared.txt", "related.txt")]
    for path in paths:
        path.write_text(f"workspace={path.stem}\nstate=original\n", encoding="utf-8")
    return paths


def snapshot(broker, path):
    broker.record_result("read_file_snapshot", {"path": path.name}, receipts.result({
        "status": "text_snapshot", "content": path.read_text(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "start_line": 1, "end_line": 2,
    }))


def proposal(paths):
    return {"patches": [{"path": path.name, "replacements": [
        {"old_text": "state=original", "new_text": "state=revised-b"},
    ]} for path in paths]}


@pytest.mark.parametrize("alias", [False, True])
def test_empty_native_batch_returns_control_before_exhaustion(runtime, variant, runner, alias):
    paths = targets(runner)
    prompt = PROMPT
    if alias:
        prompt, _, _, blocker = variant._resolve_user_context_aliases(
            "In qfiles replace state=original with state=revised-b in both files, preserving each workspace line.",
            runner.workspace, tuple(map(str, paths)),
        )
        assert not blocker
    broker = receipts.begin(runner, prompt)
    for path in paths:
        snapshot(broker, path)
    _, client, owner = native.wire(runtime, variant, broker)
    owner._wrapper_no_progress_tool_choice = lambda *_args: TOOLS[0]
    calls = []

    def empty(*args):
        calls.append(args)
        return native.raw()

    client._call_with_fallback = empty
    response = client.chat(**contracts.request(tools=TOOLS))
    assert len(calls) == 2
    assert "No edit was verified" in response["message"]["content"]
    assert not response["message"]["tool_calls"]
    assert not broker.no_progress_recovery_exhausted
    assert all("state=original" in path.read_text() for path in paths)


@pytest.mark.parametrize("mode", ["valid", "wrong_target", "incomplete", "concurrent", "uncertain", "exhausted", "read_only", "cancelled"])
def test_exact_batch_proposal_is_single_use_and_hash_bound(variant, runner, monkeypatch, mode):
    paths = targets(runner)
    broker = receipts.begin(runner, PROMPT)
    monkeypatch.setattr(runner, "_build_focused_file_context", lambda _: "")
    monkeypatch.setattr(runner, "_build_metadata_or_lexical_context", lambda _: "")
    if mode == "uncertain":
        broker.evidence.append({"tool": "apply_text_patches", "success": False, "wrapper_rejected": False})
    if mode == "exhausted":
        broker.no_progress_recovery_exhausted = True
    if mode == "read_only":
        runner.set_access_mode(variant.ACCESS_MODE_READ_ONLY)
    proposals = []

    def propose(**kwargs):
        proposals.append(kwargs)
        value = proposal(paths)
        if mode == "concurrent":
            paths[0].write_text("workspace=shared\nstate=external\n")
            value["patches"][0]["replacements"][0]["old_text"] = "state=external"
        elif mode == "incomplete":
            value["patches"].pop()
        elif mode == "wrong_target":
            value["patches"][0]["path"] = "other.txt"
        elif mode == "cancelled":
            runner._cancel_source = "user"
            runner._cancel_event.set()
        return value

    (runner.workspace / "other.txt").write_text("state=original\n")
    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))

    def handle(name, arguments):
        if name == "read_file_snapshot":
            path = runner.workspace / arguments["path"]
            return {"status": "text_snapshot", "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "content": path.read_text(), "start_line": 1, "end_line": 2}
        assert mode == "valid" and name == "apply_text_patches"
        allowed, error = broker.authorize(name, arguments)
        assert allowed, error
        assert {patch["path"] for patch in arguments["patches"]} == {path.name for path in paths}
        files = []
        for path in paths:
            path.write_text(path.read_text().replace("state=original", "state=revised-b"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            broker.transaction_changed_hashes[str(path)] = digest
            files.append({"path": path.name, "new_sha256": digest})
        return {"status": "completed", "files": files}

    calls = receipts.install_host(variant, runner, monkeypatch, handler=handle)
    if mode == "cancelled":
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_transaction_proposal_recovery(PROMPT, "", None))
        assert len(proposals) == 1
        assert not calls
        assert all("state=original" in path.read_text() for path in paths)
        return
    answers = [asyncio.run(runner._wrapper_transaction_proposal_recovery(PROMPT, "", None)) for _ in range(2)]
    assert len(proposals) == (0 if mode in {"uncertain", "exhausted", "read_only"} else 1)
    assert sum(name == "apply_text_patches" for name, _ in calls) == (1 if mode == "valid" else 0)
    assert bool(answers[0]) is (mode == "valid")
    assert answers[1] is None
    if mode == "valid":
        assert all("state=revised-b" in path.read_text() for path in paths)
    elif mode != "concurrent":
        assert all("state=original" in path.read_text() for path in paths)
    assert (runner.workspace / "other.txt").read_text() == "state=original\n"


@pytest.mark.parametrize("alias", [False, True])
def test_orchestrator_recovers_empty_batch_without_reopening_budget(runtime, variant, runner, monkeypatch, alias):
    paths = targets(runner)
    prompt = PROMPT
    if alias:
        runner._last_referenced_artifacts = tuple(map(str, paths))
        runner._last_referenced_request_index = runner._alias_request_index
        prompt = "In qfiles replace state=original with state=revised-b in both files, preserving each workspace line."
    passes, proposals = [], []

    def propose(**kwargs):
        proposals.append(kwargs)
        return proposal(paths)

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]
    scope = {}
    source = "def empty_step(self, empty_model_steps, focused_no_progress_recovery_used, messages):\n    step = 0\n"
    source += textwrap.indent(textwrap.dedent(variant._EMBEDDED_EMPTY_STEP_NEW), "    ")
    exec(compile(source, "<trusted embedded empty-step block>", "exec"), scope)  # noqa: S102

    async def native_pass(self, pass_prompt, callback=None):
        passes.append(pass_prompt)
        broker = self._tool_permission_broker
        _, client, owner = native.wire(runtime, variant, broker)
        owner._wrapper_no_progress_tool_choice = lambda *_args: TOOLS[0]
        client._call_with_fallback = lambda *_args: native.raw()
        for step in range(3):
            response = client.chat(**contracts.request(tools=TOOLS))
            content = response["message"]["content"]
            if content:
                return content
            stopped = scope["empty_step"](self, step, step >= 2, [])
            if stopped:
                return stopped
        pytest.fail("The bounded embedded loop did not return")

    monkeypatch.setattr(parent, "_run_async", native_pass)

    def handle(name, arguments):
        if name == "read_file_snapshot":
            path = runner.workspace / arguments["path"]
            return {"status": "text_snapshot", "content": path.read_text(),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "start_line": 1, "end_line": 2}
        assert name == "apply_text_patches"
        files = []
        for path in paths:
            path.write_text(path.read_text().replace("state=original", "state=revised-b"))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            runner._tool_permission_broker.transaction_changed_hashes[str(path)] = digest
            files.append({"path": path.name, "new_sha256": digest})
        return {"status": "completed", "files": files}

    calls = receipts.install_host(variant, runner, monkeypatch, handler=handle)
    answer = asyncio.run(runner._run_async(prompt))
    assert "[Completion status: verified]" in answer
    assert len(passes) == len(proposals) == 1
    assert sum(name == "apply_text_patches" for name, _ in calls) == 1
    assert all("state=revised-b" in path.read_text() for path in paths)
    assert not runner._tool_permission_broker.no_progress_recovery_exhausted
