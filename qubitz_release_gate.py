from __future__ import annotations

import ast
import asyncio
import gc
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

from runtime import read_variant_source, runtime_dependency_paths
from runtime.variant_policy import user_variant_allowed

ROOT = Path(__file__).resolve().parent
VARIANT_GLOB = "AI_Agent_Qubitz_*.py"
PROFILES_PATH = ROOT / "qubitz_variant_profiles.json"
TEMPLATE_PATH = ROOT / "qubitz_common_wrapper_template.json"
EXPECTED_TEMPLATE_SCHEMA_VERSION = 4
EXPECTED_PROFILE_SCHEMA_VERSION = 2
RELEASE_PYTHON_FILES = (
    "qubitz_disposable_eval.py",
    "qubitz_release_gate.py",
    "qubitz_variant_builder.py",
    "evaluation/disposable_followup_matrix.py",
    "evaluation/browser_observation.py",
    "evaluation/verification_outcomes.py",
    "evaluation/solar_artifact_evidence.py",
    "evaluation/smoke_create_calculator.py",
    "evaluation/test_generation_completion_regressions.py",
)
LOCAL_HARNESS_KEY_NAME = "QUBITZ_HARNESS_KEY.local.txt"
REFERENCE_VARIANT_NAME = "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"
REQUIRED_HARDENED_RUNNER_METHODS = (
    "_enforce_research_response_urls",
    "_prefetch_named_source_context",
)
REQUIRED_HARDENED_HOST_METHODS = (
    "_consume_parallel_read_only_batch",
    "_execute_workflow",
    "_parallel_read_only_tool",
    "_resolve_workflow_value",
)
DIRECT_WORKSPACE_MUTATION_TOOLS = frozenset({
    "apply_text_patch",
    "apply_text_patches",
    "copy_path",
    "delete_path",
    "make_directory",
    "move_path",
    "replace_text",
    "write_file",
    "write_files",
})
PATCH_MUTATION_TOOLS = frozenset({
    "apply_docx_text_patch",
    "apply_notebook_text_patch",
    "apply_spreadsheet_cell_patch",
})
SPECIAL_LOCAL_TOOLS = PATCH_MUTATION_TOOLS | {
    "create_sandbox",
    "inspect_python_environment",
    "search_web",
}
SAMPLING_ROUTE_CASES = (
    ("simple_question", "What is two plus two?", "simple_answer"),
    ("mcp_tool", "Use the MCP tool to open this URL.", "tool_loop"),
    ("coding_edit", "Implement a focused edit in app.py.", "retrieval_plus_model"),
    ("debugging_reasoning", "Diagnose this failing test traceback.", "retrieval_plus_model"),
    ("brainstorming_explanation", "Brainstorm and explain two approaches.", "retrieval_plus_model"),
    ("mcp_tool", "Install the requested Python package.", "dependency_install"),
)
VARIANT_SAMPLING_FAMILIES = {
    "AI_Agent_Qubitz_AgenticQwen-30B-A3B.i1-Q4.py": "agenticqwen",
    "AI_Agent_Qubitz_CalibForge-35B-A3B.i1-Q4.py": "qwen3_6_35",
    "AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py": "fallback",
    "AI_Agent_Qubitz_Devstral-Small-2-24B-Q4.py": "devstral",
    "AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.py": "gemma_4",
    "AI_Agent_Qubitz_Gemma-4-31B-IT_QAT-Q4.py": "gemma_4",
    "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py": "glm_4_7",
    "AI_Agent_Qubitz_GPT-OSS-120B_F16.py": "gpt_oss",
    "AI_Agent_Qubitz_GPT-OSS-20B_F16.py": "gpt_oss",
    "AI_Agent_Qubitz_Granite-4.2-30B_Q5.py": "granite_4_2",
    "AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py": "granite_4_2",
    "AI_Agent_Qubitz_Hypernova-60B-2605-Q5.py": "hypernova",
    "AI_Agent_Qubitz_KAT-Coder-V2.5-Dev-35B-A3B-Q4.py": "qwen3_6_35",
    "AI_Agent_Qubitz_Kimi-Linear-48B-A3B-Instruct.i1-Q6.py": "fallback",
    "AI_Agent_Qubitz_Laguna-XS-2.1-33B-A3B_Q4.py": "laguna_xs_2_1",
    "AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py": "fallback",
    "AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py": "llama_3_3_nemotron_super",
    "AI_Agent_Qubitz_Macaron-V1-Tall-35B-A3B.i1-Q4.py": "qwen3_6_35",
    "AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py": "ministral3_instruct_fallback",
    "AI_Agent_Qubitz_Muse-Glimmer-30B_Q5.py": "muse_glimmer",
    "AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py": "nex_n2_5_mini",
    "AI_Agent_Qubitz_Nemotron-3.5-Lightning-30B-A3B-IQ4.py": "nemotron_3_5_lightning",
    "AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py": "nemotron_3_5_super_vl",
    "AI_Agent_Qubitz_Nemotron-Cascade-2-30B-A3B-IQ4.py": "nemotron_cascade_2",
    "AI_Agent_Qubitz_North-Mini-Code-1.0-30B-A3B-Q4.py": "north_mini_code",
    "AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py": "ornith",
    "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py": "ornith",
    "AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py": "qwen3_coder_next",
    "AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py": "qwen3_5",
    "AI_Agent_Qubitz_Qwen3.6-35B-A3B_MTP_Q4.py": "qwen3_6_35",
    "AI_Agent_Qubitz_Qwen3.8-27B_Q4.py": "qwen3_8",
    "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py": "qwen3_8",
    "AI_Agent_Qubitz_LFM2.5-24B-A2B-Q5.py": "lfm2_5_24b_a2b",
    "AI_Agent_Qubitz_Mellum2.1-12B-A2.5B-Thinking-Q6.py": "mellum2_1_12b_a2_5b",
    "AI_Agent_Qubitz_Holo4-27B-Q4.py": "holo4_27b",
    "AI_Agent_Qubitz_Occamy-1.0-35B-A3B-Q4.py": "occamy_1_0",
    "AI_Agent_Qubitz_AREX-2-27B.i1-Q6.py": "arex_2",
    "AI_Agent_Qubitz_MiniCPM-V-4.7-35B-A3B-Q4_1.py": "minicpm_v_4_7",
    "AI_Agent_Qubitz_SERA-32B.i1-Q5.py": "fallback",
    "AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py": "tiel_coder",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_checked(label: str, command: Sequence[str]) -> None:
    print(f"[gate] {label}")
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}.")


def variant_paths() -> list[Path]:
    from runtime.paths import variant_paths as discover_variant_paths

    paths = discover_variant_paths(ROOT)
    profile_payload = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    expected_names = {str(item["filename"]) for item in profile_payload.get("variants", [])
                      if user_variant_allowed(item["filename"])}
    actual_names = {path.name for path in paths}
    if actual_names != expected_names:
        raise RuntimeError(
            "Variant launcher/profile mismatch: "
            f"missing={sorted(expected_names - actual_names)}, "
            f"unregistered={sorted(actual_names - expected_names)}."
        )
    return paths


SamplingSettings = tuple[float, float, int, float, float, float]


def _sampling_profiles_for_family(family: str) -> dict[str, SamplingSettings]:
    route_names = (
        "simple_question",
        "mcp_tool",
        "coding_edit",
        "debugging_reasoning",
        "brainstorming_explanation",
    )

    def uniform(settings: SamplingSettings) -> dict[str, SamplingSettings]:
        return {name: settings for name in route_names}

    if family == "fallback":
        return uniform((0.70, 0.95, 0, 0.0, 1.0, 0.0))
    if family == "agenticqwen":
        profiles = uniform((0.60, 0.95, 20, 0.0, 1.0, 0.0))
        profiles["simple_question"] = (0.70, 0.80, 20, 0.0, 1.0, 0.0)
        profiles["brainstorming_explanation"] = profiles["simple_question"]
        return profiles
    if family == "qwen3_coder_next":
        return uniform((1.00, 0.95, 40, 0.0, 1.0, 0.0))
    if family == "lfm2_5_24b_a2b":
        return uniform((0.10, 1.00, 50, 0.0, 1.05, 0.0))
    if family == "mellum2_1_12b_a2_5b":
        return uniform((0.60, 0.95, 20, 0.0, 1.0, 0.0))
    if family == "holo4_27b":
        return uniform((1.00, 0.95, 20, 0.0, 1.0, 0.0))
    if family == "occamy_1_0":
        return uniform((1.00, 0.95, 20, 0.0, 1.0, 1.5))
    if family == "arex_2":
        return uniform((1.00, 0.95, 20, 0.0, 1.0, 0.0))
    if family == "minicpm_v_4_7":
        return uniform((1.00, 0.95, 20, 0.0, 1.00, 0.0))
    if family == "qwen3_8":
        profiles = uniform((1.00, 0.95, 20, 0.0, 1.0, 0.0))
        profiles["simple_question"] = (0.70, 0.80, 20, 0.0, 1.0, 1.5)
        profiles["brainstorming_explanation"] = profiles["simple_question"]
        return profiles
    if family == "muse_glimmer":
        return uniform((1.00, 0.95, 64, 0.0, 1.0, 0.0))
    if family == "nex_n2_5_mini":
        return uniform((0.70, 0.95, 40, 0.0, 1.0, 0.0))
    if family in {"qwen3_6_27", "qwen3_6_35"}:
        presence = 0.0 if family == "qwen3_6_27" else 1.5
        profiles = uniform((1.00, 0.95, 20, 0.0, 1.0, presence))
        profiles["simple_question"] = (0.70, 0.80, 20, 0.0, 1.0, 1.5)
        profiles["coding_edit"] = (0.60, 0.95, 20, 0.0, 1.0, 0.0)
        profiles["brainstorming_explanation"] = profiles["simple_question"]
        return profiles
    if family == "qwen3_5":
        profiles = uniform((1.00, 0.95, 20, 0.0, 1.0, 1.5))
        profiles["simple_question"] = (0.70, 0.80, 20, 0.0, 1.0, 1.5)
        profiles["coding_edit"] = (0.60, 0.95, 20, 0.0, 1.0, 0.0)
        profiles["brainstorming_explanation"] = profiles["simple_question"]
        return profiles
    if family in {"glm_4_7", "glm_4_7_huihui"}:
        profiles = uniform((1.00, 0.95, 0, 0.0, 1.0, 0.0))
        profiles["mcp_tool"] = (0.70, 1.00, 0, 0.0, 1.0, 0.0)
        profiles["coding_edit"] = profiles["mcp_tool"]
        return profiles
    if family == "gemma_4":
        return uniform((1.00, 0.95, 64, 0.0, 1.0, 0.0))
    if family == "devstral":
        return uniform((0.20, 1.00, 0, 0.0, 1.0, 0.0))
    if family == "ministral3_instruct_fallback":
        return uniform((0.05, 1.00, 0, 0.0, 1.0, 0.0))
    if family == "gpt_oss":
        return uniform((1.00, 1.00, 0, 0.0, 1.0, 0.0))
    if family == "granite_4_2":
        return uniform((1.00, 0.95, 0, 0.0, 1.0, 0.0))
    if family == "granite_4_1":
        return uniform((0.70, 0.95, 0, 0.0, 1.0, 0.0))
    if family == "llama_3_3_nemotron_super":
        return uniform((0.60, 0.95, 0, 0.0, 1.0, 0.0))
    if family in {"nemotron_3_5_lightning", "nemotron_3_5_super_vl", "nemotron_cascade_2"}:
        return uniform((1.00, 0.95, 0, 0.0, 1.0, 0.0))
    if family == "nemotron_3_nano":
        profiles = uniform((1.00, 1.00, 0, 0.0, 1.0, 0.0))
        profiles["simple_question"] = (0.60, 0.95, 0, 0.0, 1.0, 0.0)
        profiles["mcp_tool"] = profiles["simple_question"]
        profiles["coding_edit"] = profiles["simple_question"]
        return profiles
    if family == "north_mini_code":
        return uniform((1.00, 0.95, 0, 0.0, 1.0, 0.0))
    if family in {"ornith", "tiel_coder"}:
        profiles = uniform((1.00, 0.95, 20, 0.0, 1.0, 1.5))
        profiles["simple_question"] = (0.60, 0.95, 20, 0.0, 1.0, 0.0)
        profiles["mcp_tool"] = profiles["simple_question"]
        profiles["coding_edit"] = profiles["simple_question"]
        return profiles
    if family == "laguna_xs_2_1":
        return uniform((0.70, 1.00, 20, 0.0, 1.0, 0.0))
    if family == "hypernova":
        profiles = uniform((1.00, 1.00, 0, 0.0, 1.0, 0.0))
        profiles["simple_question"] = (0.70, 1.00, 0, 0.0, 1.0, 0.0)
        profiles["brainstorming_explanation"] = profiles["simple_question"]
        return profiles
    if family == "xorton":
        return uniform((0.70, 1.00, 0, 0.0, 1.0, 0.0))
    raise RuntimeError(f"No deterministic sampling contract for family {family!r}.")


def _isolate_variant_memory(module: Any) -> None:
    store = module._APP.base.MemoryStore
    original = store.__init__
    temporary = tempfile.TemporaryDirectory(prefix="qubitz_gate_memory_", dir=ROOT / ".cache")

    def initialize(instance: Any, workspace: Path, *args: Any, **kwargs: Any) -> None:
        target = Path(temporary.name) if Path(workspace).resolve() == ROOT else workspace
        original(instance, target, *args, **kwargs)

    module._gate_memory_scope = (store, original, temporary)
    store.__init__ = initialize


def _unload_variant(module: Any) -> None:
    try:
        scope = getattr(module, "_gate_memory_scope", None)
        if scope is not None:
            store, original, temporary = scope
            store.__init__ = original
            del module._gate_memory_scope
            temporary.cleanup()
    finally:
        sys.modules.pop(module.__name__, None)


def _import_variant(path: Path, label: str) -> Any:
    module_name = f"_qubitz_{label}_{hashlib.sha256(path.name.encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path.name}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        _isolate_variant_memory(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def check_turn_relation_behavior(path: Path) -> None:
    module = _import_variant(path, "turn_relation")
    try:
        completed_history = [
            {"role": "user", "content": "Compare GLM and Granite performance."},
            {"role": "assistant", "content": "Granite was faster, while GLM was more reliable."},
        ]
        cases = (
            ("why?", completed_history, "follow_up"),
            ("Regarding your last reply, are you sure?", completed_history, "follow_up"),
            ("Your latest output is too long; shorten it.", completed_history, "follow_up"),
            ("What about the second one?", completed_history, "follow_up"),
            ("How did Granite perform?", completed_history, "follow_up"),
            ("Yes. Also update README.md.", completed_history, "mixed"),
            ("This project should use Python 3.12.", completed_history, "new_request"),
            ("Use this project to generate a report.", completed_history, "new_request"),
            ("Separate question: what is CUDA?", completed_history, "new_request"),
            ("Explain Python decorators.", completed_history, "new_request"),
        )
        for prompt, history, expected_kind in cases:
            relation = module._classify_turn_relation(prompt, history)
            if relation.kind != expected_kind:
                raise RuntimeError(
                    f"Turn relation mismatch for {prompt!r}: expected={expected_kind}, actual={relation.kind}"
                )

        artifact_history = [
            {
                "role": "user",
                "content": "Create an interactive HTML model of the solar system.",
            },
            {
                "role": "assistant",
                "content": "Completed the requested task. [Completion status: verified]",
            },
        ]
        artifact_feedback = module._classify_turn_relation(
            "The solar system is not visible.",
            artifact_history,
        )
        if artifact_feedback.kind != "follow_up":
            raise RuntimeError(
                "Distinctive outcome feedback was not bound to the preceding artifact task."
            )
        if module._conversation_only_follow_up(
            "The solar system is not visible.", artifact_feedback
        ):
            raise RuntimeError(
                "Artifact outcome feedback was incorrectly classified as conversation-only."
            )

        diagnostic_prompt = (
            'Traceback (most recent call last):\nFile "solar-system.html", line 12\n'
            "RuntimeError: render failed"
        )
        diagnostic_feedback = module._classify_turn_relation(
            diagnostic_prompt,
            artifact_history,
        )
        if diagnostic_feedback.kind != "follow_up" or not module._diagnostic_failure_feedback(
            diagnostic_prompt,
            "Create an interactive HTML model of the solar system in solar-system.html.",
        ):
            raise RuntimeError("Concrete diagnostics were not bound to the preceding artifact task.")

        selection_feedback = module._classify_turn_relation("Option 2.", completed_history)
        if selection_feedback.kind != "follow_up":
            raise RuntimeError("An explicit ordinal selection was not classified as a follow-up.")

        success_feedback = module._classify_turn_relation("That worked.", artifact_history)
        if not module._conversation_only_follow_up("That worked.", success_feedback):
            raise RuntimeError("Brief positive outcome feedback was not handled as conversation-only.")

        conversation_only_cases = (
            ("why?", True),
            ("Regarding your last reply, are you sure?", True),
            ("Your latest output is too long; shorten it.", True),
            ("What about the second one?", True),
            ("How did Granite perform?", True),
            ("Fix it.", False),
            ("Can you update it?", False),
            ("Did it finish now?", False),
            ("What about that file?", False),
        )
        for prompt, expected in conversation_only_cases:
            relation = module._classify_turn_relation(prompt, completed_history)
            actual = module._conversation_only_follow_up(prompt, relation)
            if actual is not expected:
                raise RuntimeError(
                    f"Conversation-only follow-up mismatch for {prompt!r}: expected={expected}, actual={actual}"
                )

        unfinished_history = [
            {"role": "user", "content": "Update app.py and run its tests."},
            {
                "role": "assistant",
                "content": "[Completion status: partial] Unverified postconditions: passing tests.",
            },
        ]
        continuation = module._classify_turn_relation("continue", unfinished_history)
        if continuation.kind != "follow_up" or not continuation.prior_turn_unfinished:
            raise RuntimeError(
                "Continue did not bind to the unfinished prior turn with its unfinished-state marker."
            )
        if module._conversation_only_follow_up("continue", continuation):
            raise RuntimeError("An unfinished continue request was incorrectly classified as conversation-only.")

        mixed_intent = module._analyze_semantic_intent("Yes. Also update README.md.")
        if not mixed_intent.requests("edit") or "changed_files" not in mixed_intent.required_postconditions:
            raise RuntimeError("A mixed confirmation plus edit request lost its edit intent or postcondition.")
        prohibited_intent = module._analyze_semantic_intent("Also do not update README.md.")
        if prohibited_intent.requests("edit") or ("edit", "README.md") not in prohibited_intent.scoped_prohibitions:
            raise RuntimeError("A negated mixed-turn edit was incorrectly authorized.")
        scoped_intent = module._analyze_semantic_intent("Update allowed.txt. Also do not update README.md.")
        if not scoped_intent.requests("edit") or ("edit", "README.md") not in scoped_intent.scoped_prohibitions:
            raise RuntimeError("A target-specific edit prohibition erased a separately authorized edit.")
        for question_prompt in (
            "What is the loaded context",
            "Who owns this file",
            "Is the server running",
            "Should this use CUDA",
            "Can't the wrapper recover",
        ):
            if module._analyze_semantic_intent(question_prompt).speech_act != "question":
                raise RuntimeError(
                    f"Sentence-initial question was not recognized: {question_prompt!r}."
                )
        directed_question = module._analyze_semantic_intent("Can you update README.md")
        if directed_question.speech_act != "request" or not directed_question.requests("edit"):
            raise RuntimeError("A directed question-shaped edit request lost task intent.")
        capability_question = module._analyze_semantic_intent("Can you edit files?")
        if capability_question.speech_act != "question" or capability_question.requests("edit"):
            raise RuntimeError("A generic capability inquiry was incorrectly treated as edit authorization.")
        analysis_request = module._analyze_semantic_intent("Analyze this code")
        if analysis_request.speech_act != "request" or not analysis_request.requests("read"):
            raise RuntimeError("An imperative analysis task was not recognized as a read-oriented task.")
        scoped_analysis = module._classify_query_scope(
            "Analyze this code",
            analysis_request,
            simple_question_candidate=False,
            file_token_count=0,
        )
        if scoped_analysis.query_type != "task":
            raise RuntimeError("A non-mutating imperative analysis request was classified as a question.")

        base = module._APP.base
        runner = base.AgentRunner(
            base.AgentConfig(workspace=ROOT, runtime_workspace=ROOT)
        )
        runner.history = list(completed_history)
        route_cases = (
            ("why?", "simple_answer"),
            ("Regarding your last reply, are you sure?", "simple_answer"),
            ("How did Granite perform?", "simple_answer"),
            ("Fix it.", "retrieval_plus_model"),
            ("Did it finish now?", "retrieval_plus_model"),
            ("Yes. Also update README.md.", "retrieval_plus_model"),
            ("Separate question: what is CUDA?", "simple_answer"),
        )
        for prompt, expected_route in route_cases:
            runner._turn_relation = module._classify_turn_relation(prompt, runner.history)
            decision = runner._decide_route(prompt)
            if decision.selected_route != expected_route:
                raise RuntimeError(
                    f"Turn-aware route mismatch for {prompt!r}: "
                    f"expected={expected_route}, actual={decision.selected_route}"
                )
        runner._turn_relation = module._classify_turn_relation(
            "Regarding your last answer, shorten it.",
            runner.history,
        )
        contract = runner._runtime_tool_contract_for_harness(
            "simple_answer",
            "simple_question",
            "Regarding your last answer, shorten it.",
        )
        if "most recent completed assistant output" not in contract:
            raise RuntimeError("The runtime contract did not bind last-answer references explicitly.")
    finally:
        _unload_variant(module)
        del module
        gc.collect()


def check_sampling_profiles(paths: Sequence[Path]) -> None:
    expected_names = {path.name for path in paths}
    if set(VARIANT_SAMPLING_FAMILIES) != expected_names:
        missing = sorted(expected_names - set(VARIANT_SAMPLING_FAMILIES))
        stale = sorted(set(VARIANT_SAMPLING_FAMILIES) - expected_names)
        raise RuntimeError(f"Sampling family map mismatch: missing={missing}, stale={stale}")
    for path in paths:
        module = _import_variant(path, "sampling_profile")
        try:
            family = VARIANT_SAMPLING_FAMILIES[path.name]
            model_alias = str(module._APP.base.DEFAULT_MODEL)
            expected_profiles = _sampling_profiles_for_family(family)
            for route_name, prompt, selected_route in SAMPLING_ROUTE_CASES:
                actual = module._sampling_profile_for_route(prompt, selected_route, model_alias)
                expected = (f"{family}_{route_name}", *expected_profiles[route_name])
                if actual != expected:
                    raise RuntimeError(
                        f"{path.name}: {route_name} sampling mismatch: expected={expected}, actual={actual}"
                    )
        finally:
            _unload_variant(module)
            del module
            gc.collect()


def check_sampling_runtime_behavior(path: Path) -> None:
    module = _import_variant(path, "sampling_runtime")
    try:
        base = module._APP.base
        model_alias = str(base.DEFAULT_MODEL)
        default_profile = module._sampling_profile_for_route(
            "What is two plus two?",
            "simple_answer",
            model_alias,
        )

        config = SimpleNamespace(
            temperature=0.41,
            top_p=0.82,
            min_p=0.03,
            repeat_penalty=1.04,
            _qubitz_manual_temperature_override=0.73,
        )
        before = vars(config).copy()
        state = module._sampling_state_snapshot(config)
        applied = module._apply_sampling_profile(config, default_profile)
        if applied[1] != 0.73 or not hasattr(config, "top_k") or not hasattr(config, "presence_penalty"):
            raise RuntimeError("Sampling application did not preserve the manual temperature override.")
        module._restore_sampling_state(config, state)
        if vars(config) != before:
            raise RuntimeError(f"Sampling state restoration mismatch: expected={before}, actual={vars(config)}")

        temperature_gui = next(
            (candidate for candidate in base.QubitzGUI.__mro__ if candidate.__name__ == "TemperatureStatusGUI"),
            None,
        )
        if temperature_gui is None:
            raise RuntimeError("Active GUI MRO has no TemperatureStatusGUI.")
        init_names = set(temperature_gui.__dict__["__init__"].__code__.co_names)
        if not {"_sampling_profile_for_route", "_temperature_profile_default"}.issubset(init_names):
            raise RuntimeError("GUI initialization is not bound to the model-specific sampling default.")

        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        gui = object.__new__(base.QubitzGUI)
        gui.num_ctx_var = Value("4096")
        gui.num_predict_var = Value("256")
        gui.thinking_effort_var = Value("xhigh")
        gui.temperature_var = Value(f"{default_profile[1]:g}")
        gui._temperature_profile_default = float(default_profile[1])
        gui.config = SimpleNamespace()
        gui.agent = SimpleNamespace(config=SimpleNamespace())
        sync_settings = temperature_gui.__dict__["_sync_runtime_settings"]
        sync_settings(gui)
        if hasattr(gui.config, "_qubitz_manual_temperature_override"):
            raise RuntimeError("The displayed model default was incorrectly marked as a manual override.")
        gui.temperature_var.value = f"{default_profile[1] + 0.13:g}"
        sync_settings(gui)
        for target in (gui.config, gui.agent.config):
            if getattr(target, "_qubitz_manual_temperature_override", None) != default_profile[1] + 0.13:
                raise RuntimeError("GUI manual temperature override was not propagated to both configs.")
        gui.temperature_var.value = f"{default_profile[1]:g}"
        sync_settings(gui)
        if any(
            hasattr(target, "_qubitz_manual_temperature_override")
            for target in (gui.config, gui.agent.config)
        ):
            raise RuntimeError("Returning to the displayed model default did not clear the GUI override.")

        transport = SimpleNamespace(label="direct", base_url="http://127.0.0.1:8001")
        client = object.__new__(base.LlamaCppClient)
        client.config = SimpleNamespace(top_k=20, presence_penalty=1.5)
        client.transport = transport
        client.transports = [transport]
        client.server_process = None
        client._qubitz_server_context_window = 65536
        non_streaming_payloads: list[dict[str, Any]] = []

        def fake_call(_method: str, _path: str, payload: dict[str, Any]) -> dict[str, Any]:
            non_streaming_payloads.append(dict(payload))
            return {
                "choices": [
                    {
                        "message": {
                            "content": "ok",
                            "tool_calls": [
                                {
                                    "id": "call_read",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"a.py"}',
                                    },
                                },
                                {
                                    "id": "call_list",
                                    "type": "function",
                                    "function": {
                                        "name": "list_files",
                                        "arguments": '{"path":"."}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {},
            }

        client._call_with_fallback = fake_call
        call_arguments = {
            "model_name": model_alias,
            "messages": [{"role": "user", "content": "test"}],
            "tools": [],
            "num_predict": 32,
            "temperature": 0.7,
            "top_p": 0.9,
            "min_p": 0.02,
            "repeat_penalty": 1.03,
        }
        base.set_qubitz_stream_callback(None)
        non_streaming_response = client.chat(**call_arguments)
        if len(non_streaming_payloads) != 1:
            raise RuntimeError("Non-streaming sampling payload was not captured exactly once.")
        if len(non_streaming_response.get("message", {}).get("tool_calls", [])) != 2:
            raise RuntimeError("Non-streaming chat did not preserve every model-emitted tool call.")

        streaming_payloads: list[dict[str, Any]] = []
        streamed_deltas: list[str] = []

        class FakeStreamResponse:
            status_code = 200

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def raise_for_status(self) -> None:
                return None

            def iter_lines(self) -> Sequence[str]:
                event = {
                    "choices": [
                        {
                            "delta": {
                                "content": "ok",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_read",
                                        "type": "function",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"path":"a.py"}',
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "call_list",
                                        "type": "function",
                                        "function": {
                                            "name": "list_files",
                                            "arguments": '{"path":"."}',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {},
                }
                return [f"data: {json.dumps(event)}", "data: [DONE]"]

        class FakeHTTPClient:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                return None

            def __enter__(self) -> Self:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def stream(
                self,
                _method: str,
                _url: str,
                *,
                json: dict[str, Any],
                headers: dict[str, str],
            ) -> FakeStreamResponse:
                if not re.fullmatch(r"[0-9a-f]{32}", headers.get("X-Qubitz-Request-Id", "")):
                    raise RuntimeError("Streaming request has no exact request-local identity header.")
                streaming_payloads.append(dict(json))
                return FakeStreamResponse()

        import httpx

        original_http_client = httpx.Client
        try:
            httpx.Client = FakeHTTPClient
            base.set_qubitz_stream_callback(streamed_deltas.append)
            streaming_response = client.chat(**call_arguments)
        finally:
            base.set_qubitz_stream_callback(None)
            httpx.Client = original_http_client
        if len(streaming_payloads) != 1 or streamed_deltas != ["ok"]:
            raise RuntimeError("Streaming sampling payload or callback output was not captured exactly once.")
        if len(streaming_response.get("message", {}).get("tool_calls", [])) != 2:
            raise RuntimeError("Streaming chat did not preserve every model-emitted tool call.")
        if any(
            payload.get("parallel_tool_calls") is not True
            for payload in (non_streaming_payloads[0], streaming_payloads[0])
        ):
            raise RuntimeError("Streaming or non-streaming chat did not enable parallel tool calls.")
        sampling_keys = (
            "temperature",
            "top_p",
            "top_k",
            "min_p",
            "repeat_penalty",
            "presence_penalty",
        )
        non_streaming = {key: non_streaming_payloads[0].get(key) for key in sampling_keys}
        streaming = {key: streaming_payloads[0].get(key) for key in sampling_keys}
        if streaming != non_streaming:
            raise RuntimeError(
                f"Streaming/non-streaming sampling payload mismatch: {streaming} != {non_streaming}"
            )
    finally:
        _unload_variant(module)
        del module
        gc.collect()


def check_gui_output_contract(path: Path) -> None:
    module = _import_variant(path, "gui_output_contract")
    try:
        base = module._APP.base
        tree = ast.parse(read_variant_source(path), filename=str(path))
        controls = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "RunControlGUI"]
        if len(controls) != 1:
            raise RuntimeError("Expected one run-control GUI implementation.")
        callback_helpers = {
            call.func.attr
            for method in controls[0].body
            if isinstance(method, ast.FunctionDef) and method.name in {"send_prompt", "_change_workspace"}
            for call in ast.walk(method)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "self"
        }
        missing_helpers = sorted(name for name in callback_helpers if not callable(getattr(base.QubitzGUI, name, None)))
        if missing_helpers:
            raise RuntimeError(f"Workspace callbacks call missing GUI methods: {missing_helpers}.")
        runner = object.__new__(base.AgentRunner)
        runner._last_generation_metrics = {}
        if runner._format_completion_telemetry(5.4) != "(Elapsed time: 00:05)":
            raise RuntimeError("Wrapper-only completion telemetry still exposes non-applicable token speed.")
        runner._last_generation_metrics = {"model_calls": 1, "tokens_per_second": 17.125}
        if runner._format_completion_telemetry(65.6) != (
            "(Elapsed time: 01:06 | Token speed: 17.12 tokens/s)"
        ):
            raise RuntimeError("Generated-answer completion telemetry lost token-speed reporting.")
        runner._last_generation_metrics = {"model_calls": 1}
        if runner._format_completion_telemetry(1.0) != (
            "(Elapsed time: 00:01 | Token speed: unavailable)"
        ):
            raise RuntimeError("Missing generation metrics are no longer distinguished from a fast path.")

        clipboard_gui = next(
            (
                candidate
                for candidate in base.QubitzGUI.__mro__
                if "_copy_selected_text" in candidate.__dict__
            ),
            None,
        )
        if clipboard_gui is None:
            raise RuntimeError("Active GUI MRO has no selection-copy implementation.")
        required_methods = {
            "_selected_text_for_copy",
            "_copy_selected_text",
            "_handle_copy_shortcut",
            "_copy_context_selection",
            "_show_copy_context_menu",
        }
        if not required_methods.issubset(clipboard_gui.__dict__):
            raise RuntimeError("GUI selection-copy and context-menu methods are incomplete.")
        tag_method = clipboard_gui.__dict__.get("_ensure_transcript_tags")
        if tag_method is None:
            raise RuntimeError("Active GUI MRO has no transcript-tag setup method.")
        tag_constants = tag_method.__code__.co_consts
        required_bindings = ("<Control-c>", "<Control-C>", "<Button-3>", "Copy")
        if not all(binding in tag_constants for binding in required_bindings):
            raise RuntimeError("GUI copy shortcuts or right-click Copy menu are not bound.")
        if "#ff8300" not in tag_constants:
            raise RuntimeError("User-authored transcript text is not configured with the orange role color.")

        class FakeRoot:
            def __init__(self) -> None:
                self.clipboard = ""
                self.copy_count = 0

            def clipboard_clear(self) -> None:
                self.clipboard = ""

            def clipboard_append(self, text: str) -> None:
                self.clipboard = text
                self.copy_count += 1

            def update_idletasks(self) -> None:
                pass

        class FakeWidget:
            def tag_ranges(self, _tag: str) -> tuple[str, str]:
                return ("1.0", "1.8")

            def get(self, _start: str, _end: str) -> str:
                return "selected"

        gui = object.__new__(base.QubitzGUI)
        gui.root = FakeRoot()
        copy_selected = clipboard_gui.__dict__["_copy_selected_text"]
        widget = FakeWidget()
        if not copy_selected(gui, widget) or not copy_selected(gui, widget):
            raise RuntimeError("Repeated selection copying unexpectedly failed.")
        if gui.root.clipboard != "selected" or gui.root.copy_count != 2:
            raise RuntimeError("Repeated selection copying did not refresh the clipboard each time.")
    finally:
        _unload_variant(module)
        del module
        gc.collect()


def check_syntax(paths: Sequence[Path]) -> None:
    for path in paths:
        ast.parse(read_variant_source(path), filename=str(path))


def check_effective_source_lint(paths: Sequence[Path]) -> None:
    """Lint executed source as well as the physical launcher and runtime helpers."""
    for path in paths:
        completed = subprocess.run(
            (sys.executable, "-m", "ruff", "check", "--select", "E9,F63,F7,F82",
             "--stdin-filename", path.name, "-"),
            input=read_variant_source(path), text=True, capture_output=True, cwd=ROOT, check=False,
        )
        if completed.returncode:
            raise RuntimeError(f"Effective-source Ruff failed for {path.name}:\n{completed.stdout}{completed.stderr}")


def check_variant_runtime_contracts(paths: Sequence[Path]) -> None:
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    profiles = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    if template.get("schema_version") != EXPECTED_TEMPLATE_SCHEMA_VERSION:
        raise RuntimeError("Common wrapper template schema is stale or unsupported.")
    if profiles.get("schema_version") != EXPECTED_PROFILE_SCHEMA_VERSION:
        raise RuntimeError("Variant profile schema is stale or unsupported.")
    by_name = {str(item.get("filename")): item for item in profiles.get("variants", [])
               if user_variant_allowed(str(item.get("filename")))}
    if set(by_name) != {path.name for path in paths}:
        raise RuntimeError("Runtime-contract profiles do not match the active variant set.")
    contract_flags = {
        "background_prewarm",
        "dual_gpu",
        "isolated_runtime",
        "mtp_draft",
        "sharded_model",
    }
    for path in paths:
        profile = by_name[path.name]
        contract = profile.get("runtime_contract")
        if not isinstance(contract, dict) or not isinstance(contract.get("constants"), dict):
            raise RuntimeError(f"{path.name}: missing typed runtime contract.")  # noqa: TRY004 - Gate failure contract.
        if not contract_flags.issubset(contract):
            missing = sorted(contract_flags - set(contract))
            raise RuntimeError(f"{path.name}: runtime contract is missing {missing}.")
        if any(not isinstance(contract[name], bool) for name in contract_flags):
            raise RuntimeError(f"{path.name}: runtime contract flags must be booleans.")
        if contract["background_prewarm"] is not bool(profile.get("background_prewarm")):
            raise RuntimeError(f"{path.name}: prewarm profile and runtime contract disagree.")
        residual_hash = str(profile.get("intentional_residual_sha256") or "")
        try:
            valid_hash = len(residual_hash) == 64 and int(residual_hash, 16) >= 0
        except ValueError:
            valid_hash = False
        if not valid_hash:
            raise RuntimeError(f"{path.name}: intentional variant residual hash is invalid.")
        constants = contract["constants"]
        if contract["dual_gpu"]:
            gpu_indices = [value for key, value in constants.items() if key.endswith("_GPU_INDICES")]
            if gpu_indices != [[0, 1]]:
                raise RuntimeError(f"{path.name}: dual-GPU contract does not preserve GPU indices [0, 1].")
        if contract["isolated_runtime"] and not any("RUNTIME_DIR" in key for key in constants):
            raise RuntimeError(f"{path.name}: isolated runtime contract lost its runtime directory.")
        if contract["sharded_model"] and not any(
            key.endswith(("_GGUF_SHARDS", "_GGUF_PARTS"))
            and isinstance(value, list)
            and len(value) > 1
            for key, value in constants.items()
        ):
            raise RuntimeError(f"{path.name}: sharded-model contract lost its complete shard list.")

    required_special_cases = {
        "AI_Agent_Qubitz_Gemma-4-31B-IT_QAT-Q4.py": ("background_prewarm", True),
        "AI_Agent_Qubitz_North-Mini-Code-1.0-30B-A3B-Q4.py": ("isolated_runtime", True),
        "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py": ("dual_gpu", True),
        "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py": ("dual_gpu", True),
        "AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py": ("dual_gpu", True),
        "AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py": ("mtp_draft", True),
    }
    for filename, (flag, expected) in required_special_cases.items():
        if filename not in by_name or by_name[filename]["runtime_contract"].get(flag) is not expected:
            raise RuntimeError(f"{filename}: required model-specific {flag} contract was not preserved.")


def check_local_tool_contracts(paths: Sequence[Path]) -> None:
    for path in paths:
        tree = ast.parse(read_variant_source(path), filename=str(path))
        brokers = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "_ToolPermissionBroker"
        ]
        if len(brokers) != 1:
            raise RuntimeError(f"{path.name}: expected one local tool permission broker.")
        broker = brokers[0]
        class_sets = {
            node.targets[0].id: frozenset(ast.literal_eval(node.value))
            for node in broker.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {
                "_READ_TOOLS", "_MUTATION_TOOLS", "_EXECUTION_TOOLS", "_ALWAYS_APPROVAL"
            }
        }
        required_sets = {"_READ_TOOLS", "_MUTATION_TOOLS", "_EXECUTION_TOOLS", "_ALWAYS_APPROVAL"}
        if set(class_sets) != required_sets:
            raise RuntimeError(f"{path.name}: local tool permission sets are incomplete.")
        read_tools = class_sets["_READ_TOOLS"]
        mutation_tools = class_sets["_MUTATION_TOOLS"]
        execution_tools = class_sets["_EXECUTION_TOOLS"]
        if (read_tools & mutation_tools) or (read_tools & execution_tools) or (mutation_tools & execution_tools):
            raise RuntimeError(f"{path.name}: local tool permission categories overlap.")

        registered = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                for decorator in node.decorator_list
            )
        }
        missing_mutations = DIRECT_WORKSPACE_MUTATION_TOOLS - mutation_tools
        unclassified = registered - (
            read_tools | mutation_tools | execution_tools
            | class_sets["_ALWAYS_APPROVAL"] | SPECIAL_LOCAL_TOOLS
        )
        if missing_mutations or unclassified or not SPECIAL_LOCAL_TOOLS <= registered:
            raise RuntimeError(
                f"{path.name}: local tool contract drift: "
                f"missing_mutations={sorted(missing_mutations)}, "
                f"unclassified={sorted(unclassified)}, "
                f"missing_special={sorted(SPECIAL_LOCAL_TOOLS - registered)}."
            )

        methods = {
            node.name: node for node in broker.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for method_name in ("authorize", "record_result"):
            method = methods.get(method_name)
            if method is None:
                raise RuntimeError(f"{path.name}: missing {method_name} tool policy method.")
            extensions = [
                frozenset(ast.literal_eval(node.value.right))
                for node in ast.walk(method)
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "mutation_tools" for target in node.targets)
                and isinstance(node.value, ast.BinOp)
                and isinstance(node.value.op, ast.BitOr)
                and isinstance(node.value.left, ast.Attribute)
                and node.value.left.attr == "_MUTATION_TOOLS"
                and isinstance(node.value.right, ast.Set)
            ]
            if extensions != [PATCH_MUTATION_TOOLS]:
                raise RuntimeError(f"{path.name}: {method_name} lost the structured-patch mutation policy.")
        recorded_categories = {
            call.args[0].value
            for call in ast.walk(methods["record_result"])
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "categories"
            and call.func.attr == "add"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        }
        if "changed_files" not in recorded_categories:
            raise RuntimeError(f"{path.name}: mutation results no longer record changed-file evidence.")


def check_hardened_runner_contract(paths: Sequence[Path]) -> None:
    for path in paths:
        tree = ast.parse(read_variant_source(path), filename=str(path))
        runners = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "HardenedAgentRunner"
        ]
        if len(runners) != 1:
            raise RuntimeError(
                f"{path.name}: expected one active HardenedAgentRunner, found {len(runners)}."
            )
        runner = runners[0]
        methods = {
            node.name
            for node in runner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        run_async = next(
            (
                node
                for node in runner.body
                if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_async"
            ),
            None,
        )
        if run_async is None:
            raise RuntimeError(f"{path.name}: HardenedAgentRunner._run_async is missing.")
        called_methods = {
            node.func.attr
            for node in ast.walk(run_async)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        }
        missing = sorted(
            method
            for method in REQUIRED_HARDENED_RUNNER_METHODS
            if method in called_methods and method not in methods
        )
        if missing:
            raise RuntimeError(
                f"{path.name}: HardenedAgentRunner calls missing method(s): {', '.join(missing)}."
            )
        hosts = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "HardenedMCPHost"
        ]
        if len(hosts) != 1:
            raise RuntimeError(
                f"{path.name}: expected one active HardenedMCPHost, found {len(hosts)}."
            )
        host_methods = {
            node.name
            for node in hosts[0].body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        missing_host_methods = sorted(set(REQUIRED_HARDENED_HOST_METHODS) - host_methods)
        if missing_host_methods:
            raise RuntimeError(
                f"{path.name}: HardenedMCPHost is missing {', '.join(missing_host_methods)}."
            )


def check_orchestration_runtime_behavior(path: Path) -> None:
    module = _import_variant(path, "orchestration_runtime")
    runner = None
    try:
        base = module._APP.base
        host_type = next(
            (
                candidate
                for candidate in base.MCPHost.__mro__
                if candidate.__name__ == "HardenedMCPHost"
            ),
            None,
        )
        if host_type is None:
            raise RuntimeError(f"{path.name}: active MCP host MRO has no HardenedMCPHost.")

        async def inspect_live_catalog() -> tuple[int, int]:
            with tempfile.TemporaryDirectory(prefix="qubitz_catalog_gate_") as workspace_text:
                async with base.MCPHost(Path(workspace_text)) as live_host:
                    tools = await live_host.list_tools()
                    workflow_tools = [tool for tool in tools if tool.name == "execute_workflow"]
                    if len(workflow_tools) != 1:
                        return len(tools), len(workflow_tools)
                    schema = workflow_tools[0].inputSchema
                    if schema["properties"]["steps"].get("maxItems") != 16:
                        raise RuntimeError("Live workflow tool schema lost its 16-step bound.")
                    return len(tools), len(workflow_tools)

        tool_count, workflow_tool_count = asyncio.run(inspect_live_catalog())
        if tool_count < 2 or workflow_tool_count != 1:
            raise RuntimeError(
                "Local MCP startup did not expose exactly one workflow tool alongside base tools."
            )

        def contract(name: str, *, read_only: bool) -> SimpleNamespace:
            return SimpleNamespace(
                name=name,
                annotations=SimpleNamespace(
                    readOnlyHint=read_only,
                    idempotentHint=read_only,
                ),
            )

        class ProbeHost(host_type):
            def __init__(self) -> None:
                self._qubitz_tool_contracts = {
                    "read_file": contract("read_file", read_only=True),
                    "list_files": contract("list_files", read_only=True),
                    "write_file": contract("write_file", read_only=False),
                    "execute_workflow": contract("execute_workflow", read_only=False),
                }
                self._qubitz_parallel_tool_results: dict[str, list[Any]] = {}
                self._qubitz_parallel_dispatching = False
                self.active_calls = 0
                self.max_active_calls = 0
                self.events: list[tuple[str, str, dict[str, Any]]] = []

            async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
                self.active_calls += 1
                self.max_active_calls = max(self.max_active_calls, self.active_calls)
                self.events.append(("start", name, dict(arguments)))
                try:
                    if self._parallel_read_only_tool(name):
                        await asyncio.sleep(0.01)
                    payload = {
                        "name": name,
                        "arguments": dict(arguments),
                        "value": f"{name}:{arguments.get('path', '')}",
                    }
                    return base.mcp_types.CallToolResult(
                        content=[
                            base.mcp_types.TextContent(
                                type="text",
                                text=json.dumps(payload, sort_keys=True),
                            )
                        ],
                        structuredContent=payload,
                        isError=bool(arguments.get("fail")),
                    )
                finally:
                    self.events.append(("end", name, dict(arguments)))
                    self.active_calls -= 1

        host = ProbeHost()

        class BatchOwner:
            def __init__(self) -> None:
                self._qubitz_pending_tool_batch = [
                    {
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"one.py"}',
                        }
                    },
                    {
                        "function": {
                            "name": "list_files",
                            "arguments": '{"path":"src"}',
                        }
                    },
                ]

            def stop(self) -> None:
                return None

        notices: list[str] = []
        batch_owner = BatchOwner()
        broker = SimpleNamespace(stop_callback=batch_owner.stop, _emit=notices.append)
        handled_first, first_result = asyncio.run(
            host._consume_parallel_read_only_batch(
                "read_file",
                {"path": "one.py"},
                broker,
            )
        )
        handled_second, second_result = asyncio.run(
            host._consume_parallel_read_only_batch(
                "list_files",
                {"path": "src"},
                broker,
            )
        )
        if not handled_first or not handled_second or host.max_active_calls != 2:
            raise RuntimeError(
                "Independent read-only tool calls were not executed as one bounded parallel batch."
            )
        if not notices or "results remain ordered" not in notices[0]:
            raise RuntimeError("Parallel batch execution did not report its ordering contract.")
        if (
            first_result.structuredContent["name"] != "read_file"
            or second_result.structuredContent["name"] != "list_files"
        ):
            raise RuntimeError("Parallel tool results were not replayed in model-call order.")

        host.active_calls = 0
        host.max_active_calls = 0
        host.events.clear()
        workflow = {
            "steps": [
                {"id": "read_a", "name": "read_file", "arguments": {"path": "a.py"}},
                {"id": "read_b", "name": "list_files", "arguments": {"path": "src"}},
                {
                    "id": "write",
                    "name": "write_file",
                    "arguments": {
                        "path": "out.txt",
                        "content": "${steps.read_a.structured_content.value}",
                    },
                    "depends_on": ["read_a", "read_b"],
                },
            ]
        }
        workflow_result = asyncio.run(host._execute_workflow(workflow, None))
        workflow_payload = workflow_result.structuredContent
        if workflow_result.isError or workflow_payload.get("status") != "completed":
            raise RuntimeError("A valid dependency-ordered workflow did not complete successfully.")
        if [item["id"] for item in workflow_payload["results"]] != ["read_a", "read_b", "write"]:
            raise RuntimeError("Workflow results did not preserve declared step order.")
        if host.max_active_calls != 2:
            raise RuntimeError("Workflow did not parallelize its two independent read-only steps.")
        write_start = next(
            index
            for index, item in enumerate(host.events)
            if item[:2] == ("start", "write_file")
        )
        read_ends = [
            index
            for index, item in enumerate(host.events)
            if item[0] == "end" and item[1] in {"read_file", "list_files"}
        ]
        if not read_ends or write_start <= max(read_ends):
            raise RuntimeError(
                "Workflow mutation started before all declared read dependencies completed."
            )
        write_result = workflow_payload["results"][2]
        if write_result["arguments"]["content"] != "read_file:a.py":
            raise RuntimeError(
                "Workflow dependency reference did not resolve to the exact prior result value."
            )

        failed_workflow = {
            "steps": [
                {
                    "id": "failed_read",
                    "name": "read_file",
                    "arguments": {"path": "missing.py", "fail": True},
                },
                {
                    "id": "blocked_write",
                    "name": "write_file",
                    "arguments": {"path": "never.txt", "content": "no"},
                    "depends_on": ["failed_read"],
                },
            ]
        }
        failed_result = asyncio.run(host._execute_workflow(failed_workflow, None))
        failed_payload = failed_result.structuredContent
        if not failed_result.isError or failed_payload.get("failed_steps") != [
            "failed_read",
            "blocked_write",
        ]:
            raise RuntimeError("Workflow failure did not block and report its dependent mutation.")

        with tempfile.TemporaryDirectory(prefix="qubitz_orchestration_gate_") as workspace_text:
            workspace = Path(workspace_text)
            runner = base.AgentRunner(
                base.AgentConfig(workspace=workspace, runtime_workspace=ROOT)
            )
            runner.set_access_mode(module.ACCESS_MODE_FULL)
            prompt = "Use find_symbol_callees to inspect the requested symbol."
            runner._tool_permission_broker.begin_turn(prompt, None)
            tools = [
                SimpleNamespace(name=name, description=name)
                for name in (
                    "read_file",
                    "list_files",
                    "search_text",
                    "workspace_symbols",
                    "find_symbol_definitions",
                    "find_symbol_references",
                    "find_symbol_callers",
                    "find_symbol_callees",
                    "find_symbol_type_info",
                    "document_symbols",
                    "codeintel_diagnostics",
                    "search_tools",
                    "write_file",
                )
            ]
            selected = runner._filter_tools_by_intent(prompt, tools)
            selected_names = {runner._tool_name(tool) for tool in selected}
            if "find_symbol_callees" not in selected_names:
                raise RuntimeError("An explicitly requested available tool was dropped by intent filtering.")
            runner._tool_permission_broker.end_turn()

            runner.set_access_mode(module.ACCESS_MODE_READ_ONLY)
            read_only_prompt = "Use write_file to create output.txt."
            runner._tool_permission_broker.begin_turn(read_only_prompt, None)
            selected = runner._filter_tools_by_intent(read_only_prompt, tools)
            if "write_file" in {runner._tool_name(tool) for tool in selected}:
                raise RuntimeError("Explicit tool preservation bypassed read-only access policy.")
            runner._tool_permission_broker.end_turn()

            runner._cancel_event.clear()
            runner._wrapper_interrupt_event.clear()
            runner._request_transaction_recovery()
            if not runner._wrapper_interrupt_event.is_set() or runner._cancel_event.is_set():
                raise RuntimeError("Wrapper recovery was incorrectly recorded as user cancellation.")
            try:
                runner._raise_if_cancelled()
            except asyncio.CancelledError:
                pass
            else:
                raise RuntimeError("Wrapper recovery interrupt did not stop the active model step.")
            runner._wrapper_interrupt_event.clear()
            runner._cancel_event.clear()
            runner.request_cancel()
            if not runner._cancel_event.is_set() or runner._wrapper_interrupt_event.is_set():
                raise RuntimeError("User cancellation and wrapper recovery state were not kept distinct.")

        source_tree = ast.parse(read_variant_source(path), filename=str(path))
        hardened_runner = next(
            node
            for node in ast.walk(source_tree)
            if isinstance(node, ast.ClassDef) and node.name == "HardenedAgentRunner"
        )
        run_async = next(
            node
            for node in hardened_runner.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_async"
        )
        retrieval_assignment = next(
            (
                node
                for node in ast.walk(run_async)
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "retrieval_skipped_routes"
                    for target in node.targets
                )
            ),
            None,
        )
        if retrieval_assignment is None or not isinstance(retrieval_assignment.value, ast.Set):
            raise RuntimeError("Demand-driven retrieval route policy is missing from the active runner.")
        skipped_routes = {
            item.value
            for item in retrieval_assignment.value.elts
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }
        if skipped_routes != {
            "dependency_install",
            "named_file_operation",
            "project_independent",
            "scoped_project_action",
            "simple_answer",
        }:
            raise RuntimeError(
                f"Demand-driven retrieval routes are inconsistent: {sorted(skipped_routes)}"
            )
    finally:
        if runner is not None and callable(getattr(runner, "reset_runtime", None)):
            runner.reset_runtime()
        _unload_variant(module)
        del module
        gc.collect()


def check_route_interface_compatibility(paths: Sequence[Path]) -> None:
    valid_routes = {
        "ask_user_missing_info",
        "dependency_install",
        "direct_existing_entrypoint",
        "named_file_operation",
        "project_independent",
        "read_only_workspace",
        "retrieval_plus_model",
        "scoped_project_action",
        "simple_answer",
        "tool_loop",
    }
    for path in paths:
        module = _import_variant(path, "route_interface")
        runner = None
        try:
            with tempfile.TemporaryDirectory(prefix="qubitz_route_gate_") as workspace_text:
                try:
                    base = module._APP.base
                    config = base.AgentConfig(
                        workspace=Path(workspace_text),
                        runtime_workspace=ROOT,
                    )
                    runner = base.AgentRunner(config)
                    if callable(getattr(runner, "set_access_mode", None)):
                        runner.set_access_mode(module.ACCESS_MODE_FULL)
                    decision = runner._decide_route("What is the capital of Romania?")
                    if getattr(decision, "selected_route", None) not in valid_routes:
                        raise RuntimeError(
                            f"{path.name}: route decision returned an invalid route: "
                            f"{getattr(decision, 'selected_route', None)!r}."
                        )
                    if not isinstance(getattr(decision, "reason", None), str):
                        raise RuntimeError(f"{path.name}: route decision has no string reason.")  # noqa: TRY004 - Gate failure contract.
                    if not isinstance(getattr(decision, "features", None), dict):
                        raise RuntimeError(f"{path.name}: route decision has no feature mapping.")  # noqa: TRY004 - Gate failure contract.
                    if not isinstance(getattr(decision, "fallback_routes", None), list):
                        raise RuntimeError(f"{path.name}: route decision has no fallback route list.")  # noqa: TRY004 - Gate failure contract.

                    route_cases = (
                        (
                            "Compare deductive and abductive reasoning in epistemology, including strengths and limitations.",
                            "project_independent",
                            False,
                        ),
                        (
                            "Create an HTML with JavaScript as an interactive complete model of our solar system.",
                            "scoped_project_action",
                            False,
                        ),
                        (
                            "Create a Python script named calculator.py that would be a basic calculator.",
                            "named_file_operation",
                            False,
                        ),
                    )
                    for prompt, expected_route, context_required in route_cases:
                        decision = runner._decide_route(prompt)
                        if decision.selected_route != expected_route:
                            raise RuntimeError(
                                f"{path.name}: {prompt!r} expected route {expected_route!r}, "
                                f"got {decision.selected_route!r}."
                            )
                        classification = decision.features.get("query_classification")
                        if not isinstance(classification, dict):
                            raise RuntimeError(  # noqa: TRY004 - Gate failure contract.
                                f"{path.name}: route decision omitted query classification."
                            )
                        if bool(classification.get("project_context_required")) != context_required:
                            raise RuntimeError(
                                f"{path.name}: incorrect project-context requirement for {prompt!r}."
                            )

                    scoped_request = module._scoped_project_action_request(
                        module.ACCESS_MODE_FULL,
                        "Create an HTML with JavaScript as an interactive complete model of our solar system.",
                    )
                    if not isinstance(scoped_request, dict) or scoped_request.get("suffix") != ".html":
                        raise RuntimeError(
                            f"{path.name}: scoped creation did not bind the expected artifact suffix."
                        )

                    project_decision = runner._decide_route(
                        "Inspect this project and explain its current architecture."
                    )
                    project_classification = project_decision.features.get(
                        "query_classification", {}
                    )
                    if (
                        project_classification.get("project_scope") != "project_related"
                        or not project_classification.get("project_context_required")
                        or project_decision.selected_route
                        in {"project_independent", "scoped_project_action", "simple_answer"}
                    ):
                        raise RuntimeError(
                            f"{path.name}: project-dependent inspection was routed without project context."
                        )

                    runner._route_decision = runner._decide_route(
                        "Create an HTML with JavaScript as an interactive complete model of our solar system."
                    )
                    scoped_prompt = (
                        "Create an HTML with JavaScript as an interactive complete model of our solar system."
                    )
                    prior_locked_route = getattr(runner, "_locked_route_name", "")
                    runner._locked_route_name = runner._route_decision.selected_route
                    runner._tool_permission_broker.begin_turn(scoped_prompt, None)
                    try:
                        tool_stubs = [
                            SimpleNamespace(name=name, description=name)
                            for name in ("list_files", "read_file", "search_text", "write_file")
                        ]
                        selected_tools = runner._filter_tools_by_intent(
                            scoped_prompt,
                            tool_stubs,
                        )
                        selected_names = {
                            runner._tool_name(tool) for tool in selected_tools
                        }
                        if selected_names != {"write_file"}:
                            raise RuntimeError(
                                f"{path.name}: scoped creation exposed tools {sorted(selected_names)}."
                            )
                    finally:
                        runner._tool_permission_broker.end_turn()
                        runner._locked_route_name = prior_locked_route

                    artifact_path = Path(workspace_text) / "solar-system.html"
                    artifact_path.write_text("<html></html>", encoding="utf-8")
                    artifact_prompt = "The solar system is not visible."
                    runner.history = [
                        {
                            "role": "user",
                            "content": "Create an interactive HTML model of the solar system.",
                        },
                        {
                            "role": "assistant",
                            "content": "Completed the requested task. [Completion status: verified]",
                        },
                    ]
                    runner._turn_relation = module._classify_turn_relation(
                        artifact_prompt,
                        runner.history,
                    )
                    runner._active_follow_up_paths = (str(artifact_path.resolve()),)
                    runner._bound_follow_up_repair = module._task_outcome_requires_repair(
                        artifact_prompt,
                        "Create an interactive HTML model of the solar system.",
                    )
                    runner._tool_permission_broker.begin_turn(artifact_prompt, None)
                    runner._tool_permission_broker.bind_follow_up_repair(
                        "Create an interactive HTML model of the solar system.",
                        artifact_prompt,
                        runner._active_follow_up_paths,
                    )
                    follow_up_decision = runner._decide_route(artifact_prompt)
                    follow_up_classification = follow_up_decision.features.get(
                        "query_classification", {}
                    )
                    if (
                        follow_up_decision.selected_route != "retrieval_plus_model"
                        or follow_up_classification.get("query_type") != "task"
                        or follow_up_classification.get("project_scope") != "project_related"
                        or follow_up_classification.get("inspection_level") != "exact_targets"
                        or not runner._tool_permission_broker.task_intent.semantic.requests("edit")
                        or "changed_files" not in runner._tool_permission_broker.required_postconditions
                        or str(artifact_path.resolve())
                        not in runner._tool_permission_broker.transaction_explicit_file_targets
                    ):
                        raise RuntimeError(
                            f"{path.name}: verified-target follow-up did not use exact-target project routing."
                        )
                finally:
                    if runner is not None and callable(getattr(runner, "reset_runtime", None)):
                        runner.reset_runtime()
                    runner = None
        finally:
            _unload_variant(module)
            del module
            gc.collect()

        source = read_variant_source(path)
        if 'payload["reasoning_budget"]' in source:
            raise RuntimeError(
                f"{path.name}: llama.cpp requests still use the unsupported reasoning_budget field."
            )
        if source.count("_apply_reasoning_options(payload)") < 2 or (
            'payload["thinking_budget_tokens"]' not in source
        ):
            raise RuntimeError(
                f"{path.name}: streaming and non-streaming thinking-budget payload parity is missing."
            )
        if (
            'choice = "required"' not in source
            or "_scoped_text_creation_choice" not in source
            or "Wrapper scoped file-generation adapter" not in source
        ):
            raise RuntimeError(
                f"{path.name}: bounded scoped-creation or llama.cpp forced-tool compatibility is missing."
            )


def check_named_source_prefetch_behavior(path: Path) -> None:
    module = _import_variant(path, "named_source_prefetch")
    try:
        runner_type = module._APP.base.AgentRunner
        hardened_type = next(
            (candidate for candidate in runner_type.__mro__ if candidate.__name__ == "HardenedAgentRunner"),
            None,
        )
        if hardened_type is None:
            raise RuntimeError(f"{path.name}: active runner MRO has no HardenedAgentRunner.")
        prefetch = hardened_type.__dict__.get("_prefetch_named_source_context")
        if prefetch is None:
            raise RuntimeError(f"{path.name}: named-source prefetch method is unavailable at runtime.")

        with tempfile.TemporaryDirectory(prefix="qubitz_prefetch_gate_", dir=ROOT / ".cache") as temporary:
            workspace = Path(temporary)
            runner = object.__new__(hardened_type)
            runner.workspace = workspace
            broker = module._ToolPermissionBroker(module._APP.base, workspace, workspace)
            runner._tool_permission_broker = broker
            prompt = "Read and summarize https://example.test/source using that named source."
            broker.begin_turn(prompt, None)
            original_fetch = module._fetch_named_source_for_grounding
            try:
                def offline_fetch(url: str) -> dict[str, object]:
                    raise RuntimeError(f"Offline prefetch attempted a network fetch: {url}")

                module._fetch_named_source_for_grounding = offline_fetch
                runner.network_mode = module.NETWORK_MODE_OFFLINE
                offline_events: list[tuple[str, str]] = []
                offline_result = asyncio.run(
                    prefetch(runner, prompt, lambda *event: offline_events.append(event))
                )
                if offline_result or offline_events or broker.evidence:
                    raise RuntimeError(f"{path.name}: offline named-source prefetch performed external work.")

                module._fetch_named_source_for_grounding = lambda url: {
                    "requested_url": url,
                    "final_url": url,
                    "status": 200,
                    "retrieved": True,
                    "body": "verified named-source body",
                }
                runner.network_mode = module.NETWORK_MODE_ONLINE
                online_events: list[tuple[str, str]] = []
                online_result = asyncio.run(
                    prefetch(runner, prompt, lambda *event: online_events.append(event))
                )
                if "verified named-source body" not in online_result:
                    raise RuntimeError(f"{path.name}: mocked online source text was not grounded.")
                if len(broker.evidence) != 1 or broker.evidence[0].get("tool") != "fetch_url":
                    raise RuntimeError(f"{path.name}: mocked online source evidence was not recorded exactly once.")
                if not broker.evidence[0].get("success"):
                    raise RuntimeError(f"{path.name}: successful mocked source was recorded as an error.")
                receipt = broker.current_source_receipts.get("https://example.test/source", {})
                if receipt.get("body") != "verified named-source body" or receipt.get("source_turn_id") != broker.turn_id:
                    raise RuntimeError(f"{path.name}: mocked source lacks a current-turn captured receipt.")
                if len(online_events) != 2:
                    raise RuntimeError(f"{path.name}: named-source prefetch emitted unexpected status events.")
            finally:
                module._fetch_named_source_for_grounding = original_fetch
                broker.end_turn()
    finally:
        _unload_variant(module)


def check_research_decision_behavior(path: Path) -> None:
    module = _import_variant(path, "research_decision")
    try:
        decide = module._research_decision_for_prompt
        cases = (
            (
                "Explain how this local parser works.",
                False,
                "local_first",
                (),
            ),
            (
                "Research this online and cite authoritative sources.",
                True,
                "explicit_online_research",
                (),
            ),
            (
                "Summarize https://example.test/source using that source.",
                True,
                "named_source",
                ("https://example.test/source",),
            ),
            (
                "Research https://example.test/source online and compare it with other official sources.",
                True,
                "named_source_and_general_research",
                ("https://example.test/source",),
            ),
            (
                "Search https://example.test for the installation guide.",
                True,
                "named_site_search",
                ("https://example.test",),
            ),
            (
                "What is the latest stable release information for llama.cpp?",
                True,
                "current_external_evidence",
                (),
            ),
            (
                "Open https://example.test/source in Firefox.",
                False,
                "local_first",
                (),
            ),
            (
                "Install package https://example.test/example-1.0-py3-none-any.whl.",
                False,
                "local_first",
                (),
            ),
        )
        for prompt, expected_needed, expected_reason, expected_urls in cases:
            decision = decide(prompt, module.NETWORK_MODE_ONLINE)
            if decision.needed is not expected_needed:
                raise RuntimeError(
                    f"{path.name}: research decision mismatch for {prompt!r}."
                )
            if decision.reason != expected_reason or decision.named_urls != expected_urls:
                raise RuntimeError(
                    f"{path.name}: research reason or named-source binding mismatch for {prompt!r}."
                )
            expected_search = expected_reason in {
                "explicit_online_research",
                "current_external_evidence",
                "named_source_and_general_research",
                "named_site_search",
            }
            if decision.search_requested is not expected_search:
                raise RuntimeError(
                    f"{path.name}: general-search intent mismatch for {prompt!r}."
                )
            trace = decision.to_trace_dict()
            if trace.get("needed") is not expected_needed or trace.get("reason") != expected_reason:
                raise RuntimeError(
                    f"{path.name}: research trace does not preserve the decision for {prompt!r}."
                )

        offline = decide(
            "Research this online and cite authoritative sources.",
            module.NETWORK_MODE_OFFLINE,
        )
        if not offline.needed or offline.network_allowed:
            raise RuntimeError(
                f"{path.name}: Offline mode erased research need or incorrectly allowed network work."
            )

        with tempfile.TemporaryDirectory(prefix="qubitz_research_gate_") as temporary:
            workspace = Path(temporary)
            broker = module._ToolPermissionBroker(module._APP.base, workspace, workspace)
            old_network_mode = os.environ.get(module.NETWORK_MODE_ENV_NAME)
            os.environ[module.NETWORK_MODE_ENV_NAME] = module.NETWORK_MODE_ONLINE
            try:
                broker.begin_turn("Explain how this local parser works.", None)
                allowed, denial = broker.authorize("search_web", {"query": "local parser"})
                if allowed or not isinstance(denial, dict) or denial.get("status") != "research_not_requested":
                    raise RuntimeError(
                        f"{path.name}: a local-first turn did not block unsolicited web research."
                    )
                broker.end_turn()
                broker.begin_turn("Research this online and cite authoritative sources.", None)
                allowed, denial = broker.authorize("search_web", {"query": "authoritative sources"})
                if not allowed or denial is not None:
                    raise RuntimeError(
                        f"{path.name}: an explicit online-research request was incorrectly denied."
                    )
                broker.end_turn()
            finally:
                if old_network_mode is None:
                    os.environ.pop(module.NETWORK_MODE_ENV_NAME, None)
                else:
                    os.environ[module.NETWORK_MODE_ENV_NAME] = old_network_mode
    finally:
        _unload_variant(module)


def check_research_response_url_behavior(path: Path) -> None:
    module = _import_variant(path, "url_provenance")
    try:
        runner_type = module._APP.base.AgentRunner
        hardened_type = next(
            (candidate for candidate in runner_type.__mro__ if candidate.__name__ == "HardenedAgentRunner"),
            None,
        )
        if hardened_type is None:
            raise RuntimeError(f"{path.name}: active runner MRO has no HardenedAgentRunner.")
        enforce = hardened_type.__dict__.get("_enforce_research_response_urls")
        if enforce is None:
            raise RuntimeError(f"{path.name}: research response URL enforcement is unavailable.")

        with tempfile.TemporaryDirectory(prefix="qubitz_url_gate_") as temporary:
            workspace = Path(temporary)
            broker = module._ToolPermissionBroker(module._APP.base, workspace, workspace)
            broker.begin_turn("Research this online and cite sources.", None)
            broker.record_result(
                "search_web",
                {"query": "test"},
                SimpleNamespace(
                    isError=False,
                    structuredContent={
                        "status": 200,
                        "results": [
                            {"title": "Discovered", "url": "https://discovered.example/item"}
                        ],
                    },
                ),
            )
            broker.record_result(
                "fetch_url",
                {"url": "https://source.example/article"},
                SimpleNamespace(
                    isError=False,
                    structuredContent={
                        "status": 200,
                        "requested_url": "https://source.example/article",
                        "final_url": "https://source.example/article",
                        "body": "Untrusted body URL: https://body-only.example/not-provenance",
                    },
                ),
            )
            fetch_evidence = broker._current_turn_evidence()[-1]
            if fetch_evidence["urls"] != ["https://source.example/article"]:
                raise RuntimeError(f"{path.name}: fetch body URLs leaked into source provenance.")

            runner = object.__new__(hardened_type)
            runner._tool_permission_broker = broker
            events: list[tuple[str, str]] = []
            answer = (
                "Supported [source](https://source.example/article). "
                "Discovered https://discovered.example/item. "
                "Invented https://invented.example/claim and malformed https://bad_host.example/path."
            )
            rendered = enforce(
                runner,
                "Research this online and cite sources.",
                answer,
                lambda *event: events.append(event),
            )
            if "https://source.example/article" not in rendered:
                raise RuntimeError(f"{path.name}: verified source URL was removed.")
            if "https://discovered.example/item" not in rendered:
                raise RuntimeError(f"{path.name}: observed search-result URL was removed.")
            if "invented.example" in rendered or "bad_host.example" in rendered:
                raise RuntimeError(f"{path.name}: unsupported response URL survived enforcement.")
            if rendered.count("[unverified URL omitted]") != 2 or len(events) != 1:
                raise RuntimeError(f"{path.name}: unsupported URL reporting is inconsistent.")

            source_appended = enforce(
                runner,
                "Research this online and cite sources.",
                "A sourced summary without an inline link.\n\n[Completion status: verified]",
                None,
            )
            if "Sources:\n- https://source.example/article" not in source_appended:
                raise RuntimeError(f"{path.name}: verified source fallback was not appended.")
            if not source_appended.endswith("[Completion status: verified]"):
                raise RuntimeError(f"{path.name}: URL enforcement moved completion status from the final line.")
            broker.end_turn()

        if module._canonical_http_url("https://user:secret@example.com/path"):
            raise RuntimeError(f"{path.name}: credential-bearing URL passed validation.")
        if module._canonical_http_url("https://bad_host.example/path"):
            raise RuntimeError(f"{path.name}: malformed hostname passed validation.")
        if module._canonical_http_url("https://[2001:db8::1]:443/a") != "https://[2001:db8::1]/a":
            raise RuntimeError(f"{path.name}: valid IPv6 URL canonicalization failed.")
    finally:
        _unload_variant(module)


def check_harness_route_parity(harness_text: str, paths: Sequence[Path]) -> None:
    harness_text = "\n".join(harness_text.splitlines())
    _head, header, remainder = harness_text.partition("Task Routes\n")
    section, handling, _tail = remainder.partition("Route handling:")
    if not header or not handling:
        raise RuntimeError("HARNESS.txt has no complete route declaration section.")
    declarations = re.findall(r"^\d+\. `([a-z_]+)`", section, re.MULTILINE)
    routes = set(declarations)
    if not routes or len(routes) != len(declarations):
        raise RuntimeError("HARNESS.txt route declarations are missing or duplicated.")
    blocks = set(re.findall(r"^### BLOCK: ([A-Z_]+) ###", harness_text, re.MULTILINE))
    for path in paths:
        tree = ast.parse(read_variant_source(path))
        assignment = next(
            (node for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == "_HARNESS_ROUTE_BLOCKS"
                     for target in node.targets)),
            None,
        )
        mapping = ast.literal_eval(assignment.value) if assignment is not None else {}
        if not isinstance(mapping, dict) or set(mapping) != routes:
            actual = set(mapping) if isinstance(mapping, dict) else set()
            raise RuntimeError(
                f"{path.name}: harness route-map mismatch; "
                f"missing={sorted(routes - actual)}, unknown={sorted(actual - routes)}"
            )
        for route, selected in mapping.items():
            if not isinstance(selected, (tuple, list)) or any(block not in blocks for block in selected):
                raise RuntimeError(f"{path.name}: invalid harness blocks for route {route!r}.")


def check_harness() -> tuple[str, str]:
    from runtime.policy_loader import load_harness

    plaintext_path = ROOT / "HARNESS.txt"
    encrypted_path = ROOT / "HARNESS.enc"
    if not plaintext_path.is_file() or not encrypted_path.is_file():
        raise FileNotFoundError("HARNESS.txt and HARNESS.enc are both required for release.")
    plaintext = plaintext_path.read_bytes()
    decrypted = load_harness(encrypted_path).encode("utf-8")
    if decrypted != plaintext:
        raise RuntimeError("HARNESS.enc does not decrypt byte-for-byte to HARNESS.txt.")
    bundle = json.loads((ROOT / "runtime/policy_bundle.json").read_text(encoding="utf-8"))
    for artifact in bundle["artifacts"].values():
        path = ROOT / "runtime" / artifact["filename"]
        if path.is_symlink() or path.parent != ROOT / "runtime" or sha256(path) != artifact["sha256"]:
            raise RuntimeError("Native policy bundle artifact is missing, aliased or changed.")
    harness_text = plaintext.decode("utf-8")
    required_rules = (
        "parallelize only independent read-only, idempotent calls that cannot conflict",
        "Use a bounded workflow tool only when multiple concrete steps and their dependencies are already known",
    )
    missing_rules = [rule for rule in required_rules if rule not in harness_text]
    if missing_rules:
        raise RuntimeError(f"HARNESS.txt is missing orchestration rule(s): {missing_rules}")
    check_harness_route_parity(harness_text, variant_paths())
    return sha256(plaintext_path), sha256(encrypted_path)


def check_ignored(paths: Sequence[str]) -> None:
    for path in paths:
        run_checked(
            f"Git ignore protection for {path}",
            ("git", "check-ignore", "--quiet", "--", path),
        )


def main() -> int:
    from runtime.paths import variant_path

    variants = variant_paths()
    from evaluation.verification_outcomes import probe_issues, verification_status

    for status, expected in (("passed", "passed"), ("failed", "failed"), ("unavailable", "blocked"),
                             ("error", "harness_error"), ("unknown", "harness_error")):
        if verification_status(probe_issues({"status": status}, "release_probe")) != expected:
            raise RuntimeError("Shared evaluator outcome classification failed release admission")
    run_checked(
        "common-wrapper and declarative-profile parity",
        (sys.executable, "qubitz_variant_builder.py", "--check"),
    )
    print("[gate] Python syntax")
    check_syntax((*variants, *(ROOT / name for name in RELEASE_PYTHON_FILES)))
    check_syntax([path for path in runtime_dependency_paths(ROOT) if path.suffix == ".py"])
    print("[gate] declarative runtime and intentional-variant contracts")
    check_variant_runtime_contracts(variants)
    print("[gate] registered local-tool permission and evidence contracts")
    check_local_tool_contracts(variants)
    print("[gate] hardened runner method contract")
    check_hardened_runner_contract(variants)
    print("[gate] bounded workflow, parallel reads, tool policy, retrieval, and cancellation")
    check_orchestration_runtime_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] all-variant route interface compatibility")
    check_route_interface_compatibility(variants)
    print("[gate] follow-up, mixed-turn, and new-request semantics")
    check_turn_relation_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] sampling profiles for every model alias and route")
    check_sampling_profiles(variants)
    print("[gate] sampling GUI override, payload parity, and restoration")
    check_sampling_runtime_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] GUI completion telemetry, role colors, and repeatable copying")
    check_gui_output_contract(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] named-source prefetch behavior")
    check_named_source_prefetch_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] wrapper-owned research decisions")
    check_research_decision_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    print("[gate] research response URL provenance")
    check_research_response_url_behavior(variant_path(REFERENCE_VARIANT_NAME, ROOT))
    run_checked(
        "Ruff",
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "E9,F63,F7,F82",
            *(str(path.relative_to(ROOT)) for path in variants),
            *RELEASE_PYTHON_FILES,
            *(str(path.relative_to(ROOT)) for path in runtime_dependency_paths(ROOT) if path.suffix == ".py"),
        ),
    )
    print("[gate] encrypted harness integrity")
    print("[gate] all-variant effective-source Ruff")
    check_effective_source_lint(variants)
    plaintext_sha256, encrypted_sha256 = check_harness()
    check_ignored(("HARNESS.txt", LOCAL_HARNESS_KEY_NAME))
    print(f"[gate] variants={len(variants)}")
    print(f"[gate] harness_plaintext_sha256={plaintext_sha256}")
    print(f"[gate] harness_encrypted_sha256={encrypted_sha256}")
    print("[gate] PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
