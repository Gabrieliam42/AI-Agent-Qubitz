"""Real model-free process lifecycle fixtures, runnable on native Windows and Linux."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from runtime import managed_process as managed

ROOT = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Lifecycle fixture did not reach its expected state")


def process_exited(pid):
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[0] == "Z"
        except FileNotFoundError:
            return True
    import ctypes
    from ctypes import wintypes

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x100000, False, pid)
    if not handle:
        return ctypes.get_last_error() == 87
    try:
        return api.WaitForSingleObject(handle, 0) == 0
    finally:
        api.CloseHandle(handle)


class ManagedProcessLifecycle(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / ".cache/managed_server_lifecycle_repair_20261025_01/fixtures" / uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            if session.poll() is None:
                result = session.stop()
                self.assertTrue(result["actual_wait"], result)

    def launch(self, source, **options):
        script = self.directory / f"server_{len(self.sessions)}.py"
        script.write_text(source, encoding="utf-8")
        directory = self.directory / str(len(self.sessions))

        def spawn(config):
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
            with (directory / "server.log").open("xb") as log:
                return subprocess.Popen([sys.executable, "-u", "-B", str(Path(managed.__file__).resolve()),
                                         str(config)], stdout=log, stderr=subprocess.STDOUT, **flags)

        session = managed.ManagedServerProcess.launch(directory, [sys.executable, str(script)],
                                                      str(self.directory), {}, spawn, **options)
        self.sessions.append(session)
        return session

    def test_stop_records_both_actual_waits_and_reuses_the_same_request(self):
        session = self.launch("import time\nprint('READY', flush=True)\ntime.sleep(60)\n")
        result = session.stop()
        self.assertTrue(result["stopped"], result)
        self.assertTrue(result["actual_wait"])
        self.assertTrue(result["supervisor_actual_wait"])
        self.assertTrue(result["exit_receipt"]["tree_verified"])
        original = (session.directory / "stop_request.json").read_bytes()
        self.assertTrue(session.stop()["stopped"])
        self.assertEqual(original, (session.directory / "stop_request.json").read_bytes())
        self.assertTrue(process_exited(session.pid))

    def test_natural_exit_preserves_the_original_exit_code(self):
        session = self.launch("raise SystemExit(7)\n")
        wait_for(lambda: session.poll() is not None)
        self.assertEqual(session.poll(), 7)
        self.assertTrue(session.exit_receipt()["actual_wait"])
        self.assertEqual(session.exit_receipt()["reason"], "natural_exit")

    def test_invalid_stop_token_does_not_signal_the_owned_child(self):
        session = self.launch("import time\ntime.sleep(2)\n")
        managed._write(session.directory / "stop_request.json", {"token": "f" * 32, "identity": None}, exclusive=True)
        time.sleep(0.2)
        self.assertIsNone(session.poll())
        self.assertEqual(session.stop()["status"], "ownership_unknown")
        wait_for(lambda: session.poll() is not None)
        self.assertEqual(session.exit_receipt()["reason"], "natural_exit")

    def test_stop_timeout_is_pending_until_an_actual_wait_exists(self):
        session = self.launch("import time\ntime.sleep(60)\n")
        outcome = session.stop(timeout=0)
        self.assertFalse(outcome["stopped"])
        self.assertFalse(outcome["actual_wait"])
        self.assertEqual(outcome["status"], "stop_pending")
        wait_for(lambda: session.poll() is not None)
        self.assertTrue(session.stop()["stopped"])

    def test_expired_owner_lease_stops_only_the_original_child(self):
        with patch.object(managed, "_LEASE_SECONDS", 1.0):
            session = self.launch("import time\ntime.sleep(60)\n")
        session._closed.set()
        wait_for(lambda: session.poll() is not None)
        self.assertEqual(session.exit_receipt()["reason"], "owner_lease_expired")
        self.assertTrue(session.exit_receipt()["actual_wait"])

    def _descendant_case(self, option=""):
        descendant = self.directory / f"descendant_{len(self.sessions)}.json"
        source = (
            "import json, pathlib, subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)']"
            + option + ")\n"
            f"pathlib.Path({str(descendant)!r}).write_text(json.dumps(child.pid))\n"
            "time.sleep(60)\n"
        )
        unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            session = self.launch(source)
            wait_for(descendant.is_file)
            pid = json.loads(descendant.read_text())
            self.assertTrue(session.stop()["stopped"])
            wait_for(lambda: process_exited(pid))
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=10)

    def test_contained_descendants_exit_and_an_unrelated_process_survives(self):
        self._descendant_case()

    @unittest.skipIf(os.name == "nt", "POSIX process groups and sessions")
    def test_descendants_in_other_process_groups_and_sessions_are_owned(self):
        for option in (", process_group=0", ", start_new_session=True"):
            with self.subTest(option=option):
                self._descendant_case(option)

    def test_missing_supervisor_receipt_never_admits_the_server(self):
        directory = self.directory / "missing"

        def spawn(_config):
            return subprocess.Popen([sys.executable, "-c", "pass"])

        with self.assertRaisesRegex(RuntimeError, "without an ownership receipt"):
            managed.ManagedServerProcess.launch(directory, [sys.executable, "unused.py"],
                                                str(self.directory), {}, spawn, timeout=2)
        self.assertTrue(json.loads((directory / "supervisor_wait.json").read_text())["actual_wait"])

    def test_admission_failure_never_executes_server_code_and_still_waits(self):
        directory = self.directory / "admission_failure"
        directory.mkdir()
        marker = self.directory / "must_not_exist"
        script = self.directory / "blocked.py"
        script.write_text(f"open({str(marker)!r}, 'w').close()\n")
        config = directory / "process_launch.json"
        managed._write(config, {"token": "a" * 32, "command": [sys.executable, str(script)],
                                "cwd": str(self.directory), "environment": {}, "lease_seconds": 1}, exclusive=True)
        with patch.object(managed, "_identity", side_effect=OSError("fixture identity failure")):
            self.assertEqual(managed._supervise(config), 1)
        self.assertFalse(marker.exists())
        ended = json.loads((directory / "process_exit.json").read_text())
        self.assertTrue(ended["actual_wait"])
        self.assertIn("fixture identity failure", ended["error"])

    def test_slow_supervisor_cannot_admit_after_launch_timeout(self):
        directory = self.directory / "slow"
        marker = self.directory / "must_not_execute"
        script = self.directory / "blocked.py"
        script.write_text(f"open({str(marker)!r}, 'w').close()\n")

        def spawn(config):
            helper = str(Path(managed.__file__).resolve())
            code = f"import sys,time,runpy; time.sleep(.3); sys.argv=[{helper!r},{str(config)!r}]; runpy.run_path({helper!r},run_name='__main__')"
            return subprocess.Popen([sys.executable, "-B", "-c", code])

        with self.assertRaisesRegex(RuntimeError, "admission timeout"):
            managed.ManagedServerProcess.launch(directory, [sys.executable, str(script)],
                                                str(self.directory), {}, spawn, timeout=.05)
        wait_for(lambda: (directory / "supervisor_wait.json").is_file())
        self.assertFalse(marker.exists())
        self.assertFalse((directory / "admitted.json").exists())
        self.assertTrue(json.loads((directory / "process_exit.json").read_text())["actual_wait"])
        started = json.loads((directory / "process_started.json").read_text())
        original_stop = (directory / "stop_request.json").read_bytes()
        recovered = managed.ManagedServerProcess.recover(directory, started["token"])
        self.assertIsNotNone(recovered)
        self.assertTrue(recovered.stop()["stopped"])
        self.assertEqual((directory / "stop_request.json").read_bytes(), original_stop)

    def test_stop_deadline_includes_wait_lock_contention(self):
        directory = self.directory / "bounded_stop"
        directory.mkdir()
        session = managed.ManagedServerProcess(directory, "d" * 32)
        session.receipt = {"identity": {"pid": 123}}
        managed._write(directory / "process_exit.json", {
            "token": session.token, "identity": session.receipt["identity"], "actual_wait": True,
            "tree_verified": True, "returncode": 0, "error": None,
        })
        session.process = object()
        session._lock.acquire()
        release = threading.Thread(target=lambda: (time.sleep(.3), session._lock.release()))
        release.start()
        started = time.monotonic()
        result = session.stop(timeout=.05)
        elapsed = time.monotonic() - started
        release.join()
        self.assertFalse(result["stopped"])
        self.assertLess(elapsed, .2)

    def test_command_adapter_preserves_exit_status_and_separate_streams(self):
        session = managed.launch_command_session(
            [sys.executable, "-c", "import sys; print('out'); print('err',file=sys.stderr);sys.exit(5)"],
            cwd=self.directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.sessions.append(session)
        stdout, stderr = session.process.communicate(timeout=10)
        self.assertEqual(session.wait(timeout=10), 5)
        self.assertEqual(stdout.strip(), b"out")
        self.assertEqual(stderr.strip(), b"err")

    def test_pre_cancelled_request_never_creates_a_launch_configuration(self):
        event = threading.Event()
        event.set()
        directory = self.directory / "cancelled_before_launch"
        with self.assertRaises(InterruptedError), patch.object(managed.subprocess, "Popen") as popen:
            managed.launch_command_session([sys.executable, "-c", "raise SystemExit(99)"],
                                            cwd=self.directory, directory=directory, cancel_event=event)
        popen.assert_not_called()
        self.assertFalse(directory.exists())

    def test_terminal_failed_wait_receipt_returns_an_error(self):
        directory = self.directory / "failed_wait"
        directory.mkdir()
        session = managed.ManagedServerProcess(directory, "d" * 32)
        session.receipt = {"identity": {"pid": 123}}
        managed._write(directory / "process_exit.json", {
            "token": session.token, "identity": session.receipt["identity"], "actual_wait": False,
            "returncode": None, "error": "fixture failed wait", "tree_verified": False,
        })
        with self.assertRaisesRegex(RuntimeError, "fixture failed wait"):
            session.wait()


@unittest.skipIf(os.name == "nt", "Linux pidfd ancestry fixtures")
class ManagedTreeDiscovery(unittest.TestCase):
    @staticmethod
    def status(pid, parent, creation):
        return f"{pid} (fixture) " + " ".join(["S", str(parent), *(["0"] * 17), creation])

    def tree(self):
        tree = managed._LinuxTree.__new__(managed._LinuxTree)
        tree.descriptors, tree.waits = {}, []
        tree.root_returncode = None
        return tree

    def test_reused_parent_is_not_traversed(self):
        tree = self.tree()
        parent = os.getpid()

        def read(path, *_args, **_kwargs):
            value = str(path)
            if value == f"/proc/{parent}/task/{parent}/children":
                return "123"
            if value == "/proc/123/stat":
                return self.status(123, parent, "original")
            raise AssertionError("Reused parent was traversed: " + value)

        with patch.object(Path, "read_text", read), patch.object(managed.os, "pidfd_open", return_value=77), \
                patch.object(managed.os, "close"), patch.object(tree, "_parent_alive", return_value=False):
            self.assertEqual(tree.snapshot(), [(123, "original")])

    def test_disappearing_child_closes_its_uncommitted_pidfd(self):
        tree = self.tree()
        parent, reads = os.getpid(), []

        def read(path, *_args, **_kwargs):
            if str(path).endswith("/children"):
                return "123"
            reads.append(True)
            if len(reads) > 1:
                raise FileNotFoundError
            return self.status(123, parent, "original")

        with patch.object(Path, "read_text", read), patch.object(managed.os, "pidfd_open", return_value=77), \
                patch.object(managed.os, "close") as close:
            self.assertEqual(tree.snapshot(), [])
            close.assert_called_once_with(77)
            self.assertEqual(tree.descriptors, {})

    def test_parent_exit_during_child_capture_rejects_the_new_child(self):
        tree = self.tree()
        parent = os.getpid()

        def read(path, *_args, **_kwargs):
            values = {f"/proc/{parent}/task/{parent}/children": "123",
                      "/proc/123/stat": self.status(123, parent, "original"),
                      "/proc/123/task/123/children": "456",
                      "/proc/456/stat": self.status(456, 123, "unverified")}
            return values[str(path)]

        with patch.object(Path, "read_text", read), patch.object(managed.os, "pidfd_open", side_effect=[77, 88]), \
                patch.object(managed.os, "close") as close, \
                patch.object(tree, "_parent_alive", side_effect=[True, False]):
            self.assertEqual(tree.snapshot(), [(123, "original")])
            close.assert_called_once_with(88)
            self.assertNotIn((456, "unverified"), tree.descriptors)


if __name__ == "__main__":
    unittest.main()
