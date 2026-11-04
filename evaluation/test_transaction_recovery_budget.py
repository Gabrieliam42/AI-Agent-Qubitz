"""Exercise the actual Llama budget installer without constructing a model."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime import read_variant_source
from runtime.paths import variant_path

ROOT = Path(__file__).resolve().parents[1]
NAME = "AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py"


@pytest.fixture(scope="module")
def budget_namespace():
    path = variant_path(NAME, ROOT)
    tree = ast.parse(read_variant_source(path))
    names = {
        "_install_llama_3_3_transaction_recovery_budget",
        "_llama_3_3_messages_with_reasoning_directive",
        "LLAMA_3_3_NEMOTRON_SUPER_TRANSACTION_RECOVERY_STEPS",
        "LLAMA_3_3_NEMOTRON_SUPER_TRANSACTION_RECOVERY_NUM_PREDICT",
        "LLAMA_3_3_NEMOTRON_SUPER_SIMPLE_REASONING_DIRECTIVE",
    }
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names or isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in names for target in node.targets
        ):
            nodes.append(node)
    assert len(nodes) == len(names) + 1
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)  # noqa: S102
    assert namespace["LLAMA_3_3_NEMOTRON_SUPER_TRANSACTION_RECOVERY_STEPS"] == 3
    assert namespace["LLAMA_3_3_NEMOTRON_SUPER_TRANSACTION_RECOVERY_NUM_PREDICT"] == 768
    return namespace


@pytest.mark.parametrize("getter_override", [False, True])
@pytest.mark.parametrize("chat_override", [False, True])
@pytest.mark.parametrize("outcome", ["no_op", "success", "parent_error", "lookup_error", "budget_exhausted"])
def test_lazy_lookup_preserves_budget_and_restores_overrides(
    budget_namespace, getter_override, chat_override, outcome,
):
    lookups = []
    calls = []
    statuses = []
    messages = [{"role": "system", "content": "Original system message"}]

    class Model:
        def qubitz_json_chat(self, **kwargs):
            calls.append(kwargs)
            return {"response": "bounded"}

    model = Model()
    if chat_override:
        model.qubitz_json_chat = lambda **kwargs: Model.qubitz_json_chat(model, **kwargs)
    original_chat = model.qubitz_json_chat

    class Parent:
        def _get_llm(self):
            lookups.append(True)
            if outcome == "lookup_error":
                raise RuntimeError("lookup failed")
            return model

        async def _wrapper_transaction_proposal_recovery(self, _prompt, _answer, callback):
            if outcome == "no_op":
                return None
            current = self._get_llm()
            assert self._get_llm() is current
            limit = 4 if outcome == "budget_exhausted" else 3
            for index in range(limit):
                current.qubitz_json_chat(messages=messages, num_predict=128 if index == 0 else 16384)
                if outcome == "parent_error":
                    raise RuntimeError("parent failed")
            return "recovered"

    app = SimpleNamespace(base=SimpleNamespace(AgentRunner=Parent))
    install = budget_namespace["_install_llama_3_3_transaction_recovery_budget"]
    install(app)
    installed = app.base.AgentRunner
    install(app)
    assert app.base.AgentRunner is installed
    runner = installed()
    runner.config = SimpleNamespace(num_predict=16384)
    if getter_override:
        runner._get_llm = lambda: Parent._get_llm(runner)
    original_getter = runner._get_llm
    callback = lambda kind, text: statuses.append((kind, text))
    recovery = runner._wrapper_transaction_proposal_recovery("prompt", "answer", callback)
    errors = {
        "parent_error": "parent failed",
        "lookup_error": "lookup failed",
        "budget_exhausted": "model-step budget exhausted",
    }
    if outcome in errors:
        with pytest.raises(RuntimeError, match=errors[outcome]):
            asyncio.run(recovery)
    else:
        assert asyncio.run(recovery) == (None if outcome == "no_op" else "recovered")
    assert runner._get_llm == original_getter
    assert ("_get_llm" in runner.__dict__) is getter_override
    assert model.qubitz_json_chat == original_chat
    assert ("qubitz_json_chat" in model.__dict__) is chat_override
    assert len(lookups) == (0 if outcome == "no_op" else 1)
    assert len(calls) == {"no_op": 0, "lookup_error": 0, "parent_error": 1}.get(outcome, 3)
    assert len(statuses) == len(calls)
    assert messages == [{"role": "system", "content": "Original system message"}]
    for index, call in enumerate(calls):
        assert call["num_predict"] == (128 if index == 0 else 768)
        assert call["messages"][0]["content"].endswith("/no_think")
        assert statuses[index][0] == "status"
        assert f"{index + 1}/3" in statuses[index][1]
