"""CPU-only contracts for repeated unchanged edit snapshots."""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = variant_path("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py", ROOT)


@pytest.fixture(scope="module")
def variant():
    spec = importlib.util.spec_from_file_location("snapshot_recovery_variant", REFERENCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)
    sys.modules.pop(module._APP.base.__name__, None)


def broker_for(variant, workspace: Path, prompt: str):
    broker = variant._ToolPermissionBroker(variant._APP.base, workspace, workspace)
    broker.set_access_mode(variant.ACCESS_MODE_FULL)
    broker.begin_turn(prompt, None)
    broker.completion_interrupt_enabled = True
    return broker


def record_snapshot(broker, path: Path, *, start=1, end=10, name=None) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    last = min(end, len(lines))
    broker.record_result(
        "read_file_snapshot",
        {"path": name or path.name, "start_line": start, "end_line": end},
        SimpleNamespace(
            isError=False,
            structuredContent={
                "status": "text_snapshot", "sha256": digest,
                "start_line": start, "end_line": last,
                "content": "".join(lines[start - 1:last]),
            },
            content=[],
        ),
    )


def test_third_unchanged_edit_snapshot_requests_recovery_once(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("price = 1\n", encoding="utf-8")
    prompt = "Edit pricing.py to fix its price calculation."
    broker = broker_for(variant, tmp_path, prompt)
    assert "changed_files" in broker.required_postconditions
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)

    record_snapshot(broker, target)
    record_snapshot(broker, target)
    assert not recoveries
    record_snapshot(broker, target)
    record_snapshot(broker, target)
    assert recoveries == [True]
    assert broker.transaction_recovery_required


def test_changed_snapshot_starts_new_count(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("price = 1\n", encoding="utf-8")
    broker = broker_for(variant, tmp_path, "Edit pricing.py to fix its price calculation.")
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)

    record_snapshot(broker, target)
    record_snapshot(broker, target)
    target.write_text("price = 2\n", encoding="utf-8")
    record_snapshot(broker, target)
    record_snapshot(broker, target)
    assert not recoveries
    record_snapshot(broker, target)
    assert recoveries == [True]


def test_read_only_request_does_not_request_edit_recovery(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("price = 1\n", encoding="utf-8")
    broker = broker_for(variant, tmp_path, "Explain what pricing.py does.")
    assert "changed_files" not in broker.required_postconditions
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)

    for _ in range(4):
        record_snapshot(broker, target)
    assert not recoveries


def test_changed_requested_limits_and_path_spelling_do_not_hide_repeats(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("price = 1\n", encoding="utf-8")
    broker = broker_for(variant, tmp_path, "Edit pricing.py to fix its price calculation.")
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)

    record_snapshot(broker, target, end=200)
    record_snapshot(broker, target, end=400, name="./pricing.py")
    record_snapshot(broker, target, end=4000, name=str(target))
    assert recoveries == [True]


def test_genuinely_new_returned_lines_reset_repetition(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("\n".join(f"price_{i} = {i}" for i in range(6)) + "\n", encoding="utf-8")
    broker = broker_for(variant, tmp_path, "Edit pricing.py to fix its price calculation.")
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)

    record_snapshot(broker, target, start=1, end=2)
    record_snapshot(broker, target, start=3, end=4)
    record_snapshot(broker, target, start=5, end=6)
    assert not recoveries
    record_snapshot(broker, target, start=1, end=4)
    assert not recoveries
    record_snapshot(broker, target, start=2, end=5)
    assert recoveries == [True]


def test_empty_file_snapshots_still_detect_repetition(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_bytes(b"")
    broker = broker_for(variant, tmp_path, "Edit pricing.py to fix its price calculation.")
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)
    for end in (200, 400, 4000):
        record_snapshot(broker, target, end=end)
    assert recoveries == [True]


def test_new_turn_discards_old_snapshot_coverage(variant, tmp_path):
    target = tmp_path / "pricing.py"
    target.write_text("price = 1\n", encoding="utf-8")
    prompt = "Edit pricing.py to fix its price calculation."
    broker = broker_for(variant, tmp_path, prompt)
    record_snapshot(broker, target)
    record_snapshot(broker, target)
    broker.end_turn()
    assert not broker.snapshot_read_coverage
    broker.begin_turn(prompt, None)
    broker.completion_interrupt_enabled = True
    recoveries = []
    broker.transaction_recovery_callback = lambda: recoveries.append(True)
    record_snapshot(broker, target)
    assert not recoveries


@pytest.fixture
def runner(variant, tmp_path, monkeypatch):
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    monkeypatch.setattr(variant._APP.base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("CPU test attempted to load a model"))
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    yield instance
    instance.reset_runtime()


def selected_tool(runner, prompt):
    choice = runner._wrapper_no_progress_tool_choice(prompt, "")
    return choice["function"]["name"] if choice else None


def test_batch_patch_recovery_waits_for_all_current_snapshots(variant, runner, tmp_path):
    first, second = tmp_path / "alpha.py", tmp_path / "beta.py"
    first.write_text("a = 1\n", encoding="utf-8")
    second.write_text("b = 1\n", encoding="utf-8")
    prompt = "Edit alpha.py and beta.py to fix their calculations."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)

    assert selected_tool(runner, prompt) == "read_file_snapshot"
    assert set(broker._pending_edit_snapshot_paths) == {str(first), str(second)}
    record_snapshot(broker, first)
    assert selected_tool(runner, prompt) == "read_file_snapshot"
    assert broker._pending_edit_snapshot_paths == (str(second),)
    record_snapshot(broker, second)
    assert selected_tool(runner, prompt) == "apply_text_patches"
    assert not broker._pending_edit_snapshot_paths
    first.write_text("a = 2\n", encoding="utf-8")
    assert selected_tool(runner, prompt) == "read_file_snapshot"
    assert broker._pending_edit_snapshot_paths == (str(first),)


def test_single_patch_recovery_does_not_use_an_unrelated_snapshot(variant, runner, tmp_path):
    target, other = tmp_path / "alpha.py", tmp_path / "notes.txt"
    target.write_text("a = 1\n", encoding="utf-8")
    other.write_text("notes\n", encoding="utf-8")
    prompt = "Edit alpha.py to fix its calculation."
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    record_snapshot(broker, other)
    assert selected_tool(runner, prompt) == "read_file_snapshot"
    assert broker._pending_edit_snapshot_paths == (str(target),)
    record_snapshot(broker, target)
    assert selected_tool(runner, prompt) == "apply_text_patch"
    assert target.read_text(encoding="utf-8") == "a = 1\n"
