"""An identity-bound dispatch cannot retain the prepared not-started label."""
from __future__ import annotations

import json
import subprocess

from evaluation import campaign_lifecycle as lifecycle
from evaluation import disposable_followup_matrix as matrix
from evaluation.test_campaign_lifecycle_contracts import command, identity


def test_running_publication_records_execution_started(tmp_path, monkeypatch):
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    state = tmp_path / "work_state.json"
    state.write_text(json.dumps({"output": "run", "status": "prepared", "execution_started": False}))
    published = []
    original = matrix.save_json

    def save(path, value):
        if path == state:
            published.append(value.copy())
        return original(path, value)

    monkeypatch.setattr(matrix, "save_json", save)
    processes = []

    def launch(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    def current_identity(pid):
        return identity(pid) if processes[-1].returncode is None else None

    assert lifecycle.supervise(command(tmp_path / "run"), tmp_path / "run",
                               identity=current_identity, popen=launch, control_state=state) == 0
    running = [value for value in published if value.get("status") == "running"]
    assert len(running) == 1 and running[0]["execution_started"] is True
    assert json.loads(state.read_text())["execution_started"] is True
