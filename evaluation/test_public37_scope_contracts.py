"""Public-only CPU regressions for the retained permission and answer failures."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime.prohibition_scope import evaluator_path
from runtime.prohibition_scope import test_path as protected_test_path

variant = receipts.variant
runner = receipts.runner

REPAIR_PROMPT = (
    "Fix that implementation bug, then rerun the same run_tests.py command. "
    "Do not change the test or test runner."
)


def snapshot(broker, target):
    body = target.read_text(encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "path": str(target), "status": "text_snapshot", "sha256": digest,
        "size": target.stat().st_size, "content": body, "start_line": 1,
        "end_line": len(body.splitlines()),
    }))
    return digest


@pytest.mark.parametrize("constraint", [
    "Do not change the test or test runner.", "Do not edit test files.",
    "Do not modify the tests.", "Do not rewrite the test runner.",
])
def test_test_nouns_preserve_implementation_permission(variant, runner, tmp_path, constraint):
    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a-b\n", encoding="utf-8")
    prompt = "Fix calculator.py, then rerun run_tests.py. " + constraint
    broker = receipts.begin(runner, prompt)
    semantic = broker.task_intent.semantic
    assert "edit" in semantic.requested_actions
    assert "edit" not in semantic.prohibited_actions
    assert ("edit", "@class:test_files") in semantic.scoped_prohibitions
    digest = snapshot(broker, target)
    allowed, denial = broker.authorize("apply_text_patch", {
        "path": "calculator.py", "expected_sha256": digest,
        "replacements": [{"old_text": "return a-b", "new_text": "return a+b"}],
    })
    assert allowed, denial
    for name in ("run_tests.py", "test_calculator.py", "tests/data.txt"):
        allowed, denial = broker.authorize("write_file", {"path": name, "content": "changed"})
        assert not allowed and denial["policy_id"] == "protected_test_files"


def test_exact_failed_repair_prompt_allows_repair_and_rerun(runner, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("def add(a, b):\n    return a-b\n", encoding="utf-8")
    (tmp_path / "run_tests.py").write_text("assert True\n", encoding="utf-8")
    broker = receipts.begin(runner, REPAIR_PROMPT)
    digest = snapshot(broker, target)
    allowed, denial = broker.authorize("apply_text_patch", {
        "path": str(target), "expected_sha256": digest,
        "replacements": [{"old_text": "return a-b", "new_text": "return a+b"}],
    })
    assert allowed, denial
    assert broker.authorize("run_existing_entrypoint", {"path": "run_tests.py"})[0]
    assert not broker.authorize("write_file", {"path": "run_tests.py", "content": "pass"})[0]


@pytest.mark.parametrize("constraint", [
    "Do not edit anything.", "Do not change any files.", "Do not use tools.",
])
def test_blanket_prohibitions_still_block_mutation(runner, constraint):
    broker = receipts.begin(runner, "Fix calculator.py. " + constraint)
    assert not broker.authorize("apply_text_patch", {
        "path": "calculator.py", "replacements": [{"old_text": "-", "new_text": "+"}],
    })[0]


@pytest.mark.parametrize("prompt", ["Test the implementation.", "Test runner.py."])
def test_test_verb_is_still_a_verification_request(variant, prompt):
    assert "verify" in variant._analyze_semantic_intent(prompt).requested_actions


@pytest.mark.parametrize("target", ["./", "src/package.py"])
def test_real_keep_constraints_still_protect_named_targets(runner, target):
    broker = receipts.begin(runner, f"Create output.html. Keep {target} unchanged.")
    assert ("edit", target) in broker.task_intent.semantic.scoped_prohibitions
    assert not broker.authorize("write_file", {"path": target, "content": "changed"})[0]


def test_nested_results_workspace_allows_only_requested_solar_output(runner, tmp_path):
    workspace = tmp_path / "evaluation/results/measurement/fixtures/a"
    workspace.mkdir(parents=True)
    runner.workspace = workspace
    runner._tool_permission_broker.workspace = workspace
    broker = receipts.begin(runner, (
        "Create solar_system.html. Only create solar_system.html. "
        "Do not change evaluator files or run models."
    ))
    for path in ("solar_system.html", str(workspace / "solar_system.html")):
        allowed, denial = broker.authorize("write_file", {"path": path, "content": "<!doctype html>"})
        assert allowed, denial
    for path in ("probe.txt", "evaluation/evaluator.py"):
        assert not broker.authorize("write_file", {"path": path, "content": "probe"})[0]
    assert evaluator_path(tmp_path / "evaluation/evaluator.py", workspace)


def test_canonical_constraint_paths_protect_traversal_and_symlinks(tmp_path):
    workspace = tmp_path / "evaluation/results/run/fixtures/a"
    workspace.mkdir(parents=True)
    source = tmp_path / "evaluation/evaluator.py"
    source.write_text("protected\n", encoding="utf-8")
    alias = workspace / "alias.py"
    alias.symlink_to(source)
    assert not evaluator_path(workspace / "solar_system.html", workspace)
    assert evaluator_path(alias, workspace)
    assert evaluator_path(workspace / "../../../../evaluator.py", workspace)
    assert protected_test_path(workspace / "tests/fixture.txt", workspace)
    assert not protected_test_path(workspace / "calculator.py", workspace)


def test_original_scientific_solar_prompt_authorizes_its_nested_output(variant, runner, tmp_path):
    from evaluation.followup_matrix_solar import PROMPT, prepare

    workspace = tmp_path / "evaluation/results/measurement/fixtures/a"
    workspace.mkdir(parents=True)
    prepare(workspace)
    runner.workspace = workspace
    runner._tool_permission_broker.workspace = workspace
    broker = receipts.begin(runner, PROMPT)
    assert not {"service_started", "service_stopped"} & broker.required_postconditions
    allowed, denial = broker.authorize("write_file", {
        "path": "solar_system.html", "content": "<!doctype html><canvas id='scene'></canvas>",
    })
    assert allowed, denial
    assert not broker.authorize("write_file", {"path": "_probe_ls.txt", "content": "probe"})[0]
    for path in ("solar_reference.json", "three.module.js", "OrbitControls.js"):
        assert not broker.authorize("write_file", {"path": path, "content": "changed fixture"})[0]
    assert ("execute", "@class:models") in broker.task_intent.semantic.scoped_prohibitions
    assert not broker.authorize("run_command", {"command": "python model.py"})[0]


def test_equivalent_denials_share_action_target_policy(runner, tmp_path):
    broker = receipts.begin(runner, "Create output.html. Do not change evaluator files.")
    stops = []
    broker.stop_callback = stops.append
    payload = {"status": "scope_denied", "policy_id": "protected_evaluator_files", "reason": "protected"}
    assert broker.register_permission_denial("write_file", {"path": "evaluation/evaluator.py", "content": "one"}, payload) == 1
    assert not stops
    assert broker.register_permission_denial("apply_text_patches", {"patches": [{
        "path": str(tmp_path / "evaluation/evaluator.py"),
        "replacements": [{"old_text": "one", "new_text": "two"}],
    }]}, payload) == 2
    assert stops == ["repeated equivalent permission denials"]
    record = next(iter(broker.permission_denials.values()))
    assert record["targets"] == [str(tmp_path / "evaluation/evaluator.py")]
    assert record["attempts"] == 2 and record["turn_id"] == broker.turn_id
    broker.begin_turn("Create output.html.", None)
    assert not broker.permission_denial_counts and not broker.permission_denials


def test_real_host_denials_stop_equivalent_single_and_batch_calls(variant, runner, tmp_path):
    broker = receipts.begin(runner, "Edit evaluation/evaluator.py. Do not change evaluator files.")
    stops = []
    broker.stop_callback = lambda reason: stops.append(reason)
    host = variant._APP.base.MCPHost(tmp_path)
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        first = asyncio.run(host.call_tool("write_file", {"path": "evaluation/evaluator.py", "content": "one"}))
        second = asyncio.run(host.call_tool("write_files", {"files": [{
            "path": str(tmp_path / "evaluation/evaluator.py"), "content": "two",
        }]}))
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert first.isError and second.isError
    assert first.structuredContent["policy_id"] == second.structuredContent["policy_id"] == "protected_evaluator_files"
    assert stops == ["repeated equivalent permission denials"]
    assert not (tmp_path / "evaluation/evaluator.py").exists()


def test_corrected_target_is_not_stopped_or_authorized_by_old_denial(runner):
    broker = receipts.begin(runner, "Create output.html. Do not change evaluator files.")
    stops = []
    broker.stop_callback = stops.append
    denied, payload = broker.authorize("write_file", {"path": "evaluation/evaluator.py", "content": "x"})
    assert not denied
    broker.register_permission_denial("write_file", {"path": "evaluation/evaluator.py", "content": "x"}, payload)
    assert broker.authorize("write_file", {"path": "output.html", "content": "<!doctype html>"})[0]
    assert not stops


@pytest.mark.parametrize("prompt", [
    "Create output.html. Start centered on the Sun.",
    "Create a server dashboard in output.html. Start centered on the chart.",
    "Create output.html. Start paused at epoch zero.",
])
def test_visual_start_does_not_require_service_receipts(variant, prompt):
    semantic = variant._analyze_semantic_intent(prompt)
    assert not {"service_started", "service_stopped"} & semantic.required_postconditions
    assert semantic.execution_scope != "managed_service"


@pytest.mark.parametrize("prompt,required", [
    ("Start the local HTTP server.", {"service_started"}),
    ("Restart the application.", {"service_started", "service_stopped"}),
    ("Stop the MCP server.", {"service_stopped"}),
])
def test_real_managed_actions_still_require_receipts(variant, prompt, required):
    assert required <= variant._analyze_semantic_intent(prompt).required_postconditions


def complete_reads(broker, paths):
    return {str(path): {
        "turn_id": broker.turn_id, "workspace": str(broker.workspace.resolve()),
        "body": path.read_text(encoding="utf-8"),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    } for path in paths}


@pytest.mark.parametrize("field", ["state", "mode"])
def test_current_explicit_field_lines_override_stale_alias_answer(variant, runner, tmp_path, field):
    paths = [tmp_path / name for name in ("shared.txt", "related.txt")]
    for index, path in enumerate(paths):
        path.write_text(f"workspace=A\n{field}=value-{index}\n", encoding="utf-8")
    prompt = f"Read shared.txt and related.txt and report their current {field} lines. Do not edit them."
    broker = receipts.begin(runner, prompt)
    data = complete_reads(broker, paths)
    assert variant._verified_workspace_scalar_answer(broker, prompt, data) == (
        f"shared.txt: {field}=value-0\nrelated.txt: {field}=value-1"
    )
    paths[1].write_text(f"{field}=external\n", encoding="utf-8")
    assert variant._verified_workspace_scalar_answer(broker, prompt, data) is None


@pytest.mark.parametrize("fault", ["turn", "workspace", "hash", "missing", "duplicate"])
def test_field_lines_require_complete_current_unambiguous_receipts(variant, runner, tmp_path, fault):
    path = tmp_path / "settings.txt"
    path.write_text("mode=safe\nmode=other\n" if fault == "duplicate" else "mode=safe\n", encoding="utf-8")
    prompt = "Read settings.txt and report its current mode lines."
    broker = receipts.begin(runner, prompt)
    data = complete_reads(broker, [path])
    if fault == "missing":
        data.clear()
    elif fault != "duplicate":
        data[str(path)][{"turn": "turn_id", "workspace": "workspace", "hash": "sha256"}[fault]] = "wrong"
    assert variant._verified_workspace_scalar_answer(broker, prompt, data) is None


@pytest.mark.parametrize("prompt", [
    "Read settings.txt and report its current mode lines and state lines.",
    "Read settings.txt and report its current mode lines. Count all entries.",
    "Read settings.txt and count all entries, then report its current mode lines.",
])
def test_literal_line_shortcut_does_not_discard_other_requests(variant, runner, tmp_path, prompt):
    path = tmp_path / "settings.txt"
    path.write_text("mode=safe\nstate=ready\n", encoding="utf-8")
    broker = receipts.begin(runner, prompt)
    assert variant._verified_workspace_scalar_answer(broker, prompt, complete_reads(broker, [path])) is None


def test_final_answer_uses_current_named_files_after_alias_clarification(variant, runner, tmp_path, monkeypatch):
    for name, value in (("shared.txt", "revised-a"), ("related.txt", "original")):
        (tmp_path / name).write_text(f"workspace=A\nstate={value}\n", encoding="utf-8")
    runner.history = [{"role": "user", "content": "What file does qfile refer to?"},
                      {"role": "assistant", "content": "No qfile is verified. Please name the file."}]
    receipts.install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]
    calls = []

    async def model_pass(self, prompt, callback=None):
        calls.append(prompt)
        self._build_read_only_workspace_context(prompt)
        return "No `qfile` is verified in this workspace."

    monkeypatch.setattr(parent, "_run_async", model_pass)
    answer = asyncio.run(runner._run_async(
        "Read shared.txt and related.txt in this workspace and report their current state lines. Do not edit them.",
    ))
    assert "shared.txt: state=revised-a" in answer and "related.txt: state=original" in answer
    assert "No `qfile`" not in answer
    assert len(calls) == 1
