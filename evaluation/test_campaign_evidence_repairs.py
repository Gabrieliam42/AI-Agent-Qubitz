"""Regressions for evidence scope, strict oracles, safe cleanup and storage preflight."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import disposable_followup_matrix as matrix
import followup_campaign_control as control
import pytest
import test_followup_state_receipts as receipts
from followup_matrix_cases import (
    Check,
    Turn,
    default_behavior_comparison,
    verify_turn,
    workspace_manifest,
)

variant = receipts.variant
runner = receipts.runner


def retained_change(variant, runner):
    broker = receipts.begin(runner, "Edit pricing.py, preserving default behavior.")
    target = runner.workspace / "pricing.py"
    before = b"def total(prices):\n    return sum(prices)\n"
    after = b"def total(prices, discount=0):\n    return sum(prices)*(100-discount)/100\n"
    target.write_bytes(after)
    broker.transaction_originals[str(target)] = {"data": before, "sha256": hashlib.sha256(before).hexdigest()}
    broker.transaction_changed_hashes[str(target)] = hashlib.sha256(after).hexdigest()
    return broker, target, variant._retained_behavior_evidence(broker, {})


def test_before_after_survives_execution_turn_without_invented_coverage(variant, runner):
    broker, target, data = retained_change(variant, runner)
    broker.end_turn()
    broker.begin_turn("Run the existing tests.", None)
    broker.prior_behavior_sources = data["sources"]
    broker.record_result("run_existing_entrypoint", {"path": "run_tests.py"}, receipts.result({
        "path": "run_tests.py", "command": ["python", "run_tests.py"], "returncode": 0, "stdout": "OK", "stderr": "",
    }))
    carried = variant._retained_behavior_evidence(broker, {"behavior_evidence": data})
    assert carried["sources"] == data["sources"]
    assert carried["executions"][0]["source_hashes"][str(target)] == data["sources"][str(target)]["after_sha256"]
    answer = variant._compatibility_evidence_answer("Explain how it preserves default behavior.",
                                                   "It returns exactly the same results.", carried)
    assert "exactly the same results" not in answer
    assert "sum(prices)*(100-discount)/100" in answer
    assert "precision, overflow, exceptions" in answer
    assert "exit code 0" in answer


def test_source_evidence_is_bounded_and_rejects_stale_hash(variant, runner):
    broker, target, _data = retained_change(variant, runner)
    target.write_text("x" * 6000)
    assert not variant._retained_behavior_evidence(broker, {})["sources"]
    broker.transaction_changed_hashes[str(target)] = hashlib.sha256(target.read_bytes()).hexdigest()
    source = variant._retained_behavior_evidence(broker, {})["sources"][str(target)]
    assert len(source["after_text"]) == 1600 and not source["after_complete"]


def test_no_tool_compatibility_followup_uses_only_retained_evidence(variant, runner, monkeypatch):
    broker, target, data = retained_change(variant, runner)
    broker.end_turn()
    runner._last_actionable_task = {"workspace": str(runner.workspace.resolve()),
                                    "prompt": "Extend pricing.py with a discount parameter.",
                                    "paths": [str(target)], "behavior_evidence": data, "evidence": []}
    runner.history = [{"role": "user", "content": "Extend pricing.py with a discount parameter."},
                      {"role": "assistant", "content": "Saved pricing.py."}]
    mro = type(runner).__mro__
    parent = mro[next(index for index, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def model_pass(*_args, **_kwargs):
        pytest.fail("Historical compatibility receipts must not require a new model pass")

    monkeypatch.setattr(parent, "_run_async", model_pass)
    before = target.read_bytes()
    answer = asyncio.run(runner._run_async(
        "Explain how your latest change preserves previous total behavior when no discount is passed. Do not modify or execute anything."))
    assert "before source" in answer and "after source" in answer
    assert "compatibility is not established" in answer
    assert target.read_bytes() == before
    assert not runner._tool_permission_broker.evidence


def test_schema_exposes_nonempty_bounded_patch_lists(variant, runner):
    server = variant._build_local_mcp_server(variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__))
    tools = {item.name: item.inputSchema for item in asyncio.run(server.list_tools())}
    patch = tools["apply_text_patch"]["properties"]["replacements"]
    assert (patch["minItems"], patch["maxItems"]) == (1, 64)
    assert "expected_sha256" not in tools["apply_text_patch"].get("required", [])
    many = tools["apply_text_patches"]
    assert many["properties"]["patches"]["minItems"] == 1
    assert many["$defs"]["_BoundedFileTextPatch"]["properties"]["replacements"]["minItems"] == 1


@pytest.mark.parametrize("recovered", [True, False])
def test_final_owned_cleanup_status_not_sticky_initial_failure(variant, monkeypatch, recovered):
    class Server:
        def __init__(self, config):
            self.config, self.process, self.model_path = config, None, Path("/mnt/d/model.gguf")

        def resolve_executable(self):
            return "/mnt/d/llama-server.exe"

        def _launch_command(self):
            raise AssertionError("No server launch allowed")

        def ensure_started(self):
            raise AssertionError("No server launch allowed")

        def shutdown(self):
            self.process = None

    base = SimpleNamespace(LlamaCppServerProcess=Server, in_wsl=lambda: True,
                           normalize_base_url=lambda value: value, DEFAULT_LLAMACPP_PORT=8001,
                           wsl_path_to_windows_path=str)
    variant._patch_llamacpp_launch_fit(base)
    instance = Server(SimpleNamespace(model_name="owned"))
    instance.base_url = "http://127.0.0.1:8001"
    instance._qubitz_owner_token = "f" * 32
    instance._qubitz_owned_launch_epoch = time.time()
    monkeypatch.setattr(variant, "_build_powershell_management_command", lambda *_: ["powershell", "-Command", ""])
    results = iter([
        SimpleNamespace(returncode=1, stdout='[{"pid":123,"created":"exact"}]', stderr="bounded wait timeout"),
        SimpleNamespace(returncode=0 if recovered else 1, stdout="[]", stderr="final failure"),
    ])
    monkeypatch.setattr(variant.subprocess, "run", lambda *_args, **_kwargs: next(results))
    instance.shutdown()
    assert instance._qubitz_cleanup_outcome["initial_detail"] == "bounded wait timeout"
    assert instance._qubitz_cleanup_outcome["status"] == ("recovered_after_proxy_close" if recovered else "failed")
    assert (instance._qubitz_owned_launch_epoch is None) is recovered
    assert instance._qubitz_cleanup_outcome["receipt"]["pid"] == 123


@pytest.mark.parametrize("replacement,passes", [
    ("return sum(prices)*(100-discount)/100", False),
    ("return sum(prices) if discount == 0 else sum(prices)*(100-discount)/100", True),
])
def test_default_comparison_catches_numeric_equality_false_positive(tmp_path, replacement, passes):
    previous = tmp_path / "previous.py"
    current = tmp_path / "current.py"
    previous.write_text("def total(prices):\n return sum(prices)\n")
    current.write_text("def total(prices, discount=0):\n " + replacement + "\n")
    code = default_behavior_comparison(str(current), str(previous), "total")
    if passes:
        exec(compile(code, "oracle", "exec"), {})  # noqa: S102 - test-owned sources
    else:
        with pytest.raises(AssertionError):
            exec(compile(code, "oracle", "exec"), {})  # noqa: S102 - test-owned sources


@pytest.mark.parametrize("regressed", [True, False])
def test_strict_oracle_runs_in_independent_audited_process(tmp_path, regressed):
    current = tmp_path / "pricing.py"
    current.write_text("def total(prices):\n return sum(prices)\n")
    before = workspace_manifest(tmp_path)
    backup = tmp_path / "backup/pricing.py.001.bak"
    backup.parent.mkdir()
    backup.write_bytes(current.read_bytes())
    current.write_text("def total(prices, discount=0):\n return " + (
        "sum(prices)*(100-discount)/100" if regressed else "sum(prices) if discount == 0 else sum(prices)*(100-discount)/100") + "\n")
    (tmp_path / "changelog.txt").write_text("backup/pricing.py.001.bak | pricing.py | Add an optional discount.\n")
    turn = Turn("Extend pricing.py preserving omitted-discount behavior.",
                (Check("default_behavior", "pricing.py", "total"),), ("pricing.py",))
    issues, evidence = verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))
    assert bool(issues) is regressed
    assert evidence[0]["verification_process"]["exit_code"] == (1 if regressed else 0)


def test_v3_does_not_rewrite_v2_oracles():
    old = matrix.selected_scenarios(None, "expanded-v2")
    new = matrix.selected_scenarios(None, "expanded-v3")
    assert len(old) == len(new) == 26
    assert sum(a != b for a, b in zip(old, new, strict=True)) == 1
    case = next(item for item in new if item.name == "multi_module_requirement_revision")
    assert "default_behavior" in {check.kind for check in case.turns[1].checks}


@pytest.mark.parametrize("field,value", [("pid", 2), ("creation_ticks", "different"),
                                        ("executable", "other.exe"), ("command_line", "other command")])
def test_stop_identity_rejects_reuse_and_changed_arguments(field, value):
    expected = {"pid": 1, "creation_ticks": "100", "executable": "python.exe", "command_line": "python.exe matrix.py "}
    assert control.same_identity({**expected, "command_line": expected["command_line"].rstrip()}, expected)
    assert not control.same_identity({**expected, field: value}, expected)


def test_stop_request_is_bound_to_one_coordinator(tmp_path):
    expected = {"pid": 1, "creation_ticks": "100", "executable": "python.exe", "command_line": "python matrix.py"}
    (tmp_path / "coordinator_identity.json").write_text(json.dumps(expected))
    (tmp_path / "stop_request.json").write_text(json.dumps({"coordinator": expected}))
    assert control.stop_requested(tmp_path)
    (tmp_path / "coordinator_identity.json").write_text(json.dumps({**expected, "creation_ticks": "101"}))
    assert not control.stop_requested(tmp_path)


@pytest.mark.parametrize("cleanup_blocked", [False, True])
def test_cooperative_stop_checkpoints_finished_child_without_overwriting(tmp_path, monkeypatch, cleanup_blocked):
    import smoke_complex_tasks

    output = tmp_path / "campaign"
    case = SimpleNamespace(name="test", turns=(SimpleNamespace(),))
    identity = {"pid": 1, "creation_ticks": "100", "executable": "python.exe", "command_line": "python matrix.py "}
    monkeypatch.setattr(control, "process_identity", lambda _pid: identity)
    monkeypatch.setattr(matrix, "variants", lambda *_: ["test.py"])
    monkeypatch.setattr(matrix, "selected_scenarios", lambda *_: [case])
    monkeypatch.setattr(matrix, "asdict", lambda _case: {})
    monkeypatch.setattr(matrix, "freeze_sources", lambda: {"source": "unchanged"})
    monkeypatch.setattr(matrix, "validate_output", lambda path: path)
    monkeypatch.setattr(smoke_complex_tasks, "protected_source_manifest", lambda *_: {"protected": "unchanged"})
    child_bytes = b'{"status":"passed","finished":"captured","turns":[{"turn":1,"status":"passed"}]}'
    child_path = None

    def run_attempt(_args, _name, _case, directory):
        nonlocal child_path
        directory.mkdir(parents=True)
        child_path = directory / "result.json"
        child_path.write_bytes(child_bytes)
        matrix.save_json(output / "stop_request.json", {"coordinator": identity})
        return {**json.loads(child_bytes), "variant": "test.py", "scenario": "test", "output": str(directory),
                "status": "harness_error" if cleanup_blocked else "passed"}

    monkeypatch.setattr(matrix, "run_attempt", run_attempt)
    args = SimpleNamespace(output=output, variants=None, scenarios=None, tier="large", suite="expanded-v3",
                           repeats=1, gui=True, timeout=10, startup_allowance=0, distro="Ubuntu", resume=False)
    assert matrix._coordinate(args) == (2 if cleanup_blocked else 0)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == ("stopped_infrastructure" if cleanup_blocked else "paused")
    assert summary["runs"][0]["status"] == ("harness_error" if cleanup_blocked else "passed")
    assert child_path.read_bytes() == child_bytes
    if not cleanup_blocked:
        assert control.stop_campaign(output)["already_checkpointed"]
    assert child_path.read_bytes() == child_bytes


def test_stop_refuses_changed_coordinator_before_writing_request(tmp_path, monkeypatch):
    (tmp_path / matrix.MARKER).write_text("developer-only follow-up matrix\n")
    (tmp_path / "summary.json").write_text('{"status":"running"}')
    identity = {"pid": 1, "creation_ticks": "100", "executable": "python.exe", "command_line": "python matrix.py"}
    (tmp_path / "coordinator_identity.json").write_text(json.dumps(identity))
    monkeypatch.setattr(matrix, "validate_output", lambda path: path)
    monkeypatch.setattr(control, "process_identity", lambda _pid: {**identity, "creation_ticks": "101"})
    with pytest.raises(RuntimeError, match="identity changed"):
        control.stop_campaign(tmp_path)
    assert not (tmp_path / "stop_request.json").exists()


@pytest.mark.parametrize("free,ready", [(10 * 1024**3, True), (1024, False)])
def test_storage_counts_all_requested_shards_and_exact_partial(variant, tmp_path, monkeypatch, free, ready):
    import huggingface_hub
    from huggingface_hub._local_folder import get_local_download_paths

    names = [f"model-0000{number}-of-00002.gguf" for number in (1, 2)]
    files = [SimpleNamespace(rfilename="Q4/" + name, size=100, blob_id="etag", lfs=None) for name in names]
    api = SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=files))
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: api)
    monkeypatch.setattr(variant.shutil, "disk_usage", lambda *_: SimpleNamespace(free=free))
    directory = tmp_path / ".cache/models/owner/repo"
    (directory / "Q4").mkdir(parents=True)
    target = directory / "Q4" / names[0]
    target.write_bytes(b"a" * 100)
    # NTFS file writes and WSL time.time may have different wall-clock origins.
    now = time.time()
    os.utime(target, (now, now))
    metadata = get_local_download_paths(directory, "Q4/" + names[0]).metadata_path
    metadata.write_text("commit\netag\n" + str(now + 1) + "\n")
    partial = get_local_download_paths(directory, "Q4/" + names[1]).incomplete_path("etag")
    partial.write_bytes(b"b" * 40)
    if not ready:
        with pytest.raises(RuntimeError, match="insufficient_space"):
            variant._model_storage_preflight(tmp_path, "owner/repo", names[0])
    else:
        receipt = variant._model_storage_preflight(tmp_path, "owner/repo", names[0])
        assert receipt["remaining_bytes"] == 60
        assert sum(item["completed_bytes"] for item in receipt["files"]) == 100
        assert receipt["files"][1]["resumable_bytes"] == 40


def test_storage_does_not_treat_directory_as_cached_model(variant, tmp_path, monkeypatch):
    import huggingface_hub

    model = SimpleNamespace(rfilename="gpt-oss-120b-F16.gguf", size=66_000_000_000, blob_id="x", lfs=None)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=[model])))
    monkeypatch.setattr(variant.shutil, "disk_usage", lambda *_: SimpleNamespace(free=68_000_000_000))
    (tmp_path / ".cache/models/owner/repo").mkdir(parents=True)
    with pytest.raises(RuntimeError, match="insufficient_space"):
        variant._model_storage_preflight(tmp_path, "owner/repo", model.rfilename)


@pytest.mark.parametrize("cached,assembled,short", [(0, False, False), (1, False, False),
                                                   (2, False, False), (2, True, False), (0, False, True)])
def test_storage_counts_multipart_download_and_assembly(variant, tmp_path, monkeypatch, cached, assembled, short):
    import huggingface_hub
    from huggingface_hub._local_folder import get_local_download_paths

    filename = "model.gguf"
    files = [SimpleNamespace(rfilename=f"Q4/{filename}.part{number}of2", size=100, blob_id="etag", lfs=None)
             for number in (1, 2)]
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=files)))
    monkeypatch.setattr(variant.shutil, "disk_usage", lambda *_: SimpleNamespace(free=2 * 1024**3 + 399 if short else 10 * 1024**3))
    directory = tmp_path / ".cache/models/owner/repo"
    (directory / "Q4").mkdir(parents=True)
    for item in files[:cached]:
        target = directory / item.rfilename
        target.write_bytes(b"x" * item.size)
        now = time.time()
        os.utime(target, (now, now))
        get_local_download_paths(directory, item.rfilename).metadata_path.write_text(f"commit\netag\n{now + 1}\n")
    if assembled:
        (directory / "Q4" / filename).write_bytes(b"x" * 200)
    if short:
        with pytest.raises(RuntimeError, match="insufficient_space"):
            variant._model_storage_preflight(tmp_path, "owner/repo", filename)
    else:
        receipt = variant._model_storage_preflight(tmp_path, "owner/repo", filename)
        assert receipt["assembly_remaining_bytes"] == (0 if assembled else 200)
        assert receipt["remaining_bytes"] == 200 - cached * 100 + receipt["assembly_remaining_bytes"]
        assert len(receipt["files"]) == 2


@pytest.mark.parametrize("condition", ["missing", "duplicate", "ambiguous"])
def test_storage_refuses_incomplete_or_ambiguous_multipart_metadata(variant, tmp_path, monkeypatch, condition):
    import huggingface_hub

    files = [SimpleNamespace(rfilename=f"Q4/model.gguf.part{number}of2", size=100, blob_id="etag", lfs=None)
             for number in (1, 2)]
    if condition == "missing":
        files.pop()
    elif condition == "duplicate":
        files.append(files[0])
    else:
        files.append(SimpleNamespace(rfilename="other/model.gguf.part1of2", size=100, blob_id="etag", lfs=None))
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=files)))
    with pytest.raises(RuntimeError, match="metadata"):
        variant._model_storage_preflight(tmp_path, "owner/repo", "model.gguf")


def test_storage_preserves_corrupt_metadata_and_does_not_credit_unverified_file(variant, tmp_path, monkeypatch):
    import huggingface_hub

    model = SimpleNamespace(rfilename="model.gguf", size=100, blob_id="etag", lfs=None)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **_kwargs: SimpleNamespace(siblings=[model])))
    monkeypatch.setattr(variant.shutil, "disk_usage", lambda *_: SimpleNamespace(free=10 * 1024**3))
    directory = tmp_path / ".cache/models/owner/repo"
    directory.mkdir(parents=True)
    (directory / "model.gguf").write_bytes(b"x" * 100)
    metadata = directory / ".cache/huggingface/download/model.gguf.metadata"
    metadata.parent.mkdir(parents=True)
    metadata.write_bytes(b"malformed metadata preserved verbatim\n")
    receipt = variant._model_storage_preflight(tmp_path, "owner/repo", model.rfilename)
    assert receipt["remaining_bytes"] == 100
    assert metadata.read_bytes() == b"malformed metadata preserved verbatim\n"


@pytest.mark.parametrize("condition", ["stale", "future", "etag", "size"])
def test_storage_timestamp_and_identity_guards_are_not_relaxed(variant, tmp_path, monkeypatch, condition):
    import huggingface_hub

    model = SimpleNamespace(rfilename="model.gguf", size=100, blob_id="etag", lfs=None)
    monkeypatch.setattr(huggingface_hub, "HfApi", lambda: SimpleNamespace(model_info=lambda **kwargs: SimpleNamespace(siblings=[model])))
    monkeypatch.setattr(variant.shutil, "disk_usage", lambda *_: SimpleNamespace(free=10 * 1024**3))
    directory = tmp_path / ".cache/models/owner/repo"
    directory.mkdir(parents=True)
    target = directory / "model.gguf"
    target.write_bytes(b"x" * (99 if condition == "size" else 100))
    now = time.time()
    os.utime(target, (now, now))
    timestamp = now - 120 if condition == "stale" else now + 3600 if condition == "future" else now + 1
    etag = "other" if condition == "etag" else "etag"
    metadata = directory / ".cache/huggingface/download/model.gguf.metadata"
    metadata.parent.mkdir(parents=True)
    original = f"commit\n{etag}\n{timestamp}\n"
    metadata.write_text(original)
    receipt = variant._model_storage_preflight(tmp_path, "owner/repo", model.rfilename)
    assert receipt["remaining_bytes"] == 100
    assert receipt["files"][0]["completed_bytes"] == 0
    assert metadata.read_text() == original
