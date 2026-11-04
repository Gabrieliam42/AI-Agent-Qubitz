"""Owned command execution with actual waits and complete retained output."""
from __future__ import annotations

import json
import subprocess
import time

from runtime.invocation_receipts import retain_process_output
from runtime.managed_process import launch_command_session


def run_owned_command(command, *, cwd, timeout_seconds, windows=False, cancel_event=None):
    session = launch_command_session(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     windows=windows, cancel_event=cancel_event)
    process = session.process
    deadline = time.monotonic() + max(1, timeout_seconds)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            code = session.wait(timeout=10)
            receipt = {
                "started": session.receipt, "exit": session.exit_receipt(),
                "supervisor_actual_wait": process.returncode is not None,
                "directory": str(session.directory), "actual_wait": True,
                "output_retention": retain_process_output(session.directory, stdout or b"", stderr or b""),
            }
            if cancel_event is not None and cancel_event.is_set():
                error = InterruptedError("Command exited during cancellation; inspect its effects before replay")
                error.execution_receipt = receipt
                raise error
            return code, stdout or b"", stderr or b"", receipt
        except subprocess.TimeoutExpired:
            cancelled = cancel_event is not None and cancel_event.is_set()
            if not cancelled and time.monotonic() < deadline:
                continue
            outcome = session.stop()
            stdout, stderr = process.communicate(timeout=2)
            retention = retain_process_output(session.directory, stdout or b"", stderr or b"")
            if not outcome.get("stopped"):
                raise RuntimeError("Command cleanup unverified: " + json.dumps(outcome))
            error = (InterruptedError("Command cancelled; do not replay uncertain effects") if cancelled
                     else subprocess.TimeoutExpired(command, timeout_seconds, output=stdout, stderr=stderr))
            error.execution_receipt = {**outcome, "output_retention": retention}
            raise error
