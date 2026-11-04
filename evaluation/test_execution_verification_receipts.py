"""Model-free coverage of direct execution receipts and committed-edit recovery."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.variant_test_selection import selected_variant_names
from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = selected_variant_names((
    "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py",
    "AI_Agent_Qubitz_Devstral-Small-2-24B-Q4.py",
), ROOT)


@pytest.fixture(scope="module", params=VARIANTS)
def variant(request):
    name = "execution_receipt_" + str(request.param).replace(".", "_").replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, variant_path(request.param, ROOT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(name, None)


@pytest.fixture
def runner(variant, tmp_path, monkeypatch):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    monkeypatch.setattr(instance, "_get_llm", lambda: pytest.fail("A model must not load in this test"))
    yield instance
    instance.reset_runtime()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_direct_execution_records_exact_target_once(variant, runner, tmp_path, monkeypatch):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json\nfrom pathlib import Path\n"
        "counter = Path('executions.txt')\n"
        "counter.write_text(counter.read_text() + 'x' if counter.exists() else 'x')\n"
        "print(json.dumps([{'name': 'probe', 'value': 'executed'}]))\n",
        encoding="utf-8",
    )
    prompt = "Run probe.py and report its stdout."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    monkeypatch.setattr(runner, "_resolve_existing_entrypoint_for_prompt", lambda _prompt: {
        "kind": "file", "path": str(probe), "label": "probe.py",
    })
    monkeypatch.setattr(variant, "_select_direct_workspace_python", lambda _base, _workspace: (
        Path(sys.executable), "shell",
    ))
    if hasattr(runner, "_direct_workspace_python_plan"):
        monkeypatch.setattr(runner, "_direct_workspace_python_plan", lambda: (Path(sys.executable), "shell"))
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        # Select the actual enhanced ancestor, not a different generation's preflight.
        for cls in type(runner).__mro__:
            if cls.__name__ == "EnhancedAgentRunner":
                if "_try_direct_existing_script_completion" in cls.__dict__:
                    legacy = cls.__dict__["_try_direct_existing_script_completion"].__get__(runner)
                    legacy(prompt, None)
                else:
                    runner._run_existing_script_preflight(prompt, None)
                break
        else:
            pytest.fail("No enhanced direct execution path was found")
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert (tmp_path / "executions.txt").read_text() == "x"
    receipts = [entry for entry in broker.evidence if "entrypoint_executed" in entry.get("categories", [])]
    assert len(receipts) == 1
    assert receipts[0]["evidence_path"] == str(probe.resolve())
    assert receipts[0]["cwd"] == str(tmp_path.resolve())
    assert receipts[0]["interpreter"] == sys.executable
    assert receipts[0]["return_code"] == 0
    assert "entrypoint_executed" not in broker.completion_report(prompt)[1]


@pytest.mark.parametrize("result", [
    {}, {"returncode": 1}, {"returncode": True},
    {"returncode": 0, "status": "cancelled"},
    {"returncode": 0, "status": "interpreter_unavailable"},
])
def test_failed_or_missing_exit_status_cannot_verify_execution(variant, runner, tmp_path, result):
    target = tmp_path / "probe.py"
    target.write_text("print('probe')\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn("Run probe.py.", None)
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        variant._record_direct_entrypoint_execution(
            variant._APP.base, tmp_path, {"kind": "file", "path": str(target)}, result, "shell",
        )
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert "entrypoint_executed" in broker.completion_report("Run probe.py.")[1]


def test_execution_receipt_does_not_verify_a_different_requested_file(variant, runner, tmp_path):
    actual = tmp_path / "actual.py"
    requested = tmp_path / "requested.py"
    for target in (actual, requested):
        target.write_text("print('probe')\n", encoding="utf-8")
    broker = runner._tool_permission_broker
    broker.begin_turn("Run requested.py.", None)
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        variant._record_direct_entrypoint_execution(
            variant._APP.base, tmp_path, {"kind": "file", "path": str(actual)}, {"returncode": 0}, "shell",
        )
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)
    assert "entrypoint_executed" in broker.completion_report("Run requested.py.")[1]


def collection_result(path, error="SyntaxError: unmatched '}'"):
    return {
        "test_runner": "pytest", "returncode": 2,
        "stdout": f'ERROR collecting tests/test_probe.py\nE     File "{path}", line 2\nE   {error}\n'
        "Interrupted: 1 error during collection\n",
    }


def test_changed_python_syntax_failure_is_actionable(variant, tmp_path):
    target = tmp_path / "src" / "labels.py"
    target.parent.mkdir()
    target.write_text("def label(name):\n    return name.upper()}\n", encoding="utf-8")
    assert variant._actionable_committed_pytest_failure(
        tmp_path, {str(target): digest(target)}, collection_result(target),
    )


def test_short_colored_collection_trace_is_actionable(variant, tmp_path):
    target = tmp_path / "module.py"
    target.write_text("bad syntax }\n", encoding="utf-8")
    result = {
        "test_runner": "pytest", "returncode": 2,
        "stdout": "ERROR collecting tests/test_probe.py\n"
        "\x1b[31mmodule.py:1: SyntaxError: unmatched '}'\x1b[0m\n",
    }
    assert variant._actionable_committed_pytest_failure(tmp_path, {str(target): digest(target)}, result)


@pytest.mark.parametrize("error", ["NameError: name 'missing' is not defined", "AttributeError: missing"])
def test_changed_module_collection_failure_is_actionable(variant, tmp_path, error):
    target = tmp_path / "module.py"
    target.write_text("missing\n", encoding="utf-8")
    assert variant._actionable_committed_pytest_failure(
        tmp_path, {str(target): digest(target)}, collection_result(target, error),
    )


@pytest.mark.parametrize("problem", [
    "cancelled", "missing_dependency", "import_error", "wrong_path", "changed_again", "unrelated_exit", "no_collection",
])
def test_collection_blockers_are_not_code_recovery(variant, tmp_path, problem):
    target = tmp_path / "module.py"
    target.write_text("bad syntax }\n", encoding="utf-8")
    hashes = {str(target): digest(target)}
    result = collection_result(target)
    if problem == "cancelled":
        result["status"] = "cancelled"
    elif problem == "missing_dependency":
        result = collection_result(target, "ModuleNotFoundError: No module named 'optional_dependency'")
    elif problem == "import_error":
        result = collection_result(target, "ImportError: incompatible dependency")
    elif problem == "wrong_path":
        result = collection_result(tmp_path / "other.py")
    elif problem == "changed_again":
        target.write_text("different syntax }\n", encoding="utf-8")
    elif problem == "unrelated_exit":
        result["returncode"] = 4
    else:
        result["stdout"] = "KeyboardInterrupt\n"
    assert not variant._actionable_committed_pytest_failure(tmp_path, hashes, result)


@pytest.mark.parametrize("cancelled", [False, True])
def test_committed_collection_failure_preserves_new_files_and_respects_cancel(
    variant, runner, tmp_path, monkeypatch, cancelled,
):
    target = tmp_path / "module.py"
    original = b"def label(name):\n    return name.lower()\n"
    target.write_bytes(original)
    (tmp_path / "test_probe.py").write_text("import module\n", encoding="utf-8")
    new_file = tmp_path / "new.txt"
    new_file.write_text("keep this output", encoding="utf-8")
    prompt = "Fix module.py, then run the tests."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    broker.transaction_originals[str(target)] = {"data": original, "mode": target.stat().st_mode}
    target.write_text("def label(name):\n    return name.upper()}\n", encoding="utf-8")
    broker.transaction_changed_hashes[str(target)] = digest(target)
    result = SimpleNamespace(isError=False, structuredContent=collection_result(target))

    class Host:
        def __init__(self, _workspace):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def list_tools(self):
            return []

        async def call_tool(self, _name, _arguments):
            if cancelled:
                runner.request_cancel()
            return result

    monkeypatch.setattr(variant._APP.base, "MCPHost", Host)
    if cancelled:
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
        assert target.read_bytes() != original
        assert broker.transaction_verification_failures == 0
    else:
        passed, output = asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
        assert passed is False
        assert "SyntaxError" in output
        assert target.read_bytes() != original
        assert broker._test_correction_targets == {str(target): digest(target)}
        assert broker.transaction_verification_failures == 1
    assert new_file.read_text() == "keep this output"


def test_verification_correction_limit_is_per_turn(variant, runner):
    broker = runner._tool_permission_broker
    prompt = "Fix module.py, then run the tests."
    broker.begin_turn(prompt, None)
    broker.transaction_verification_failures = 2
    assert asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "failed tests", None)) is None
    broker.end_turn()
    assert broker.transaction_verification_failures == 0
    broker.begin_turn(prompt, None)
    assert broker.transaction_verification_failures == 0
