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
 'filename': 'AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py',
 'wrapper': {'000': '',
             '001': '',
             '002': '',
             '003': 'SCRIPT_PREFLIGHT_TIMEOUT_SECONDS = 300\n'
                    'SCRIPT_PREFLIGHT_MAX_URLS = 64\n'
                    'ROUTE_SIMPLE_ANSWER = "simple_answer"\n'
                    'ROUTE_DIRECT_EXISTING_ENTRYPOINT = "direct_existing_entrypoint"\n'
                    'ROUTE_READ_ONLY_WORKSPACE = "read_only_workspace"\n'
                    'ROUTE_RETRIEVAL_PLUS_MODEL = "retrieval_plus_model"\n'
                    'ROUTE_TOOL_LOOP = "tool_loop"\n'
                    'ROUTE_ASK_USER_MISSING_INFO = "ask_user_missing_info"\n'
                    'ROUTE_PROFILE_QWEN3_NEXT_DUAL_48G = "48g_dual_gpu_retrieval"\n'
                    'ROUTE_TRACE_FILE_NAME = "route_traces.jsonl"\n'
                    'ROUTE_TRACE_PROMPT_PREVIEW_CHARS = 240\n'
                    'ROUTE_TRACE_ANSWER_PREVIEW_CHARS = 400\n'
                    '\n'
                    '\n'
                    '@dataclass\n'
                    'class RouteFeatures:\n'
                    '    prompt_length: int\n'
                    '    line_count: int\n'
                    '    simple_question_candidate: bool\n'
                    '    explicit_existing_entrypoint: bool\n'
                    '    read_intent: bool\n'
                    '    edit_intent: bool\n'
                    '    verify_intent: bool\n'
                    '    workspace_context_needed: bool\n'
                    '    file_token_count: int\n'
                    '    mentions_tools_or_mcp: bool\n'
                    '    powershell_or_side_effect: bool\n'
                    '    plugin_or_skill_request: bool\n'
                    '    has_entrypoint_spec: bool\n'
                    '    entrypoint_kind: str = ""\n'
                    '    needs_missing_context: bool = False\n'
                    '\n'
                    '    def to_trace_dict(self) -> dict[str, Any]:\n'
                    '        return {\n'
                    '            "prompt_length": self.prompt_length,\n'
                    '            "line_count": self.line_count,\n'
                    '            "simple_question_candidate": self.simple_question_candidate,\n'
                    '            "explicit_existing_entrypoint": self.explicit_existing_entrypoint,\n'
                    '            "read_intent": self.read_intent,\n'
                    '            "edit_intent": self.edit_intent,\n'
                    '            "verify_intent": self.verify_intent,\n'
                    '            "workspace_context_needed": self.workspace_context_needed,\n'
                    '            "file_token_count": self.file_token_count,\n'
                    '            "mentions_tools_or_mcp": self.mentions_tools_or_mcp,\n'
                    '            "powershell_or_side_effect": self.powershell_or_side_effect,\n'
                    '            "plugin_or_skill_request": self.plugin_or_skill_request,\n'
                    '            "has_entrypoint_spec": self.has_entrypoint_spec,\n'
                    '            "entrypoint_kind": self.entrypoint_kind,\n'
                    '            "needs_missing_context": self.needs_missing_context,\n'
                    '        }\n'
                    '\n'
                    '\n'
                    '@dataclass\n'
                    'class RouteDecision:\n'
                    '    selected_route: str\n'
                    '    reason: str\n'
                    '    scores: dict[str, int]\n'
                    '    features: dict[str, Any]\n'
                    '    fallback_routes: list[str] = field(default_factory=list)\n'
                    '    profile: str = ROUTE_PROFILE_QWEN3_NEXT_DUAL_48G\n'
                    '    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])\n',
             '004': '',
             '005': 'SCRIPT_PREFLIGHT_BROWSER_GLOB_PATTERNS = ("open*.ps1", "open*.bat", "open*.cmd")\n',
             '006': '    concrete_action = re.search(\n'
                    '        '
                    'r"\\b(build|compile|test|lint|run|start|serve|deploy|fetch|get|list|open|read|inspect|explain|summarize|find|search|convert|train|evaluate|benchmark|install|update|fix|create|delete|rename)\\b",\n'
                    '        lowered,\n'
                    '    )\n'
                    '    if concrete_action is not None:\n',
             '007': '    return any(\n'
                    '        marker in lowered\n'
                    '        for marker in (\n'
                    '            "produce one concrete verified project result",\n'
                    '            "produce a project result",\n'
                    '            "use the active workspace",\n'
                    '            "use this project",\n'
                    '            "use the project",\n'
                    '            "use this workspace",\n'
                    '            "use the workspace",\n'
                    '        )\n'
                    '    )\n',
             '008': '    for pattern in SCRIPT_PREFLIGHT_BROWSER_GLOB_PATTERNS:\n',
             '009': '        if len(urls) >= SCRIPT_PREFLIGHT_MAX_URLS:\n',
             '010': '# <QUBITZ_SHARED_REFERENCE:wrapper:8e2b980c6e721daa>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:8fd25370bafe5829>\n'
                    '\n',
             '011': '',
             '012': '',
             '013': '',
             '014': '',
             '015': '                "artifacts",\n'
                    '                "data",\n'
                    '                "entries",\n'
                    '                "files",\n'
                    '                "items",\n'
                    '                "opened_urls",\n'
                    '                "outputs",\n'
                    '                "records",\n'
                    '                "results",\n'
                    '                "rows",\n'
                    '                "urls",\n',
             '016': '',
             '017': '\n'
                    '\n'
                    'def _rewrite_answer_urls_from_helper(answer: str, urls: Sequence[str]) -> str:\n'
                    '    return answer\n',
             '018': '',
             '019': '',
             '020': '\n\n',
             '021': '',
             '022': 'def _local_skills_root(runtime_workspace: Path) -> Path:\n'
                    '    return runtime_workspace / ".skills"\n'
                    '\n'
                    '\n'
                    'def _local_skills_available(runtime_workspace: Path) -> bool:\n'
                    '    root = _local_skills_root(runtime_workspace)\n'
                    '    if not root.exists() or not root.is_dir():\n'
                    '        return False\n'
                    '    with suppress(Exception):\n'
                    '        for candidate in root.iterdir():\n'
                    '            if candidate.name.startswith("."):\n'
                    '                continue\n'
                    '            return True\n'
                    '    return False\n'
                    '\n'
                    '\n',
             '023': '\n',
             '024': '\n',
             '025': '',
             '026': '\n\n',
             '027': '\n\n',
             '028': '\n',
             '029': '\n',
             '030': '\n\n# <QUBITZ_SHARED_REFERENCE:wrapper:683850aa594ab1e9>\n',
             '031': '\n',
             '032': '\n',
             '033': '\n',
             '034': '\n',
             '035': '',
             '036': '    skills = base.SkillRegistry(runtime_workspace) if _local_skills_available(runtime_workspace) '
                    'else None\n',
             '037': '                "skills_root": base.relative_path(_local_skills_root(runtime_workspace), '
                    'runtime_workspace),\n'
                    '                "skill_count": skills.count() if skills is not None else 0,\n'
                    '                "skill_warnings": skills.warnings if skills is not None else [],\n',
             '038': '    if skills is not None:\n'
                    '        @server.resource("skills://index")\n'
                    '        def skills_index() -> str:\n'
                    '            return json.dumps(\n'
                    '                {\n'
                    '                    "skills_root": base.relative_path(skills.skills_root, runtime_workspace),\n'
                    '                    "count": skills.count(),\n'
                    '                    "warnings": skills.warnings,\n'
                    '                    "skills": skills.list_summaries(),\n'
                    '                },\n'
                    '                ensure_ascii=True,\n'
                    '                indent=2,\n'
                    '            )\n',
             '039': '    @server.resource("plugins://index")\n'
                    '    def plugins_index() -> str:\n'
                    '        return json.dumps({"plugins": plugin_registry.list_plugins()}, ensure_ascii=True, '
                    'indent=2)\n',
             '040': '    if skills is not None:\n'
                    '        @server.tool(description="List runtime-root local skills discovered under .skills.")\n'
                    '        def list_skills() -> dict[str, Any]:\n'
                    '            return {\n'
                    '                "skills_root": base.relative_path(skills.skills_root, runtime_workspace),\n'
                    '                "count": skills.count(),\n'
                    '                "warnings": skills.warnings,\n'
                    '                "skills": skills.list_summaries(),\n'
                    '            }\n',
             '041': '        @server.tool(description="Read a runtime-root local skill\'s metadata and body.")\n'
                    '        def read_skill(skill_name: str) -> dict[str, Any]:\n'
                    '            skill = skills.get(skill_name)\n'
                    '            summary = skill.to_summary(runtime_workspace)\n'
                    '            summary["body"] = skill.body\n'
                    '            return summary\n',
             '042': '        @server.tool(description="Read a file or directory inside a runtime-root local skill '
                    'root.")\n'
                    '        def read_skill_resource(skill_name: str, resource_path: str) -> dict[str, Any]:\n'
                    '            return skills.read_skill_resource(skill_name, resource_path)\n',
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
                    'project Python "\n'
                    '            "interpreter path. Do not use this for a session-scoped stdio MCP script passed to "\n'
                    '            "list_project_mcp_tools or call_project_mcp_tool."\n',
             '047': '            "List tools from a local MCP server. An explicit script or configuration '
                    'server_reference is opened as "\n'
                    '            "a session-scoped stdio process and closed automatically; it does not require '
                    'start_project_mcp_server. "\n',
             '048': '            "Invoke one named local MCP tool. An explicit script or configuration '
                    'server_reference is opened as a "\n'
                    '            "session-scoped stdio process and closed automatically; it does not require '
                    'start_project_mcp_server. "\n',
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
             '067': '',
             '068': '',
             '069': '',
             '070': ')\n',
             '071': '',
             '072': '\n',
             '073': '',
             '074': '\n',
             '075': '\n',
             '076': '',
             '077': '',
             '078': '',
             '079': '                if selected_route not in {ROUTE_SIMPLE_ANSWER, '
                    'ROUTE_DIRECT_EXISTING_ENTRYPOINT}:\n',
             '080': '                if selected_route == ROUTE_SIMPLE_ANSWER:\n',
             '081': '                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n',
             '082': '# <QUBITZ_SHARED_REFERENCE:wrapper:d1926feb76ad9268>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:d59eb373431ccdf8>\n'
                    '\n'
                    '            def _selected_route_name(self, prompt: str) -> str:\n'
                    '                decision = getattr(self, "_route_decision", None)\n'
                    '                if isinstance(decision, RouteDecision):\n'
                    '                    return decision.selected_route\n'
                    '                if self._should_bypass_embedding_retrieval(prompt):\n'
                    '                    return ROUTE_SIMPLE_ANSWER\n'
                    '                if self._is_foreground_existing_script_task(prompt):\n'
                    '                    return ROUTE_DIRECT_EXISTING_ENTRYPOINT\n'
                    '                return ROUTE_RETRIEVAL_PLUS_MODEL\n'
                    '\n',
             '083': '            def _score_routes(self, features: RouteFeatures) -> dict[str, int]:\n',
             '084': '                    ROUTE_SIMPLE_ANSWER: 0,\n'
                    '                    ROUTE_DIRECT_EXISTING_ENTRYPOINT: 0,\n'
                    '                    ROUTE_READ_ONLY_WORKSPACE: 0,\n'
                    '                    ROUTE_RETRIEVAL_PLUS_MODEL: 0,\n'
                    '                    ROUTE_TOOL_LOOP: 0,\n'
                    '                    ROUTE_ASK_USER_MISSING_INFO: 0,\n',
             '085': '                if features.simple_question_candidate:\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] += 30\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 8\n'
                    '                    scores[ROUTE_TOOL_LOOP] -= 10\n'
                    '                if features.explicit_existing_entrypoint:\n'
                    '                    scores[ROUTE_DIRECT_EXISTING_ENTRYPOINT] += 28\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 4\n'
                    '                    scores[ROUTE_TOOL_LOOP] -= 6\n'
                    '                elif features.has_entrypoint_spec:\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] += 8 if features.read_intent else 0\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 8\n'
                    '                if features.read_intent:\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] += 10\n'
                    '                if features.file_token_count:\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] += 12\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 4\n'
                    '                if features.workspace_context_needed:\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] += 5\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 10\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 12\n'
                    '                if features.edit_intent:\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 12\n'
                    '                    scores[ROUTE_TOOL_LOOP] += 6\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 20\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] -= 4\n'
                    '                if features.verify_intent:\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 10\n'
                    '                    scores[ROUTE_TOOL_LOOP] += 8\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 20\n'
                    '                if features.mentions_tools_or_mcp:\n'
                    '                    scores[ROUTE_TOOL_LOOP] += 14\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 12\n'
                    '                if features.powershell_or_side_effect:\n'
                    '                    scores[ROUTE_DIRECT_EXISTING_ENTRYPOINT] += 12\n'
                    '                    scores[ROUTE_TOOL_LOOP] += 18\n'
                    '                    scores[ROUTE_READ_ONLY_WORKSPACE] -= 30\n'
                    '                if features.plugin_or_skill_request:\n'
                    '                    scores[ROUTE_TOOL_LOOP] += 10\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 8\n'
                    '                if features.line_count > 1 or features.prompt_length > 220:\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 4\n'
                    '                    scores[ROUTE_SIMPLE_ANSWER] -= 6\n'
                    '                if features.needs_missing_context:\n'
                    '                    scores[ROUTE_ASK_USER_MISSING_INFO] += 18\n'
                    '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 4\n',
             '086': '                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 3\n',
             '087': '            def _fallback_routes_for_selection(self, selected_route: str) -> list[str]:\n'
                    '                if selected_route == ROUTE_SIMPLE_ANSWER:\n'
                    '                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]\n'
                    '                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n'
                    '                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]\n'
                    '                if selected_route == ROUTE_READ_ONLY_WORKSPACE:\n'
                    '                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]\n'
                    '                if selected_route == ROUTE_TOOL_LOOP:\n'
                    '                    return [ROUTE_RETRIEVAL_PLUS_MODEL]\n'
                    '                if selected_route == ROUTE_ASK_USER_MISSING_INFO:\n'
                    '                    return [ROUTE_READ_ONLY_WORKSPACE, ROUTE_RETRIEVAL_PLUS_MODEL]\n'
                    '                return [ROUTE_TOOL_LOOP]\n'
                    '\n'
                    '            def _decide_route(self, prompt: str) -> RouteDecision:\n',
             '088': '                        item[0] == ROUTE_SIMPLE_ANSWER,\n'
                    '                        item[0] == ROUTE_DIRECT_EXISTING_ENTRYPOINT,\n'
                    '                        item[0] == ROUTE_RETRIEVAL_PLUS_MODEL,\n',
             '089': '                if selected_route == ROUTE_SIMPLE_ANSWER:\n'
                    '                    reason = "Short direct question with no workspace, file, or tool cues."\n'
                    '                elif selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n',
             '090': '                    reason = f"Prompt points at {entry_label} and asks to use the existing '
                    'workspace path first."\n'
                    '                elif selected_route == ROUTE_READ_ONLY_WORKSPACE:\n'
                    '                    reason = "Read-oriented workspace prompt with explicit repo or file context '
                    'and no edit requirement."\n'
                    '                elif selected_route == ROUTE_TOOL_LOOP:\n'
                    '                    reason = "Prompt explicitly leans on tools, commands, MCP, plugins, or '
                    'verification."\n'
                    '                elif selected_route == ROUTE_ASK_USER_MISSING_INFO:\n'
                    '                    reason = "Prompt appears to reference missing context that should be '
                    'clarified before proceeding."\n'
                    '                else:\n'
                    '                    reason = "Default project route: retrieve focused context first, then reason '
                    'or use tools as needed."\n'
                    '                return RouteDecision(\n'
                    '                    selected_route=selected_route,\n'
                    '                    reason=reason,\n'
                    '                    scores=scores,\n'
                    '                    features=features.to_trace_dict(),\n'
                    '                    fallback_routes=self._fallback_routes_for_selection(selected_route),\n'
                    '                )\n',
             '091': '            def _route_trace_path(self) -> Path:\n'
                    '                return self.runtime_workspace / LOCAL_ONLY_DIR_NAME / ROUTE_TRACE_FILE_NAME\n',
             '092': '            def _record_route_trace(\n'
                    '                self,\n'
                    '                prompt: str,\n'
                    '                status: str,\n'
                    '                *,\n'
                    '                answer: str = "",\n'
                    '                error: str = "",\n'
                    '            ) -> None:\n'
                    '                decision = getattr(self, "_route_decision", None)\n'
                    '                if not isinstance(decision, RouteDecision):\n'
                    '                    return\n'
                    '                payload = {\n'
                    '                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),\n'
                    '                    "trace_id": decision.trace_id,\n'
                    '                    "variant": wrapper_script.name,\n'
                    '                    "workspace": str(self.workspace),\n'
                    '                    "profile": decision.profile,\n'
                    '                    "selected_route": decision.selected_route,\n'
                    '                    "fallback_routes": list(decision.fallback_routes),\n'
                    '                    "fallback_chain": list(getattr(self, "_route_fallback_chain", [])),\n'
                    '                    "reason": decision.reason,\n'
                    '                    "scores": dict(decision.scores),\n'
                    '                    "features": dict(decision.features),\n'
                    '                    "status": status,\n'
                    '                    "prompt_preview": _shorten(" ".join(prompt.split()), '
                    'ROUTE_TRACE_PROMPT_PREVIEW_CHARS),\n'
                    '                    "answer_preview": _shorten(" ".join(answer.split()), '
                    'ROUTE_TRACE_ANSWER_PREVIEW_CHARS) if answer else "",\n'
                    '                    "error": error,\n'
                    '                    "timings": dict(getattr(self, "_route_phase_timings", {})),\n'
                    '                    "existing_script_preflight_succeeded": '
                    'bool(self._existing_script_preflight_succeeded),\n'
                    '                    "used_local_plugins": self._local_plugin_context != "None",\n'
                    '                }\n'
                    '                trace_path = self._route_trace_path()\n'
                    '                with suppress(Exception):\n'
                    '                    trace_path.parent.mkdir(parents=True, exist_ok=True)\n'
                    '                    with trace_path.open("a", encoding="utf-8") as handle:\n'
                    '                        handle.write(json.dumps(payload, ensure_ascii=True) + "\\n")\n',
             '093': '            def _route_status_message(self) -> str:\n'
                    '                decision = getattr(self, "_route_decision", None)\n'
                    '                if not isinstance(decision, RouteDecision):\n'
                    '                    return ""\n'
                    '                if decision.selected_route == ROUTE_SIMPLE_ANSWER:\n',
             '094': '                if decision.selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n',
             '095': '                        "Wrapper route: direct_existing_entrypoint. Run the existing workspace '
                    'entrypoint first, then use "\n'
                    '                        "model or tool reasoning only as fallback."\n',
             '096': '                if decision.selected_route == ROUTE_READ_ONLY_WORKSPACE:\n',
             '097': '                if decision.selected_route == ROUTE_TOOL_LOOP:\n'
                    '                    return "Wrapper route: tool_loop. This prompt explicitly favors grounded '
                    'local tool results."\n'
                    '                if decision.selected_route == ROUTE_ASK_USER_MISSING_INFO:\n'
                    '                    return "Wrapper route: ask_user_missing_info. Missing context appears more '
                    'important than immediate tool use."\n'
                    '                return "Wrapper route: retrieval_plus_model. Retrieve focused project context '
                    'before broader orchestration."\n',
             '098': '\n            def _run_existing_script_preflight(\n',
             '099': '            ) -> str:\n',
             '100': '                    return ""\n',
             '101': '                before_helpers = {\n'
                    '                    str(candidate.resolve()): candidate.stat().st_mtime\n'
                    '                    for candidate in _collect_browser_helper_candidates(self.workspace)\n'
                    '                }\n'
                    '                before_artifacts = _workspace_text_artifact_state(base, self.workspace)\n',
             '102': '                            direct_python = self._direct_workspace_python_plan()\n',
             '103': '                                return ""\n',
             '104': '',
             '105': '                                    base,\n'
                    '                                    self.workspace,\n'
                    '                                    command,\n'
                    '                                    SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                                    cancel_event=self._cancel_event,\n',
             '106': '                                runner_kind = "powershell"\n',
             '107': '                                    base,\n'
                    '                                    self.workspace,\n'
                    '                                    command,\n'
                    '                                    SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                                    cancel_event=self._cancel_event,\n',
             '108': '                                runner_kind = "shell"\n',
             '109': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '110': '                            runner_kind = "powershell"\n',
             '111': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '112': '                            runner_kind = "shell"\n',
             '113': '                                base,\n'
                    '                                self.workspace,\n'
                    '                                command,\n'
                    '                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                                cancel_event=self._cancel_event,\n',
             '114': '                            runner_kind = "shell"\n',
             '115': '                            return ""\n',
             '116': '                            return ""\n',
             '117': '                            base,\n'
                    '                            self.workspace,\n'
                    '                            command,\n'
                    '                            SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n'
                    '                            cancel_event=self._cancel_event,\n',
             '118': '                        runner_kind = "shell"\n',
             '119': '                        return ""\n',
             '120': '                    return ""\n',
             '121': '                stderr_text = str(getattr(result, "stderr_full", result.get("stderr", "")) or '
                    '"")\n'
                    '                stdout_text = str(getattr(result, "stdout_full", result.get("stdout", "")) or '
                    '"")\n',
             '122': '                if browser_helper_path is not None and not browser_helper_text:\n',
             '123': '                        browser_helper_text = browser_helper_path.read_text(encoding="utf-8", '
                    'errors="ignore")\n',
             '124': '                requested_browser_open = _prompt_requests_browser_open(prompt)\n',
             '125': '                browser_result: dict[str, Any] | None = None\n'
                    '                evidence_texts = [stderr_text, stdout_text, browser_helper_text]\n',
             '126': '                result_rows: list[dict[str, Any]] = []\n',
             '127': '                            result_rows.append(normalized_row)\n',
             '128': '                    SCRIPT_PREFLIGHT_MAX_URLS,\n',
             '129': '                self._existing_script_browser_urls = output_urls\n'
                    '                structured_rows: list[dict[str, Any]] = []\n'
                    '                for index, row in enumerate(result_rows):\n'
                    '                    enriched = _normalize_result_row(dict(row))\n'
                    '                    if index < len(output_urls) and _is_external_http_url(output_urls[index]):\n'
                    '                        enriched["url"] = output_urls[index]\n'
                    '                    structured_rows.append(enriched)\n'
                    '                direct_rows = structured_rows or [\n'
                    '                    {"url": url}\n'
                    '                    for url in output_urls\n'
                    '                ]\n'
                    '                self._existing_script_preflight_succeeded = return_code == 0\n'
                    '                expected_count = _expected_result_count(prompt)\n'
                    '                requested_fields = _requested_output_fields(prompt)\n',
             '130': '                target_count = expected_count or len(direct_rows)\n'
                    '                selected_rows = direct_rows[:target_count]\n'
                    '                selected_urls = _validated_external_urls(\n'
                    '                    [row.get("url", "") for row in selected_rows],\n',
             '131': '                    stdout_text,\n',
             '132': '                if (\n'
                    '                    requested_browser_open\n'
                    '                    and not reported_opened_urls\n'
                    '                    and len(selected_urls) >= target_count\n'
                    '                    and (\n'
                    '                        self._wsl_windows_interop_available()\n'
                    '                        or not (callable(getattr(base, "in_wsl", None)) and base.in_wsl())\n',
             '133': '',
             '134': '                        selected_urls,\n'
                    '                        SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,\n',
             '135': '                inline_browser_open_ran = browser_result is not None and int(\n'
                    '                    browser_result.get("return_code", browser_result.get("returncode", 0))\n'
                    '                ) == 0\n'
                    '                opened_urls = reported_opened_urls or (selected_urls if inline_browser_open_ran '
                    'else [])\n'
                    '                browser_open_ran = bool(opened_urls)\n',
             '136': '                    broker.provenance_urls.update(url.casefold() for url in output_urls if '
                    '_is_external_http_url(url))\n'
                    '                    categories = ["result_count"] if return_code == 0 and direct_rows else []\n',
             '137': '                    if return_code == 0 and changed_artifacts and count_requirement and '
                    'count_requirement[0] == "created_outputs":\n',
             '138': '                            "success": return_code == 0,\n',
             '139': '                            "status": "completed" if return_code == 0 else "failed",\n',
             '140': '                            "result_count": len(direct_rows),\n'
                    '                            "opened_count": len(opened_urls),\n'
                    '                            "urls": opened_urls,\n',
             '141': '                if (\n'
                    '                    return_code == 0\n'
                    '                    and target_count > 0\n'
                    '                    and len(direct_rows) >= target_count\n'
                    '                    and (not requires_url or len(selected_urls) >= target_count)\n'
                    '                    and (not requested_browser_open or browser_open_ran)\n'
                    '                    and (not requested_fields or _rows_have_required_fields(selected_rows, '
                    'requested_fields))\n'
                    '                ):\n'
                    '                    self._existing_script_direct_answer = _format_direct_result_answer(\n'
                    '                        selected_rows,\n'
                    '                        browser_open_ran=browser_open_ran,\n'
                    '                        requested_browser_open=requested_browser_open,\n'
                    '                        requested_fields=requested_fields,\n',
             '142': '                        (\n'
                    '                            "Wrapper direct path completed the existing-script task without '
                    'entering the model loop. "\n'
                    '                            f"Rows: {target_count}. Browser step: "\n'
                    '                            f"{\'verified entrypoint-reported opens\' if reported_opened_urls '
                    'else \'inline Start-Process with validated URLs\' if inline_browser_open_ran else \'none\'}."\n'
                    '                        ),\n',
             '143': '',
             '144': '                        f"Wrapper direct path: {entrypoint_label} exited {return_code} via '
                    '{runner_kind}; "\n'
                    '                        f"captured {len(direct_rows) or len(result_rows)} row(s) and '
                    '{len(output_urls)} distinct external URL(s)."\n',
             '145': '                verified_count = len(selected_urls) if requires_url else len(direct_rows)\n'
                    '                missing_count = max(0, target_count - verified_count)\n'
                    '                context_parts = [\n'
                    '                    "Wrapper preflight direct existing-entrypoint execution already ran before '
                    'the model loop.",\n'
                    '                    f"Entrypoint: {entrypoint_label}",\n'
                    '                    f"Launch path: {runner_kind}",\n'
                    '                    f"Exit code: {return_code}",\n'
                    '                    f"Requested result count: {target_count}",\n'
                    '                    f"Verified direct result count: {verified_count}",\n'
                    '                    f"Missing result count: {missing_count}",\n'
                    '                ]\n'
                    '                if browser_result is not None:\n'
                    '                    browser_exit = int(browser_result.get("return_code", '
                    'browser_result.get("returncode", 0)))\n'
                    '                    context_parts.append(f"Browser-opening helper exit code: {browser_exit}")\n'
                    '                if reported_opened_urls:\n'
                    '                    context_parts.append(\n'
                    '                        f"Entrypoint-reported browser opens verified conservatively: '
                    '{len(reported_opened_urls)}."\n'
                    '                    )\n'
                    '                    remaining_urls = [url for url in output_urls if url not in '
                    'set(reported_opened_urls)]\n'
                    '                    if remaining_urls:\n'
                    '                        context_parts.append("URLs not covered by the entrypoint\'s reported '
                    'browser-open count:")\n'
                    '                        '
                    'context_parts.append("\\n".join(remaining_urls[:SCRIPT_PREFLIGHT_MAX_URLS]))\n'
                    '                if structured_rows:\n'
                    '                    context_parts.append(\n'
                    '                        "Structured rows extracted from the script output and the generated '
                    'browser helper. "\n'
                    '                        "Preserve these exact fields and prefer these exact external URLs:"\n'
                    '                    )\n'
                    '                    context_parts.append(\n'
                    '                        "\\n".join(json.dumps(row, ensure_ascii=True) for row in '
                    'structured_rows[:SCRIPT_PREFLIGHT_MAX_URLS])\n'
                    '                    )\n'
                    '                elif output_urls:\n'
                    '                    context_parts.append("External URLs extracted from the generated browser '
                    'helper, in order:")\n'
                    '                    context_parts.append("\\n".join(output_urls[:SCRIPT_PREFLIGHT_MAX_URLS]))\n'
                    '                if stdout_text:\n'
                    '                    context_parts.append(\n'
                    '                        "Script stdout excerpt:\\n"\n'
                    '                        + _shorten(stdout_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)\n'
                    '                    )\n'
                    '                if stderr_text:\n'
                    '                    context_parts.append(\n'
                    '                        "Script stderr excerpt:\\n"\n'
                    '                        + _shorten(stderr_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)\n'
                    '                    )\n'
                    '                if browser_helper_path is not None and browser_helper_text:\n'
                    '                    context_parts.append(\n'
                    '                        "Generated browser-opening helper excerpt:\\n"\n'
                    '                        + _shorten(browser_helper_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)\n'
                    '                    )\n'
                    '                for artifact_path, artifact_text in changed_artifacts[:4]:\n'
                    '                    artifact_label = base.relative_path(artifact_path, self.workspace)\n'
                    '                    context_parts.append(\n'
                    '                        (\n'
                    '                            f"Changed output artifact {artifact_label} excerpt:\\n"\n'
                    '                            + _shorten(artifact_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS // 4)\n'
                    '                        )\n'
                    '                        if artifact_text\n'
                    '                        else f"Changed non-text or oversized output artifact: {artifact_label}"\n'
                    '                    )\n'
                    '                context_parts.append(\n'
                    '                    "Continue only the missing postconditions from these exact verified preflight '
                    'results. Preserve valid existing results, "\n'
                    '                    "avoid broad repository retrieval, and do not rerun the same entrypoint '
                    'unless new evidence shows that a rerun can "\n'
                    '                    "produce the missing result. Do not claim completion until every requested '
                    'quantity and side effect is verified."\n'
                    '                )\n'
                    '                return _shorten(\n'
                    '                    "\\n\\n".join(part for part in context_parts if part).strip(),\n'
                    '                    SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS,\n'
                    '                )\n',
             '146': '# <QUBITZ_SHARED_REFERENCE:wrapper:c4066f729f61e008>\n\n',
             '147': '\n'
                    '            def _direct_workspace_python_plan(self) -> tuple[Path, str] | None:\n'
                    '                return _select_direct_workspace_python(base, self.workspace)\n',
             '148': '                if selected_route == ROUTE_SIMPLE_ANSWER:\n',
             '149': '                        "- This is a simple direct question.\\n"\n'
                    '                        "- Answer directly.\\n"\n'
                    '                        "- Do not use tools unless they are truly necessary.\\n"\n',
             '150': '                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n',
             '151': '                        "- This is a foreground existing-project task.",\n',
             '152': '',
             '153': '                            "- Do not modify or replace an existing workspace entrypoint unless '
                    'the user explicitly asked for edits.",\n',
             '154': '',
             '155': '                if selected_route == ROUTE_READ_ONLY_WORKSPACE:\n',
             '156': '                        "- Read the minimum relevant files or retrieval context first.\\n"\n'
                    '                        "- Do not edit files or trigger heavier execution unless it becomes '
                    'necessary.\\n"\n',
             '157': '                    )\n'
                    '                if selected_route == ROUTE_TOOL_LOOP:\n'
                    '                    return (\n'
                    '                        "- This prompt explicitly leans on tools, commands, MCP, or '
                    'verification.\\n"\n'
                    '                        "- Prefer grounded local tool results over speculation.\\n"\n'
                    '                        "- Do not loop on the same failing route.\\n"\n'
                    '                    )\n'
                    '                if selected_route == ROUTE_ASK_USER_MISSING_INFO:\n'
                    '                    return (\n'
                    '                        "- Essential context appears to be missing.\\n"\n'
                    '                        "- Ask the user for the missing identifier, file, script, or target '
                    'before making edits or claims.\\n"\n',
             '158': '                if selected_route == ROUTE_SIMPLE_ANSWER:\n',
             '159': '                if selected_route == ROUTE_READ_ONLY_WORKSPACE:\n',
             '160': '                if selected_route != ROUTE_DIRECT_EXISTING_ENTRYPOINT:\n',
             '161': '                preflight_ready = bool(self._existing_script_preflight_context)\n',
             '162': '                primary_names = {\n',
             '163': '',
             '164': '                    primary_names.add("run_powershell_command")\n'
                    '                fallback_names = {\n',
             '165': '',
             '166': '',
             '167': '                    if name in mutation_names:\n'
                    '                        continue\n'
                    '                    if name.startswith("sandbox_") or name == "list_sandboxes":\n'
                    '                        continue\n'
                    '                    if preflight_ready and name in {"run_project_command", '
                    '"start_project_mcp_server", "list_project_mcp_tools"}:\n'
                    '                        continue\n',
             '168': '                    if name in primary_names:\n',
             '169': '                    elif name in fallback_names:\n',
             '170': '',
             '171': '# <QUBITZ_SHARED_REFERENCE:wrapper:d445027df34e5908>\n',
             '172': '                self._existing_script_browser_urls = []\n'
                    '                self._existing_script_preflight_succeeded = False\n'
                    '                self._existing_script_direct_answer = ""\n'
                    '                self._route_decision: RouteDecision | None = None\n'
                    '                self._route_fallback_chain: list[str] = []\n'
                    '                self._route_phase_timings: dict[str, float] = {}\n',
             '173': '',
             '174': '                run_started_at = time.perf_counter()\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:7a3188c220541554>\n'
                    '                route_started_at = time.perf_counter()\n'
                    '                self._route_decision = self._decide_route(user_prompt)\n'
                    '                self._route_fallback_chain = [self._route_decision.selected_route]\n'
                    '                self._route_phase_timings = {\n'
                    '                    "route_detect_seconds": round(time.perf_counter() - route_started_at, 4)\n'
                    '                }\n'
                    '                if self._route_decision.selected_route == ROUTE_ASK_USER_MISSING_INFO:\n',
             '175': '                bypass_retrieval = self._route_decision.selected_route == ROUTE_SIMPLE_ANSWER\n'
                    '                foreground_existing_script_task = self._route_decision.selected_route == '
                    'ROUTE_DIRECT_EXISTING_ENTRYPOINT\n'
                    '                read_only_workspace_task = self._route_decision.selected_route == '
                    'ROUTE_READ_ONLY_WORKSPACE\n',
             '176': '                    self.local_plugins.render_for_prompt(user_prompt)\n'
                    '                    if self.local_only_config.plugins_enabled and not bypass_retrieval and not '
                    'read_only_workspace_task\n',
             '177': '                route_status = self._route_status_message()\n'
                    '                if route_status:\n'
                    '                    self._emit(callback, "status", route_status)\n'
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
                    '                    )\n',
             '178': '                original_temperature = getattr(self.config, "temperature", None)\n'
                    '                original_top_p = getattr(self.config, "top_p", None)\n'
                    '                original_min_p = getattr(self.config, "min_p", None)\n'
                    '                original_repeat_penalty = getattr(self.config, "repeat_penalty", None)\n'
                    '                if bypass_retrieval:\n',
             '179': '',
             '180': '                    preflight_started_at = time.perf_counter()\n'
                    '                    self._existing_script_preflight_context = '
                    'self._run_existing_script_preflight(prompt, callback)\n'
                    '                    self._route_phase_timings["preflight_seconds"] = round(\n'
                    '                        time.perf_counter() - preflight_started_at,\n'
                    '                        4,\n'
                    '                    )\n'
                    '                    if self._existing_script_direct_answer:\n'
                    '                        direct_answer = self._existing_script_direct_answer\n'
                    '                        self._route_phase_timings["total_seconds"] = round(time.perf_counter() - '
                    'run_started_at, 4)\n'
                    '                        self._record_route_trace(\n'
                    '                            user_prompt,\n'
                    '                            "direct_preflight_answer",\n'
                    '                            answer=direct_answer,\n'
                    '                        )\n'
                    '                        self._simple_direct_mode = False\n'
                    '                        self._local_plugin_context = "None"\n'
                    '                        self._task_case_guidance = ""\n'
                    '                        self._runtime_fact_context = ""\n'
                    '                        self._existing_script_preflight_context = ""\n'
                    '                        self._existing_script_browser_urls = []\n'
                    '                        self._existing_script_preflight_succeeded = False\n'
                    '                        self._existing_script_direct_answer = ""\n'
                    '                        self._prompt_step_retry_override = None\n'
                    '                        self._route_decision = None\n'
                    '                        self._route_fallback_chain = []\n'
                    '                        self._route_phase_timings = {}\n'
                    '                        return direct_answer\n'
                    '                    if self._existing_script_preflight_context:\n'
                    '                        self._route_fallback_chain.append("model_loop_with_preflight_context")\n'
                    '                        prompt = (\n'
                    '                            f"{prompt.rstrip()}\\n\\n"\n'
                    '                            "Wrapper preflight execution context:\\n"\n'
                    '                            f"{self._existing_script_preflight_context}"\n'
                    '                        )\n'
                    '                        original_format_context = self.retriever.format_context\n'
                    '                        self._should_skip_repo_retrieval = lambda _prompt: True\n'
                    '                        self.memory.build_context = lambda _prompt: ""\n'
                    '                        self.retriever.format_context = lambda _prompt: '
                    'self._existing_script_preflight_context\n'
                    '                        self._emit(\n'
                    '                            callback,\n'
                    '                            "status",\n'
                    '                            "Wrapper focused continuation: preserving verified entrypoint results '
                    'and skipping broad retrieval while completing only missing postconditions.",\n'
                    '                        )\n',
             '181': '                        '
                    'self._route_fallback_chain.append("model_loop_with_read_only_file_context")\n',
             '182': '',
             '183': '',
             '184': '                    return self._prioritize_tools_for_prompt(prompt, tools)\n',
             '185': '                sampling_profile = _sampling_profile_for_route(user_prompt, '
                    'self._route_decision.selected_route)\n'
                    '                _apply_sampling_profile(self.config, sampling_profile)\n',
             '186': '',
             '187': '                    first_pass_started_at = time.perf_counter()\n',
             '188': '                    self._route_phase_timings["first_model_pass_seconds"] = round(\n'
                    '                        time.perf_counter() - first_pass_started_at,\n'
                    '                        4,\n'
                    '                    )\n'
                    '                    retry_step_cap, retry_label = self._retry_step_cap_for_failed_answer(prompt, '
                    'answer)\n',
             '189': '                        '
                    'self._route_fallback_chain.append(f"{self._route_decision.selected_route}_retry_{retry_step_cap}")\n',
             '190': '                        retry_started_at = time.perf_counter()\n',
             '191': '                        self._route_phase_timings["retry_model_pass_seconds"] = round(\n'
                    '                            time.perf_counter() - retry_started_at,\n'
                    '                            4,\n'
                    '                        )\n'
                    '                        if self._selected_route_name(prompt) == ROUTE_SIMPLE_ANSWER and '
                    'self._is_retryable_final_answer(prompt, answer):\n'
                    '                            '
                    'self._route_fallback_chain.append("simple_answer_unlimited_fallback")\n',
             '192': '                            fallback_started_at = time.perf_counter()\n',
             '193': '                            self._route_phase_timings["unlimited_fallback_seconds"] = round(\n'
                    '                                time.perf_counter() - fallback_started_at,\n'
                    '                                4,\n'
                    '                            )\n'
                    '                    if self._existing_script_browser_urls:\n'
                    '                        answer = _rewrite_answer_urls_from_helper(answer, '
                    'self._existing_script_browser_urls)\n'
                    '                    self._route_phase_timings["total_seconds"] = round(time.perf_counter() - '
                    'run_started_at, 4)\n'
                    '                    self._record_route_trace(user_prompt, "answered", answer=answer)\n',
             '194': '                except Exception as exc:\n'
                    '                    self._route_phase_timings["total_seconds"] = round(time.perf_counter() - '
                    'run_started_at, 4)\n'
                    '                    self._record_route_trace(\n'
                    '                        user_prompt,\n'
                    '                        "error",\n'
                    '                        error=f"{type(exc).__name__}: {exc}",\n'
                    '                    )\n'
                    '                    raise\n',
             '195': '                    if original_temperature is not None:\n'
                    '                        self.config.temperature = original_temperature\n'
                    '                    if original_top_p is not None:\n'
                    '                        self.config.top_p = original_top_p\n'
                    '                    if original_min_p is not None:\n'
                    '                        self.config.min_p = original_min_p\n'
                    '                    if original_repeat_penalty is not None:\n'
                    '                        self.config.repeat_penalty = original_repeat_penalty\n',
             '196': '                    self._simple_direct_mode = False\n',
             '197': '',
             '198': '                    self._existing_script_browser_urls = []\n'
                    '                    self._existing_script_preflight_succeeded = False\n'
                    '                    self._existing_script_direct_answer = ""\n',
             '199': '                    self._route_decision = None\n'
                    '                    self._route_fallback_chain = []\n'
                    '                    self._route_phase_timings = {}\n',
             '200': '                overlay_lines = [\n'
                    '                    "",\n'
                    '                    "Local-only overlay for this copy:",\n'
                    '                    "- Use only local files, local shells, local MCP servers, and local model '
                    'runtimes.",\n'
                    '                    "- Prefer the shortest direct project path before broader tool '
                    'orchestration.",\n'
                    '                    "- Prefer direct interpreter paths over activation-script chains when a local '
                    'project interpreter is available.",\n'
                    '                    "- If the wrapper reports Windows-executable interop is available, prefer '
                    'that direct path instead of assuming WSL-only execution.",\n'
                    '                    (\n'
                    '                        "- A direct existing-script preflight already ran for this task; use its '
                    'exact outputs and "\n'
                    '                        "prefer its captured external URLs."\n'
                    '                        if self._existing_script_preflight_context\n'
                    '                        else "- If an existing script was already executed by the wrapper, prefer '
                    'its exact outputs over rerunning it."\n'
                    '                    ),\n'
                    '                ]\n'
                    '                if self._runtime_fact_context:\n'
                    '                    overlay_lines.extend(\n'
                    '                        [\n'
                    '                            "",\n'
                    '                            "Authoritative runtime facts for this task:",\n'
                    '                            self._runtime_fact_context,\n'
                    '                        ]\n'
                    '                    )\n'
                    '                if self._task_case_guidance:\n'
                    '                    overlay_lines.extend(\n'
                    '                        [\n'
                    '                            "",\n'
                    '                            "Prompt-specific routing guidance:",\n'
                    '                            self._task_case_guidance,\n'
                    '                        ]\n'
                    '                    )\n'
                    '                if self._local_plugin_context != "None":\n'
                    '                    overlay_lines.extend(\n'
                    '                        [\n'
                    '                            "",\n'
                    '                            "Active local plugins:",\n'
                    '                            self._local_plugin_context,\n'
                    '                        ]\n'
                    '                    )\n',
             '201': '                    overlay_lines.extend(["", "Current memory context:", memory_context])\n',
             '202': '                    overlay_lines.extend(["", "Active local skill context:", '
                    'active_skill_context])\n',
             '203': '                    overlay_lines.extend(["", "Condensed earlier conversation summary:", '
                    'history_summary])\n'
                    '                overlay = "\\n".join(overlay_lines).rstrip()\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:28c585c30a060bef>\n',
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
             '220': '\n\n',
             '221': '# <QUBITZ_SHARED_REFERENCE:wrapper:b3be058936b061da>\n'
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
                    '\n',
             '222': '# <QUBITZ_SHARED_REFERENCE:wrapper:38c3c563aff17f5f>\n\n',
             '223': '',
             '224': '\n',
             '225': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:5a6c821dfda3f3b6>\n\n',
             '226': '\n',
             '227': '# <QUBITZ_SHARED_REFERENCE:wrapper:79589de5517a2378>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:2e6a6d3436d3e115>\n',
             '228': '\n\n',
             '229': '\n',
             '230': '',
             '231': '\n',
             '232': '    _install_daily_memory_store(app)\n',
             '233': '',
             '234': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:7873f4bb2ec60cea>\n',
             '235': '# <QUBITZ_SHARED_REFERENCE:wrapper:348943cecf708d86>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:f8c884b1e07a766b>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:0353f3c875f1b913>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:8f4df2dc4865730b>\n',
             '236': '# <QUBITZ_SHARED_REFERENCE:wrapper:c3fe67b7d0e7fc9f>\n',
             '237': '# <QUBITZ_SHARED_REFERENCE:wrapper:4135c7f338fd931f>\n\n',
             '238': '# <QUBITZ_SHARED_REFERENCE:wrapper:6bbfcf6d5e2c8cfe>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:5b2df0d7db4eb9ef>\n',
             '239': '# <QUBITZ_SHARED_REFERENCE:wrapper:b643a1100f5399fb>\n\n',
             '240': '',
             '241': '',
             '242': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:6cc1c8966d6d4a8d>\n',
             '243': '',
             '244': '',
             '245': '# <QUBITZ_SHARED_REFERENCE:wrapper:c01591ef2d01e166>\n',
             '246': '# <QUBITZ_SHARED_REFERENCE:wrapper:dba7a921146763ff>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:32b3242c1d30f2d6>\n'
                    '\n'
                    '# <QUBITZ_SHARED_REFERENCE:wrapper:39fd337c87c00874>\n',
             '247': '# <QUBITZ_SHARED_REFERENCE:wrapper:9fe212a961e2f06d>\n',
             '248': '# <QUBITZ_SHARED_REFERENCE:wrapper:5c34096f33ce4e08>\n',
             '249': '',
             '250': '\n# <QUBITZ_SHARED_REFERENCE:wrapper:74fb6e2a33e4d698>\n',
             '251': '\n',
             '252': '\n',
             '253': 'QWEN3_NEXT_GPU_INDICES = (0, 1)\n'
                    'QWEN3_NEXT_GPU_SAFETY_MARGIN_MIB = 1536\n'
                    'QWEN3_NEXT_MIN_RUNTIME_HEADROOM_MIB = 512\n'
                    'QWEN3_NEXT_MIN_FIT_CONTEXT = 131072\n'
                    'QWEN3_NEXT_REQUIRED_LLAMA_FLAGS = (\n'
                    '    "--device",\n'
                    '    "--fit",\n'
                    '    "--fit-ctx",\n'
                    '    "--fit-target",\n'
                    '    "--split-mode",\n'
                    ')\n'
                    '\n'
                    '\n'
                    'def _qwen3_next_gpu_inventory() -> list[dict[str, Any]]:\n'
                    '    candidates = [shutil.which("nvidia-smi")]\n'
                    '    if os.name == "posix":\n'
                    '        candidates.append("/usr/lib/wsl/lib/nvidia-smi")\n'
                    '    else:\n'
                    '        candidates.append(\n'
                    '            str(Path(os.environ.get("WINDIR", r"C:\\Windows")) / "System32" / "nvidia-smi.exe")\n'
                    '        )\n'
                    '    executable = next(\n'
                    '        (str(candidate) for candidate in candidates if candidate and Path(candidate).is_file()),\n'
                    '        "",\n'
                    '    )\n'
                    '    if not executable:\n'
                    '        raise RuntimeError(\n'
                    '            "Qwen3-Coder-Next multi-GPU launch requires nvidia-smi to verify both CUDA devices."\n'
                    '        )\n'
                    '    query = subprocess.run(\n'
                    '        [\n'
                    '            executable,\n'
                    '            "--query-gpu=index,name,uuid,pci.bus_id,memory.total,memory.free",\n'
                    '            "--format=csv,noheader,nounits",\n'
                    '        ],\n'
                    '        capture_output=True,\n'
                    '        text=True,\n'
                    '        timeout=20,\n'
                    '        check=False,\n'
                    '    )\n'
                    '    if query.returncode != 0:\n'
                    '        detail = (query.stderr or query.stdout).strip()\n'
                    '        raise RuntimeError(\n'
                    '            "Qwen3-Coder-Next multi-GPU launch could not query CUDA memory"\n'
                    '            + (f": {detail}" if detail else ".")\n'
                    '        )\n'
                    '    devices: list[dict[str, Any]] = []\n'
                    '    for raw_line in query.stdout.splitlines():\n'
                    '        parts = [part.strip() for part in raw_line.split(",", maxsplit=5)]\n'
                    '        if len(parts) != 6:\n'
                    '            continue\n'
                    '        try:\n'
                    '            index = int(parts[0])\n'
                    '            total_mib = int(parts[4])\n'
                    '            free_mib = int(parts[5])\n'
                    '        except ValueError:\n'
                    '            continue\n'
                    '        devices.append(\n'
                    '            {\n'
                    '                "index": index,\n'
                    '                "name": parts[1],\n'
                    '                "uuid": parts[2],\n'
                    '                "pci_bus_id": parts[3],\n'
                    '                "total_mib": total_mib,\n'
                    '                "free_mib": min(total_mib, free_mib),\n'
                    '            }\n'
                    '        )\n'
                    '    by_index = {int(device["index"]): device for device in devices}\n'
                    '    missing = [index for index in QWEN3_NEXT_GPU_INDICES if index not in by_index]\n'
                    '    if missing:\n'
                    '        raise RuntimeError(\n'
                    '            "Qwen3-Coder-Next requires CUDA devices 0 and 1 for its two-GPU layer split; "\n'
                    '            f"missing device index(es): {\', \'.join(str(index) for index in missing)}."\n'
                    '        )\n'
                    '    return [by_index[index] for index in QWEN3_NEXT_GPU_INDICES]\n'
                    '\n'
                    '\n'
                    'def _qwen3_next_split_plan(\n'
                    '    devices: Sequence[dict[str, Any]],\n'
                    '    model_size_mib: int,\n'
                    ') -> dict[str, Any]:\n'
                    '    if len(devices) != len(QWEN3_NEXT_GPU_INDICES):\n'
                    '        raise RuntimeError("Qwen3-Coder-Next requires exactly two selected CUDA devices.")\n'
                    '    usable_mib = [\n'
                    '        max(0, int(device["free_mib"]) - QWEN3_NEXT_GPU_SAFETY_MARGIN_MIB)\n'
                    '        for device in devices\n'
                    '    ]\n'
                    '    if any(value <= 0 for value in usable_mib):\n'
                    '        raise RuntimeError(\n'
                    '            "Qwen3-Coder-Next cannot reserve its per-GPU safety margin because a selected CUDA "\n'
                    '            "device has insufficient free VRAM."\n'
                    '        )\n'
                    '    if model_size_mib <= 0:\n'
                    '        raise RuntimeError("Qwen3-Coder-Next GGUF size must be positive.")\n'
                    '    minimum_cpu_resident_mib = max(\n'
                    '        0,\n'
                    '        model_size_mib\n'
                    '        + QWEN3_NEXT_MIN_RUNTIME_HEADROOM_MIB\n'
                    '        - sum(usable_mib),\n'
                    '    )\n'
                    '    return {\n'
                    '        "devices": [dict(device) for device in devices],\n'
                    '        "usable_mib": usable_mib,\n'
                    '        "fit_target": ",".join(\n'
                    '            str(QWEN3_NEXT_GPU_SAFETY_MARGIN_MIB) for _ in devices\n'
                    '        ),\n'
                    '        "minimum_cpu_resident_mib": minimum_cpu_resident_mib,\n'
                    '    }\n'
                    '\n'
                    '\n'
                    'def _install_qwen3_next_multi_gpu_layer_split(app: LocalOnlyApp) -> None:\n'
                    '    """Fit Qwen3-Coder-Next across both GPUs and leave only unavoidable overflow in system RAM."""\n'
                    '    base = app.base\n'
                    '    server_cls = getattr(base, "LlamaCppServerProcess", None)\n'
                    '    if server_cls is None or getattr(\n'
                    '        server_cls,\n'
                    '        "_qubitz_qwen3_next_multi_gpu_installed",\n'
                    '        False,\n'
                    '    ):\n'
                    '        return\n'
                    '    original_launch_command = server_cls._launch_command\n'
                    '    replaced_options = {\n'
                    '        "--device",\n'
                    '        "--fit",\n'
                    '        "--fit-ctx",\n'
                    '        "--fit-target",\n'
                    '        "--main-gpu",\n'
                    '        "--n-gpu-layers",\n'
                    '        "--split-mode",\n'
                    '        "--tensor-split",\n'
                    '    }\n'
                    '\n'
                    '    def _local_model_path(raw_path: str) -> Path:\n'
                    '        match = re.match(r"^([A-Za-z]):[\\\\/](.*)$", str(raw_path or ""))\n'
                    '        if os.name == "posix" and match is not None:\n'
                    '            drive, remainder = match.groups()\n'
                    '            return Path(f"/mnt/{drive.lower()}/" + remainder.replace("\\\\", "/"))\n'
                    '        return Path(raw_path)\n'
                    '\n'
                    '    def _model_size_mib(model_path: str) -> int:\n'
                    '        try:\n'
                    '            size = _local_model_path(model_path).stat().st_size\n'
                    '        except OSError as exc:\n'
                    '            raise RuntimeError(\n'
                    '                f"Qwen3-Coder-Next could not inspect the GGUF before planning its GPU split: {exc}"\n'
                    '            ) from exc\n'
                    '        return (size + 1024**2 - 1) // 1024**2\n'
                    '\n'
                    '    def _validate_runtime_capabilities() -> None:\n'
                    '        help_cache = getattr(server_cls, "_qubitz_server_help_cache", {})\n'
                    '        help_text = "\\n".join(str(value) for value in help_cache.values())\n'
                    '        missing = [flag for flag in QWEN3_NEXT_REQUIRED_LLAMA_FLAGS if flag not in help_text]\n'
                    '        if missing:\n'
                    '            raise RuntimeError(\n'
                    '                "The selected llama.cpp runtime lacks Qwen3-Coder-Next\'s required multi-GPU "\n'
                    '                f"capabilities: {\', \'.join(missing)}."\n'
                    '            )\n'
                    '\n'
                    '    def _split_arguments(model_path: str) -> tuple[list[str], dict[str, Any]]:\n'
                    '        _validate_runtime_capabilities()\n'
                    '        plan = _qwen3_next_split_plan(\n'
                    '            _qwen3_next_gpu_inventory(),\n'
                    '            _model_size_mib(model_path),\n'
                    '        )\n'
                    '        return (\n'
                    '            [\n'
                    '                "--device",\n'
                    '                "CUDA0,CUDA1",\n'
                    '                "--split-mode",\n'
                    '                "layer",\n'
                    '                "--fit",\n'
                    '                "on",\n'
                    '                "--fit-ctx",\n'
                    '                str(QWEN3_NEXT_MIN_FIT_CONTEXT),\n'
                    '                "--fit-target",\n'
                    '                str(plan["fit_target"]),\n'
                    '            ],\n'
                    '            plan,\n'
                    '        )\n'
                    '\n'
                    '    def _strip_raw_options(arguments: list[str]) -> list[str]:\n'
                    '        cleaned: list[str] = []\n'
                    '        index = 0\n'
                    '        while index < len(arguments):\n'
                    '            argument = str(arguments[index])\n'
                    '            matched = next(\n'
                    '                (\n'
                    '                    option\n'
                    '                    for option in replaced_options\n'
                    '                    if argument == option or argument.startswith(f"{option}=")\n'
                    '                ),\n'
                    '                None,\n'
                    '            )\n'
                    '            if matched is None:\n'
                    '                cleaned.append(arguments[index])\n'
                    '                index += 1\n'
                    '                continue\n'
                    '            index += 1 if argument.startswith(f"{matched}=") else 2\n'
                    '        return cleaned\n'
                    '\n'
                    '    def _launch_command_with_qwen3_next_split(self: Any) -> list[str]:\n'
                    '        command = list(original_launch_command(self))\n'
                    '        if not command:\n'
                    '            return command\n'
                    '        head_name = str(command[0]).replace("\\\\", "/").rsplit("/", 1)[-1].lower()\n'
                    '        if head_name == "powershell.exe":\n'
                    '            encoded_index = -1\n'
                    '            for index in range(1, len(command) - 1):\n'
                    '                if str(command[index]).lower() == "-encodedcommand":\n'
                    '                    encoded_index = index + 1\n'
                    '                    break\n'
                    '            if encoded_index < 0:\n'
                    '                raise RuntimeError(\n'
                    '                    "Qwen3-Coder-Next could not locate the encoded llama.cpp launch command."\n'
                    '                )\n'
                    '            import base64\n'
                    '\n'
                    '            try:\n'
                    '                script = base64.b64decode(str(command[encoded_index])).decode("utf-16-le")\n'
                    '            except (ValueError, UnicodeDecodeError) as exc:\n'
                    '                raise RuntimeError(\n'
                    '                    "Qwen3-Coder-Next could not decode the llama.cpp launch command."\n'
                    '                ) from exc\n'
                    '            model_match = re.search(r"\'--model\'\\s+\'([^\']+)\'", script)\n'
                    '            if model_match is None:\n'
                    '                raise RuntimeError("Qwen3-Coder-Next could not resolve its GGUF launch path.")\n'
                    '            split_args, plan = _split_arguments(model_match.group(1))\n'
                    '            new_script = script\n'
                    '            for option in replaced_options:\n'
                    '                new_script = re.sub(\n'
                    '                    rf"\\s*\'{re.escape(option)}\'(?:\\s+\'[^\']*\'|=[^\\s]+)",\n'
                    '                    "",\n'
                    '                    new_script,\n'
                    '                )\n'
                    '            new_script = new_script.rstrip() + "".join(\n'
                    '                f" \'{argument}\'" for argument in split_args\n'
                    '            )\n'
                    '            command[encoded_index] = base64.b64encode(\n'
                    '                new_script.encode("utf-16-le")\n'
                    '            ).decode("ascii")\n'
                    '            self._qubitz_qwen3_next_multi_gpu_plan = plan\n'
                    '            return command\n'
                    '\n'
                    '        try:\n'
                    '            model_index = command.index("--model")\n'
                    '            model_path = str(command[model_index + 1])\n'
                    '        except (ValueError, IndexError) as exc:\n'
                    '            raise RuntimeError("Qwen3-Coder-Next could not resolve its GGUF launch path.") from exc\n'
                    '        split_args, plan = _split_arguments(model_path)\n'
                    '        command = _strip_raw_options(command)\n'
                    '        command.extend(split_args)\n'
                    '        self._qubitz_qwen3_next_multi_gpu_plan = plan\n'
                    '        return command\n'
                    '\n'
                    '    server_cls._launch_command = _launch_command_with_qwen3_next_split\n'
                    '    server_cls._qubitz_qwen3_next_multi_gpu_installed = True\n'
                    '\n'
                    '\n'
                    '_install_qwen3_next_multi_gpu_layer_split(_APP)\n',
             '254': '',
             '255': '',
             '256': ''},
 'base': {'000': '',
          '001': "DEFAULT_MODEL = 'unsloth/Qwen3-Coder-Next-GGUF'\n"
                 "DEFAULT_HF_GGUF_REPO_ID = 'unsloth/Qwen3-Coder-Next-GGUF'\n"
                 "Q4_GGUF_MODEL_FILENAME = 'Qwen3-Coder-Next-UD-Q4_K_S.gguf'\n"
                 "DEFAULT_GGUF_MODEL_LABEL = 'Qwen3-Coder-Next-UD-Q4_K_S'\n"
                 "DEFAULT_GGUF_MODEL_FILENAME = 'Qwen3-Coder-Next-UD-Q4_K_S.gguf'\n",
          '002': 'DEFAULT_LLAMACPP_MODEL_READY_TIMEOUT_SECONDS = 300.0\n',
          '003': 'DEFAULT_NUM_CTX = 262144\nDEFAULT_NUM_PREDICT = 16384\n',
          '004': '        workspace / ".cache" / "models" / "unsloth" / "Qwen3-Coder-Next-GGUF" / '
                 'preferred_filename,\n',
          '005': '    local_dir = workspace / ".cache" / "models" / "unsloth" / "Qwen3-Coder-Next-GGUF"\n',
          '006': '            "Local path to the fixed Q4 GGUF model. Must point to "\n',
          '007': ''}}

_initialize_variant(globals(), _QUBITZ_VARIANT_SPEC)
