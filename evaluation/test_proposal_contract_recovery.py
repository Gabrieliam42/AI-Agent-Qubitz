"""Task-neutral proposal boundaries and evidence, without model inference."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from evaluation import test_creation_recovery_provenance as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_snapshot_behavior_recovery as spans
from evaluation import test_structured_response_containment as containment
from evaluation import test_tool_argument_boundaries as boundaries
from evaluation.disposable_followup_matrix import (
    Observer,
    runner_snapshot,
    selected_scenarios,
)
from evaluation.outcome_axes import summarize
from evaluation.structured_failure_fixture import (
    StructuredFailureFixture,
    containment_verified,
    followup_verified,
)
from runtime.recovery_protocol import (
    snapshot_line_replacements,
    snapshot_proposal_schema,
)
from runtime.structured_response import (
    StructuredBackendError,
    StructuredProposal,
    StructuredResponseError,
    proposal_evidence,
    record_failure,
)

variant = receipts.variant
runner = receipts.runner


def test_snapshot_schema_binds_each_file_and_local_order_remains_strict():
    schema = snapshot_proposal_schema({"alpha.txt": "first\r\n", "nested/beta.txt": "a\nb\nc\n"})
    branches = schema["properties"]["patches"]["items"]["anyOf"]
    assert [x["properties"]["path"]["enum"] for x in branches] == [["alpha.txt"], ["nested/beta.txt"]]
    assert [x["properties"]["replacements"]["items"]["properties"]["end_line"]["maximum"]
            for x in branches] == [1, 3]
    text = branches[0]["properties"]["replacements"]["items"]["properties"]["new_text"]["anyOf"]
    assert text[0]["maxLength"] == text[1]["items"]["maxLength"] == 1000
    assert text[1]["maxItems"] * text[1]["items"]["maxLength"] == 24000
    with pytest.raises(ValueError, match="Invalid snapshot line span"):
        snapshot_line_replacements("a\nb\nc\n", [{"start_line": 3, "end_line": 2, "new_text": "x"}])


@pytest.mark.parametrize("snapshots", [{}, {"empty.txt": ""}, {"file.txt": None}, {"": "x"}])
def test_unusable_snapshot_contract_is_unavailable(snapshots):
    with pytest.raises(ValueError):
        snapshot_proposal_schema(snapshots)


def test_proposal_evidence_is_bounded_and_json_body_stays_unchanged():
    body = {"patches": [{"path": "alpha.txt", "replacements": [{
        "start_line": 4, "end_line": 3, "new_text": "literal\\n\r\n",
    }]}]}
    proposal = StructuredProposal(body, {"request_id": "a" * 32, "request_policy": "verified_policy",
                                          "finish_reason": "stop", "authorization": "must_not_be_copied"})
    assert json.loads(json.dumps(proposal)) == body
    evidence = proposal_evidence(proposal)
    assert evidence["proposal"] == body and evidence["request"]["request_id"] == "a" * 32
    assert "authorization" not in evidence["request"]
    broker = SimpleNamespace(turn_id="turn", workspace_generation=2, evidence=[])
    record_failure(broker, StructuredResponseError("invalid_proposal", schema_name="span", detail="bounds"),
                   phase="edit_proposal", proposal=proposal)
    assert broker.evidence[-1]["finish_reason"] == "stop" and broker.evidence[-1]["proposal"] == body
    assert broker.evidence[-1]["mutation_dispatched"] is False
    huge = proposal_evidence({"new_text": "x" * 24001})
    assert huge["proposal"] is None and huge["proposal_truncated"] is True and huge["proposal_sha256"]


def test_opt_in_containment_keeps_default_matrix_scope_unchanged():
    assert len(selected_scenarios(None, "expanded-v6")) == 27
    case = selected_scenarios(["structured_error_then_new_task"], "expanded-v6")[0]
    assert case.gui_only and case.category == "error_containment" and len(case.turns) == 2
    assert case.turns[0].structured_error and case.turns[0].allow_partial
    assert not case.turns[1].structured_error and not case.turns[1].allow_partial


def test_observer_records_its_clock_domain_without_rewriting_wall_time(tmp_path, monkeypatch):
    observer = Observer(tmp_path)
    monkeypatch.setattr("evaluation.disposable_followup_matrix.time.time", lambda: 90.0)
    monkeypatch.setattr("evaluation.disposable_followup_matrix.time.monotonic", lambda: 12.0)
    observer.emit("fixture_clock")
    row = json.loads((tmp_path / "events.jsonl").read_text())
    assert row["time"] == 90.0 and row["monotonic"] == 12.0
    assert row["clock_domain"]["clock"] == "time.monotonic" and row["clock_domain"]["process_id"] > 0


@pytest.mark.parametrize("header", ["a" * 32, "invalid identity"])
def test_http_capture_correlates_only_well_formed_request_local_identity(monkeypatch, header):
    from evaluation.smoke_create_calculator import install_request_capture

    events = []
    recorder = SimpleNamespace(turn=1, emit=lambda kind, **data: events.append({"kind": kind, **data}))
    base = SimpleNamespace(LlamaCppClient=type("Client", (), {"_call_with_fallback": lambda *_args: None}))
    for cls, name in ((httpx.Client, "send"), (httpx.Response, "iter_lines"),
                      (httpx.Response, "close"), (httpx.Response, "read")):
        monkeypatch.setattr(cls, name, getattr(cls, name))
    install_request_capture(base, recorder)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"ok": True}, request=request))
    with httpx.Client(transport=transport) as client:
        response = client.post("http://127.0.0.1:8001/v1/chat/completions",
                               headers={"X-Qubitz-Request-Id": header}, json={"stream": False})
        assert response.status_code == 200
    request = next(x for x in events if x["kind"] == "http_request")
    assert request["structured_request_identity"] is (header == "a" * 32)
    assert (request["request_id"] == header) is (header == "a" * 32)
    assert all(x["request_id"] == request["request_id"] for x in events)


def test_outcome_axes_do_not_turn_not_reached_or_injected_turns_into_model_failures():
    clean = {"generation_quality": {"status": "clean", "model_requests": 1,
                                    "requests": [{"transport_status": "completed"}], "problems": []}}
    result = summarize({"infrastructure_passed": True, "task_passed": False}, {"runs": [
        {"variant": "public", "scenario": "task", "status": "failed", "turns": [
            {"turn": 1, "status": "failed", "observed": clean}, {"turn": 2, "status": "not_reached"},
        ]}, {"variant": "public", "scenario": "fixture", "status": "passed",
              "evaluation_axes": {"injected_containment_turns": [1]}, "turns": [
                  {"turn": 1, "status": "passed", "observed": {"generation_quality": {"model_requests": 0}}},
                  {"turn": 2, "status": "passed", "observed": clean},
              ]},
    ]})
    assert result["status"] == "held" and result["generation_quality_passed"] is True
    assert result["generation_coverage_complete"] is False and result["generation_problems"] == []
    assert result["quality_turn_counts_all_task_outcomes"] == {
        "clean": 2, "not_reached": 1, "injected_fixture_not_model_quality": 1,
    }
    assert result["containment_trajectory_counts"] == {"passed": 1}


def test_injected_turn_cannot_hide_external_intervention():
    result = summarize({"infrastructure_passed": True, "task_passed": True}, {"runs": [{
        "status": "passed", "evaluation_axes": {"injected_containment_turns": [1]},
        "turns": [{"turn": 1, "status": "passed", "observed": {
            "external_input": True, "generation_quality": {"model_requests": 0},
        }}],
    }]})
    assert result["status"] == "held" and result["external_intervention"] is True


@pytest.mark.parametrize("mode", ["passed", "overlap"])
def test_real_snapshot_recovery_retains_successful_or_rejected_proposal(
    variant, runner, tmp_path, monkeypatch, mode,
):
    spans.test_exact_recovery_uses_snapshot_lines_and_normal_hash_bound_broker(
        variant, runner, tmp_path, monkeypatch, mode,
    )
    entries = [x for x in runner._tool_permission_broker.evidence if x.get("receipt_kind") == "structured_proposal"]
    assert len(entries) == 1 and entries[0]["proposal"]["patches"][0]["path"] == "notes.txt"
    assert entries[0]["proposal"]["patches"][0]["replacements"][0]["start_line"] == 2
    assert entries[0]["success"] is (mode == "passed")


def test_real_native_json_metadata_survives_success_and_typed_failures(variant, runner, monkeypatch):
    for mode in ["valid", "empty", "backend_error", "http_503", "http_400_grammar"]:
        client, requests = containment.wire_transport(variant, runner, monkeypatch, mode)
        if mode in {"http_503", "http_400_grammar"}:
            class HTTPClient:
                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    pass

                @contextmanager
                def stream(self, method, url, _requests=requests, _mode=mode, **options):
                    _requests.append(options["json"])
                    status = 400 if _mode == "http_400_grammar" else 503
                    detail = "Failed to initialize samplers: failed to parse grammar" if status == 400 else "explicit fixture outage"
                    yield httpx.Response(status, json={"error": {"message": detail}},
                                         request=httpx.Request(method, url, headers=options.get("headers")))

            monkeypatch.setattr(httpx, "Client", lambda **_options: HTTPClient())
        kwargs = {"model_name": runner.config.model_name, "messages": [], "schema_name": "contract",
                  "schema": {"type": "object"}, "num_predict": 128}
        if mode == "valid":
            value = client.qubitz_json_chat(**kwargs)
            assert isinstance(value, StructuredProposal) and value == {"patches": []}
            metadata = value.receipt
            assert metadata["finish_reason"] == "stop" and metadata["response_id"] == "captured-shape-fixture"
            assert metadata["content_characters"] == len('{"patches":[]}')
        else:
            with pytest.raises(StructuredResponseError if mode == "empty" else StructuredBackendError) as error:
                client.qubitz_json_chat(**kwargs)
            metadata = error.value.receipt
        assert len(requests) == 1 and len(metadata["request_id"]) == 32
        expected_status = 503 if mode == "http_503" else 400 if mode == "http_400_grammar" else 200
        assert metadata["http_status"] == expected_status and metadata["request_policy"]
        serialized = json.dumps(requests[0], ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        assert metadata["request_sha256"] == hashlib.sha256(serialized.encode()).hexdigest()


@pytest.mark.parametrize("mode", ["corrected", "still_wrong", "budget", "cancel_correction"])
def test_real_numeric_correction_has_rejected_basis_without_implementation_values(
    variant, runner, tmp_path, monkeypatch, mode,
):
    target = tmp_path / "weights.py"
    before = "def keep(value):\n    return value\n"
    added = "\ndef shift(value, offset, limit):\n    if offset > limit:\n        raise ValueError('offset exceeds limit')\n    return value + offset\n"
    after = before + added
    prompt = "Add shift(value, offset, limit) to weights.py: return value plus offset; reject offsets above limit with ValueError. Preserve keep."
    broker = receipts.begin(runner, prompt)
    broker.required_postconditions.add("tests")
    broker.transaction_originals[str(target)] = {"data": before.encode()}
    target.write_text(after)
    broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
        "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }))
    if mode == "budget":
        assert broker.action_progress.reserve_recovery("edit")
    requests = []

    def propose(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        wrong = len(requests) == 1 or mode in {"still_wrong", "budget"}
        if len(requests) == 2:
            text = str(kwargs["messages"])
            assert '"previous_proposal"' in text and '"failed_case_index": 1' in text
            assert "Claimed boundary in original request" in text
            assert "observed" not in text.lower() and "return value + offset" not in text
            if mode == "cancel_correction":
                runner.request_cancel()
        checks = [{"path": target.name, "function": "shift", "args": [10, 2, 5],
                   "expectation": ({"kind": "exception", "name": "ValueError", "basis": "Claimed boundary in original request"}
                                   if wrong else {"kind": "value", "value": 12, "basis": "Requested value plus offset"})},
                  {"path": target.name, "function": "shift", "args": [1, 7, 5],
                   "expectation": {"kind": "exception", "name": "ValueError", "basis": "Offset above requested limit"}}]
        return StructuredProposal({"checks": checks}, {"request_id": str(len(requests)) * 32,
                                                         "request_policy": "fixture", "finish_reason": "stop"})

    monkeypatch.setattr(runner, "_get_llm", lambda: SimpleNamespace(qubitz_json_chat=propose))
    calls = receipts.install_host(variant, runner, monkeypatch, handler=lambda *_: pytest.fail("Probe dispatched a tool"))
    if mode == "cancel_correction":
        with pytest.raises(variant._APP.base.RunCancelled):
            asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
    else:
        answer = asyncio.run(runner._wrapper_verify_committed_transaction(prompt, None))
        assert answer[0] is (True if mode == "corrected" else None)
        evidence = broker.evidence[-1]
        assert evidence["evaluated_cases"] == evidence["checked_cases"] == (2 if mode == "corrected" else 1)
        assert evidence["matched_cases"] == evidence["accepted_cases"] == (2 if mode == "corrected" else 0)
        assert evidence["attempts"][0]["proposal"]["checks"][0]["expectation"]["basis"] == "Claimed boundary in original request"
        assert evidence["total_evaluated_cases"] == (1 if mode == "budget" else 3 if mode == "corrected" else 2)
    assert len(requests) == (1 if mode == "budget" else 2) and len(broker.action_progress.recoveries) <= 2
    assert not calls and target.read_text() == after


def test_followup_distinguishes_fresh_stale_and_unbound_receipts():
    identity = {"runner_identity": 2, "gui_identity": 3, "window_id": "7", "gui_agent_identity": 2}
    observed = {"same_gui_error_followup": {"original_identity": identity, "current_identity": identity,
                "error_turn_id": "previous"}, "runtime": {"turn_control": {"turn_id": "current"}},
                "completed": True, "generation_quality": {"model_requests": 1}}
    for kind in ("recovery_receipts", "structured_proposal_receipts"):
        for turn_id, expected in (("current", True), ("previous", False), (None, False)):
            trial = copy.deepcopy(observed)
            trial["runtime"][kind] = [{"turn_id": turn_id}]
            assert followup_verified(trial) is expected


def test_injected_error_is_partial_and_same_runner_is_clean_for_next_task(variant, runner, tmp_path, monkeypatch):
    base = variant._APP.base
    target = tmp_path / "state.txt"
    target.write_text("baseline\n")
    observer = SimpleNamespace(emit=lambda *_args, **_kwargs: None)
    gui = SimpleNamespace(agent=runner, root=SimpleNamespace(winfo_id=lambda: 73))
    original_host = base.MCPHost

    class Host:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def list_tools(self):
            return []

        async def call_tool(self, name, arguments):
            broker = runner._tool_permission_broker
            if name == "read_file_snapshot":
                checksum = boundaries.record_snapshot(broker, target)
                return receipts.result({"path": target.name, "sha256": checksum, "content": target.read_text()})
            host = boundaries.host_for(variant, broker)
            result = await host.call_tool(name, arguments)
            assert not host.dispatched
            return result

    monkeypatch.setattr(base, "MCPHost", Host)
    parent = creation.parent_runner(runner)

    async def followup(self, _prompt, callback=None):
        creation.record_creation(self._tool_permission_broker, tmp_path / "after_error.txt", "ready\n")
        return "Saved after_error.txt."

    monkeypatch.setattr(parent, "_run_async", followup)
    fixture = StructuredFailureFixture(runner, base, tmp_path, observer, gui)
    fixture.install()
    try:
        answer = asyncio.run(runner._run_async("Edit state.txt to replace its content with changed followed by a newline."))
    finally:
        fixture.restore()
    first = runner_snapshot(runner)
    assert containment_verified({"structured_failure_fixture": fixture.state, "runtime": first,
                                 "completed": True, "assistant": answer}), json.dumps({
        "fixture": fixture.state, "recovery_receipts": first["recovery_receipts"],
        "structured_proposal_receipts": first["structured_proposal_receipts"],
    }, indent=2)
    assert target.read_text() == "baseline\n" and first["structured_proposal_receipts"]
    answer = asyncio.run(runner._run_async("New task: create after_error.txt containing ready followed by a newline."))
    second = runner_snapshot(runner)
    assert (tmp_path / "after_error.txt").read_text() == "ready\n" and "partial" not in answer
    assert followup_verified({"same_gui_error_followup": {"original_identity": fixture.state["identity"],
                              "current_identity": fixture.state["identity"],
                              "error_turn_id": first["turn_control"]["turn_id"]}, "runtime": second,
                              "completed": True, "generation_quality": {"model_requests": 1}})
    assert base.MCPHost is Host and original_host is not Host
