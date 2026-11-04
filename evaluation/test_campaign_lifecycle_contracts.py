"""Real harmless subprocess waits plus crash-boundary tests; no matrix or models."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from evaluation import campaign_lifecycle as lifecycle
from evaluation import disposable_followup_matrix as matrix


def command(output, code=0):
    source = ("import json,pathlib,sys; p=pathlib.Path(sys.argv[1]);p.mkdir();"
              "(p/'summary.json').write_text(json.dumps({'status':'complete'}));sys.exit(int(sys.argv[2]))")
    return [sys.executable, "-u", "-B", "-c", source, str(output), str(code)]


def identity(pid):
    return {"pid": pid, "creation_ticks": "test-bound-handle", "command_line": "harmless fixture"}


@pytest.mark.parametrize("arguments", [
    ["--execute", "--supervise"],
    ["--execute", "--supervise", "--output", "relative-output"],
    ["--execute", "--supervise", "--output=relative-output"],
])
def test_supervised_child_uses_exact_parent_output_even_when_defaulted(tmp_path, arguments):
    output = tmp_path / "parent_timestamped_output"
    child = lifecycle.coordinator_command(sys.executable, tmp_path / "matrix.py", arguments, output)
    assert "--supervise" not in child
    assert child[-2:] == ["--output", str(output.resolve())]
    assert child.count("--execute") == 1


def test_supervisor_records_actual_wait_and_refuses_redispatch(tmp_path):
    output = tmp_path / "run"
    assert lifecycle.supervise(command(output, 7), output, identity=identity) == 7
    evidence = tmp_path / "run_supervision"
    wait = json.loads((evidence / "coordinator_exit.json").read_text())
    assert wait["actual_wait"] and wait["exit_code"] == 7
    assert json.loads((evidence / "work_state.json").read_text())["actual_wait"]
    with pytest.raises(ValueError, match="fresh output"):
        lifecycle.supervise(command(output), output, identity=identity)


def test_post_dispatch_state_failure_still_waits_exact_process(tmp_path):
    output = tmp_path / "run"
    original = matrix.save_json
    failures = [0]

    def write(path, value):
        if value.get("status") == "running":
            failures[0] += 1
            raise OSError("state publication failed after launch")
        return original(path, value)

    with patch.object(matrix, "save_json", side_effect=write):
        assert lifecycle.supervise(command(output), output, identity=identity) == 0
    evidence = tmp_path / "run_supervision"
    assert failures == [1]
    assert json.loads((evidence / "coordinator_exit.json").read_text())["actual_wait"]
    assert json.loads((evidence / "post_dispatch_failure.json").read_text())["outcome"] == "dispatched_do_not_retry"


def test_existing_intent_without_launch_receipt_is_unknown_not_retried(tmp_path):
    output = tmp_path / "run"
    directory = tmp_path / "run_supervision"
    directory.mkdir()
    (directory / "launch_intent.json").write_text('{}')
    with patch.object(subprocess, "Popen") as launch, pytest.raises(FileExistsError):
        lifecycle.supervise(command(output), output, identity=identity, popen=launch)
    launch.assert_not_called()


def test_compact_progress_has_hashed_case_links_without_full_payload(tmp_path):
    output = tmp_path / "run"
    case = output / "case"
    case.mkdir(parents=True)
    (case / "result.json").write_text(json.dumps({"status": "failed", "turns": [{"trace": "x" * 100000}]}))
    row = {"variant": "fixture.py", "scenario": "fixture", "status": "failed", "output": str(case),
           "turns": [{"trace": "x" * 100000}]}
    row["result_reference"] = lifecycle.result_reference(output, row)
    summary = {"contract": {"repeat": 1}, "status": "paused", "runs": [row]}
    matrix.save_json(output / "summary.json", summary)
    progress = lifecycle.read_progress(output)
    assert len(json.dumps(progress)) < 3000
    assert lifecycle.resolve_result(output, progress["cases"][0]["result_reference"])["status"] == "failed"
    assert progress["summary_sha256"] == hashlib.sha256((output / "summary.json").read_bytes()).hexdigest()
    (case / "result.json").write_text('{}')
    with pytest.raises(ValueError, match="hash or length"):
        lifecycle.resolve_result(output, row["result_reference"])


@pytest.mark.parametrize("tampered", [False, True])
def test_stop_reconciles_compact_progress_without_replaying_existing_intent(tmp_path, monkeypatch, tampered):
    from evaluation import followup_campaign_control as control

    output = tmp_path / "campaign"
    output.mkdir()
    (output / matrix.MARKER).write_text("developer-only follow-up matrix\n")
    expected = {"pid": 12345, "creation_ticks": "100", "executable": "fixture.exe", "command_line": "fixture"}
    (output / "coordinator_identity.json").write_text(json.dumps(expected))
    contract = {"variants": ["fixture.py"]}
    running = {"contract": contract, "status": "running", "runs": []}
    final = {**running, "status": "paused"}
    progress = lifecycle.progress_record(final, json.dumps(final))
    if tampered:
        progress["contract_sha256"] = "0" * 64
    (output / "progress.json").write_text(json.dumps(progress))
    (output / "summary.json").write_text(json.dumps(final))
    original_read = control.read_json
    reads = [0]

    def read(path):
        if path.name == "summary.json":
            reads[0] += 1
            return running if reads[0] == 1 else final
        return original_read(path)

    monkeypatch.setitem(sys.modules, "disposable_followup_matrix", matrix)
    monkeypatch.setattr(matrix, "validate_output", lambda value: value)
    monkeypatch.setattr(control, "read_json", read)
    identities = iter([expected, None])
    monkeypatch.setattr(control, "process_identity", lambda _pid: next(identities))
    if tampered:
        with pytest.raises(RuntimeError, match="different campaign contract"):
            control.stop_campaign(output)
        assert not (output / "stop_result.json").exists()
    else:
        result = control.stop_campaign(output)
        assert result["coordinator_stopped"] and result["actual_exit_code"] is None
        assert (output / "stop_result.json").is_file()
    assert (output / "stop_request.json").is_file()
