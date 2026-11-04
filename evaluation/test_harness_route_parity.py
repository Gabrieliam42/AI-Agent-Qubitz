"""Route declaration and block-selection contracts; no model server required."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import test_repeated_snapshot_recovery as snapshots

import qubitz_release_gate as gate
from runtime import read_variant_source
from runtime.paths import variant_path

variant = snapshots.variant
ROOT = Path(__file__).resolve().parents[1]


def test_all_standalone_variants_match_harness_routes():
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    gate.check_harness_route_parity(harness, gate.variant_paths())


def test_named_file_operation_selects_edit_blocks_without_fallback(variant):
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    scoped = variant._select_harness_blocks(
        harness, "named_file_operation", prompt="Edit alpha.py to fix its calculation.",
    )
    assert scoped != harness
    assert "### BLOCK: CORE ###" in scoped
    assert "### BLOCK: EDIT ###" in scoped
    assert "### BLOCK: DELEGATION ###" not in scoped


@pytest.mark.parametrize("change, error", [
    ("missing", "missing=.*named_file_operation"),
    ("unknown", "unknown=.*undeclared_route"),
    ("block", "invalid harness blocks"),
])
def test_parity_rejects_route_or_block_drift(tmp_path, change, error):
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    tree = ast.parse(read_variant_source(variant_path(snapshots.REFERENCE.name, ROOT)))
    mapping = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "_HARNESS_ROUTE_BLOCKS"
                           for target in node.targets))
    if change == "missing":
        mapping.pop("named_file_operation")
    elif change == "unknown":
        mapping["undeclared_route"] = ()
    else:
        mapping["named_file_operation"] = ("NOT_A_BLOCK",)
    target = tmp_path / "variant.py"
    target.write_text(f"_HARNESS_ROUTE_BLOCKS = {mapping!r}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match=error):
        gate.check_harness_route_parity(harness, [target])


def test_route_parity_accepts_crlf_declarations():
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    gate.check_harness_route_parity(harness.replace("\n", "\r\n"), gate.variant_paths())


@pytest.mark.parametrize("route, prompt, clause", [
    ("direct_existing_entrypoint", "Run the named existing script.",
     "Establish the project root from verified runtime facts or project metadata"),
    ("named_file_operation", "Edit the named existing file.",
     "For a verified thin-launcher/shared-runtime layout"),
    ("tool_loop", "Update documentation/README.md.",
     "`PROJECT.md` approval rules apply at every location"),
])
def test_redesign_guidance_is_selected_for_the_relevant_task(variant, route, prompt, clause):
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    scoped = variant._select_harness_blocks(harness, route, prompt=prompt)
    assert scoped != harness
    assert clause in scoped


def test_redesign_guidance_does_not_expand_simple_answer_context(variant):
    harness = (ROOT / "HARNESS.txt").read_text(encoding="utf-8")
    scoped = variant._select_harness_blocks(harness, "simple_answer", prompt="What is 2 plus 2?")
    assert scoped != harness
    for clause in ("Establish the project root from verified runtime facts or project metadata",
                   "For a verified thin-launcher/shared-runtime layout",
                   "`PROJECT.md` approval rules apply at every location"):
        assert clause not in scoped
