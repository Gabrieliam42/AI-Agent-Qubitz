"""The GUI may consume done synchronously; state must already be retained."""
from __future__ import annotations

from types import SimpleNamespace

import tmp_smoke_variant as smoke


def test_snapshot_is_saved_before_done_is_published():
    state = {"turn_id": "original"}
    observed = []

    def publish(item):
        if item[1] == "done":
            observed.append(dict(gui._smoke_runs[1]["runtime"]))
            state["turn_id"] = "queued"

    def worker(_prompt, run_id):
        gui.event_queue.put((run_id, "answer", "original answer"))
        gui.event_queue.put((run_id, "done", ""))

    gui = SimpleNamespace(_append_transcript=lambda *_: None, event_queue=SimpleNamespace(put=publish),
                          _worker_run_tagged=worker, _smoke_snapshot_runner=lambda: dict(state))
    smoke._attach_event_capture(gui)
    gui._worker_run_tagged("task", 1)
    assert observed == [{"turn_id": "original"}]
    assert gui._smoke_runs[1]["runtime"] == {"turn_id": "original"}
    assert state == {"turn_id": "queued"}
