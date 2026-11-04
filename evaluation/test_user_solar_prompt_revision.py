"""Model-free provenance and selection checks for the corrected user solar prompt."""
from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict

import pytest

from evaluation import test_solar_system_matrix as solar_tests

matrix = solar_tests.matrix


def test_revision_changes_only_the_requested_plane_clause():
    import followup_matrix_user_solar as old
    import followup_matrix_user_solar_v2 as new

    assert len(old.PROMPT) == 898
    assert hashlib.sha256(old.PROMPT.encode()).hexdigest() == "68c32d1d6d046635b22ab3fbd631a1ea2ad93f1e0c462289bf53778eb6fbeae5"
    assert new.PROMPT == old.PROMPT.replace(new.OLD_PLANE_CLAUSE, new.NEW_PLANE_CLAUSE, 1)
    assert new.PROMPT.replace(new.NEW_PLANE_CLAUSE, new.OLD_PLANE_CLAUSE, 1) == old.PROMPT
    assert 'astronomically  accurate' in new.PROMPT
    assert 'ligthed properly' in new.PROMPT


def test_old_catalog_and_scientific_case_are_unchanged():
    import followup_matrix_user_solar as old
    import followup_matrix_user_solar_v2 as new
    from followup_matrix_solar import scenario

    before = old.catalog()
    after = new.catalog()
    assert before[0] == after[0]
    assert before[1].turns[0].prompt == old.PROMPT
    assert after[1].name == new.NAME and len(after[1].turns) == 1
    assert after[1].turns[0].prompt == new.PROMPT
    assert scenario() == matrix.selected_scenarios(["solar_system_3d_followup"], "expanded-v5")[0]


def test_corrected_dry_plan_is_one_unmodified_user_turn(monkeypatch, capsys):
    import followup_matrix_user_solar_v2 as new

    monkeypatch.setattr(sys, "argv", ["matrix", "--suite", "mini-user-v2", "--gui", "--variants",
                        "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py", "--scenarios", new.NAME])
    monkeypatch.setattr(matrix, "coordinate", lambda *_args: pytest.fail("Model run attempted"))
    assert matrix.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution_started"] is False
    assert result["trajectory_count"] == result["turn_count"] == 1
    assert result["scenarios"][0] == json.loads(json.dumps(asdict(new.catalog()[1])))
    assert new.LIMITATION in result["limitations"]


def test_corrected_case_preserves_original_local_assets(tmp_path):
    import followup_matrix_user_solar as old
    import followup_matrix_user_solar_v2 as new
    from followup_matrix_cases import workspace_manifest

    roots_old = old.prepare(old.catalog()[1], tmp_path / "old")
    roots_new = new.prepare(new.catalog()[1], tmp_path / "new")
    assert workspace_manifest(roots_old["a"]) == workspace_manifest(roots_new["a"])
    assert not (roots_new["a"] / "solar_reference.json").exists()
