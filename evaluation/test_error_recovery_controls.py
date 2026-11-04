"""Captured transient-read and GUI arming failures; no model execution."""
from __future__ import annotations

import errno
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import disposable_followup_matrix as matrix
from evaluation.followup_matrix_cases import Turn


class FaultingReader:
    def __init__(self, inner, count):
        self.inner, self.count = inner, count

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        return self.inner.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def readline(self, *args):
        if self.count[0]:
            self.count[0] -= 1
            raise OSError(errno.ENODATA, "No data available")
        return self.inner.readline(*args)


@pytest.mark.parametrize("failures", [1, 3, 4])
def test_transient_capture_read_retains_offset_and_has_a_hard_budget(tmp_path, monkeypatch, failures):
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"kind": "http_request", "turn": 1, "request_id": "one"}) + "\n")
    original = Path.open
    count = [failures]
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: FaultingReader(original(self, *a, **k), count)
                        if self == path and a == ("rb",) else original(self, *a, **k))
    state = {}
    for _ in range(min(failures, 3)):
        assert matrix.generation_cancel_evidence(path, 1, state) is None
        assert state.get("offset", 0) == 0
    if failures > 3:
        with pytest.raises(OSError, match="No data available"):
            matrix.generation_cancel_evidence(path, 1, state)
    else:
        assert matrix.generation_cancel_evidence(path, 1, state) is None
        assert state["offset"] == path.stat().st_size


def test_cancel_poll_stays_armed_before_send_and_surfaces_control_fault(tmp_path, monkeypatch):
    callbacks = []
    root = SimpleNamespace(after=lambda _ms, callback: callbacks.append(callback) or len(callbacks),
                           after_cancel=lambda _timer: None, winfo_exists=lambda: True)
    runner = SimpleNamespace(history=[], config=SimpleNamespace(), request_cancel=lambda: None)
    gui = SimpleNamespace(root=root, busy=False, access_mode_var=SimpleNamespace(set=lambda _: None),
                          _apply_access_mode=lambda: None)
    observer = matrix.Observer(tmp_path)
    observer.turn = 1
    def send(*_args):
        callbacks.pop(0)()  # First callback before ordinary Send marks busy.
        assert callbacks, "Cancellation polling must remain armed while Send initializes"
        gui.busy = True
        callbacks.pop(0)()
        return {"completed": True, "timed_out": False}
    calls = [0]
    def trigger(*_args):
        calls[0] += 1
        if calls[0] > 1:
            raise OSError(errno.EIO, "permanent capture failure")
    monkeypatch.setattr(matrix, "generation_cancel_evidence", trigger)
    result = matrix.execute_turn(Turn("Explain.", (), cancel=True, cancel_on_generation=True), runner, gui,
                                 SimpleNamespace(_send_prompt=send), tmp_path, observer, 10)
    assert result["evaluation_control_failure"]["exception_type"] == "OSError"
    rows = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(row["kind"] == "evaluation_control_error" for row in rows)
