"""CPU-only module composition, typed evidence, and coverage contracts."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.coverage_groups import (
    coverage_plan,
    grouped_results,
    runtime_parser_observation,
)
from evaluation.evidence_receipts import WindowsProcessIdentity, chain_pass_issues
from evaluation.gui_diagnostics import GuiDiagnosticsReceipt, collect_gui_diagnostics
from runtime import source
from runtime.symbols import symbol_sources
from runtime.variant_policy import user_variant_allowed

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("raw", [{}, {"pid": 1}, {"pid": True, "creation_ticks": "100",
                          "executable": "python.exe", "command_line": "python x.py"},
                         {"pid": 1, "creation_ticks": 100, "executable": "python.exe", "command_line": "python x.py"}])
def test_incomplete_process_receipt_never_establishes_ownership(raw):
    from evaluation.followup_campaign_control import same_identity

    assert not same_identity(raw, raw)
    with pytest.raises(ValueError):
        WindowsProcessIdentity.from_mapping(raw)


def test_identity_keeps_raw_command_and_only_ignores_trailing_whitespace():
    raw = {"pid": 12, "creation_ticks": "639275210773642830", "executable": "C:/Python/python.exe",
           "command_line": '"C:/Python/python.exe" -u exact.py  '}
    receipt = WindowsProcessIdentity.from_mapping(raw)
    assert receipt.command_line == raw["command_line"]
    assert receipt.matches(WindowsProcessIdentity.from_mapping({**raw, "command_line": raw["command_line"].rstrip()}))
    for changed in (" " + raw["command_line"], raw["command_line"].replace(" -u", "  -u")):
        assert not receipt.matches(WindowsProcessIdentity.from_mapping({**raw, "command_line": changed}))
    assert raw["command_line"].endswith("  ")


@pytest.mark.parametrize("change,issue", [
    ({"finished": None}, "unfinished_child_cannot_pass"),
    ({"exit_code": True}, "passed_receipt_with_nonzero_exit"),
    ({"exit_code": 1}, "passed_receipt_with_nonzero_exit"),
    ({"turns": [{"turn": 1, "status": "passed"}]}, "incomplete_chain_cannot_pass"),
    ({"turns": [{"turn": 1, "status": "passed"}] * 2}, "incomplete_chain_cannot_pass"),
    ({"turns": [{"turn": True, "status": "passed"}]}, "incomplete_chain_cannot_pass"),
])
def test_incomplete_intermediate_or_duplicate_turns_cannot_finalize(change, issue):
    raw = {"finished": "captured", "exit_code": 0,
           "turns": [{"turn": 1, "status": "passed"}, {"turn": 2, "status": "passed"}], **change}
    original = json.dumps(raw, sort_keys=True)
    assert issue in chain_pass_issues(raw, 2, finalized=True)
    assert json.dumps(raw, sort_keys=True) == original


def test_worker_check_does_not_demand_finished_before_shutdown():
    raw = {"turns": [{"turn": 1, "status": "passed"}]}
    assert chain_pass_issues(raw, 1, finalized=False) == ()
    assert set(chain_pass_issues(raw, 1, finalized=True)) == {
        "unfinished_child_cannot_pass", "passed_receipt_with_nonzero_exit",
    }


def test_gui_typed_collector_preserves_acknowledged_raw_order(tmp_path):
    log = tmp_path / "gui.jsonl"
    prefix = json.dumps({"event": "gui_started"}) + "\n"
    log.write_text(prefix + '{"incomplete":', encoding="utf-8")
    owner = SimpleNamespace(_qubitz_gui_log_path=log, _qubitz_gui_log_flushed_bytes=len(prefix.encode()),
                            _qubitz_flush_gui_log=lambda **_: True)
    receipt = collect_gui_diagnostics(owner)
    assert isinstance(receipt, GuiDiagnosticsReceipt)
    assert receipt.to_dict()["events"] == [{"event": "gui_started"}]
    assert receipt.behavior_issues == []
    assert log.read_text() == prefix + '{"incomplete":'


@pytest.mark.parametrize("events,counts,issues,warnings", [
    ([{"event": "gui_started"}], (0, 0, 0, 0), [], []),
    ([{"event": "gui_heartbeat_stalled", "timestamp": "20"},
      {"event": "gui_heartbeat_recovered", "timestamp": "10"}], (1, 1, 1, 0), [], ["gui_heartbeat_stall_recovered"]),
    ([{"event": "gui_heartbeat_recovered"}, {"event": "gui_heartbeat_stalled"}],
     (1, 1, 0, 1), ["gui_heartbeat_stalled"], []),
    ([{"event": "gui_heartbeat_stalled", "sequence": 1, "gui_log_session": "a", "monotonic_seconds": 1.0},
      {"event": "gui_heartbeat_recovered", "sequence": 1, "gui_log_session": "b", "monotonic_seconds": 1.0}],
     (1, 1, 0, 1), ["gui_heartbeat_stalled"], []),
    ([{"event": "gui_heartbeat_stalled"}, {"event": "gui_event", "kind": "done"}],
     (1, 0, 1, 0), [], ["gui_heartbeat_stall_recovered"]),
    ([{"event": "gui_event", "kind": "error"}], (0, 0, 0, 0), ["gui_logged_error"], []),
    ([], (0, 0, 0, 0), ["gui_diagnostics_empty"], []),
])
def test_extracted_collector_preserves_counts_and_raw_order(tmp_path, events, counts, issues, warnings):
    log = tmp_path / "gui.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events))
    owner = SimpleNamespace(_qubitz_gui_log_path=log, _qubitz_flush_gui_log=lambda **_: True)
    receipt = collect_gui_diagnostics(owner)
    assert receipt.events == events
    assert (receipt.stall_count, receipt.recovery_count, receipt.recovered_stall_count,
            receipt.unresolved_stall_count) == counts
    assert receipt.behavior_issues == issues and receipt.warnings == warnings


def test_real_all_variant_sources_include_exact_logger_module():
    from runtime.paths import variant_paths

    module = (ROOT / "runtime/gui_diagnostics.py").read_text()
    for path in variant_paths(ROOT):
        effective = source.read_variant_source(path)
        assert module in effective
        assert "from runtime.gui_diagnostics" not in effective
        functions = {node.name for node in ast.parse(effective).body if isinstance(node, ast.FunctionDef)}
        assert {"_qubitz_gui_log_enqueue", "_qubitz_gui_log_write", "_qubitz_gui_log_stop"} <= functions
        compile(effective, str(path), "exec")


def test_module_symbol_executes_without_shared_runtime_or_mutable_globals(tmp_path):
    registry = symbol_sources("wrapper", ROOT / "runtime")
    text = registry["bf27a6e31914ad01"]
    first, second = {"__name__": "export_one"}, {"__name__": "export_two"}
    exec(compile(text, "<standalone-diagnostics>", "exec"), first)  # noqa: S102 - trusted project module source
    exec(compile(text, "<standalone-diagnostics>", "exec"), second)  # noqa: S102 - trusted project module source
    assert first["_qubitz_gui_log_enqueue"].__globals__ is first
    assert second["_qubitz_gui_log_enqueue"].__globals__ is second
    assert first["_qubitz_gui_log_enqueue"] is not second["_qubitz_gui_log_enqueue"]
    assert not any(isinstance(value, dict) for key, value in first.items() if key != "__builtins__")


@pytest.mark.parametrize("registry", [
    "# <QUBITZ_SHARED_MODULE:base:bf27a6e31914ad01:module.py>\n",
    "# <QUBITZ_SHARED_MODULE:wrapper:bf27a6e31914ad01:../escaped.py>\n",
    "# <QUBITZ_SHARED_MODULE:wrapper:bf27a6e31914ad01:module.py>\n" * 2,
])
def test_invalid_module_registry_is_rejected(tmp_path, registry):
    (tmp_path / "wrapper.shared.py.in").write_text(registry)
    with pytest.raises(ValueError):
        symbol_sources("wrapper", tmp_path)


def test_module_alias_outside_package_is_rejected(tmp_path):
    directory = tmp_path / "runtime"
    directory.mkdir(parents=True)
    (directory / "wrapper.shared.py.in").write_text(
        "# <QUBITZ_SHARED_MODULE:wrapper:bf27a6e31914ad01:module.py>\n")
    outside = tmp_path / "outside.py"
    outside.write_text("raise RuntimeError('never execute')\n")
    (directory / "module.py").symlink_to(outside)
    with pytest.raises(ValueError, match="real runtime-package"):
        symbol_sources("wrapper", directory)


def test_universal_module_fingerprint_exclusion_never_hides_variant_drift():
    import qubitz_variant_builder as builder
    from runtime.paths import DEFAULT_VARIANT, variant_path

    path = variant_path(DEFAULT_VARIANT)
    original = source.read_variant_source(path)
    module = (ROOT / "runtime/gui_diagnostics.py").read_text()
    expected = json.loads((ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    saved = next(row for row in expected if row["filename"] == path.name)
    assert builder.profile_from_text(path, original) == saved
    altered = original.replace("_gui_log_queue.Full:", "_gui_log_queue.Empty:", 1)
    assert altered != original
    assert builder.profile_guard_fields(altered) != builder.profile_guard_fields(original)
    assert builder.profile_guard_fields(original + module) != builder.profile_guard_fields(original)


def test_coverage_public37_keeps_configured_tiers_separate_from_model_size():
    profiles = json.loads((ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    names = [row["filename"] for row in profiles if user_variant_allowed(row["filename"])]
    plan = coverage_plan(ROOT, names)
    assert plan["execution_order"] == names and list(plan["variants"]) == names
    assert len(plan["variants"]) == 40
    assert {entry["vram_profile_tier_gb"] for entry in plan["variants"].values()} == {12, 24, 48}
    assert plan["variants"]["AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py"]["vram_profile_tier_gb"] == 24
    assert all(entry["chat_format"] is None for entry in plan["variants"].values())
    assert (plan["variants"]["AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py"]["backend_group"]
            != plan["variants"]["AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"]["backend_group"])


def test_parser_observation_never_calls_a_backend_or_invents_a_family():
    runner = SimpleNamespace(_get_llm=lambda: pytest.fail("Coverage attempted startup"))
    assert runtime_parser_observation(runner)["source"] == "unobserved"
    runner._llm = SimpleNamespace(_qubitz_reasoning_capabilities={"chat_format": "exact-native-format",
                                 "reasoning_format": "unknown", "supports_preserve_reasoning": False})
    observed = runtime_parser_observation(runner)
    assert observed["chat_format"] == "exact-native-format" and observed["reasoning_format"] is None
    assert observed["capabilities"]["supports_preserve_reasoning"] is False


def test_groups_do_not_rewrite_or_merge_individual_outcomes():
    names = ["one.py", "two.py"]
    plan = {"variants": {name: {"vram_profile_tier_gb": 24, "backend_group": "llama.cpp/shared"} for name in names}}
    rows = [{"variant": name, "status": status, "turns": [{"observed": {"runtime": {
        "parser_observation": {"chat_format": "native", "reasoning_format": None}}}}]}
            for name, status in zip(names, ("passed", "failed"), strict=True)]
    original = json.dumps(rows)
    groups = grouped_results(plan, rows)
    assert groups[0]["status_counts"] == {"passed": 1, "failed": 1}
    assert groups[0]["variants"] == names
    assert json.dumps(rows) == original


def test_failed_turns_with_missing_parser_data_remain_unknown():
    rows = [{"variant": "fixture.py", "status": "failed", "turns": [
        {"observed": None}, {"observed": {"runtime": {"parser_observation": {"chat_format": []}}}},
    ]}]
    original = json.dumps(rows)
    groups = grouped_results({}, rows)
    assert len(groups) == 1 and groups[0]["chat_format"] is None
    assert groups[0]["status_counts"] == {"failed": 1}
    assert json.dumps(rows) == original


def test_full_v5_dry_plan_covers_current_catalog_without_execution(monkeypatch, capsys):
    from evaluation import disposable_followup_matrix as matrix

    monkeypatch.setattr(sys, "argv", ["matrix", "--suite", "expanded-v5", "--gui"])
    monkeypatch.setattr(matrix, "coordinate", lambda *_: pytest.fail("Dry plan executed a campaign"))
    assert matrix.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["variants"] == list(matrix.ALL_VARIANTS)
    assert plan["trajectory_count"] == 1080 and plan["turn_count"] == 3520
    assert plan["execution_started"] is False
    assert plan["coverage_plan"]["execution_order"] == plan["variants"]
