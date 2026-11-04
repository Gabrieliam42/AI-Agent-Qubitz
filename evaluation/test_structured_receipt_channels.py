"""Structured receipts count raw response channels without changing requests."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_structured_response_containment as transport

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("reasoning", ["", "raw reasoning", None, ["not text"], {"not": "text"}])
def test_receipt_counts_raw_reasoning_not_the_display_normalized_message(variant, runner, monkeypatch, reasoning):
    body = {"id": "raw-channel-fixture", "choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": '{"patches":[]}', "reasoning_content": reasoning,
    }}]}
    before = copy.deepcopy(body)
    monkeypatch.setattr(transport, "response_body", lambda _mode: body)
    client, requests = transport.wire_transport(variant, runner, monkeypatch, "valid")
    proposal = client.qubitz_json_chat(
        model_name=runner.config.model_name, messages=[{"role": "user", "content": "Return the object."}],
        schema_name="channel_receipt_probe", schema={"type": "object"}, num_predict=256,
    )
    assert proposal == {"patches": []} and body == before and len(requests) == 1
    assert proposal.receipt["reasoning_characters"] == (len(reasoning) if isinstance(reasoning, str) else 0)
    assert proposal.receipt["content_characters"] == len(body["choices"][0]["message"]["content"])
    assert proposal.receipt["response_id"] == body["id"] and proposal.receipt["finish_reason"] == "stop"
    expected_hash = hashlib.sha256(json.dumps(requests[0], sort_keys=True, ensure_ascii=True,
                                              separators=(",", ":")).encode()).hexdigest()
    assert proposal.receipt["request_sha256"] == expected_hash
    assert requests[0]["max_tokens"] == 256 and requests[0]["tools"] == []
