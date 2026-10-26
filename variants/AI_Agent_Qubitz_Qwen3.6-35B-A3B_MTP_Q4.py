"""Variant-specific configuration and source for the shared Qubitz runtime."""
from __future__ import annotations

import sys as _qubitz_bootstrap_sys
from pathlib import Path as _QubitzBootstrapPath

_qubitz_bootstrap_root = _QubitzBootstrapPath(__file__).resolve().parent
if _qubitz_bootstrap_root.name == 'variants':
    _qubitz_bootstrap_root = _qubitz_bootstrap_root.parent
if str(_qubitz_bootstrap_root) not in _qubitz_bootstrap_sys.path:
    _qubitz_bootstrap_sys.path.insert(0, str(_qubitz_bootstrap_root))

from qubitz_runtime import initialize_variant as _initialize_variant

_QUBITZ_VARIANT_SPEC = {'schema_version': 2,
 'filename': 'AI_Agent_Qubitz_Qwen3.6-35B-A3B_MTP_Q4.py',
 'wrapper': {'000': '',
             '001': '',
             '002': '',
             '003': 'SCRIPT_PREFLIGHT_TIMEOUT_SECONDS = 300\nSCRIPT_PREFLIGHT_MAX_URLS = 64\n',
             '004': '',
             '005': 'SCRIPT_PREFLIGHT_BROWSER_GLOB_PATTERNS = ("open*.ps1", "open*.bat", "open*.cmd")\n',
             '006': '    if '
                    're.search(r"\\b(build|compile|test|lint|run|start|serve|deploy|fetch|get|list|open|read|inspect|explain|summarize|find|search|convert|train|evaluate|benchmark|install|update|fix|create|delete|rename)\\b", '
                    'lowered):\n',
             '007': '    return any(marker in lowered for marker in ("produce one concrete verified project result", '
                    '"produce a project result", "use the active workspace", "use this project", "use the project", '
                    '"use this workspace", "use the workspace"))\n',
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
             '020': '',
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
             '023': '',
             '024': '',
             '025': '',
             '026': '\n',
             '027': '\n',
             '028': '',
             '029': '',
             '030': '\n\n# <QUBITZ_SHARED_REFERENCE:wrapper:683850aa594ab1e9>\n',
             '031': '',
             '032': '',
             '033': '',
             '034': '',
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
             '067': '',
             '068': '\n'
                    '_EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(\n'
                    '    "DEFAULT_NUM_CTX = 202752\\n",\n'
                    '    "DEFAULT_NUM_CTX = 262144\\n",\n'
                    '    1,\n'
                    ')\n'
                    '_EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(\n'
                    '    \'"unsloth" / "Qwen3.6-35B-A3B-GGUF"\',\n'
                    '    \'"unsloth" / "Qwen3.6-35B-A3B-MTP-GGUF"\',\n'
                    ')\n'
                    '\n'
                    '\n',
             '069': '',
             '070': ')\n',
             '071': '\n',
             '072': '',
             '073': '',
             '074': '',
             '075': '',
             '076': '\n'
                    '\n'
                    'def _patch_model_identity(base: Any) -> None:\n'
                    '    base.DEFAULT_MODEL = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"\n'
                    '    base.DEFAULT_HF_GGUF_REPO_ID = "unsloth/Qwen3.6-35B-A3B-MTP-GGUF"\n'
                    '    base.Q4_GGUF_MODEL_FILENAME = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"\n'
                    '    base.DEFAULT_GGUF_MODEL_LABEL = "Qwen3.6-35B-A3B-MTP-UD-Q4_K_M"\n'
                    '    base.DEFAULT_GGUF_MODEL_FILENAME = "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"\n',
             '077': '',
             '078': '        _patch_model_identity(self.base)\n',
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
             '098': '            def _run_existing_script_preflight(\n',
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
             '105': '                                    base, self.workspace, command, '
                    'SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, cancel_event=self._cancel_event\n',
             '106': '                                runner_kind = "powershell"\n',
             '107': '                                    base, self.workspace, command, '
                    'SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, cancel_event=self._cancel_event\n',
             '108': '                                runner_kind = "shell"\n',
             '109': '                                base, self.workspace, command, SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, '
                    'cancel_event=self._cancel_event\n',
             '110': '                            runner_kind = "powershell"\n',
             '111': '                                base, self.workspace, command, SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, '
                    'cancel_event=self._cancel_event\n',
             '112': '                            runner_kind = "shell"\n',
             '113': '                                base, self.workspace, command, SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, '
                    'cancel_event=self._cancel_event\n',
             '114': '                            runner_kind = "shell"\n',
             '115': '                            return ""\n',
             '116': '                            return ""\n',
             '117': '                            base, self.workspace, command, SCRIPT_PREFLIGHT_TIMEOUT_SECONDS, '
                    'cancel_event=self._cancel_event\n',
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
             '148': '                if selected_route == "simple_answer":\n',
             '149': '                        "- This is a simple direct question.\\n"\n'
                    '                        "- Answer directly.\\n"\n'
                    '                        "- Do not use tools unless they are truly necessary.\\n"\n',
             '150': '                if selected_route == "direct_existing_entrypoint":\n',
             '151': '                        "- This is a foreground existing-project task.",\n',
             '152': '',
             '153': '                            "- Do not modify or replace an existing workspace entrypoint unless '
                    'the user explicitly asked for edits.",\n',
             '154': '',
             '155': '                if selected_route == "read_only_workspace":\n',
             '156': '                        "- Use the wrapper-provided file excerpts first when present.\\n"\n'
                    '                        "- Do not edit files, run commands, or invoke broad tools unless the '
                    'provided file context is insufficient.\\n"\n',
             '157': '',
             '158': '                if selected_route == "simple_answer":\n',
             '159': '                if selected_route == "read_only_workspace":\n',
             '160': '                if selected_route != "direct_existing_entrypoint":\n',
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
                    '                self._locked_route_name = ""\n'
                    '                self._locked_route_reason = ""\n',
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
                    '                read_only_workspace_task = selected_route == "read_only_workspace"\n',
             '178': '                original_temperature = getattr(self.config, "temperature", None)\n'
                    '                original_top_p = getattr(self.config, "top_p", None)\n'
                    '                original_min_p = getattr(self.config, "min_p", None)\n'
                    '                original_repeat_penalty = getattr(self.config, "repeat_penalty", None)\n'
                    '                if bypass_retrieval:\n',
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
                    '                    )\n'
                    '                    self._existing_script_preflight_context = '
                    'self._run_existing_script_preflight(user_prompt, callback)\n'
                    '                    if self._existing_script_direct_answer:\n'
                    '                        direct_answer = self._existing_script_direct_answer\n'
                    '                        self._simple_direct_mode = False\n'
                    '                        self._local_plugin_context = "None"\n'
                    '                        self._task_case_guidance = ""\n'
                    '                        self._runtime_fact_context = ""\n'
                    '                        self._existing_script_preflight_context = ""\n'
                    '                        self._existing_script_browser_urls = []\n'
                    '                        self._existing_script_preflight_succeeded = False\n'
                    '                        self._existing_script_direct_answer = ""\n'
                    '                        self._prompt_step_retry_override = None\n'
                    '                        self._clear_locked_route()\n'
                    '                        return direct_answer\n'
                    '                    if self._existing_script_preflight_context:\n'
                    '                        prompt = (\n'
                    '                            f"{user_prompt.rstrip()}\\n\\n"\n'
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
             '193': '                    if self._existing_script_browser_urls:\n'
                    '                        answer = _rewrite_answer_urls_from_helper(answer, '
                    'self._existing_script_browser_urls)\n',
             '194': '',
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
             '199': '                    self._clear_locked_route()\n',
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
             '229': '\n',
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
          '001': "DEFAULT_MODEL = 'unsloth/Qwen3.6-35B-A3B-MTP-GGUF'\n"
                 "DEFAULT_HF_GGUF_REPO_ID = 'unsloth/Qwen3.6-35B-A3B-MTP-GGUF'\n"
                 "Q4_GGUF_MODEL_FILENAME = 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'\n"
                 "DEFAULT_GGUF_MODEL_LABEL = 'Qwen3.6-35B-A3B-MTP-UD-Q4_K_M'\n"
                 "DEFAULT_GGUF_MODEL_FILENAME = 'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf'\n",
          '002': 'DEFAULT_LLAMACPP_MODEL_READY_TIMEOUT_SECONDS = 300.0\n',
          '003': 'DEFAULT_NUM_CTX = 262144\nDEFAULT_NUM_PREDICT = 16384\n',
          '004': '        workspace / ".cache" / "models" / "unsloth" / "Qwen3.6-35B-A3B-GGUF" / preferred_filename,\n',
          '005': '    local_dir = workspace / ".cache" / "models" / "unsloth" / "Qwen3.6-35B-A3B-GGUF"\n',
          '006': '            "Local path to the fixed Q4 GGUF model. Must point to "\n',
          '007': ''}}

_initialize_variant(globals(), _QUBITZ_VARIANT_SPEC)
