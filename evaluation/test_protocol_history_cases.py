"""Pair validation must not alter valid provider histories or multimodal content."""
from __future__ import annotations

import copy

import pytest

from runtime.recovery_protocol import append_recovery_instruction, validate_tool_history


def group(key):
    return [{"role": "assistant", "content": "", "tool_calls": [{"id": key, "type": "function",
             "function": {"name": "read", "arguments": '{"path":"exact"}'}}]},
            {"role": "tool", "tool_call_id": key, "content": "exact result"}]


def test_resolved_call_id_can_be_reused_in_a_later_unambiguous_group():
    messages = [{"role": "user", "content": "Read the target."}, *group("call-0"), *group("call-0")]
    before = copy.deepcopy(messages)
    validate_tool_history(messages)
    updated = append_recovery_instruction(messages, "Continue from verified state.")
    assert updated[1:] == messages[1:] and messages == before


def test_parallel_results_preserve_original_ids_order_and_bytes():
    messages = [{"role": "user", "content": "Read both targets."}, group("a")[0]]
    messages[-1]["tool_calls"].extend(group("b")[0]["tool_calls"])
    messages.extend([group("b")[1], group("a")[1]])
    updated = append_recovery_instruction(messages, "Continue safely.")
    assert updated[1:] == messages[1:]
    validate_tool_history(updated)


def test_multimodal_task_preserves_existing_parts_and_tool_evidence():
    parts = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
             {"type": "text", "text": "Inspect the target."}]
    messages = [{"role": "user", "content": parts}, *group("read-1")]
    before = copy.deepcopy(messages)
    updated = append_recovery_instruction(messages, "Use the exact snapshot.")
    assert messages == before and updated[0]["content"][:-1] == parts
    assert updated[1:] == messages[1:]


@pytest.mark.parametrize("messages", [[*group("read-1")],
                                      [{"role": "user", "content": "Read."}, group("read-1")[0]]])
def test_missing_task_or_incomplete_tool_pair_is_not_fabricated(messages):
    before = copy.deepcopy(messages)
    with pytest.raises(ValueError):
        append_recovery_instruction(messages, "Continue.")
    assert messages == before
