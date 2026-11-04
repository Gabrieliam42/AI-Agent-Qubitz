"""Approved matrix-derived corrections; CPU-only and no live model startup."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_context_and_campaign_regressions as contexts
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_generation_completion_regressions as generation
from evaluation import test_reasoning_display as reasoning
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation.disposable_followup_matrix import (
    coordinator_event,
    generation_quality,
    shutdown_runtime_responsively,
)

variant = receipts.variant
runner = receipts.runner
display_runtime = reasoning.display_runtime
runtime = reasoning.runtime


@pytest.fixture(autouse=True)
def no_retrieval_model(variant, monkeypatch):
    monkeypatch.setattr(variant._APP.base.BGECodeEmbedder, "_load_model",
                        lambda *_args, **_kwargs: pytest.fail("CPU test attempted to load a retrieval model"))


@pytest.fixture
def executable_runner(variant, runner):
    # Reuse the real active environment, without installing or creating another stack.
    try:
        (runner.workspace / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Cannot expose the active interpreter in the test workspace: {exc}")
    plan = variant._select_direct_workspace_python(variant._APP.base, runner.workspace)
    assert plan is not None and plan[0].samefile(sys.executable)
    return runner


def test_completion_uses_current_mutation_targets_not_inspection_scope(variant, runner):
    pricing = runner.workspace / "pricing.py"
    labels = runner.workspace / "labels.py"
    labels.write_text("def label(x): return x.upper()\n")
    pricing.write_text("def total(x, discount=0): return sum(x)-discount\n")
    prompt = "Revise pricing.py to use a percentage discount. Preserve labels.py unchanged."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets.update(map(str, (pricing, labels)))
    generation.owned_edit(broker, pricing, pricing.read_text(), "def total(x, discount=0): return sum(x)*(1-discount/100)\n")
    broker.record_result("apply_text_patch", {"path": str(pricing)}, receipts.result({
        "changed_files": [{"path": str(pricing)}], "status": "patched",
    }))
    required, missing = broker.completion_report(prompt)
    assert "changed_files" in required and "changed_files" not in missing
    assert broker.last_completion_report["edit_targets"] == [str(pricing)]
    assert broker.last_completion_report["turn_id"] == broker.turn_id
    assert broker.last_completion_report["workspace"] == str(runner.workspace.resolve())


def test_completion_does_not_drop_second_requested_edit(variant, runner):
    prompt = "Edit first.py and second.py to return True."
    broker = receipts.begin(runner, prompt)
    for name in ("first.py", "second.py"):
        (runner.workspace / name).write_text("return False\n")
    target = runner.workspace / "first.py"
    generation.owned_edit(broker, target, "return False\n", "return True\n")
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "changed_files": [{"path": str(target)}], "status": "patched",
    }))
    assert "changed_files" in broker.completion_report(prompt)[1]
    assert len(broker.last_completion_report["edit_targets"]) == 2


@pytest.mark.parametrize("context", [
    "as used by the existing client.py caller",
    "as called by client.py",
    "as required by client.py",
    "for compatibility with client.py",
    "using client.py as a reference",
])
def test_contextual_caller_is_not_a_named_edit_target(variant, runner, context):
    (runner.workspace / "client.py").write_text("from engine import score_items\n")
    prompt = f"Extend that score_items function with an optional scale parameter {context}. Preserve the other module's behavior."
    assert variant._explicit_edit_target_tokens(variant._APP.base, prompt) == ()
    assert variant._named_file_operation_request(
        variant._APP.base, runner.workspace, variant.ACCESS_MODE_FULL, prompt,
    ) is None
    assert variant._explicit_edit_target_tokens(
        variant._APP.base, f"Revise engine.py {context}. Also edit helper.py.",
    ) == ("engine.py", "helper.py")


@pytest.mark.parametrize("context", ["as used by the existing client.py caller", "for compatibility with client.py"])
def test_referential_completion_does_not_require_mutating_caller(variant, runner, context):
    target = runner.workspace / "engine.py"
    helper = runner.workspace / "helper.py"
    caller = runner.workspace / "client.py"
    target.write_text("def score_items(x): return x\n")
    helper.write_text("def label(x): return str(x)\n")
    caller.write_text("from engine import score_items\n")
    original_helper, original_caller = helper.read_bytes(), caller.read_bytes()
    prompt = f"Extend that score_items function with an optional scale parameter {context}. Preserve the other module's behavior."
    broker = receipts.begin(runner, prompt)
    broker.bind_follow_up_repair("Fix engine.py and helper.py.", prompt, (str(target), str(helper)))
    generation.owned_edit(broker, target, target.read_text(), "def score_items(x, scale=1): return x * scale\n")
    # A referential request has no explicit filename; use the actual mutation receipt.
    broker.transaction_explicit_file_targets.clear()
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "changed_files": [{"path": str(target)}], "status": "patched",
    }))
    broker.record_result("run_existing_entrypoint", {"path": "tests"}, receipts.result({
        "returncode": 0, "command": [sys.executable, "-m", "pytest"], "test_runner": "pytest",
    }))
    required, missing = broker.completion_report(prompt)
    assert "changed_files" in required and not missing
    assert broker.last_completion_report["edit_targets"] == [str(target)]
    assert helper.read_bytes() == original_helper and caller.read_bytes() == original_caller
    target.write_text("external change\n")
    assert "changed_files" in broker.completion_report(prompt)[1]


@pytest.mark.parametrize("exception_type", ["ValueError", "TypeError", "RuntimeError", "Exception"])
def test_requested_exception_is_not_reported_failure(variant, exception_type):
    prior = "Create engine.py with a score function."
    for prompt in (
        f"Extend that score function, raising {exception_type} for invalid input.",
        f"Reject invalid score inputs with {exception_type}.",
        f"Raise {exception_type} when the score is invalid.",
    ):
        assert not variant._diagnostic_failure_feedback(prompt, prior)
    assert variant._diagnostic_failure_feedback(f"That score function raises {exception_type} unexpectedly.", prior)
    assert variant._diagnostic_failure_feedback(f"{exception_type}: invalid score", prior)


@pytest.mark.parametrize("mode", ["revision", "revision_with_tests", "failure_feedback", "required_tests_missing"])
def test_followup_report_separates_saved_revision_from_failure_validation(variant, runner, monkeypatch, mode):
    target = runner.workspace / "engine.py"
    target.write_text("def score(value): return value\n")
    (runner.workspace / "client.py").write_text("from engine import score\n")
    (runner.workspace / "tests").mkdir()
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]
    calls = []

    async def model_pass(self, prompt, callback=None):
        broker = self._tool_permission_broker
        calls.append(broker.follow_up_outcome_feedback)
        original = target.read_text()
        updated = ("def score(value): return value + 1\n" if len(calls) == 1 else
                   "def score(value, scale=1):\n    if scale < 0: raise ValueError('scale')\n    return value * scale\n")
        generation.owned_edit(broker, target, original, updated)
        broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
            "changed_files": [{"path": str(target)}], "status": "patched",
        }))
        if len(calls) == 2 and mode in {"revision_with_tests", "failure_feedback"}:
            broker.record_result("run_existing_entrypoint", {"path": "tests"}, receipts.result({
                "returncode": 0, "command": [sys.executable, "-m", "pytest", "tests"], "test_runner": "pytest",
            }))
        return "Saved the requested changes."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    asyncio.run(runner._run_async("Revise engine.py to accept a scale parameter."))
    prompt = ("The engine.py score function still raises ValueError for negative inputs. Fix it." if mode == "failure_feedback" else
              "Extend that score function with an optional scale parameter as used by client.py. Reject negative scales with ValueError.")
    if mode == "required_tests_missing":
        prompt += " Run tests and confirm they pass."
    answer = asyncio.run(runner._run_async(prompt))
    assert calls[:2] == [False, mode == "failure_feedback"]
    expected_status = "partial" if mode in {"failure_feedback", "required_tests_missing"} else "verified"
    assert f"[Completion status: {expected_status}]" in answer
    if mode == "failure_feedback":
        assert "requested repair behavior" in answer
    elif mode == "revision":
        assert "[Behavior status: unverified]" in answer
    elif mode == "revision_with_tests":
        assert "only the checks actually executed" in answer


def test_named_edit_preserves_all_requested_targets_beyond_caller_context(variant, runner):
    for name in ("engine.py", "client.py", "helper.py"):
        (runner.workspace / name).write_text("old\n")
    prompt = "Edit engine.py as used by client.py, then edit helper.py."
    assert variant._named_file_operation_request(
        variant._APP.base, runner.workspace, variant.ACCESS_MODE_FULL, prompt,
    ) is None
    broker = receipts.begin(runner, prompt)
    target = runner.workspace / "engine.py"
    generation.owned_edit(broker, target, "old\n", "new\n")
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "changed_files": [{"path": str(target)}], "status": "patched",
    }))
    assert "changed_files" in broker.completion_report(prompt)[1]
    assert set(broker.last_completion_report["edit_targets"]) == {
        str(target), str(runner.workspace / "helper.py"),
    }


def test_completion_does_not_reuse_a_patch_receipt_after_external_edit(variant, runner):
    target = runner.workspace / "state.txt"
    prompt = "Edit state.txt to contain beta."
    broker = receipts.begin(runner, prompt)
    generation.owned_edit(broker, target, "alpha", "beta")
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "changed_files": [{"path": str(target)}], "status": "patched",
    }))
    assert "changed_files" not in broker.completion_report(prompt)[1]
    target.write_text("external revision")
    assert "changed_files" in broker.completion_report(prompt)[1]


def test_exact_phrase_checks_requested_section_and_preserved_bytes(variant, runner):
    target = runner.workspace / "source.md"
    prompt = ("Revise only the Transfer paragraph in source.md. Keep it one paragraph of 3 to 12 words. "
              "Include the exact phrase independent checks. Preserve all other sections byte-for-byte.")
    original = "# Overview\r\n\r\nindependent checks outside\r\n\r\n# Transfer\r\n\r\nold transfer text\r\n\r\n# Closing\r\n\r\nunchanged\r\n"
    broker = receipts.begin(runner, prompt)
    generation.owned_edit(broker, target, original, original.replace("old transfer text", "Independent checks remain important"))
    assert any("case-sensitive" in issue for issue in variant._saved_text_contract_issues(broker, prompt, []))
    target.write_bytes(original.replace("old transfer text", "These independent checks remain important").encode())
    assert variant._saved_text_contract_issues(broker, prompt, []) == []
    target.write_bytes(target.read_bytes().replace(b"unchanged", b"changed"))
    assert any("outside" in issue for issue in variant._saved_text_contract_issues(broker, prompt, []))


@pytest.mark.parametrize("value,expected", [(11, "11"), (True, "true"), ("kept-label", "kept-label"), ({"x": 1}, None)])
def test_flat_scalar_is_grounded_in_current_complete_read(variant, runner, value, expected):
    target = runner.workspace / "selected.json"
    body = json.dumps({"value": value})
    target.write_text(body)
    prompt = "Read selected.json without edits or MCP calls. Answer only the value."
    broker = receipts.begin(runner, prompt)
    receipt = {"body": body, "sha256": hashlib.sha256(body.encode()).hexdigest(),
               "turn_id": broker.turn_id, "workspace": str(broker.workspace.resolve())}
    assert variant._verified_workspace_scalar_answer(broker, prompt, {str(target): receipt}) == expected
    for key, bad in (("turn_id", "older-turn"), ("workspace", "other-workspace"), ("sha256", "wrong")):
        assert variant._verified_workspace_scalar_answer(broker, prompt, {str(target): {**receipt, key: bad}}) is None


@pytest.mark.parametrize("tail,expected", [
    ("30", {"path": "calculator.py", "timeout_seconds": 30}),
    ("true", None), ("99999999", None), ('"30"', None),
])
def test_ling_captured_missing_numeric_sibling_is_schema_bounded(variant, tail, expected):
    schema = {"type": "object", "required": ["path"], "additionalProperties": False, "properties": {
        "path": {"type": "string"}, "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 120},
    }}
    raw = {"path": "calculator.py</arg_value><arg_key>timeout_seconds</arg_key>\n<arg_value>" + tail}
    repaired, changes, errors = variant._repair_tool_argument_transport(raw, schema)
    if expected is None:
        assert errors and repaired == raw and not changes
    else:
        assert repaired == expected and changes and not errors
        assert variant._preserves_valid_tool_arguments(raw, repaired)


def test_transport_never_invents_content_or_overwrites_valid_sibling(variant):
    schema = {"type": "object", "properties": {"path": {"type": "string"},
              "content": {"type": "string"}, "timeout_seconds": {"type": "integer"}}}
    for raw in (
        {"path": "state.txt</arg_value><arg_key>content</arg_key>\n<arg_value>invented"},
        {"path": "state.txt</arg_value><arg_key>timeout_seconds</arg_key>\n<arg_value>30", "timeout_seconds": 60},
    ):
        assert variant._repair_tool_argument_boundaries(raw, schema) == (raw, [])
    raw = {"path": "state.txt", "content": "literal </arg_value><arg_key>markup</arg_key>"}
    assert variant._repair_tool_argument_boundaries(raw, schema) == (raw, [])


@pytest.mark.parametrize("body,prompt,expected", [
    ('{"balance": 2.5}', "Read account.json. Answer only the balance.", "2.5"),
    ('{"balance": 2.5}', "Read account.json. Answer only the balance in JSON.", None),
    ('{"balance": 2.5, "balance": 3}', "Read account.json. Answer only the balance.", None),
])
def test_scalar_binding_is_field_generic_and_rejects_ambiguity(variant, runner, body, prompt, expected):
    target = runner.workspace / "account.json"
    target.write_text(body)
    broker = receipts.begin(runner, prompt)
    captured = {str(target): {"body": body, "sha256": hashlib.sha256(body.encode()).hexdigest(),
                             "turn_id": broker.turn_id, "workspace": str(broker.workspace.resolve())}}
    assert variant._verified_workspace_scalar_answer(broker, prompt, captured) == expected
    target.write_text('{"balance": 99}')
    assert variant._verified_workspace_scalar_answer(broker, prompt, captured) is None


def test_missing_read_path_is_structured_without_dispatch(variant, runner):
    broker = receipts.begin(runner, "Read missing.txt without edits.")
    host = boundaries.host_for(variant, broker)
    result = asyncio.run(host.call_tool("read_file", {"path": "missing.txt"}))
    assert result.isError and result.structuredContent["status"] == "path_resolution_error"
    assert result.structuredContent["dispatch_attempted"] is False
    assert not host.dispatched


def test_relative_read_resolves_only_in_current_workspace(variant, runner):
    target = runner.workspace / "state.txt"
    target.write_text("current")
    broker = receipts.begin(runner, "Read state.txt without edits.")
    host = boundaries.host_for(variant, broker)
    asyncio.run(host.call_tool("read_file", {"path": "state.txt"}))
    assert host.dispatched[-1] == ("read_file", {"path": str(target.resolve())})


def test_failed_requested_test_entrypoint_is_reported_without_replay(variant, executable_runner):
    runner = executable_runner
    target = runner.workspace / "run_checks.py"
    target.write_text("import sys\nfrom pathlib import Path\nwith Path('executions.txt').open('a') as log: log.write('ran\\n')\n"
                      "print('check failed', file=sys.stderr)\nraise SystemExit(1)\n")
    events = []
    answer = asyncio.run(runner._run_async(
        "Run run_checks.py and report its failing check. Do not edit or retry it.",
        lambda role, message: events.append((role, message)),
    ))
    assert "Exit code: 1" in answer and "check failed" in answer
    receipts_seen = [json.loads(message) for role, message in events if role == "completion_receipt"]
    assert any("entrypoint_executed" in item["verified"] for item in receipts_seen)
    assert target.read_text().count("raise SystemExit(1)") == 1
    assert (runner.workspace / "executions.txt").read_text() == "ran\n"


def test_direct_entrypoint_honors_the_actual_permission_broker(variant, runner, monkeypatch):
    target = runner.workspace / "main.py"
    marker = runner.workspace / "executed.txt"
    target.write_text("from pathlib import Path\nPath('executed.txt').write_text('ran')\n")
    prompt = "Run main.py and report its exit status."
    broker = receipts.begin(runner, prompt)
    observed = []

    def deny(name, arguments):
        observed.append((name, arguments))
        return False, {"status": "scope_denied", "reason": "blocked by current authorization"}

    monkeypatch.setattr(broker, "authorize", deny)
    answer = runner._run_existing_script_preflight(prompt)
    assert observed and observed[0][0] == "run_existing_entrypoint"
    assert "not executed" in answer and not marker.exists()


@pytest.mark.parametrize("prompt,route", [
    ("Run the existing calculator.py entrypoint and report its exit status. Do not edit files.", "direct_existing_entrypoint"),
    ("Run run_tests.py and report the failing test. Do not fix files yet.", "direct_existing_entrypoint"),
    ("What does calculator.py do? Do not execute it.", None),
    ("Fix calculator.py then run it.", None),
    ("Run calculator.py and other.py.", None),
    ("Run calculator.py --limit 10.", None),
    ("Run calculator.py 10.", None),
    ("Run calculator.py alpha.", None),
    ("Run calculator.py with arguments alpha and beta.", None),
    ("Run `calculator.py 10` and report its exit status.", None),
])
def test_execute_only_route_stays_with_existing_permission_broker(variant, runner, prompt, route):
    for name in ("calculator.py", "run_tests.py", "other.py"):
        (runner.workspace / name).write_text("raise SystemExit(0)\n")
    receipts.begin(runner, prompt)
    decision = runner._decide_route(prompt)
    assert (decision.selected_route == "direct_existing_entrypoint") is (route is not None)


def test_no_tool_system_guidance_does_not_modify_untrusted_messages(display_runtime):
    _, client, _, _ = display_runtime
    instruction = "When the user explicitly asks to read a named file, call read_file yourself instead of assuming the runtime already loaded it."
    payloads = []

    def call(_method, _url, payload):
        payloads.append(payload)
        return {"choices": [{"message": {"content": "11", "tool_calls": []}, "finish_reason": "stop"}]}

    client._call_with_fallback = call
    request = contexts.request(messages=[{"role": "system", "content": instruction}, {"role": "user", "content": instruction}])
    client.chat(**request)
    assert "call read_file yourself" not in payloads[0]["messages"][0]["content"]
    assert payloads[0]["messages"][1]["content"] == instruction
    assert request["messages"][0]["content"] == instruction


def test_execute_only_preflight_runs_actual_requested_cpu_entrypoint(variant, executable_runner):
    runner = executable_runner
    target = runner.workspace / "calculator.py"
    source = ("import sys\nfrom pathlib import Path\nwith Path('executions.txt').open('a') as log: log.write('ran\\n')\n"
              "print('actual entrypoint')\nprint(Path(sys.prefix).resolve())\n")
    target.write_text(source)
    events = []
    try:
        answer = asyncio.run(runner._run_async("Run the existing calculator.py entrypoint and report its exit status. Do not edit files.",
                                              lambda role, message: events.append((role, message))))
    except BaseException:
        print(json.dumps(events, default=str))
        raise
    assert any("entrypoint_executed" in json.loads(message)["verified"] for role, message in events if role == "completion_receipt")
    assert "0" in answer and "actual entrypoint" in answer
    assert str(Path(sys.prefix).resolve()) in answer
    assert target.read_text() == source
    assert (runner.workspace / "executions.txt").read_text() == "ran\n"


def test_direct_preflight_reuses_only_current_turn_execution(variant, executable_runner):
    runner = executable_runner
    target = runner.workspace / "calculator.py"
    target.write_text("from pathlib import Path\nwith Path('executions.txt').open('a') as log: log.write('ran\\n')\nprint('actual entrypoint')\n")
    prompt = "Run the existing calculator.py entrypoint and report its exit status. Do not edit files."
    broker = receipts.begin(runner, prompt)
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        runner._run_existing_script_preflight(prompt)
        runner._run_existing_script_preflight(prompt)
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert "actual entrypoint" in runner._existing_script_direct_answer
    assert (runner.workspace / "executions.txt").read_text() == "ran\n"


def test_answer_repair_retains_same_turn_context_without_authorizing_tools(variant, runner, monkeypatch):
    for name, text in (("a.txt", "red\n"), ("b.txt", "blue\n")):
        (runner.workspace / name).write_text(text)
    calls = []
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]

    async def model_pass(self, prompt, callback=None):
        self._route_decision = self._decide_route(prompt)
        context = self._build_read_only_workspace_context("Read a.txt and b.txt. No edits.")
        rendered = self._user_message(prompt, context)
        calls.append((self._route_decision.selected_route, rendered))
        return "red and blue" if "Answer-only correction:" in prompt else "<tool_call><function=read_file>{}</function></tool_call>"

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async("Read a.txt and b.txt and report their contents. Do not edit or execute anything."))
    assert answer == "red and blue" and len(calls) == 2
    assert calls[1][0] == "simple_answer" and "red" in calls[1][1] and "blue" in calls[1][1]
    assert not hasattr(runner, "_qubitz_answer_repair_context")


@pytest.mark.parametrize("field,value", [("turn_id", "stale"), ("workspace", "elsewhere"), ("sha256", "bad")])
def test_answer_repair_rejects_stale_or_tampered_context(variant, runner, field, value):
    broker = receipts.begin(runner, "Read a.txt without edits.")
    runner._route_decision = SimpleNamespace(selected_route="read_only_workspace")
    runner._user_message(broker.current_prompt, "current verified excerpt: red")
    runner._qubitz_answer_repair_context = {**runner._qubitz_current_answer_context, field: value}
    runner._route_decision = SimpleNamespace(selected_route="simple_answer")
    rendered = runner._user_message("Answer-only correction", "")
    assert "current verified excerpt: red" not in rendered


def test_raw_native_repetition_survives_clean_generation_quality(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = []
    argument = json.dumps({"path": "run_tests.py</arg_value><arg_key>timeout_seconds</arg_key>\n<arg_value>60"})
    for index in range(2):
        rows.extend([
            {"turn": 1, "time": index * 2, "kind": "http_request", "request_id": str(index), "payload": {}},
            {"turn": 1, "time": index * 2 + 1, "kind": "http_stream_line", "request_id": str(index),
             "line": "data: " + json.dumps({"choices": [{"finish_reason": "tool_calls", "delta": {
                 "tool_calls": [{"index": 0, "function": {"name": "run_existing_entrypoint", "arguments": argument}}],
             }}]})},
        ])
    path.write_text("\n".join(map(json.dumps, rows)) + "\n")
    quality = generation_quality(path, 1)
    assert quality["status"] == "clean"
    assert quality["workflow"]["raw_argument_repetitions"][0]["count"] == 2
    assert all(call["delimiter_markers_present"] for call in quality["workflow"]["native_tool_calls"])


def test_lifecycle_receipts_are_append_only(tmp_path):
    coordinator_event(tmp_path, "start", registered_rows=54)
    before = (tmp_path / "coordinator_events.jsonl").read_bytes()
    coordinator_event(tmp_path, "return", status="failed")
    assert (tmp_path / "coordinator_events.jsonl").read_bytes().startswith(before)
    assert len((tmp_path / "coordinator_events.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("failure", [False, True])
def test_evaluator_cleanup_pumps_real_tk_and_preserves_errors(failure):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    ticks = []
    events = []
    owner = threading.get_ident()

    def tick():
        ticks.append(threading.get_ident())
        root.after(10, tick)

    def cleanup():
        assert threading.get_ident() != owner
        time.sleep(0.12)
        if failure:
            raise RuntimeError("owned shutdown failed")

    root.after(0, tick)
    try:
        observer = SimpleNamespace(emit=lambda kind, **data: events.append((kind, data)))
        if failure:
            with pytest.raises(RuntimeError, match="owned shutdown failed"):
                shutdown_runtime_responsively(SimpleNamespace(reset_runtime=cleanup), SimpleNamespace(root=root), observer)
        else:
            shutdown_runtime_responsively(SimpleNamespace(reset_runtime=cleanup), SimpleNamespace(root=root), observer)
        assert len(ticks) > 2 and set(ticks) == {owner}
        assert events[-1][1]["succeeded"] is not failure
    finally:
        root.destroy()


@pytest.mark.parametrize("failure", [False, True])
def test_actual_workspace_button_publishes_only_after_owned_cleanup(variant, tmp_path, monkeypatch, failure):
    base = variant._APP.base
    old, new = tmp_path / "a", tmp_path / "b"
    old.mkdir()
    new.mkdir()
    config = base.AgentConfig(workspace=old, runtime_workspace=receipts.ROOT)
    try:
        gui = base.QubitzGUI(config)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root = gui.root
    root.withdraw()
    cleanup_threads = []
    main = threading.get_ident()
    errors = []
    callback_errors = []
    ticks = []
    original_agent = gui.agent
    original_cleanup = original_agent.reset_runtime

    def tick():
        ticks.append(threading.get_ident())
        root.after(10, tick)

    def cleanup():
        cleanup_threads.append(threading.get_ident())
        time.sleep(0.15)
        if failure:
            raise RuntimeError("owned cleanup failed")

    monkeypatch.setattr(base, "pick_workspace_directory", lambda *_args, **_kwargs: new)
    monkeypatch.setattr(base, "configure_project_environment", lambda _path: None)
    monkeypatch.setattr(original_agent, "reset_runtime", cleanup)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: errors.append(args))
    root.report_callback_exception = lambda *args: callback_errors.append(args)
    gui.pending_prompts = ["old target"]
    root.after(0, tick)
    try:
        gui.change_workspace_button.invoke()
        assert not callback_errors
        assert gui.config.workspace == old and gui.busy and not gui.pending_prompts
        assert gui.send_button.cget("text") == "Queue"
        gui.send_prompt()
        assert "no prompt was dispatched" in gui.transcript.get("1.0", "end")
        transition = gui._qubitz_workspace_transition
        deadline = time.monotonic() + 10
        while not transition["finished"].is_set() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert transition["finished"].is_set(), "Workspace preparation did not finish"
        deadline = time.monotonic() + 2
        while gui.busy and not errors and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        if failure:
            assert gui.config.workspace == old and gui.busy and errors
            assert gui._qubitz_workspace_transition is not None
        else:
            assert gui.config.workspace == new and not gui.busy and not errors
            assert gui.agent.workspace == new and gui.agent.runtime_workspace == receipts.ROOT
            assert gui.send_button.cget("text") == "Send"
            assert gui._qubitz_workspace_transition is None
            transcript = gui.transcript.get("1.0", "end")
            assert "Workspace changed to " in transcript and "Pending prompts were cleared." in transcript
        assert not callback_errors
        assert len(ticks) > 2 and set(ticks) == {main}
        assert cleanup_threads and main not in cleanup_threads
    finally:
        original_cleanup()
        if gui.agent is not original_agent:
            gui.agent.reset_runtime()
        transition = getattr(gui, "_qubitz_workspace_transition", None)
        if transition and transition.get("agent") is not None:
            transition["agent"].reset_runtime()
        root.destroy()


def test_legitimate_json_answer_is_not_rewritten_as_scalar(variant, runner, monkeypatch):
    target = runner.workspace / "account.json"
    target.write_text('{"balance": 2.5}')
    answer = '{"balance": 2.5}'
    calls = []
    parent = next(cls for cls in type(runner).__mro__ if cls.__name__ == "HardenedAgentRunner").__mro__[1]

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        return answer

    monkeypatch.setattr(parent, "_run_async", model_pass)
    result = asyncio.run(runner._run_async("Read account.json without edits. Answer only with a JSON object containing balance."))
    assert result == answer and len(calls) == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_workspace_button_preserves_reasoning_transport(variant, tmp_path, monkeypatch, enabled):
    base = variant._APP.base
    old, new = tmp_path / "original", tmp_path / "selected"
    old.mkdir()
    new.mkdir()
    config = base.AgentConfig(workspace=old, runtime_workspace=receipts.ROOT)
    try:
        gui = base.QubitzGUI(config)
    except tk.TclError as exc:
        pytest.skip(f"Tk display unavailable: {exc}")
    root = gui.root
    root.withdraw()
    original_agent = gui.agent
    callback_errors = []
    root.report_callback_exception = lambda *args: callback_errors.append(args)
    monkeypatch.setattr(base, "pick_workspace_directory", lambda *_args, **_kwargs: new)
    monkeypatch.setattr(base, "configure_project_environment", lambda _path: None)
    gui.reasoning_visible_var.set(enabled)
    gui._toggle_reasoning_display()
    try:
        gui.change_workspace_button.invoke()
        deadline = time.monotonic() + 3
        while gui.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        assert not gui.busy and not callback_errors
        assert gui.config.workspace == new and gui.agent is not original_agent
        assert gui.reasoning_visible_var.get() is enabled
        assert getattr(gui.config, "show_reasoning", False) is enabled
        assert gui.agent.config is gui.config
        # Exercise the actual client, broker callback, GUI queue and Tk text tags.
        client = base.LlamaCppClient(gui.agent.config, [SimpleNamespace(label="direct", base_url="http://fixture")])
        monkeypatch.setattr(client, "_probe_with_fallback", lambda _path: {})
        observed = []
        run_id = getattr(gui, "_active_gui_run_id", 0)

        def emit(kind, message):
            observed.append((kind, message))
            gui.event_queue.put((run_id, kind, message))

        broker = gui.agent._tool_permission_broker
        broker.begin_turn("Explain the result briefly without tools.", emit)
        token = variant._TOOL_CALL_CONTEXT.set(broker)
        payloads, answers = reasoning.install_stream(monkeypatch, base, client, [
            reasoning.chunk(reasoning_content="Checking the supplied value."),
            reasoning.chunk(content="The answer is nine."),
        ])
        try:
            response = client.chat(**contexts.request())
            gui._poll_events()
            assert response["message"]["content"] == "The answer is nine."
            assert "".join(answers) == "The answer is nine."
            assert len(payloads) == 1 and payloads[0]["tools"] == []
            status = json.loads(next(message for kind, message in observed if kind == "reasoning_status"))
            assert status["received"] == len("Checking the supplied value.")
            assert status["display_enabled"] is enabled
            assert status["forwarded"] == (status["received"] if enabled else 0)
            rendered = gui.transcript.get("1.0", "end-1c")
            if enabled:
                assert "Checking the supplied value." in rendered
                assert gui.transcript.tag_ranges("role_reasoning")
                assert gui.transcript.tag_cget("role_reasoning", "foreground") == "#969696"
            else:
                assert "Checking the supplied value." not in rendered
                assert not gui.transcript.tag_ranges("role_reasoning")
        finally:
            base.set_qubitz_stream_callback(None)
            variant._TOOL_CALL_CONTEXT.reset(token)
            broker.end_turn()
    finally:
        original_agent.reset_runtime()
        if gui.agent is not original_agent:
            gui.agent.reset_runtime()
        for timer in root.tk.call("after", "info"):
            root.after_cancel(timer)
        root.destroy()


def test_memory_context_merges_markdown_without_losing_provenance(variant, tmp_path):
    base = variant._APP.base
    variant._install_daily_memory_store(SimpleNamespace(base=base))
    store = base.MemoryStore(tmp_path)
    store.current_path.write_text(
        "# Qubitz Memory\n\n## Notes\n\n- Same fact: use the project interpreter.\n",
        encoding="utf-8",
    )
    (store.memory_dir / "MEMORY_20261007.md").write_text(
        "# Qubitz Daily Memory\n\n## Entries\n\n- Same fact: use the project interpreter.\n\n- Timeout is 30 seconds.\n",
        encoding="utf-8",
    )
    (store.memory_dir / "MEMORY_20261006.md").write_text(
        "# Qubitz Daily Memory\n\n## Entries\n\n- Timeout is 60 seconds.\n",
        encoding="utf-8",
    )
    store._consolidate_memory_file()
    context = store.build_context("interpreter timeout")
    assert context.count("Same fact: use the project interpreter.") == 1
    assert "MEMORY.md" in context and "MEMORY_20261007.md" in context
    assert "Timeout is 30 seconds." in context and "Timeout is 60 seconds." in context
