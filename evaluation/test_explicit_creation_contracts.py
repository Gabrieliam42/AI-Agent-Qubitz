"""CPU-only contracts using disposable files and registered tools; no model loads."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = variant_path(os.environ.get(
    "QUBITZ_TEST_VARIANT", "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"
), ROOT)
CREATE = "create a Python script named calculator.py in the workspace directory that would be a basic calculator"


@pytest.fixture(scope="module")
def variant():
    spec = importlib.util.spec_from_file_location("creation_contract_variant", REFERENCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def broker(variant, tmp_path):
    instance = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    instance.begin_turn(CREATE, None)
    return instance


def test_explicit_creation_is_authorized_from_first_turn(variant, broker):
    semantic = variant._analyze_semantic_intent(CREATE)
    assert {"create", "edit"} <= semantic.requested_actions
    assert not semantic.prohibited_actions
    allowed, result = broker.authorize("write_file", {"path": "calculator.py", "content": "print(2 + 2)\n"})
    assert allowed, result
    assert variant._creation_tool_name(CREATE) == "write_file"
    assert "created_outputs" in broker.completion_report(CREATE)[1]


def test_descriptive_noun_does_not_turn_creation_into_search(variant, tmp_path):
    prompt = 'Create an HTML page with an interactive scene; the default view is centered on the subject and is on one "plane".'
    semantic = variant._analyze_semantic_intent(prompt)
    assert semantic.requested_actions == {"create", "edit"}
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt) == []
    assert variant._named_text_creation_request(variant._APP.base, tmp_path, "full", prompt) is None
    assert variant._scoped_project_action_request("full", prompt) is not None
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode("full")
    broker.begin_turn(prompt, None)
    assert broker.creation_targets == set()


@pytest.mark.parametrize(
    ("prompt", "expected_route"),
    [
        (
            "Create an HTML page with elements positioned according to public measurements.",
            "scoped_project_action",
        ),
        (
            "Create an HTML page with interactive controls based on these written requirements.",
            "scoped_project_action",
        ),
        (
            "Create a Python script named calculator.py as a calculator based on these instructions.",
            "named_file_operation",
        ),
    ],
)
def test_standalone_creation_skips_project_retrieval(variant, tmp_path, prompt, expected_route):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    runner = variant._APP.base.AgentRunner(config)
    try:
        runner.set_access_mode(variant.ACCESS_MODE_FULL)
        decision = runner._decide_route(prompt)
        assert decision.selected_route == expected_route
        classification = decision.features["query_classification"]
        assert classification["project_context_required"] is False
        assert classification["inspection_level"] in {"none", "exact_targets"}
    finally:
        runner.reset_runtime()


@pytest.mark.parametrize(
    "prompt",
    [
        "Create report.html from source.py.",
        "Create a Python script that reads source.py and writes a report.",
        "Create an HTML page matching this project's existing style.",
    ],
)
def test_creation_with_existing_source_does_not_take_standalone_route(variant, tmp_path, prompt):
    assert variant._creation_requires_existing_workspace_context(variant._APP.base, prompt)
    assert variant._named_text_creation_request(variant._APP.base, tmp_path, "full", prompt) is None
    assert variant._scoped_project_action_request("full", prompt) is None


def test_chained_action_remains_action_and_named_file_remains_target(variant, tmp_path):
    prompt = 'Create a script named "calculator.py" and test it.'
    semantic = variant._analyze_semantic_intent(prompt)
    assert {"create", "edit", "verify"} <= semantic.requested_actions
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt) == ["calculator.py"]
    assert variant._named_text_creation_request(variant._APP.base, tmp_path, "full", prompt) is None
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode("full")
    broker.begin_turn(prompt, None)
    assert broker.creation_targets == {str((tmp_path / "calculator.py").resolve())}


@pytest.mark.parametrize("prompt", [
    'Create a guide about Python "3.14".',
    'Create an HTML page explaining version "v1.5".',
    'Create a script that reads source.py and writes a report.',
])
def test_versions_and_source_files_are_not_creation_destinations(variant, tmp_path, prompt):
    broker = variant._ToolPermissionBroker(variant._APP.base, tmp_path, tmp_path)
    broker.set_access_mode("full")
    broker.begin_turn(prompt, None)
    assert broker.creation_targets == set()


def test_extensionless_named_file_and_bare_output_remain_targets(variant, tmp_path):
    base = variant._APP.base
    named = 'Create a file named Dockerfile.'
    bare = 'Create calculator.py and verify it.'
    assert variant._explicit_file_reference_tokens(base, named, for_creation=True) == ["Dockerfile"]
    assert variant._explicit_file_reference_tokens(base, bare, for_creation=True) == ["calculator.py"]


@pytest.mark.parametrize("technology", ["RenderKit.js", "ViewCore.js"])
def test_library_mention_is_not_an_output_filename_or_workspace_target(variant, tmp_path, technology):
    prompt = (
        "Create a single HTML file with JavaScript that renders an interactive 3D scene. "
        f"Use a 3D library such as {technology}. Keep the result self-contained."
    )
    base = variant._APP.base
    assert variant._explicit_file_reference_tokens(base, prompt) == []
    assert variant._explicit_file_reference_tokens(base, prompt, for_creation=True) == []
    broker = variant._ToolPermissionBroker(base, tmp_path, tmp_path)
    broker.set_access_mode("full")
    broker.begin_turn(prompt, None)
    assert broker.creation_targets == set()
    allowed, notice = broker.authorize(
        "write_file", {"path": "scene.html", "content": "<!doctype html><title>Scene</title>"}
    )
    assert allowed, notice
    semantic = variant._analyze_semantic_intent(prompt)
    classification = variant._classify_query_scope(
        prompt,
        semantic,
        simple_question_candidate=False,
        file_token_count=0,
        scoped_project_action=variant._scoped_project_action_request("full", prompt) is not None,
    )
    assert classification.inspection_level == "none"


def test_unnamed_artifact_route_skips_unrelated_workspace_inspection(variant, tmp_path):
    prompt = (
        "Create a single HTML file with JavaScript that renders an interactive scene. "
        "Use a 3D library such as RenderKit.js."
    )
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    runner = variant._APP.base.AgentRunner(config)
    try:
        runner.set_access_mode(variant.ACCESS_MODE_FULL)
        decision = runner._decide_route(prompt)
        classification = decision.features["query_classification"]
        assert classification["project_context_required"] is False
        assert classification["inspection_level"] == "none"
    finally:
        runner.reset_runtime()


def test_named_output_is_not_confused_with_a_referenced_library(variant, tmp_path):
    prompt = "Create scene.html using a 3D framework like RenderKit.js."
    base = variant._APP.base
    assert variant._explicit_file_reference_tokens(base, prompt) == ["scene.html"]
    assert variant._explicit_file_reference_tokens(base, prompt, for_creation=True) == ["scene.html"]
    broker = variant._ToolPermissionBroker(base, tmp_path, tmp_path)
    broker.set_access_mode("full")
    broker.begin_turn(prompt, None)
    assert broker.creation_targets == {str((tmp_path / "scene.html").resolve())}


def test_existing_source_file_remains_readable_without_becoming_output(variant, tmp_path):
    prompt = "Create report.html from source.py."
    assert variant._explicit_file_reference_tokens(variant._APP.base, prompt) == ["report.html", "source.py"]
    assert variant._explicit_file_reference_tokens(
        variant._APP.base, prompt, for_creation=True,
    ) == ["report.html"]


def test_owned_windows_server_shutdown_verifies_process_identity(variant, tmp_path, monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

        def wait(self, *, timeout):
            assert timeout > 0
            return 0

    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=tmp_path)
    server = variant._APP.base.LlamaCppServerProcess(config)
    server.process = FakeProcess()
    server.model_path = Path("/mnt/d/AI_Research/AI_Agent_Qubitz/.cache/models/target.gguf")
    server._qubitz_owned_launch_epoch = time.time()
    server._qubitz_owner_token = "test-owned-launch"
    monkeypatch.setattr(variant._APP.base, "in_wsl", lambda: True)
    monkeypatch.setattr(variant._APP.base, "wsl_path_to_windows_path", lambda path: str(path))
    monkeypatch.setattr(server, "resolve_executable", lambda: "/mnt/d/AI_Research/llama-server.exe")
    commands = []
    original_run = variant.subprocess.run

    def capture(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(variant.subprocess, "run", capture)
    server.shutdown()
    assert server.process is None
    assert server._qubitz_owned_launch_epoch is None
    assert len(commands) == 2
    assert commands[0][-2] == "-EncodedCommand"
    script = base64.b64decode(commands[0][-1]).decode("utf-16-le")
    assert "Get-CimInstance Win32_Process" in script
    assert "ExecutablePath" in script
    assert "CreationDate" in script
    assert "target.gguf" in script
    assert "--port" in script
    assert "$ownerToken = 'test-owned-launch'" in script
    assert "ParentProcessId" in script
    assert "# QUBITZ_OWNER=" in script
    assert "$receiptCreated" in script
    if Path("/init").is_file() and shutil.which("powershell.exe"):
        parser_script = (
            "$text = [Text.Encoding]::Unicode.GetString("
            f"[Convert]::FromBase64String('{commands[0][-1]}')); "
            "$tokens = $null; $errors = $null; "
            "[System.Management.Automation.Language.Parser]::ParseInput("
            "$text, [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
        )
        parser_command = [
            *commands[0][:-1],
            base64.b64encode(parser_script.encode("utf-16-le")).decode("ascii"),
        ]
        parsed = original_run(
            parser_command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert parsed.returncode == 0, parsed.stderr


def test_unowned_windows_server_shutdown_does_not_kill_listener(variant, tmp_path, monkeypatch):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=tmp_path)
    server = variant._APP.base.LlamaCppServerProcess(config)
    monkeypatch.setattr(
        variant,
        "_build_powershell_management_command",
        lambda _base, _script: pytest.fail("Unowned server must not trigger process cleanup"),
    )
    server.shutdown()


@pytest.mark.parametrize("mode", ["read_only", "plan"])
def test_explicit_request_does_not_bypass_access_mode(variant, broker, mode):
    broker.set_access_mode(mode)
    allowed, _ = broker.authorize("write_file", {"path": "calculator.py", "content": "print(4)\n"})
    assert not allowed


@pytest.mark.parametrize("prompt", [
    "Do not create calculator.py.",
    "Explain how to create calculator.py.",
    "Can this script be created?",
])
def test_explanations_and_prohibitions_do_not_authorize_creation(variant, prompt):
    assert variant._creation_tool_name(prompt) is None


@pytest.mark.parametrize("prompt", [
    "why don't you create it?",
    "but I explicitly asked you to do that",
    "but actually I explicitly asked you to do that",
    "Still you did not create the actual calculator.py file in the workspace directory",
])
def test_corrections_retain_only_prior_authorized_task(variant, prompt):
    assert variant._resolve_corrective_task_prompt(prompt, CREATE) == CREATE
    assert variant._resolve_corrective_task_prompt(prompt, "") is None


def test_quoted_refusal_is_not_a_new_user_prohibition(variant):
    previous_answer = "I cannot create calculator.py. Do not create or edit files."
    prompt = f'you said "[assistant] {previous_answer}"\n\nbut actually I explicitly asked you to do that'
    assert variant._resolve_corrective_task_prompt(prompt, CREATE, previous_answer) == CREATE
    assert variant._resolve_corrective_task_prompt(prompt, CREATE, "A different answer") is None


def test_why_dont_is_not_a_prohibition(variant):
    assert not variant._analyze_semantic_intent("why don't you create it?").prohibited_actions
    assert "create" in variant._analyze_semantic_intent("Don't create it.").prohibited_actions


@pytest.mark.parametrize("prompt", [
    "Do not create it.",
    "Create a different file named other.py.",
    "Explain why it failed.",
    "You did not create other.py.",
])
def test_unrelated_or_prohibited_followup_does_not_resume(variant, prompt):
    assert variant._resolve_corrective_task_prompt(prompt, CREATE) is None


def test_registered_write_tool_and_completion_readback(variant, broker, tmp_path):
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    arguments = {"path": "calculator.py", "content": "print(2 + 2)\n"}
    result = asyncio.run(server.call_tool("write_file", arguments))
    if isinstance(result, tuple):
        structured = result[1]
    else:
        structured = json.loads(next(block.text for block in result if block.type == "text"))
    assert structured["created"] is True
    broker.record_result("write_file", arguments, SimpleNamespace(isError=False, structuredContent=structured, content=[]))
    assert (tmp_path / "calculator.py").read_text(encoding="utf-8") == arguments["content"]
    assert not broker.completion_report(CREATE)[1]
    (tmp_path / "calculator.py").write_text("# externally changed\n", encoding="utf-8")
    assert "created_outputs" in broker.completion_report(CREATE)[1]


def test_created_file_survives_later_edit_and_incomplete_verification(variant, broker, tmp_path):
    target = tmp_path / "calculator.py"
    original = "print(2 + 2)\n"
    updated = "print(3 + 3)\n"
    target.write_text(original, encoding="utf-8")
    original_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result(
        "write_file",
        {"path": "calculator.py", "content": original},
        SimpleNamespace(isError=False, structuredContent={
            "path": "calculator.py", "created": True, "new_sha256": original_hash,
        }, content=[]),
    )
    broker.record_result(
        "read_file_snapshot",
        {"path": "calculator.py"},
        SimpleNamespace(isError=False, structuredContent={"sha256": original_hash}, content=[]),
    )
    broker.register_transaction_original(target, target.read_bytes(), target.stat().st_mode)
    assert str(target.resolve()) not in broker.transaction_originals

    target.write_text(updated, encoding="utf-8")
    updated_hash = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result(
        "apply_text_patch",
        {"path": "calculator.py"},
        SimpleNamespace(isError=False, structuredContent={
            "path": "calculator.py", "new_sha256": updated_hash,
        }, content=[]),
    )
    assert not broker.completion_report(CREATE)[1]
    assert broker.rollback_transaction()["restored"] == []
    assert target.read_text(encoding="utf-8") == updated

    broker.transaction_originals[str(target.resolve())] = {
        "data": original.encode(), "mode": target.stat().st_mode,
    }
    broker.transaction_changed_hashes[str(target.resolve())] = updated_hash
    assert broker.rollback_transaction()["restored"] == []
    assert target.read_text(encoding="utf-8") == updated


def test_existing_file_keeps_atomic_rollback(variant, broker, tmp_path):
    target = tmp_path / "existing.txt"
    original = b"before\n"
    updated = b"after\n"
    target.write_bytes(original)
    broker.register_transaction_original(target, original, target.stat().st_mode)
    target.write_bytes(updated)
    broker.transaction_changed_hashes[str(target.resolve())] = hashlib.sha256(updated).hexdigest()

    assert broker.rollback_transaction()["restored"] == [str(target.resolve())]
    assert target.read_bytes() == original


def test_rollback_only_invalidates_evidence_for_restored_paths(variant, broker, tmp_path):
    created = tmp_path / "calculator.py"
    created.write_text("print(2 + 2)\n", encoding="utf-8")
    created_hash = hashlib.sha256(created.read_bytes()).hexdigest()
    broker.record_result(
        "write_file",
        {"path": "calculator.py", "content": created.read_text(encoding="utf-8")},
        SimpleNamespace(isError=False, structuredContent={
            "path": "calculator.py", "created": True, "new_sha256": created_hash,
        }, content=[]),
    )

    existing = tmp_path / "existing.txt"
    existing.write_text("before\n", encoding="utf-8")
    broker.register_transaction_original(existing, existing.read_bytes(), existing.stat().st_mode)
    existing.write_text("after\n", encoding="utf-8")
    updated_hash = hashlib.sha256(existing.read_bytes()).hexdigest()
    broker.record_result(
        "apply_text_patch",
        {"path": "existing.txt"},
        SimpleNamespace(isError=False, structuredContent={
            "path": "existing.txt", "new_sha256": updated_hash,
        }, content=[]),
    )

    rollback = broker.rollback_transaction()
    assert rollback == {"restored": [str(existing.resolve())], "skipped": []}
    broker.reset_transaction_after_rollback(rollback["restored"])
    assert created.read_text(encoding="utf-8") == "print(2 + 2)\n"
    assert existing.read_text(encoding="utf-8") == "before\n"
    assert not broker.completion_report(CREATE)[1]
    assert any(
        evidence["success"] and str(created.resolve()) in evidence["paths"]
        for evidence in broker._current_turn_evidence()
    )
    assert not any(
        evidence["success"] and str(existing.resolve()) in evidence["paths"]
        for evidence in broker._current_turn_evidence()
    )


def test_registered_write_never_clobbers_existing_file(variant, tmp_path):
    target = tmp_path / "calculator.py"
    target.write_text("# original\n", encoding="utf-8")
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    with pytest.raises(Exception, match="already exists"):
        asyncio.run(server.call_tool("write_file", {"path": "calculator.py", "content": "print(4)\n"}))
    assert target.read_text(encoding="utf-8") == "# original\n"


def test_multi_file_creation_is_one_atomic_verified_operation(variant, broker, tmp_path):
    prompt = (
        "Create miniapp/__init__.py and miniapp/cli.py as a minimal importable Python package. "
        "Verify both files."
    )
    broker.begin_turn(prompt, None)
    request = variant._named_file_operation_request(
        variant._APP.base, tmp_path, variant.ACCESS_MODE_FULL, prompt
    )
    assert request is not None and request["operation"] == "create_many"
    arguments = {
        "files": [
            {"path": "miniapp/__init__.py", "content": "from .cli import main\\n"},
            {"path": "miniapp/cli.py", "content": "def main():\\n    return 0\\n"},
        ]
    }
    allowed, notice = broker.authorize("write_files", arguments)
    assert allowed, notice
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    result = asyncio.run(server.call_tool("write_files", arguments))
    structured = result[1] if isinstance(result, tuple) else json.loads(
        next(block.text for block in result if block.type == "text")
    )
    broker.record_result(
        "write_files",
        arguments,
        SimpleNamespace(isError=False, structuredContent=structured, content=[]),
    )
    assert (tmp_path / "miniapp" / "__init__.py").read_text(encoding="utf-8") == "from .cli import main\n"
    assert (tmp_path / "miniapp" / "cli.py").read_text(encoding="utf-8") == "def main():\n    return 0\n"
    assert not broker.completion_report(prompt)[1]
    expected_paths = {str((tmp_path / "miniapp" / name).resolve()) for name in ("__init__.py", "cli.py")}
    assert set(broker.verified_creation_hashes) == expected_paths
    broker.evidence.clear()
    assert not broker.completion_report(prompt)[1]
    (tmp_path / "miniapp" / "cli.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    assert "created_outputs" in broker.completion_report(prompt)[1]


def test_multi_file_receipts_bind_absolute_requests_to_relative_results(variant, broker, tmp_path):
    prompt = "Create miniapp/__init__.py and miniapp/cli.py as a minimal importable Python package. Verify both files."
    broker.begin_turn(prompt, None)
    targets = [tmp_path / "miniapp" / "__init__.py", tmp_path / "miniapp" / "cli.py"]
    contents = ["from .cli import main\n", "def main():\n    return 0\n"]
    for target, content_text in zip(targets, contents):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content_text, encoding="utf-8")
    arguments = {
        "files": [
            {"path": str(target.resolve()), "content": content_text}
            for target, content_text in zip(targets, contents)
        ]
    }
    structured = {
        "files": [
            {
                "path": target.relative_to(tmp_path).as_posix(),
                "created": True,
                "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            }
            for target in targets
        ]
    }
    broker.record_result(
        "write_files",
        arguments,
        SimpleNamespace(isError=False, structuredContent=structured, content=[]),
    )
    assert not broker.completion_report(prompt)[1]
    assert set(broker.verified_creation_hashes) == {str(target.resolve()) for target in targets}


def test_multi_file_receipt_cannot_substitute_another_requested_path(variant, broker, tmp_path):
    prompt = "Create miniapp/__init__.py and miniapp/cli.py as a minimal importable Python package. Verify both files."
    broker.begin_turn(prompt, None)
    first = tmp_path / "miniapp" / "__init__.py"
    second = tmp_path / "miniapp" / "cli.py"
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    arguments = {"files": [{"path": str(first), "content": "first\n"}, {"path": str(second), "content": "second\n"}]}
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    structured = {
        "files": [
            {"path": "miniapp/__init__.py", "created": True, "new_sha256": digest},
            {"path": "miniapp/__init__.py", "created": True, "new_sha256": digest},
        ]
    }
    broker.record_result(
        "write_files",
        arguments,
        SimpleNamespace(isError=False, structuredContent=structured, content=[]),
    )
    assert "created_outputs" in broker.completion_report(prompt)[1]


def test_exact_script_execution_requires_matching_current_turn_evidence(variant, broker, tmp_path):
    target = tmp_path / "run_me.py"
    target.write_text("print('RUN_OK')\n", encoding="utf-8")
    other = tmp_path / "other.py"
    other.write_text("print('OTHER')\n", encoding="utf-8")
    prompt = "Run run_me.py and report its exact stdout."
    broker.begin_turn(prompt, None)
    assert "entrypoint_executed" in broker.completion_report(prompt)[1]
    broker.evidence.append(
        {
            "turn_id": broker.turn_id,
            "tool": "run_existing_entrypoint",
            "success": True,
            "categories": ["entrypoint_executed"],
            "evidence_path": str(other),
        }
    )
    assert "entrypoint_executed" in broker.completion_report(prompt)[1]
    broker.evidence.append(
        {
            "turn_id": broker.turn_id,
            "tool": "run_existing_entrypoint",
            "success": True,
            "categories": ["entrypoint_executed"],
            "evidence_path": "run_me.py",
        }
    )
    assert not broker.completion_report(prompt)[1]


def test_active_permission_broker_lifetime_is_turn_scoped(variant, broker):
    assert getattr(variant._APP.base, "_qubitz_active_permission_broker", None) is broker
    broker.end_turn()
    assert getattr(variant._APP.base, "_qubitz_active_permission_broker", None) is None


def test_atomic_multi_file_creation_refuses_partial_publish(variant, tmp_path):
    existing = tmp_path / "miniapp" / "__init__.py"
    existing.parent.mkdir()
    existing.write_text("original\n", encoding="utf-8")
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    with pytest.raises(Exception, match="already exists"):
        asyncio.run(
            server.call_tool(
                "write_files",
                {
                    "files": [
                        {"path": "miniapp/__init__.py", "content": "changed\n"},
                        {"path": "miniapp/cli.py", "content": "def main():\n    return 0\n"},
                    ]
                },
            )
        )
    assert existing.read_text(encoding="utf-8") == "original\n"
    assert not (tmp_path / "miniapp" / "cli.py").exists()


def test_python_creation_repairs_only_parseable_extra_escaped_newlines(variant, broker, tmp_path):
    prompt = "Create a Python script named escaped.py as a simple module and verify it."
    broker.begin_turn(prompt, None)
    arguments = {"path": "escaped.py", "content": "def value():\\n    return 4\\n"}
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    result = asyncio.run(server.call_tool("write_file", arguments))
    structured = result[1] if isinstance(result, tuple) else json.loads(
        next(block.text for block in result if block.type == "text")
    )
    assert structured["content_repair"] == "decoded_extra_escaped_newlines"
    broker.record_result(
        "write_file",
        arguments,
        SimpleNamespace(isError=False, structuredContent=structured, content=[]),
    )
    assert (tmp_path / "escaped.py").read_text(encoding="utf-8") == "def value():\n    return 4\n"
    assert not broker.completion_report(prompt)[1]


def test_named_delete_requires_exact_delete_evidence(variant, broker, tmp_path):
    target = tmp_path / "delete_me.txt"
    target.write_text("delete fixture\n", encoding="utf-8")
    prompt = "Delete delete_me.txt because this explicit task requests its deletion, then verify it is gone."
    broker.begin_turn(prompt, None)
    broker.evidence.append(
        {
            "turn_id": broker.turn_id,
            "tool": "apply_text_patch",
            "success": True,
            "categories": ["changed_files"],
            "paths": [str(target)],
        }
    )
    assert "changed_files" in broker.completion_report(prompt)[1]
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, REFERENCE)
    arguments = {"path": "delete_me.txt", "recursive": False}
    result = asyncio.run(server.call_tool("delete_path", arguments))
    structured = result[1] if isinstance(result, tuple) else json.loads(
        next(block.text for block in result if block.type == "text")
    )
    broker.record_result(
        "delete_path",
        arguments,
        SimpleNamespace(isError=False, structuredContent=structured, content=[]),
    )
    assert not broker.completion_report(prompt)[1]


def test_named_edit_requires_evidence_for_requested_file(broker, tmp_path):
    target = tmp_path / "target.py"
    other = tmp_path / "other.py"
    target.write_text("def value():\n    return 4\n", encoding="utf-8")
    other.write_text("def value():\n    return 5\n", encoding="utf-8")
    prompt = "Edit target.py so value returns 5."
    broker.begin_turn(prompt, None)
    assert str(target.resolve()) in broker.transaction_explicit_file_targets
    broker.evidence.append(
        {
            "turn_id": broker.turn_id,
            "tool": "apply_text_patch",
            "success": True,
            "categories": ["changed_files"],
            "paths": [str(other.resolve())],
        }
    )
    assert "changed_files" in broker.completion_report(prompt)[1]
    broker.evidence.append(
        {
            "turn_id": broker.turn_id,
            "tool": "apply_text_patch",
            "success": True,
            "categories": ["changed_files"],
            "paths": [str(target.resolve())],
        }
    )
    assert "changed_files" in broker.completion_report(prompt)[1]
    target.write_text("def value():\n    return 5\n", encoding="utf-8")
    broker.record_result(
        "apply_text_patch", {"path": str(target)},
        SimpleNamespace(isError=False, structuredContent={
            "status": "patched", "changed_files": [{"path": str(target)}],
        }, content=[]),
    )
    assert "changed_files" not in broker.completion_report(prompt)[1]


def test_coding_repair_precedes_direct_entrypoint_route(variant, tmp_path):
    (tmp_path / "math_utils.py").write_text("def add(a, b): return a - b\n", encoding="utf-8")
    (tmp_path / "test_math_utils.py").write_text(
        "from math_utils import add\ndef test_add(): assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    runner = variant._APP.base.AgentRunner(config)
    try:
        runner.set_access_mode(variant.ACCESS_MODE_FULL)
        decision = runner._decide_route(
            "Run the tests, diagnose the failure, repair math_utils.py, and rerun the focused test until it passes."
        )
        assert decision.selected_route == "retrieval_plus_model"
    finally:
        runner.reset_runtime()


def test_wrong_destination_cannot_satisfy_named_creation(variant, broker):
    allowed, result = broker.authorize("write_file", {"path": "other.py", "content": "print(4)\n"})
    assert not allowed
    assert result["status"] == "scope_denied"
    assert result["files_unchanged"]
    assert not (broker.workspace / "other.py").exists()
    assert "created_outputs" in broker.completion_report(CREATE)[1]


def test_browser_constraint_is_bound_to_tool_arguments(variant, broker):
    prompt = "Open https://example.com/article as a tab in Microsoft Edge"
    broker.begin_turn(prompt, None)
    allowed, result = broker.authorize("open_urls_in_browser", {
        "urls": ["https://example.com/article"], "browser": "firefox",
    })
    assert not allowed
    assert result["status"] == "constraint_mismatch"
    broker.evidence.append({
        "turn_id": broker.turn_id, "success": True, "categories": ["opened_urls"],
        "browser": "firefox", "urls": ["https://example.com/article"], "opened_count": 1,
    })
    assert "opened_urls" in broker.completion_report(prompt)[1]
