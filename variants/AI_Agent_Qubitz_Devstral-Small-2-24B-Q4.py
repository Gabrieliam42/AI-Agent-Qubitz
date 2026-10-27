"""Variant-specific configuration and source for the shared Qubitz runtime."""
from __future__ import annotations

import sys as _qubitz_bootstrap_sys
from pathlib import Path as _QubitzBootstrapPath

_qubitz_bootstrap_root = _QubitzBootstrapPath(__file__).resolve().parent
if _qubitz_bootstrap_root.name == 'variants':
    _qubitz_bootstrap_root = _qubitz_bootstrap_root.parent
if str(_qubitz_bootstrap_root) not in _qubitz_bootstrap_sys.path:
    _qubitz_bootstrap_sys.path.insert(0, str(_qubitz_bootstrap_root))

from runtime import initialize_variant as _initialize_variant

_QUBITZ_VARIANT_SPEC = {'schema_version': 2,
 'filename': 'AI_Agent_Qubitz_Devstral-Small-2-24B-Q4.py',
 'wrapper': {'000': '',
             '001': '',
             '002': '',
             '003': 'THINKING_EFFORT_STEP_CAPS: dict[str, int] = {\n'
                    '    "low": 8,\n'
                    '    "medium": 16,\n'
                    '    "high": 48,\n'
                    '    "xhigh": 128,\n'
                    '}\n'
                    'DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS = 300\n'
                    'DIRECT_SCRIPT_COMPLETION_MAX_URLS = 64\n',
             '004': 'SCRIPT_BROWSER_GLOB_PATTERNS = ("open*.ps1", "open*.bat", "open*.cmd")\n',
             '005': 'SCRIPT_PREFLIGHT_MAX_URLS = DIRECT_SCRIPT_COMPLETION_MAX_URLS\n',
             '006': '    if '
                    're.search(r"\\b(build|compile|test|lint|run|start|serve|deploy|fetch|get|list|open|read|inspect|explain|summarize|find|search|convert|train|evaluate|benchmark|install|update|fix|create|delete|rename)\\b", '
                    'lowered):\n',
             '007': '    return any(marker in lowered for marker in ("produce one concrete verified project result", '
                    '"produce a project result", "use the active workspace", "use this project", "use the project", '
                    '"use this workspace", "use the workspace"))\n',
             '008': '    for pattern in SCRIPT_BROWSER_GLOB_PATTERNS:\n',
             '009': '        if len(urls) >= DIRECT_SCRIPT_COMPLETION_MAX_URLS:\n',
             '010': '# <QUBITZ_SHARED_REFERENCE:wrapper:8e2b980c6e721daa>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:8fd25370bafe5829>\n'
                    '\n',
             '011': '',
             '012': '',
             '013': '',
             '014': '',
             '015': '                "artifacts", "data", "entries", "files", "items", "opened_urls",\n'
                    '                "outputs", "records", "results", "rows", "urls",\n',
             '016': 'def _build_direct_continuation_context(\n'
                    '    base: Any,\n'
                    '    workspace: Path,\n'
                    '    *,\n'
                    '    entrypoint_label: str,\n'
                    '    return_code: int,\n'
                    '    target_count: int,\n'
                    '    verified_count: int,\n'
                    '    rows: Sequence[dict[str, Any]],\n'
                    '    urls: Sequence[str],\n'
                    '    stdout_text: str,\n'
                    '    stderr_text: str,\n'
                    '    artifacts: Sequence[tuple[Path, str]],\n'
                    ') -> str:\n'
                    '    parts = [\n'
                    '        "Wrapper preflight direct existing-entrypoint execution already ran before the model '
                    'loop.",\n'
                    '        f"Entrypoint: {entrypoint_label}",\n'
                    '        f"Exit code: {return_code}",\n'
                    '        f"Requested result count: {target_count}",\n'
                    '        f"Verified direct result count: {verified_count}",\n'
                    '        f"Missing result count: {max(0, target_count - verified_count)}",\n'
                    '    ]\n'
                    '    if rows:\n'
                    '        parts.append("Verified structured rows:\\n" + "\\n".join(\n'
                    '            json.dumps(row, ensure_ascii=True, sort_keys=True, default=str)\n'
                    '            for row in rows[:SCRIPT_PREFLIGHT_MAX_URLS]\n'
                    '        ))\n'
                    '    elif urls:\n'
                    '        parts.append("Verified distinct external URLs:\\n" + '
                    '"\\n".join(urls[:SCRIPT_PREFLIGHT_MAX_URLS]))\n'
                    '    if stdout_text:\n'
                    '        parts.append("Script stdout excerpt:\\n" + _shorten(stdout_text, '
                    'SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS // 3))\n'
                    '    if stderr_text:\n'
                    '        parts.append("Script stderr excerpt:\\n" + _shorten(stderr_text, '
                    'SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS // 3))\n'
                    '    for artifact_path, artifact_text in artifacts[:4]:\n'
                    '        artifact_label = base.relative_path(artifact_path, workspace)\n'
                    '        parts.append(\n'
                    '            (\n'
                    '                f"Changed output artifact {artifact_label} excerpt:\\n"\n'
                    '                + _shorten(artifact_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS // 4)\n'
                    '            )\n'
                    '            if artifact_text\n'
                    '            else f"Changed non-text or oversized output artifact: {artifact_label}"\n'
                    '        )\n'
                    '    parts.append(\n'
                    '        "Continue only the missing postconditions from these exact verified preflight results. '
                    'Preserve valid existing "\n'
                    '        "results, avoid broad repository retrieval, and do not rerun the same entrypoint unless '
                    'new evidence shows that "\n'
                    '        "a rerun can produce the missing result. Do not claim completion until every requested '
                    'quantity and side effect is verified."\n'
                    '    )\n'
                    '    return _shorten("\\n\\n".join(parts), SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)\n'
                    '\n'
                    '\n',
             '017': '',
             '018': '',
             '019': '',
             '020': '',
             '021': '',
             '022': '',
             '023': '',
             '024': '',
             '025': '# <QUBITZ_SHARED_REFERENCE:wrapper:683850aa594ab1e9>\n\n\n',
             '026': '\n',
             '027': '\n',
             '028': '',
             '029': '',
             '030': '',
             '031': '',
             '032': '',
             '033': '',
             '034': '',
             '035': '',
             '036': '    skills = base.SkillRegistry(runtime_workspace)\n',
             '037': '                "skills_root": base.relative_path(skills.skills_root, runtime_workspace),\n'
                    '                "skill_count": skills.count(),\n'
                    '                "skill_warnings": skills.warnings,\n',
             '038': '    @server.resource("skills://index")\n'
                    '    def skills_index() -> str:\n'
                    '        return json.dumps(\n'
                    '            {\n'
                    '                "skills_root": base.relative_path(skills.skills_root, runtime_workspace),\n'
                    '                "count": skills.count(),\n'
                    '                "warnings": skills.warnings,\n'
                    '                "skills": skills.list_summaries(),\n'
                    '            },\n'
                    '            ensure_ascii=True,\n'
                    '            indent=2,\n'
                    '        )\n',
             '039': '    @server.resource("plugins://index")\n'
                    '    def plugins_index() -> str:\n'
                    '        return json.dumps({"plugins": plugin_registry.list_plugins()}, ensure_ascii=True, '
                    'indent=2)\n',
             '040': '    @server.tool(description="List runtime-root local skills discovered under .skills.")\n'
                    '    def list_skills() -> dict[str, Any]:\n'
                    '        return {\n'
                    '            "skills_root": base.relative_path(skills.skills_root, runtime_workspace),\n'
                    '            "count": skills.count(),\n'
                    '            "warnings": skills.warnings,\n'
                    '            "skills": skills.list_summaries(),\n'
                    '        }\n',
             '041': '    @server.tool(description="Read a runtime-root local skill\'s metadata and body.")\n'
                    '    def read_skill(skill_name: str) -> dict[str, Any]:\n'
                    '        skill = skills.get(skill_name)\n'
                    '        summary = skill.to_summary(runtime_workspace)\n'
                    '        summary["body"] = skill.body\n'
                    '        return summary\n',
             '042': '    @server.tool(description="Read a file or directory inside a runtime-root local skill root.")\n'
                    '    def read_skill_resource(skill_name: str, resource_path: str) -> dict[str, Any]:\n'
                    '        return skills.read_skill_resource(skill_name, resource_path)\n',
             '043': '    @server.tool(description="List local-only plugin manifests loaded from runtime and active '
                    'workspace .qubitz/plugins.")\n'
                    '    def list_local_plugins() -> dict[str, Any]:\n'
                    '        plugin_registry.reload()\n'
                    '        return {"plugins": plugin_registry.list_plugins()}\n',
             '044': '    @server.tool(description="Read one local-only plugin manifest by name.")\n'
                    '    def read_local_plugin(name: str) -> dict[str, Any]:\n'
                    '        plugin_registry.reload()\n'
                    '        return plugin_registry.read_plugin(name)\n',
             '045': '    @server.tool(description="Create a local sandbox for non-trivial changes or background '
                    'jobs.")\n'
                    '    def create_sandbox(label: str = "", mode: str = "auto") -> dict[str, Any]:\n'
                    '        return sandboxes.create(label=label, mode=mode)\n',
             '046': '            "Start a persistent local server process as a managed background job using a direct '
                    'project Python interpreter path. Do not use this for a session-scoped stdio MCP script passed to '
                    'list_project_mcp_tools or call_project_mcp_tool. "\n'
                    '            ""\n',
             '047': '            "List tools from a local MCP server. An explicit script or configuration '
                    'server_reference is opened as a session-scoped stdio process and closed automatically; it does '
                    'not require start_project_mcp_server. "\n',
             '048': '            "Invoke one named local MCP tool. An explicit script or configuration '
                    'server_reference is opened as a session-scoped stdio process and closed automatically; it does '
                    'not require start_project_mcp_server. "\n',
             '049': '    @server.tool(description="List local sandboxes stored under the runtime-root cache.")\n'
                    '    def list_sandboxes() -> dict[str, Any]:\n'
                    '        return {"sandboxes": sandboxes.list_sandboxes()}\n',
             '050': '    @server.tool(description="Write or overwrite a file inside a sandbox.")\n'
                    '    def sandbox_write_file(sandbox_id: str, path: str, content: str, make_parents: bool = True) '
                    '-> dict[str, Any]:\n'
                    '        return sandboxes.write_file(sandbox_id, path, content, make_parents)\n',
             '051': '    @server.tool(description="Replace exact text inside a file in a sandbox.")\n'
                    '    def sandbox_replace_text(\n'
                    '        sandbox_id: str,\n'
                    '        path: str,\n'
                    '        old_text: str,\n'
                    '        new_text: str,\n'
                    '        count: int = 0,\n'
                    '    ) -> dict[str, Any]:\n'
                    '        return sandboxes.replace_text(sandbox_id, path, old_text, new_text, count)\n',
             '052': '    @server.tool(description="Delete a path inside a sandbox.")\n'
                    '    def sandbox_delete_path(sandbox_id: str, path: str, recursive: bool = False) -> dict[str, '
                    'Any]:\n'
                    '        return sandboxes.delete_path(sandbox_id, path, recursive)\n',
             '053': '    @server.tool(description="Create a directory inside a sandbox.")\n'
                    '    def sandbox_make_directory(sandbox_id: str, path: str) -> dict[str, Any]:\n'
                    '        return sandboxes.make_directory(sandbox_id, path)\n',
             '054': '    @server.tool(description="Move or rename a path inside a sandbox.")\n'
                    '    def sandbox_move_path(sandbox_id: str, source: str, destination: str, overwrite: bool = '
                    'False) -> dict[str, Any]:\n'
                    '        return sandboxes.move_path(sandbox_id, source, destination, overwrite)\n',
             '055': '    @server.tool(description="Run a local shell command inside a sandbox.")\n'
                    '    def sandbox_run_command(sandbox_id: str, command: str, timeout_seconds: int = 120) -> '
                    'dict[str, Any]:\n'
                    '        return sandboxes.run_command(sandbox_id, command, timeout_seconds)\n',
             '056': '    @server.tool(description="Show a diff summary for a sandbox.")\n'
                    '    def sandbox_diff(sandbox_id: str, max_chars: int = LOCAL_ONLY_MAX_DIFF_CHARS) -> dict[str, '
                    'Any]:\n'
                    '        return sandboxes.diff(sandbox_id, max_chars)\n',
             '057': '    @server.tool(description="Copy changed files from a sandbox back into the active '
                    'workspace.")\n'
                    '    def sandbox_apply_back(sandbox_id: str, overwrite: bool = True) -> dict[str, Any]:\n'
                    '        return sandboxes.apply_back(sandbox_id, overwrite)\n',
             '058': '    @server.tool(description="Destroy a sandbox and remove its cached files.")\n'
                    '    def sandbox_destroy(sandbox_id: str) -> dict[str, Any]:\n'
                    '        return sandboxes.destroy(sandbox_id)\n',
             '059': '    @server.tool(description="List local background jobs started by the local-only wrapper.")\n'
                    '    def list_background_jobs() -> dict[str, Any]:\n'
                    '        return {"jobs": jobs.list_jobs()}\n',
             '060': '    @server.tool(description="Read one background job\'s metadata and log.")\n'
                    '    def read_background_job(job_id: str, max_chars: int = 12000) -> dict[str, Any]:\n'
                    '        return jobs.read_job(job_id, max_chars)\n',
             '061': '    @server.tool(description="Cancel a local background job by id.")\n'
                    '    def cancel_background_job(job_id: str) -> dict[str, Any]:\n'
                    '        return jobs.cancel_job(job_id)\n',
             '062': '\n\n# <QUBITZ_SHARED_REFERENCE:wrapper:4767f5f2420da5b1>\n',
             '063': '',
             '064': '',
             '065': '    if not hasattr(base, "_QUBITZ_ORIGINAL_WSL_WINDOWS_EXECUTABLE_INTEROP_AVAILABLE"):\n'
                    '        base._QUBITZ_ORIGINAL_WSL_WINDOWS_EXECUTABLE_INTEROP_AVAILABLE = getattr(\n'
                    '            base, "wsl_windows_executable_interop_available", None\n'
                    '        )\n',
             '066': '_EMBEDDED_BASE_SOURCE = __QUBITZ_EMBEDDED_SOURCE_SLOT__\n',
             '067': 'UNSLOTH_DEVSTRAL_MODEL_ALIAS = "unsloth/Devstral-Small-2-24B-Instruct-2512"\n'
                    'UNSLOTH_DEVSTRAL_GGUF_REPO = "unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF"\n'
                    'UNSLOTH_DEVSTRAL_GGUF_FILE = "Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"\n'
                    'UNSLOTH_DEVSTRAL_GGUF_LABEL = "Unsloth-Devstral-Small-2-24B-Q4_K_M"\n'
                    'UNSLOTH_DEVSTRAL_DEFAULT_NUM_CTX = 262144\n'
                    '\n'
                    'for source, target in (\n'
                    '    (\n'
                    '        \'DEFAULT_MODEL = "mistralai/Devstral-Small-2-24B-Instruct-2512"\',\n'
                    '        f\'DEFAULT_MODEL = "{UNSLOTH_DEVSTRAL_MODEL_ALIAS}"\',\n'
                    '    ),\n'
                    '    (\n'
                    "        'DEFAULT_HF_GGUF_REPO_ID = "
                    '"bartowski/mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF"\',\n'
                    '        f\'DEFAULT_HF_GGUF_REPO_ID = "{UNSLOTH_DEVSTRAL_GGUF_REPO}"\',\n'
                    '    ),\n'
                    '    (\n'
                    '        \'Q4_GGUF_MODEL_FILENAME = "mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"\',\n'
                    '        f\'Q4_GGUF_MODEL_FILENAME = "{UNSLOTH_DEVSTRAL_GGUF_FILE}"\',\n'
                    '    ),\n'
                    '    (\n'
                    '        \'DEFAULT_GGUF_MODEL_LABEL = "Devstral-Small-2-24B-Q4_K_M"\',\n'
                    '        f\'DEFAULT_GGUF_MODEL_LABEL = "{UNSLOTH_DEVSTRAL_GGUF_LABEL}"\',\n'
                    '    ),\n'
                    '    (\n'
                    "        'DEFAULT_GGUF_MODEL_FILENAME = "
                    '"mistralai_Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf"\',\n'
                    '        f\'DEFAULT_GGUF_MODEL_FILENAME = "{UNSLOTH_DEVSTRAL_GGUF_FILE}"\',\n'
                    '    ),\n'
                    '    (\n'
                    "        'DEFAULT_NUM_CTX = 202752',\n"
                    "        f'DEFAULT_NUM_CTX = {UNSLOTH_DEVSTRAL_DEFAULT_NUM_CTX}',\n"
                    '    ),\n'
                    '    (\n'
                    '        \'workspace / ".cache" / "models" / "bartowski" / '
                    '"mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF"\',\n'
                    '        \'workspace / ".cache" / "models" / "unsloth" / '
                    '"Devstral-Small-2-24B-Instruct-2512-GGUF"\',\n'
                    '    ),\n'
                    '):\n'
                    '    _EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(source, target)\n'
                    '\n',
             '068': '',
             '069': '',
             '070': ')\n',
             '071': '\n',
             '072': '',
             '073': '',
             '074': '',
             '075': '',
             '076': '',
             '077': '',
             '078': '',
             '079': '                if selected_route not in {"simple_answer", "direct_existing_entrypoint"}:\n',
             '080': '                if selected_route == "simple_answer":\n',
             '081': '                if selected_route == "direct_existing_entrypoint":\n',
             '082': '',
             '083': '            def _score_routes(self, features: dict[str, Any]) -> dict[str, int]:\n',
             '084': '                    "simple_answer": 0,\n'
                    '                    "direct_existing_entrypoint": 0,\n'
                    '                    "read_only_workspace": 0,\n'
                    '                    "retrieval_plus_model": 0,\n'
                    '                    "tool_loop": 0,\n'
                    '                    "ask_user_missing_info": 0,\n',
             '085': '                if features["simple_question_candidate"]:\n'
                    '                    scores["simple_answer"] += 30\n'
                    '                    scores["retrieval_plus_model"] -= 8\n'
                    '                    scores["tool_loop"] -= 10\n'
                    '                if features["explicit_existing_entrypoint"]:\n'
                    '                    scores["direct_existing_entrypoint"] += 28\n'
                    '                    scores["retrieval_plus_model"] -= 4\n'
                    '                    scores["tool_loop"] -= 6\n'
                    '                elif features["has_entrypoint_spec"]:\n'
                    '                    scores["read_only_workspace"] += 8 if features["read_intent"] else 0\n'
                    '                    scores["retrieval_plus_model"] += 8\n'
                    '                if features["read_intent"]:\n'
                    '                    scores["read_only_workspace"] += 10\n'
                    '                if features["file_token_count"]:\n'
                    '                    scores["read_only_workspace"] += 12\n'
                    '                    scores["retrieval_plus_model"] += 4\n'
                    '                if features["workspace_context_needed"]:\n'
                    '                    scores["read_only_workspace"] += 5\n'
                    '                    scores["retrieval_plus_model"] += 10\n'
                    '                    scores["simple_answer"] -= 12\n'
                    '                if features["edit_intent"]:\n'
                    '                    scores["retrieval_plus_model"] += 12\n'
                    '                    scores["tool_loop"] += 6\n'
                    '                    scores["simple_answer"] -= 20\n'
                    '                    scores["read_only_workspace"] -= 4\n'
                    '                if features["verify_intent"]:\n'
                    '                    scores["retrieval_plus_model"] += 10\n'
                    '                    scores["tool_loop"] += 8\n'
                    '                    scores["simple_answer"] -= 20\n'
                    '                if features["mentions_tools_or_mcp"]:\n'
                    '                    scores["tool_loop"] += 14\n'
                    '                    scores["simple_answer"] -= 12\n'
                    '                if features["powershell_or_side_effect"]:\n'
                    '                    scores["direct_existing_entrypoint"] += 12\n'
                    '                    scores["tool_loop"] += 18\n'
                    '                    scores["read_only_workspace"] -= 30\n'
                    '                if features["plugin_or_skill_request"]:\n'
                    '                    scores["tool_loop"] += 10\n'
                    '                    scores["simple_answer"] -= 8\n'
                    '                if features["line_count"] > 1 or features["prompt_length"] > 220:\n'
                    '                    scores["retrieval_plus_model"] += 4\n'
                    '                    scores["simple_answer"] -= 6\n'
                    '                if features["needs_missing_context"]:\n'
                    '                    scores["ask_user_missing_info"] += 18\n'
                    '                    scores["retrieval_plus_model"] -= 4\n',
             '086': '                    scores["retrieval_plus_model"] += 3\n',
             '087': '            def _decide_locked_route(self, prompt: str) -> tuple[str, str]:\n',
             '088': '                        item[0] == "simple_answer",\n'
                    '                        item[0] == "direct_existing_entrypoint",\n'
                    '                        item[0] == "retrieval_plus_model",\n',
             '089': '                if selected_route == "simple_answer":\n'
                    '                    return selected_route, "Short direct question with no workspace, file, or '
                    'tool cues."\n'
                    '                if selected_route == "direct_existing_entrypoint":\n',
             '090': '                    return selected_route, f"Prompt points at {entry_label} and asks to use the '
                    'existing workspace path first."\n'
                    '                if selected_route == "read_only_workspace":\n'
                    '                    return selected_route, "Read-oriented workspace prompt with explicit repo or '
                    'file context and no edit requirement."\n'
                    '                if selected_route == "tool_loop":\n'
                    '                    return selected_route, "Prompt explicitly leans on tools, commands, MCP, '
                    'plugins, or verification."\n'
                    '                if selected_route == "ask_user_missing_info":\n'
                    '                    return selected_route, "Prompt appears to reference missing context that '
                    'should be clarified before proceeding."\n'
                    '                return selected_route, "Default project route: retrieve focused context first, '
                    'then reason or use tools as needed."\n',
             '091': '# <QUBITZ_SHARED_REFERENCE:wrapper:d1926feb76ad9268>\n',
             '092': '# <QUBITZ_SHARED_REFERENCE:wrapper:d59eb373431ccdf8>\n',
             '093': '            def _selected_route_name(self, prompt: str) -> str:\n'
                    '                locked_name = str(getattr(self, "_locked_route_name", "") or "")\n'
                    '                if locked_name:\n'
                    '                    return locked_name\n'
                    '                return self._decide_locked_route(prompt)[0]\n'
                    '\n'
                    '            def _clear_locked_route(self) -> None:\n'
                    '                self._locked_route_name = ""\n'
                    '                self._locked_route_reason = ""\n'
                    '\n'
                    '            def _route_status_message(self, prompt: str) -> str:\n'
                    '                selected_route = self._selected_route_name(prompt)\n'
                    '                if selected_route == "simple_answer":\n',
             '094': '                if selected_route == "direct_existing_entrypoint":\n',
             '095': '                        "Wrapper route: direct_existing_entrypoint. Run the existing workspace '
                    'path first, then widen only "\n'
                    '                        "if that path fails or returns insufficient verified output."\n',
             '096': '                if selected_route == "read_only_workspace":\n',
             '097': '                return "Wrapper route: retrieval_plus_model. Use focused local context before '
                    'broader tool use."\n',
             '098': '            def _try_direct_existing_script_completion(\n',
             '099': '            ) -> str | None:\n',
             '100': '                    return None\n'
                    '                expected_count = _expected_result_count(prompt)\n'
                    '                requested_fields = _requested_output_fields(prompt)\n'
                    '                requested_browser_open = _prompt_requests_browser_open(prompt)\n'
                    '                before_helpers = {\n'
                    '                    str(candidate.resolve()): candidate.stat().st_mtime\n'
                    '                    for candidate in _collect_browser_helper_candidates(self.workspace)\n'
                    '                }\n'
                    '                before_artifacts = _workspace_text_artifact_state(base, self.workspace)\n',
             '101': '',
             '102': '                            direct_python = _select_direct_workspace_python(base, '
                    'self.workspace)\n',
             '103': '                                return None\n',
             '104': '                            self._emit(\n'
                    '                                callback,\n'
                    '                                "status",\n'
                    '                                (\n'
                    '                                    "Wrapper direct path: using "\n'
                    '                                    f"{base.relative_path(interpreter_path, self.workspace)} "\n'
                    '                                    f"through the {launch_mode} execution path."\n'
                    '                                ),\n'
                    '                            )\n',
             '105': '                                    base,\n'
                    '                                    self.workspace,\n'
                    '                                    command,\n'
                    '                                    DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                                    cancel_event=self._cancel_event,\n',
             '106': '',
             '107': '                                    base,\n'
                    '                                    self.workspace,\n'
                    '                                    command,\n'
                    '                                    DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                                    cancel_event=self._cancel_event,\n',
             '108': '',
             '109': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '110': '',
             '111': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '112': '',
             '113': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '114': '',
             '115': '                            return None\n',
             '116': '                            return None\n',
             '117': '                            base,\n'
                    '                            self.workspace,\n'
                    '                            command,\n'
                    '                            DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n'
                    '                            cancel_event=self._cancel_event,\n',
             '118': '',
             '119': '                        return None\n',
             '120': '                    return None\n',
             '121': '                if return_code != 0:\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        f"Wrapper direct path: {entrypoint_label} exited {return_code}; falling '
                    'back to the model loop.",\n'
                    '                    )\n'
                    '                    return None\n',
             '122': '                if not browser_helper_text:\n',
             '123': '                        if browser_helper_path is not None:\n'
                    '                            browser_helper_text = browser_helper_path.read_text(encoding="utf-8", '
                    'errors="ignore")\n',
             '124': '',
             '125': '                stderr_for_checks = str(getattr(result, "stderr_full", result.get("stderr", "")) '
                    'or "")\n'
                    '                stdout_for_checks = str(getattr(result, "stdout_full", result.get("stdout", "")) '
                    'or "")\n'
                    '                evidence_texts = [stderr_for_checks, stdout_for_checks, browser_helper_text]\n',
             '126': '                rows: list[dict[str, Any]] = []\n',
             '127': '                            rows.append(normalized_row)\n',
             '128': '                    DIRECT_SCRIPT_COMPLETION_MAX_URLS,\n',
             '129': '                direct_rows = rows or [{"url": url} for url in output_urls]\n'
                    '                target_count = expected_count or len(direct_rows)\n'
                    '                completed_rows: list[dict[str, Any]] = []\n'
                    '                for index, row in enumerate(direct_rows[:target_count]):\n'
                    '                    merged = _normalize_result_row(dict(row))\n'
                    '                    if not _is_external_http_url(merged.get("url")) and index < '
                    'len(output_urls):\n'
                    '                        merged["url"] = output_urls[index]\n'
                    '                    completed_rows.append(merged)\n',
             '130': '                completed_urls = _validated_external_urls(\n'
                    '                    [row.get("url", "") for row in completed_rows],\n',
             '131': '                    stdout_for_checks,\n',
             '132': '                verified_count = len(completed_urls) if requires_url else len(completed_rows)\n'
                    '                rows_complete = target_count > 0 and len(completed_rows) >= target_count\n'
                    '                urls_complete = not requires_url or len(completed_urls) >= target_count\n'
                    '                fields_complete = not requested_fields or '
                    '_rows_have_required_fields(completed_rows, requested_fields)\n'
                    '                if not (rows_complete and urls_complete and fields_complete):\n'
                    '                    broker = _TOOL_CALL_CONTEXT.get()\n'
                    '                    if broker is not None and hasattr(broker, "evidence"):\n'
                    '                        broker.provenance_urls.update(\n'
                    '                            url.casefold() for url in output_urls if _is_external_http_url(url)\n'
                    '                        )\n'
                    '                        categories = ["result_count"] if direct_rows else []\n'
                    '                        count_requirement = _requested_count_postcondition(prompt)\n'
                    '                        if changed_artifacts and count_requirement and count_requirement[0] == '
                    '"created_outputs":\n'
                    '                            categories.append("created_outputs")\n'
                    '                        if reported_opened_urls:\n'
                    '                            categories.append("opened_urls")\n'
                    '                        broker.evidence.append(\n'
                    '                            {\n'
                    '                                "turn_id": broker.turn_id,\n'
                    '                                "tool": "wrapper_direct_existing_entrypoint",\n'
                    '                                "success": True,\n'
                    '                                "categories": categories,\n'
                    '                                "paths": [str(path) for path, _ in changed_artifacts],\n'
                    '                                "status": "incomplete",\n'
                    '                                "return_code": return_code,\n'
                    '                                "result_count": len(direct_rows),\n'
                    '                                "opened_count": len(reported_opened_urls),\n'
                    '                                "urls": reported_opened_urls,\n'
                    '                            }\n'
                    '                        )\n'
                    '                    self._existing_script_preflight_context = '
                    '_build_direct_continuation_context(\n'
                    '                        base,\n'
                    '                        self.workspace,\n'
                    '                        entrypoint_label=entrypoint_label,\n'
                    '                        return_code=return_code,\n'
                    '                        target_count=target_count,\n'
                    '                        verified_count=verified_count,\n'
                    '                        rows=completed_rows,\n'
                    '                        urls=output_urls,\n'
                    '                        stdout_text=stdout_for_checks,\n'
                    '                        stderr_text=stderr_for_checks,\n'
                    '                        artifacts=changed_artifacts,\n',
             '133': '                    if reported_opened_urls:\n'
                    '                        remaining_urls = [url for url in output_urls if url not in '
                    'set(reported_opened_urls)]\n'
                    '                        self._existing_script_preflight_context += (\n'
                    '                            f"\\nEntrypoint-reported browser opens verified conservatively: '
                    '{len(reported_opened_urls)}."\n'
                    '                        )\n'
                    '                        if remaining_urls:\n'
                    '                            self._existing_script_preflight_context += (\n'
                    '                                "\\nURLs not covered by the entrypoint\'s reported browser-open '
                    'count:\\n"\n'
                    '                                + "\\n".join(remaining_urls[:DIRECT_SCRIPT_COMPLETION_MAX_URLS])\n'
                    '                            )\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        (\n'
                    '                            "Wrapper direct path produced incomplete verified output "\n'
                    '                            f"({verified_count} of {target_count}); continuing only the missing '
                    'postconditions."\n'
                    '                        ),\n'
                    '                    )\n'
                    '                    return None\n'
                    '                browser_open_ran = bool(reported_opened_urls)\n'
                    '                if requested_browser_open and not browser_open_ran and (\n'
                    '                    self._wsl_windows_interop_available()\n'
                    '                    or not (callable(getattr(base, "in_wsl", None)) and base.in_wsl())\n',
             '134': '                        completed_urls,\n'
                    '                        DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,\n',
             '135': '                    browser_open_ran = int(browser_result.get("return_code", '
                    'browser_result.get("returncode", 0))) == 0\n',
             '136': '                    categories = ["result_count"]\n',
             '137': '                    if changed_artifacts and count_requirement and count_requirement[0] == '
                    '"created_outputs":\n',
             '138': '                            "success": True,\n',
             '139': '                            "status": "completed" if not requested_browser_open or '
                    'browser_open_ran else "incomplete",\n',
             '140': '                            "result_count": len(completed_rows),\n'
                    '                            "opened_count": len(reported_opened_urls) if reported_opened_urls '
                    'else len(completed_urls) if browser_open_ran else 0,\n'
                    '                            "urls": reported_opened_urls or (completed_urls if browser_open_ran '
                    'else []),\n',
             '141': '                if requested_browser_open and not browser_open_ran:\n'
                    '                    self._existing_script_preflight_context = '
                    '_build_direct_continuation_context(\n'
                    '                        base,\n'
                    '                        self.workspace,\n'
                    '                        entrypoint_label=entrypoint_label,\n'
                    '                        return_code=return_code,\n'
                    '                        target_count=target_count,\n'
                    '                        verified_count=len(completed_rows),\n'
                    '                        rows=completed_rows,\n'
                    '                        urls=completed_urls,\n'
                    '                        stdout_text=stdout_for_checks,\n'
                    '                        stderr_text=stderr_for_checks,\n'
                    '                        artifacts=changed_artifacts,\n',
             '142': '                        "Wrapper direct path collected the requested results but did not verify '
                    'the browser side effect; continuing only that missing postcondition.",\n',
             '143': '                    return None\n'
                    '                if not requested_browser_open:\n'
                    '                    helper_label = "none"\n'
                    '                else:\n'
                    '                    helper_label = "inline Start-Process with validated URLs"\n',
             '144': '                        "Wrapper direct path completed the existing-script task without entering '
                    'the model loop. "\n'
                    '                        f"Rows: {len(completed_rows)}. Browser step: {helper_label}."\n',
             '145': '                return _format_direct_result_answer(\n'
                    '                    completed_rows,\n'
                    '                    browser_open_ran=browser_open_ran,\n'
                    '                    requested_browser_open=requested_browser_open,\n'
                    '                    requested_fields=requested_fields,\n'
                    '                )\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:c4066f729f61e008>\n',
             '146': '',
             '147': '',
             '148': '                if selected_route == "simple_answer":\n',
             '149': '                        "- This is a simple direct question. Answer directly and do not use tools '
                    'unless they are "\n'
                    '                        "truly necessary.\\n"\n',
             '150': '                if selected_route == "direct_existing_entrypoint":\n',
             '151': '                        "- This is a foreground active-workspace task that asks you to use '
                    'existing project functionality.",\n'
                    '                        "- Prefer the shortest existing local execution path first.",\n',
             '152': '                            "- Keep background-job tools, sandbox tools, and auxiliary '
                    'MCP-management tools as fallbacks unless the direct path fails or the task clearly needs '
                    'them.",\n',
             '153': '                            "- Do not modify or replace an existing workspace entrypoint just to '
                    'make the task work unless the user explicitly asked for that.",\n',
             '154': '                            "- If the task requires a structured list, verify the final answer '
                    'includes the requested fields for every returned item.",\n',
             '155': '                if selected_route == "read_only_workspace":\n',
             '156': '                        "- Use the wrapper-provided file excerpts first when present.\\n"\n'
                    '                        "- Do not edit files, run commands, or invoke broad tools unless the '
                    'provided file context is insufficient.\\n"\n',
             '157': '',
             '158': '                if selected_route == "simple_answer":\n',
             '159': '                if selected_route == "read_only_workspace":\n',
             '160': '                if selected_route != "direct_existing_entrypoint":\n',
             '161': '',
             '162': '                direct_names = {\n',
             '163': '                    "read_memory",\n                    "search_memory",\n',
             '164': '                    direct_names.add("run_powershell_command")\n'
                    '                auxiliary_mcp_names = {\n',
             '165': '',
             '166': '                background_names = {\n'
                    '                    "list_background_jobs",\n'
                    '                    "read_background_job",\n'
                    '                    "cancel_background_job",\n'
                    '                }\n',
             '167': '',
             '168': '                    if name in mutation_names:\n'
                    '                        continue\n'
                    '                    if name in direct_names:\n',
             '169': '                    elif name in background_names or name.startswith("sandbox_") or name == '
                    '"list_sandboxes":\n'
                    '                        bucket = 3\n'
                    '                    elif name in auxiliary_mcp_names:\n',
             '170': '# <QUBITZ_SHARED_REFERENCE:wrapper:d445027df34e5908>\n',
             '171': '',
             '172': '                self._locked_route_name = ""\n                self._locked_route_reason = ""\n',
             '173': '',
             '174': '# <QUBITZ_SHARED_REFERENCE:wrapper:7a3188c220541554>\n'
                    '                selected_route, route_reason = self._decide_locked_route(user_prompt)\n'
                    '                self._locked_route_name = selected_route\n'
                    '                self._locked_route_reason = route_reason\n'
                    '                if selected_route == "ask_user_missing_info":\n',
             '175': '',
             '176': '                    self.local_plugins.render_for_prompt(user_prompt)\n'
                    '                    if self.local_only_config.plugins_enabled and selected_route not in '
                    '{"simple_answer", "read_only_workspace"}\n',
             '177': '                self._emit(callback, "status", self._route_status_message(user_prompt))\n'
                    '                runtime_capabilities = self._runtime_capabilities()\n'
                    '                in_wsl_session = bool(runtime_capabilities.get("in_wsl"))\n'
                    '                interop_available = bool(runtime_capabilities.get("windows_interop_available"))\n'
                    '                configured_server_path = (\n'
                    '                    getattr(self.config, "llama_server_path", "") or '
                    'os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME) or ""\n'
                    '                ).strip()\n'
                    '                configured_server_url = (os.environ.get(base.LLAMACPP_SERVER_URL_ENV_NAME) or '
                    '"").strip()\n'
                    '                if in_wsl_session and interop_available:\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        "WSL Windows-executable interop probe succeeded; direct Windows '
                    'PowerShell and .exe paths are available from this session.",\n'
                    '                    )\n'
                    '                if (\n'
                    '                    in_wsl_session\n'
                    '                    and not interop_available\n'
                    '                    and not configured_server_path\n'
                    '                    and not configured_server_url\n'
                    '                    and base.normalize_base_url(self.config.server_url) == '
                    'base.DEFAULT_LLAMACPP_BASE_URL\n'
                    '                ):\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        (\n'
                    '                            "WSL Windows-executable interop is unavailable in this session; "\n'
                    '                            "using the native WSL llama.cpp backend instead of the cached Windows '
                    'runtime."\n'
                    '                        ),\n'
                    '                    )\n'
                    '                    runtime_info = '
                    'base.ensure_project_local_native_llamacpp_runtime(self.runtime_workspace)\n'
                    '                    self.config.llama_server_path = str(runtime_info["executable"])\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        (\n'
                    '                            f"Native WSL llama.cpp backend ready at '
                    '{runtime_info[\'executable\']} "\n'
                    '                            f"(log: {runtime_info[\'build_log\']})"\n'
                    '                        ),\n'
                    '                    )\n'
                    '                bypass_retrieval = selected_route == "simple_answer"\n'
                    '                foreground_existing_script_task = selected_route == "direct_existing_entrypoint"\n'
                    '                read_only_workspace_task = selected_route == "read_only_workspace"\n'
                    '                if foreground_existing_script_task:\n'
                    '                    direct_answer = self._try_direct_existing_script_completion(user_prompt, '
                    'callback)\n'
                    '                    if direct_answer is not None:\n'
                    '                        self._local_plugin_context = "None"\n'
                    '                        self._task_case_guidance = ""\n'
                    '                        self._simple_direct_mode = False\n'
                    '                        self._runtime_fact_context = ""\n'
                    '                        self._prompt_step_retry_override = None\n'
                    '                        self._clear_locked_route()\n'
                    '                        return direct_answer\n',
             '178': '                original_temperature = getattr(self.config, "temperature", None)\n'
                    '                original_top_p = getattr(self.config, "top_p", None)\n'
                    '                original_min_p = getattr(self.config, "min_p", None)\n'
                    '                original_repeat_penalty = getattr(self.config, "repeat_penalty", None)\n'
                    '                if foreground_existing_script_task and self._existing_script_preflight_context:\n'
                    '                    prompt = (\n'
                    '                        f"{prompt.rstrip()}\\n\\n"\n'
                    '                        "Wrapper preflight execution context:\\n"\n'
                    '                        f"{self._existing_script_preflight_context}"\n'
                    '                    )\n'
                    '                    original_format_context = self.retriever.format_context\n'
                    '                    self._should_skip_repo_retrieval = lambda _prompt: True\n'
                    '                    self.memory.build_context = lambda _prompt: ""\n'
                    '                    self.retriever.format_context = lambda _prompt: '
                    'self._existing_script_preflight_context\n'
                    '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        "Wrapper focused continuation: preserving verified entrypoint results and '
                    'skipping broad retrieval while completing only missing postconditions.",\n'
                    '                    )\n'
                    '                elif bypass_retrieval:\n',
             '179': '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        "Simple direct question detected; skipping repository retrieval, '
                    'embedding generation, and MCP tool loading.",\n'
                    '                    )\n',
             '180': '                    self._emit(\n'
                    '                        callback,\n'
                    '                        "status",\n'
                    '                        "Foreground existing-entrypoint task detected; prioritizing direct '
                    'workspace tools and keeping wrapper tools as fallbacks.",\n'
                    '                    )\n',
             '181': '',
             '182': '',
             '183': '\n',
             '184': '                    return self._prioritize_tools_for_prompt(user_prompt, tools)\n',
             '185': '                sampling_profile = _sampling_profile_for_route(user_prompt, selected_route)\n'
                    '                _apply_sampling_profile(self.config, sampling_profile)\n',
             '186': '',
             '187': '',
             '188': '                    retry_step_cap, retry_label = '
                    'self._retry_step_cap_for_failed_answer(user_prompt, answer)\n',
             '189': '',
             '190': '',
             '191': '                        if selected_route == "simple_answer" and '
                    'self._is_retryable_final_answer(user_prompt, answer):\n',
             '192': '',
             '193': '',
             '194': '',
             '195': '                    if original_temperature is not None:\n'
                    '                        self.config.temperature = original_temperature\n'
                    '                    if original_top_p is not None:\n'
                    '                        self.config.top_p = original_top_p\n'
                    '                    if original_min_p is not None:\n'
                    '                        self.config.min_p = original_min_p\n'
                    '                    if original_repeat_penalty is not None:\n'
                    '                        self.config.repeat_penalty = original_repeat_penalty\n',
             '196': '',
             '197': '                    self._simple_direct_mode = False\n',
             '198': '',
             '199': '                    self._clear_locked_route()\n',
             '200': '                overlay = textwrap.dedent(\n'
                    '                    f"""\n'
                    '\n'
                    '                    Local-only overlay for this copy:\n'
                    '                    - Use only local files, local shells, local MCP servers, and local model '
                    'runtimes.\n'
                    '                    - Do not rely on paid services, cloud agents, hosted inference APIs, or '
                    'remote execution.\n'
                    '                    - Downloading free local model files or runtime dependencies is allowed when '
                    'required.\n'
                    '                    - For a session-scoped stdio MCP script, use list_project_mcp_tools and '
                    'call_project_mcp_tool directly; use start and stop tools only for a persistent managed server.\n'
                    '                    - For venv-backed Python tasks, prefer direct interpreter paths over '
                    'activation-script shell chains when a local project interpreter is available.\n'
                    '                    - Prefer code-intelligence tools for symbols, definitions, references, '
                    'callers, callees, diagnostics, and type-hint questions.\n'
                    '                    - Prefer sandbox_* tools for multi-file edits, deletes, installs, and shell '
                    'commands that mutate state.\n'
                    '                    - Local background jobs are available through /bg and can be inspected with '
                    '/jobs.\n'
                    '                    - Local plugin manifests may add extra task guidance from .qubitz/plugins.\n'
                    '\n'
                    '                    Authoritative runtime facts for this task:\n'
                    '                    {self._runtime_fact_context or "None"}\n'
                    '\n'
                    '                    Prompt-specific routing guidance:\n'
                    '                    {self._task_case_guidance or "None"}\n'
                    '\n'
                    '                    Active local plugins:\n'
                    '                    {self._local_plugin_context}\n'
                    '                    """\n'
                    '                ).rstrip()\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:28c585c30a060bef>\n'
                    '                dynamic_sections: list[str] = []\n',
             '201': '                    dynamic_sections.append(f"Current memory context:\\n{memory_context}")\n',
             '202': '                    dynamic_sections.append(f"Active local skill '
                    'context:\\n{active_skill_context}")\n',
             '203': '                    dynamic_sections.append(f"Condensed earlier conversation '
                    'summary:\\n{history_summary}")\n'
                    '                if dynamic_sections:\n'
                    '                    combined = f"{combined}\\n\\nDynamic context for this task:\\n" + '
                    '"\\n\\n".join(dynamic_sections)\n',
             '204': '',
             '205': '',
             '206': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:a3a174413ec3d707>\n',
             '207': '',
             '208': '',
             '209': '',
             '210': '',
             '211': '',
             '212': '                kwargs["ollama_num_ctx"] = args.num_ctx\n',
             '213': '                kwargs["num_ctx"] = args.num_ctx\n',
             '214': '                kwargs["ollama_num_predict"] = args.num_predict\n',
             '215': '                kwargs["num_predict"] = args.num_predict\n',
             '216': '',
             '217': '',
             '218': '',
             '219': '',
             '220': '',
             '221': '',
             '222': '',
             '223': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:38c3c563aff17f5f>\n',
             '224': '',
             '225': '\n',
             '226': '',
             '227': '# <QUBITZ_SHARED_REFERENCE:wrapper:b3be058936b061da>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:f4bb4ab237517c13>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:8aa0015742b3bf92>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:ba7c64fb361a1107>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:73d63eb8b34a0672>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:065cf7deea2d63f4>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:dfa78a941ab87e58>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:c6d66babd87afd87>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:5a6c821dfda3f3b6>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:79589de5517a2378>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:2e6a6d3436d3e115>\n',
             '228': '\n',
             '229': '',
             '230': '\n',
             '231': '',
             '232': '',
             '233': '# <QUBITZ_SHARED_REFERENCE:wrapper:7873f4bb2ec60cea>\n\n',
             '234': '',
             '235': '# <QUBITZ_SHARED_REFERENCE:wrapper:348943cecf708d86>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:f8c884b1e07a766b>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:0353f3c875f1b913>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:8f4df2dc4865730b>\n',
             '236': '# <QUBITZ_SHARED_REFERENCE:wrapper:c3fe67b7d0e7fc9f>\n',
             '237': '',
             '238': '# <QUBITZ_SHARED_REFERENCE:wrapper:4135c7f338fd931f>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:6bbfcf6d5e2c8cfe>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:5b2df0d7db4eb9ef>\n',
             '239': '# <QUBITZ_SHARED_REFERENCE:wrapper:b643a1100f5399fb>\n\n',
             '240': '',
             '241': '',
             '242': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:6cc1c8966d6d4a8d>\n',
             '243': '        \n\n',
             '244': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:dba7a921146763ff>\n',
             '245': '# <QUBITZ_SHARED_REFERENCE:wrapper:32b3242c1d30f2d6>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:39fd337c87c00874>\n',
             '246': '# <QUBITZ_SHARED_REFERENCE:wrapper:c01591ef2d01e166>\n',
             '247': '# <QUBITZ_SHARED_REFERENCE:wrapper:9fe212a961e2f06d>\n',
             '248': '# <QUBITZ_SHARED_REFERENCE:wrapper:5c34096f33ce4e08>\n',
             '249': '# <QUBITZ_SHARED_REFERENCE:wrapper:74fb6e2a33e4d698>\n\n',
             '250': '',
             '251': '',
             '252': '\n',
             '253': '',
             '254': '',
             '255': '',
             '256': ''},
 'base': {'000': '',
          '001': "DEFAULT_MODEL = 'unsloth/Devstral-Small-2-24B-Instruct-2512'\n"
                 "DEFAULT_HF_GGUF_REPO_ID = 'unsloth/Devstral-Small-2-24B-Instruct-2512-GGUF'\n"
                 "Q4_GGUF_MODEL_FILENAME = 'Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf'\n"
                 "DEFAULT_GGUF_MODEL_LABEL = 'Unsloth-Devstral-Small-2-24B-Q4_K_M'\n"
                 "DEFAULT_GGUF_MODEL_FILENAME = 'Devstral-Small-2-24B-Instruct-2512-Q4_K_M.gguf'\n",
          '002': 'DEFAULT_LLAMACPP_MODEL_READY_TIMEOUT_SECONDS = 300.0\n',
          '003': 'DEFAULT_NUM_CTX = 262144\nDEFAULT_NUM_PREDICT = 16384\n',
          '004': '        workspace / ".cache" / "models" / "bartowski" / '
                 '"mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF" / preferred_filename,\n',
          '005': '    local_dir = workspace / ".cache" / "models" / "bartowski" / '
                 '"mistralai_Devstral-Small-2-24B-Instruct-2512-GGUF"\n',
          '006': '            "Local path to the fixed Q4 GGUF model. Must point to "\n',
          '007': ''}}

_initialize_variant(globals(), _QUBITZ_VARIANT_SPEC)
