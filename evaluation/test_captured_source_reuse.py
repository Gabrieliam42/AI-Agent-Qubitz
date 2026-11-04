"""Captured-source follow-ups must retain provenance without replaying a fetch."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
URL = "https://example.org/release.json"
BODY = '{"version":"1.0","requires_python":">=3.12"}'
REUSE = "Using only the source content verified in your previous turn, update report.md. Do not fetch again."


@pytest.fixture(scope="module")
def variant():
    path = variant_path(os.environ.get("QUBITZ_TEST_VARIANT", "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"), ROOT)
    spec = importlib.util.spec_from_file_location("captured_source_variant", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.fixture
def runner(variant, tmp_path, monkeypatch):
    monkeypatch.setattr(variant._APP.base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("CPU check attempted a model load"))
    config = variant._APP.base.AgentConfig(workspace=tmp_path, runtime_workspace=ROOT)
    instance = variant._APP.base.AgentRunner(config)
    instance.set_access_mode(variant.ACCESS_MODE_FULL)
    yield instance
    instance.reset_runtime()


def capture(broker, *, url=URL, body=BODY, status=200, error=False):
    broker.record_result("fetch_url", {"url": url}, SimpleNamespace(
        isError=error, content=[], structuredContent={
            "requested_url": url, "final_url": url, "status": status,
            "retrieved": not error, "body": body,
        },
    ))


def followup(variant, broker, prompt=REUSE, kind="follow_up", previous_user=f"Fetch {URL}"):
    broker.begin_turn(prompt, None)
    broker.bind_captured_sources(prompt, variant.TurnRelation(
        kind=kind, reason="test", previous_user=previous_user,
    ))


def snapshot_for_edit(broker):
    target = broker.workspace / "report.md"
    target.write_text("unchanged\n")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    broker.record_result("read_file_snapshot", {"path": "report.md"}, SimpleNamespace(
        isError=False, content=[], structuredContent={
            "status": "text_snapshot", "sha256": digest, "content": "unchanged\n",
            "start_line": 1, "end_line": 1,
        },
    ))
    return {"path": "report.md", "expected_sha256": digest,
            "replacements": [{"old_text": "unchanged\n", "new_text": "version=1.0\n"}]}


def test_reuses_actual_content_and_origin_without_network(variant, runner, monkeypatch):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL} and update report.md", None)
    origin = broker.turn_id
    capture(broker)
    broker.end_turn()
    followup(variant, broker)
    monkeypatch.setattr(variant, "_fetch_named_source_for_grounding", lambda *_args: pytest.fail("Source fetched again"))
    context = asyncio.run(runner._prefetch_named_source_context(REUSE, None))
    assert BODY in context and URL in context
    assert "no fresh fetch occurred" in context
    receipt = broker._current_turn_evidence()[-1]
    assert receipt["tool"] == "wrapper_source_reuse"
    assert receipt["source_turn_id"] == origin != receipt["turn_id"]
    assert receipt["fresh_fetch"] is False
    assert broker.authorize("fetch_url", {"url": URL})[0] is False
    assert broker.authorize("apply_text_patch", snapshot_for_edit(broker))[0] is True
    assert runner._enforce_research_response_urls(REUSE, f"Source: {URL}", None).find(URL) >= 0


@pytest.mark.parametrize("prompt,kind", [
    (REUSE, "new_request"),
    ('Explain this instruction: "Use the previously fetched source."', "follow_up"),
    ("Do not use the previously fetched source. Create report.md independently.", "follow_up"),
    ("Use the previous MCP results to create report.md.", "follow_up"),
    ("Fetch the latest source metadata and update report.md.", "follow_up"),
])
def test_unrelated_or_negated_requests_do_not_activate_reuse(variant, runner, prompt, kind):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    capture(broker)
    followup(variant, broker, prompt, kind)
    assert broker.source_reuse_requested is False
    assert not any(item["tool"] == "wrapper_source_reuse" for item in broker.evidence)


@pytest.mark.parametrize("prior_fetch", [False, True])
def test_local_source_document_does_not_become_external_evidence(variant, runner, prior_fetch):
    broker = runner._tool_permission_broker
    broker.begin_turn("Create a source document and report.md", None)
    if prior_fetch:
        capture(broker)
    followup(
        variant, broker,
        "Use only the previous source document content to update report.md.",
        previous_user="Create a source document and report.md",
    )
    assert not broker.source_reuse_requested and not broker.source_reuse_blocker
    assert broker.authorize("apply_text_patch", snapshot_for_edit(broker))[0] is True


def test_generic_local_source_reuse_without_online_context_is_not_blocked(variant, runner):
    broker = runner._tool_permission_broker
    broker.begin_turn("Create source.md", None)
    followup(variant, broker, previous_user="Create source.md")
    assert not broker.source_reuse_requested and not broker.source_reuse_blocker
    assert broker.authorize("apply_text_patch", snapshot_for_edit(broker))[0] is True


@pytest.mark.parametrize("prompt", [
    REUSE.replace("update report.md", "update the source file report.md"),
    REUSE.replace("update report.md", "revise the source document report.md"),
    "Write the source section in report.md using the previously fetched source. Do not fetch again.",
    "Use the earlier fetched content to update the source file report.md. Do not fetch again.",
])
def test_destination_wording_does_not_hide_the_captured_online_source(variant, runner, prompt):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    capture(broker)
    followup(variant, broker, prompt)
    assert broker.source_reuse_requested and not broker.source_reuse_blocker
    assert broker.current_source_receipts[URL]["body"] == BODY


@pytest.mark.parametrize("prompt", [
    "Use source material to update the previous source file report.md.",
    "Use source data to rewrite the earlier source section in report.md.",
    "Use source content to update report.md based on the previous file.",
])
def test_destination_qualifiers_do_not_authorize_online_source_reuse(variant, runner, prompt):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    capture(broker)
    followup(variant, broker, prompt)
    assert not broker.source_reuse_requested and not broker.source_reuse_blocker
    assert not any(item["tool"] == "wrapper_source_reuse" for item in broker.evidence)


@pytest.mark.parametrize("problem", ["missing", "error", "workspace", "hash", "ambiguous", "other_url", "freshness"])
def test_invalid_or_ambiguous_source_never_authorizes_mutation(variant, runner, tmp_path, problem):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    if problem != "missing":
        capture(broker, error=problem == "error", status=403 if problem == "error" else 200)
    if problem == "workspace":
        broker.current_source_receipts[URL]["workspace"] = str(tmp_path.parent)
    elif problem == "hash":
        broker.current_source_receipts[URL]["body"] = "altered"
    elif problem == "ambiguous":
        capture(broker, url="https://example.org/other.json")
    prompt = REUSE
    if problem == "other_url":
        prompt = "Use the previously fetched source at https://example.org/missing.json to update report.md."
    elif problem == "freshness":
        prompt = "Use the previously fetched source to verify the current online version and update report.md."
    followup(variant, broker, prompt)
    assert broker.source_reuse_blocker
    allowed, error = broker.authorize("apply_text_patch", snapshot_for_edit(broker))
    assert not allowed and error["status"] == "source_evidence_required"
    assert (tmp_path / "report.md").read_text() == "unchanged\n"


def test_chained_reuse_keeps_original_capture_and_exact_url(variant, runner):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    origin = broker.turn_id
    capture(broker)
    for _ in range(2):
        followup(variant, broker, f"Use the previously fetched source at {URL} to update report.md.")
        assert not broker.source_reuse_blocker
        assert broker.current_source_receipts[URL]["source_turn_id"] == origin
        assert broker._missing_source_evidence_urls() == []


def test_new_session_and_intervening_unrelated_turn_do_not_inherit_sources(variant, runner, tmp_path):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    capture(broker)
    followup(variant, broker, "What is two plus two?", "new_request")
    followup(variant, broker)
    assert broker.source_reuse_blocker
    fresh = variant._ToolPermissionBroker(variant._APP.base, tmp_path, ROOT)
    followup(variant, fresh)
    assert fresh.source_reuse_blocker


def test_offline_reuses_captured_text_without_fetching(variant, runner, monkeypatch):
    broker = runner._tool_permission_broker
    broker.begin_turn(f"Fetch {URL}", None)
    capture(broker)
    followup(variant, broker)
    runner.network_mode = "offline"
    monkeypatch.setattr(variant, "_fetch_named_source_for_grounding", lambda *_args: pytest.fail("Offline fetch"))
    assert BODY in asyncio.run(runner._prefetch_named_source_context(REUSE, None))
