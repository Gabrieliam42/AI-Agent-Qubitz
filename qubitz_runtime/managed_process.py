"""Own managed server processes in their native OS and retain actual exit receipts."""
from __future__ import annotations

import asyncio
import atexit
import json
import os
import re
import runpy
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

_SESSIONS: dict[str, ManagedServerProcess] = {}
_LEASE_SECONDS = 45.0
STATE_LOCK = threading.RLock()


def _write(path: Path, value: dict, *, exclusive: bool = False) -> None:
    data = json.dumps(value, ensure_ascii=True, indent=2).encode()
    if exclusive:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def settle_owned_call(function: Callable, *args, **kwargs) -> tuple[asyncio.Task, asyncio.CancelledError | None]:
    """Retain a threaded operation's outcome before propagating caller cancellation."""
    operation = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation = None
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError as exc:
            if operation.cancelled():
                raise
            cancellation = exc
        except Exception:  # noqa: BLE001 - preserve the completed exception for the caller's reconciliation
            break  # The caller reconciles the original completed exception via result().
    return operation, cancellation


class ManagedServerProcess:
    """A token authorizes IPC to the original supervisor, never a numeric PID signal."""

    def __init__(self, directory: Path, token: str) -> None:
        self.directory = directory.resolve()
        self.token = token
        self.process: subprocess.Popen | None = None
        self.receipt: dict = {}
        self._closed = threading.Event()
        self._lock = threading.Lock()

    @classmethod
    def launch(cls, directory: Path, command: list[str] | str, cwd: str, environment: dict[str, str],
               spawn: Callable[[Path], subprocess.Popen], *, timeout: float = 30,
               kind: str = "python", shell: bool = False,
               cancel_event: threading.Event | None = None) -> ManagedServerProcess:
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Managed command was cancelled before launch")
        directory.mkdir(parents=True, exist_ok=True)
        session = cls(directory, uuid.uuid4().hex)
        config = directory / "process_launch.json"
        _write(config, {"token": session.token, "command": command, "cwd": cwd,
                        "environment": environment, "lease_seconds": _LEASE_SECONDS,
                        "kind": kind, "shell": shell}, exclusive=True)
        _write(directory / "lease.json", {"token": session.token, "counter": 0})
        session.process = spawn(config)
        _SESSIONS[str(session.directory)] = session
        threading.Thread(target=session._heartbeat, name="qubitz-managed-server", daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                session.stop()
                raise InterruptedError("Managed command was cancelled before admission")
            receipt = session._owned("process_started.json")
            if receipt:
                session.receipt = receipt
                request = directory / "admission_request.json"
                if not request.exists():
                    _write(request, {"token": session.token, "identity": receipt["identity"]}, exclusive=True)
                if session._owned("admitted.json"):
                    return session
            ended = session._owned("process_exit.json")
            if ended:
                session._wait_proxy()
                raise RuntimeError("Managed server failed before admission: " + str(ended.get("error")))
            if session.process.poll() is not None:
                session._wait_proxy()
                raise RuntimeError("Managed process supervisor exited without an ownership receipt")
            time.sleep(0.05)
        session.stop(timeout=10)
        raise RuntimeError("Managed server ownership receipt was not produced before admission timeout")

    @classmethod
    def recover(cls, directory: Path, token: str) -> ManagedServerProcess | None:
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            return None
        key = str(directory.resolve())
        session = _SESSIONS.get(key)
        if session is not None:
            return session if session.token == token else None
        session = cls(directory, token)
        session.receipt = session._owned("process_started.json") or {}
        identity = session.receipt.get("identity") or {}
        return session if all(identity.get(key) for key in
                              ("pid", "parent_pid", "creation", "namespace", "executable", "command_line")) else None

    def _owned(self, name: str) -> dict | None:
        record = _read(self.directory / name)
        if not record or record.get("token") != self.token:
            return None
        if name == "process_exit.json" and self.receipt and record.get("identity") != self.receipt.get("identity"):
            return None
        if name == "process_exit.json" and not self.receipt:
            started = self._owned("process_started.json")
            if started and started.get("identity") == record.get("identity"):
                self.receipt = started
        return record

    def _wait_proxy(self, deadline: float | None = None) -> bool:
        if self.process is None:
            return self.supervisor_waited
        deadline = time.monotonic() + 10 if deadline is None else deadline
        if not self._lock.acquire(timeout=max(0, deadline - time.monotonic())):
            return False
        try:
            try:
                code = self.process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                return False
            _write(self.directory / "supervisor_wait.json", {
                "token": self.token, "pid": self.process.pid, "actual_wait": True, "returncode": code,
            })
            self._closed.set()
            if _SESSIONS.get(str(self.directory)) is self:
                _SESSIONS.pop(str(self.directory))
            return True
        finally:
            self._lock.release()

    def _heartbeat(self) -> None:
        counter = 0
        while not self._closed.is_set():
            if self._owned("process_exit.json") or self.process is not None and self.process.poll() is not None:
                self._wait_proxy()
                return
            counter += 1
            try:
                _write(self.directory / "lease.json", {"token": self.token, "counter": counter})
            except OSError:
                pass
            self._closed.wait(2)

    def poll(self) -> int | None:
        ended = self._owned("process_exit.json")
        if ended and ended.get("actual_wait") is True:
            self._wait_proxy()
            return int(ended["returncode"])
        return None

    def exit_receipt(self) -> dict | None:
        return self._owned("process_exit.json")

    @property
    def returncode(self) -> int | None:
        ended = self.exit_receipt()
        return int(ended["returncode"]) if ended and ended.get("actual_wait") else None

    def wait(self, timeout: float | None = None) -> int:
        deadline = None if timeout is None else time.monotonic() + timeout
        while deadline is None or time.monotonic() < deadline:
            ended = self.exit_receipt()
            if ended and ended.get("error"):
                self._wait_proxy(deadline)
                raise RuntimeError("Managed process cleanup failed: " + str(ended["error"]))
            if ended is None and self.process is not None and self.process.poll() is not None:
                self._wait_proxy(deadline)
                raise RuntimeError("Managed supervisor exited without a child exit receipt")
            if ended and ended.get("actual_wait") and self._wait_proxy(deadline):
                if ended.get("error") or ended.get("tree_verified") is not True:
                    raise RuntimeError("Managed process tree cleanup is unverified: " + str(ended.get("error")))
                return int(ended["returncode"])
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(str(self.directory), timeout)

    @property
    def supervisor_waited(self) -> bool:
        waited = self._owned("supervisor_wait.json")
        return bool(waited and waited.get("actual_wait") is True)

    @property
    def pid(self) -> int:
        return int(self.receipt["identity"]["pid"])

    def stop(self, *, timeout: float = 15) -> dict:
        request_path = self.directory / "stop_request.json"
        request = {"token": self.token, "identity": self.receipt.get("identity")}
        if not request_path.exists():
            try:
                _write(request_path, request, exclusive=True)
            except FileExistsError:
                pass
        existing = _read(request_path)
        if (not existing or existing.get("token") != self.token
                or existing.get("identity") not in (None, self.receipt.get("identity"))):
            return {"stopped": False, "actual_wait": False, "status": "ownership_unknown",
                    "error": "The saved stop request does not match the original launch"}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ended = self._owned("process_exit.json")
            if ended:
                proxy_waited = self._wait_proxy(deadline)
                verified = (ended.get("actual_wait") is True and not ended.get("error") and proxy_waited
                            and ended.get("tree_verified") is True)
                return {"stopped": verified, "actual_wait": ended.get("actual_wait") is True,
                        "status": "stopped" if verified else "stop_unknown",
                        "returncode": ended.get("returncode"), "reason": ended.get("reason"),
                        "supervisor_actual_wait": proxy_waited, "exit_receipt": ended}
            time.sleep(0.05)
        return {"stopped": False, "actual_wait": False, "status": "stop_pending",
                "error": "The original supervisor has not produced a verified process exit receipt"}


class _WindowsJob:
    def __init__(self, process: subprocess.Popen) -> None:
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [("user", ctypes.c_longlong), ("job_user", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ("read_ops", "write_ops", "other_ops", "read", "write", "other")]

        class Extended(ctypes.Structure):
            _fields_ = [("basic", Limits), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                        ("peak_job", ctypes.c_size_t)]

        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in (
            ("CreateJobObjectW", [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ("SetInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ("AssignProcessToJobObject", [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ("TerminateJobObject", [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            ("QueryInformationJobObject", [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                           wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            ("CloseHandle", [wintypes.HANDLE], wintypes.BOOL),
        ):
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not (self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits))
                and self.api.AssignProcessToJobObject(self.handle, int(process._handle))):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def terminate(self) -> None:
        if not self.api.TerminateJobObject(self.handle, 1):
            import ctypes

            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def wait_empty(self, timeout: float = 10) -> None:
        import ctypes
        from ctypes import wintypes

        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
                (name, wintypes.DWORD) for name in ("faults", "total", "active", "terminated")]

        deadline = time.monotonic() + timeout
        while True:
            counts = Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(counts), ctypes.sizeof(counts), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if counts.active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned Windows job still has active processes")
            time.sleep(0.05)


class _LinuxTree:
    def __init__(self) -> None:
        import ctypes

        api = ctypes.CDLL(None, use_errno=True)
        if api.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Unable to own orphaned managed descendants")
        self.descriptors: dict[tuple[int, str], int] = {}
        self.waits: list[dict] = []
        self.root_returncode: int | None = None

    def snapshot(self) -> list[tuple[int, str]]:
        pending, found = [(os.getpid(), "self")], []
        while pending:
            parent, parent_creation = pending.pop()
            parent_key = (parent, parent_creation)
            if parent != os.getpid() and not self._parent_alive(parent_key):
                continue
            try:
                children = Path(f"/proc/{parent}/task/{parent}/children").read_text().split()
            except FileNotFoundError:
                continue
            for raw in children:
                pid = int(raw)
                descriptor = None
                committed = False
                try:
                    parts = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
                    if int(parts[1]) != parent:
                        continue
                    key = (pid, parts[19])
                    if key not in self.descriptors:
                        descriptor = os.pidfd_open(pid)
                        again = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
                        if again[19] != key[1] or int(again[1]) != parent:
                            continue
                        if parent != os.getpid() and not self._parent_alive(parent_key):
                            continue
                        self.descriptors[key] = descriptor
                        committed = True
                    found.append(key)
                    pending.append(key)
                except ProcessLookupError:
                    continue
                except FileNotFoundError:
                    continue
                finally:
                    if descriptor is not None and not committed:
                        os.close(descriptor)
        return found

    def _parent_alive(self, key: tuple[int, str]) -> bool:
        descriptor = self.descriptors.get(key)
        if descriptor is None:
            return False
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        if poller.poll(0):
            return False
        try:
            return Path(f"/proc/{key[0]}/stat").read_text().rsplit(") ", 1)[1].split()[19] == key[1]
        except FileNotFoundError:
            return False

    def reap_adopted(self, root_pid: int) -> None:
        for pid, creation in self.snapshot():
            if pid == root_pid:
                continue
            try:
                waited, status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if waited:
                self.waits.append({"pid": pid, "creation": creation, "actual_wait": True,
                                   "returncode": os.waitstatus_to_exitcode(status)})
                descriptor = self.descriptors.pop((pid, creation), None)
                if descriptor is not None:
                    os.close(descriptor)
        for key, descriptor in list(self.descriptors.items()):
            if key[0] == root_pid:
                continue
            poller = select.poll()
            poller.register(descriptor, select.POLLIN)
            if poller.poll(0):
                os.close(self.descriptors.pop(key))

    def signal(self, keys: list[tuple[int, str]], value: int) -> None:
        for key in keys:
            try:
                signal.pidfd_send_signal(self.descriptors[key], value)
            except ProcessLookupError:
                pass

    def cleanup(self, process: subprocess.Popen) -> int:
        deadline, graceful_until = time.monotonic() + 10, time.monotonic() + 0.5
        while True:
            children = self.snapshot()
            self.signal(children, signal.SIGTERM if time.monotonic() < graceful_until else signal.SIGKILL)
            if self.root_returncode is None and _exited_without_reaping(process):
                self.root_returncode = process.wait(timeout=0)
                self.waits.append({"pid": process.pid, "actual_wait": True, "returncode": self.root_returncode})
            self.reap_adopted(process.pid)
            if not Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_text().strip():
                if self.root_returncode is None:
                    raise RuntimeError("Original managed child disappeared without its wait")
                return self.root_returncode
            if time.monotonic() >= deadline:
                raise TimeoutError("Owned managed descendants remain after bounded cleanup")
            time.sleep(0.05)

    def close(self) -> None:
        for descriptor in self.descriptors.values():
            os.close(descriptor)


def _identity(process: subprocess.Popen, command: list[str]) -> dict:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
        api.GetProcessTimes.restype = wintypes.BOOL
        values = [wintypes.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(int(process._handle), *(ctypes.byref(value) for value in values)):
            raise ctypes.WinError(ctypes.get_last_error())
        creation = str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
        namespace = "windows"
        raw = subprocess.list2cmdline(command)
    else:
        status = Path(f"/proc/{process.pid}/stat").read_text()
        creation = status.rsplit(") ", 1)[1].split()[19]
        namespace = "linux:" + Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        raw = Path(f"/proc/{process.pid}/cmdline").read_bytes().decode(errors="replace").replace("\0", " ").rstrip()
    return {"pid": process.pid, "parent_pid": os.getpid(), "creation": creation,
            "namespace": namespace, "executable": sys.executable, "command_line": raw}


def _exited_without_reaping(process: subprocess.Popen) -> bool:
    if os.name == "nt":
        return process.poll() is not None
    # Keep the group leader unreaped until group cleanup, preventing PID/group reuse.
    return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _supervise(config_path: Path) -> int:
    config = _read(config_path)
    if not config:
        raise RuntimeError("Missing managed process launch configuration")
    directory, token = config_path.parent, config["token"]
    command = [sys.executable, "-u", "-B", str(Path(__file__).resolve()), "--child", str(config_path)]
    environment = os.environ.copy()
    environment.update(config["environment"])
    process = None
    job = None
    tree = None
    reason = "startup_error"
    error = None
    code = None
    identity = None
    tree_verified = False
    try:
        if os.name != "nt":
            tree = _LinuxTree()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=config["cwd"], env=environment,
                                   stdin=subprocess.DEVNULL, **options)
        if os.name == "nt":
            job = _WindowsJob(process)
        else:
            tree.snapshot()
        identity = _identity(process, command)
        _write(directory / "process_started.json", {"token": token, "identity": identity,
               "command": config["command"], "supervisor_pid": os.getpid(),
               "containment": "windows_job" if job else "linux_subreaper"}, exclusive=True)
        counter, changed = None, time.monotonic()
        admitted, last_reap = False, time.monotonic()
        while not _exited_without_reaping(process):
            request = _read(directory / "stop_request.json")
            if request and request.get("token") == token and request.get("identity") in (None, identity):
                reason = "requested_stop"
                break
            lease = _read(directory / "lease.json")
            if lease and lease.get("token") == token and lease.get("counter") != counter:
                counter, changed = lease["counter"], time.monotonic()
            if time.monotonic() - changed > config["lease_seconds"]:
                reason = "owner_lease_expired"
                break
            admission = _read(directory / "admission_request.json")
            if not admitted and admission == {"token": token, "identity": identity}:
                _write(directory / "admitted.json", {"token": token}, exclusive=True)
                admitted = True
            if tree and time.monotonic() - last_reap >= .5:
                tree.reap_adopted(process.pid)
                last_reap = time.monotonic()
            time.sleep(0.05)
        else:
            reason = "natural_exit"
    except BaseException as exc:  # noqa: BLE001 - preserve the original child wait even on interruption
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            try:
                if job:
                    job.terminate()
                elif tree:
                    code = tree.cleanup(process)
                    tree_verified = True
                elif process.poll() is None:
                    process.terminate()
                if code is None:
                    try:
                        code = process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait(timeout=10)
                if job:
                    job.wait_empty()
                    tree_verified = True
            except BaseException as exc:  # noqa: BLE001 - a failed wait must remain explicit in the exit receipt
                error = error or f"{type(exc).__name__}: {exc}"
        if job:
            job.close()
        if tree:
            code = tree.root_returncode
            tree.close()
        _write(directory / "process_exit.json", {"token": token, "identity": identity,
               "actual_wait": code is not None, "returncode": code,
               "tree_verified": tree_verified, "descendant_waits": tree.waits if tree else [],
               "reason": reason, "error": error}, exclusive=True)
    return 0 if error is None else 1


def _child(config_path: Path) -> None:
    config = _read(config_path)
    if not config:
        raise RuntimeError("Missing child launch configuration")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        stop = _read(config_path.parent / "stop_request.json")
        if stop and stop.get("token") == config["token"]:
            return
        admitted = _read(config_path.parent / "admitted.json")
        if admitted and admitted.get("token") == config["token"]:
            break
        time.sleep(0.05)
    else:
        raise RuntimeError("Managed child was not admitted into process containment")
    if config.get("kind") == "command":
        command = config["command"]
        if os.name != "nt":
            argv = ["/bin/sh", "-c", command] if config.get("shell") else command
            os.execvpe(argv[0], argv, os.environ)
        raise SystemExit(subprocess.call(command, shell=config.get("shell", False),
                                         stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr,
                                         creationflags=subprocess.CREATE_NO_WINDOW))
    sys.argv = config["command"][1:]
    sys.path[0] = str(Path(sys.argv[0]).resolve().parent)
    runpy.run_path(sys.argv[0], run_name="__main__")


def close_owned_sessions() -> None:
    for session in list(_SESSIONS.values()):
        outcome = session.stop()
        _write(session.directory / "owner_shutdown.json", {"token": session.token, **outcome})
        if not outcome.get("stopped"):
            print("Managed server shutdown is unverified: " + str(session.directory), file=sys.stderr)


def _windows_path(path: str | Path) -> str:
    value = str(path)
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", value)
    if match:
        return match[1].upper() + ":\\" + match[2].replace("/", "\\")
    if value.startswith("/"):
        result = subprocess.run(["wslpath", "-w", value], capture_output=True, text=True,
                                check=True, timeout=5)
        return result.stdout.strip()
    return value


@lru_cache(maxsize=1)
def _native_windows_python() -> str:
    candidates = [shutil.which("python.exe"), "/mnt/c/Program Files/Python312/python.exe",
                  "/mnt/c/Python312/python.exe"]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run([candidate, "-I", "-c",
                                     "import os,sys; print(os.name, sys.version_info.major, sys.version_info.minor)"],
                                    capture_output=True, text=True, timeout=10, check=True)
            if result.stdout.strip() == "nt 3 12":
                return candidate
        except (OSError, subprocess.SubprocessError):
            continue
    raise RuntimeError("Verified Windows Python 3.12 is required to own Windows command cleanup from WSL")


def launch_command_session(command: list[str] | str, *, cwd: Path, environment: dict[str, str] | None = None,
                           directory: Path | None = None, stdout=None, stderr=None,
                           text: bool = False, shell: bool = False, windows: bool = False,
                           cancel_event: threading.Event | None = None) -> ManagedServerProcess:
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("Command was cancelled before launch")
    argv = list(command) if not isinstance(command, str) else command
    if isinstance(argv, list) and argv and argv[0] == "/init":
        argv = argv[1:]
    cross_windows = os.name != "nt" and (windows or isinstance(argv, list) and bool(argv)
                                         and argv[0].lower().endswith(".exe"))
    interpreter = _native_windows_python() if cross_windows else sys.executable
    target_cwd = _windows_path(cwd) if cross_windows else str(cwd)
    if cross_windows and isinstance(argv, list):
        argv = [_windows_path(part) if part.startswith("/mnt/") and Path(part).exists() else part for part in argv]
    directory = directory or Path(__file__).resolve().parents[1] / ".cache/owned_commands" / uuid.uuid4().hex

    def spawn(config: Path):
        helper = str(Path(__file__).resolve())
        arguments = [interpreter, "-u", "-B", _windows_path(helper) if cross_windows else helper,
                     _windows_path(config) if cross_windows else str(config)]
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        return subprocess.Popen(arguments, cwd=cwd, stdout=stdout, stderr=stderr,
                                stdin=subprocess.DEVNULL, text=text, **options)

    return ManagedServerProcess.launch(directory, argv, target_cwd, environment or {}, spawn,
                                       kind="command", shell=shell, cancel_event=cancel_event)


atexit.register(close_owned_sessions)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        _child(Path(sys.argv[2]))
    elif len(sys.argv) == 2:
        raise SystemExit(_supervise(Path(sys.argv[1])))
    else:
        raise SystemExit("Usage: managed_process.py [--child] CONFIG")
