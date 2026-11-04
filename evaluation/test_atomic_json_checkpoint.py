"""Model-free checkpoint contracts, including real Windows deny-delete readers."""
from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import contextmanager, redirect_stderr
from ctypes import wintypes
from pathlib import Path
from unittest.mock import patch

from evaluation import disposable_followup_matrix as matrix


@contextmanager
def deny_delete_reader(path):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    lock = threading.Lock()

    def close():
        nonlocal handle
        with lock:
            if handle is not None:
                if not api.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                handle = None

    try:
        yield close
    finally:
        close()


def windows_conflict(code):
    error = PermissionError(errno.EACCES, "fixture replace conflict")
    error.winerror = code
    return error


class AtomicJSONCheckpointTests(unittest.TestCase):
    def setUp(self):
        root = matrix.ROOT / ".cache"
        self.temporary = tempfile.TemporaryDirectory(prefix="checkpoint-contract-", dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.assertTrue(self.root.is_relative_to(root.resolve()))
        self.path = self.root / "summary.json"
        self.path.write_text('{"status":"running"}', encoding="utf-8")
        self.stderr = io.StringIO()
        self.capture = redirect_stderr(self.stderr)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def assert_staged(self):
        self.assertEqual(json.loads(self.path.read_text()), {"status": "running"})
        pending = list(self.root.glob("*.pending"))
        self.assertEqual(len(pending), 1)
        self.assertEqual(json.loads(pending[0].read_text()), {"status": "paused"})

    def test_success_is_atomic_and_leaves_no_pending_file(self):
        matrix.save_json(self.path, {"status": "paused"})
        self.assertEqual(json.loads(self.path.read_text()), {"status": "paused"})
        self.assertEqual(list(self.root.glob("*.pending")), [])
        self.assertEqual(self.stderr.getvalue(), "")

    def test_known_windows_conflicts_retry_the_same_fsynced_file(self):
        original = Path.replace
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                calls = []

                def replace(source, destination, *, observed=calls, error_code=code):
                    observed.append((source, source.read_bytes()))
                    if len(observed) < 3:
                        raise windows_conflict(error_code)
                    return original(source, destination)

                with patch.object(Path, "replace", autospec=True, side_effect=replace), \
                        patch.object(matrix.time, "sleep") as sleep:
                    matrix.save_json(self.path, {"status": "paused"})
                self.assertEqual(len(calls), 3)
                self.assertEqual(len(set(calls)), 1)
                self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.05, 0.1])
                self.assertEqual(list(self.root.glob("*.pending")), [])
        self.assertIn("recovered after 2 retries", self.stderr.getvalue())

    def test_exhaustion_preserves_original_and_staged_json(self):
        error = windows_conflict(5)
        with (patch.object(Path, "replace", side_effect=error) as replace,
              patch.object(matrix.time, "sleep") as sleep,
              self.assertRaises(PermissionError) as caught):
            matrix.save_json(self.path, {"status": "paused"})
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 8)
        self.assertEqual(sleep.call_count, 7)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list), 1.35)
        self.assertIn("staged data retained", error.__notes__[0])
        self.assert_staged()

    def test_unrelated_errors_are_not_retried(self):
        for error in (PermissionError(errno.EACCES, "permanent access"),
                      windows_conflict(123), FileNotFoundError("missing destination")):
            with self.subTest(error=str(error)), \
                    patch.object(Path, "replace", side_effect=error) as replace, \
                    patch.object(matrix.time, "sleep") as sleep:
                with self.assertRaises(type(error)):
                    matrix.save_json(self.path, {"status": "paused"})
                self.assertEqual(replace.call_count, 1)
                sleep.assert_not_called()
        self.assertEqual(json.loads(self.path.read_text()), {"status": "running"})
        self.assertEqual(len(list(self.root.glob("*.pending"))), 3)

    def test_fsync_failure_does_not_attempt_replace(self):
        with (patch.object(matrix.os, "fsync", side_effect=OSError(errno.ENOSPC, "fixture disk full")),
              patch.object(Path, "replace") as replace,
              self.assertRaisesRegex(OSError, "fixture disk full")):
            matrix.save_json(self.path, {"status": "paused"})
        replace.assert_not_called()
        self.assert_staged()

    @unittest.skipUnless(os.name == "nt", "Requires the actual Windows coordinator host")
    def test_real_reader_conflict_recovers_after_reader_closes(self):
        blocked = threading.Event()
        closed = threading.Event()
        errors = []
        original = Path.replace

        def replace(source, destination):
            try:
                return original(source, destination)
            except PermissionError as exc:
                errors.append(exc.winerror)
                blocked.set()
                raise

        with deny_delete_reader(self.path) as close:
            def release():
                if blocked.wait(3):
                    close()
                    closed.set()

            worker = threading.Thread(target=release)
            worker.start()
            try:
                with patch.object(Path, "replace", autospec=True, side_effect=replace):
                    matrix.save_json(self.path, {"status": "paused"})
            finally:
                worker.join(4)
            self.assertFalse(worker.is_alive())
            self.assertTrue(closed.is_set())
        self.assertTrue(errors)
        self.assertTrue(set(errors) <= {5, 32, 33})
        self.assertEqual(json.loads(self.path.read_text()), {"status": "paused"})
        self.assertEqual(list(self.root.glob("*.pending")), [])
        self.assertIn("recovered", self.stderr.getvalue())

    @unittest.skipUnless(os.name == "nt", "Requires the actual Windows coordinator host")
    def test_real_permanent_reader_conflict_preserves_both_versions(self):
        with deny_delete_reader(self.path), self.assertRaises(PermissionError) as caught:
            matrix.save_json(self.path, {"status": "paused"})
        self.assertIn(caught.exception.winerror, (5, 32, 33))
        self.assertIn("exhausted 8 attempts", caught.exception.__notes__[0])
        self.assert_staged()


if __name__ == "__main__":
    unittest.main()
