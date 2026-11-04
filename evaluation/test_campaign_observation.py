"""Model-free recorder, live-reader, ownership and cursor failure contracts."""
from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stderr
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from evaluation import campaign_observation as observation
from evaluation import disposable_followup_matrix as matrix


@contextmanager
def deny_write_reader(path):
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                               wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    handle = api.CreateFileW(str(path), 0x80000000, 0x1, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        api.CloseHandle(handle)


class ObservationContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="observation-contract-", dir=matrix.ROOT / ".cache")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.path = self.root / "events.jsonl"
        self.path.write_bytes(b'[{"fixture":true}]\n')
        self.identity = {"pid": 123, "creation_ticks": "456", "executable": "fixture.exe",
                         "command_line": '"fixture.exe" --literal argument'}

    def test_reader_does_not_prevent_append(self):
        with observation.shared_reader(self.path) as reader:
            with self.path.open("ab") as writer:
                writer.write(b"next\n")
            self.assertTrue(reader.read().endswith(b"next\n"))

    def test_json_snapshot_closes_before_atomic_replace(self):
        replacement = self.root / "replacement.jsonl"
        replacement.write_bytes(b"replacement\n")
        self.assertEqual(observation.read_json(self.path), [{"fixture": True}])
        # Actual MoveFileEx can reject replacing an open target despite share-delete.
        replacement.replace(self.path)
        self.assertEqual(self.path.read_bytes(), b"replacement\n")

    def test_reader_closes_after_consumer_exception(self):
        with self.assertRaisesRegex(RuntimeError, "fixture"), observation.shared_reader(self.path) as reader:
            raise RuntimeError("fixture")
        self.assertTrue(reader.closed)

    def test_missing_reader_target_stays_an_error(self):
        with self.assertRaises(OSError), observation.shared_reader(self.root / "missing"):
            self.fail("Missing file was silently replaced")

    def test_only_complete_events_advance_cursor(self):
        first = b'{"sequence":1}\n'
        second = b'{"sequence":2}\n'
        self.path.write_bytes(first + second[:-2])
        events, offset = observation.event_delta(self.path, 0)
        self.assertEqual(events, [{"sequence": 1}])
        self.assertEqual(offset, len(first))
        with self.path.open("ab") as stream:
            stream.write(second[-2:])
        self.assertEqual(observation.event_delta(self.path, offset), ([{"sequence": 2}], len(first + second)))

    def test_bounded_delta_does_not_replay_or_skip(self):
        data = b'{"sequence":1}\n{"sequence":2}\n'
        self.path.write_bytes(data)
        events, offset = observation.event_delta(self.path, 0, 20)
        self.assertEqual(events, [{"sequence": 1}])
        self.assertEqual(observation.event_delta(self.path, offset)[0], [{"sequence": 2}])

    def test_truncation_and_oversize_events_hold_instead_of_resetting(self):
        for offset, limit in ((self.path.stat().st_size + 1, 100), (0, 3)):
            with self.subTest(offset=offset), self.assertRaises(RuntimeError):
                observation.event_delta(self.path, offset, limit)

    def test_absent_and_incomplete_identity_are_distinct(self):
        self.assertEqual(observation.process_observation(None, self.identity), "absent")
        for key in ("executable", "command_line", "creation_ticks"):
            actual = {**self.identity, key: None}
            self.assertEqual(observation.process_observation(actual, self.identity), "incomplete_metadata")

    def test_available_identity_conflicts_are_never_ignored(self):
        for key, value in (("pid", 124), ("creation_ticks", "457"), ("executable", "other.exe"),
                           ("command_line", '"fixture.exe" --different')):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                observation.process_observation({**self.identity, key: value}, self.identity)
        with self.assertRaises(RuntimeError):
            observation.process_observation({**self.identity, "creation_ticks": "457", "executable": None}, self.identity)

    def test_identity_ignores_only_trailing_command_whitespace(self):
        actual = {**self.identity, "command_line": self.identity["command_line"] + "  \r\n"}
        self.assertEqual(observation.process_observation(actual, self.identity), "matched")
        with self.assertRaises(RuntimeError):
            observation.process_observation({**actual, "command_line": " " + self.identity["command_line"]}, self.identity)

    def test_saved_full_argument_baseline_remains_binding(self):
        receipt = {key: self.identity[key] for key in ("pid", "creation_ticks", "executable")}
        with self.assertRaises(RuntimeError):
            observation.process_observation({**self.identity, "command_line": "changed"}, receipt, self.identity)

    def test_durable_append_recovers_failed_cursor_save_without_replay(self):
        history = self.root / "observations.jsonl"
        cursor = {"observation_id": "new", "case_offsets": {"fixture": {"event_offset": 42}}}
        row = {"observation_id": "new", "cursor_after": cursor}
        with self.assertRaises(PermissionError):
            observation.commit_observation(history, row, lambda _value: (_ for _ in ()).throw(PermissionError("fixture")))
        before = history.read_bytes()
        saved = []
        recovered = observation.recover_cursor(history, {"observation_id": "old"}, saved.append)
        self.assertEqual(recovered, cursor)
        self.assertEqual(saved, [cursor])
        self.assertEqual(history.read_bytes(), before)
        observation.recover_cursor(history, recovered, saved.append)
        self.assertEqual(len(saved), 1)

    def test_partial_history_is_preserved_and_never_replayed(self):
        history = self.root / "observations.jsonl"
        history.write_bytes(b'{"partial":')
        with self.assertRaisesRegex(RuntimeError, "Incomplete derived history"):
            observation.recover_cursor(history, {}, lambda _value: self.fail("Cursor advanced"))
        self.assertEqual(history.read_bytes(), b'{"partial":')

    def test_failure_preserves_exact_event_and_is_not_retried(self):
        observer = matrix.Observer(self.root)
        original_open = Path.open

        def denied(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError(errno.EACCES, "fixture denied append")
            return original_open(path, *args, **kwargs)

        with patch.object(Path, "open", denied), redirect_stderr(io.StringIO()), \
                self.assertRaises(matrix.EvidenceRecordingError):
            observer.emit("http_stream_line", line="exact fixture payload")
        receipt = json.loads((self.root / "evidence_recording_failure.json").read_text())
        self.assertEqual(receipt["failed_event"]["line"], "exact fixture payload")
        self.assertEqual(json.loads(receipt["serialized_event"]), receipt["failed_event"])
        self.assertFalse(receipt["capture_complete"])
        self.assertEqual(receipt["append_outcome"], "unknown_do_not_replay")
        with self.assertRaises(matrix.EvidenceRecordingError):
            observer.emit("not_a_successful_recovery")
        self.assertEqual(observer.sequence, 1)
        self.assertEqual(self.path.read_bytes(), b'[{"fixture":true}]\n')

    def test_partial_append_is_not_replayed(self):
        observer = matrix.Observer(self.root)
        original_open = Path.open

        @contextmanager
        def partial_writer():
            with original_open(self.path, "a", encoding="utf-8") as stream:
                class Writer:
                    def write(self, text):
                        stream.write(text[:7])
                        stream.flush()
                        raise OSError(errno.ENOSPC, "fixture partial write")
                yield Writer()

        def opened(path, *args, **kwargs):
            return partial_writer() if path == self.path else original_open(path, *args, **kwargs)

        with patch.object(Path, "open", opened), redirect_stderr(io.StringIO()), self.assertRaises(matrix.EvidenceRecordingError):
            observer.emit("fixture")
        self.assertEqual(self.path.read_bytes(), b'[{"fixture":true}]\n' + observer.recording_failure["serialized_event"][:7].encode())

    def test_failed_sidecar_still_preserves_full_event_on_stderr(self):
        observer = matrix.Observer(self.root)
        captured = io.StringIO()
        with (patch.object(Path, "open", side_effect=PermissionError("fixture append")),
              patch.object(matrix, "save_json", side_effect=OSError(errno.ENOSPC, "fixture receipt")),
              redirect_stderr(captured), self.assertRaises(matrix.EvidenceRecordingError)):
            observer.emit("fixture", payload="retained")
        self.assertIn('"payload": "retained"', captured.getvalue())
        self.assertIn("receipt_error", captured.getvalue())

    def test_recording_failure_overrides_task_and_gui_outcome(self):
        observer = SimpleNamespace(recording_failure={"capture_complete": False})
        for status in ("passed", "failed", "interrupted"):
            report = {"status": status, "issues": []}
            self.assertTrue(matrix.mark_recording_failure(report, observer))
            self.assertTrue(matrix.mark_recording_failure(report, observer))
            self.assertEqual(report["status"], "harness_error")
            self.assertEqual(report["issues"], ["evaluator_recording_failure"])
        report = {"status": "failed", "issues": ["actual_task_failure"]}
        self.assertFalse(matrix.mark_recording_failure(report, SimpleNamespace()))
        self.assertEqual(report["status"], "failed")

    def test_recording_failure_does_not_prevent_runtime_cleanup(self):
        observer = SimpleNamespace(emit=lambda *_args, **_kwargs: (_ for _ in ()).throw(matrix.EvidenceRecordingError("fixture")))
        cleaned = []
        runner = SimpleNamespace(reset_runtime=lambda: cleaned.append(True))
        with patch.object(matrix, "gui_root_exists", return_value=False):
            matrix.shutdown_runtime_responsively(runner, SimpleNamespace(), observer)
        self.assertEqual(cleaned, [True])

    def test_actual_child_classifies_stream_recording_failure_as_infrastructure(self):
        import complex_smoke_runtime as runtime

        import tmp_smoke_variant as smoke

        installed = []
        cleaned = []

        class Runner:
            def set_access_mode(self, _mode):
                pass

            def run_sync(self, _prompt, _callback):
                installed[0].emit("http_stream_line", line="fixture stream")
                raise AssertionError("Failed capture continued")

            def reset_runtime(self):
                cleaned.append(True)

        original_open = Path.open

        def opened(path, *args, **kwargs):
            if path == self.path and args and args[0] == "a" and installed and installed[0].sequence > 1:
                raise PermissionError("fixture stream append denied")
            return original_open(path, *args, **kwargs)

        args = SimpleNamespace(output=self.root, owner="a" * 32, suite="original-v1", scenario="create_edit_execute",
                               variant="unit_fixture_no_variant_import.py", tier="all", gui=False,
                               startup_allowance=7200, timeout=1800)
        base = SimpleNamespace(AgentRunner=lambda _config: Runner())
        with ExitStack() as stack:
            stack.enter_context(redirect_stderr(io.StringIO()))
            stack.enter_context(patch.object(runtime, "propagate_owner"))
            stack.enter_context(patch.object(matrix, "validate_child", return_value=self.root))
            stack.enter_context(patch.object(matrix, "install_private_memory"))
            stack.enter_context(patch.object(matrix.Observer, "install", lambda observer, _base: installed.append(observer)))
            stack.enter_context(patch.object(smoke, "_load_module", return_value=(SimpleNamespace(), None)))
            stack.enter_context(patch.object(smoke, "_build_wrapped_config", return_value=(SimpleNamespace(), base)))
            stack.enter_context(patch.object(matrix, "runner_snapshot", return_value={}))
            stack.enter_context(patch.object(matrix, "collect_logs", return_value=[]))
            stack.enter_context(patch.object(Path, "open", opened))
            status = matrix.child(args)
        report = json.loads((self.root / "result.json").read_text())
        self.assertEqual(status, matrix.STATUS_CODES["harness_error"])
        self.assertEqual(report["status"], "harness_error")
        self.assertEqual(report["turns"][0]["status"], "harness_error")
        self.assertIn("evaluator_recording_failure", report["issues"])
        self.assertEqual(report["turns"][0]["observed"]["generation_quality"]["status"], "incomplete_capture")
        self.assertEqual([turn["status"] for turn in report["turns"][1:]], ["not_reached", "not_reached"])
        self.assertEqual(cleaned, [True])

    def test_actual_coordinator_stops_after_harness_error(self):
        import followup_campaign_control as campaign_control
        import smoke_complex_tasks

        output = self.root / "campaign"
        variants = ["AI_Agent_Qubitz_Hypernova-60B-2605-Q5.py", "AI_Agent_Qubitz_GPT-OSS-120B_F16.py"]
        args = SimpleNamespace(output=output, variants=variants, tier="all", suite="original-v1",
                               scenarios=["create_edit_execute"], repeats=1, gui=False, timeout=1800,
                               startup_allowance=7200, distro="Ubuntu", resume=False)
        row = {"variant": variants[0], "scenario": "create_edit_execute", "status": "harness_error",
               "finished": "fixture", "issues": ["evaluator_recording_failure"],
               "turns": [{"turn": 1, "status": "harness_error"}], "output": str(output / "case")}
        with ExitStack() as stack:
            stack.enter_context(patch.object(matrix, "validate_output", return_value=output))
            stack.enter_context(patch.object(matrix, "variants", return_value=variants))
            stack.enter_context(patch.object(matrix, "freeze_sources", return_value={}))
            stack.enter_context(patch.object(smoke_complex_tasks, "protected_source_manifest", return_value={}))
            stack.enter_context(patch.object(matrix, "coverage_plan", return_value={}))
            stack.enter_context(patch.object(matrix, "grouped_results", return_value={}))
            stack.enter_context(patch.object(campaign_control, "process_identity", return_value=self.identity))
            stack.enter_context(patch.object(campaign_control, "stop_requested", return_value=False))
            launch = stack.enter_context(patch.object(matrix, "run_attempt", return_value=row))
            with patch("sys.stdout", io.StringIO()):
                status = matrix._coordinate(args)
        self.assertEqual(status, 2)
        self.assertEqual(launch.call_count, 1)
        summary = json.loads((output / "summary.json").read_text())
        self.assertEqual(summary["status"], "stopped_infrastructure")
        self.assertEqual(summary["runs"][0]["status"], "harness_error")
        self.assertEqual(summary["counts"]["harness_error"], 1)
        self.assertEqual(summary["turn_counts"]["harness_error"], 1)
        self.assertEqual(sum(summary["turn_counts"].values()), 1)


@unittest.skipUnless(os.name == "nt", "Actual Windows sharing contracts")
class WindowsObservationContracts(unittest.TestCase):
    setUp = ObservationContracts.setUp

    def test_real_deny_write_reader_is_an_infrastructure_failure(self):
        observer = matrix.Observer(self.root)
        with deny_write_reader(self.path), redirect_stderr(io.StringIO()), self.assertRaises(matrix.EvidenceRecordingError):
            observer.emit("fixture")
        receipt = json.loads((self.root / "evidence_recording_failure.json").read_text())
        self.assertIn("PermissionError", receipt["exception"])
        report = {"status": "failed", "issues": []}
        matrix.mark_recording_failure(report, observer)
        self.assertEqual(report["status"], "harness_error")

    def test_shared_reader_allows_actual_wsl_append(self):
        relative = self.path.relative_to(matrix.ROOT).as_posix()
        code = "import pathlib,sys; p=pathlib.Path(sys.argv[1]); f=p.open('a'); f.write('wsl_append\\n'); f.close()"
        with observation.shared_reader(self.path):
            checked = subprocess.run(["wsl.exe", "-d", "Ubuntu", "--cd", "/mnt/d/AI_Research/AI_Agent_Qubitz",
                                      ".venv/bin/python", "-B", "-c", code, relative],
                                     capture_output=True, text=True, check=False, timeout=30)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertTrue(self.path.read_bytes().endswith(b"wsl_append\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
