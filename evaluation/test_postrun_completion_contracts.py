"""Preservation, candidate admission, dispatch and policy regressions without inference."""
from __future__ import annotations

import asyncio
import hashlib
import json
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation.campaign_observation import referenced_event_delta
from evaluation.case_evidence import publish_manifest
from evaluation.followup_matrix_cases import captured_entrypoint_receipts
from evaluation.solar_artifact_evidence import limits_report, semantic_issues
from runtime.artifact_contracts import html_envelope_issues
from runtime.cancellation import RequestCancelled
from runtime.default_behavior import compare, requested
from runtime.execution_contracts import ordered_entrypoints
from runtime.invocation_receipts import INVOCATION_ID

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("expression,passed", [
    ("subtotal - subtotal * discount / 100", False),
    ("subtotal if discount == 0 else subtotal - subtotal * discount / 100", True),
])
def test_default_comparison_checks_types_and_precision(expression, passed):
    before = "def total(prices):\n return sum(prices)\n"
    after = f"def total(prices, discount=0):\n subtotal=sum(prices)\n return {expression}\n"
    result = compare(before, after)
    assert (result["status"] == "passed_bounded_comparison") is passed
    if not passed:
        assert any(not item["matched"] for item in result["cases"])


def test_default_comparison_refuses_imports_or_effects():
    result = compare("def f(x):\n return x\n", "import os\ndef f(x,y=0):\n return x\n")
    assert result["status"] == "unavailable"


def test_default_preservation_is_not_invented_from_a_negative_directive():
    assert not requested("Do not preserve default behavior.")
    assert not requested("We are not preserving omitted-argument behavior.")
    assert requested("Without running tools, preserve omitted-discount behavior.")


def test_changed_default_exception_details_are_not_equal():
    result = compare("def f(x):\n raise ValueError('old')\n",
                     "def f(x, optional=0):\n raise ValueError('new')\n")
    assert result["status"] == "failed"


def test_candidate_exact_phrase_and_bounds_without_writing(variant, runner):
    target = runner.workspace / "source.md"
    before = "## Intake\nKeep this.\n\n## Transfer\nPrevious paragraph.\n\n## Delivery\nKeep that.\n"
    target.write_text(before)
    prompt = ("Revise only the Transfer paragraph in source.md. Use one paragraph of 40 to 80 words "
              "and include the exact phrase `independent checks`. Preserve all other sections.")
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    for phrase, expected in [("Independent checks", True), ("independent checks", False)]:
        paragraph = phrase + " " + " ".join(["evidence"] * 45) + "."
        candidate = before.replace("Previous paragraph.", paragraph).encode()
        issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): candidate})
        assert any("case-sensitive" in issue for issue in issues) is expected, issues
        assert target.read_text() == before


@pytest.mark.parametrize("words,accepted", [(39, False), (40, True), (80, True), (81, False)])
def test_candidate_paragraph_word_bounds(variant, runner, words, accepted):
    target = runner.workspace / "source.md"
    target.write_text("## Transfer\nOriginal.\n")
    prompt = "Revise the Transfer paragraph in source.md. Use one paragraph of 40 to 80 words."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    candidate = ("## Transfer\n" + " ".join(["word"] * words) + "\n").encode()
    issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): candidate})
    assert (not issues) is accepted, issues


def test_ordered_runners_require_successful_prerequisite(runner):
    for name in ("run_tests.py", "run_report.py"):
        (runner.workspace / name).write_text("print('owned fixture')\n")
    broker = receipts.begin(runner, "Run run_tests.py then run run_report.py.")
    allowed, denial = broker.authorize("run_existing_entrypoint", {"path": "run_report.py"})
    assert not allowed and denial["status"] == "ordered_execution_prerequisite"
    assert broker.authorize("run_existing_entrypoint", {"path": "run_tests.py"})[0]
    broker.record_result("run_existing_entrypoint", {"path": "run_tests.py"}, receipts.result({
        "path": "run_tests.py", "cwd": ".", "command": ["python", "run_tests.py"], "returncode": 0,
    }))
    assert broker.authorize("run_existing_entrypoint", {"path": "run_report.py"})[0], broker.evidence


def test_repaired_execution_has_canonical_host_lineage(variant, runner):
    target = runner.workspace / "run_tests.py"
    target.write_text("print('owned fixture executed')\n")
    broker = receipts.begin(runner, "Run run_tests.py.")
    server = variant._build_local_mcp_server(variant._APP.base, runner.workspace,
                                            variant._APP.runtime_workspace, Path(variant.__file__))
    host = boundaries.host_for(variant, broker, server)
    host._qubitz_tool_contracts["run_existing_entrypoint"] = SimpleNamespace(
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}, "cwd": {"type": "string"}},
                     "required": ["path"], "additionalProperties": False}, outputSchema=None,
    )
    original = {"path": 'run_tests.py</arg_value><arg_key>cwd</arg_key><arg_value>"."', "cwd": "."}
    token = INVOCATION_ID.set("owned-fixture-execution")
    try:
        result = asyncio.run(host.call_tool("run_existing_entrypoint", original))
    finally:
        INVOCATION_ID.reset(token)
    assert result.structuredContent.get("returncode") == 0, result
    assert result.structuredContent["actual_wait"] is True
    execution = result.structuredContent["execution_receipt"]
    assert execution["started"]["identity"]["pid"] > 0
    assert execution["exit"]["actual_wait"] is True
    assert execution["supervisor_actual_wait"] is True
    assert execution["output_retention"]["complete"] is True
    output_files = execution["output_retention"]["files"]
    assert Path(output_files[0]["path"]).read_bytes() == b"owned fixture executed\n"
    assert Path(output_files[1]["path"]).read_bytes() == b""
    row = {"turn": 1, "name": "run_existing_entrypoint", "capture_origin": "mcp_host",
           "invocation_id": "owned-fixture-execution", "arguments": original,
           "dispatch_receipt": result._qubitz_invocation_receipt,
           "result": {"structured_content": result.structuredContent, "is_error": False}}
    assert captured_entrypoint_receipts(runner.workspace, {"turn": 1, "tools": [row]}, "run_tests.py")
    row["dispatch_receipt"]["invocation_id"] = "wrong-invocation"
    assert not captured_entrypoint_receipts(runner.workspace, {"turn": 1, "tools": [row]}, "run_tests.py")


def test_managed_child_execution_requires_bound_wait_and_exact_target(variant, runner):
    target = runner.workspace / "run_report.py"
    target.write_text("print('managed report')\n")
    command = [sys.executable, str(target)]
    receipt = {}
    code, stdout, stderr = variant._run_owned_process(
        command, cwd=runner.workspace, timeout_seconds=30, execution_receipt=receipt,
    )
    assert code == 0 and stdout == b"managed report\n" and stderr == b""
    result_data = {
        "command": command,
        "cwd": str(runner.workspace.resolve()),
        "returncode": code,
        "execution_receipt": receipt,
    }
    row = {
        "turn": 1,
        "name": "run_command",
        "capture_origin": "mcp_host",
        "arguments": {"command": f'"{sys.executable}" run_report.py'},
        "result": {"structured_content": result_data, "is_error": False},
    }
    assert captured_entrypoint_receipts(runner.workspace, {"turn": 1, "tools": [row]}, "run_report.py")
    invalid = {**result_data, "execution_receipt": {**receipt, "supervisor_actual_wait": False}}
    row["result"] = {"structured_content": invalid, "is_error": False}
    assert not captured_entrypoint_receipts(runner.workspace, {"turn": 1, "tools": [row]}, "run_report.py")
    invalid_exit = {**receipt["exit"], "identity": {**receipt["exit"]["identity"], "pid": -1}}
    invalid = {**result_data, "execution_receipt": {**receipt, "exit": invalid_exit}}
    row["result"] = {"structured_content": invalid, "is_error": False}
    assert not captured_entrypoint_receipts(runner.workspace, {"turn": 1, "tools": [row]}, "run_report.py")


def test_same_test_command_followup_keeps_execution_and_read_obligations(variant, runner):
    prompt = "Now rerun the same test command, then run the existing run_report.py. Verify the report file."
    broker = receipts.begin(runner, prompt)
    assert variant._requests_test_execution(prompt)
    assert variant._entrypoint_requires_command_binding(prompt)
    assert {"test_command_executed", "entrypoint_executed", "workspace_file_read"} <= broker.required_postconditions


def test_only_root_envelope_leakage_is_rejected():
    assert html_envelope_issues('<html lang="en">", "complete": false}<head></head></html>')
    assert not html_envelope_issues('<html><body><pre>\", "complete": false}</pre></body></html>')


def test_limits_report_accepts_paraphrases_without_magic_keyword():
    answer = ("Changed the initial Earth focus and speed. Source inspection and saved bytes show those edits. "
              "The headless browser load produced a canvas; no visual framing, interaction controls "
              "or scientific accuracy was independently verified. These remain unverified.")
    assert limits_report(answer)["passed"]
    assert not limits_report(answer + " It is fully accurate.")["passed"]
    assert not limits_report("Everything works.")["passed"]


def test_frozen_world_positions_and_origin_centered_rings_fail():
    state = {"bodies": {"Earth": {"position": [10, 0, 0], "projected": [0, 0, 0]},
                        "Saturn": {"position": [20, 0, 0], "projected": [0.3, 0, 0]}},
             "time": 0, "rings": [{"body": "Saturn", "position": [0, 0, 0]}]}
    issues = semantic_issues(state, {**state, "time": 100}, {**state, "time": 200}, state)
    assert "planet_world_position_motion_not_verified" in issues
    assert "ring_body_colocation_not_verified" in issues


def test_full_event_reference_keeps_incomplete_tail_and_exact_bytes(tmp_path):
    source = tmp_path / "events.jsonl"
    packet = b'{"kind":"tool_result","content":"' + b"x" * 20000 + b'"}\n'
    source.write_bytes(packet + b'{"kind":')
    rows, offset = referenced_event_delta(source, 0)
    assert offset == len(packet) and rows[0]["truncated_in_receipt"]
    assert rows[0]["sha256"] == hashlib.sha256(packet).hexdigest()
    assert source.read_bytes()[offset:] == b'{"kind":'


def test_manifest_does_not_attribute_root_personal_journal(tmp_path):
    outside = tmp_path / "personal" / "conversations" / "chat.txt"
    outside.parent.mkdir(parents=True)
    outside.write_text("private unrelated chat")
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    journal = SimpleNamespace(close=lambda **_: None, error="", _thread=SimpleNamespace(is_alive=lambda: False), path=outside)
    with pytest.raises(ValueError, match="shared or unrelated"):
        publish_manifest(attempt, {"variant": "test", "scenario": "case"}, journal)


@pytest.mark.parametrize("prompt,expected", [
    ("Run a.py then run b.py.", ("a.py", "b.py")),
    ("Run a.py before running b.py.", ("a.py", "b.py")),
    ("Run a.py after running b.py.", ("b.py", "a.py")),
    ("Before running a.py, run b.py.", ("b.py", "a.py")),
])
def test_explicit_execution_order(prompt, expected):
    assert ordered_entrypoints(prompt, ("a.py", "b.py")) == expected


def test_manifest_keeps_invalid_complete_records_as_incomplete_evidence(tmp_path):
    (tmp_path / "events.jsonl").write_bytes(b'[]\n{"kind":"tool_result","sequence":2}\n')
    result = publish_manifest(tmp_path, {"variant": "test", "scenario": "case"}, require_journal=False)
    assert result["status"] == "incomplete"
    assert "invalid_event_record_preserved" in result["issues"]
    assert (tmp_path / "event_payload_index.jsonl").read_text().count("\n") == 2


def test_thermal_host_propagates_campaign_cancellation_without_runtime_error(variant, runner):
    gate = SimpleNamespace(begin_step=lambda: False, _cancel_origin="coordinator_stop")
    token = variant._GPU_THERMAL_GATE_CONTEXT.set(gate)
    try:
        host = variant._APP.base.MCPHost(runner.workspace)
        with pytest.raises(RequestCancelled) as stopped:
            asyncio.run(host.call_tool("read_file_snapshot", {"path": "not-dispatched.txt"}))
        assert stopped.value.origin == "coordinator_stop"
    finally:
        variant._GPU_THERMAL_GATE_CONTEXT.reset(token)


def test_runner_records_evaluator_cancel_origin(runner):
    runner.request_cancel(origin="evaluator_timeout")
    assert runner._cancel_origin == "evaluator_timeout"
    assert "evaluator timeout" in runner._cancel_partial_answer


def _managed_entrypoint_observation(tmp_path):
    project = tmp_path / "project"
    manager = project / "runtime" / "managed_process.py"
    manager.parent.mkdir(parents=True)
    manager.write_text("# receipt path fixture\n")
    workspace = project / "evaluation" / "results" / "fixture" / "fixtures" / "a"
    workspace.mkdir(parents=True)
    (workspace / "run_tests.py").write_text("raise SystemExit(1)\n")

    token = "e1983372f10a4e33825d0b19cc180802"
    owner = project / ".cache" / "owned_commands" / "0123456789abcdef0123456789abcdef"
    owner.mkdir(parents=True)
    launch_path = owner / "process_launch.json"
    child_command = "python -u -B run_tests.py"
    launch = {"token": token, "command": child_command, "cwd": str(workspace), "kind": "command"}
    identity = {
        "pid": 202,
        "parent_pid": 101,
        "creation": "12345",
        "namespace": "linux:test-namespace",
        "executable": sys.executable,
        "command_line": shlex.join([
            sys.executable, "-u", "-B", str(manager), "--child", str(launch_path),
        ]),
    }
    receipts = {
        "process_launch.json": launch,
        "admitted.json": {"token": token},
        "process_started.json": {
            "token": token,
            "identity": identity,
            "command": child_command,
            "supervisor_pid": 101,
            "containment": "linux_subreaper",
        },
        "process_exit.json": {
            "token": token,
            "identity": identity,
            "actual_wait": True,
            "returncode": 1,
            "tree_verified": True,
            "descendant_waits": [{"pid": 202, "actual_wait": True, "returncode": 1}],
            "error": None,
        },
        "supervisor_wait.json": {"token": token, "pid": 101, "actual_wait": True, "returncode": 0},
    }
    for name, payload in receipts.items():
        (owner / name).write_text(json.dumps(payload))

    observed = {
        "turn": 1,
        "tools": [],
        "processes": [{
            "pid": 101,
            "command": [sys.executable, "-u", "-B", str(manager), str(launch_path)],
            "exit_code": 0,
        }],
    }
    return project, workspace, owner, observed


@pytest.mark.parametrize("tamper", [
    "owner_token", "child_identity", "child_wait", "tree_wait", "supervisor_wait",
    "wrong_cwd", "wrong_target", "target_as_data", "unbound_process", "wrapper_exit",
])
def test_direct_managed_entrypoint_requires_bound_execution_receipts(tmp_path, tamper):
    project, workspace, owner, observed = _managed_entrypoint_observation(tmp_path)
    if tamper == "owner_token":
        path = owner / "process_exit.json"
        value = json.loads(path.read_text())
        value["token"] = "different-token"
        path.write_text(json.dumps(value))
    elif tamper == "child_identity":
        path = owner / "process_exit.json"
        value = json.loads(path.read_text())
        value["identity"]["pid"] += 1
        path.write_text(json.dumps(value))
    elif tamper in {"child_wait", "tree_wait"}:
        path = owner / "process_exit.json"
        value = json.loads(path.read_text())
        value["actual_wait" if tamper == "child_wait" else "tree_verified"] = False
        path.write_text(json.dumps(value))
    elif tamper == "supervisor_wait":
        path = owner / "supervisor_wait.json"
        value = json.loads(path.read_text())
        value["actual_wait"] = False
        path.write_text(json.dumps(value))
    elif tamper == "wrong_cwd":
        path = owner / "process_launch.json"
        value = json.loads(path.read_text())
        value["cwd"] = str(workspace.parent)
        path.write_text(json.dumps(value))
    elif tamper == "wrong_target":
        path = owner / "process_launch.json"
        value = json.loads(path.read_text())
        value["command"] = "python run_report.py"
        path.write_text(json.dumps(value))
    elif tamper == "target_as_data":
        path = owner / "process_launch.json"
        value = json.loads(path.read_text())
        value["command"] = "python -c 'print(1)' run_tests.py"
        path.write_text(json.dumps(value))
    elif tamper == "unbound_process":
        observed["processes"] = [{"pid": 202, "command": [sys.executable, "run_tests.py"], "exit_code": 0}]
    elif tamper == "wrapper_exit":
        observed["processes"][0]["exit_code"] = 1

    assert not captured_entrypoint_receipts(
        workspace, observed, "run_tests.py", project_root=project,
    )


def test_direct_managed_entrypoint_accepts_waited_nonzero_child_exit(tmp_path):
    project, workspace, _, observed = _managed_entrypoint_observation(tmp_path)
    result = captured_entrypoint_receipts(
        workspace, observed, "run_tests.py", project_root=project,
    )
    assert len(result) == 1
    assert result[0]["source"] == "managed_process_owner_receipts"
    assert result[0]["exit_code"] == 1
    assert result[0]["actual_wait"] is True and result[0]["tree_verified"] is True
