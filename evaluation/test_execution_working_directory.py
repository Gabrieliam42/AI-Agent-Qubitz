"""User-owned execution cwd binding and exact receipts; no model loads."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation.followup_matrix_cases import (
    Check,
    Turn,
    verify_turn,
    workspace_manifest,
)

variant = receipts.variant
runner = receipts.runner


def probe(root):
    target = root / "job.py"
    target.write_text("from pathlib import Path\nprint(Path.cwd())\n", encoding="utf-8")
    return target


def test_unrequested_model_cwd_is_bound_to_active_workspace(variant, runner, tmp_path):
    target = probe(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    messages = []
    broker = runner._tool_permission_broker
    broker.begin_turn("Run job.py and report its exit code.", lambda *item: messages.append(item))
    arguments = {"path": str(target), "cwd": str(other)}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == tmp_path
    assert any("working directory" in str(item).lower() for item in messages)


@pytest.mark.parametrize("instruction", [
    'Run job.py with cwd="build folder".',
    'Run job.py using working directory "build folder".',
    'Run job.py from directory "build folder".',
    'Run job.py. Use "build folder" as the working directory.',
    'Run job.py. Set cwd to "build folder".',
    'Run job.py with cwd="build folder" and report the current working directory.',
])
def test_explicit_user_cwd_is_preserved_not_defaulted(variant, runner, tmp_path, instruction):
    target = probe(tmp_path)
    directory = tmp_path / "build folder"
    directory.mkdir()
    broker = receipts.begin(runner, instruction)
    arguments = {"path": str(target), "cwd": str(tmp_path)}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == directory


@pytest.mark.parametrize("directory_name", ["build", "run", "check", "test"])
def test_unquoted_action_like_directory_names_are_not_actions(variant, runner, tmp_path, directory_name):
    target = probe(tmp_path)
    directory = tmp_path / directory_name
    directory.mkdir()
    broker = receipts.begin(runner, f"Run job.py with cwd={directory_name}.")
    arguments = {"path": str(target), "cwd": "."}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == directory


def test_build_is_still_an_action_outside_a_directory_value(variant, runner, tmp_path):
    target = probe(tmp_path)
    directory = tmp_path / "build"
    directory.mkdir()
    prompt = "Build a script named job.py that prints done. Then run job.py with cwd=build."
    actions = variant._semantic_action_segments(prompt)
    assert any(verb == "build" and act == "request" and not negated
               for verb, _segment, act, negated in actions)
    broker = receipts.begin(runner, prompt)
    arguments = {"path": str(target), "cwd": "."}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == directory


def test_explicit_workspace_root_wording_is_not_hidden(variant, runner, tmp_path):
    target = probe(tmp_path)
    (tmp_path / "other").mkdir()
    broker = receipts.begin(runner, "Run job.py with cwd=workspace root.")
    arguments = {"path": str(target), "cwd": "other"}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == tmp_path


@pytest.mark.parametrize("instruction", [
    'Run job.py. Do not use other as cwd.',
    'Run job.py. Explain how to use other as working directory.',
    'Run job.py. Hypothetically, use other as working directory.',
    'Run job.py and print "cwd=other".',
    'Run job.py and write note.txt containing "cwd=other".',
    'Run job.py --cwd other.',
    'Run job.py without changing cwd=other.',
])
def test_non_directives_do_not_change_working_directory(variant, runner, tmp_path, instruction):
    target = probe(tmp_path)
    (tmp_path / "other").mkdir()
    broker = receipts.begin(runner, instruction)
    arguments = {"path": str(target), "cwd": "other"}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == tmp_path


@pytest.mark.parametrize("instruction", [
    "Run job.py with cwd=missing.",
    "Run job.py with cwd=job.py.",
    "Run job.py with cwd=one and cwd=two.",
    "Run job.py. Use a directory selected earlier as cwd.",
])
def test_invalid_or_ambiguous_user_cwd_never_falls_back(variant, runner, tmp_path, instruction):
    target = probe(tmp_path)
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    broker = receipts.begin(runner, instruction)
    arguments = {"path": str(target), "cwd": "."}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert not allowed
    assert denial["process_started"] is False
    assert arguments["cwd"] == "."


def test_each_explicit_entrypoint_gets_its_own_cwd(variant, runner, tmp_path):
    first = probe(tmp_path)
    second = tmp_path / "second.py"
    second.write_text("print('second')\n")
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
    broker = receipts.begin(runner, "Run job.py with cwd=one. Then run second.py with cwd=two.")
    for target, expected in ((first, "one"), (second, "two")):
        arguments = {"path": str(target), "cwd": "."}
        allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
        assert allowed, denial
        assert Path(arguments["cwd"]).resolve() == tmp_path / expected
        broker.record_result("run_existing_entrypoint", arguments, receipts.result({
            "path": str(target), "cwd": str(tmp_path / expected),
            "command": [sys.executable, str(target)], "returncode": 0,
        }))


def test_explicit_external_cwd_in_full_mode_is_not_replaced(variant, runner, tmp_path):
    target = probe(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "_explicit")
    outside.mkdir()
    broker = receipts.begin(runner, f'Run job.py with cwd="{outside}".')
    arguments = {"path": str(target), "cwd": "."}
    allowed, denial = broker.authorize("run_existing_entrypoint", arguments)
    assert allowed, denial
    assert Path(arguments["cwd"]).resolve() == outside


def test_explicit_current_workspace_and_manual_approval_are_preserved(variant, runner, tmp_path):
    target = probe(tmp_path)
    directory = tmp_path / "build"
    directory.mkdir()
    broker = receipts.begin(runner, "Run job.py in the current working directory.")
    arguments = {"path": str(target), "cwd": "build"}
    assert broker.authorize("run_existing_entrypoint", arguments)[0]
    assert Path(arguments["cwd"]).resolve() == tmp_path
    broker = receipts.begin(runner, "Run job.py.")
    explicitly_approved = {"path": str(target), "cwd": str(directory)}
    broker.approved_once.add(broker._fingerprint("run_existing_entrypoint", explicitly_approved))
    assert broker.authorize("run_existing_entrypoint", explicitly_approved)[0]
    assert explicitly_approved["cwd"] == str(directory)


def test_cwd_binding_does_not_reuse_prior_turn_scope(variant, runner, tmp_path):
    target = probe(tmp_path)
    (tmp_path / "build").mkdir()
    broker = receipts.begin(runner, "Run job.py with cwd=build.")
    arguments = {"path": str(target)}
    assert broker.authorize("run_existing_entrypoint", arguments)[0]
    assert Path(arguments["cwd"]).resolve() == tmp_path / "build"
    broker.begin_turn("Run job.py.", None)
    arguments = {"path": str(target), "cwd": "build"}
    assert broker.authorize("run_existing_entrypoint", arguments)[0]
    assert Path(arguments["cwd"]).resolve() == tmp_path


def test_default_dispatch_arguments_stay_compatible(variant, runner, tmp_path):
    target = probe(tmp_path)
    broker = receipts.begin(runner, "Run job.py.")
    host = boundaries.host_for(variant, broker)
    asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "job.py"}))
    assert host.dispatched[-1] == ("run_existing_entrypoint", {"path": str(target)})


def test_worker_receipt_names_absolute_path_bases(variant, tmp_path, monkeypatch):
    target = probe(tmp_path)
    directory = tmp_path / "build"
    directory.mkdir()
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))
    result = asyncio.run(server.call_tool("run_existing_entrypoint", {
        "path": "job.py", "cwd": "build",
    }))[1]
    assert result["returncode"] == 0
    assert result["stdout"].strip() == str(directory)
    assert result["command"] == [sys.executable, str(target)]
    assert result["workspace"] == str(tmp_path)
    assert result["resolved_path"] == str(target)
    assert result["resolved_cwd"] == str(directory)
    assert result["path_base"] == "workspace"
    assert result["path"] == "job.py" and result["cwd"] == "build"


def test_real_host_binds_model_cwd_without_replaying(variant, runner, tmp_path, monkeypatch):
    target = tmp_path / "job.py"
    target.write_text("from pathlib import Path\nPath('execution.txt').write_text('once')\nprint(Path.cwd())\n")
    other = tmp_path / "other"
    other.mkdir()
    broker = receipts.begin(runner, "Run job.py and report its exit code.")
    monkeypatch.setattr(variant, "_preferred_project_python", lambda _: Path(sys.executable))
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))
    host = boundaries.host_for(variant, broker, server)
    result = asyncio.run(host.call_tool("run_existing_entrypoint", {"path": "job.py", "cwd": str(other)}))
    assert not result.isError, result.structuredContent
    assert result.structuredContent["stdout"].strip() == str(tmp_path)
    assert len(host.dispatched) == 1
    assert (tmp_path / "execution.txt").read_text() == "once"
    assert not (other / "execution.txt").exists()


def test_explicit_cwd_skips_no_argument_direct_shortcut(variant, runner, tmp_path, monkeypatch):
    target = probe(tmp_path)
    (tmp_path / "build").mkdir()
    prompt = "Run job.py with cwd=build."
    receipts.begin(runner, prompt)
    monkeypatch.setattr(runner, "_resolve_existing_entrypoint_for_prompt", lambda _prompt: {
        "kind": "file", "path": str(target), "label": "job.py",
    })
    monkeypatch.setattr(variant.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Direct path ignored explicit cwd"))
    assert runner._run_existing_script_preflight(prompt, None) == ""


def test_strict_evaluator_still_rejects_wrong_cwd_even_with_new_fields(tmp_path):
    target = probe(tmp_path)
    state = workspace_manifest(tmp_path)
    row = {"turn": 1, "capture_origin": "mcp_host", "name": "run_existing_entrypoint",
           "arguments": {"path": str(target)}, "result": {"is_error": False, "structured_content": {
               "path": "job.py", "cwd": str(tmp_path / "other"), "command": [sys.executable, str(target)],
               "returncode": 0, "workspace": str(tmp_path), "resolved_path": str(target),
               "resolved_cwd": str(tmp_path / "other"), "path_base": "workspace"}}}
    turn = Turn("Run job.py", (Check("execution", "job.py"),))
    assert verify_turn(tmp_path, turn, {"turn": 1, "tools": [row]}, state, state)[0]
