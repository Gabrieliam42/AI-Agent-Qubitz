"""New progress guards must preserve existing tightly scoped correction authority."""
from __future__ import annotations

from evaluation import test_patch_layout_recovery as layout
from evaluation import test_tool_argument_boundaries as boundaries

variant = layout.variant
case = layout.case


def test_trusted_one_shot_saved_contract_correction_is_not_an_unjustified_cycle(case):
    assert not layout.call(case).isError
    for path in case.paths:
        boundaries.record_snapshot(case.broker, path)
    case.broker._saved_contract_correction_active = True
    case.broker._saved_file_contract_issues = ["Test-owned saved-contract mismatch"]
    # The production correction computes these replacements from its exact contract;
    # this boundary fixture supplies them explicitly to isolate the new cycle guard.
    args = {"patches": [{"path": item["path"], "replacements": [{
        "old_text": item["new_text"], "new_text": item["old_text"],
    }]} for item in case.bad["patches"]]}
    result = layout.call(case, args)
    assert not result.isError
    assert case.broker._saved_contract_correction_committed
    assert len(case.host.dispatched) == 2
    assert all("state=original" in path.read_text() for path in case.paths)
    assert layout.call(case, args).isError
    assert len(case.host.dispatched) == 2


def test_bounded_execution_tracking_does_not_claim_unknown_keys_are_cycles(tmp_path):
    from runtime.action_progress import ActionProgress

    progress = ActionProgress("Run the authorized checks.", tmp_path)
    for number in range(128):
        assert progress.reserve_execution("run_command", {"command": str(number)}, (("target", "same"),))
    assert progress.reserve_execution("run_command", {"command": "new-untracked"}, (("target", "same"),))
    assert len(progress.executions) == 128
