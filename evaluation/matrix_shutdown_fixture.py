"""Harmless Windows subprocess fixture; never imports a variant or loads a model."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation.campaign_observation import read_json
from evaluation.complex_smoke_runtime import _exclusive_json
from evaluation.followup_campaign_control import process_identity, same_identity


def server(directory: Path, owner: str) -> int:
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        path = directory / "server_stop.json"
        if path.exists():
            if read_json(path) != {"owner": owner}:
                return 3
            return 0
        time.sleep(0.02)
    return 2


def worker(directory: Path, owner: str, mode: str) -> int:
    import tkinter

    from evaluation import disposable_followup_matrix as matrix

    root = tkinter.Tk()
    root.withdraw()
    gui = SimpleNamespace(root=root, busy=mode == "cancel")
    expected = read_json(directory / "server_identity.json")
    observer = matrix.Observer(directory)
    calls = []

    def cancel() -> None:
        calls.append("cancel")
        root.after(20, lambda: setattr(gui, "busy", False))

    def reset() -> None:
        if not same_identity(process_identity(expected["pid"]), expected):
            raise RuntimeError("Fixture server identity changed before cooperative shutdown")
        _exclusive_json(directory / "server_stop.json", {"owner": owner})
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            actual = process_identity(expected["pid"])
            if actual is None or (actual.get("creation_ticks")
                                  and actual["creation_ticks"] != expected["creation_ticks"]):
                calls.append("shutdown")
                return
            time.sleep(0.02)
        raise TimeoutError("Fixture Windows server did not exit")

    runner = SimpleNamespace(request_cancel=cancel, reset_runtime=reset)
    control = matrix.CooperativeWorkerStop(directory, owner, runner, gui, observer)
    try:
        _exclusive_json(directory / "fixture_ready.json", {"owner": owner})
        deadline = time.monotonic() + 40
        while gui.busy:
            root.update()
            if time.monotonic() >= deadline:
                raise TimeoutError("Fixture cancellation request not observed")
            time.sleep(0.01)
        matrix.wait_for_task_idle(runner, gui)
        matrix.shutdown_runtime_responsively(runner, gui, observer, timeout=25)
        _exclusive_json(directory / "fixture_finished.json", {"owner": owner, "calls": calls})
    finally:
        control.close()
        root.destroy()
    return 0


def check(directory: Path, owner: str, mode: str) -> dict:
    from evaluation import disposable_followup_matrix as matrix

    directory.mkdir(parents=True, exist_ok=False)
    _exclusive_json(directory / "launch.json", {"owner": owner})
    with (directory / "server.stdout.log").open("xb") as stdout, (directory / "server.stderr.log").open("xb") as stderr:
        process = subprocess.Popen([sys.executable, "-B", __file__, "server", str(directory), owner],
                                   stdout=stdout, stderr=stderr)
    child = None
    identity = None
    try:
        identity = process_identity(process.pid)
        if not same_identity(identity, identity):
            raise RuntimeError("Dummy server identity is incomplete")
        _exclusive_json(directory / "server_identity.json", identity)
        with (directory / "worker.stdout.log").open("xb") as stdout, (directory / "worker.stderr.log").open("xb") as stderr:
            child = subprocess.Popen([sys.executable, "-B", __file__, "worker", str(directory), owner,
                                      "--mode", mode], stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 25
        while not (directory / "fixture_ready.json").exists():
            if child.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("Dummy worker failed before readiness; inspect its captured stderr")
            time.sleep(0.05)
        waited = matrix.wait_for_worker_exit(child, directory, owner,
                                             "coordinator_stop" if mode == "cancel" else None, timeout=50)
        server_code = process.wait(timeout=10)
        current = process_identity(identity["pid"])
        calls = read_json(directory / "fixture_finished.json")["calls"]
        if (waited["exit_code"] != 0 or server_code != 0 or current is not None
                or calls != (["cancel", "shutdown"] if mode == "cancel" else ["shutdown"])):
            raise RuntimeError("Real Windows fixture failed shutdown/wait verification")
        result = {"mode": mode, "passed": True, "worker_wait": waited,
                  "server_identity": identity, "server_actual_wait": True, "server_exit_code": server_code,
                  "original_server_absent": current is None, "calls": calls, "models_loaded": False}
        _exclusive_json(directory / "review.json", result)
        return result
    finally:
        if process.poll() is None and not (directory / "server_stop.json").exists():
            if identity is None or not same_identity(process_identity(process.pid), identity):
                raise RuntimeError("Fixture cleanup identity is incomplete; refusing an action")
            _exclusive_json(directory / "server_stop.json", {"owner": owner})
        if child is not None:
            child.wait(timeout=75)
        process.wait(timeout=75)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("server", "worker", "check"))
    parser.add_argument("directory", type=Path)
    parser.add_argument("owner")
    parser.add_argument("--mode", choices=("normal", "cancel"), default="cancel")
    args = parser.parse_args()
    if args.action == "check":
        print(json.dumps(check(args.directory.resolve(), args.owner, args.mode)))
        return 0
    return server(args.directory, args.owner) if args.action == "server" else worker(
        args.directory, args.owner, args.mode)


if __name__ == "__main__":
    raise SystemExit(main())
