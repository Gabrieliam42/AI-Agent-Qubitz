"""Task-neutral structured requests and numeric abstention, without inference."""
from __future__ import annotations

import asyncio
import copy
import hashlib

import httpx
import pytest

from evaluation import test_creation_recovery_provenance as creation
from evaluation import test_followup_state_receipts as receipts
from evaluation import test_structured_response_containment as transport
from evaluation import test_verified_recovery_handoff as handoff
from evaluation.disposable_followup_matrix import runner_snapshot
from evaluation.test_snapshot_behavior_recovery import wire_checks
from runtime import structured_response

variant = receipts.variant
runner = receipts.runner

CHAT_FALSE = (
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}"
    "{% if enable_thinking is defined and enable_thinking is false %}"
    "{{ '<think>\\n\\n</think>\\n\\n' }}{% endif %}{% endif %}"
)
CHAT_TRUE_ELSE = (
    "{% if add_generation_prompt %}{{ '<|im_start|>assistant\\n' }}"
    "{% if enable_thinking is defined and enable_thinking is true %}{{ '<think>\\n' }}"
    "{% else %}{{ '<think>\\n\\n</think>\\n\\n' }}{% endif %}{% endif %}"
)
CHANNEL = (
    "{% if add_generation_prompt %}"
    "{% if ns.prev_message_type != 'tool_response' and ns.prev_message_type != 'tool_call' %}"
    "{{ '<|turn>model\\n' }}{% if not enable_thinking %}"
    "{{ '<|channel>thought\\n<channel|>' }}{% endif %}"
    "{% elif ns.prev_message_type == 'tool_response' and enable_thinking %}"
    "{{ '<|channel>thought\\n' }}{% endif %}{% endif %}"
)


@pytest.mark.parametrize("template,expected", [
    (CHAT_FALSE, "closed_think_prefill"), (CHAT_TRUE_ELSE, "closed_think_prefill"),
    (CHANNEL, "closed_channel_prefill"), ("{# " + CHANNEL + " #}", "unverified"),
    (CHAT_TRUE_ELSE.replace("</think>", "still thinking"), "unverified"),
    ("enable_thinking is supported", "unverified"), (None, "unverified"),
])
def test_control_requires_a_verified_template_contract(template, expected):
    assert structured_response.non_thinking_template_contract(template) == expected


@pytest.mark.parametrize("capabilities,expected", [
    ({"structured_thinking_control": "closed_think_prefill"}, "template_non_thinking"),
    ({"structured_thinking_control": "closed_channel_prefill"}, "template_non_thinking"),
    ({"supports_reasoning_effort": True}, "advertised_reasoning_none"),
    ({"supports_reasoning_effort": "true"}, "unchanged_unverified_template"),
    ({}, "unchanged_unverified_template"),
])
def test_policy_changes_only_the_request_copy(capabilities, expected):
    controls = {"enable_thinking": True, "other_template_option": "retained"}
    before = copy.deepcopy(controls)
    payload = {"chat_template_kwargs": controls, "max_tokens": 321, "temperature": 0.1}
    assert structured_response.apply_structured_request_policy(payload, capabilities) == expected
    assert controls == before
    assert payload["max_tokens"] == 321 and payload["temperature"] == 0.1
    assert payload["chat_template_kwargs"]["other_template_option"] == "retained"
    assert payload["chat_template_kwargs"]["enable_thinking"] is (expected == "unchanged_unverified_template")


@pytest.mark.parametrize("template,controlled", [(CHAT_TRUE_ELSE, True), (CHANNEL, True), ("unknown", False)])
def test_real_json_request_policy_does_not_leak_to_normal_chat(variant, runner, monkeypatch, template, controlled):
    client, requests = transport.wire_transport(variant, runner, monkeypatch, "valid")
    monkeypatch.setattr(client, "_probe_with_fallback", lambda _path: {
        "status_code": 200, "json": {"chat_template": template,
                                      "default_generation_settings": {"n_ctx": runner.config.num_ctx}},
    })
    base = variant._APP.base
    base.set_qubitz_chat_template_kwargs({"enable_thinking": True, "other_template_option": "retained"})
    try:
        client.qubitz_json_chat(model_name=runner.config.model_name,
                                messages=[{"role": "user", "content": "Return the requested object."}],
                                schema_name="unrelated_object", schema={"type": "object"}, num_predict=321)
        assert len(requests) == 1 and requests[0]["max_tokens"] == 321
        assert requests[0]["chat_template_kwargs"]["enable_thinking"] is (not controlled)
        monkeypatch.setattr(client, "_call_with_fallback", lambda _method, _path, payload: (
            requests.append(payload) or transport.response_body("valid")))
        client.chat(model_name=runner.config.model_name, messages=[{"role": "user", "content": "Explain."}],
                    tools=[], num_predict=64, temperature=0.6, top_p=0.9, min_p=0.01, repeat_penalty=1.02)
        assert len(requests) == 2 and requests[1]["chat_template_kwargs"]["enable_thinking"] is True
    finally:
        base.set_qubitz_chat_template_kwargs(None)


@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_failed_json_transport_is_not_replayed_and_accepts_a_new_request(variant, runner, monkeypatch, error_type):
    calls = []

    def fail_first():
        calls.append(True)
        if len(calls) == 1:
            raise error_type("injected transport failure")

    client, requests = transport.wire_transport(variant, runner, monkeypatch, "valid", fail_first)
    arguments = {"model_name": runner.config.model_name, "messages": [{"role": "user", "content": "Object."}],
                 "schema_name": "another_object", "schema": {"type": "object"}, "num_predict": 128}
    with pytest.raises((RuntimeError, httpx.HTTPError)):
        client.qubitz_json_chat(**arguments)
    assert len(requests) == len(calls) == 1
    assert client.qubitz_json_chat(**arguments) == {"patches": []}
    assert len(requests) == len(calls) == 2


NUMERIC_TASKS = [
    ("weights.py", "rescale", "value, multiplier",
     "    if multiplier < 0:\n        raise ValueError('negative multiplier')\n    return value * multiplier\n",
     "return the product and reject negative multipliers with ValueError",
     [([0, 2], 0, ""), ([3, 2], 6, ""), ([3, -1], None, "ValueError")]),
    ("offsets.py", "shift", "level, delta",
     "    if level < -2:\n        raise ValueError('level too low')\n    return level + delta\n",
     "return the sum and reject levels below -2 with ValueError",
     [([0, -5], -5, ""), ([-2, 0], -2, ""), ([-3, 7], None, "ValueError")]),
]


@pytest.mark.parametrize("task", NUMERIC_TASKS)
@pytest.mark.parametrize("mode", ["verified", "abstained", "single_case"])
def test_task_neutral_numeric_contract_and_next_task(variant, runner, tmp_path, monkeypatch, task, mode):
    filename, function, signature, body, behavior, examples = task
    target = tmp_path / filename
    before = "def retain(value):\n    return value\n"
    after = before + f"\ndef {function}({signature}):\n" + body
    target.write_text(before)
    proposals, passes = [], []
    cases = [{"path": filename, "function": function, "args": args, "expected": expected, "raises": raises}
             for args, expected, raises in examples]

    def propose(**kwargs):
        proposals.append(kwargs)
        return {"checks": wire_checks(cases if mode == "verified" else [] if mode == "abstained" else cases[:1])}

    async def model_pass(self, prompt, callback=None):
        passes.append(prompt)
        broker = self._tool_permission_broker
        if len(passes) > 1:
            creation.record_creation(broker, tmp_path / "receipt.txt", "ready")
            return "Saved receipt.txt."
        broker.required_postconditions.add("tests")
        broker.transaction_originals[str(target)] = {"data": before.encode()}
        target.write_text(after)
        broker.record_result("apply_text_patch", {"path": str(target)}, receipts.result({
            "status": "completed", "path": str(target), "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }))
        self._raise_if_cancelled()
        return "Saved the requested change."

    monkeypatch.setattr(handoff.parent_runner(runner), "_run_async", model_pass)
    monkeypatch.setattr(runner, "_get_llm", lambda: type("ProposalClient", (), {"qubitz_json_chat": staticmethod(propose)})())
    receipts.install_host(variant, runner, monkeypatch)
    answer = asyncio.run(runner._run_async(f"Add {function}({signature}) to {filename}: {behavior}. Preserve retain."))
    assert len(passes) == 1 and len(proposals) == (2 if mode == "single_case" else 1)
    assert target.read_text() == after
    prompt = proposals[0]["messages"][0]["content"]
    assert "zero numerator" not in prompt and "zero divisor" not in prompt
    assert proposals[0]["schema"]["properties"]["checks"]["minItems"] == 0
    verification = runner_snapshot(runner)["verification_receipts"]
    assert len(verification) == 1
    if mode == "verified":
        assert "[Completion status: verified]" in answer and verification[0]["success"] is True
        assert len(verification[0]["case_results"]) == 3
    else:
        assert "[Completion status: partial]" in answer and verification[0]["success"] is False
        assert verification[0]["outcome"] == "unavailable"
    following = asyncio.run(runner._run_async("Create receipt.txt containing exactly ready."))
    assert "[Completion status: verified]" in following
    assert runner_snapshot(runner)["verification_receipts"] == []
    assert len(proposals) == (2 if mode == "single_case" else 1) and target.read_text() == after
