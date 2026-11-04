"""Explicit evaluator-only fault injection; never loaded by a product runtime."""
from __future__ import annotations

import uuid
from types import SimpleNamespace


def gui_identity(runner, gui) -> dict:
    return {"runner_identity": id(runner), "gui_identity": id(gui) if gui is not None else None,
            "window_id": str(gui.root.winfo_id()) if gui is not None else None,
            "gui_agent_identity": id(gui.agent) if gui is not None else None}


class StructuredFailureFixture:
    """Exercise the real edit orchestrator with an injected unusable JSON proposal."""

    def __init__(self, runner, base, root, observer, gui):
        self.runner, self.base, self.root, self.observer, self.gui = runner, base, root, observer, gui
        self.parent = next(cls for cls in type(runner).__mro__
                           if cls.__name__ == "HardenedAgentRunner").__mro__[1]
        self.original_run = self.parent._run_async
        self.had_client_override = "_get_llm" in runner.__dict__
        self.original_client_override = runner.__dict__.get("_get_llm")
        self.state = {"scope": "evaluator_fixture_only", "fault": "invalid_json",
                      "identity": gui_identity(runner, gui), "model_passes": 0,
                      "injection_count": 0, "native_generation_for_injected_proposal": False,
                      "host_rejections": [],
                      "request_id": uuid.uuid4().hex}
        self.installed = False

    def install(self):
        from runtime.structured_response import StructuredResponseError

        state, instance, original, base = self.state, self.runner, self.original_run, self.base

        async def model_pass(runner, prompt, callback=None):
            if runner is not instance:
                return await original(runner, prompt, callback)
            state["model_passes"] += 1
            if state["model_passes"] > 2:
                raise RuntimeError("Fixture exceeded the initial and one native-correction model pass")
            async with base.MCPHost(runner.workspace) as host:
                await host.list_tools()
                await host.call_tool("read_file_snapshot", {"path": "state.txt"})
                rejected = await host.call_tool("apply_text_patch", {
                    "path": "state.txt", "replacements": [{
                        "old_text": "__injected_missing_" + state["request_id"] + "__",
                        "new_text": "changed\n",
                    }],
                })
            data = getattr(rejected, "structuredContent", None) or {}
            state["host_rejection"] = {"is_error": bool(getattr(rejected, "isError", False)),
                                       "status": data.get("status"),
                                       "dispatch_attempted": data.get("dispatch_attempted"),
                                       "mutation_dispatched": data.get("mutation_dispatched")}
            state["host_rejections"].append(dict(state["host_rejection"]))
            self.observer.emit("structured_fixture_host_rejection", **state["host_rejection"])
            return "The fixture edit was not committed."

        def proposal(**kwargs):
            state["injection_count"] += 1
            self.observer.emit("structured_fixture_injection", request_id=state["request_id"],
                               schema_name=kwargs.get("schema_name"),
                               scope=state["scope"], native_generation=False)
            error = StructuredResponseError("invalid_json", schema_name=kwargs["schema_name"],
                                            request_id=state["request_id"], finish_reason="stop",
                                            content_characters=1,
                                            detail="Explicit evaluator-only malformed JSON fixture")
            error.receipt["request_policy"] = "fixture_only_no_native_generation"
            raise error

        self.parent._run_async = model_pass
        self.runner._get_llm = lambda: SimpleNamespace(qubitz_json_chat=proposal)
        self.installed = True
        return self.state

    def restore(self):
        if not self.installed:
            return
        self.parent._run_async = self.original_run
        if self.had_client_override:
            self.runner._get_llm = self.original_client_override
        else:
            del self.runner._get_llm
        self.installed = False
        self.state["restored"] = True


def containment_verified(observed: dict) -> bool:
    fixture = observed.get("structured_failure_fixture") or {}
    rejected = fixture.get("host_rejection") or {}
    runtime = observed.get("runtime") or {}
    errors = runtime.get("recovery_receipts") or []
    return (fixture.get("scope") == "evaluator_fixture_only" and fixture.get("restored") is True
            and fixture.get("injection_count") == 1 and 1 <= fixture.get("model_passes", 0) <= 2
            and len(fixture.get("host_rejections", [])) == fixture.get("model_passes")
            and all(x.get("is_error") is True and x.get("status") == "patch_text_mismatch"
                    and x.get("dispatch_attempted") is False and x.get("mutation_dispatched") is False
                    for x in fixture.get("host_rejections", []))
            and rejected.get("is_error") is True and rejected.get("status") == "patch_text_mismatch"
            and rejected.get("dispatch_attempted") is False and rejected.get("mutation_dispatched") is False
            and any(x.get("outcome") == "invalid_json" and x.get("request_id") == fixture.get("request_id")
                    and x.get("mutation_dispatched") is False for x in errors)
            and observed.get("completed") is True and not observed.get("exception")
            and "[Completion status: partial]" in observed.get("assistant", ""))


def followup_verified(observed: dict) -> bool:
    followup = observed.get("same_gui_error_followup") or {}
    runtime = observed.get("runtime") or {}
    control = runtime.get("turn_control") or {}
    receipts = [*(runtime.get("recovery_receipts") or []), *(runtime.get("structured_proposal_receipts") or [])]
    return (followup.get("original_identity") == followup.get("current_identity")
            and followup.get("original_identity", {}).get("gui_identity") is not None
            and bool(followup.get("error_turn_id")) and bool(control.get("turn_id"))
            and followup["error_turn_id"] != control["turn_id"]
            and all(isinstance(receipt, dict) and receipt.get("turn_id") == control["turn_id"] for receipt in receipts)
            and observed.get("completed") is True and not observed.get("exception")
            and (observed.get("generation_quality") or {}).get("model_requests", 0) > 0)
