"""Real owned-process receipts retain output and do not replay commands after failure."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from runtime.command_receipts import run_owned_command


def test_large_output_is_complete_even_when_tool_display_would_truncate(tmp_path):
    code, stdout, stderr, receipt = run_owned_command(
        [sys.executable, "-u", "-B", "-c", "import sys;sys.stdout.write('x'*40000);sys.stderr.write('failure detail');sys.exit(7)"],
        cwd=tmp_path, timeout_seconds=15,
    )
    assert code == 7 and stdout == b"x" * 40000 and stderr == b"failure detail"
    assert receipt["actual_wait"] and receipt["exit"]["actual_wait"] and receipt["supervisor_actual_wait"]
    assert receipt["output_retention"]["complete"]
    files = receipt["output_retention"]["files"]
    assert Path(files[0]["path"]).read_bytes() == stdout
    assert Path(files[1]["path"]).read_bytes() == stderr


def test_timeout_preserves_actual_wait_and_output_without_replay(tmp_path):
    marker = tmp_path / "execution_count.txt"
    source = ("from pathlib import Path;import time;"
              "p=Path('execution_count.txt');p.write_text(p.read_text()+'x' if p.exists() else 'x');"
              "print('before timeout',flush=True);time.sleep(10)")
    with pytest.raises(subprocess.TimeoutExpired) as stopped:
        run_owned_command([sys.executable, "-u", "-B", "-c", source], cwd=tmp_path, timeout_seconds=1)
    receipt = stopped.value.execution_receipt
    assert receipt["actual_wait"] and receipt["supervisor_actual_wait"]
    assert receipt["exit_receipt"]["actual_wait"] and receipt["exit_receipt"]["tree_verified"]
    assert marker.read_text() == "x"
    files = receipt["output_retention"]["files"]
    assert Path(files[0]["path"]).read_bytes() == b"before timeout\n"
