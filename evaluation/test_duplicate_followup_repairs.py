"""Regressions derived from duplicate follow-up evidence, without inference."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation.campaign_history import history_findings
from evaluation.campaign_observation import resolve_event_reference
from runtime.recovery_protocol import (
    repair_staged_text,
    snapshot_line_replacements,
    snapshot_proposal_schema,
)

variant = receipts.variant
runner = receipts.runner


def test_source_fields_validate_candidate_not_existing_disk(variant, runner):
    target = runner.workspace / "package.md"
    before = "Keep\n<!-- selected:start -->\n{}\n<!-- selected:end -->\nFooter\n"
    target.write_text(before)
    prompt = ("In package.md replace only the selected JSON object containing exactly name, version, and source_url. "
              "Take name and version from the fetched info object. Preserve everything outside the markers.")
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets = {str(target)}
    broker.transaction_originals[str(target)] = {"data": before.encode()}
    broker.record_result("fetch_url", {"url": "https://example.org/data.json"}, receipts.result({
        "body": '{"info":{"name":"demo","version":"1.0"}}', "http_status": 200, "retrieved": True,
    }))
    for version, accepted in [("1.0", True), ("wrong", False)]:
        candidate = before.replace("{}", json.dumps({"name": "demo", "version": version,
                                                     "source_url": "https://example.org/data.json"})).encode()
        issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): candidate})
        assert (not issues) is accepted, issues
        assert target.read_text() == before


def test_inferred_candidate_default_preservation_is_checked(variant, runner):
    target = runner.workspace / "compute.py"
    original = "def compute(values):\n    return sum(values)\n"
    target.write_text(original)
    prompt = "Extend that function with an optional rate. Preserve omitted-argument behavior including result types."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets.clear()
    bad = "def compute(values, rate=0):\n    return sum(values) * (1 - rate / 100)\n"
    good = "def compute(values, rate=0):\n    return sum(values) if rate == 0 else sum(values) * (1-rate/100)\n"
    for candidate, accepted in [(bad, False), (good, True)]:
        issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(target): candidate.encode()})
        assert (not issues) is accepted, issues
        assert broker._default_behavior_receipts[str(target)]["after_sha256"] == hashlib.sha256(candidate.encode()).hexdigest()
    assert target.read_text() == original


def test_committed_inferred_target_keeps_default_receipt(variant, runner):
    target = runner.workspace / "compute.py"
    original = b"def compute(values):\n    return sum(values)\n"
    current = b"def compute(values, rate=0):\n    return sum(values) if rate == 0 else sum(values) * (1-rate/100)\n"
    target.write_bytes(current)
    prompt = "Extend that function. Preserve omitted-argument behavior."
    broker = receipts.begin(runner, prompt)
    broker.transaction_explicit_file_targets.clear()
    broker.transaction_originals[str(target)] = {"data": original}
    broker.transaction_changed_hashes[str(target)] = hashlib.sha256(current).hexdigest()
    assert not variant._saved_text_contract_issues(broker, prompt, ())
    context = broker.transaction_recovery_context()
    assert hashlib.sha256(current).hexdigest() in context
    assert "Earlier file excerpts" in context and "not a new permission grant" in context


def test_query_delimiters_are_not_dispatched_as_literal_search_by_accident(variant, runner):
    query = "note.md</arg_value><arg_key>path</arg_key><arg_value>/somewhere"
    broker = receipts.begin(runner, "Update note.md using its verified snapshot.")
    token = variant._TOOL_CALL_CONTEXT.set(broker)
    try:
        assert variant._tool_argument_boundary_errors({"query": query})
        assert not variant._tool_argument_boundary_errors({"new_text": query, "content": query})
        broker.current_prompt = f"Search for this literal text: `{query}`."
        assert not variant._tool_argument_boundary_errors({"query": query})
    finally:
        variant._TOOL_CALL_CONTEXT.reset(token)


def test_bounded_parts_keep_exact_text_and_existing_cap():
    schema = snapshot_proposal_schema({"x.txt": "old\r\n"})
    string, parts = schema["properties"]["patches"]["items"]["anyOf"][0]["properties"]["replacements"]["items"]["properties"]["new_text"]["anyOf"]
    assert string["maxLength"] == parts["items"]["maxLength"] == 1000
    assert parts["maxItems"] == 24
    text = ["x" * 1000] * 24
    assert snapshot_line_replacements("old\r\n", [{"start_line": 1, "end_line": 1, "new_text": text}])[0]["new_text"] == "".join(text)
    for value in [text + [""], ["x" * 1001], [False], "x" * 24001]:
        with pytest.raises(ValueError):
            snapshot_line_replacements("old\n", [{"start_line": 1, "end_line": 1, "new_text": value}])


def test_repeated_staged_text_requires_exact_diagnosed_occurrence():
    content = "HEAD repeated MIDDLE repeated TAIL"
    context = {"draft_sha256": hashlib.sha256(content.encode()).hexdigest(),
               "rejected_draft_excerpt": content,
               "excerpt_ranges": [{"draft_offset": 0, "excerpt_offset": 0, "length": len(content)}]}
    patch = {"old_text": "repeated", "new_text": "corrected"}
    with pytest.raises(ValueError, match="not unique"):
        repair_staged_text(content, patch, context)
    offset = content.rindex("repeated")
    assert repair_staged_text(content, {**patch, "old_offset": offset}, context) == "HEAD repeated MIDDLE corrected TAIL"
    for wrong in [True, offset + 1, -1]:
        with pytest.raises(ValueError, match="offset"):
            repair_staged_text(content, {**patch, "old_offset": wrong}, context)
    with pytest.raises(ValueError, match="hash changed"):
        repair_staged_text(content + " changed", {**patch, "old_offset": offset}, context)
    with pytest.raises(ValueError, match="offset"):
        repair_staged_text(content, {"old_text": "HEAD", "new_text": "NEW", "old_offset": None}, context)


def test_staged_tail_excerpt_keeps_its_actual_offset():
    content = "x" * 14000 + "diagnosed"
    context = {"draft_sha256": hashlib.sha256(content.encode()).hexdigest(),
               "rejected_draft_excerpt": "head[omitted]diagnosed",
               "excerpt_ranges": [{"draft_offset": 14000, "excerpt_offset": 13, "length": 9}]}
    assert repair_staged_text(content, {"old_text": "diagnosed", "new_text": "fixed"}, context) == "x" * 14000 + "fixed"
    with pytest.raises(ValueError, match="outside"):
        repair_staged_text(content, {"old_text": "x", "new_text": "y", "old_offset": 1000}, context)


def history_fixture(tmp_path, *, stopped=True):
    output = tmp_path / "output"
    attempt = output / "variant/repeat_001/scenario/attempt_001"
    attempt.mkdir(parents=True)
    directory = tmp_path / "supervision/full"
    directory.mkdir(parents=True)
    event = {"sequence": 1, "turn": 1, "kind": "managed_server_cleanup_outcome",
             "outcome": {"stopped": stopped, "actual_wait": stopped}}
    raw = (json.dumps(event) + "\n").encode()
    (attempt / "events.jsonl").write_bytes(raw)
    reference = {"source": "events.jsonl", "offset": 0, "length": len(raw),
                 "sha256": hashlib.sha256(raw).hexdigest(), "sequence": 1, "turn": 1,
                 "kind": event["kind"], "invocation_id": None}
    index = attempt / "event_payload_index.jsonl"
    index.write_text(json.dumps(reference) + "\n")
    cursor = {"files": {(attempt / "events.jsonl").relative_to(output).as_posix(): len(raw)}}
    identifier = "a" * 32
    receipt = directory / f"notable_{identifier}.json"
    receipt.write_text(json.dumps({"observation_id": identifier, "stage": "full", "storage_unchanged": True,
                                  "notable": [{"file": index.relative_to(output).as_posix(),
                                               "kind": event["kind"], "event": reference}]}))
    for path, value in [(directory / "plan.json", {"output": "output"}),
                        (directory / "observation_cursor.json", cursor)]:
        path.write_text(json.dumps(value))
    (directory / "observations.jsonl").write_text(json.dumps({"observation_id": identifier,
        "receipt": receipt.relative_to(tmp_path).as_posix(), "cursor_after": cursor}) + "\n")
    control = SimpleNamespace(ROOT=tmp_path, project_path=lambda p: tmp_path / p,
                              relative=lambda p: p.relative_to(tmp_path).as_posix(),
                              digest=lambda p: hashlib.sha256(p.read_bytes()).hexdigest())
    return control, directory, index, reference, len(raw), output


@pytest.mark.parametrize("stopped", [True, False])
def test_history_resolves_descriptor_before_cleanup_classification(tmp_path, stopped):
    control, directory, *_ = history_fixture(tmp_path, stopped=stopped)
    issues, unknown, _ = history_findings(control, directory)
    assert issues == ([] if stopped else ["observed_managed_server_cleanup_unverified"])
    assert unknown == []


@pytest.mark.parametrize("change", ["hash", "range", "source", "identity", "incomplete"])
def test_reference_tampering_is_unverified_without_cursor_change(tmp_path, change):
    _, directory, index, original, consumed, output = history_fixture(tmp_path)
    reference = dict(original)
    before = (directory / "observation_cursor.json").read_bytes()
    if change == "hash":
        reference["sha256"] = "0" * 64
    elif change == "range":
        consumed -= 1
    elif change == "source":
        reference["source"] = "../events.jsonl"
    elif change == "identity":
        reference["turn"] = 2
    else:
        (index.parent / "events.jsonl").write_bytes(b"{}")
    with pytest.raises(ValueError):
        resolve_event_reference(index, reference, consumed, output)
    assert (directory / "observation_cursor.json").read_bytes() == before


def test_truncated_cleanup_preview_is_not_a_verified_outcome(tmp_path):
    control, directory, *_ = history_fixture(tmp_path)
    path = directory / ("notable_" + "a" * 32 + ".json")
    receipt = json.loads(path.read_text())
    receipt["notable"][0]["event"] = "truncated preview"
    path.write_text(json.dumps(receipt))
    issues, _, _ = history_findings(control, directory)
    assert issues == ["observed_event_reference_unverified"]
