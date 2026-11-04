"""Evaluation-only ownership and bounded, write-restricted verification helpers."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

OWNER_ENV = "QUBITZ_COMPLEX_SMOKE_OWNER"


def _exclusive_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def worker_identity(pid: int, token: str, proc: Path = Path("/proc")) -> dict[str, Any]:
    if pid <= 1 or len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        raise ValueError("Invalid worker identity or ownership token")
    directory = proc / str(pid)
    fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    environment = (directory / "environ").read_bytes().split(b"\0")
    marker = f"{OWNER_ENV}={token}".encode()
    if environment.count(marker) != 1:
        raise RuntimeError("Worker ownership marker does not match")
    return {
        "pid": pid, "parent_pid": int(fields[1]), "group": int(fields[2]),
        "session": int(fields[3]), "start_ticks": int(fields[19]),
        "boot_id": (proc / "sys/kernel/random/boot_id").read_text().strip(),
        "executable": os.readlink(directory / "exe"),
        "command_line_base64": base64.b64encode((directory / "cmdline").read_bytes()).decode("ascii"),
        "owner": token,
    }


def record_worker_identity(output: Path, token: str) -> dict[str, Any]:
    identity = worker_identity(os.getpid(), token)
    if identity["pid"] != identity["group"] or identity["pid"] != identity["session"]:
        raise RuntimeError("Smoke worker is not the leader of its private session")
    _exclusive_json(output / "worker_identity.json", identity)
    with (output / "wsl-process-group.pid").open("x", encoding="utf-8") as stream:
        stream.write(str(identity["group"]))
        stream.flush()
        os.fsync(stream.fileno())
    return identity


def admit_recorded_worker(output: Path, token: str) -> None:
    identity = json.loads((output / "worker_identity.json").read_text(encoding="utf-8"))
    if (identity.get("owner") != token or type(identity.get("pid")) is not int
            or identity["pid"] <= 1 or identity.get("group") != identity["pid"]
            or identity.get("session") != identity["pid"]):
        raise RuntimeError("Worker admission identity does not match")
    _exclusive_json(output / "launch_admission.json", {"owner": token, "identity": identity})


def wait_for_worker_admission(output: Path, token: str, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            admitted = json.loads((output / "launch_admission.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.05)
            continue
        if admitted != {"owner": token, "identity": worker_identity(os.getpid(), token)}:
            raise RuntimeError("Worker admission is stale or mismatched; model loading refused")
        return
    raise RuntimeError("No worker admission; model loading refused")


def stop_recorded_worker(receipt: Path, token: str, output: Path) -> dict[str, Any]:
    expected = json.loads(receipt.read_text(encoding="utf-8"))
    if (expected.get("owner") != token or expected.get("pid") != expected.get("group")
            or expected.get("pid") != expected.get("session")):
        raise RuntimeError("Invalid worker ownership receipt")
    try:
        actual = worker_identity(expected["pid"], token)
    except FileNotFoundError:
        # A process disappearing mid-probe is not proof that its original PID is absent.
        if (Path("/proc") / str(expected["pid"])).exists():
            raise RuntimeError("Worker identity became incomplete; cleanup refused") from None
        actual = None
    if actual is not None and actual != expected:
        raise RuntimeError("Worker identity changed; cleanup refused")
    output.mkdir(parents=True, exist_ok=True)
    _exclusive_json(output / "stop_intent.json", {
        "expected": expected, "actual": actual, "monotonic": time.monotonic(),
    })
    result = cleanup_owned(token)
    _exclusive_json(output / "stop_result.json", result)
    return result


def storage_journal_types(base: Any) -> set[type]:
    """Composed variants have their own journal class, not just the importable one."""
    from runtime.conversations import ConversationJournal

    journals = {ConversationJournal}
    for gui_class in getattr(getattr(base, "QubitzGUI", None), "__mro__", ()):
        namespace = getattr(vars(gui_class).get("__init__"), "__globals__", {})
        journal = namespace.get("ConversationJournal")
        if isinstance(journal, type):
            journals.add(journal)
    return journals


def install_private_storage(base: Any, output: Path) -> None:
    """Redirect only test-owned persistent storage, not runtime/model configuration."""
    original_memory = base.MemoryStore

    class PrivateMemory(original_memory):
        def __init__(self, workspace: Path) -> None:
            key = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
            super().__init__(output / "private_memory" / key)

    journal_root = output / "private_transcripts"
    journal_root.mkdir(parents=True, exist_ok=True)

    def redirect(original: Any) -> Any:
        def journal_init(self: Any, _root: Path, **kwargs: Any) -> None:
            original(self, journal_root, **kwargs)

        return journal_init

    base.MemoryStore = PrivateMemory
    for journal in storage_journal_types(base):
        journal.__init__ = redirect(journal.__init__)


def propagate_owner(token: str, receipts: list[dict[str, Any]] | None = None) -> None:
    """Keep ownership on children even when MCP supplies a sanitized environment."""
    os.environ[OWNER_ENV] = token
    original = subprocess.Popen

    class OwnedPopen(original):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            if kwargs.get("env") is not None:
                kwargs["env"] = {**kwargs["env"], OWNER_ENV: token}
            super().__init__(*args, **kwargs)

        def communicate(self, *args: Any, **kwargs: Any) -> Any:
            output = super().communicate(*args, **kwargs)
            if receipts is not None and not getattr(self, "_receipt_saved", False):
                receipts.append({"command": self.args, "pid": self.pid, "exit_code": self.returncode,
                                 "stdout": output[0].decode(errors="replace") if isinstance(output[0], bytes) else output[0],
                                 "stderr": output[1].decode(errors="replace") if isinstance(output[1], bytes) else output[1]})
                self._receipt_saved = True
            return output

    subprocess.Popen = OwnedPopen


def owned_processes(token: str) -> list[int]:
    marker = f"{OWNER_ENV}={token}".encode()
    found = []
    for directory in Path("/proc").iterdir():
        if not directory.name.isdigit() or int(directory.name) == os.getpid():
            continue
        try:
            if marker in (directory / "environ").read_bytes().split(b"\0"):
                found.append(int(directory.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    return found


def cleanup_owned(token: str) -> dict[str, Any]:
    if len(token) != 32 or any(c not in "0123456789abcdef" for c in token):
        raise ValueError("Invalid evaluation ownership token")
    receipts = []
    for sig, grace in ((signal.SIGTERM, 3.0), (signal.SIGKILL, 3.0)):
        for pid in owned_processes(token):
            descriptor = None
            try:
                # A pidfd cannot target a subsequently reused numeric PID.
                descriptor = os.pidfd_open(pid)
                if pid not in owned_processes(token):
                    continue
                signal.pidfd_send_signal(descriptor, sig)
                receipts.append({"pid": pid, "signal": sig.name})
            except (ProcessLookupError, FileNotFoundError):
                pass
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        deadline = time.monotonic() + grace
        while owned_processes(token) and time.monotonic() < deadline:
            time.sleep(0.1)
    return {"signals": receipts, "survivors": owned_processes(token)}


VERIFICATION_BOOTSTRAP = r'''
import os, pathlib, runpy, sys
root = pathlib.Path(sys.argv[1]).resolve()
code = sys.argv[2]
def inside(value):
    if isinstance(value, int):
        return True
    try:
        return pathlib.Path(os.fsdecode(value)).resolve().is_relative_to(root)
    except (ValueError, TypeError):
        return False
def audit(event, args):
    if event == "open":
        path, mode, flags = args
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
        if writing and path != os.devnull and not inside(path):
            raise PermissionError("Verification cannot write outside its fixture")
    if event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.chdir"}:
        if not inside(args[0]) or (event in {"os.remove", "os.rmdir", "os.mkdir"} and len(args) > 1 and args[-1] not in {None, -1}):
            raise PermissionError("Verification mutation outside fixture or through dir_fd")
    if event in {"os.rename", "os.link", "os.symlink"}:
        if not all(inside(p) for p in args[:2]) or any(p not in {None, -1} for p in args[2:]):
            raise PermissionError("Verification cannot link or move outside fixture")
    if event.startswith(("subprocess.", "socket.", "ctypes.")) or event in {"os.system", "os.exec", "os.fork", "os.posix_spawn"}:
        raise PermissionError("Verification cannot launch processes, load native helpers or use the network")
os.chdir(root)
sys.path.insert(0, str(root))
sys.dont_write_bytecode = True
sys.addaudithook(audit)
exec(compile(code, "<fixture-verification>", "exec"))
'''


def verify_python(workspace: Path, code: str) -> tuple[bool, dict[str, Any]]:
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    command = [sys.executable, "-I", "-B", "-c", VERIFICATION_BOOTSTRAP, str(workspace.resolve()), code]
    try:
        result = subprocess.run(command, cwd=workspace, env=environment, capture_output=True,
                                text=True, timeout=30, check=False)
        return result.returncode == 0, {"command": command, "exit_code": result.returncode,
                                      "stdout": result.stdout, "stderr": result.stderr}
    except subprocess.TimeoutExpired as exc:
        return False, {"timeout": True, "stdout": str(exc.stdout or ""), "stderr": str(exc.stderr or "")}


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--cleanup-owned")
    action.add_argument("--stop-recorded-worker", type=Path)
    parser.add_argument("--owner-token")
    parser.add_argument("--stop-output", type=Path)
    args = parser.parse_args()
    if args.stop_recorded_worker:
        if not args.owner_token or args.stop_output is None:
            parser.error("--stop-recorded-worker requires --owner-token and --stop-output")
        result = stop_recorded_worker(args.stop_recorded_worker, args.owner_token, args.stop_output)
    else:
        result = cleanup_owned(args.cleanup_owned)
    print(json.dumps(result), flush=True)
    return 1 if result["survivors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
