"""Visible-GUI creation smoke, with artifact verification and no product shortcuts.

Run the coordinator on Windows. The WSL child uses the ordinary GUI send path.
Only calculator.py created by this campaign may be removed from the workspace.
Use --workspace and --keep-artifact for isolated runs with retained artifacts.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evaluation import complex_smoke_runtime as ownership
from qubitz_live_eval_policy import require_live_variants_allowed
from runtime.paths import variant_paths

WORKSPACE = (Path(r"D:\AI_Research\Cyber_News_MCP") if os.name == "nt"
             else Path("/mnt/d/AI_Research/Cyber_News_MCP"))
PROMPT = "create a Python script named calculator.py that would be a basic calculator in the workspace directory"
LIMITATIONS = [
    "Tk window visibility, geometry, events and stall diagnostics are captured; visual rendering is not independently inspected.",
    "Browser devtools are not captured; this task does not require a browser.",
    "Generated code is parsed but not executed; file creation is verified, calculator arithmetic quality is not functionally proven.",
    "Memory and conversation storage are private to the run; older shared-memory smokes are not controlled comparisons.",
]


def save_json(path: Path, data: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".pending")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
    temporary.replace(path)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def server_log_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    before = path.stat()
    checksum = digest(path)
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise RuntimeError("Server log changed while recording its baseline")
    return {"bytes": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": checksum}


def capture_server_log(
    source: Path,
    output: Path,
    baseline: dict[str, Any] | None,
    started_ns: int,
    owned_servers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Capture only new bytes, retaining attribution separately from log content."""
    current = server_log_snapshot(source)
    receipt = {"source": str(source), "baseline": baseline, "current": current,
               "observed_servers": owned_servers}
    if current is None:
        return {**receipt, "status": "not_created"}
    if baseline is not None and current["sha256"] == baseline["sha256"]:
        return {**receipt, "status": "unchanged_not_captured"}
    if current["mtime_ns"] < started_ns:
        return {**receipt, "status": "predates_run_not_captured"}
    offset = 0
    with source.open("rb") as stream:
        if baseline is not None and current["bytes"] > baseline["bytes"]:
            prefix = hashlib.sha256()
            remaining = int(baseline["bytes"])
            while remaining:
                chunk = stream.read(min(remaining, 1024 * 1024))
                if not chunk:
                    raise RuntimeError("Server log shrank during capture")
                prefix.update(chunk)
                remaining -= len(chunk)
            if prefix.hexdigest() == baseline["sha256"]:
                offset = int(baseline["bytes"])
        name = "llama-server.log" if owned_servers else "llama-server.unattributed.log"
        destination = output / name
        stream.seek(offset)
        with destination.open("xb") as captured:
            shutil.copyfileobj(stream, captured)
    if server_log_snapshot(source) != current:
        raise RuntimeError("Server log changed during capture; captured evidence is unverified")
    return {**receipt, "status": "captured" if owned_servers else "captured_unattributed",
            "artifact": name, "offset": offset, "bytes": current["bytes"] - offset,
            "sha256": digest(destination)}


def fingerprint() -> dict[str, str]:
    from runtime import runtime_dependency_paths

    files = variant_paths(ROOT)
    files += runtime_dependency_paths(ROOT)
    files += [ROOT / name for name in ("HARNESS.enc", "tmp_smoke_variant.py",
                                      "qubitz_variant_profiles.json")]
    files.append(Path(__file__).resolve())
    files.append(ROOT / "evaluation" / "complex_smoke_runtime.py")
    return {str(path.relative_to(ROOT)): digest(path) for path in sorted(files)}


class Recorder:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def emit(self, kind: str, **data: Any) -> None:
        with self.lock, self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"time": time.time(), "kind": kind, **data}, default=str) + "\n")


def install_request_capture(base: Any, recorder: Recorder) -> None:
    """Observe requests without altering tools, messages, decoding or responses."""
    import httpx

    original_fallback = base.LlamaCppClient._call_with_fallback

    def fallback(client: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.emit("backend_call", args=args, kwargs=kwargs)
        try:
            result = original_fallback(client, *args, **kwargs)
        except Exception as exc:
            recorder.emit("backend_exception", error=repr(exc))
            raise
        recorder.emit("backend_result", result=result)
        return result

    base.LlamaCppClient._call_with_fallback = fallback
    original_send = httpx.Client.send
    original_lines = httpx.Response.iter_lines
    original_close = httpx.Response.close
    original_read = httpx.Response.read

    def send(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        capture = request.url.host in {"127.0.0.1", "localhost"} and "chat/completions" in request.url.path
        capture_id = uuid.uuid4().hex if capture else ""
        structured_id = str(request.headers.get("X-Qubitz-Request-Id", ""))
        if capture and len(structured_id) == 32 and all(char in "0123456789abcdef" for char in structured_id):
            capture_id = structured_id
        capture_turn = getattr(recorder, "turn", None)
        if capture:
            try:
                payload = json.loads(request.content)
            except (ValueError, httpx.RequestNotRead):
                payload = "request body unavailable"
            recorder.emit("http_request", request_id=capture_id, url=str(request.url), payload=payload,
                          structured_request_identity=bool(structured_id and capture_id == structured_id))
        try:
            response = original_send(client, request, *args, **kwargs)
        except Exception as exc:
            if capture:
                recorder.emit("http_transport_error", request_id=capture_id, turn=capture_turn,
                              exception_type=type(exc).__name__)
            raise
        if capture:
            response.extensions["qubitz_smoke_capture"] = True
            response.extensions["qubitz_smoke_request_id"] = capture_id
            response.extensions["qubitz_smoke_turn"] = capture_turn
            response.extensions["qubitz_smoke_payload_stream"] = bool(isinstance(payload, dict) and payload.get("stream"))
            recorder.emit("http_status", request_id=capture_id, turn=capture_turn, status=response.status_code)
            if not kwargs.get("stream", False):
                response.extensions["qubitz_smoke_body_recorded"] = True
                recorder.emit("http_body", request_id=capture_id, turn=response.extensions.get("qubitz_smoke_turn"),
                              body=response.text)
        return response

    def read(response: Any) -> bytes:
        data = original_read(response)
        if (response.extensions.get("qubitz_smoke_capture")
                and not response.extensions.get("qubitz_smoke_payload_stream")
                and not response.extensions.get("qubitz_smoke_body_recorded")):
            response.extensions["qubitz_smoke_body_recorded"] = True
            recorder.emit("http_body", request_id=response.extensions.get("qubitz_smoke_request_id"),
                          turn=response.extensions.get("qubitz_smoke_turn"), body=response.text)
        return data

    def lines(response: Any):
        try:
            for line in original_lines(response):
                if response.extensions.get("qubitz_smoke_capture"):
                    recorder.emit("http_stream_line", request_id=response.extensions.get("qubitz_smoke_request_id"),
                                  turn=response.extensions.get("qubitz_smoke_turn"), line=line)
                    if line.startswith("data:") and line[5:].strip() not in {"", "[DONE]"}:
                        try:
                            event = json.loads(line[5:].strip())
                            if any(choice.get("finish_reason") for choice in event.get("choices", [])):
                                response.extensions["qubitz_smoke_terminal_seen"] = True
                        except (ValueError, AttributeError, TypeError):
                            pass  # The unchanged runtime parser classifies malformed generation.
                yield line
        except Exception as exc:
            if response.extensions.get("qubitz_smoke_capture"):
                kind = "http_terminal_metadata_error" if response.extensions.get("qubitz_smoke_terminal_seen") else "http_stream_error"
                recorder.emit(kind, request_id=response.extensions.get("qubitz_smoke_request_id"),
                              turn=response.extensions.get("qubitz_smoke_turn"), exception_type=type(exc).__name__)
            raise

    def close(response: Any) -> None:
        try:
            original_close(response)
        finally:
            if response.extensions.get("qubitz_smoke_capture") and not response.extensions.get("qubitz_smoke_close_recorded"):
                response.extensions["qubitz_smoke_close_recorded"] = True
                recorder.emit("http_stream_close", request_id=response.extensions.get("qubitz_smoke_request_id"),
                              turn=response.extensions.get("qubitz_smoke_turn"), closed=bool(response.is_closed))

    httpx.Client.send = send
    httpx.Response.iter_lines = lines
    httpx.Response.close = close
    httpx.Response.read = read


def shutdown_runtime_responsively(gui: Any, timeout: float = 90.0) -> dict[str, Any]:
    """Stop the backend off the Tk thread while continuing to service Tk events."""
    finished = threading.Event()
    failures: list[BaseException] = []

    def shutdown() -> None:
        try:
            gui.agent.reset_runtime()
        except BaseException as exc:  # noqa: BLE001 - preserve worker failures in the coordinator receipt
            failures.append(exc)
        finally:
            finished.set()

    worker = threading.Thread(target=shutdown, name="qubitz-smoke-runtime-shutdown")
    started = time.monotonic()
    worker.start()
    deadline = started + max(0.0, timeout)
    pump_error = ""
    while not finished.is_set() and time.monotonic() < deadline:
        try:
            gui.root.update()
        except gui.tk.TclError as exc:  # the window may already have been closed by the user
            pump_error = repr(exc)
            finished.wait(0.05)
            continue
        finished.wait(0.02)
    worker.join(timeout=0)
    elapsed = round(time.monotonic() - started, 3)
    if not finished.is_set():
        return {"completed": False, "timed_out": True, "elapsed_seconds": elapsed,
                "gui_pump_error": pump_error or None}
    if failures:
        raise RuntimeError(f"Runtime shutdown failed: {failures[0]!r}") from failures[0]
    return {"completed": True, "timed_out": False, "elapsed_seconds": elapsed,
            "gui_pump_error": pump_error or None}


def inspect_artifact(destination: Path, output: Path) -> dict[str, Any]:
    if not os.path.lexists(destination):
        return {"created": False}
    if destination.is_symlink() or not destination.is_file():
        return {"created": False, "error": "target_is_not_a_regular_file"}
    data = destination.read_bytes()
    result: dict[str, Any] = {"created": True, "bytes": len(data),
                              "sha256": hashlib.sha256(data).hexdigest()}
    (output / "calculator.artifact.py").write_bytes(data)
    try:
        ast.parse(data, filename="calculator.py")
        result["syntax_valid"] = True
    except (SyntaxError, ValueError) as exc:
        result.update(syntax_valid=False, syntax_error=str(exc))
    return result


def child(args: argparse.Namespace) -> int:
    require_live_variants_allowed([args.child])
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    identity = ownership.record_worker_identity(output, args.owner_token)
    ownership.wait_for_worker_admission(output, args.owner_token)
    ownership.propagate_owner(args.owner_token)
    import tmp_smoke_variant as smoke

    destination = WORKSPACE / "calculator.py"
    if os.path.lexists(destination):
        raise RuntimeError("Refusing to overwrite pre-existing calculator.py")
    before = smoke._workspace_manifest(WORKSPACE)
    save_json(output / "workspace_before.json", before)
    recorder = Recorder(output / "requests.jsonl")
    result: dict[str, Any] = {"variant": args.child, "prompt": PROMPT,
                              "limitations": LIMITATIONS, "issues": [], "worker_identity": identity,
                              "persistent_storage": "run_private_memory_and_transcripts"}
    gui = None
    try:
        module, _ = smoke._load_module(args.child)
        ownership.install_private_storage(module._APP.base, output)
        config, base = smoke._build_wrapped_config(module, ROOT)
        install_request_capture(base, recorder)
        gui = base.QubitzGUI(config)
        smoke._attach_event_capture(gui)
        smoke._install_messagebox_capture(gui)
        smoke._present_gui(gui)
        smoke._drive(gui.root, lambda: False, 0.5)
        result["gui"] = {"state": gui.root.state(), "mapped": bool(gui.root.winfo_ismapped()),
                          "geometry": gui.root.winfo_geometry()}
        result["workspace_switch"] = smoke._switch_workspace(gui, base, WORKSPACE)
        if Path(gui.config.workspace).resolve() != WORKSPACE.resolve():
            raise RuntimeError("GUI workspace switch failed; prompt not submitted")
        result["task_started_at"] = time.time()
        result["task"] = smoke._send_prompt(gui, PROMPT, args.timeout)
        if gui.busy:
            smoke._drive(gui.root, lambda: not gui.busy, 30.0)
        result["worker_still_busy"] = bool(gui.busy)
        result["artifact"] = inspect_artifact(destination, output)
        result["workspace_changes"] = smoke._manifest_changes(before, smoke._workspace_manifest(WORKSPACE))
        unexpected = {kind: [name for name in names if name != "calculator.py"]
                      for kind, names in result["workspace_changes"].items()}
        result["unexpected_workspace_changes"] = unexpected
        if any(unexpected.values()):
            result["issues"].append("unexpected_workspace_mutation")
        task = result["task"]
        artifact = result["artifact"]
        result["passed"] = bool(task["completed"] and artifact.get("created")
                                and artifact.get("bytes", 0) > 0 and artifact.get("syntax_valid")
                                and not any(unexpected.values()) and not gui.busy)
        if not artifact.get("created"):
            result["issues"].append("requested_file_not_created")
        if task.get("timed_out"):
            result["issues"].append("task_timeout")
        if task.get("errors"):
            result["issues"].append("gui_error_output")
        if "[Completion status: partial]" in task.get("assistant", ""):
            result["issues"].append("partial_completion_report")
    except Exception:  # noqa: BLE001 - persist unexpected GUI/task failures as smoke evidence
        result.update(passed=False, exception=traceback.format_exc())
        result["issues"].append("child_exception")
        traceback.print_exc()
    finally:
        # Publish evidence before shutdown in case a worker or backend hangs.
        if gui is not None:
            result["all_gui_events"] = getattr(gui, "_smoke_events", [])
            result["messageboxes"] = getattr(gui, "_smoke_messageboxes", [])
        save_json(output / "result.json", result)
        if gui is not None:
            try:
                if not gui.busy:
                    result["runtime_shutdown"] = shutdown_runtime_responsively(gui)
                    if result["runtime_shutdown"].get("timed_out"):
                        result["issues"].append("runtime_shutdown_timeout")
                else:
                    result["runtime_shutdown"] = {"completed": False, "skipped": "agent_busy"}
                    result["issues"].append("runtime_shutdown_skipped_while_busy")
                try:
                    gui.root.destroy()
                except gui.tk.TclError as exc:
                    result["root_destroy_error"] = repr(exc)
                result["gui_diagnostics"] = smoke._collect_gui_diagnostics(gui)
                result["issues"].extend(result["gui_diagnostics"].get("behavior_issues", []))
            except Exception:  # noqa: BLE001 - teardown failures must still be captured
                result["shutdown_exception"] = traceback.format_exc()
                result["issues"].append("gui_shutdown_exception")
        if result.get("issues"):
            result["passed"] = False
        save_json(output / "result.json", result)
    return 0 if result.get("passed") else 1


def wsl_path(path: Path) -> str:
    return "/mnt/" + path.drive[0].lower() + path.as_posix().split(":", 1)[1]


def powershell(script: str) -> str:
    completed = subprocess.run(["powershell.exe", "-NoProfile", "-Command", script],
                               capture_output=True, text=True, timeout=30, check=True)
    return completed.stdout.strip()


class IncompleteProcessIdentityError(RuntimeError):
    """Windows reported a process whose identity cannot yet be verified."""


def model_process_snapshot() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return verified identities separately from transient/incomplete CIM rows."""
    raw = powershell(
        "$ErrorActionPreference='Stop'; $rows=@(Get-CimInstance Win32_Process "
        "-Filter \"Name='llama-server.exe'\" | ForEach-Object { "
        "[pscustomobject]@{ProcessId=$_.ProcessId; "
        "CreationDate=$(if ($_.CreationDate) {$_.CreationDate.ToUniversalTime().Ticks.ToString()} else {''}); "
        "ExecutablePath=$_.ExecutablePath; CommandLine=$_.CommandLine} }); "
        "ConvertTo-Json -InputObject $rows -Compress"
    )
    value = json.loads(raw or "[]")
    rows = [value] if isinstance(value, dict) else value
    if not isinstance(rows, list):
        raise RuntimeError("Unable to verify model-server process inventory")  # noqa: TRY004 - external process protocol failure
    verified: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    for row in rows:
        try:
            server_identity(row)
        except IncompleteProcessIdentityError as exc:
            incomplete.append({**row, "identity_error": str(exc)})
        else:
            verified.append(row)
    return verified, incomplete


def model_processes() -> list[dict[str, Any]]:
    verified, incomplete = model_process_snapshot()
    if incomplete:
        raise IncompleteProcessIdentityError("Unowned model server: incomplete process identity")
    return verified


def server_identity(row: dict[str, Any]) -> tuple[int, str, str]:
    """Never substitute executable/model similarity for a captured process identity."""
    if not isinstance(row, dict):
        raise RuntimeError("Unowned model server: invalid process metadata")  # noqa: TRY004 - external process protocol failure
    pid = row.get("ProcessId")
    created = str(row.get("CreationDate") or "")
    executable = str(row.get("ExecutablePath") or "")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not created.isdigit() or not executable:
        raise IncompleteProcessIdentityError("Unowned model server: incomplete process identity")
    return pid, created, executable.casefold()


def _project_cache_path(path: str) -> bool:
    """Accept the Windows path returned by CIM and the WSL path used by tests."""
    raw = str(path).replace("\\", "/").rstrip("/").casefold()
    root = ROOT.resolve().as_posix().rstrip("/").casefold()
    if raw == root or raw.startswith(root + "/"):
        return raw == root + "/.cache" or raw.startswith(root + "/.cache/")
    if root.startswith("/mnt/") and len(root) > 6 and root[5].isalpha():
        windows_root = root[5] + ":" + root[6:]
        return raw == windows_root + "/.cache" or raw.startswith(windows_root + "/.cache/")
    return False


def stop_verified_server(process: dict[str, Any]) -> None:
    pid, created, _ = server_identity(process)
    executable = str(process.get("ExecutablePath") or "")
    if not _project_cache_path(executable):
        raise RuntimeError("Refusing cleanup of non-project model server")
    current = next((p for p in model_processes() if int(p["ProcessId"]) == pid), None)
    if current is None:
        return
    if server_identity(current) != server_identity(process):
        raise RuntimeError("Model-server PID identity changed; refusing cleanup")
    escaped_executable = executable.replace("'", "''")
    powershell(
        f"$ErrorActionPreference='Stop'; $p=Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
        "if ($p) { "
        f"if ($p.Name -ne 'llama-server.exe' -or $p.ExecutablePath -ne '{escaped_executable}' "
        f"-or $p.CreationDate.ToUniversalTime().Ticks.ToString() -ne '{created}') "
        "{ throw 'Model-server PID identity changed before termination' }; "
        "$result=Invoke-CimMethod -InputObject $p -MethodName Terminate; "
        "if ($result.ReturnValue -ne 0) { throw ('Model-server termination failed: ' + $result.ReturnValue) } }"
    )


def require_idle() -> None:
    from evaluation.process_inventory import is_qubitz_worker

    active = powershell("@((Get-CimInstance Win32_Process | Where-Object { "
                        "$_.Name -match '^python(w)?[.]exe$' }) "
                        "| Select-Object ProcessId,ParentProcessId,CreationDate,ExecutablePath,CommandLine) "
                        "| ConvertTo-Json -Compress")
    current_pid = os.getpid()
    active_value = json.loads(active or "[]")
    active_rows = [active_value] if isinstance(active_value, dict) else active_value
    incomplete = [row for row in active_rows if int(row.get("ProcessId", -1)) != current_pid
                  and not all(row.get(key) for key in ("ProcessId", "CreationDate", "ExecutablePath", "CommandLine"))]
    if incomplete:
        raise IncompleteProcessIdentityError("Cannot classify native Python process identity: " + json.dumps(incomplete, default=str))
    active_processes = [
        process for process in active_rows
        if int(process.get("ProcessId", -1)) != current_pid
        and is_qubitz_worker(str(process.get("CommandLine") or ""), windows=True)
    ]
    if active_processes:
        raise RuntimeError("A Windows Qubitz worker is active: " + json.dumps(active_processes, default=str))
    probe = subprocess.run(["wsl.exe", "--exec", "ps", "-eo", "pid=,args="],
                           capture_output=True, text=True, timeout=30, check=False)
    blockers = [line.strip() for line in probe.stdout.splitlines()
                if len(line.split(None, 1)) == 2 and is_qubitz_worker(line.split(None, 1)[1])]
    if blockers:
        raise RuntimeError("Another Qubitz GUI or evaluation worker is active: " + json.dumps(blockers))
    if probe.returncode != 0:
        raise RuntimeError("Unable to verify WSL process state: " + probe.stderr)


def port_free() -> bool:
    with socket.socket() as sock:
        sock.settimeout(1)
        return sock.connect_ex(("127.0.0.1", 8001)) != 0


def wait_for_owned_cleanup(owned: list[dict[str, Any]], timeout: float = 45.0) -> dict[str, Any]:
    """Require a quiet interval, not a single transient empty port/process snapshot."""
    owned_ids = {server_identity(row) for row in owned}
    deadline = time.monotonic() + timeout
    quiet_since = None
    metadata_retries = 0
    while time.monotonic() < deadline:
        try:
            servers = model_processes()
            for server in servers:
                if server_identity(server) not in owned_ids:
                    raise RuntimeError("Unowned model server; cleanup stopped without terminating it")
                # The application owns shutdown. Observation alone never authorizes a signal.
            if not servers and port_free():
                if quiet_since is None:
                    quiet_since = time.monotonic()
                if time.monotonic() - quiet_since >= 4.0:
                    require_idle()
                    if not model_processes() and port_free():
                        return {"verified_idle": True, "metadata_retries": metadata_retries}
                    quiet_since = None
            else:
                quiet_since = None
        except IncompleteProcessIdentityError:
            # An incomplete sample never establishes ownership or an idle interval.
            metadata_retries += 1
            quiet_since = None
        time.sleep(0.5)
    raise RuntimeError(f"Owned model cleanup did not settle after {metadata_retries} incomplete metadata sample(s); next variant was not launched")


def cutoff_activity(path: Path, cutoff_at: float) -> str:
    """Report transport activity as evidence, not as proof of progress or a hang."""
    last_generation = None
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                when = float(event.get("time", 0))
                if when <= cutoff_at and event.get("kind") in {"http_stream_line", "backend_result", "http_body"}:
                    last_generation = when
    if last_generation is not None and cutoff_at - last_generation <= 30:
        return "recent_generation_output_at_cutoff_not_proven_hang"
    return "no_recent_generation_output_hang_not_established"


def clean_created_file(output: Path) -> dict[str, Any]:
    destination = WORKSPACE / "calculator.py"
    if not os.path.lexists(destination):
        return {"removed": False, "reason": "not_created"}
    if destination.is_symlink() or not destination.is_file() or destination.resolve().parent != WORKSPACE.resolve():
        raise RuntimeError("Unsafe calculator.py cleanup target; leaving it untouched")
    observed = inspect_artifact(destination, output)
    save_json(output / "artifact_before_cleanup.json", observed)
    if digest(destination) != observed["sha256"]:
        raise RuntimeError("calculator.py changed during cleanup; leaving it untouched")
    destination.unlink()
    return {"removed": True, "sha256": observed["sha256"]}


def all_gpu_snapshot() -> dict[str, Any]:
    command = ["nvidia-smi", "--query-gpu=index,uuid,temperature.gpu,power.draw,utilization.gpu,memory.used,memory.total",
               "--format=csv,noheader,nounits"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"GPU query exited {result.returncode}")
        gpus = []
        for line in result.stdout.splitlines():
            index, identity, temperature, power, utilization, used, total = (part.strip() for part in line.split(","))
            gpus.append({"index": int(index), "uuid": identity, "temperature_c": float(temperature),
                         "power_w": float(power), "utilization_percent": float(utilization),
                         "memory_used_mib": float(used), "memory_total_mib": float(total)})
        if not gpus or len({gpu["index"] for gpu in gpus}) != len(gpus) or len({gpu["uuid"] for gpu in gpus}) != len(gpus):
            raise RuntimeError("GPU query returned an empty or ambiguous inventory")
        return {"available": True, "gpus": gpus}
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "gpus": [], "error": str(exc)}


def wsl_worker_command(command: list[str], token: str) -> list[str]:
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("Invalid smoke ownership token")
    environment = ["env", "QUBITZ_SKIP_WSLG_VISIBILITY_PROBE=1", "QUBITZ_SMOKE_ALLOW_WORKSPACE_MUTATION=1",
                   f"{ownership.OWNER_ENV}={token}"]
    inner = f"cd {shlex.quote(wsl_path(ROOT))} && exec " + shlex.join([*environment, *command])
    return ["wsl.exe", "-d", "Ubuntu", "bash", "-lc", "exec setsid --wait bash -lc " + shlex.quote(inner)]


def stop_wsl_worker(run: Path, token: str) -> dict[str, Any]:
    command = ["wsl.exe", "-d", "Ubuntu", "--cd", wsl_path(ROOT),
               wsl_path(ROOT / ".venv/bin/python"), "-B", wsl_path(ROOT / "evaluation/complex_smoke_runtime.py"),
               "--stop-recorded-worker", wsl_path(run / "worker_identity.json"), "--owner-token", token,
               "--stop-output", wsl_path(run / "timeout_stop")]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        return {"command": command, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "exit_code": None, "error": str(exc), "status": "uncertain_do_not_retry"}


def persistent_inventory() -> dict[str, str]:
    paths = [path for name in (".memory", "conversations") for path in (ROOT / name).rglob("*")
             if path.is_file() or path.is_symlink()]
    return {str(path.relative_to(ROOT)): ("symlink:" + os.readlink(path) if path.is_symlink() else digest(path))
            for path in sorted(paths)}


def admit_wsl_worker(run: Path, token: str, process: Any, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Worker exited before admission; model loading was not authorized")
        try:
            ownership.admit_recorded_worker(run, token)
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.05)
            continue
        return
    raise RuntimeError("Worker identity receipt unavailable; model loading was not authorized")


def supervise_worker(process: Any, run: Path, token: str, started: float, timeout: float) -> dict[str, Any]:
    """Keep the original waiter and never repeat an uncertain stop attempt."""
    telemetry = Recorder(run / "telemetry.jsonl")
    state: dict[str, Any] = {"owned_servers": [], "gpu_observation_errors": [], "timed_out": False}
    errors: set[str] = set()
    stop_attempted = False
    try:
        admit_wsl_worker(run, token, process)
        while process.poll() is None:
            servers, incomplete_servers = model_process_snapshot()
            for server in servers:
                if server not in state["owned_servers"]:
                    state["owned_servers"].append(server)
            gpu_state = all_gpu_snapshot()
            if not gpu_state["available"]:
                errors.add(gpu_state["error"])
            first = gpu_state["gpus"][0] if gpu_state["available"] else None
            legacy = ({key: first[key] for key in ("temperature_c", "power_w", "memory_used_mib", "memory_total_mib")}
                      if first else None)
            telemetry.emit("gpu", snapshot=legacy, all_gpus=gpu_state, servers=servers,
                           incomplete_servers=incomplete_servers)
            if time.monotonic() - started > timeout:
                state["timed_out"] = True
                stop_attempted = True
                state["stop"] = stop_wsl_worker(run, token)
                save_json(run / "timeout_supervision.json", state["stop"])
                break
            time.sleep(5)
    except BaseException:
        failure = traceback.format_exc()
        if not stop_attempted:
            stop_attempted = True
            try:
                state["stop"] = stop_wsl_worker(run, token)
            except BaseException:  # noqa: BLE001 - preserve uncertainty, wait, then re-raise the original failure
                state["stop"] = {"status": "uncertain_do_not_retry", "error": traceback.format_exc()}
        save_json(run / "interrupted_supervision.json", {**state, "exception": failure})
        raise
    finally:
        # This must be an actual wait on the original Popen, not a PID disappearance claim.
        exit_code = process.wait()
        save_json(run / "worker_wait.json", {"pid": process.pid, "exit_code": exit_code,
                                            "actual_wait": True, "stop_attempted": stop_attempted})
    state["gpu_observation_errors"] = sorted(errors)
    return state


def require_fresh_output(output: Path) -> None:
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise RuntimeError("Smoke output must be new or empty; prior evidence cannot be resumed or overwritten")


def coordinator(args: argparse.Namespace) -> int:
    if os.name != "nt":
        raise RuntimeError("Run the coordinator on Windows, not WSL")
    require_fresh_output(args.output.resolve())
    import qubitz_disposable_eval as evaluation
    import tmp_run_smoke_suite as suite

    variants = [name for name, _ in suite._discover_variants()]
    if args.failed_from:
        previous = json.loads(args.failed_from.read_text(encoding="utf-8"))
        requested = [r["variant"] for r in previous["runs"] if not r.get("passed") or r.get("issues")]
        variants = [name for name in variants if name in requested]
    if args.variants:
        require_live_variants_allowed(args.variants)
        if set(args.variants) - set(variants):
            raise ValueError("Unknown or unselected variant")
        variants = args.variants
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "summary.json"
    frozen = fingerprint()
    summary: dict[str, Any] = {"status": "running", "prompt": PROMPT, "workspace": str(WORKSPACE),
                               "keep_artifact": args.keep_artifact,
                               "variants": variants, "source_hashes": frozen, "runs": [],
                               "limitations": LIMITATIONS, "pid": os.getpid(), "started": datetime.now().astimezone().isoformat()}
    summary["persistent_storage_before"] = persistent_inventory()
    with suite.smoke_suite_lock():
        require_idle()
        if os.path.lexists(WORKSPACE / "calculator.py"):
            raise RuntimeError("Pre-existing calculator.py must be preserved; campaign not started")
        if model_processes():
            raise RuntimeError("An existing model server is active; no unowned server was stopped")
        if not port_free():
            raise RuntimeError("Port 8001 is occupied by an unowned service")
        health = suite.run_wsl_gpu_preflight(include_torch=True)
        summary["preflight"] = health
        save_json(summary_path, summary)
        if not health.get("ok"):
            raise RuntimeError("GPU infrastructure preflight failed: " + str(health))
        for variant in variants:
            if fingerprint() != frozen:
                raise RuntimeError("Product/test sources changed during the campaign")
            require_idle()
            if not port_free() or model_processes():
                raise RuntimeError("Unexpected model server between serialized runs")
            if os.path.lexists(WORKSPACE / "calculator.py"):
                raise RuntimeError("Unowned calculator.py appeared between runs")
            health = suite.run_wsl_gpu_preflight(include_torch=False)
            if not health.get("ok"):
                raise RuntimeError("GPU infrastructure failure: " + str(health))
            run = output / variant.removesuffix(".py")
            run.mkdir(exist_ok=False)
            summary["active_variant"] = variant
            save_json(summary_path, summary)
            owner_token = uuid.uuid4().hex
            command = wsl_worker_command([
                wsl_path(ROOT / ".venv/bin/python"), "-u", wsl_path(Path(__file__).resolve()),
                "--child", variant, "--output", wsl_path(run), "--timeout", str(args.timeout),
                "--workspace", wsl_path(WORKSPACE), "--owner-token", owner_token,
            ], owner_token)
            save_json(run / "launch_command.json", {"command": command, "owner_token": owner_token})
            log = evaluation.LLAMACPP_LOG_PATH
            log_baseline = server_log_snapshot(log)
            started_ns = time.time_ns()
            save_json(run / "server_log_baseline.json", {
                "source": str(log), "baseline": log_baseline, "started_ns": started_ns,
            })
            started = time.monotonic()
            with (run / "stdout.log").open("w", encoding="utf-8") as stdout, (run / "stderr.log").open("w", encoding="utf-8") as stderr:
                process = subprocess.Popen(command, stdout=stdout, stderr=stderr,
                                           creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
                supervision = supervise_worker(process, run, owner_token, started, args.timeout + 240)
            timed_out = supervision["timed_out"]
            owned_servers = supervision["owned_servers"]
            gpu_observation_errors = supervision["gpu_observation_errors"]
            result_path = run / "result.json"
            row = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {
                "variant": variant, "passed": False, "issues": ["missing_child_result"]}
            row.update(exit_code=process.returncode, coordinator_timeout=timed_out,
                       wall_seconds=round(time.monotonic() - started, 3))
            row["gpu_observation_errors"] = sorted(gpu_observation_errors)
            row["persistent_storage_after"] = persistent_inventory()
            if row["persistent_storage_after"] != summary["persistent_storage_before"]:
                row["issues"].append("shared_persistent_storage_changed")
                row["passed"] = False
            if gpu_observation_errors:
                row["issues"].append("all_gpu_telemetry_incomplete")
                row["passed"] = False
            if timed_out:
                row["timeout_supervision"] = supervision["stop"]
            if timed_out or process.returncode != 0:
                row["passed"] = False
            if timed_out or (row.get("task") or {}).get("timed_out"):
                task_elapsed = (row.get("task") or {}).get("elapsed_seconds")
                cutoff_at = (float(row["task_started_at"]) + float(task_elapsed)
                             if task_elapsed is not None and "task_started_at" in row else time.time())
                row["cutoff_activity"] = cutoff_activity(run / "requests.jsonl", cutoff_at)
            # No new variant is permitted until the exact child group and its server are gone.
            save_json(result_path, row)
            try:
                row["process_cleanup"] = wait_for_owned_cleanup(owned_servers)
            except RuntimeError as exc:
                row["agent_passed"] = bool(row.get("passed"))
                row["passed"] = False
                row["issues"].append("cleanup_did_not_settle")
                row["cleanup_error"] = str(exc)
                save_json(result_path, row)
                summary["runs"].append(row)
                summary.update(status="cleanup_blocked", active_variant=variant)
                save_json(summary_path, summary)
                raise
            row["server_log_capture"] = capture_server_log(
                log, run, log_baseline, started_ns, owned_servers,
            )
            row["cleanup"] = ({"removed": False, "reason": "artifact_retained_by_request"}
                              if args.keep_artifact else clean_created_file(run))
            save_json(result_path, row)
            summary["runs"].append(row)
            save_json(summary_path, summary)
            if not row.get("passed") or row.get("issues"):
                print(json.dumps({"variant": variant, "passed": row.get("passed"), "issues": row.get("issues")}), flush=True)
            if "unexpected_workspace_mutation" in row.get("issues", []):
                raise RuntimeError("Unexpected workspace change; campaign stopped to preserve files")
            if not row.get("passed") or row.get("issues"):
                summary.update(status="held_after_failure", active_variant=None,
                               finished=datetime.now().astimezone().isoformat())
                save_json(summary_path, summary)
                return 1
        summary.update(status="complete", active_variant=None,
                       finished=datetime.now().astimezone().isoformat())
        save_json(summary_path, summary)
    return 0


def main() -> int:
    global WORKSPACE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child")
    parser.add_argument("--owner-token", help=argparse.SUPPRESS)
    parser.add_argument("--variants", nargs="+")
    parser.add_argument("--failed-from", type=Path)
    parser.add_argument("--timeout", type=float, default=280.0)
    parser.add_argument("--workspace", type=Path, help="Existing isolated workspace for the generated calculator")
    parser.add_argument("--keep-artifact", action="store_true", help="Retain the generated calculator for independent checks")
    parser.add_argument("--output", type=Path,
                        default=ROOT / "evaluation" / "results" / ("calculator_smoke_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")))
    args = parser.parse_args()
    if args.workspace is not None:
        if args.workspace.is_symlink() or not args.workspace.is_dir():
            parser.error("--workspace must be an existing non-symlink directory")
        WORKSPACE = args.workspace.resolve()
    if args.keep_artifact and not args.child and args.variants and len(args.variants) != 1:
        parser.error("--keep-artifact requires one variant per isolated workspace")
    if args.keep_artifact and not args.child and not args.variants:
        parser.error("--keep-artifact requires an explicit single variant")
    if args.child:
        return child(args)
    require_fresh_output(args.output.resolve())
    try:
        return coordinator(args)
    except BaseException as exc:
        _record_coordinator_failure(args.output, exc)
        raise


def _record_coordinator_failure(output: Path, error: BaseException) -> None:
    """Persist terminal failure details and any child result before re-raising."""
    summary_path = output.resolve() / "summary.json"
    if not summary_path.is_file():
        return
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(summary, dict):
        return
    summary["coordinator_failure"] = {
        "type": type(error).__name__,
        "message": str(error),
        "traceback": traceback.format_exc(),
    }
    summary["finished"] = datetime.now().astimezone().isoformat()
    active_variant = summary.get("active_variant")
    if active_variant:
        run = output.resolve() / str(active_variant).removesuffix(".py")
        result_path = run / "result.json"
        if result_path.is_file():
            try:
                row = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                row = None
            if isinstance(row, dict):
                row["passed"] = False
                row.setdefault("issues", []).append("coordinator_aborted")
                row["coordinator_failure"] = summary["coordinator_failure"]
                row.setdefault("process_cleanup", {"verified_idle": False, "status": "unknown"})
                save_json(result_path, row)
                runs = summary.setdefault("runs", [])
                existing_index = next(
                    (index for index, existing in enumerate(runs)
                     if existing.get("variant") == active_variant),
                    None,
                )
                if existing_index is None:
                    runs.append(row)
                else:
                    runs[existing_index] = row
    if summary.get("status") == "running":
        summary["status"] = "coordinator_failed"
    save_json(summary_path, summary)


if __name__ == "__main__":
    raise SystemExit(main())
