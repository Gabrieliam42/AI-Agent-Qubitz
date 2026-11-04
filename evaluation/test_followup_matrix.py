"""CPU-only contracts for the evaluation suite; never import a product variant."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix
from followup_matrix_cases import (
    Check,
    Turn,
    changed_paths,
    compatibility_scope_evidence,
    fixture_path,
    prepare_scenario,
    scenarios,
    verify_turn,
    workspace_manifest,
    write_fixture,
)
from followup_mcp_fixture import Store


def test_coordinator_event_keeps_output_metadata_separate_from_campaign_path(tmp_path):
    matrix.coordinator_event(tmp_path, "trajectory_launch", output="retained-attempt-path")
    row = json.loads((tmp_path / "coordinator_events.jsonl").read_text())
    assert row["kind"] == "trajectory_launch" and row["output"] == "retained-attempt-path"


def test_manifest_differences_identifies_changed_added_and_removed_paths():
    expected = {"a.py": {"sha256": "old"}, "removed.md": {"size": 1}}
    observed = {"a.py": {"sha256": "new"}, "added.md": {"size": 2}}

    assert matrix.manifest_differences(expected, observed) == [
        {"path": "a.py", "expected": {"sha256": "old"}, "observed": {"sha256": "new"}},
        {"path": "added.md", "expected": None, "observed": {"size": 2}},
        {"path": "removed.md", "expected": {"size": 1}, "observed": None},
    ]


def test_compatibility_scope_rejects_caveated_but_contradictory_guarantees():
    observed_answer = (
        "The omitted call reduces exactly to the pre-extension behavior and computes the same arithmetic. "
        "Types, precision, and overflow semantics are untouched. I have not separately proven every edge case."
    )

    evidence = compatibility_scope_evidence(observed_answer)
    assert evidence["limitation_present"] is True
    assert evidence["unqualified_claims"] == [
        "exact_prior_behavior",
        "unchanged_numeric_or_exception_semantics",
    ]


def test_compatibility_scope_accepts_specific_evidence_with_bounded_conclusion():
    answer = (
        "The six same-input cases recorded earlier matched. That evidence is limited to those cases; "
        "it does not establish behavior for every type, precision, overflow, or exception edge case."
    )

    evidence = compatibility_scope_evidence(answer)
    assert evidence == {"limitation_present": True, "unqualified_claims": []}


def test_compatibility_scope_ignores_explicitly_denied_guarantees():
    answer = (
        "A default of 0 does not by itself guarantee the return type is unchanged. "
        "Identical result types, numeric precision, overflow, and exception behavior are not guaranteed. "
        "These claims remain unverified outside the inspected cases."
    )

    evidence = compatibility_scope_evidence(answer)
    assert evidence == {"limitation_present": True, "unqualified_claims": []}


def test_record_source_drift_persists_manifest_difference_before_invalidation(tmp_path):
    summary = {"status": "running"}
    differences = matrix.record_source_drift(
        tmp_path, summary, {"module.py": {"sha256": "old"}},
        {"module.py": {"sha256": "new"}}, boundary="after_trajectory", scenario="case",
    )

    saved = json.loads((tmp_path / "summary.json").read_text())
    event = json.loads((tmp_path / "coordinator_events.jsonl").read_text().splitlines()[0])
    assert saved["status"] == "invalid_source_drift"
    assert saved["source_drift_events"][0]["differences"] == differences
    assert event["kind"] == "source_drift" and event["differences"] == differences
    assert event["scenario"] == "case"


def test_coordinator_launch_path_records_and_finalizes_one_mock_attempt(tmp_path, monkeypatch):
    import followup_campaign_control
    import smoke_complex_tasks

    monkeypatch.setattr(followup_campaign_control, "process_identity", lambda _pid: {
        "pid": 123, "creation_ticks": "100", "executable": "python.exe", "command_line": "python matrix.py",
    })
    monkeypatch.setattr(matrix, "validate_output", lambda path: path)
    monkeypatch.setattr(matrix, "freeze_sources", lambda: {"source": "frozen"})
    monkeypatch.setattr(smoke_complex_tasks, "protected_source_manifest", lambda _path: {})
    calls = []

    def run_attempt(_args, name, case, output):
        calls.append(output)
        return {"variant": name, "scenario": case.name, "status": "passed", "task_status": "passed",
                "gui_status": "passed", "finished": matrix.stamp(), "output": str(output),
                "turns": [{"turn": number, "status": "passed"} for number in range(1, len(case.turns) + 1)]}

    monkeypatch.setattr(matrix, "run_attempt", run_attempt)
    args = SimpleNamespace(variants=["AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"], tier="large",
                           scenarios=["create_edit_execute"], suite="expanded-v2", output=tmp_path / "campaign",
                           repeats=1, gui=True, timeout=30, startup_allowance=0, distro="Ubuntu", resume=False)
    assert matrix._coordinate(args) == 0
    summary = json.loads((args.output / "summary.json").read_text())
    assert summary["status"] == "complete" and len(summary["runs"]) == len(calls) == 1
    events = [json.loads(line) for line in (args.output / "coordinator_events.jsonl").read_text().splitlines()]
    launch = next(row for row in events if row["kind"] == "trajectory_launch")
    assert launch["output"] == str(calls[0])
    assert events[-1]["kind"] == "coordinator_stop" and events[-1]["status"] == "complete"


def test_catalog_has_distinct_chained_and_negative_cases():
    catalog = scenarios()
    assert len(catalog) >= 20
    assert len({case.name for case in catalog}) == len(catalog)
    assert all(len(case.turns) >= 2 for case in catalog)
    assert {case.category for case in catalog} >= {"mcp", "mcp_recovery", "execution_recovery", "references", "negative_controls", "constraints", "steering"}


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "D:/escape", "a\\b", "a/../b", "a//b"])
def test_fixture_paths_reject_escapes(tmp_path, name):
    with pytest.raises(ValueError):
        fixture_path(tmp_path, name)


def test_fixture_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "fixture"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks unavailable to this account")
    with pytest.raises(ValueError):
        write_fixture(root, "link/unsafe.txt", "bad")
    assert not (outside / "unsafe.txt").exists()


def test_setup_creates_private_workspaces_with_complete_mcp_source(tmp_path):
    case = next(case for case in scenarios() if case.name == "mcp_returned_identifier_chain")
    roots = prepare_scenario(case, tmp_path / "case")
    assert roots["a"] != roots["b"]
    assert "server.run(transport=" in (roots["a"] / "fixture_mcp.py").read_text()
    assert "protected.txt" in workspace_manifest(roots["b"])
    with pytest.raises(FileExistsError):
        prepare_scenario(case, tmp_path / "case")


def test_false_completion_does_not_pass_missing_artifact(tmp_path):
    before = workspace_manifest(tmp_path)
    turn = Turn("Create result.txt", (Check("text", "result.txt", "done\n"),), ("result.txt",))
    issues, _ = verify_turn(tmp_path, turn, {"assistant": "Created it. [Completion status: verified]"}, before, before)
    assert "postcondition_failed:text:result.txt" in issues


def test_unrelated_question_cannot_silently_mutate_previous_target(tmp_path):
    write_fixture(tmp_path, "earlier.txt", "safe\n")
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "earlier.txt", "changed\n")
    turn = Turn("What is 6 times 7?", (Check("exact_answer", expected="42"),))
    issues, _ = verify_turn(tmp_path, turn, {"assistant": "42\n\n(Elapsed time: 00:01)"}, before, workspace_manifest(tmp_path))
    assert issues == ["unexpected_mutation:earlier.txt"]


def test_python_edit_allows_only_verified_policy_backup_and_changelog(tmp_path):
    write_fixture(tmp_path, "src/job.py", "before = True\n")
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "src/job.py", "before = False\n")
    backup = "backup/src__job.py.001.bak"
    write_fixture(tmp_path, backup, "before = True\n")
    write_fixture(tmp_path, "changelog.txt", "backup\\src__job.py.001.bak | src/job.py | Backed up before the edit.\n")
    turn = Turn("Edit job.py", (), ("src/job.py",))
    assert verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0] == []


def test_python_edit_rejects_incorrect_backup_content(tmp_path):
    write_fixture(tmp_path, "job.py", "before = True\n")
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "job.py", "before = False\n")
    backup = "backup/job.py.001.bak"
    write_fixture(tmp_path, backup, "wrong backup\n")
    write_fixture(tmp_path, "changelog.txt", f"{backup} | job.py | Backed up before the edit.\n")
    turn = Turn("Edit job.py", (), ("job.py",))
    assert verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0] == [
        "unexpected_mutation:backup/job.py.001.bak,changelog.txt",
        "python_backup_policy_failed:job.py",
    ]


def test_cleanup_waits_for_transient_incomplete_server_identity(monkeypatch):
    import smoke_create_calculator as smoke

    calls = iter((smoke.IncompleteProcessIdentityError("transient"), []))

    def inventory():
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(smoke, "model_processes", inventory)
    monkeypatch.setattr(matrix.time, "sleep", lambda _seconds: None)
    matrix.require_model_servers_clear_after_cleanup(timeout=1)


def test_cleanup_still_rejects_a_verified_unowned_server(monkeypatch):
    import smoke_create_calculator as smoke

    monkeypatch.setattr(smoke, "model_processes", lambda: [{"ProcessId": 123}])
    with pytest.raises(RuntimeError, match="server remains"):
        matrix.require_model_servers_clear_after_cleanup(timeout=0)


def test_old_mcp_receipt_cannot_verify_new_turn(tmp_path):
    store = Store(tmp_path)
    store.audit("echo", value="verified", label="old")
    turn = Turn("Call echo again", (Check("mcp", expected=[{"tool": "echo", "value": "verified"}]),))
    observed = {"mcp_audit_start": 1, "tools": [{"name": "call_project_mcp_tool", "arguments": {"tool_name": "echo"}}]}
    state = workspace_manifest(tmp_path)
    issues, _ = verify_turn(tmp_path, turn, observed, state, state)
    assert "postcondition_failed:mcp:" in issues


def test_claimed_tool_output_without_actual_invocation_fails(tmp_path):
    Store(tmp_path).audit("echo", value="verified")
    turn = Turn("Call echo", (Check("mcp", expected=[{"tool": "echo", "value": "verified"}]),))
    state = workspace_manifest(tmp_path)
    issues, _ = verify_turn(tmp_path, turn, {"assistant": "verified", "tools": []}, state, state)
    assert issues


def test_record_update_requires_real_returned_id(tmp_path):
    store = Store(tmp_path)
    record = store.create("alpha", 7)
    with pytest.raises(ValueError, match="Unknown record_id"):
        store.update("made-up", 11)
    assert store.get(record["record_id"])["value"] == 7
    store.update(record["record_id"], 11)
    assert store.get(record["record_id"])["label"] == "alpha"
    assert len(store.read("records.json", {})) == 1


def test_transient_counter_survives_server_restart(tmp_path):
    with pytest.raises(ValueError, match="transient"):
        Store(tmp_path).flaky("first")
    assert Store(tmp_path).flaky("first") == {"value": "first"}


def test_driver_keeps_same_runner_across_turns(tmp_path):
    class Runner:
        def __init__(self):
            self.history = []
            self.config = SimpleNamespace()
            self.requests = []

        def set_access_mode(self, value):
            self.config.access_mode = value

        def request_cancel(self):
            raise AssertionError("Short CPU fixture must not time out")

        def run_sync(self, prompt, callback):
            self.requests.append(prompt)
            self.history.append({"role": "user", "content": prompt})
            return "ok"

    observer = matrix.Observer(tmp_path)
    runner = Runner()
    observer.turn = 1
    first = matrix.execute_turn(Turn("first", ()), runner, None, None, tmp_path, observer, 30)
    observer.turn = 2
    second = matrix.execute_turn(Turn("follow-up", ()), runner, None, None, tmp_path, observer, 30)
    assert first["runtime"]["runner_identity"] == second["runtime"]["runner_identity"]
    assert runner.requests == ["first", "follow-up"]
    assert len(second["runtime"]["history"]) == 2


def test_evaluator_execution_accepts_wrapper_owned_command(tmp_path):
    turn = Turn("Run job.py", (Check("execution", "job.py"),))
    state = workspace_manifest(tmp_path)
    good = {"processes": [{"command": ["python", str(tmp_path / "job.py")], "exit_code": 0}]}
    assert verify_turn(tmp_path, turn, good, state, state)[0] == []
    assert verify_turn(tmp_path, turn, {"assistant": "ran job.py successfully"}, state, state)[0]


def test_nonpython_transaction_backup_is_checked_not_ignored(tmp_path):
    write_fixture(tmp_path, "settings.json", '{"limit":3}\n')
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "settings.json", '{"limit":7}\n')
    write_fixture(tmp_path, ".qubitz/backups/settings.json.001.bak", '{"limit":3}\n')
    turn = Turn("Edit settings.json", (), ("settings.json",))
    assert not verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0]
    write_fixture(tmp_path, ".qubitz/backups/settings.json.001.bak", "wrong\n")
    assert verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0]
    write_fixture(tmp_path, ".qubitz/backups/unrelated.json.001.bak", "extra\n")
    assert "unrelated.json" in str(verify_turn(tmp_path, turn, {}, before, workspace_manifest(tmp_path))[0])


@pytest.mark.parametrize("exit_code", [0, 1])
def test_actual_mcp_worker_command_counts_as_executed(tmp_path, exit_code):
    turn = Turn("Run job.py", (Check("execution", "job.py"),))
    state = workspace_manifest(tmp_path)
    row = {"turn": 2, "capture_origin": "mcp_host", "name": "run_existing_entrypoint",
           "arguments": {"path": "job.py"}, "result": {"is_error": False, "structured_content": {
               "path": "job.py", "cwd": ".", "command": ["python", str(tmp_path / "job.py")],
               "returncode": exit_code}}}
    assert not verify_turn(tmp_path, turn, {"turn": 2, "tools": [row]}, state, state)[0]
    assert verify_turn(tmp_path, turn, {"turn": 3, "tools": [row]}, state, state)[0]
    row["result"]["structured_content"]["returncode"] = True
    assert verify_turn(tmp_path, turn, {"turn": 2, "tools": [row]}, state, state)[0]
    row["result"]["structured_content"]["returncode"] = exit_code
    row["result"]["structured_content"]["command"] = ["python", "other.py"]
    assert verify_turn(tmp_path, turn, {"turn": 2, "tools": [row]}, state, state)[0]


def test_mcp_worker_accepts_equivalent_absolute_and_relative_paths(tmp_path):
    turn = Turn("Run job.py", (Check("execution", "job.py"),))
    state = workspace_manifest(tmp_path)
    script = tmp_path / "job.py"
    for requested, reported in ((str(script), "job.py"), ("job.py", str(script))):
        row = {"turn": 2, "capture_origin": "mcp_host", "name": "run_existing_entrypoint",
               "arguments": {"path": requested}, "result": {"is_error": False, "structured_content": {
                   "path": reported, "cwd": ".", "command": ["python", str(script)], "returncode": 0}}}
        assert verify_turn(tmp_path, turn, {"turn": 2, "tools": [row]}, state, state)[0] == []

    row["arguments"]["path"] = str(tmp_path / "other.py")
    assert verify_turn(tmp_path, turn, {"turn": 2, "tools": [row]}, state, state)[0]


def test_gui_access_selector_is_changed_before_send(tmp_path):
    config = SimpleNamespace(access_mode="full")
    runner = SimpleNamespace(config=config, history=[])
    selection = SimpleNamespace(value="Full Access")
    selection.set = lambda value: setattr(selection, "value", value)
    gui = SimpleNamespace(access_mode_var=selection, root=SimpleNamespace(winfo_exists=lambda: True))
    gui._apply_access_mode = lambda: setattr(config, "access_mode", selection.value)

    def send(_gui, _prompt, _timeout):
        assert selection.value == "Read-only"
        assert config.access_mode == "Read-only"
        return {"assistant": "Read-only blocks editing.", "completed": True}

    smoke = SimpleNamespace(_send_prompt=send)
    result = matrix.execute_turn(Turn("Edit file.txt", (), access="read_only"), runner, gui, smoke,
                                 tmp_path, matrix.Observer(tmp_path), 30)
    assert result["completed"]


def test_manifest_detects_new_modified_and_removed_files(tmp_path):
    write_fixture(tmp_path, "keep.txt", "old")
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "keep.txt", "new")
    write_fixture(tmp_path, "extra.txt", "new")
    assert changed_paths(before, workspace_manifest(tmp_path)) == ["extra.txt", "keep.txt"]


def test_mcp_count_detects_blind_replayed_mutation(tmp_path):
    store = Store(tmp_path)
    store.create("one", 4, "commit_then_error")
    store.create("one", 4, "commit_then_error")
    turn = Turn("Commit once", (Check("mcp_count", expected={"commit_then_error": 1}), Check("record", expected={"label": "one", "value": 4})))
    state = workspace_manifest(tmp_path)
    assert len(verify_turn(tmp_path, turn, {}, state, state)[0]) == 2


def test_observer_keeps_stream_on_disk_not_in_memory(tmp_path):
    observer = matrix.Observer(tmp_path)
    observer.turn = 2
    observer.emit("http_stream_line", line="data: exposed output")
    assert observer.events == []
    record = json.loads((tmp_path / "events.jsonl").read_text())
    assert record["turn"] == 2 and record["kind"] == "http_stream_line"


def owned_server_fixture(tmp_path):
    owner = "a" * 32
    matrix.save_json(tmp_path / "launch.json", {"owner": owner})
    row = {"ProcessId": 123, "CreationDate": "639268218832164070",
           "ExecutablePath": r"D:\AI_Research\AI_Agent_Qubitz\.cache\runtime\llama-server.exe"}
    matrix.save_json(tmp_path / "owned_windows_servers.json", {
        "schema_version": matrix.SCHEMA_VERSION, "owner": owner, "servers": [row],
    })
    return owner, row


def test_windows_timestamp_preserves_seventh_fractional_digit():
    assert matrix.windows_creation_ticks("2026-10-05T18:31:23.2164070Z") == "639268218832164070"
    assert matrix.windows_creation_ticks("2026-10-05T18:31:23.2164071Z") == "639268218832164071"
    with pytest.raises(ValueError):
        matrix.windows_creation_ticks("unknown")


@pytest.mark.parametrize("startup_failed", [False, True])
def test_observer_persists_product_owned_identity_at_startup(tmp_path, monkeypatch, startup_failed):
    import smoke_create_calculator as smoke

    owner, expected = owned_server_fixture(tmp_path)
    # Capture must write fresh evidence, rather than depend on the fixture's receipt.
    (tmp_path / "owned_windows_servers.json").unlink()
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    log_path = tmp_path / ".cache" / "llama.cpp" / "llama-server.log"
    log_path.parent.mkdir(parents=True)
    log_path.write_text("[startup] llama-server\n", encoding="utf-8")
    observer = matrix.Observer(tmp_path)

    class Server:
        def __init__(self):
            self._qubitz_owned_windows_receipt = {"pid": 123, "created": "2026-10-05T18:31:23.2164070Z"}
            self._qubitz_owner_token = "b" * 32
            self.model_path = "model.gguf"
            self.config = SimpleNamespace(model_name="local-model")
            self.log_path = log_path

        def resolve_executable(self):
            return expected["ExecutablePath"]

        def ensure_started(self):
            if startup_failed:
                raise RuntimeError("Startup failed after ownership capture")
            return "ready"

    base = SimpleNamespace(LlamaCppServerProcess=Server, MCPHost=SimpleNamespace(call_tool=None),
                           wsl_path_to_windows_path=lambda value: value)
    monkeypatch.setattr(smoke, "install_request_capture", lambda *_args: None)
    observer.install(base)
    server = Server()
    for _ in range(2):
        if startup_failed:
            with pytest.raises(RuntimeError, match="Startup failed"):
                server.ensure_started()
        else:
            assert server.ensure_started() == "ready"
    captured = json.loads((tmp_path / "owned_windows_servers.json").read_text())
    assert captured["owner"] == owner
    assert len(captured["servers"]) == 1
    assert {key: captured["servers"][0][key] for key in expected} == expected
    assert captured["servers"][0]["log_path"] == str(log_path)


def test_cleanup_passes_only_captured_identities_to_existing_guard(tmp_path, monkeypatch):
    import smoke_create_calculator as smoke

    owner, expected = owned_server_fixture(tmp_path)
    calls = []

    def cleanup(owned):
        calls.append(owned)
        return {"verified_idle": True}

    monkeypatch.setattr(smoke, "wait_for_owned_cleanup", cleanup)
    assert matrix.cleanup_captured_windows_servers(tmp_path, owner)["verified_idle"]
    assert calls == [[expected]]


@pytest.mark.parametrize("damage", ["receipt_owner", "launch_owner", "incomplete_identity"])
def test_cleanup_rejects_invalid_receipts_before_termination(tmp_path, monkeypatch, damage):
    import smoke_create_calculator as smoke

    owner, _ = owned_server_fixture(tmp_path)
    path = tmp_path / ("launch.json" if damage == "launch_owner" else "owned_windows_servers.json")
    payload = json.loads(path.read_text())
    if damage == "incomplete_identity":
        payload["servers"][0]["CreationDate"] = ""
    else:
        payload["owner"] = "c" * 32
    matrix.save_json(path, payload)
    monkeypatch.setattr(smoke, "wait_for_owned_cleanup", lambda *_args: pytest.fail("Invalid ownership authorized cleanup"))
    with pytest.raises(RuntimeError):
        matrix.cleanup_captured_windows_servers(tmp_path, owner)


@pytest.mark.parametrize("change", ["creation", "executable", "unrelated", "missing_receipt"])
def test_cleanup_refuses_reused_pid_or_unowned_server(tmp_path, monkeypatch, change):
    import smoke_create_calculator as smoke

    owner, row = owned_server_fixture(tmp_path)
    current = dict(row)
    if change == "creation":
        current["CreationDate"] = "639268218832164071"
    elif change == "executable":
        current["ExecutablePath"] = r"D:\other\llama-server.exe"
    elif change == "unrelated":
        current["ProcessId"] = 456
    else:
        (tmp_path / "owned_windows_servers.json").unlink()
    monkeypatch.setattr(smoke, "model_processes", lambda: [current])
    monkeypatch.setattr(smoke, "stop_verified_server", lambda *_args: pytest.fail("Unowned process was stopped"))
    with pytest.raises(RuntimeError, match="Unowned model server"):
        matrix.cleanup_captured_windows_servers(tmp_path, owner)


@pytest.mark.parametrize("already_exited", [False, True])
def test_cleanup_guard_waits_for_product_shutdown_without_signalling(tmp_path, monkeypatch, already_exited):
    import smoke_create_calculator as smoke

    owner, row = owned_server_fixture(tmp_path)
    current = [] if already_exited else [row]
    tick = 0

    def clock():
        nonlocal tick
        tick += 1
        return float(tick)

    def sample():
        observed = list(current)
        current.clear()  # Simulate the application's own completed shutdown.
        return observed

    monkeypatch.setattr(smoke, "model_processes", sample)
    monkeypatch.setattr(smoke, "stop_verified_server", lambda *_args: pytest.fail("Observer signalled a server"))
    monkeypatch.setattr(smoke, "port_free", lambda: True)
    monkeypatch.setattr(smoke, "require_idle", lambda: None)
    monkeypatch.setattr(smoke.time, "monotonic", clock)
    monkeypatch.setattr(smoke.time, "sleep", lambda _seconds: None)
    assert matrix.cleanup_captured_windows_servers(tmp_path, owner)["verified_idle"]


def test_cleanup_guard_never_terminates_a_still_running_owned_server(tmp_path, monkeypatch):
    import smoke_create_calculator as smoke

    owner, row = owned_server_fixture(tmp_path)
    ticks = iter(range(100))
    monkeypatch.setattr(smoke.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(smoke.time, "sleep", lambda _: None)
    monkeypatch.setattr(smoke, "model_processes", lambda: [row])
    monkeypatch.setattr(smoke, "stop_verified_server", lambda *_args: pytest.fail("Observer signalled a server"))
    with pytest.raises(RuntimeError, match="cleanup did not settle"):
        matrix.cleanup_captured_windows_servers(tmp_path, owner)


def test_interrupted_child_cannot_pass_a_partially_finished_chain(tmp_path, monkeypatch):
    import complex_smoke_runtime as runtime

    import tmp_smoke_variant as smoke

    case = SimpleNamespace(name="interrupt_chain", gui_only=False, category="ordinary", turns=[
        Turn("First", ()), Turn("Second", ()), Turn("Third", ()),
    ])
    resets = []
    runner = SimpleNamespace(reset_runtime=lambda: resets.append(True))
    base = SimpleNamespace(AgentRunner=lambda _config: runner)
    roots = {"a": tmp_path / "a"}
    roots["a"].mkdir()
    monkeypatch.setattr(matrix, "validate_child", lambda *_args: tmp_path)
    monkeypatch.setattr(matrix, "selected_scenarios", lambda *_args: [case])
    monkeypatch.setattr(matrix, "prepare_scenario", lambda *_args: roots)
    monkeypatch.setattr(matrix, "install_private_memory", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(matrix.Observer, "install", lambda *_args: None)
    monkeypatch.setattr(matrix, "collect_logs", lambda *_args: [])
    monkeypatch.setattr(runtime, "propagate_owner", lambda *_args: None)
    monkeypatch.setattr(smoke, "_load_module", lambda *_args: (None, None))
    monkeypatch.setattr(smoke, "_build_wrapped_config", lambda *_args: (SimpleNamespace(), base))

    def execute(_turn, _runner, _gui, _smoke, _root, observer, _timeout, *, base=None):
        if observer.turn == 2:
            raise KeyboardInterrupt
        return {"completed": True, "assistant": "ok"}

    monkeypatch.setattr(matrix, "execute_turn", execute)
    args = SimpleNamespace(output=tmp_path, owner="a" * 32, suite="original-v1", scenario=case.name,
                           variant="cpu-fixture", tier="large", gui=False, startup_allowance=0, timeout=30)
    assert matrix.child(args) == matrix.STATUS_CODES["interrupted"]
    report = json.loads((tmp_path / "result.json").read_text())
    assert report["status"] == report["task_status"] == "interrupted"
    assert [turn["status"] for turn in report["turns"]] == ["passed", "interrupted", "not_reached"]
    assert resets == [True]
