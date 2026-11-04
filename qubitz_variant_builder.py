from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime import read_variant_source
from runtime.development import replace_variant_source
from runtime.paths import all_variant_paths, variant_path
from runtime.paths import variant_paths as discover_variant_paths
from runtime.source import launcher_spec
from runtime.symbols import MODULE_PATTERN, symbol_sources
from runtime.variant_policy import user_variant_allowed

ROOT = Path(__file__).resolve().parent
VARIANT_GLOB = "AI_Agent_Qubitz_*.py"
DEFAULT_REFERENCE = "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"
TEMPLATE_PATH = ROOT / "qubitz_common_wrapper_template.json"
PROFILES_PATH = ROOT / "qubitz_variant_profiles.json"
BACKUP_DIR = ROOT / "backup"
CHANGELOG_PATH = ROOT / "changelog.txt"

PROFILE_CONSTANTS = {
    "model_alias": "DEFAULT_MODEL",
    "hf_gguf_repo_id": "DEFAULT_HF_GGUF_REPO_ID",
    "gguf_filename": "DEFAULT_GGUF_MODEL_FILENAME",
    "gguf_label": "DEFAULT_GGUF_MODEL_LABEL",
    "embedding_model": "DEFAULT_EMBED_MODEL",
    "num_ctx": "DEFAULT_NUM_CTX",
    "num_predict": "DEFAULT_NUM_PREDICT",
}
PROFILE_COMPATIBILITY_CONSTANTS = {
    "gguf_filename": (
        "Q4_GGUF_MODEL_FILENAME",
        "Q5_GGUF_MODEL_FILENAME",
        "Q6_GGUF_MODEL_FILENAME",
    ),
}
STRUCTURAL_PROFILE_KEYS = ("background_prewarm", "isolated_llamacpp_runtime")
TEMPLATE_SCHEMA_VERSION = 4
PROFILE_SCHEMA_VERSION = 2
RUNTIME_ROOT_LAYOUT_GUARD = (
    '        if (self.runtime_workspace.name == "variants"\n'
    '                and (self.runtime_workspace.parent / "runtime" / "__init__.py").is_file()):\n'
    '            self.runtime_workspace = self.runtime_workspace.parent\n'
)
RUNTIME_ROOT_TRANSPORT_PROBE = "base.llamacpp_native_server_executable(_APP.runtime_workspace)"
LEGACY_CWD_TRANSPORT_PROBE = "base.llamacpp_native_server_executable(Path.cwd())"
TRANSPORT_PROBE_QUALNAME = "_patch_local_only_dependencies._reachable_transports"
EMBEDDED_COMMON_FUNCTIONS = ("AgentRunner._run_async",)
RUNTIME_CONTRACT_EXACT_NAMES = {"QUBITZ_VRAM_BUDGET_MIB"}
RUNTIME_CONTRACT_SUFFIXES = (
    "_GPU_INDICES",
    "_GPU_SAFETY_MARGIN_MIB",
    "_MIN_FIT_CONTEXT",
    "_DEFAULT_KV_CACHE_TYPE",
    "_KV_CACHE_TYPE",
    "_GGUF_SHARDS",
    "_GGUF_PARTS",
    "_REQUIRED_LLAMA_FLAGS",
    "_ISOLATED_LLAMA_TAG",
    "_ISOLATED_LLAMA_RUNTIME_DIR_NAME",
    "_LLAMACPP_RUNTIME_DIR_NAME",
    "_LLAMACPP_NATIVE_RUNTIME_DIR_NAME",
)

SEMANTIC_START = "@dataclass(frozen=True)\nclass SemanticIntent:"
SEMANTIC_END = "\n\nDIRECT_RESULT_MISSING_URL ="
HARNESS_START = "_HARNESS_ROUTE_BLOCKS = {"
HARNESS_END = "\n\nclass _ToolPermissionBroker:"
MUTATION_TOOLS_START = "    _MUTATION_TOOLS = {"
MUTATION_TOOLS_END = "\n    _EXECUTION_TOOLS = {"
EFFORT_BUDGET_START = 'THINKING_EFFORT_OPTIONS = ("low", "medium", "high", "xhigh")'
EFFORT_BUDGET_END = "\nSIMPLE_DIRECT_QUESTION_STEP_CAP ="
SAMPLING_START = "ROUTE_SAMPLING_PROFILES:"
SAMPLING_END = "\n\ndef _sampling_value"
ENHANCED_GUI_PREFIX = "LocalOnlyApp._build_gui_class.EnhancedGUI"
ENHANCED_GUI_NEW_COPY_METHODS = (
    f"{ENHANCED_GUI_PREFIX}._selected_text_for_copy",
    f"{ENHANCED_GUI_PREFIX}._copy_selected_text",
    f"{ENHANCED_GUI_PREFIX}._copy_context_selection",
    f"{ENHANCED_GUI_PREFIX}._show_copy_context_menu",
)
ENHANCED_GUI_COPY_HANDLER = f"{ENHANCED_GUI_PREFIX}._handle_copy_shortcut"
ENHANCED_GUI_TAG_SETUP = f"{ENHANCED_GUI_PREFIX}._ensure_transcript_tags"
ENHANCED_GUI_APPEND = f"{ENHANCED_GUI_PREFIX}._append_transcript"

COMMON_FUNCTIONS = (
    "_json_schema_errors",
    "_mcp_result_has_error",
    "_requested_test_entrypoints",
    "_requests_test_execution",
    "_requested_mcp_tool_names",
    "_record_direct_entrypoint_execution",
    "_actionable_committed_pytest_failure",
    "_direct_wheel_public_version_matches",
    "ResearchDecision",
    "_canonical_http_url",
    "_check_html_dependencies",
    "_is_external_http_url",
    "_validated_external_urls",
    "_extract_external_urls",
    "_prompt_is_browser_only_url_action",
    "_validate_html_artifact",
    "_check_graphical_html_visibility",
    "_unverified_html_behavior_postconditions",
    "_run_inline_browser_open",
    "_reported_entrypoint_browser_urls",
    "_named_source_urls",
    "_prompt_requests_general_web_search",
    "_research_decision_for_prompt",
    "_prompt_requests_network_research",
    "_tool_arguments_request_external_network",
    "_tool_annotations",
    "_loaded_llamacpp_context_window",
    "_patch_streaming_chat",
    "_sampling_value",
    "_apply_sampling_profile",
    "_sampling_state_snapshot",
    "_restore_sampling_state",
    "_sampling_profile_status",
    "_install_gpu_thermal_gate",
    "_install_optional_mcp_capabilities",
    "_install_gui_run_controls",
    "_install_gui_temperature_status_patch.TemperatureStatusGUI.__init__",
    "_install_gui_temperature_status_patch.TemperatureStatusGUI._append_transcript",
    "_install_gui_temperature_status_patch.TemperatureStatusGUI._sync_runtime_settings",
    "_project_environment_candidates",
    "_explicit_prompt_environment",
    "_probe_python_environment",
    "_inspect_project_python_environment",
    "_preferred_project_linux_python",
    "_preferred_project_windows_python",
    "_preferred_project_python",
    "_workspace_runtime_capabilities",
    "_select_direct_workspace_python",
    "_decode_text_preserving_encoding",
    "_encode_text_preserving_encoding",
    "_extract_explicit_quoted_edit_target",
    "_binary_file_notice",
    "_structured_text_edit_error",
    "LocalCodeIntel._parse_generic",
    "_patch_cuda_retrieval_runtime",
    "_install_pinned_llamacpp_runtime",
    "_patch_llamacpp_launch_fit",
    "_install_daily_memory_store",
    "_resolve_unique_workspace_file_by_basename",
    "_user_context_aliases",
    "_verified_created_alias_paths",
    "_verified_recent_alias_paths",
    "_resolve_user_context_aliases",
    "_expand_alias_tool_paths",
    "_task_outcome_feedback",
    "_task_outcome_requires_repair",
    "_implicit_artifact_revision_request",
    "_unexecuted_tool_call_name",
    "_verified_workspace_scalar_answer",
    "_retained_verified_edit_receipt",
    "_historical_edit_source_evidence",
    "_verified_edit_receipt_answer",
    "_install_agent_tool_hardening.HardenedMCPHost.call_tool",
    "_install_agent_tool_hardening.HardenedAgentRunner.__init__",
    "_install_agent_tool_hardening.HardenedAgentRunner._try_direct_existing_script_completion",
    "_install_agent_tool_hardening.HardenedAgentRunner._runtime_fact_block",
    "_install_agent_tool_hardening.HardenedAgentRunner._run_async",
    "LocalMCPServerManager.__init__",
    "LocalMCPServerManager._read_meta",
    "LocalMCPServerManager._probe_mcp_python",
    "LocalMCPServerManager._resolve_mcp_python",
    "LocalMCPServerManager._resolve_reference",
    "LocalMCPServerManager.start_server",
    "LocalMCPServerManager.list_tools",
    "LocalMCPServerManager.call_tool",
    "_build_local_mcp_server._relative_exclusion_parts",
    "_build_local_mcp_server._is_excluded_candidate",
    "_build_local_mcp_server.list_files",
    "_build_local_mcp_server.search_tools",
    "_build_local_mcp_server.search_text",
    "_build_local_mcp_server.inspect_python_environment",
    "_build_local_mcp_server.install_python_package",
    "_build_local_mcp_server.write_file",
    "_NewTextFile",
    "_build_local_mcp_server.write_files",
    "_build_local_mcp_server.delete_path",
    "_build_local_mcp_server.move_path",
    "_build_local_mcp_server.read_file_snapshot",
    "_build_local_mcp_server.apply_docx_text_patch",
    "_build_local_mcp_server.apply_notebook_text_patch",
    "_SpreadsheetCellEdit",
    "_build_local_mcp_server.apply_spreadsheet_cell_patch",
    "_build_local_mcp_server.apply_text_patch",
    "_build_local_mcp_server.apply_text_patches",
    "_embedded_encrypted_only_harness_source",
    "_load_embedded_base_module",
    "_patch_harness_loader",
    *ENHANCED_GUI_NEW_COPY_METHODS,
    ENHANCED_GUI_COPY_HANDLER,
    ENHANCED_GUI_TAG_SETUP,
    ENHANCED_GUI_APPEND,
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._extract_route_features",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._is_coding_repair_task",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._filter_cached_tool_definitions",
    "_install_agent_tool_hardening.HardenedMCPHost.list_tools",
    "_install_agent_tool_hardening.HardenedMCPHost._tool_call_key",
    "_install_agent_tool_hardening.HardenedMCPHost._parallel_read_only_tool",
    "_install_agent_tool_hardening.HardenedMCPHost._consume_parallel_read_only_batch",
    "_install_agent_tool_hardening.HardenedMCPHost._resolve_workflow_value",
    "_install_agent_tool_hardening.HardenedMCPHost._execute_workflow",
    "_build_local_mcp_server.run_existing_entrypoint",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._resolve_read_only_prompt_files",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._build_read_only_workspace_context",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._build_metadata_or_lexical_context",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._task_class_label",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._runtime_fact_block",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._effort_step_budget_profile",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._effective_max_steps",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._effective_prompt_max_steps",
    "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._adaptive_step_budget",
    "_ToolPermissionBroker.__init__",
    "_ToolPermissionBroker._result_fingerprint",
    "_ToolPermissionBroker._mcp_invocation_key",
    "_ToolPermissionBroker.reserve_mcp_invocation",
    "_ToolPermissionBroker.bind_mcp_action_intent",
    "_ToolPermissionBroker.preserve_mcp_call_arguments",
    "_ToolPermissionBroker._mcp_schema_for_call",
    "_ToolPermissionBroker._explicit_mcp_arguments_satisfied",
    "_ToolPermissionBroker._references_protected_harness",
    "_ToolPermissionBroker.begin_turn",
    "_ToolPermissionBroker.bind_follow_up_repair",
    "_ToolPermissionBroker.register_transaction_original",
    "_ToolPermissionBroker.end_turn",
    "_ToolPermissionBroker._invalid_error_class_key",
    "_ToolPermissionBroker.register_invalid_call",
    "_ToolPermissionBroker.register_permission_denial",
    "_ToolPermissionBroker.progress_token",
    "_ToolPermissionBroker._transaction_progress_token",
    "_ToolPermissionBroker._track_transaction_recovery_progress",
    "_ToolPermissionBroker.stage_text_patch_arguments",
    "_ToolPermissionBroker.transaction_recovery_context",
    "_ToolPermissionBroker.semantic_argument_error",
    "_ToolPermissionBroker._register_mcp_launch_result",
    "_ToolPermissionBroker.record_result",
    "_ToolPermissionBroker._completion_requirements",
    "_ToolPermissionBroker.completion_report",
    "_ToolPermissionBroker.update_focused_postconditions",
    "_ToolPermissionBroker.completion_evidence_summary",
    "_ToolPermissionBroker.mark_transaction_recovery_required",
    "_ToolPermissionBroker.rollback_transaction",
    "_ToolPermissionBroker.reset_transaction_after_rollback",
    "_ToolPermissionBroker._outside_workspace_paths",
    "_ToolPermissionBroker._command_text",
    "_ToolPermissionBroker._missing_source_evidence_urls",
    "_ToolPermissionBroker.bind_captured_sources",
    "_ToolPermissionBroker.authorize",
    "_install_agent_tool_hardening.HardenedMCPHost._success_result",
    "_install_agent_tool_hardening.HardenedMCPHost._call_broker_bound_patch_tool",
    "_install_agent_tool_hardening.HardenedMCPHost.call_tool",
    "_install_agent_tool_hardening.one_tool_chat",
    "_install_agent_tool_hardening.build_semantic_mcp_server.fetch_url",
    "_install_agent_tool_hardening.build_semantic_mcp_server.search_web",
    "_install_agent_tool_hardening.build_semantic_mcp_server.copy_path",
    "_install_agent_tool_hardening.HardenedAgentRunner._request_transaction_recovery",
    "_install_agent_tool_hardening.HardenedAgentRunner._run_existing_script_preflight",
    "_install_agent_tool_hardening.HardenedAgentRunner._request_mcp_recovery",
    "_install_agent_tool_hardening.HardenedAgentRunner._request_verified_completion",
    "_install_agent_tool_hardening.HardenedAgentRunner._request_wrapper_stop",
    "_install_agent_tool_hardening.HardenedAgentRunner.request_cancel",
    "_install_agent_tool_hardening.HardenedAgentRunner._raise_if_cancelled",
    "_install_agent_tool_hardening.HardenedAgentRunner._format_completion_telemetry",
    "_install_agent_tool_hardening.HardenedAgentRunner.run_sync",
    "_install_agent_tool_hardening.HardenedAgentRunner.__init__",
    "_install_agent_tool_hardening.HardenedAgentRunner._task_intent_for_prompt",
    "_install_agent_tool_hardening.HardenedAgentRunner._select_task_guidance",
    "_install_agent_tool_hardening.HardenedAgentRunner._read_only_external_path_preflight",
    "_install_agent_tool_hardening.HardenedAgentRunner._empty_workspace_listing_preflight",
    "_install_agent_tool_hardening.HardenedAgentRunner._dependency_install_preflight",
    "_install_agent_tool_hardening.HardenedAgentRunner._named_file_operation_preflight",
    "_install_agent_tool_hardening.HardenedAgentRunner._decide_route",
    "_install_agent_tool_hardening.HardenedAgentRunner._selected_route_name",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_no_progress_tool_choice",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_existing_test_target",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_verify_committed_transaction",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_docx_edit_recovery",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_transaction_proposal_recovery",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_saved_contract_snapshots",
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_mcp_lifecycle_recovery",
    "_install_agent_tool_hardening.HardenedAgentRunner._filter_tools_by_intent",
    "_install_agent_tool_hardening.HardenedAgentRunner._prioritize_tools_for_prompt",
    "_install_agent_tool_hardening.HardenedAgentRunner._prefetch_named_source_context",
    "_install_agent_tool_hardening.HardenedAgentRunner._enforce_research_response_urls",
    "_install_agent_tool_hardening.HardenedAgentRunner._social_fast_path_response",
    "_install_agent_tool_hardening.HardenedAgentRunner._apply_side_notes",
    "_install_agent_tool_hardening.HardenedAgentRunner._run_async",
    "_install_agent_tool_hardening.HardenedAgentRunner._verified_mcp_result_answer",
    "_install_agent_tool_hardening.HardenedAgentRunner._runtime_tool_contract_for_harness",
    "_install_agent_tool_hardening.HardenedAgentRunner._system_prompt",
    "_install_agent_tool_hardening.HardenedGUI.__init__",
    "_install_agent_tool_hardening.HardenedGUI._install_variant_selector",
    "_install_agent_tool_hardening.HardenedGUI._apply_network_mode",
    "_install_agent_tool_hardening.HardenedGUI._sync_runtime_settings",
    "_install_agent_tool_hardening.HardenedGUI._set_busy",
    "_install_agent_tool_hardening.HardenedGUI._finalize_elapsed_message",
    "_install_agent_tool_hardening.HardenedGUI._worker_run_tagged",
    "_install_agent_tool_hardening.HardenedGUI._poll_events",
    "_install_agent_tool_hardening.HardenedGUI._drain_gui_events",
    "_install_agent_tool_hardening.HardenedGUI._consume_gui_events",
)

TASK_INTENT_QUALNAME = "_install_agent_tool_hardening.HardenedAgentRunner._task_intent_for_prompt"
ROUTE_DECISION_QUALNAME = "_install_agent_tool_hardening.HardenedAgentRunner._decide_route"
NO_PROGRESS_CHOICE_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_no_progress_tool_choice"
)
EXISTING_TEST_TARGET_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_existing_test_target"
)
COMMITTED_TRANSACTION_VERIFY_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_verify_committed_transaction"
)
DOCX_EDIT_RECOVERY_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_docx_edit_recovery"
)
TRANSACTION_PROPOSAL_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_transaction_proposal_recovery"
)
MCP_LIFECYCLE_RECOVERY_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_mcp_lifecycle_recovery"
)
NAMED_SOURCE_PREFETCH_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._prefetch_named_source_context"
)
RESEARCH_RESPONSE_URLS_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._enforce_research_response_urls"
)
SOCIAL_FAST_PATH_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._social_fast_path_response"
)
NAMED_FILE_PREFLIGHT_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._named_file_operation_preflight"
)
TRANSACTION_RESET_QUALNAME = "_ToolPermissionBroker.reset_transaction_after_rollback"
SOURCE_EVIDENCE_QUALNAME = "_ToolPermissionBroker._missing_source_evidence_urls"
EMBEDDED_HARNESS_SOURCE_QUALNAME = "_embedded_encrypted_only_harness_source"
EMBEDDED_LOADER_QUALNAME = "_load_embedded_base_module"
PROTECTED_HARNESS_QUALNAME = "_ToolPermissionBroker._references_protected_harness"
TOOL_AUTHORIZE_QUALNAME = "_ToolPermissionBroker.authorize"
TASK_GUIDANCE_QUALNAME = "_install_agent_tool_hardening.HardenedAgentRunner._select_task_guidance"
TOOL_DESCRIPTION_QUALNAME = "_install_agent_tool_hardening.HardenedAgentRunner._tool_description"
FETCH_URL_QUALNAME = "_install_agent_tool_hardening.build_semantic_mcp_server.fetch_url"
SEARCH_WEB_QUALNAME = "_install_agent_tool_hardening.build_semantic_mcp_server.search_web"
GUI_NETWORK_MODE_QUALNAME = "_install_agent_tool_hardening.HardenedGUI._apply_network_mode"
GUI_VARIANT_SELECTOR_QUALNAME = (
    "_install_agent_tool_hardening.HardenedGUI._install_variant_selector"
)
GUI_SYNC_RUNTIME_QUALNAME = "_install_agent_tool_hardening.HardenedGUI._sync_runtime_settings"
DOCX_PATCH_QUALNAME = "_build_local_mcp_server.apply_docx_text_patch"
TEXT_PATCH_QUALNAME = "_build_local_mcp_server.apply_text_patch"
LEGACY_LOCAL_SPREADSHEET_EDIT_QUALNAME = "_build_local_mcp_server._SpreadsheetCellEdit"
WRAPPER_STOP_QUALNAME = "_install_agent_tool_hardening.HardenedAgentRunner._request_wrapper_stop"
TRANSACTION_RECOVERY_REQUEST_QUALNAME = (
    "_install_agent_tool_hardening.HardenedAgentRunner._request_transaction_recovery"
)
ENHANCED_SYSTEM_PROMPT = "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._system_prompt"
SIMPLE_BLOCK_START = "                if self._simple_direct_mode:\n"
SIMPLE_BLOCK_END = "                original = super()._system_prompt"


@dataclass(frozen=True)
class NodeSpan:
    qualname: str
    start: int
    end: int
    text: str


def variant_paths() -> list[Path]:
    return discover_variant_paths(ROOT)


def line_offsets(text: str) -> list[int]:
    offsets = [0]
    for index, character in enumerate(text):
        if character == "\n":
            offsets.append(index + 1)
    return offsets


def node_text_span(text: str, node: ast.AST) -> tuple[int, int]:
    offsets = line_offsets(text)
    start = offsets[node.lineno - 1] + node.col_offset
    end = offsets[node.end_lineno - 1] + node.end_col_offset
    return start, end


def named_assignment_value(tree: ast.Module, name: str) -> ast.expr:
    matches: list[ast.expr] = []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == name for target in targets):
            matches.append(node.value)
    if len(matches) != 1:
        raise RuntimeError(f"Expected exactly one assignment to {name}, found {len(matches)}.")
    return matches[0]


def embedded_base_source(text: str) -> tuple[str, tuple[int, int]]:
    matches: list[ast.expr] = []
    for node in ast.parse(text).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "_EMBEDDED_BASE_SOURCE"
            for target in node.targets
        ):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            matches.append(node.value)
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one literal assignment to _EMBEDDED_BASE_SOURCE, "
            f"found {len(matches)}."
        )
    value = matches[0]
    source = ast.literal_eval(value)
    if not isinstance(source, str):
        raise RuntimeError("_EMBEDDED_BASE_SOURCE must be a string literal.")
    return source, node_text_span(text, value)


def module_constant_values(source: str, names: set[str]) -> dict[str, Any]:
    tree = ast.parse(source)
    result: dict[str, Any] = {}
    for name in names:
        value = named_assignment_value(tree, name)
        result[name] = ast.literal_eval(value)
    return result


def replace_module_constants(source: str, replacements: dict[str, Any]) -> str:
    tree = ast.parse(source)
    edits: list[tuple[int, int, str]] = []
    for name, replacement in replacements.items():
        value = named_assignment_value(tree, name)
        start, end = node_text_span(source, value)
        edits.append((start, end, repr(replacement)))
    for start, end, replacement in sorted(edits, reverse=True):
        source = f"{source[:start]}{replacement}{source[end:]}"
    ast.parse(source)
    return source


def node_spans(text: str) -> dict[str, NodeSpan]:
    tree = ast.parse(text)
    offsets = line_offsets(text)
    result: dict[str, NodeSpan] = {}

    def visit(node: ast.AST, path: tuple[str, ...] = ()) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                child_path = (*path, child.name)
                qualname = ".".join(child_path)
                start_line = min(
                    [child.lineno, *(decorator.lineno for decorator in getattr(child, "decorator_list", []))]
                )
                start = offsets[start_line - 1]
                end_line = child.end_lineno or child.lineno
                end = offsets[end_line] if end_line < len(offsets) else len(text)
                result[qualname] = NodeSpan(qualname, start, end, text[start:end])
                visit(child, child_path)
            else:
                visit(child, path)

    visit(tree)
    return result


def delimited_section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def simple_prompt_block(text: str) -> str:
    system_prompt = node_spans(text)[ENHANCED_SYSTEM_PROMPT].text
    start = system_prompt.index(SIMPLE_BLOCK_START)
    end = system_prompt.index(SIMPLE_BLOCK_END, start)
    return system_prompt[start:end]


def universal_module_sources() -> dict[str, str]:
    directory = ROOT / "runtime"
    registry = directory / "wrapper.shared.py.in"
    if not registry.is_file():
        return {}
    text = registry.read_text(encoding="utf-8")
    values = symbol_sources("wrapper", directory)
    return {match[2]: values[match[2]] for match in MODULE_PATTERN.finditer(text)}


def _intentional_variant_residual(text: str) -> str:
    """Remove synchronized regions so all remaining variant code can be preserved exactly."""
    # Only byte-identical registered universal source is excluded. Modified or
    # duplicated module text remains fingerprinted, never normalized away.
    for module in universal_module_sources().values():
        if text.count(module) == 1:
            text = text.replace(module, "", 1)
    constructor = node_spans(text).get("LocalOnlyApp.__init__")
    if constructor is not None and constructor.text.count(RUNTIME_ROOT_LAYOUT_GUARD) == 1:
        # This exact shared layout block is validated separately by the template
        # check; retain the original preservation hashes for variant-specific code.
        normalized = constructor.text.replace(RUNTIME_ROOT_LAYOUT_GUARD, "", 1)
        text = text[:constructor.start] + normalized + text[constructor.end:]
    transport_probe = node_spans(text).get(TRANSPORT_PROBE_QUALNAME)
    if transport_probe is not None and transport_probe.text.count(RUNTIME_ROOT_TRANSPORT_PROBE) == 1:
        # This exact layout correction is checked separately; other transport
        # changes, including North's listener condition, stay fingerprinted.
        normalized = transport_probe.text.replace(RUNTIME_ROOT_TRANSPORT_PROBE, LEGACY_CWD_TRANSPORT_PROBE, 1)
        text = text[:transport_probe.start] + normalized + text[transport_probe.end:]
    embedded_source, (embedded_start, embedded_end) = embedded_base_source(text)
    embedded_spans = node_spans(embedded_source)
    embedded_ranges = [
        (embedded_spans[name].start, embedded_spans[name].end)
        for name in EMBEDDED_COMMON_FUNCTIONS
    ]
    for start, end in sorted(embedded_ranges, reverse=True):
        embedded_source = (
            f"{embedded_source[:start]}\n<QUBITZ_EMBEDDED_SYNCHRONIZED_REGION>\n"
            f"{embedded_source[end:]}"
        )
    text = f"{text[:embedded_start]}{embedded_source!r}{text[embedded_end:]}"
    spans = node_spans(text)
    ranges: list[tuple[int, int]] = []
    for start_marker, end_marker in (
        (EFFORT_BUDGET_START, EFFORT_BUDGET_END),
        (SAMPLING_START, SAMPLING_END),
        (SEMANTIC_START, SEMANTIC_END),
        (HARNESS_START, HARNESS_END),
        (MUTATION_TOOLS_START, MUTATION_TOOLS_END),
    ):
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        ranges.append((start, end))

    system_prompt = spans[ENHANCED_SYSTEM_PROMPT]
    local_start = system_prompt.text.index(SIMPLE_BLOCK_START)
    local_end = system_prompt.text.index(SIMPLE_BLOCK_END, local_start)
    ranges.append((system_prompt.start + local_start, system_prompt.start + local_end))
    for qualname in (*COMMON_FUNCTIONS, LEGACY_LOCAL_SPREADSHEET_EDIT_QUALNAME):
        span = spans.get(qualname)
        if span is not None:
            ranges.append((span.start, span.end))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and (
            start <= merged[-1][1]
            or not text[merged[-1][1] : start].strip()
        ):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    pieces: list[str] = []
    cursor = 0
    for start, end in merged:
        pieces.append(text[cursor:start])
        pieces.append("\n<QUBITZ_SYNCHRONIZED_REGION>\n")
        cursor = end
    pieces.append(text[cursor:])
    residual = "".join(pieces)
    marker = "<QUBITZ_SYNCHRONIZED_REGION>"
    while f"\n\n\n{marker}" in residual:
        residual = residual.replace(f"\n\n\n{marker}", f"\n\n{marker}")
    while f"{marker}\n\n\n" in residual:
        residual = residual.replace(f"{marker}\n\n\n", f"{marker}\n\n")
    return residual


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_compatible(item) for item in value]
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_compatible(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def runtime_contract_from_text(text: str) -> dict[str, Any]:
    """Capture model-specific launch invariants without executing a variant."""
    constants: dict[str, Any] = {}
    for node in ast.parse(text).body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            name = target.id
            if name not in RUNTIME_CONTRACT_EXACT_NAMES and not name.endswith(
                RUNTIME_CONTRACT_SUFFIXES
            ):
                continue
            try:
                constants[name] = _json_compatible(ast.literal_eval(node.value))
            except (TypeError, ValueError):
                constants[name] = "<dynamic>"
    gpu_indices = [
        value
        for name, value in constants.items()
        if name.endswith("_GPU_INDICES") and isinstance(value, list)
    ]
    return {
        "constants": dict(sorted(constants.items())),
        "dual_gpu": any(len(value) > 1 for value in gpu_indices),
        "isolated_runtime": bool(
            any(
                name.endswith(
                    (
                        "_ISOLATED_LLAMA_RUNTIME_DIR_NAME",
                        "_LLAMACPP_RUNTIME_DIR_NAME",
                    )
                )
                for name in constants
            )
        ),
        "mtp_draft": "--spec-type" in text and "draft-mtp" in text,
        "background_prewarm": "def prewarm_local_model" in text,
        "sharded_model": any(
            name.endswith(("_GGUF_SHARDS", "_GGUF_PARTS")) for name in constants
        ),
    }


def profile_guard_fields(text: str) -> dict[str, Any]:
    return {
        "runtime_contract": runtime_contract_from_text(text),
        "intentional_residual_sha256": sha256_text(_intentional_variant_residual(text)),
    }


def capture_template(reference: Path) -> dict[str, Any]:
    text = read_variant_source(reference)
    spans = node_spans(text)
    embedded_source, _ = embedded_base_source(text)
    embedded_spans = node_spans(embedded_source)
    missing = [name for name in COMMON_FUNCTIONS if name not in spans]
    if missing:
        raise RuntimeError(f"Reference is missing common functions: {missing}")
    missing_embedded = [
        name for name in EMBEDDED_COMMON_FUNCTIONS if name not in embedded_spans
    ]
    if missing_embedded:
        raise RuntimeError(
            f"Reference is missing embedded common functions: {missing_embedded}"
        )
    template = {
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "reference_filename": reference.name,
        "universal_modules": {key: value for key, value in universal_module_sources().items()
                              if text.count(value) == 1},
        "runtime_root_layout_guard": RUNTIME_ROOT_LAYOUT_GUARD,
        "runtime_root_transport_probe": RUNTIME_ROOT_TRANSPORT_PROBE,
        "effort_budget_section": delimited_section(
            text,
            EFFORT_BUDGET_START,
            EFFORT_BUDGET_END,
        ),
        "sampling_section": delimited_section(
            text,
            SAMPLING_START,
            SAMPLING_END,
        ),
        "semantic_section": delimited_section(text, SEMANTIC_START, SEMANTIC_END),
        "harness_section": delimited_section(text, HARNESS_START, HARNESS_END),
        "mutation_tools_section": delimited_section(
            text, MUTATION_TOOLS_START, MUTATION_TOOLS_END,
        ),
        "simple_prompt_block": simple_prompt_block(text),
        "functions": {name: spans[name].text for name in COMMON_FUNCTIONS},
        "embedded_functions": {
            name: embedded_spans[name].text for name in EMBEDDED_COMMON_FUNCTIONS
        },
    }
    template["hashes"] = {
        "effort_budget_section": sha256_text(template["effort_budget_section"]),
        "sampling_section": sha256_text(template["sampling_section"]),
        "semantic_section": sha256_text(template["semantic_section"]),
        "harness_section": sha256_text(template["harness_section"]),
        "mutation_tools_section": sha256_text(template["mutation_tools_section"]),
        "simple_prompt_block": sha256_text(template["simple_prompt_block"]),
        "functions": {
            name: sha256_text(source)
            for name, source in template["functions"].items()
        },
        "embedded_functions": {
            name: sha256_text(source)
            for name, source in template["embedded_functions"].items()
        },
    }
    write_with_backup(
        TEMPLATE_PATH,
        json.dumps(template, indent=2, ensure_ascii=True) + "\n",
        f"Recaptured the common wrapper template from {reference.name} "
        "via qubitz_variant_builder.py --capture-template, after that backup was created.",
    )
    return template


def backup_file(path: Path, description: str) -> Path:
    """Copy path into ./backup as <name>.<NNN>.bak and log the pending change.

    The number is the lowest unused one for that file, so existing backups are
    never overwritten and every write leaves a rollback point behind. Callers
    must invoke this only when a write is actually going to happen, otherwise a
    no-op run would litter the backup directory.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    number = 1
    while (BACKUP_DIR / f"{path.name}.{number:03d}.bak").exists():
        number += 1
    backup_path = BACKUP_DIR / f"{path.name}.{number:03d}.bak"
    backup_path.write_bytes(path.read_bytes())

    entry = f"backup\\{backup_path.name} | {path.name} | {description}\n"
    with CHANGELOG_PATH.open("a", encoding="utf-8", newline="") as handle:
        handle.write(entry)
    return backup_path


def write_with_backup(path: Path, text: str, description: str, *, newline: str | None = None) -> bool:
    """Write text to path, backing up any existing file whose content differs.

    Returns False without touching the file when the content is unchanged, so
    repeated capture runs stay idempotent and do not accumulate backups.
    """
    if path.is_file():
        if path.read_text(encoding="utf-8") == text:
            return False
        backup_file(path, description)
    path.write_text(text, encoding="utf-8", newline=newline)
    return True


def replace_delimited(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
) -> str:
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return f"{text[:start]}{replacement}{text[end:]}"


def replace_simple_prompt_block(text: str, replacement: str) -> str:
    spans = node_spans(text)
    system_prompt = spans[ENHANCED_SYSTEM_PROMPT]
    local_start = system_prompt.text.index(SIMPLE_BLOCK_START)
    local_end = system_prompt.text.index(SIMPLE_BLOCK_END, local_start)
    start = system_prompt.start + local_start
    end = system_prompt.start + local_end
    return f"{text[:start]}{replacement}{text[end:]}"


def replace_function(text: str, qualname: str, replacement: str) -> str:
    spans = node_spans(text)
    span = spans.get(qualname)
    if span is None:
        if qualname == "_install_agent_tool_hardening.HardenedAgentRunner._try_direct_existing_script_completion":
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._run_existing_script_preflight"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._call_broker_bound_patch_tool":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost.call_tool"]
        elif qualname in {"_mcp_result_has_error", "_requested_test_entrypoints", "_requested_mcp_tool_names", "_record_direct_entrypoint_execution", "_actionable_committed_pytest_failure"}:
            anchor = spans["_run_inline_browser_open"]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._prioritize_tools_for_prompt":
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._filter_tools_by_intent"]
        elif qualname == "_install_optional_mcp_capabilities":
            anchor = spans["_install_gpu_thermal_gate"]
        elif qualname == "_resolve_unique_workspace_file_by_basename":
            anchor = spans["LocalMCPServerManager"]
        elif qualname == EMBEDDED_HARNESS_SOURCE_QUALNAME:
            anchor = spans[EMBEDDED_LOADER_QUALNAME]
        elif qualname == TASK_INTENT_QUALNAME:
            anchor = spans[TASK_GUIDANCE_QUALNAME]
        elif qualname == ROUTE_DECISION_QUALNAME:
            anchor = spans[TOOL_DESCRIPTION_QUALNAME]
        elif qualname == TRANSACTION_PROPOSAL_QUALNAME:
            anchor = spans[NO_PROGRESS_CHOICE_QUALNAME]
        elif qualname in {
            EXISTING_TEST_TARGET_QUALNAME,
            COMMITTED_TRANSACTION_VERIFY_QUALNAME,
        } or qualname == DOCX_EDIT_RECOVERY_QUALNAME:
            anchor = spans[TRANSACTION_PROPOSAL_QUALNAME]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._wrapper_saved_contract_snapshots":
            anchor = spans[MCP_LIFECYCLE_RECOVERY_QUALNAME]
        elif qualname == MCP_LIFECYCLE_RECOVERY_QUALNAME:
            anchor = spans[
                "_install_agent_tool_hardening.HardenedAgentRunner._tool_description"
            ]
        elif qualname == NO_PROGRESS_CHOICE_QUALNAME:
            anchor = spans[
                "_install_agent_tool_hardening.HardenedAgentRunner._filter_tools_by_intent"
            ]
        elif qualname == NAMED_SOURCE_PREFETCH_QUALNAME:
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._run_async"]
        elif qualname == RESEARCH_RESPONSE_URLS_QUALNAME:
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._social_fast_path_response"]
        elif qualname in {
            SOCIAL_FAST_PATH_QUALNAME,
            "_install_agent_tool_hardening.HardenedAgentRunner._apply_side_notes",
        }:
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._run_async"]
        elif qualname == PROTECTED_HARNESS_QUALNAME or qualname in {SOURCE_EVIDENCE_QUALNAME, TRANSACTION_RESET_QUALNAME, "_ToolPermissionBroker.bind_captured_sources"}:
            anchor = spans[TOOL_AUTHORIZE_QUALNAME]
        elif qualname == "_install_gui_run_controls":
            anchor = spans["_install_gpu_thermal_gate"]
        elif qualname == "_patch_cuda_retrieval_runtime" or qualname == "_install_pinned_llamacpp_runtime":
            anchor = spans["_patch_llamacpp_launch_fit"]
        elif qualname == "_install_daily_memory_store":
            anchor = spans["_install_agent_tool_hardening"]
        elif qualname == "_check_graphical_html_visibility":
            anchor = spans["_prompt_is_browser_only_url_action"]
        elif qualname in ENHANCED_GUI_NEW_COPY_METHODS:
            anchor = spans[ENHANCED_GUI_COPY_HANDLER]
        elif qualname in {
            "_sampling_state_snapshot",
            "_restore_sampling_state",
        }:
            anchor = spans["_sampling_profile_status"]
        elif qualname in {
            "_project_environment_candidates",
            "_explicit_prompt_environment",
            "_probe_python_environment",
            "_inspect_project_python_environment",
        }:
            anchor = spans["_preferred_project_linux_python"]
        elif qualname in {
            "_structured_text_edit_error",
            "_decode_text_preserving_encoding",
            "_encode_text_preserving_encoding",
            "_extract_explicit_quoted_edit_target",
        }:
            anchor = spans["_binary_file_notice"]
        elif qualname in {
            "LocalMCPServerManager._probe_mcp_python",
            "LocalMCPServerManager._resolve_mcp_python",
        }:
            anchor = spans["LocalMCPServerManager._normalize_env"]
        elif qualname in {
            "_build_local_mcp_server._relative_exclusion_parts",
            "_build_local_mcp_server._is_excluded_candidate",
        }:
            anchor = spans["_build_local_mcp_server.workspace_summary"]
        elif qualname == DOCX_PATCH_QUALNAME:
            anchor = spans[TEXT_PATCH_QUALNAME]
        elif qualname == "_build_local_mcp_server.inspect_python_environment":
            anchor = spans["_build_local_mcp_server.install_python_package"]
        elif qualname in {
            "_user_context_aliases",
            "_verified_created_alias_paths",
            "_verified_recent_alias_paths",
            "_resolve_user_context_aliases",
            "_expand_alias_tool_paths",
        }:
            anchor = spans["_explicit_file_reference_tokens"]
        elif qualname == "_unexecuted_tool_call_name":
            anchor = spans["_diagnostic_failure_feedback"]
        elif qualname == "_verified_workspace_scalar_answer":
            anchor = spans["_verified_source_receipt_answer"]
        elif qualname == WRAPPER_STOP_QUALNAME:
            anchor = spans[TRANSACTION_RECOVERY_REQUEST_QUALNAME]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._request_mcp_recovery":
            anchor = spans[
                "_install_agent_tool_hardening.HardenedAgentRunner._request_verified_completion"
            ]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._request_verified_completion":
            anchor = spans[WRAPPER_STOP_QUALNAME]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._format_completion_telemetry":
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner.run_sync"]
        elif qualname == "_ToolPermissionBroker.register_permission_denial":
            anchor = spans["_ToolPermissionBroker._track_transaction_recovery_progress"]
        elif qualname == "_ToolPermissionBroker._invalid_error_class_key":
            anchor = spans["_ToolPermissionBroker.register_invalid_call"]
        elif qualname in {
            "_ToolPermissionBroker._result_fingerprint",
            "_ToolPermissionBroker._mcp_invocation_key",
            "_ToolPermissionBroker.reserve_mcp_invocation",
            "_ToolPermissionBroker.bind_mcp_action_intent",
            "_ToolPermissionBroker.preserve_mcp_call_arguments",
            "_ToolPermissionBroker._mcp_schema_for_call",
            "_ToolPermissionBroker._explicit_mcp_arguments_satisfied",
        }:
            anchor = spans["_ToolPermissionBroker._references_protected_harness"]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._verified_mcp_result_answer":
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner._run_async"]
        elif qualname == "_ToolPermissionBroker.progress_token":
            anchor = spans["_ToolPermissionBroker._transaction_progress_token"]
        elif qualname == "_ToolPermissionBroker.bind_follow_up_repair":
            anchor = spans["_ToolPermissionBroker.end_turn"]
        elif qualname in {"_SpreadsheetCellEdit", "_NewTextFile"}:
            anchor = spans["_build_local_mcp_server"]
        elif qualname == "_build_local_mcp_server.write_files":
            anchor = spans["_build_local_mcp_server.read_file_snapshot"]
        elif qualname in {
            "_build_local_mcp_server.apply_notebook_text_patch",
            "_build_local_mcp_server.apply_spreadsheet_cell_patch",
        }:
            anchor = spans["_build_local_mcp_server.apply_text_patch"]
        elif qualname == "_ToolPermissionBroker.stage_text_patch_arguments":
            anchor = spans["_ToolPermissionBroker.semantic_argument_error"]
        elif qualname == "_ToolPermissionBroker._register_mcp_launch_result":
            anchor = spans["_ToolPermissionBroker.record_result"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._success_result":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost.call_tool"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._tool_call_key":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost.list_tools"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._parallel_read_only_tool":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost._tool_call_key"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._consume_parallel_read_only_batch":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost._parallel_read_only_tool"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._resolve_workflow_value":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost._consume_parallel_read_only_batch"]
        elif qualname == "_install_agent_tool_hardening.HardenedMCPHost._execute_workflow":
            anchor = spans["_install_agent_tool_hardening.HardenedMCPHost._resolve_workflow_value"]
        elif qualname == "_install_agent_tool_hardening.HardenedAgentRunner._raise_if_cancelled":
            anchor = spans["_install_agent_tool_hardening.HardenedAgentRunner.request_cancel"]
        elif qualname == "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._effort_step_budget_profile":
            anchor = spans[
                "LocalOnlyApp._build_agent_runner_class.EnhancedAgentRunner._effective_max_steps"
            ]
        elif qualname in {
            "_install_agent_tool_hardening.HardenedAgentRunner._read_only_external_path_preflight",
            "_install_agent_tool_hardening.HardenedAgentRunner._empty_workspace_listing_preflight",
            "_install_agent_tool_hardening.HardenedAgentRunner._dependency_install_preflight",
        } or qualname == NAMED_FILE_PREFLIGHT_QUALNAME:
            anchor = spans[TASK_GUIDANCE_QUALNAME]
        elif qualname == "_requests_test_execution":
            anchor = spans["_requested_mcp_tool_names"]
        elif qualname == SEARCH_WEB_QUALNAME:
            anchor = spans[FETCH_URL_QUALNAME]
        elif qualname == GUI_NETWORK_MODE_QUALNAME:
            anchor = spans[GUI_SYNC_RUNTIME_QUALNAME]
        elif qualname == GUI_VARIANT_SELECTOR_QUALNAME:
            anchor = spans[GUI_NETWORK_MODE_QUALNAME]
        else:
            raise RuntimeError(f"Missing required common function {qualname}")
        separator = "\n\n" if qualname in {"_SpreadsheetCellEdit", "_NewTextFile"} else "\n"
        return f"{text[:anchor.start]}{replacement}{separator}{text[anchor.start:]}"
    return f"{text[:span.start]}{replacement}{text[span.end:]}"


def replace_common_functions(text: str, replacements: dict[str, str]) -> str:
    """Replace non-overlapping existing common functions with one AST parse."""
    spans = node_spans(text)
    missing = [qualname for qualname in replacements if qualname not in spans]
    for qualname in missing:
        text = replace_function(text, qualname, replacements[qualname])
    if missing:
        spans = node_spans(text)
    ordered = sorted(
        ((spans[qualname], replacement) for qualname, replacement in replacements.items()),
        key=lambda item: item[0].start,
        reverse=True,
    )
    for span, replacement in ordered:
        text = f"{text[:span.start]}{replacement}{text[span.end:]}"
    return text


def replace_embedded_functions(text: str, replacements: dict[str, str]) -> str:
    source, (start, end) = embedded_base_source(text)
    spans = node_spans(source)
    missing = [name for name in replacements if name not in spans]
    if missing:
        raise RuntimeError(f"Missing required embedded common functions: {missing}")
    edits = sorted(
        (
            (spans[name].start, spans[name].end, replacement)
            for name, replacement in replacements.items()
        ),
        reverse=True,
    )
    for edit_start, edit_end, replacement in edits:
        source = f"{source[:edit_start]}{replacement}{source[edit_end:]}"
    ast.parse(source)
    updated = f"{text[:start]}{source!r}{text[end:]}"
    ast.parse(updated)
    return updated


def sync_variant(path: Path, template: dict[str, Any]) -> bool:
    original = read_variant_source(path)
    intentional_residual = _intentional_variant_residual(original)
    text = replace_delimited(
        original,
        EFFORT_BUDGET_START,
        EFFORT_BUDGET_END,
        template["effort_budget_section"],
    )
    text = replace_delimited(
        text,
        SAMPLING_START,
        SAMPLING_END,
        template["sampling_section"],
    )
    text = replace_delimited(
        text,
        SEMANTIC_START,
        SEMANTIC_END,
        template["semantic_section"],
    )
    text = replace_delimited(
        text,
        HARNESS_START,
        HARNESS_END,
        template["harness_section"],
    )
    text = replace_delimited(
        text,
        MUTATION_TOOLS_START,
        MUTATION_TOOLS_END,
        template["mutation_tools_section"],
    )
    text = replace_simple_prompt_block(text, template["simple_prompt_block"])
    text = replace_common_functions(text, template["functions"])
    text = replace_embedded_functions(text, template["embedded_functions"])
    legacy_span = node_spans(text).get(LEGACY_LOCAL_SPREADSHEET_EDIT_QUALNAME)
    if legacy_span is not None:
        text = f"{text[:legacy_span.start]}{text[legacy_span.end:]}"
    ast.parse(text)
    if _intentional_variant_residual(text) != intentional_residual:
        raise RuntimeError(
            f"{path.name}: synchronization would change code outside declared shared regions; refusing to write"
        )
    if text == original:
        return False
    if launcher_spec(path.read_text(encoding="utf-8")) is not None:
        return replace_variant_source(path, text)
    backup_file(
        path,
        "Synchronized the common wrapper sections and shared functions from the "
        "reference variant via qubitz_variant_builder.py --sync, after that backup was created.",
    )
    path.write_text(text, encoding="utf-8", newline="\n")
    return True


def load_template() -> dict[str, Any]:
    if not TEMPLATE_PATH.is_file():
        raise FileNotFoundError(
            f"{TEMPLATE_PATH.name} is missing. Run --capture-template first."
        )
    template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
    if template.get("schema_version") != TEMPLATE_SCHEMA_VERSION:
        raise RuntimeError(
            f"{TEMPLATE_PATH.name} schema {template.get('schema_version')!r} is stale; "
            "recapture it from the verified reference variant."
        )
    return template


def import_variant(path: Path) -> Any:
    module_name = f"_qubitz_profile_{hashlib.sha256(path.name.encode()).hexdigest()[:12]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    except Exception:
        sys.modules.pop(module_name, None)
        raise


def effective_profile(path: Path) -> dict[str, Any]:
    source = read_variant_source(path)
    return profile_from_text(path, source)


def profile_from_text(path: Path, text: str) -> dict[str, Any]:
    source, _ = embedded_base_source(text)
    constants = module_constant_values(source, set(PROFILE_CONSTANTS.values()))
    return {
        "filename": path.name,
        **{
            profile_key: constants[constant_name]
            for profile_key, constant_name in PROFILE_CONSTANTS.items()
        },
        "background_prewarm": "def prewarm_local_model" in text,
        "isolated_llamacpp_runtime": bool(
            "NORTH_LLAMACPP_RUNTIME_DIR_NAME" in text
            or "ISOLATED_LLAMA" in text
            or "isolated llama.cpp runtime" in text.lower()
        ),
        **profile_guard_fields(text),
    }


def apply_profiles(paths: list[Path]) -> list[str]:
    if not PROFILES_PATH.is_file():
        raise FileNotFoundError(f"{PROFILES_PATH.name} is missing.")
    payload = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    expected = {item["filename"]: item for item in payload.get("variants", [])}
    path_names = {path.name for path in paths}
    missing = sorted(path_names - set(expected))
    stale = sorted(set(expected) - path_names)
    if missing or stale:
        raise RuntimeError(f"Profile/path mismatch: missing={missing}, stale={stale}")

    candidates: list[tuple[Path, str]] = []
    for path in paths:
        original = read_variant_source(path)
        source, (start, end) = embedded_base_source(original)
        profile = expected[path.name]
        replacements = {
            constant_name: profile[profile_key]
            for profile_key, constant_name in PROFILE_CONSTANTS.items()
        }
        assigned_names = {
            target.id
            for node in ast.parse(source).body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (
                node.targets if isinstance(node, ast.Assign) else [node.target]
            )
            if isinstance(target, ast.Name)
        }
        for profile_key, constant_names in PROFILE_COMPATIBILITY_CONSTANTS.items():
            replacements.update(
                {
                    constant_name: profile[profile_key]
                    for constant_name in constant_names
                    if constant_name in assigned_names
                }
            )
        updated_source = replace_module_constants(source, replacements)
        updated = f"{original[:start]}{updated_source!r}{original[end:]}"
        ast.parse(updated)
        actual = profile_from_text(path, updated)
        if actual != profile:
            mismatches = {
                key: {"expected": profile.get(key), "actual": actual.get(key)}
                for key in (*PROFILE_CONSTANTS, *STRUCTURAL_PROFILE_KEYS)
                if profile.get(key) != actual.get(key)
            }
            raise RuntimeError(f"{path.name}: profile application mismatch: {mismatches}")
        if updated != original:
            candidates.append((path, updated))

    changed: list[str] = []
    for path, updated in candidates:
        if launcher_spec(path.read_text(encoding="utf-8")) is not None:
            replace_variant_source(path, updated)
            changed.append(path.name)
            continue
        backup_file(
            path,
            "Applied the declarative scalar runtime profile via "
            "qubitz_variant_builder.py --apply-profiles, after that backup was created.",
        )
        path.write_text(updated, encoding="utf-8", newline="\n")
        changed.append(path.name)
    return changed


def capture_profiles(paths: list[Path]) -> list[dict[str, Any]]:
    profiles = [effective_profile(path) for path in paths]
    write_with_backup(
        PROFILES_PATH,
        json.dumps(
            {"schema_version": PROFILE_SCHEMA_VERSION, "variants": profiles},
            indent=2,
            ensure_ascii=True,
        )
        + "\n",
        "Recaptured the declarative variant profiles via "
        "qubitz_variant_builder.py --capture-profiles, after that backup was created.",
    )
    return profiles


def check_template(paths: list[Path], template: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        text = read_variant_source(path)
        try:
            for key, module in template.get("universal_modules", {}).items():
                if text.count(module) != 1:
                    issues.append(f"{path.name}: universal module drift: {key}")
            constructor = node_spans(text).get("LocalOnlyApp.__init__")
            if (template.get("runtime_root_layout_guard") != RUNTIME_ROOT_LAYOUT_GUARD
                    or constructor is None or constructor.text.count(RUNTIME_ROOT_LAYOUT_GUARD) != 1):
                issues.append(f"{path.name}: shared runtime-root layout drift")
            transport_probe = node_spans(text).get(TRANSPORT_PROBE_QUALNAME)
            if (template.get("runtime_root_transport_probe") != RUNTIME_ROOT_TRANSPORT_PROBE
                    or transport_probe is None or transport_probe.text.count(RUNTIME_ROOT_TRANSPORT_PROBE) != 1
                    or LEGACY_CWD_TRANSPORT_PROBE in transport_probe.text):
                issues.append(f"{path.name}: shared runtime-root transport drift")
            if delimited_section(text, EFFORT_BUDGET_START, EFFORT_BUDGET_END) != template["effort_budget_section"]:
                issues.append(f"{path.name}: effort budget section drift")
            if delimited_section(text, SAMPLING_START, SAMPLING_END) != template["sampling_section"]:
                issues.append(f"{path.name}: sampling section drift")
            if delimited_section(text, SEMANTIC_START, SEMANTIC_END) != template["semantic_section"]:
                issues.append(f"{path.name}: semantic section drift")
            if delimited_section(text, HARNESS_START, HARNESS_END) != template["harness_section"]:
                issues.append(f"{path.name}: harness section drift")
            if delimited_section(text, MUTATION_TOOLS_START, MUTATION_TOOLS_END) != template["mutation_tools_section"]:
                issues.append(f"{path.name}: mutation tool classification drift")
            if simple_prompt_block(text) != template["simple_prompt_block"]:
                issues.append(f"{path.name}: simple prompt drift")
            spans = node_spans(text)
            for qualname, expected in template["functions"].items():
                actual = spans.get(qualname)
                if actual is None:
                    issues.append(f"{path.name}: missing {qualname}")
                elif actual.text != expected:
                    issues.append(f"{path.name}: drift in {qualname}")
            embedded_source, _ = embedded_base_source(text)
            embedded_spans = node_spans(embedded_source)
            for qualname, expected in template["embedded_functions"].items():
                actual = embedded_spans.get(qualname)
                if actual is None:
                    issues.append(f"{path.name}: missing embedded {qualname}")
                elif actual.text != expected:
                    issues.append(f"{path.name}: drift in embedded {qualname}")
        except Exception as exc:
            issues.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return issues


def check_profiles(paths: list[Path]) -> list[str]:
    if not PROFILES_PATH.is_file():
        return [f"{PROFILES_PATH.name} is missing"]
    expected_payload = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    if expected_payload.get("schema_version") != PROFILE_SCHEMA_VERSION:
        return [
            f"{PROFILES_PATH.name} schema {expected_payload.get('schema_version')!r} is stale; "
            "recapture declarative profiles"
        ]
    expected = {
        item["filename"]: item
        for item in expected_payload.get("variants", [])
        if user_variant_allowed(item["filename"]) or any(path.name == item["filename"] for path in paths)
    }
    issues: list[str] = []
    for path in paths:
        actual = effective_profile(path)
        saved = expected.get(path.name)
        if saved is None:
            issues.append(f"{path.name}: missing declarative profile")
        elif actual != saved:
            issues.append(f"{path.name}: effective profile differs from declarative profile")
    extra = sorted(set(expected) - {path.name for path in paths})
    issues.extend(f"{name}: stale declarative profile" for name in extra)
    return issues


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Development-only Qubitz profile and common-wrapper maintenance for shared-runtime variants."
        )
    )
    parser.add_argument(
        "--reference",
        default=DEFAULT_REFERENCE,
        help="Variant whose effective source is used to capture the common wrapper template.",
    )
    parser.add_argument("--capture-template", action="store_true")
    parser.add_argument("--sync", action="store_true")
    parser.add_argument(
        "--apply-profiles",
        action="store_true",
        help=(
            "Apply declarative scalar runtime settings to variant-specific source; "
            "structural profile fields remain validation-only."
        ),
    )
    parser.add_argument("--capture-profiles", action="store_true")
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    paths = all_variant_paths(ROOT) if (args.sync or args.apply_profiles or args.capture_profiles) else variant_paths()
    if not paths:
        raise RuntimeError("No Qubitz variants were found.")
    if args.capture_template:
        reference = variant_path(args.reference, ROOT)
        capture_template(reference)
        print(f"Captured common wrapper template from {reference.name}.")
    template = load_template() if (args.sync or args.check) else None
    if args.sync:
        profile_issues = check_profiles(paths)
        if profile_issues:
            for issue in profile_issues:
                print(f"Pre-sync profile check: {issue}", file=sys.stderr)
            return 1
        print(f"Validated {len(paths)} intentional declarative and structural variant profiles before synchronization.")
        changed = [path.name for path in paths if sync_variant(path, template)]
        print(f"Synchronized {len(changed)} variant(s).")
        for name in changed:
            print(name)
        post_sync_issues = check_template(paths, template)
        post_sync_issues.extend(check_profiles(paths))
        if post_sync_issues:
            for issue in post_sync_issues:
                print(f"Post-sync preservation check: {issue}", file=sys.stderr)
            return 1
        print(f"Revalidated shared parity and all {len(paths)} intentional variant profiles after synchronization.")
    if args.apply_profiles:
        changed = apply_profiles(paths)
        print(f"Applied declarative profiles to {len(changed)} variant(s).")
        for name in changed:
            print(name)
    if args.capture_profiles:
        profiles = capture_profiles(paths)
        print(f"Captured {len(profiles)} declarative variant profile(s).")
    if args.check:
        issues = check_template(paths, template)
        issues.extend(check_profiles(paths))
        if issues:
            for issue in issues:
                print(issue, file=sys.stderr)
            return 1
        print(f"Validated common wrapper parity and {len(paths)} declarative profiles.")
    if not any(
        (
            args.capture_template,
            args.sync,
            args.apply_profiles,
            args.capture_profiles,
            args.check,
        )
    ):
        print("No action selected. Use --help.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
