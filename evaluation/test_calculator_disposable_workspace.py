"""Model-free CLI checks for isolated calculator smoke workspaces."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from evaluation import smoke_create_calculator as smoke


def test_cli_passes_explicit_workspace_and_retention(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(smoke, "WORKSPACE", Path("unused-default"))
    monkeypatch.setattr(smoke, "coordinator", lambda args: seen.append((smoke.WORKSPACE, args)) or 0)
    monkeypatch.setattr(sys, "argv", ["smoke", "--workspace", str(tmp_path),
                                    "--keep-artifact", "--variants", "public-fixture.py"])
    assert smoke.main() == 0
    assert seen[0][0] == tmp_path.resolve()
    assert seen[0][1].keep_artifact


def test_child_uses_explicit_workspace(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(smoke, "WORKSPACE", Path("unused-default"))
    monkeypatch.setattr(smoke, "child", lambda args: seen.append((smoke.WORKSPACE, args.child)) or 0)
    monkeypatch.setattr(sys, "argv", ["smoke", "--workspace", str(tmp_path), "--child", "public-fixture.py"])
    assert smoke.main() == 0
    assert seen == [(tmp_path.resolve(), "public-fixture.py")]


@pytest.mark.parametrize("variants", [[], ["one.py", "two.py"]])
def test_retained_artifact_cannot_be_reused_by_another_variant(monkeypatch, tmp_path, variants):
    arguments = ["smoke", "--workspace", str(tmp_path), "--keep-artifact"]
    if variants:
        arguments.extend(["--variants", *variants])
    monkeypatch.setattr(smoke, "WORKSPACE", Path("unused-default"))
    monkeypatch.setattr(sys, "argv", arguments)
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2


def test_missing_workspace_is_rejected_before_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["smoke", "--workspace", str(tmp_path / "missing")])
    with pytest.raises(SystemExit) as error:
        smoke.main()
    assert error.value.code == 2
