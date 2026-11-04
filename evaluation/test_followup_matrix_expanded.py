"""CPU-only v2 evaluator contracts; never import a variant or start a model server."""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix
import followup_matrix_expanded as expanded
from followup_matrix_cases import (
    Check,
    Turn,
    scenarios,
    workspace_manifest,
    write_fixture,
)


def document(fragment: str) -> str:
    return "## Overview\n\nKeep this overview.\n\n## Transfer\n\n" + fragment + "\n\n## Closing\n\nKeep this ending.\n"


def fetch_receipt(turn: int = 1, version: str = "25.0") -> dict:
    return {"turn": turn, "capture_origin": "wrapper_source_grounding", "url": expanded.SOURCE_URL,
            "result": {"requested_url": expanded.SOURCE_URL, "retrieved": True, "status": 200,
                       "body": json.dumps({"info": {"name": "packaging", "version": version,
                                                     "requires_python": ">=3.8"}})}}


def mcp_receipt(turn: int = 1, value: int = 7) -> dict:
    return {"turn": turn, "capture_origin": "mcp_host", "name": "call_project_mcp_tool",
            "arguments": {"tool_name": "typed_echo"}, "result": {"is_error": False, "structured_content": {
                "tool": "typed_echo", "returncode": 0, "result": {"isError": False,
                "structuredContent": {"value": value, "label": "kept-label", "ignored": "extra"}}}}}


def test_v2_retains_original_definitions_and_adds_gui_chains():
    catalog = expanded.catalog()
    original = scenarios()
    assert catalog[:len(original)] == tuple(replace(case, gui_only=True) for case in original)
    assert len(catalog) == len(original) + 5
    assert len({case.name for case in catalog}) == len(catalog)
    assert all(case.gui_only for case in catalog)
    assert matrix.selected_scenarios(None) == list(original)
    assert matrix.selected_scenarios(None, expanded.SUITE) == list(catalog)


def test_v2_prepares_distinct_same_named_files_and_mcp_servers(tmp_path):
    case = next(case for case in expanded.catalog() if case.name == "workspace_a_b_a_file_context")
    roots = expanded.prepare(case, tmp_path / "files")
    assert (roots["a"] / "shared.txt").read_text().startswith("workspace=A\n")
    assert (roots["b"] / "shared.txt").read_text().startswith("workspace=B\n")
    assert (roots["b"] / "protected.txt").read_text() == "leave this second workspace unchanged\n"
    mcp = next(case for case in expanded.catalog() if case.name == "workspace_a_b_a_mcp_bindings")
    roots = expanded.prepare(mcp, tmp_path / "mcp")
    assert (roots["a"] / "fixture_mcp.py").read_bytes() == (roots["b"] / "fixture_mcp.py").read_bytes()
    assert [turn.workspace for turn in mcp.turns] == ["a", "b", "a", "a"]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_generated_document_oracle_accepts_either_normal_line_ending(tmp_path, newline):
    paragraph = " ".join(["prose"] * 50)
    content = "".join(f"## {heading}{newline}{newline}{paragraph}{newline}{newline}"
                      for heading in ("Overview", "Transfer", "Closing"))
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "source.md", content)
    turn = Turn("Generate", (Check("document_sections", "source.md"),), ("source.md",))
    assert not expanded.verify(tmp_path, turn, {}, before, workspace_manifest(tmp_path), {}, {})[0]


@pytest.mark.parametrize("changed_outside,duplicated,stale", [(False, False, False), (True, False, False),
                                                            (False, True, False), (False, False, True)])
def test_transfer_uses_actual_source_and_preserves_destination(tmp_path, changed_outside, duplicated, stale):
    fragment = "This is a newly generated paragraph, not a predefined golden response."
    write_fixture(tmp_path, "source.md", document(fragment))
    write_fixture(tmp_path, "handbook.md", expanded.DESTINATION)
    turn = Turn("Transfer", (Check("fragment_transfer", "handbook.md", "source.md"),), ("handbook.md",))
    before = workspace_manifest(tmp_path)
    texts = expanded.snapshot_text(tmp_path, turn)
    prefix, _, suffix = expanded.marked(texts["handbook.md"])
    body = "stale paragraph" if stale else fragment * (2 if duplicated else 1)
    write_fixture(tmp_path, "handbook.md", prefix + body + "\n" + suffix + ("changed" if changed_outside else ""))
    issues, _ = expanded.verify(tmp_path, turn, {}, before, workspace_manifest(tmp_path), texts, {})
    assert bool(issues) == (changed_outside or duplicated or stale)


def test_revision_and_propagation_reject_stale_destination(tmp_path):
    old = " ".join(["original"] * 48)
    new = "independent checks " + " ".join(["revised"] * 48)
    write_fixture(tmp_path, "source.md", document(old))
    turn = Turn("Revise", (Check("fragment_revision", "source.md", "independent checks"),), ("source.md",))
    before = workspace_manifest(tmp_path)
    texts = expanded.snapshot_text(tmp_path, turn)
    write_fixture(tmp_path, "source.md", document(new))
    assert not expanded.verify(tmp_path, turn, {}, before, workspace_manifest(tmp_path), texts, {})[0]
    write_fixture(tmp_path, "handbook.md", expanded.START + old + "\n" + expanded.END)
    transfer = Turn("Refresh", (Check("fragment_transfer", "handbook.md", "source.md"),), ("handbook.md",))
    state = workspace_manifest(tmp_path)
    texts = expanded.snapshot_text(tmp_path, transfer)
    assert expanded.verify(tmp_path, transfer, {}, state, state, texts, {})[0]
    write_fixture(tmp_path, "handbook.md", expanded.START + new + "\n" + expanded.END)
    assert not expanded.verify(tmp_path, transfer, {}, state, workspace_manifest(tmp_path), texts, {})[0]


def test_claimed_source_without_captured_fetch_fails(tmp_path):
    write_fixture(tmp_path, "package.md", expanded.ONLINE_INITIAL)
    turn = Turn("Update", (Check("online_section", "package.md", ["name", "version"]),), ("package.md",))
    before = workspace_manifest(tmp_path)
    texts = expanded.snapshot_text(tmp_path, turn)
    prefix, _, suffix = expanded.marked(texts["package.md"])
    values = {"name": "packaging", "version": "25.0", "source_url": expanded.SOURCE_URL}
    write_fixture(tmp_path, "package.md", prefix + json.dumps(values) + "\n" + suffix)
    claimed = {"turn": 1, "assistant": "Fetched it. [Completion status: verified]"}
    assert expanded.verify(tmp_path, turn, claimed, before, workspace_manifest(tmp_path), texts, {})[0]
    actual = {"turn": 1, "source_fetches": [fetch_receipt()]}
    receipts = {}
    assert not expanded.verify(tmp_path, turn, actual, before, workspace_manifest(tmp_path), texts, receipts)[0]
    assert receipts[expanded.SOURCE_URL]["turn"] == 1


def test_online_followup_reuses_only_trajectory_receipt_and_exact_fields(tmp_path):
    write_fixture(tmp_path, "package.md", expanded.ONLINE_INITIAL)
    turn = Turn("Update", (Check("online_section", "package.md", ["name", "version", "requires_python"]),
                          Check("no_source_fetch")), ("package.md",))
    before = workspace_manifest(tmp_path)
    texts = expanded.snapshot_text(tmp_path, turn)
    prefix, _, suffix = expanded.marked(texts["package.md"])
    values = {"name": "packaging", "version": "99.9", "requires_python": ">=3.8", "source_url": expanded.SOURCE_URL}
    write_fixture(tmp_path, "package.md", prefix + json.dumps(values) + "\n" + suffix)
    receipt = expanded.fetched_source({"turn": 1, "source_fetches": [fetch_receipt(version="99.9")]}, expanded.SOURCE_URL)
    observed = {"turn": 2}
    assert not expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), texts,
                               {expanded.SOURCE_URL: receipt})[0]
    assert expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), texts, {})[0]
    observed["source_fetches"] = [fetch_receipt(turn=2)]
    assert expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), texts,
                           {expanded.SOURCE_URL: receipt})[0]


@pytest.mark.parametrize("change", ["old_turn", "wrong_url", "not_retrieved", "truncated", "http_error"])
def test_fetch_provenance_rejects_invalid_receipts(change):
    row = fetch_receipt()
    if change == "old_turn":
        row["turn"] = 0
    elif change == "wrong_url":
        row["result"]["requested_url"] = "https://example.com/"
    elif change == "not_retrieved":
        row["result"]["retrieved"] = False
    elif change == "truncated":
        row["result"]["truncated"] = True
    else:
        row["result"]["status"] = 503
    assert expanded.fetched_source({"turn": 1, "source_fetches": [row]}, expanded.SOURCE_URL) is None


def test_mcp_file_uses_real_current_nested_payload_not_claim(tmp_path):
    turn = Turn("Write result", (Check("mcp_file", "selected.json", "typed_echo"),), ("selected.json",))
    before = workspace_manifest(tmp_path)
    write_fixture(tmp_path, "selected.json", '{"value":7,"label":"kept-label"}')
    assert expanded.verify(tmp_path, turn, {"turn": 1, "assistant": "Done"}, before,
                           workspace_manifest(tmp_path), {}, {})[0]
    observed = {"turn": 1, "tools": [mcp_receipt()]}
    assert not expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), {}, {})[0]
    observed["turn"] = 2
    assert expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), {}, {})[0]
    observed["tools"] = [mcp_receipt(turn=2, value=11)]
    assert expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), {}, {})[0]
    write_fixture(tmp_path, "selected.json", '{"value":11,"label":"kept-label"}')
    assert not expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), {}, {})[0]
    observed["tools"][0]["result"]["structured_content"]["result"]["isError"] = True
    assert expanded.verify(tmp_path, turn, observed, before, workspace_manifest(tmp_path), {}, {})[0]


def test_unavailability_cannot_hide_mutation_or_false_success():
    observed = {"turn": 1, "source_fetches": [{"turn": 1, "url": expanded.SOURCE_URL, "result": {
        "status": "network_request_failed", "retrieved": False}}], "assistant": "Source unavailable."}
    assert expanded.source_unavailable(observed)
    issues = ["postcondition_failed:online_section:package.md", "partial_completion_report"]
    state = {"a": {"package.md": "hash"}}
    assert expanded.only_source_blockers(issues, observed, state, state)
    assert not expanded.only_source_blockers(issues, observed, state, {"a": {"package.md": "changed"}})
    observed["assistant"] = "Done. [Completion status: verified]"
    assert not expanded.only_source_blockers(issues, observed, state, state)


def test_workspace_context_oracle_rejects_cross_workspace_retrieval(tmp_path):
    turn = Turn("Read context", (Check("workspace_context", expected="old-marker"),))
    state = workspace_manifest(tmp_path)
    observed = {"retrieval_contexts": [{"workspace": str(tmp_path), "content": "current-marker"}]}
    assert not expanded.verify(tmp_path, turn, observed, state, state, {}, {})[0]
    observed["retrieval_contexts"][0]["content"] = "old-marker"
    assert expanded.verify(tmp_path, turn, observed, state, state, {}, {})[0]
    observed["retrieval_contexts"][0] = {"workspace": str(tmp_path / "other"), "content": "current-marker"}
    assert expanded.verify(tmp_path, turn, observed, state, state, {}, {})[0]
    _, evidence = expanded.verify(tmp_path, turn, {}, state, state, {}, {})
    assert evidence[-1]["retrieval_exercised"] is False


def test_v2_memory_is_private_per_workspace(tmp_path):
    class Store:
        def __init__(self, path):
            self.path = path

    base = SimpleNamespace(MemoryStore=Store)
    matrix.install_private_memory(base, tmp_path, separate_workspaces=True)
    a = base.MemoryStore(tmp_path / "a").path
    b = base.MemoryStore(tmp_path / "b").path
    assert a != b and a.is_relative_to(tmp_path) and b.is_relative_to(tmp_path)
    assert base.MemoryStore(tmp_path / "a").path == a


@pytest.mark.parametrize("mutate,runtime_preserved", [(False, True), (True, True), (False, False)])
def test_initial_selection_uses_normal_gui_switch_and_guards_fixtures(tmp_path, mutate, runtime_preserved):
    roots = {key: tmp_path / key for key in ("a", "b")}
    for root in roots.values():
        root.mkdir()
        write_fixture(root, "protected.txt", "protected\n")
    gui = SimpleNamespace(config=SimpleNamespace(workspace=tmp_path / "landing"))
    calls = []

    def switch(actual_gui, _base, target):
        calls.append(target)
        actual_gui.config.workspace = target
        if mutate:
            write_fixture(roots["b"], "protected.txt", "changed\n")
        return {"runtime_preserved": runtime_preserved}

    smoke = SimpleNamespace(_switch_workspace=switch)
    if mutate or not runtime_preserved:
        with pytest.raises(RuntimeError, match="mutated fixtures or changed"):
            matrix.select_workspace(gui, None, smoke, roots, "landing", "a", 1, guarded=True)
    else:
        receipt = matrix.select_workspace(gui, None, smoke, roots, "landing", "a", 1, guarded=True)
        assert receipt["from_workspace"] == "landing" and receipt["before_turn"] == 1
        assert receipt["mutations"] == {"a": [], "b": []}
    assert calls == [roots["a"]]


def test_new_suite_counts_source_blockers_separately():
    summary = {"contract": {"suite": expanded.SUITE}, "runs": [
        {"status": "blocked", "turns": [{"status": "blocked"}, {"status": "not_reached"}]}]}
    matrix.update_counts(summary)
    assert summary["counts"]["blocked"] == 1 and summary["turn_counts"]["blocked"] == 1
    assert summary["turn_counts"]["failed"] == 0
