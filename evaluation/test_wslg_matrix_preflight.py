"""CPU-only WSLg infrastructure receipts; never invoke a real WSL command."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix


@pytest.mark.parametrize("stdout,returncode,ready", [
    ("RDP backend: use_gfxredir = 1\n", 0, True),
    ("Failed: Input/output error\nRDP backend: use_gfxredir = 0\n", 0, False),
    ("RDP backend: use_gfxredir = 1\nWARN:COPY MODE\n", 0, False),
    ("RDP backend: use_gfxredir = 1\nInput/output error\n", 0, False),
    ("", 0, False),
    ("RDP backend: use_gfxredir = 1\n", 1, False),
])
def test_gui_preflight_distinguishes_health_from_pixel_verification(tmp_path, monkeypatch, stdout, returncode, ready):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 30
        return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)

    monkeypatch.setattr(matrix.subprocess, "run", run)
    args = SimpleNamespace(gui=True, distro="Ubuntu", output=tmp_path)
    if ready:
        receipt = matrix.wslg_preflight(args)
        assert receipt["status"] == "gfxredir_ready"
    else:
        with pytest.raises(RuntimeError, match="no trajectory was launched"):
            matrix.wslg_preflight(args)
        receipt = json.loads(next(tmp_path.glob("wslg_blocker_*.json")).read_text())
    assert len(calls) == 1 and "--shutdown" not in calls[0]
    assert not receipt["pixels_observed"] and not receipt["automatic_restart"]


def test_headless_preflight_does_not_touch_wsl(monkeypatch):
    monkeypatch.setattr(matrix.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("Headless run touched WSLg"))
    assert matrix.wslg_preflight(SimpleNamespace(gui=False)) is None
