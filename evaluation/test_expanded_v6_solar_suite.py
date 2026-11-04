"""Model-free scope and lineage tests for the corrected expanded solar suite."""
from __future__ import annotations

import hashlib
import json
import sys

import pytest

from evaluation import test_solar_system_matrix as solar_tests

matrix = solar_tests.matrix


def test_v6_preserves_v5_non_solar_definitions_and_corrected_first_turn():
    import followup_matrix_solar_user_v3 as full_solar
    import followup_matrix_user_solar_v2 as mini

    v5 = matrix.selected_scenarios(None, "expanded-v5")
    v6 = matrix.selected_scenarios(None, "expanded-v6")
    assert len(v5) == len(v6) == 27
    assert v6[:-1] == v5[:-1]
    assert v5[-1].name == "solar_system_3d_followup"
    assert v6[-1] == full_solar.scenario()
    assert v6[-1].name == "solar_system_user_prompt_v3"
    assert len(v6[-1].turns) == 3
    assert v6[-1].turns[0].prompt == mini.PROMPT
    assert v6[-1].turns[0].checks[0].kind == "user_html_artifact"
    assert v6[-1].turns[1].checks[0].kind == "user_html_artifact_update"
    assert v6[-1].turns[2].checks[0].kind == "no_tools"


def test_v6_public_dry_plan_covers_the_current_catalog(monkeypatch, capsys):
    names = matrix.variants(None, "all")
    assert len(names) == 40
    monkeypatch.setattr(sys, "argv", ["matrix", "--suite", "expanded-v6", "--gui", "--tier", "all"])
    monkeypatch.setattr(matrix, "coordinate", lambda *_args: pytest.fail("Model execution attempted"))
    assert matrix.main() == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["execution_started"] is False
    assert len(plan["variants"]) == 40
    assert len(plan["scenarios"]) == 27
    assert plan["trajectory_count"] == 1080
    assert plan["turn_count"] == 3520
    assert plan["scenarios"][-1]["name"] == "solar_system_user_prompt_v3"
    assert plan["scenarios"][-1]["turns"][0]["prompt"] == matrix.selected_scenarios(
        ["solar_system_user_prompt_v3"], "expanded-v6"
    )[0].turns[0].prompt


def test_solar_update_check_requires_changed_html_and_keeps_fixed_assets_read_only(tmp_path):
    import followup_matrix_solar_user_v3 as full_solar
    from followup_matrix_cases import workspace_manifest

    root = tmp_path / "workspace"
    root.mkdir()
    html = root / "solar.html"
    html.write_text("<html><script>const focus='Sun';</script></html>", encoding="utf-8")
    dependency = root / "three.module.js"
    dependency.write_text("export {};\n", encoding="utf-8")
    before = workspace_manifest(root)
    html.write_text("<html><script>const focus='Earth';</script></html>", encoding="utf-8")
    after = workspace_manifest(root)
    issues, evidence = full_solar.verify(root, full_solar.scenario().turns[1], {}, before, after, {}, {})
    assert "evidence_unavailable:solar_observed_behavior:solar.html" in issues
    assert "evidence_unavailable:solar_preservation_baseline:solar.html" in issues
    assert evidence[-1]["full_task_success_verified"] is False
    assert evidence[-1]["artifacts"][0]["changed_this_turn"] is True

    before = after
    dependency.write_text("export const changed = true;\n", encoding="utf-8")
    after = workspace_manifest(root)
    issues, _ = full_solar.verify(root, full_solar.scenario().turns[1], {}, before, after, {}, {})
    assert any(issue.startswith("unexpected_mutation:") for issue in issues)


def test_v6_prompt_remains_exactly_the_approved_corrected_user_text():
    import followup_matrix_solar_user_v3 as full_solar
    import followup_matrix_user_solar_v2 as mini

    prompt = full_solar.scenario().turns[0].prompt
    assert prompt == mini.PROMPT
    assert len(prompt) == 898
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == (
        "112e749ee40c3ce489d22cc64e616524767bdf48e2dd24f990c9347e04fce3cf"
    )
    assert "roughly 60 degrees, not exactly aligned" in prompt


@pytest.mark.parametrize("suite", ["original-v1", "expanded-v2", "expanded-v3", "expanded-v4",
                                  "expanded-v5", "expanded-v6", "solar-visual-v1", "mini-user-v1", "mini-user-v2"])
def test_every_suite_satisfies_its_runtime_interface(suite):
    assert matrix.selected_scenarios(None, suite)


@pytest.mark.parametrize("hook", ["preflight_source", "source_unavailable", "only_source_blockers"])
def test_v6_rejects_missing_source_hooks_during_model_free_selection(monkeypatch, hook):
    import followup_matrix_solar_user_v3 as full_solar

    monkeypatch.setattr(full_solar, hook, None)
    with pytest.raises(ValueError, match=hook):
        matrix.selected_scenarios(None, "expanded-v6")


def test_v6_source_preflight_and_failure_callbacks_use_real_expanded_behavior(monkeypatch):
    import urllib.error

    import followup_matrix_expanded as expanded
    import followup_matrix_solar_user_v3 as full_solar

    assert full_solar.preflight_source is expanded.preflight_source
    assert full_solar.source_unavailable is expanded.source_unavailable
    assert full_solar.only_source_blockers is expanded.only_source_blockers

    def unavailable(*args, **kwargs):
        raise urllib.error.URLError("model-free fixture unavailable")

    monkeypatch.setattr(expanded.urllib.request, "urlopen", unavailable)
    receipt = full_solar.preflight_source(expanded.SOURCE_URL)
    assert receipt["status"] == "unavailable"
    observed = {"turn": 1, "source_fetches": [{"turn": 1, "url": expanded.SOURCE_URL, "result": {
        "status": "network_request_failed", "retrieved": False}}], "assistant": "Source unavailable."}
    issues = ["postcondition_failed:online_section:package.md", "partial_completion_report"]
    before = {"a": {"package.md": "unchanged"}}
    assert full_solar.source_unavailable(observed)
    assert full_solar.only_source_blockers(issues, observed, before, before)
    assert not full_solar.only_source_blockers(issues, observed, before, {"a": {"package.md": "changed"}})
    observed["assistant"] = "Done. [Completion status: verified]"
    assert not full_solar.only_source_blockers(issues, observed, before, before)
