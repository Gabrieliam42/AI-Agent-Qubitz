"""Generic template and rejected-behavior feedback regressions, without inference."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from pathlib import Path

import pytest
from jinja2.sandbox import SandboxedEnvironment

from evaluation import test_followup_state_receipts as receipts
from evaluation import test_structured_request_policy as policy
from evaluation import test_tool_argument_boundaries as boundaries
from runtime.default_behavior import compare, counterexample, recovery_counterexamples
from runtime.structured_response import (
    apply_structured_request_policy,
    non_thinking_template_contract,
)

variant = receipts.variant
runner = receipts.runner

NORMALIZED = """{# task-neutral captured template contract #}
{%- if enable_thinking is defined %}
 {%- if enable_thinking %}{%- set thinking_option = 'on' %}
 {%- else %}{%- set thinking_option = 'off' %}{%- endif %}
{%- elif thinking_option is not defined %}{%- set thinking_option = 'on' %}{%- endif %}
{{ messages[0].content }}
{%- if add_generation_prompt %}{{- '<role>ASSISTANT</role>' }}
 {%- if thinking_option == 'on' %}{{- '\\n<think>' }}
 {%- elif thinking_option == 'off' %}{{- '\\n<think></think>' }}{%- endif %}{%- endif %}
"""


def test_normalized_contract_matches_actual_offline_render():
    assert non_thinking_template_contract(NORMALIZED) == "normalized_thinking_option_prefill"
    template = SandboxedEnvironment().from_string(NORMALIZED)
    messages = [{"content": "An unrelated structured task"}]
    assert template.render(messages=messages, add_generation_prompt=True, enable_thinking=False).endswith("<think></think>")
    assert template.render(messages=messages, add_generation_prompt=True).endswith("<think>")
    options = {"enable_thinking": True, "other_option": 7}
    payload = {"chat_template_kwargs": options, "max_tokens": 512}
    assert apply_structured_request_policy(payload, {"structured_thinking_control": non_thinking_template_contract(NORMALIZED)}) == "template_non_thinking"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False, "other_option": 7}
    assert options["enable_thinking"] is True and payload["max_tokens"] == 512


@pytest.mark.parametrize("source", [
    NORMALIZED.replace("set thinking_option = 'off'", "set thinking_option = 'on'"),
    NORMALIZED.replace("</think>", "not closed"),
    NORMALIZED.replace("{{ messages[0].content }}", "{% set thinking_option = 'on' %}"),
    NORMALIZED.replace("{{ messages[0].content }}", "{% set enable_thinking = true %}"),
    "{# " + NORMALIZED.replace("{# task-neutral captured template contract #}", "") + " #}",
    "{% raw %}" + NORMALIZED + "{% endraw %}",
    NORMALIZED + "unexpected suffix",
])
def test_incomplete_or_rebound_contract_stays_unverified(source):
    assert non_thinking_template_contract(source) == "unverified"


def test_normalized_json_policy_does_not_leak_into_normal_chat(variant, runner, monkeypatch):
    policy.test_real_json_request_policy_does_not_leak_to_normal_chat(variant, runner, monkeypatch, NORMALIZED, True)


BEFORE = "def aggregate(values):\n    return sum(values)\n"
BAD = "def aggregate(values, factor=1):\n    return sum(values) * factor / 1\n"


def comparison(path="compute.py"):
    item = compare(BEFORE, BAD)
    item.update(turn_id="current", workspace_generation=3,
                before_sha256=hashlib.sha256(BEFORE.encode()).hexdigest(),
                after_sha256=hashlib.sha256(BAD.encode()).hexdigest())
    return {path: item}


def test_counterexample_preserves_typed_failure_not_acceptance():
    item = comparison()["compute.py"]
    before = copy.deepcopy(item)
    detail = counterexample(item)
    assert detail["arguments_repr"] == "([],)"
    assert detail["before"] == ["int", "0"] and detail["after"] == ["float", "0.0"]
    assert item == before and item["status"] == "failed"


@pytest.mark.parametrize("stale", ["turn", "workspace", "source", "candidate_hash"])
def test_stale_or_unbound_comparison_not_delivered(stale):
    items = comparison()
    hashes = {"compute.py": items["compute.py"]["before_sha256"]}
    if stale == "turn":
        items["compute.py"]["turn_id"] = "old"
    elif stale == "workspace":
        items["compute.py"]["workspace_generation"] = 2
    elif stale == "source":
        hashes["compute.py"] = "0" * 64
    else:
        items["compute.py"]["after_sha256"] = "not a hash"
    assert not recovery_counterexamples(items, turn_id="current", workspace_generation=3, snapshots=hashes)


def test_counterexample_payload_is_bounded():
    item = {"status": "failed", "cases": [{"matched": False, "function": "f" * 200,
            "arguments_repr": "x" * 10000, "before": ["str", "a" * 10000], "after": ["str", "b" * 10000]}]}
    result = counterexample(item)
    assert result["truncated"] is True and len(json.dumps(result)) < 1600


def test_actual_admission_returns_counterexample_without_dispatch(variant, runner):
    target = runner.workspace / "compute.py"
    target.write_text(BEFORE)
    broker = receipts.begin(runner, "Edit compute.py to add an optional factor. Preserve omitted-argument behavior and result types.")
    boundaries.record_snapshot(broker, target)
    server = variant._build_local_mcp_server(variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__))
    host = boundaries.host_for(variant, broker, server)
    result = asyncio.run(host.call_tool("apply_text_patch", {"path": "compute.py", "replacements": [
        {"old_text": BEFORE, "new_text": BAD},
    ]}))
    data = result.structuredContent
    assert result.isError and data["mutation_dispatched"] is False and data["file_unchanged"] is True
    assert data["behavioral_counterexamples"][0]["before"] == ["int", "0"]
    assert host.dispatched == [] and target.read_text() == BEFORE


def test_focused_recovery_receives_hash_bound_rejection(variant, runner, monkeypatch):
    target = runner.workspace / "compute.py"
    target.write_text(BEFORE)
    prompt = "Change compute.py's existing aggregate function with an optional factor. Preserve omitted-argument behavior and result types."
    broker = receipts.begin(runner, prompt)
    boundaries.record_snapshot(broker, target)
    broker.transaction_explicit_file_targets = {str(target)}
    server = variant._build_local_mcp_server(variant._APP.base, runner.workspace, runner.workspace, Path(variant.__file__))
    host = boundaries.host_for(variant, broker, server)
    rejected = asyncio.run(host.call_tool("apply_text_patch", {"path": "compute.py", "replacements": [
        {"old_text": BEFORE, "new_text": BAD},
    ]}))
    assert rejected.isError and not host.dispatched
    requests = []

    def propose(**kwargs):
        requests.append(kwargs)
        return {"patches": []}

    receipts.install_host(variant, runner, monkeypatch)
    monkeypatch.setattr(runner, "_get_llm", lambda: type("Client", (), {"qubitz_json_chat": staticmethod(propose)})())
    asyncio.run(runner._wrapper_transaction_proposal_recovery(prompt, "", None))
    assert requests and "rejected_candidate_sha256" in requests[0]["messages"][1]["content"]
    assert target.read_text() == BEFORE
