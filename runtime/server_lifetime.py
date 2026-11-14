"""Retain actual owned native-child exits without treating proxy exits as native."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path


def exclusive(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def powershell_launch(command: list[str], directory: str, token: str) -> str:
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    # The Process object retains the exact child handle until WaitForExit returns.
    return f"""$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
function Write-Receipt($name, $value) {{
    $file = [IO.File]::Open((Join-Path {quote(directory)} $name), [IO.FileMode]::CreateNew)
    try {{
        $raw = [Text.Encoding]::UTF8.GetBytes(($value | ConvertTo-Json -Depth 12 -Compress))
        $file.Write($raw, 0, $raw.Length); $file.Flush($true)
    }} finally {{ $file.Dispose() }}
}}
$info = New-Object Diagnostics.ProcessStartInfo
$info.FileName = {quote(command[0])}
$info.Arguments = {quote(subprocess.list2cmdline(command[1:]))}
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$child = New-Object Diagnostics.Process
$child.StartInfo = $info
if (-not $child.Start()) {{ throw 'Owned server start failed' }}
$outCopy = $child.StandardOutput.BaseStream.CopyToAsync([Console]::OpenStandardOutput())
$errCopy = $child.StandardError.BaseStream.CopyToAsync([Console]::OpenStandardError())
$identity = @{{pid=$child.Id; creation_ticks=[string]$child.StartTime.ToUniversalTime().Ticks;
 executable=$info.FileName; arguments=$info.Arguments; parent_pid=$PID; owner_token={quote(token)}}}
try {{
    Write-Receipt 'native_launch.json' $identity
}} finally {{
    $child.WaitForExit()
    $code = $child.ExitCode
    [void]$outCopy.GetAwaiter().GetResult()
    [void]$errCopy.GetAwaiter().GetResult()
    Write-Receipt 'native_exit.json' @{{identity=$identity; actual_wait=$true; exit_code=$code;
        shutdown_requested=(Test-Path -LiteralPath (Join-Path {quote(directory)} 'shutdown_request.json'))}}
}}
exit $code
"""


def prepare(server, command: list[str], translate) -> list[str]:
    token = getattr(server, "_qubitz_owner_token", "") or uuid.uuid4().hex
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', token):
        raise ValueError('Invalid native lifetime owner token')
    directory = Path(server.runtime_workspace) / ".cache/llama.cpp/lifetimes" / token
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError('Aliased native lifetime directory')
    directory.mkdir(parents=True, exist_ok=False)
    server._qubitz_lifetime_directory = directory
    native_command = list(command)
    native_handle_in_powershell = False
    if Path(command[0]).name.casefold() == "powershell.exe":
        index = next((i + 1 for i, item in enumerate(command[:-1])
                      if item.casefold() == "-encodedcommand"), None)
        if index is None:
            raise ValueError("Cannot bind owned PowerShell server command")
        script = base64.b64decode(command[index], validate=True).decode("utf-16le")
        lines = script.splitlines()
        positions = [i for i, line in enumerate(lines) if line.startswith("& ")]
        if len(positions) != 1:
            raise ValueError("Expected one exact owned native server invocation")
        line = lines[positions[0]][2:]
        pattern = r"'(?:[^']|'')*'"
        if re.sub(pattern, "", line).strip():
            raise ValueError("Owned native arguments are not literal PowerShell strings")
        native_command = [part[1:-1].replace("''", "'") for part in re.findall(pattern, line)]
        lines[positions[0]] = powershell_launch(native_command, str(translate(directory)), token)
        result = list(command)
        result[index] = base64.b64encode("\n".join(lines).encode("utf-16le")).decode("ascii")
        native_handle_in_powershell = True
    else:
        result = command
    exclusive(directory / "intent.json", {"owner_token": token, "command": native_command,
                                          "native_handle_in_powershell": native_handle_in_powershell})
    return result


def observe_native(server) -> None:
    """On native hosts Popen itself is the native child, not a WSL/PowerShell proxy."""
    directory = server._qubitz_lifetime_directory
    process = server.process
    intent = json.loads((directory / "intent.json").read_bytes())
    identity = {"pid": process.pid, "owner_token": directory.name, "command": intent["command"],
                "parent_pid": os.getpid()}
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        times = [wintypes.FILETIME() for _ in range(4)]
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        if not api.GetProcessTimes(int(process._handle), *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        identity["creation_ticks"] = str((times[0].dwHighDateTime << 32) + times[0].dwLowDateTime + 504911232000000000)
    else:
        try:
            identity["creation"] = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        except FileNotFoundError:
            identity['creation'] = 'unavailable_after_early_exit'
        identity["namespace"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    exclusive(directory / "native_launch.json", identity)

    def wait():
        code = process.wait()
        try:
            exclusive(directory / "native_exit.json", {"identity": identity, "actual_wait": True,
                      "exit_code": code, "shutdown_requested": (directory / "shutdown_request.json").exists()})
        except OSError as error:
            server._qubitz_lifetime_error = str(error)

    threading.Thread(target=wait, name="qubitz-server-exit", daemon=True).start()


def observe_proxy(server) -> None:
    """Track the WSL interop process separately from its Windows child receipts."""
    directory = server._qubitz_lifetime_directory
    process = server.process
    identity = {"pid": process.pid, "owner_token": directory.name,
                "command": process.args, "parent_pid": os.getpid(), "role": "wsl_interop_proxy"}
    try:
        identity["creation"] = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
        identity["namespace"] = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except (FileNotFoundError, IndexError):
        identity["creation"] = "unavailable_after_early_exit"
        identity["namespace"] = "unavailable"
    exclusive(directory / "proxy_launch.json", identity)

    def wait():
        code = process.wait()
        try:
            exclusive(directory / "proxy_exit.json", {"identity": identity, "actual_wait": True,
                      "exit_code": code})
        except OSError as error:
            server._qubitz_proxy_lifetime_error = str(error)

    threading.Thread(target=wait, name="qubitz-server-proxy-exit", daemon=True).start()


def request_shutdown(server) -> None:
    directory = getattr(server, "_qubitz_lifetime_directory", None)
    if directory is not None and not (directory / "shutdown_request.json").exists():
        exclusive(directory / "shutdown_request.json", {"owner_token": directory.name,
                                                        "reason": "runtime_owned_shutdown"})


def snapshot(server) -> dict:
    if server is None:
        return {"native_exit": "unknown", "reason": "no_owned_server"}
    directory = getattr(server, "_qubitz_lifetime_directory", None)
    result = {"native_exit": "unknown", "directory": str(directory) if directory else None}
    if directory is not None:
        for name in ("intent.json", "native_launch.json", "native_exit.json", "proxy_launch.json",
                     "proxy_exit.json", "shutdown_request.json"):
            path = directory / name
            if path.is_file():
                try:
                    raw = path.read_bytes()
                    result[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "value": json.loads(raw)}
                except (OSError, ValueError) as error:
                    result[name] = {"error": str(error)}
        launch = result.get("native_launch.json", {}).get("value")
        exit_record = result.get("native_exit.json", {}).get("value", {})
        if (launch and launch.get('owner_token') == directory.name
                and exit_record.get("identity") == launch and exit_record.get("actual_wait") is True
                and type(exit_record.get('exit_code')) is int):
            result["native_exit"] = exit_record.get("exit_code")
        proxy_launch = result.get("proxy_launch.json", {}).get("value")
        proxy_exit = result.get("proxy_exit.json", {}).get("value", {})
        if (proxy_launch and proxy_launch.get("owner_token") == directory.name
                and proxy_exit.get("identity") == proxy_launch and proxy_exit.get("actual_wait") is True
                and type(proxy_exit.get("exit_code")) is int):
            result["proxy_exit"] = proxy_exit.get("exit_code")
        else:
            result["proxy_exit"] = "unknown"
    try:
        result["log_tail"] = server.read_log_tail(4000)
    except (OSError, AttributeError) as error:
        result["log_error"] = str(error)
    return result


def wait_receipt(server, timeout: float = 2) -> dict:
    """Wait only for publication after cleanup; never signal or infer an exit."""
    deadline = time.monotonic() + timeout
    while True:
        result = snapshot(server)
        if result.get('native_exit') != 'unknown' or time.monotonic() >= deadline:
            return result
        time.sleep(0.025)
