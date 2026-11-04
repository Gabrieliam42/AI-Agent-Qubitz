"""Verified-artifact follow-up routing without model or server execution."""
from __future__ import annotations

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner

SOLAR_EDIT = (
    "In the solar-system HTML you just created, change the initial view to focus on Earth and set the initial "
    "visible animation speed to 3 (or the closest clearly labeled equivalent). Preserve the existing scene, "
    "bodies, visual content, controls, labels, animation, scientific caveats and the roughly 60-degree plane "
    "relationship. Keep the change limited to this initial focus and speed; do not edit supplied fixtures or "
    "install anything."
)
HISTORY = [
    {"role": "user", "content": "Create an interactive solar-system HTML scene."},
    {"role": "assistant", "content": "Created solar_system.html. [Completion status: partial]"},
]


@pytest.mark.parametrize("prompt", [
    SOLAR_EDIT,
    "In the file you created, replace the initial speed with 3.",
    "Update the report that you generated to include the new total.",
    "Change the chart we just made to use a logarithmic axis.",
])
def test_artifact_relative_clauses_are_followups(variant, prompt):
    assert variant._classify_turn_relation(prompt, HISTORY).depends_on_prior


@pytest.mark.parametrize("prompt", [
    "New task: explain how to modify the HTML you just created.",
    "Separate question: what is JavaScript?",
    "Create an independent HTML page explaining the solar system.",
])
def test_explicit_new_requests_do_not_inherit_an_artifact(variant, prompt):
    assert not variant._classify_turn_relation(prompt, HISTORY).depends_on_prior


def test_artifact_reference_without_history_cannot_invent_a_binding(variant):
    assert not variant._classify_turn_relation(SOLAR_EDIT, []).depends_on_prior


def test_solar_edit_route_keeps_exact_target_patch_tools(variant, runner, tmp_path):
    target = tmp_path / "solar_system.html"
    original = "<html><script>const speed = 1;</script></html>"
    target.write_text(original, encoding="utf-8")
    runner.history = list(HISTORY)
    runner._turn_relation = variant._classify_turn_relation(SOLAR_EDIT, runner.history)
    runner._active_follow_up_paths = (str(target),)
    runner._tool_permission_broker.begin_turn(SOLAR_EDIT, None)

    decision = runner._decide_route(SOLAR_EDIT)

    assert decision.selected_route == "retrieval_plus_model"
    assert decision.features["query_classification"]["inspection_level"] == "exact_targets"
    runner._route_decision = decision
    definitions = [{"name": name} for name in (
        "read_file_snapshot", "apply_text_patch", "write_file", "list_directory",
    )]
    selected = {runner._tool_name(tool) for tool in runner._filter_tools_by_intent(SOLAR_EDIT, definitions)}
    assert {"read_file_snapshot", "apply_text_patch"} <= selected
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("constraint", [
    "Do not edit supplied fixtures or install anything.",
    "Do not modify the provided fixture files.",
    "Do not change fixtures.",
])
def test_fixture_scope_allows_only_bound_artifact(variant, runner, tmp_path, constraint):
    target = tmp_path / "solar_system.html"
    target.write_text("<script>const speed = 1;</script>", encoding="utf-8")
    runner.history = list(HISTORY)
    prompt = "In the HTML you just created, change the initial speed to 3. " + constraint
    runner._turn_relation = variant._classify_turn_relation(prompt, runner.history)
    runner._active_follow_up_paths = (str(target),)
    broker = runner._tool_permission_broker
    broker.begin_turn(prompt, None)
    runner._task_intent_for_prompt(prompt)
    semantic = broker.task_intent.semantic
    assert "edit" in semantic.requested_actions
    assert "edit" not in semantic.prohibited_actions
    assert ("edit", "@class:fixture_files") in semantic.scoped_prohibitions
    digest = variant.hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("read_file_snapshot", {"path": str(target)}, receipts.result({
        "path": str(target), "status": "text_snapshot", "sha256": digest,
        "content": target.read_text(encoding="utf-8"), "start_line": 1, "end_line": 1,
    }))
    allowed, denial = broker.authorize("apply_text_patch", {
        "path": str(target), "expected_sha256": digest,
        "replacements": [{"old_text": "speed = 1", "new_text": "speed = 3"}],
    })
    assert allowed, denial
    for protected in ("fixtures/input.json", "fixture_input.json", "solar_reference.json", "three.module.js", "other.html"):
        allowed, denial = broker.authorize("write_file", {"path": protected, "content": "changed"})
        assert not allowed and denial["policy_id"] == "protected_fixture_files"
    assert target.read_text(encoding="utf-8") == "<script>const speed = 1;</script>"


def test_fixture_scope_cannot_invent_an_artifact_binding(runner):
    broker = runner._tool_permission_broker
    broker.begin_turn("Change the scene. Do not edit supplied fixtures.", None)
    allowed, denial = broker.authorize("write_file", {"path": "scene.html", "content": "changed"})
    assert not allowed and denial["policy_id"] == "protected_fixture_files"
