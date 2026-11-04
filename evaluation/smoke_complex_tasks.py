"""Disposable 12-case Qubitz smoke campaign.

Every case runs in a fresh temporary workspace. The Qubitz repository is
snapshotted before and after each case and the campaign stops on any product
tree mutation. Only the temporary case workspace is removed during cleanup.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROTECTED_RUNTIME_DIRS = {".cache", ".git", ".memory", ".qubitz", "__pycache__"}
sys.path.insert(0, str(ROOT))
import evaluation.smoke_create_calculator as calculator_smoke
import tmp_smoke_variant as smoke
from evaluation.complex_smoke_runtime import OWNER_ENV, propagate_owner, verify_python
from qubitz_live_eval_policy import live_variant_allowed, require_live_variants_allowed
from runtime.paths import variant_paths


def save_json(path: Path, payload: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    temporary.replace(path)


def write_fixture(workspace: Path, relative: str, content: str) -> None:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


class Task:
    def __init__(
        self,
        task_id: str,
        prompt: str,
        setup: Callable[[Path], None],
        verify: Callable[[Path, dict[str, list[str]]], tuple[bool, list[str]]],
    ) -> None:
        self.task_id = task_id
        self.prompt = prompt
        self.setup = setup
        self.verify = verify


def no_files(_workspace: Path) -> None:
    return None


def setup_read_fixture(workspace: Path) -> None:
    write_fixture(workspace, "README.md", "# Fixture Project\n\nThis disposable project tests safe reading.\n")


def setup_search_fixture(workspace: Path) -> None:
    write_fixture(workspace, "notes.txt", "alpha\nTARGET_MARKER=verified\nomega\n")


def setup_edit_fixture(workspace: Path) -> None:
    write_fixture(workspace, "calculator.py", "def add(a, b):\n    return a + b\n")


def setup_run_fixture(workspace: Path) -> None:
    write_fixture(workspace, "run_me.py", "print('RUN_OK')\n")


def setup_test_fixture(workspace: Path) -> None:
    write_fixture(workspace, "math_utils.py", "def add(a, b):\n    return a - b\n")
    write_fixture(
        workspace,
        "test_math_utils.py",
        "from math_utils import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n",
    )


def setup_rename_fixture(workspace: Path) -> None:
    write_fixture(workspace, "rename_me.txt", "rename fixture\n")


def setup_delete_fixture(workspace: Path) -> None:
    write_fixture(workspace, "delete_me.txt", "delete fixture\n")


def verify_none(_workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    unexpected = [name for names in changes.values() for name in names]
    return not unexpected, (["unexpected_file_mutation"] if unexpected else [])


def verify_created_python(name: str) -> Callable[[Path, dict[str, list[str]]], tuple[bool, list[str]]]:
    def verify(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
        path = workspace / name
        issues: list[str] = []
        if not path.is_file():
            issues.append("requested_file_not_created")
        else:
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=name)
            except (OSError, SyntaxError, ValueError) as exc:
                issues.append(f"invalid_python:{exc}")
        allowed = {name}
        unexpected = [entry for names in changes.values() for entry in names if entry not in allowed]
        if unexpected:
            issues.append("unexpected_file_mutation")
        return not issues, issues

    return verify


def verify_edited_calculator(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    path = workspace / "calculator.py"
    issues: list[str] = []
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename="calculator.py")
        names = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if "subtract" not in names:
            issues.append("requested_subtract_function_missing")
    except (OSError, SyntaxError, ValueError) as exc:
        issues.append(f"invalid_python:{exc}")
    unexpected = [entry for names in changes.values() for entry in names if entry != "calculator.py"]
    if unexpected:
        issues.append("unexpected_file_mutation")
    passed, evidence = verify_python(workspace, "from calculator import add, subtract\nassert add(2, 3) == 5\nassert subtract(7, 3) == 4\nassert subtract(3, 7) == -4")
    if not passed:
        issues.append("calculator_behavior_failed:" + json.dumps(evidence))
    return not issues, issues


def verify_multi_file(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    required = {"miniapp/__init__.py", "miniapp/cli.py"}
    issues = [f"missing:{name}" for name in required if not (workspace / name).is_file()]
    unexpected = [entry for names in changes.values() for entry in names if entry not in required]
    if unexpected:
        issues.append("unexpected_file_mutation")
    passed, evidence = verify_python(workspace, "import miniapp\nimport miniapp.cli")
    if not passed:
        issues.append("package_not_importable:" + json.dumps(evidence))
    return not issues, issues


def verify_test_repair(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    passed, evidence = verify_python(
        workspace,
        "import pytest\nraise SystemExit(pytest.main(['-q', '-s', '-p', 'no:cacheprovider', 'test_math_utils.py']))",
    )
    if not passed:
        issues.append("failing_test_not_repaired:" + json.dumps(evidence))
    unexpected = [entry for names in changes.values() for entry in names if entry != "math_utils.py"]
    if unexpected:
        issues.append("unexpected_file_mutation")
    return not issues, issues


def verify_rename(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if (workspace / "rename_me.txt").exists() or not (workspace / "renamed.txt").is_file():
        issues.append("rename_postcondition_failed")
    elif (workspace / "renamed.txt").read_bytes() != b"rename fixture\n":
        issues.append("rename_content_changed")
    unexpected = [entry for names in changes.values() for entry in names if entry not in {"rename_me.txt", "renamed.txt"}]
    if unexpected:
        issues.append("unexpected_file_mutation")
    return not issues, issues


def verify_delete(workspace: Path, changes: dict[str, list[str]]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if (workspace / "delete_me.txt").exists():
        issues.append("requested_file_not_deleted")
    unexpected = [entry for names in changes.values() for entry in names if entry != "delete_me.txt"]
    if unexpected:
        issues.append("unexpected_file_mutation")
    return not issues, issues


TASKS = (
    Task("simple_question", "What is the capital of Romania? Answer with only the city name.", no_files, verify_none),
    Task("identity_question", "Who are you? Answer briefly with your configured model identity.", no_files, verify_none),
    Task("workspace_listing", "List the files in this workspace and summarize what is present. Do not modify anything.", no_files, verify_none),
    Task("read_document", "Read README.md and report its first heading. Do not modify the file.", setup_read_fixture, verify_none),
    Task("search_text", "Find TARGET_MARKER in notes.txt and report the exact value. Do not modify files.", setup_search_fixture, verify_none),
    Task("create_calculator", "Create calculator.py in the workspace as a basic calculator. Verify that it is a valid Python file.", no_files, verify_created_python("calculator.py")),
    Task("edit_existing_file", "Edit calculator.py by adding a subtract(a, b) function while preserving the existing style, then verify the file.", setup_edit_fixture, verify_edited_calculator),
    Task("run_existing_script", "Run run_me.py and report its output. Do not modify any files.", setup_run_fixture, verify_none),
    Task("create_package", "Create miniapp/__init__.py and miniapp/cli.py as a minimal importable Python package. Verify both files.", no_files, verify_multi_file),
    Task("repair_failing_test", "Run the tests, diagnose the failure, repair math_utils.py, and rerun the focused test until it passes.", setup_test_fixture, verify_test_repair),
    Task("rename_named_file", "Rename rename_me.txt to renamed.txt and verify the old name is gone.", setup_rename_fixture, verify_rename),
    Task("delete_named_file", "Delete delete_me.txt because this explicit task requests its deletion, then verify it is gone.", setup_delete_fixture, verify_delete),
)


def case_child(args: argparse.Namespace) -> int:
    require_live_variants_allowed([args.variant])
    process_results: list[dict[str, Any]] = []
    propagate_owner(args.owner_token, process_results)
    module, _ = smoke._load_module(args.variant)
    workspace = Path(args.workspace).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    task = next(item for item in TASKS if item.task_id == args.task)
    task.setup(workspace)
    before = smoke._workspace_manifest(workspace)
    recorder = calculator_smoke.Recorder(output / "requests.jsonl")
    result: dict[str, Any] = {"variant": args.variant, "task_id": task.task_id, "prompt": task.prompt, "issues": [], "process_results": process_results}
    gui = None
    try:
        config, base = smoke._build_wrapped_config(module, workspace)
        calculator_smoke.install_request_capture(base, recorder)
        original_call = base.MCPHost.call_tool
        result["tool_results"] = []

        async def observed_call(host: Any, name: str, arguments: Any) -> Any:
            value = await original_call(host, name, arguments)
            receipt = {"name": name, "arguments": arguments, "result": base.serialize_mcp_result(value)}
            result["tool_results"].append(receipt)
            recorder.emit("tool_result", **receipt)
            return value

        base.MCPHost.call_tool = observed_call
        gui = base.QubitzGUI(config)
        smoke._attach_event_capture(gui)
        smoke._install_messagebox_capture(gui)
        smoke._present_gui(gui)
        smoke._drive(gui.root, lambda: False, 0.5)
        result["task_started_at"] = time.time()
        result["task"] = smoke._send_prompt(gui, task.prompt, args.timeout)
        after = smoke._workspace_manifest(workspace)
        changes = smoke._manifest_changes(before, after)
        result["workspace_changes"] = changes
        passed, issues = task.verify(workspace, changes)
        result["passed"] = bool(passed and not gui.busy)
        result["issues"].extend(issues)
        result["issues"].extend(
            verify_response(
                task.task_id,
                result,
                config.model_name,
                getattr(module, "DEFAULT_GGUF_MODEL_LABEL", ""),
            )
        )
        if gui.busy:
            result["issues"].append("worker_still_busy")
        if result["task"].get("timed_out"):
            result["issues"].append("task_timeout")
        if result["task"].get("errors"):
            result["issues"].append("gui_error_output")
        if "[Completion status: partial]" in result["task"].get("assistant", ""):
            result["issues"].append("partial_completion_report")
    except Exception:  # noqa: BLE001 - child results must record all runtime failures.
        result["passed"] = False
        result["issues"].append("child_exception")
        result["exception"] = traceback.format_exc()
    finally:
        if gui is not None:
            gui_was_busy = bool(gui.busy)
            result["all_gui_events"] = getattr(gui, "_smoke_events", [])
            result["messageboxes"] = getattr(gui, "_smoke_messageboxes", [])
            for _timestamp, role, message in result["all_gui_events"]:
                prefix = "llama.cpp server log: "
                if role == "status" and message.startswith(prefix):
                    source = Path(message.removeprefix(prefix).strip())
                    if source.is_file() and source.resolve().is_relative_to(ROOT / ".cache"):
                        shutil.copy2(source, output / "llama-server.log")
            try:
                if not gui.busy:
                    gui.agent.reset_runtime()
                gui.root.destroy()
                result["gui_diagnostics"] = reconcile_completed_gui_diagnostics(
                    smoke._collect_gui_diagnostics(gui),
                    result.get("task", {}),
                    gui_was_busy,
                )
                result["issues"].extend(result["gui_diagnostics"].get("behavior_issues", []))
            except Exception:  # noqa: BLE001 - shutdown diagnostics must not hide the case result.
                result["issues"].append("gui_shutdown_exception")
        result["passed"] = bool(result.get("passed") and not result["issues"])
        result["observation_limits"] = [
            "Tk events, geometry and stall diagnostics captured; rendering was not independently visually inspected.",
            "Browser devtools are unavailable in this evaluator.",
            "Calculator creation checks syntax; arbitrary calculator interfaces are not functionally exercised.",
        ]
        save_json(output / "result.json", result)
    return 0 if result.get("passed") else 1


def reconcile_completed_gui_diagnostics(
    diagnostics: dict[str, Any],
    task: dict[str, Any],
    worker_busy: bool,
) -> dict[str, Any]:
    """Treat a terminal answer after a shutdown-race stall as recovered, not failed."""
    reconciled = dict(diagnostics)
    issues = list(reconciled.get("behavior_issues", []))
    stall_count = sum(issue == "gui_heartbeat_stalled" for issue in issues)
    if (
        stall_count
        and not worker_busy
        and task.get("completed")
        and str(task.get("assistant") or "").strip()
    ):
        reconciled["behavior_issues"] = [issue for issue in issues if issue != "gui_heartbeat_stalled"]
        warnings = list(reconciled.get("warnings", []))
        warning = "gui_heartbeat_stall_recovered_by_terminal_answer"
        if warning not in warnings:
            warnings.append(warning)
        reconciled["warnings"] = warnings
        unresolved = max(stall_count, int(reconciled.get("unresolved_stall_count") or 0))
        reconciled["recovered_stall_count"] = int(reconciled.get("recovered_stall_count") or 0) + unresolved
        reconciled["unresolved_stall_count"] = 0
    return reconciled


def verify_response(
    task_id: str,
    result: dict[str, Any],
    model: str,
    *configured_identity_labels: str,
) -> list[str]:
    task = result.get("task", {})
    answer = re.sub(r"\s*\(Elapsed time:.*?\)\s*$", "", str(task.get("assistant", "")), flags=re.DOTALL).strip()
    if not task.get("completed") or not answer:
        return ["no_completed_answer"]
    normalized = re.sub(r"[^a-z0-9]+", "", answer.casefold())
    if task_id == "simple_question" and normalized != "bucharest":
        return ["incorrect_answer_or_requested_format"]
    if task_id == "identity_question":
        identity_candidates = {
            re.sub(r"[^a-z0-9]+", "", model.rsplit("/", 1)[-1].casefold().removesuffix("-gguf")),
            *(
                re.sub(r"[^a-z0-9]+", "", label.casefold())
                for label in configured_identity_labels
                if str(label).strip()
            ),
        }
        identity_candidates.discard("")
        if not any(identity in normalized for identity in identity_candidates):
            return ["configured_model_identity_missing"]
    if task_id == "read_document" and "fixture project" not in answer.casefold():
        return ["requested_heading_missing"]
    if task_id == "search_text" and not re.search(r"\bverified\b", answer):
        return ["requested_marker_value_missing"]
    if task_id == "workspace_listing" and not re.search(r"\bempty\b|\bno (?:visible |project )?files\b|\b0 (?:files|entries)\b", answer, re.IGNORECASE):
        return ["empty_workspace_not_reported"]
    if task_id == "run_existing_script":
        receipts = result.get("tool_results", [])
        evidence = [item for item in receipts if item["name"] == "run_existing_entrypoint"
                    and "run_me.py" in json.dumps(item["arguments"])
                    and "RUN_OK" in json.dumps(item["result"])
                    and not item["result"].get("is_error")]
        process_evidence = [item for item in result.get("process_results", [])
                            if isinstance(item.get("command"), list)
                            and any(Path(str(arg)).name == "run_me.py" for arg in item["command"])
                            and item.get("exit_code") == 0
                            and "RUN_OK" in str(item.get("stdout"))]
        if "RUN_OK" not in answer or not (evidence or process_evidence):
            return ["script_execution_not_verified"]
    return []


def wsl_path(path: Path) -> str:
    if path.as_posix().startswith("/mnt/"):
        return path.as_posix()
    return "/mnt/" + path.drive[0].lower() + path.as_posix().split(":", 1)[1]


def protected_source_manifest(output: Path) -> dict[str, dict[str, Any]]:
    manifest = {}
    for current, directories, files in os.walk(ROOT, followlinks=False):
        directory = Path(current)
        directories[:] = [name for name in directories
                          if name not in PROTECTED_RUNTIME_DIRS | smoke._MANIFEST_EXCLUDED_DIRS
                          and not smoke._is_environment_dir(Path(name))
                          and not (directory == ROOT and name == "backup")
                          and not (directory == ROOT and name == "conversations")
                          and directory.name != "evaluation"
                          and (directory / name).resolve() != output.resolve()]
        for name in files:
            path = directory / name
            relative = path.relative_to(ROOT).as_posix()
            if relative in {"changelog.txt", "files_list.txt"} or (
                directory == ROOT / "evaluation" and path.suffix != ".py"
            ):
                continue
            stat = path.stat()
            digest = ""
            if stat.st_size <= 32 * 1024 * 1024:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
            manifest[relative] = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "sha256": digest}
    return manifest


def manifest_changes(
    expected: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> list[str]:
    return sorted(
        path
        for path in set(expected) | set(current)
        if expected.get(path) != current.get(path)
    )


def invalidate_campaign(
    summary_path: Path,
    summary: dict[str, Any],
    reason: str,
    expected: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> None:
    summary["status"] = "invalidated"
    summary["finished"] = datetime.now(timezone.utc).isoformat()
    safety = summary.setdefault("safety", {})
    safety["source_mutation_detected"] = True
    safety["invalidation_reason"] = reason
    safety["changed_paths"] = manifest_changes(expected, current)
    save_json(summary_path, summary)


def remove_case_workspace(workspace: Path, task_root: Path) -> None:
    resolved = workspace.resolve()
    allowed_root = task_root.resolve()
    if resolved.parent != allowed_root or not resolved.name.startswith("qubitz_complex_"):
        raise RuntimeError(f"Refusing cleanup of non-campaign workspace: {resolved}")
    shutil.rmtree(resolved, ignore_errors=False)


@contextmanager
def campaign_lock():
    lock_path = ROOT / ".cache" / "complex-smoke.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def cleanup_case_processes(token: str, run_dir: Path) -> None:
    command = ["wsl.exe", "-d", "Ubuntu", "--exec", wsl_path(ROOT / ".venv/bin/python"),
               wsl_path(ROOT / "evaluation/complex_smoke_runtime.py"), "--cleanup-owned", token]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    save_json(run_dir / "process-cleanup.json", {"exit_code": result.returncode,
                                               "stdout": result.stdout, "stderr": result.stderr})
    if result.returncode:
        raise RuntimeError("Owned process cleanup failed: " + result.stdout + result.stderr)


def coordinator(args: argparse.Namespace) -> int:
    with campaign_lock():
        return _coordinator(args)


def _coordinator(args: argparse.Namespace) -> int:
    variants = args.variants or [path.name for path in variant_paths(ROOT) if live_variant_allowed(path.name)]
    require_live_variants_allowed(variants)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    task_root = args.task_root.resolve()
    task_root.mkdir(parents=True, exist_ok=True)
    calculator_smoke.require_idle()
    summary_path = output / "summary.json"
    existing = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else None
    task_ids = [task.task_id for task in TASKS]
    if existing is not None:
        if existing.get("variants") != variants or existing.get("tasks") != task_ids:
            raise RuntimeError("Existing campaign checkpoint does not match the requested variants/tasks")
        summary = existing
        source_before = summary["safety"]["source_snapshot"]
        current_source = protected_source_manifest(output)
        if summary.get("verifier_version") != 2 or current_source != source_before:
            invalidate_campaign(
                summary_path,
                summary,
                "Source or verifier changed before campaign resume.",
                source_before,
                current_source,
            )
            raise RuntimeError("Source or verifier changed; use a new output directory to preserve measurement provenance.")
        summary["status"] = "running"
        summary.pop("finished", None)
    else:
        source_before = protected_source_manifest(output)
        summary = {
            "status": "running",
            "started": datetime.now(timezone.utc).isoformat(),
            "tasks": task_ids,
            "variants": variants,
            "runs": [],
            "verifier_version": 2,
            "safety": {"source_snapshot": source_before, "source_mutation_detected": False},
        }
    completed_keys = {(row.get("variant"), row.get("task_id")) for row in summary.get("runs", [])
                      if "coordinator_interrupted" not in row.get("issues", [])}
    for variant in variants:
        for task in TASKS:
            if (variant, task.task_id) in completed_keys:
                continue
            current_source = protected_source_manifest(output)
            if current_source != source_before:
                invalidate_campaign(
                    summary_path,
                    summary,
                    "Source changed before case launch.",
                    source_before,
                    current_source,
                )
                raise RuntimeError("Source changed before case launch; campaign stopped.")
            calculator_smoke.require_idle()
            attempt = 1 + sum(row.get("variant") == variant and row.get("task_id") == task.task_id for row in summary["runs"])
            run_dir = output / variant.removesuffix(".py") / task.task_id / f"attempt_{attempt:03}"
            run_dir.mkdir(parents=True, exist_ok=True)
            workspace = Path(tempfile.mkdtemp(prefix="qubitz_complex_", dir=task_root))
            task.setup(workspace)
            initial_servers = calculator_smoke.model_processes()
            pidfile = run_dir / "wsl-process-group.pid"
            owner_token = uuid.uuid4().hex
            child_command = shlex.join([
                wsl_path(ROOT / ".venv" / "bin" / "python"),
                "-u",
                wsl_path(Path(__file__).resolve()),
                "--child",
                "--variant", variant,
                "--task", task.task_id,
                "--workspace", wsl_path(workspace),
                "--output", wsl_path(run_dir),
                "--timeout", str(args.timeout),
                "--owner-token", owner_token,
            ])
            inner = (
                f"echo $$ > {shlex.quote(wsl_path(pidfile))}; "
                f"cd {shlex.quote(wsl_path(ROOT))} && exec env "
                f"{OWNER_ENV}={owner_token} QUBITZ_SKIP_WSLG_VISIBILITY_PROBE=1 QUBITZ_SMOKE_ALLOW_WORKSPACE_MUTATION=1 {child_command}"
            )
            command = ["wsl.exe", "-d", "Ubuntu", "bash", "-lc", "exec setsid --wait bash -lc " + shlex.quote(inner)]
            started = time.monotonic()
            completed = None
            cleanup_error = None
            interrupted = False
            execution_error = None
            stdout_path, stderr_path = run_dir / "stdout.log", run_dir / "stderr.log"
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    completed = subprocess.run(command, cwd=ROOT, stdout=stdout, stderr=stderr,
                                               timeout=args.timeout + 300, check=False)
            except subprocess.TimeoutExpired as exc:
                execution_error = f"child_timeout: {exc}"
            except KeyboardInterrupt:
                interrupted = True
                execution_error = "coordinator_interrupted"
            except OSError as exc:
                execution_error = f"child_launch_error: {exc}"
            finally:
                try:
                    cleanup_case_processes(owner_token, run_dir)
                except Exception as exc:  # noqa: BLE001 - exact child cleanup is recorded.
                    cleanup_error = cleanup_error or str(exc)
                try:
                    current_servers = calculator_smoke.model_processes()
                    initial_ids = {calculator_smoke.server_identity(row) for row in initial_servers}
                    owned = [row for row in current_servers if calculator_smoke.server_identity(row) not in initial_ids]
                    calculator_smoke.wait_for_owned_cleanup(owned)
                except Exception as exc:  # noqa: BLE001 - cleanup failure is recorded and fails the case.
                    cleanup_error = str(exc)
                try:
                    if not cleanup_error:
                        remove_case_workspace(workspace, task_root)
                except Exception as exc:  # noqa: BLE001 - cleanup failure is recorded and fails the case.
                    cleanup_error = cleanup_error or str(exc)
            result_path = run_dir / "result.json"
            row = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {
                "variant": variant, "task_id": task.task_id, "passed": False, "issues": ["missing_child_result"]
            }
            if completed is None:
                row.update(exit_code=None)
                row.setdefault("issues", []).append(execution_error or "child_not_completed")
            else:
                row.update(exit_code=completed.returncode)
            row.update(stdout_path=str(stdout_path), stderr_path=str(stderr_path),
                       owner_token=owner_token, workspace=str(workspace))
            row["wall_seconds"] = round(time.monotonic() - started, 3)
            if completed is None or completed.returncode != 0:
                row["passed"] = False
            if cleanup_error:
                row["passed"] = False
                row.setdefault("issues", []).append("cleanup_error")
                row["cleanup_error"] = cleanup_error
            source_after = protected_source_manifest(output)
            if source_after != source_before:
                row.setdefault("issues", []).append("qubitz_source_tree_changed")
                summary["runs"].append(row)
                invalidate_campaign(
                    summary_path,
                    summary,
                    "Source changed during a campaign case.",
                    source_before,
                    source_after,
                )
                raise RuntimeError("Qubitz source tree changed; campaign stopped immediately")
            summary["runs"].append(row)
            save_json(output / "summary.json", summary)
            if not row.get("passed") or row.get("issues"):
                print(json.dumps({"variant": variant, "task": task.task_id, "passed": row.get("passed"), "issues": row.get("issues")}), flush=True)
            if cleanup_error or interrupted:
                summary["status"] = "cleanup_blocked" if cleanup_error else "paused"
                save_json(summary_path, summary)
                return 2
    summary["status"] = "complete"
    summary["finished"] = datetime.now(timezone.utc).isoformat()
    save_json(output / "summary.json", summary)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--variant", help="Variant filename for child mode.")
    parser.add_argument("--task", help="Task id for child mode.")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "results" / ("complex_smoke_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")))
    default_task_root = Path(r"D:\AI_Research\.=_") if os.name == "nt" else Path("/mnt/d/AI_Research/.=_")
    parser.add_argument("--task-root", type=Path, default=default_task_root)
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--owner-token", default="")
    args = parser.parse_args()
    if args.child:
        if not args.variant or not args.task or args.workspace is None:
            parser.error("child mode requires --variant, --task, and --workspace")
        return case_child(args)
    return coordinator(args)


if __name__ == "__main__":
    raise SystemExit(main())
