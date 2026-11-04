"""Exact selected-JSON source binding, independent of package or model identity."""
from __future__ import annotations

import json

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("change", [None, "project", "revision", "type", "origin", "extra"])
def test_selected_json_keeps_exact_source_fields_and_user_url(variant, runner, change):
    url = "https://example.org/metadata.json"
    path = runner.workspace / "details.md"
    before = "Keep\n<!-- start -->\n{}\n<!-- end -->\nFooter\n"
    path.write_text(before)
    prompt = (f"Fetch metadata from {url}. In details.md replace the selected JSON object containing exactly "
              "project, revision, and origin. Take project and revision from the captured metadata object. "
              "origin must be the exact URL I supplied. Preserve everything outside the markers.")
    broker = receipts.begin(runner, prompt)
    broker.transaction_originals[str(path)] = {"data": before.encode()}
    broker.record_result("fetch_url", {"url": url}, receipts.result({
        "body": '{"metadata":{"project":"example","revision":"1.0"}}',
        "http_status": 200, "retrieved": True,
    }))
    data = {"project": "example", "revision": "1.0", "origin": url}
    if change in {"project", "revision", "origin"}:
        data[change] = "wrong"
    elif change == "type":
        data["revision"] = 1.0
    elif change == "extra":
        data["extra"] = True
    candidate = before.replace("{}", json.dumps(data)).encode()
    issues = variant._saved_text_contract_issues(broker, prompt, (), candidates={str(path): candidate})
    assert (not issues) is (change is None), issues
    assert path.read_text() == before
