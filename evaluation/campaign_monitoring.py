"""Owned evidence monitoring; mutable snapshots never share append-only cursors."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from evaluation.campaign_observation import (
    commit_observation,
    read_json,
    recover_cursor,
    shared_reader,
)


def capture_stop_boundary() -> dict:
    if os.name != "nt":
        raise RuntimeError("Native stop ownership cannot be established on this host")
    script = r"""$ErrorActionPreference='Stop'
$all=@(Get-CimInstance Win32_Process)
$selected=@($all|Where-Object {$_.Name -match '^(python(w)?|llama-server)[.]exe$'})
$ids=@($selected|ForEach-Object {$_.ProcessId;$_.ParentProcessId})
$rows=@($all|Where-Object {$ids -contains $_.ProcessId}|ForEach-Object {
 $owner=Invoke-CimMethod -InputObject $_ -MethodName GetOwner
 [ordered]@{pid=[int]$_.ProcessId;parent_pid=[int]$_.ParentProcessId;
 creation_ticks=[string]$_.CreationDate.ToUniversalTime().Ticks;executable=$_.ExecutablePath;
 command_line=$_.CommandLine;owner=if($owner.ReturnValue -eq 0){$owner.Domain+'\'+$owner.User}else{$null}}
})
[ordered]@{processes=$rows;ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue|
 Where-Object {$_.LocalPort -in 8000,8001,8765}|Select-Object LocalAddress,LocalPort,OwningProcess)}|
 ConvertTo-Json -Depth 8 -Compress
"""
    commands = {
        "native": ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        "wsl": ["wsl.exe", "--exec", "ps", "-eo", "pid=,ppid=,user=,etimes=,args="],
        "wsl_ports": ["wsl.exe", "--exec", "ss", "-H", "-ltnp"],
        "wsl_namespace": ["wsl.exe", "--exec", "cat", "/proc/sys/kernel/random/boot_id"],
        "gpu": ["nvidia-smi", "--query-gpu=index,temperature.gpu,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits"],
    }
    snapshot = {"time": time.time()}
    for name, command in commands.items():
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        snapshot[name] = {"command": command, "actual_exit_code": result.returncode,
                          "stdout": result.stdout, "stderr": result.stderr}
        if result.returncode:
            raise RuntimeError("Incomplete stop boundary: " + name)
    snapshot["native_processes"] = json.loads(snapshot["native"]["stdout"])["processes"]
    return snapshot


class CampaignMonitor:
    def __init__(self, output: Path, directory: Path, *, boundary=capture_stop_boundary, stop=None):
        from evaluation.disposable_followup_matrix import save_json

        self.output, self.directory = output.resolve(), directory.resolve()
        self.boundary, self.stop = boundary, stop
        self.save = save_json
        self.cursor_path = directory / "monitor_cursor.json"
        self.history = directory / "monitor_history.jsonl"
        self.cursor = read_json(self.cursor_path) if self.cursor_path.exists() else {"streams": {}, "artifacts": {}}
        self.cursor = recover_cursor(self.history, self.cursor, lambda value: self.save(self.cursor_path, value))
        self.failures = 0
        self.escalated = (directory / "monitor_alert.json").exists()

    def owned(self, path: Path) -> Path:
        if (path.resolve() != path or not path.is_relative_to(self.output)
                and not path.is_relative_to(self.directory)
                or any(part.is_symlink() for part in (path, *path.parents))):
            raise ValueError("Evidence path is external or aliased: " + str(path))
        return path

    def discover(self) -> tuple[list[Path], list[Path]]:
        self.discovery_errors = []
        streams = [self.output / "coordinator_events.jsonl", self.directory / "stdout.log", self.directory / "stderr.log"]
        artifacts = [self.output / "progress.json"]
        for launch in self.output.glob("*/repeat_*/*/attempt_*/launch.json"):
            try:
                self.owned(launch)
                row = read_json(launch)
                if not all(isinstance(row.get(key), str) and row[key] for key in ("owner", "variant", "scenario")):
                    raise ValueError("Incomplete owned case launch receipt")
                case = launch.parent
                if case.parent.name != row["scenario"] or case.parents[2].name != Path(row["variant"]).stem:
                    raise ValueError("Case launch receipt does not match its path")
            except (OSError, ValueError) as error:
                self.discovery_errors.append({"path": str(launch), "error": str(error)})
                continue
            streams.extend(case / name for name in ("events.jsonl", "event_payload_index.jsonl", "gpu.jsonl", "stdout.log", "stderr.log"))
            streams.extend(case.glob("server_[0-9][0-9][0-9].log"))
            streams.extend((case / "private_transcripts/conversations").glob("*.txt"))
            artifacts.extend(case / name for name in ("result.json", "turn_progress.json", "owned_windows_servers.json"))
        return sorted({path for path in streams if path.exists()}), sorted({path for path in artifacts if path.exists()})

    def stream_range(self, path: Path, previous: dict, terminal: bool) -> tuple[dict | None, dict]:
        offset = previous.get("offset", 0)
        with shared_reader(self.owned(path)) as stream:
            stat = os.fstat(stream.fileno())
            identity = [stat.st_dev, stat.st_ino]
            if stat.st_size < offset or previous.get("identity", identity) != identity:
                raise RuntimeError("Owned stream shrank or was replaced; cursor preserved")
            if previous.get("anchor_sha256"):
                stream.seek(previous["anchor_start"])
                anchor = stream.read(offset - previous["anchor_start"])
                if hashlib.sha256(anchor).hexdigest() != previous["anchor_sha256"]:
                    raise RuntimeError("Previously consumed stream boundary changed; cursor preserved")
            stream.seek(offset)
            raw = stream.read(4_000_000)
        if not terminal or offset + len(raw) < stat.st_size:
            raw = raw[:raw.rfind(b"\n") + 1]
        if not raw:
            if stat.st_size - offset >= 4_000_000:
                raise RuntimeError("Record exceeds observation window; no tail discarded")
            return None, previous
        end = offset + len(raw)
        anchor = raw[-64:]
        return ({"path": str(path), "start": offset, "end": end, "sha256": hashlib.sha256(raw).hexdigest()},
                {"offset": end, "identity": identity, "anchor_start": end - len(anchor),
                 "anchor_sha256": hashlib.sha256(anchor).hexdigest()})

    def artifact_snapshot(self, path: Path) -> dict:
        state = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
        directory = self.directory / "artifact_versions"
        directory.mkdir(exist_ok=True)
        if directory.resolve() != directory or directory.is_symlink():
            raise ValueError("Artifact archive is aliased")

        def transfer(stream, size, target=None):
            digest = hashlib.sha256()
            remaining = size
            while remaining:
                raw = stream.read(min(remaining, 1024 * 1024))
                if not raw:
                    raise RuntimeError("Mutable artifact changed during snapshot")
                digest.update(raw)
                if target is not None:
                    target.write(raw)
                remaining -= len(raw)
            return digest.hexdigest()

        with shared_reader(self.owned(path)) as stream:
            before = os.fstat(stream.fileno())
            digest = transfer(stream, before.st_size)
            after = os.fstat(stream.fileno())
            if state(before) != state(after):
                raise RuntimeError("Mutable artifact is unstable")
            version = directory / (digest + ".bin")
            if version.is_symlink():
                raise ValueError("Archived artifact version is aliased")
            if not version.exists():
                if shutil.disk_usage(directory).free - before.st_size < 22 * 1024**3:
                    raise RuntimeError("Artifact snapshot would cross the 22 GiB stop threshold")
                stream.seek(0)
                with version.open("xb") as target:
                    copied = transfer(stream, before.st_size, target)
                    target.flush()
                    os.fsync(target.fileno())
                if copied != digest or state(before) != state(os.fstat(stream.fileno())):
                    raise RuntimeError("Mutable artifact changed during archival; preserve partial evidence")
        with shared_reader(version) as archived:
            if hashlib.file_digest(archived, "sha256").hexdigest() != digest:
                raise RuntimeError("Archived artifact version is incomplete; preserve for review")
        return {"path": str(path), "bytes": before.st_size, "sha256": digest, "snapshot": str(version)}

    def observe(self, *, terminal=False) -> dict:
        streams, artifacts = self.discover()
        if terminal and (self.output / "summary.json").exists():
            artifacts.append(self.output / "summary.json")
        cursor = {"streams": dict(self.cursor["streams"]), "artifacts": dict(self.cursor["artifacts"])}
        ranges, versions, errors = [], [], list(self.discovery_errors)
        for path in streams:
            try:
                item, updated = self.stream_range(path, cursor["streams"].get(str(path), {}), terminal)
                if item:
                    ranges.append(item)
                    cursor["streams"][str(path)] = updated
            except (OSError, ValueError, RuntimeError) as error:
                errors.append({"path": str(path), "error": str(error)})
        for path in artifacts:
            try:
                item = self.artifact_snapshot(path)
                if cursor["artifacts"].get(str(path)) != item["sha256"]:
                    versions.append(item)
                    cursor["artifacts"][str(path)] = item["sha256"]
            except (OSError, ValueError, RuntimeError) as error:
                errors.append({"path": str(path), "error": str(error)})
        observation = {"observation_id": time.time_ns(), "ranges": ranges, "versions": versions,
                       "errors": errors, "cursor_after": cursor, "semantic_review_complete": False}
        cursor["observation_id"] = observation["observation_id"]
        commit_observation(self.history, observation, lambda value: self.save(self.cursor_path, value))
        self.cursor = cursor
        return observation

    def escalate(self, errors: list) -> None:
        from evaluation.campaign_lifecycle import exclusive_json
        from evaluation.followup_campaign_control import same_identity, stop_campaign

        self.escalated = True
        alert = {"status": "persistent_observation_failure", "errors": errors, "time": time.time(),
                 "requires_attention": True, "no_force_or_retry": True}
        exclusive_json(self.directory / "monitor_alert.json", alert)
        try:
            if (self.output / "stop_request.json").exists():
                outcome = {"status": "existing_stop_request_not_repeated"}
            else:
                snapshot = self.boundary()
                if (any(snapshot.get(name, {}).get("actual_exit_code") != 0
                        for name in ("native", "wsl", "wsl_ports", "wsl_namespace", "gpu"))
                        or not snapshot["wsl_namespace"].get("stdout", "").strip()
                        or not snapshot["gpu"].get("stdout", "").strip()):
                    raise RuntimeError("Fresh native/WSL/namespace/GPU stop boundary is incomplete")
                expected = read_json(self.directory / "launch_receipt.json")["identity"]
                actual = next((p for p in snapshot["native_processes"] if p["pid"] == expected["pid"]), None)
                parent = next((p for p in snapshot["native_processes"] if p["pid"] == os.getpid()), None)
                if (not same_identity(actual, expected) or actual.get("parent_pid") != os.getpid()
                        or not parent or not parent.get("owner") or actual.get("owner") != parent["owner"]
                        or any(not p.get(key) for p in snapshot["native_processes"]
                               for key in ("pid", "parent_pid", "creation_ticks", "executable", "command_line", "owner"))):
                    raise RuntimeError("Fresh complete original/parent ownership is not established")
                if read_json(self.output / "coordinator_identity.json") != expected:
                    raise RuntimeError("Coordinator receipt differs from supervised original")
                exclusive_json(self.directory / "monitor_stop_snapshot.json", snapshot)
                exclusive_json(self.directory / "monitor_stop_decision.json", {**alert, "decision": "cooperative_stop_only"})
                exclusive_json(self.directory / "monitor_stop_intent.json", {"identity": expected, "time": time.time()})
                outcome = (self.stop or stop_campaign)(self.output)
        except Exception as error:  # noqa: BLE001 - unknown stop outcome must never be replayed
            outcome = {"status": "unknown_do_not_retry_or_force", "error": repr(error)}
        exclusive_json(self.directory / "monitor_escalation_result.json", outcome)

    def tick(self, *, terminal=False) -> dict:
        try:
            observation = self.observe(terminal=terminal)
        except Exception as error:  # noqa: BLE001 - retain independent lifecycle and original waiter
            observation = {"ranges": [], "versions": [], "errors": [{"error": repr(error)}]}
        errors = observation["errors"]
        progress = {}
        try:
            if (self.output / "progress.json").exists():
                progress = read_json(self.output / "progress.json")
        except (OSError, ValueError) as error:
            errors.append({"path": "progress.json", "error": str(error)})
        self.failures = self.failures + 1 if errors else 0
        terminal_problem = None
        if terminal:
            try:
                waited = read_json(self.directory / "coordinator_exit.json")
                summary = read_json(self.output / "summary.json")
                if (waited.get("actual_wait") is not True or type(waited.get("exit_code")) is not int
                        or waited["exit_code"] != 0 or summary.get("status") not in {"complete", "paused"}):
                    terminal_problem = {"exit_code": waited.get("exit_code"),
                                        "actual_wait": waited.get("actual_wait"),
                                        "checkpoint_status": summary.get("status")}
            except (OSError, ValueError, TypeError):
                terminal_problem = {"status": "terminal_outcome_unknown"}
            if terminal_problem and not (self.directory / "monitor_terminal_alert.json").exists():
                from evaluation.campaign_lifecycle import exclusive_json

                exclusive_json(self.directory / "monitor_terminal_alert.json", {
                    "status": "terminal_review_required", "time": time.time(),
                    "requires_attention": True, "terminal": terminal_problem, "no_action_dispatched": True,
                })
        status = {"status": "coordinator_exited_review_required" if terminal else "observation_degraded" if errors else "watching",
                  "updated_unix": time.time(), "errors": errors, "consecutive_failures": self.failures,
                  "counts": progress.get("counts", {}), "evidence_review_complete": False,
                  "requires_attention": self.escalated or bool(errors) or terminal_problem is not None,
                  "terminal_problem": terminal_problem}
        self.save(self.directory / "monitor_state.json", status)
        if not terminal and self.failures >= 3 and not self.escalated:
            self.escalate(errors)
        return observation

    def finish(self) -> None:
        for _ in range(1024):
            result = self.tick(terminal=True)
            if not result["ranges"]:
                break
