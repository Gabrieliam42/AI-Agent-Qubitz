from __future__ import annotations

# Standalone local-only wrapper with vendored support code and embedded base module.
# This file intentionally embeds both the wrapper implementation and its
# corresponding base app so it can run independently.

import ast
import asyncio
import difflib
import hashlib
import json
import locale
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
import threading
import time
import tomllib
import uuid
import types
import webbrowser
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence, TypedDict

LOCAL_ONLY_CONFIG_NAME = "local_only.toml"
LOCAL_ONLY_DIR_NAME = ".qubitz"
LOCAL_ONLY_PLUGINS_DIR = "plugins"
LOCAL_ONLY_MAX_DIFF_CHARS = 20000
SIMPLE_DIRECT_QUESTION_PREFIXES = (
    "what is ",
    "what's ",
    "who is ",
    "who's ",
    "where is ",
    "when is ",
    "when did ",
    "which ",
    "name ",
)
SIMPLE_DIRECT_QUESTION_BLOCKERS = (
    "workspace",
    "repo",
    "repository",
    "file",
    "files",
    "script",
    "server",
    "tool",
    "tools",
    "command",
    "powershell",
    "python",
    "code",
    "function",
    "class",
    "module",
    "mcp",
    "bug",
    "fix",
    "implement",
    "create",
    "edit",
    "delete",
    "refactor",
    "optimize",
    "package",
    "install",
    "run ",
)
WORKSPACE_CONTEXT_HINTS = (
    "this project",
    "the project",
    "current project",
    "this workspace",
    "the workspace",
    "current workspace",
    "this repo",
    "the repo",
    "this repository",
    "the repository",
    "this codebase",
    "the codebase",
    "current codebase",
    "this app",
    "the app",
    "this application",
    "the application",
    "in this project",
    "in the project",
    "in this workspace",
    "in the workspace",
    "in this repo",
    "in the repo",
    "in this repository",
    "in the repository",
    "in this codebase",
    "in the codebase",
    "how does this project",
    "what does this project",
    "what does the project",
    "what is this project",
    "what is the project",
)
SCRIPT_FILE_SUFFIXES = (".py", ".ps1", ".sh", ".bat", ".cmd")
MAKEFILE_CANDIDATE_NAMES = ("GNUmakefile", "Makefile", "makefile")
FOREGROUND_EXISTING_SCRIPT_HINTS = (
    "existing script",
    "existing workspace setup",
    "already set up",
    "already setup",
    "do not create a new script",
    "use the existing",
    "preferred existing script",
)
IMPLICIT_EXISTING_ENTRYPOINT_HINTS = (
    "use this project as it already exists",
    "use the project as it already exists",
    "use this workspace as it already exists",
    "use the workspace as it already exists",
    "existing project script",
    "existing project helper",
    "existing project entrypoint",
    "existing project command",
    "do not create a replacement script",
    "do not reimplement it",
    "do not fix the project",
)
PROJECT_LAUNCH_REQUEST_PATTERN = re.compile(
    r"\b(?:run|execute|start|launch)\s+(?:up\s+)?(?:this|the(?:\s+current)?|current)\s+"
    r"(?:project|workspace|repo(?:sitory)?|app(?:lication)?)\b",
    re.IGNORECASE,
)
PROJECT_USE_TASK_PREFIX_PATTERN = re.compile(
    r"\b(?P<verb>run|execute|start|launch|use)\s+(?:this|the(?:\s+current)?|current)\s+"
    r"(?:project|workspace|repo(?:sitory)?|app(?:lication)?)\b",
    re.IGNORECASE,
)
PROJECT_LAUNCH_EXPLANATION_PATTERN = re.compile(
    r"\b(?:how\s+(?:do|can|should|would)\s+[^\n?]{0,80}|how\s+to\s+|"
    r"what\s+(?:command|commands|steps?)\s+[^\n?]{0,80})(?:run|execute|start|launch)\b",
    re.IGNORECASE,
)
PROJECT_LAUNCH_BENIGN_TRAILING_PATTERN = re.compile(
    r"^\s*[,;:-]?\s*(?:(?:now|please|for me|in the background|"
    r"and\s+(?:keep|leave)\s+it\s+running)\s*)*[.!?]?\s*$",
    re.IGNORECASE,
)
PROJECT_LAUNCH_BENIGN_LEADING_PATTERN = re.compile(
    r"^\s*(?:(?:please|can|could|would|will)\s+)*(?:you\s+)?(?:please\s+)?$",
    re.IGNORECASE,
)
PROJECT_LAUNCH_LOCAL_URL_PATTERN = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d+)?[^\s'\"<>]*",
    re.IGNORECASE,
)
PROJECT_LAUNCH_FATAL_LOG_PATTERN = re.compile(
    r"(?:^|\n)\s*(?:Traceback \(most recent call last\):|"
    r"(?:AssertionError|ImportError|ModuleNotFoundError|RuntimeError|SyntaxError|TypeError|ValueError):)",
    re.IGNORECASE,
)
PROJECT_LAUNCH_READY_WAIT_SECONDS = 3.0
PROJECT_LAUNCH_ROOT_FILENAMES = (
    "main.py",
    "app.py",
    "server.py",
    "run.py",
    "start.py",
    "launch.py",
    "serve.py",
    "run.ps1",
    "start.ps1",
    "launch.ps1",
    "serve.ps1",
    "run.sh",
    "start.sh",
    "launch.sh",
    "serve.sh",
    "run.cmd",
    "start.cmd",
    "launch.cmd",
    "serve.cmd",
    "run.bat",
    "start.bat",
    "launch.bat",
    "serve.bat",
)
IMPLICIT_ENTRYPOINT_DISCOVERY_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "browser",
    "by",
    "command",
    "commands",
    "create",
    "current",
    "do",
    "existing",
    "first",
    "fix",
    "for",
    "from",
    "helper",
    "helpers",
    "if",
    "in",
    "is",
    "it",
    "its",
    "line",
    "not",
    "of",
    "on",
    "one",
    "open",
    "or",
    "project",
    "reimplement",
    "replacement",
    "return",
    "script",
    "setup",
    "tabs",
    "task",
    "the",
    "them",
    "this",
    "those",
    "to",
    "urls",
    "use",
    "workspace",
}
IMPLICIT_ENTRYPOINT_WEIGHT_BY_TOKEN = {
    "benchmark": 5,
    "build": 5,
    "compile": 5,
    "convert": 5,
    "deploy": 5,
    "evaluate": 5,
    "export": 5,
    "fetch": 4,
    "generate": 5,
    "launch": 4,
    "lint": 5,
    "migrate": 5,
    "report": 4,
    "run": 3,
    "serve": 4,
    "start": 3,
    "test": 5,
    "train": 5,
}
IMPLICIT_ENTRYPOINT_MIN_SCORE = 12
THINKING_EFFORT_OPTIONS = ("default", "low", "medium", "high", "xhigh")
THINKING_EFFORT_DISPLAY_OPTIONS = ("Default", "low", "medium", "high", "xhigh")
THINKING_EFFORT_STEP_CAPS: dict[str, int | None] = {
    "default": None,
    "low": 8,
    "medium": 16,
    "high": 24,
    "xhigh": 0,
}
SIMPLE_DIRECT_QUESTION_STEP_CAP = 2
SIMPLE_DIRECT_QUESTION_RETRY_STEP_CAP = 8
FOREGROUND_EXISTING_SCRIPT_STEP_CAP = 12
FOREGROUND_EXISTING_SCRIPT_RETRY_STEP_CAP = 24
SCRIPT_PREFLIGHT_TIMEOUT_SECONDS = 300
SCRIPT_PREFLIGHT_MAX_URLS = 64
ROUTE_SIMPLE_ANSWER = "simple_answer"
ROUTE_DIRECT_EXISTING_ENTRYPOINT = "direct_existing_entrypoint"
ROUTE_READ_ONLY_WORKSPACE = "read_only_workspace"
ROUTE_RETRIEVAL_PLUS_MODEL = "retrieval_plus_model"
ROUTE_TOOL_LOOP = "tool_loop"
ROUTE_ASK_USER_MISSING_INFO = "ask_user_missing_info"
ROUTE_PROFILE_AGENTIC_QWEN_24G = "24g_retrieval"
ROUTE_TRACE_FILE_NAME = "route_traces.jsonl"
ROUTE_TRACE_PROMPT_PREVIEW_CHARS = 240
ROUTE_TRACE_ANSWER_PREVIEW_CHARS = 400


@dataclass
class RouteFeatures:
    prompt_length: int
    line_count: int
    simple_question_candidate: bool
    explicit_existing_entrypoint: bool
    read_intent: bool
    edit_intent: bool
    verify_intent: bool
    workspace_context_needed: bool
    file_token_count: int
    mentions_tools_or_mcp: bool
    powershell_or_side_effect: bool
    plugin_or_skill_request: bool
    has_entrypoint_spec: bool
    entrypoint_kind: str = ""
    needs_missing_context: bool = False

    def to_trace_dict(self) -> dict[str, Any]:
        return {
            "prompt_length": self.prompt_length,
            "line_count": self.line_count,
            "simple_question_candidate": self.simple_question_candidate,
            "explicit_existing_entrypoint": self.explicit_existing_entrypoint,
            "read_intent": self.read_intent,
            "edit_intent": self.edit_intent,
            "verify_intent": self.verify_intent,
            "workspace_context_needed": self.workspace_context_needed,
            "file_token_count": self.file_token_count,
            "mentions_tools_or_mcp": self.mentions_tools_or_mcp,
            "powershell_or_side_effect": self.powershell_or_side_effect,
            "plugin_or_skill_request": self.plugin_or_skill_request,
            "has_entrypoint_spec": self.has_entrypoint_spec,
            "entrypoint_kind": self.entrypoint_kind,
            "needs_missing_context": self.needs_missing_context,
        }


@dataclass
class RouteDecision:
    selected_route: str
    reason: str
    scores: dict[str, int]
    features: dict[str, Any]
    fallback_routes: list[str] = field(default_factory=list)
    profile: str = ROUTE_PROFILE_AGENTIC_QWEN_24G
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
ACCESS_MODE_ENV_NAME = "QUBITZ_ACCESS_MODE"
ACCESS_MODE_READ_ONLY = "read_only"
ACCESS_MODE_PLAN = "plan"
ACCESS_MODE_FULL = "full"
DEFAULT_ACCESS_MODE = ACCESS_MODE_FULL
ACCESS_MODE_LABELS = {
    ACCESS_MODE_READ_ONLY: "Read-only",
    ACCESS_MODE_PLAN: "Plan Mode",
    ACCESS_MODE_FULL: "Full Access",
}
ACCESS_MODE_VALUES = tuple(ACCESS_MODE_LABELS.values())


def _normalize_access_mode(value: Any) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "read": ACCESS_MODE_READ_ONLY,
        "readonly": ACCESS_MODE_READ_ONLY,
        "read_only": ACCESS_MODE_READ_ONLY,
        "plan": ACCESS_MODE_PLAN,
        "plan_mode": ACCESS_MODE_PLAN,
        "full": ACCESS_MODE_FULL,
        "full_access": ACCESS_MODE_FULL,
    }
    return aliases.get(normalized, DEFAULT_ACCESS_MODE)


def _access_mode_label(value: Any) -> str:
    return ACCESS_MODE_LABELS[_normalize_access_mode(value)]


def _full_access_enabled() -> bool:
    return _normalize_access_mode(os.environ.get(ACCESS_MODE_ENV_NAME, DEFAULT_ACCESS_MODE)) == ACCESS_MODE_FULL


@dataclass(frozen=True)
class SemanticIntent:
    speech_act: str
    requested_actions: frozenset[str]
    prohibited_actions: frozenset[str]
    objects: frozenset[str]
    execution_scope: str
    required_postconditions: frozenset[str]

    def requests(self, *actions: str) -> bool:
        return bool(self.requested_actions.intersection(actions))


_SEMANTIC_ACTION_PATTERN = re.compile(
    r"\b(?P<verb>look\s+at|shut\s+down|"
    r"read|show|view|inspect|examine|review|display|print|open|"
    r"edit|modify|change|replace|update|fix|refactor|rewrite|implement|patch|"
    r"create|generate|export|save|write|add|remove|delete|erase|"
    r"copy|duplicate|clone|move|rename|"
    r"run|execute|start|launch|serve|stop|shutdown|terminate|kill|restart|"
    r"install|upgrade|uninstall|verify|validate|test|lint|fetch|download|call|invoke|list|discover|cancel)\b",
    re.IGNORECASE,
)
_SEMANTIC_SIDE_EFFECT_ACTIONS = frozenset(
    {
        "copy",
        "clone_repository",
        "create",
        "delete",
        "edit",
        "execute",
        "install",
        "mcp_call",
        "move",
        "open_browser",
        "open_project",
        "restart",
        "start",
        "stop",
        "uninstall",
        "upgrade",
    }
)


def _semantic_objects(text: str) -> set[str]:
    objects: set[str] = set()
    patterns = {
        "application": r"\b(?:application|executable|program)\b|\bapp\b(?!\.[a-z0-9])",
        "browser": r"https?://|\b(?:browser|chrome|edge|firefox|links?|tabs?|urls?)\b",
        "code": r"\b(?:class|code|function|method|module|source|symbol|text|line)\b",
        "file": (
            r"\b(?:config|directory|document|file|folder|path|readme)\b"
            r"|(?:^|[^a-z0-9_])[a-z0-9_.\\/-]+\.[a-z0-9]{1,12}(?=$|[^a-z0-9_])"
        ),
        "mcp": r"\b(?:mcp|model context protocol|mcp tool|tool)\b",
        "package": (
            r"\b(?:dependencies|dependency|pip|requirements|uv)\b"
            r"|\bpackages?\b(?!\.(?:json|lock))"
        ),
        "project": r"\b(?:codebase|project|repo|repository|workspace)\b",
        "script": r"\b(?:command|entrypoint|helper|script|\.bat|\.cmd|\.exe|\.ps1|\.py|\.sh)\b",
        "service": r"\b(?:background job|job|process|server|service)\b",
        "test": r"\b(?:check|checks|lint|pytest|ruff|test|tests|unittest)\b",
    }
    for name, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            objects.add(name)
    return objects


def _semantic_action_is_negated(clause: str, action_start: int) -> bool:
    prefix = clause[:action_start]
    return bool(
        re.search(
            r"(?:\bdo\s+not|\bdon't|\bnever|\bmust\s+not|\bshould\s+not|"
            r"\bwithout|\bavoid(?:ed|ing)?|\brefus(?:e|ed|ing)|\bprevent(?:ed|ing)?)"
            r"(?:\W+\w+){0,7}\W*$",
            prefix,
            re.IGNORECASE,
        )
    )


def _analyze_semantic_intent(prompt: str) -> SemanticIntent:
    text = " ".join(str(prompt).strip().split())
    lowered = text.lower()
    directed_request = bool(
        re.search(
            r"^(?:please\s+)?(?:can|could|would|will)\s+you\b"
            r"|^(?:please\s+)|\b(?:i\s+(?:want|need)|i(?:'d|\s+would)\s+like)\s+you\s+to\b",
            lowered,
        )
    )
    imperative = bool(re.match(rf"^(?:please\s+)?{_SEMANTIC_ACTION_PATTERN.pattern}", lowered, re.IGNORECASE))
    explanatory = bool(
        re.search(
            r"\b(?:explain|describe)\b|\bhow\s+(?:do|can|should|would)\s+(?:i|we|someone)\b"
            r"|\bhow\s+to\b|\bwhat\s+(?:command|steps?)\b",
            lowered,
        )
    )
    hypothetical = bool(re.search(r"\b(?:hypothetically|suppose|what\s+if|would\s+it\s+if)\b", lowered))
    if directed_request or imperative:
        speech_act = "request"
    elif explanatory:
        speech_act = "explanation"
    elif hypothetical:
        speech_act = "hypothetical"
    elif text.endswith("?"):
        speech_act = "question"
    else:
        speech_act = "request"

    requested: set[str] = set()
    prohibited: set[str] = set()
    all_objects: set[str] = set()
    clauses = [
        clause.strip()
        for clause in re.split(r"(?:[.;\n]+|\bbut\b|\bhowever\b|\binstead\b)", lowered)
        if clause.strip()
    ]
    for clause in clauses or [lowered]:
        objects = _semantic_objects(clause)
        all_objects.update(objects)
        for match in _SEMANTIC_ACTION_PATTERN.finditer(clause):
            verb = re.sub(r"\s+", " ", match.group("verb").lower())
            actions: set[str] = set()
            if verb in {"read", "show", "view", "inspect", "examine", "review", "display", "print", "look at"}:
                actions.add("read")
            elif verb == "open":
                if "browser" in objects:
                    actions.add("open_browser")
                elif objects.intersection({"file", "code"}):
                    actions.add("read")
                elif "application" in objects:
                    actions.add("start")
                elif "project" in objects:
                    actions.add("open_project")
                elif objects.intersection({"service", "script"}):
                    actions.add("start")
                else:
                    actions.add("read")
            elif verb in {"edit", "modify", "change", "replace", "fix", "refactor", "rewrite", "implement", "patch"}:
                actions.add("edit")
            elif verb == "update":
                actions.add("upgrade" if "package" in objects and "file" not in objects else "edit")
            elif verb in {"create", "generate", "export", "save", "write"}:
                actions.update({"create", "edit"})
            elif verb == "add":
                actions.add("install" if "package" in objects and "file" not in objects else "edit")
            elif verb == "remove":
                if "file" in objects:
                    actions.add("delete")
                elif "code" in objects:
                    actions.add("edit")
                elif "package" in objects:
                    actions.add("uninstall")
                else:
                    actions.add("edit")
            elif verb in {"delete", "erase"}:
                actions.add("delete")
            elif verb in {"copy", "duplicate"}:
                actions.add("copy")
            elif verb == "clone":
                actions.add("clone_repository" if "project" in objects else "copy")
            elif verb in {"move", "rename"}:
                actions.add("move")
            elif verb in {"run", "execute"}:
                actions.add("execute")
            elif verb in {"start", "launch", "serve"}:
                actions.add("execute" if "script" in objects and not objects.intersection({"project", "service"}) else "start")
            elif verb in {"stop", "shutdown", "shut down", "terminate", "kill"}:
                actions.add("stop")
            elif verb == "restart":
                actions.add("restart")
            elif verb == "install":
                actions.add("install")
            elif verb == "upgrade":
                actions.add("upgrade")
            elif verb == "uninstall":
                actions.add("uninstall")
            elif verb in {"verify", "validate", "test", "lint"}:
                actions.add("verify")
            elif verb == "fetch":
                actions.add("fetch_url")
            elif verb == "download":
                actions.update({"create", "execute"})
            elif verb in {"call", "invoke"} and "mcp" in objects:
                actions.add("mcp_call")
            elif verb in {"list", "discover"} and "mcp" in objects:
                actions.add("mcp_discover")
            elif verb == "cancel":
                actions.add("stop")
            if _semantic_action_is_negated(clause, match.start()):
                prohibited.update(actions)
            else:
                requested.update(actions)

    if speech_act != "request":
        requested.difference_update(_SEMANTIC_SIDE_EFFECT_ACTIONS)
    requested.difference_update(prohibited)

    named_process = bool(all_objects.intersection({"application", "service", "mcp"}))
    if requested.intersection({"restart", "start", "stop"}) and named_process:
        execution_scope = "managed_service"
    elif requested == {"stop"} and not named_process:
        execution_scope = "foreground_task"
    else:
        execution_scope = "workspace"

    postconditions: set[str] = set()
    if requested.intersection({"delete", "edit", "move"}):
        postconditions.add("changed_files")
    if requested.intersection({"clone_repository", "copy", "create"}):
        postconditions.update({"changed_files", "created_outputs"})
    if "verify" in requested and "test" in all_objects:
        postconditions.add("tests")
    if "start" in requested or "restart" in requested:
        postconditions.add("service_started")
    if ("stop" in requested and named_process) or "restart" in requested:
        postconditions.add("service_stopped")
    if "open_browser" in requested:
        postconditions.add("opened_urls")
    if "mcp_discover" in requested:
        postconditions.add("mcp_ready")
    if "mcp_call" in requested:
        postconditions.add("mcp_tool_called")
    return SemanticIntent(
        speech_act=speech_act,
        requested_actions=frozenset(requested),
        prohibited_actions=frozenset(prohibited),
        objects=frozenset(all_objects),
        execution_scope=execution_scope,
        required_postconditions=frozenset(postconditions),
    )


DIRECT_RESULT_MISSING_URL = "(no URL)"
SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS = 12000
SCRIPT_PREFLIGHT_BROWSER_GLOB_PATTERNS = ("open*.ps1", "open*.bat", "open*.cmd")
SCRIPT_PREFLIGHT_ARTIFACT_MAX_BYTES = 2 * 1024 * 1024
SCRIPT_PREFLIGHT_ARTIFACT_MAX_FILES = 32
START_PROCESS_URL_PATTERN = re.compile(
    r"Start-Process\s+(?:-FilePath\s+)?(?P<quote>['\"])(?P<target>.*?)(?P=quote)",
    re.IGNORECASE,
)
UV_RUN_COMMAND_PATTERN = re.compile(
    r"\buv\s+run\s+(?P<target>`[^`]+`|\"[^\"]+\"|'[^']+'|[A-Za-z0-9_./:\\-]+)",
    re.IGNORECASE,
)
PACKAGE_SCRIPT_COMMAND_PATTERN = re.compile(
    r"\b(?P<tool>npm|pnpm)\s+run\s+(?P<script>[A-Za-z0-9:_./-]+)",
    re.IGNORECASE,
)
MAKE_TARGET_COMMAND_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./:-])make\s+(?P<target>[A-Za-z0-9_.:/-]+)",
    re.IGNORECASE,
)
JSON_CODE_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.IGNORECASE | re.DOTALL)
MARKDOWN_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*:?-{3,}:?\s*$")
DIRECT_RESULT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "rank": ("rank", "position", "index", "number"),
    "score": ("score", "points"),
    "author": ("author", "user", "username", "owner", "by"),
    "title": ("title", "name", "headline", "subject"),
    "url": ("url", "link", "href", "uri"),
    "time": ("time", "time ago", "age", "timestamp", "date", "published", "when"),
}
DIRECT_RESULT_FALLBACK_FIELDS = ("rank", "score", "author", "title", "url", "time")
RANKED_RESULT_LINE_PATTERN = re.compile(
    r"^\s*(?P<rank>\d+)\s+(?P<score>\d+)\s+(?P<time>(?:\d+[smhd]\s+ago|N/A))\s+"
    r"(?P<author>\S+)\s+(?P<title>.+?)\s*$"
)


def _now_stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _decode_subprocess_output(data: bytes | str | None) -> str:
    if data is None or isinstance(data, str):
        return data or ""
    encodings = ["utf-8", "utf-8-sig", "cp1252"]
    preferred = locale.getpreferredencoding(False) or "utf-8"
    if preferred not in encodings:
        encodings.append(preferred)
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _shorten(text: str, limit: int = 1600) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _workspace_relative_command_path(base: Any, workspace: Path, target: Path) -> str:
    resolved_workspace = workspace.resolve()
    resolved_target = target.resolve()
    try:
        relative_target = resolved_target.relative_to(resolved_workspace)
    except ValueError:
        if callable(getattr(base, "in_wsl", None)) and base.in_wsl() and resolved_target.suffix.lower() == ".exe":
            return _workspace_windows_path(base, resolved_target)
        return str(resolved_target)
    relative = str(relative_target).replace("/", "\\")
    if not relative.startswith(".") and not re.match(r"^[A-Za-z]:[\\/]", relative):
        relative = f".\\{relative}"
    return relative


def _normalize_prompt_path_token(token: str) -> str:
    candidate = str(token).strip().strip("`\"'")
    if candidate.startswith("\\") and not candidate.startswith("\\\\"):
        return f".{candidate}"
    return candidate


def _normalize_project_use_task_prompt(prompt: str) -> str:
    match = PROJECT_USE_TASK_PREFIX_PATTERN.search(prompt)
    if match is None or match.group("verb").lower() == "use":
        return prompt
    trailing = prompt[match.end() :]
    if PROJECT_LAUNCH_BENIGN_TRAILING_PATTERN.fullmatch(trailing):
        return prompt
    return f"{prompt[: match.start('verb')]}use{prompt[match.end('verb') :]}"


def _iter_prioritized_script_tokens(base: Any, prompt: str) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def add_tokens(text: str) -> None:
        for raw_token in getattr(base, "extract_file_tokens", lambda _text: [])(text):
            normalized = _normalize_prompt_path_token(raw_token)
            if not normalized.lower().endswith(SCRIPT_FILE_SUFFIXES):
                continue
            key = normalized.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(normalized)

    priority_markers = ("preferred existing script", "existing script")
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in priority_markers):
            add_tokens(line)
    add_tokens(prompt)
    return ordered


def _load_package_json_scripts(workspace: Path) -> dict[str, str]:
    package_path = workspace / "package.json"
    if not package_path.exists():
        return {}
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {
        str(name): str(command)
        for name, command in scripts.items()
        if isinstance(name, str) and isinstance(command, str) and name.strip() and command.strip()
    }


def _find_makefile_path(workspace: Path) -> Path | None:
    for candidate_name in MAKEFILE_CANDIDATE_NAMES:
        candidate = workspace / candidate_name
        if candidate.is_file():
            return candidate
    return None


def _load_make_targets(workspace: Path) -> set[str]:
    makefile_path = _find_makefile_path(workspace)
    if makefile_path is None:
        return set()
    targets: set[str] = set()
    try:
        content = makefile_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return targets
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", "\t", ".")):
            continue
        match = re.match(r"^(?P<name>[A-Za-z0-9_.@%/:-]+)\s*:(?![=])", raw_line)
        if not match:
            continue
        targets.add(match.group("name"))
    return targets


def _resolve_existing_entrypoint_spec(base: Any, workspace: Path, prompt: str) -> dict[str, Any] | None:
    prompt = _normalize_project_use_task_prompt(prompt)
    specs: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_spec(key: str, spec: dict[str, Any]) -> None:
        normalized_key = key.casefold()
        if normalized_key in seen:
            return
        seen.add(normalized_key)
        specs.append(spec)

    for token_text in _iter_prioritized_script_tokens(base, prompt):
        try:
            candidate = base.resolve_workspace_path(
                workspace,
                token_text,
                allow_missing=False,
                allow_external=True,
            )
        except Exception:
            continue
        if candidate.is_file():
            resolved = candidate.resolve()
            relative = str(base.relative_path(resolved, workspace))
            add_spec(
                f"file:{resolved}",
                {"kind": "file", "path": resolved, "label": relative},
            )

    for match in UV_RUN_COMMAND_PATTERN.finditer(prompt):
        target_text = _normalize_prompt_path_token(match.group("target"))
        try:
            candidate = base.resolve_workspace_path(
                workspace,
                target_text,
                allow_missing=False,
                allow_external=True,
            )
        except Exception:
            continue
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        relative = str(base.relative_path(resolved, workspace))
        add_spec(
            f"uv:{resolved}",
            {
                "kind": "uv_run",
                "path": resolved,
                "label": f"uv run {relative}",
                "command": f"uv run {_shell_quote_path(relative)}",
            },
        )

    package_scripts = _load_package_json_scripts(workspace)
    for match in PACKAGE_SCRIPT_COMMAND_PATTERN.finditer(prompt):
        tool = str(match.group("tool")).lower()
        script_name = str(match.group("script")).strip()
        if script_name not in package_scripts:
            continue
        add_spec(
            f"{tool}:{script_name}",
            {
                "kind": "package_script",
                "tool": tool,
                "script": script_name,
                "label": f"{tool} run {script_name}",
                "command": f"{tool} run {shlex.quote(script_name)}",
            },
        )

    make_targets = _load_make_targets(workspace)
    for match in MAKE_TARGET_COMMAND_PATTERN.finditer(prompt):
        target = str(match.group("target")).strip()
        if target not in make_targets:
            continue
        add_spec(
            f"make:{target}",
            {
                "kind": "make_target",
                "target": target,
                "label": f"make {target}",
                "command": f"make {shlex.quote(target)}",
            },
        )
    if not specs:
        implicit_spec = _discover_implicit_existing_entrypoint_spec(base, workspace, prompt)
        if implicit_spec is not None:
            add_spec(f"implicit:{implicit_spec['label']}", implicit_spec)
    return specs[0] if specs else None


def _prompt_has_explicit_entrypoint_command(prompt: str) -> bool:
    return bool(
        UV_RUN_COMMAND_PATTERN.search(prompt)
        or PACKAGE_SCRIPT_COMMAND_PATTERN.search(prompt)
        or MAKE_TARGET_COMMAND_PATTERN.search(prompt)
    )


def _prompt_prefers_implicit_existing_entrypoint(prompt: str) -> bool:
    cleaned = prompt.strip()
    if not cleaned:
        return False
    lowered = cleaned.lower()
    if not any(hint in lowered for hint in WORKSPACE_CONTEXT_HINTS):
        return False
    if any(hint in lowered for hint in IMPLICIT_EXISTING_ENTRYPOINT_HINTS):
        return True
    if re.search(r"\b(?:do not|don't|never|without)\b[^\n.;]{0,120}\b(?:run|execute|launch|start|open)\w*\b", lowered):
        return False
    concrete_action = re.search(
        r"\b(?:run|execute|launch|start|serve|build|compile|test|fetch|get|generate|export|convert|train|evaluate|benchmark)\b",
        lowered,
    )
    browser_action = bool(re.search(r"\bopen(?:ing)?\b", lowered) and re.search(r"\b(?:browser|tabs?|windows?|urls?|links?)\b", lowered))
    return bool(concrete_action or browser_action)


def _implicit_entrypoint_prompt_tokens(prompt: str) -> set[str]:
    tokens: set[str] = set()
    for raw_token in re.findall(r"[a-z0-9_]+", prompt.lower()):
        if len(raw_token) < 3:
            continue
        if raw_token.isdigit():
            continue
        if raw_token in IMPLICIT_ENTRYPOINT_DISCOVERY_STOPWORDS:
            continue
        tokens.add(raw_token)
    if "url" in prompt.lower():
        tokens.add("url")
    if "urls" in prompt.lower():
        tokens.add("urls")
    return tokens


def _score_implicit_existing_entrypoint_candidate(
    prompt_tokens: set[str],
    relative_path: Path,
    candidate_path: Path,
) -> int:
    stem_tokens = set(re.findall(r"[a-z0-9]+", candidate_path.stem.lower()))
    if not stem_tokens:
        return -999
    score = 0
    for token in prompt_tokens.intersection(stem_tokens):
        score += IMPLICIT_ENTRYPOINT_WEIGHT_BY_TOKEN.get(token, 3)
    if candidate_path.suffix.lower() == ".py":
        score += 4
    elif candidate_path.suffix.lower() in {".ps1", ".bat", ".cmd", ".sh"}:
        score += 1
    depth = len(relative_path.parts)
    if depth <= 2:
        score += 3
    elif depth <= 4:
        score += 1
    if stem_tokens.intersection({"test", "tests", "smoke", "benchmark", "bench", "eval"}):
        score -= 12
    if prompt_tokens.intersection({"url", "urls"}):
        if stem_tokens.intersection({"url", "urls"}):
            score += 6
        if stem_tokens.intersection({"summary", "summaries"}):
            score -= 4
    return score


def _score_implicit_entrypoint_capabilities(prompt: str, candidate_path: Path) -> int:
    try:
        source = candidate_path.read_text(encoding="utf-8", errors="ignore")[:65536]
    except OSError:
        return 0
    score = 0
    expected_count = _expected_result_count(prompt)
    if expected_count is not None:
        filename_counts = {
            int(value)
            for value in re.findall(r"(?<!\d)(\d{1,4})(?!\d)", candidate_path.stem)
        }
        if expected_count in filename_counts:
            score += 16
        declared_counts = {
            int(value)
            for value in re.findall(r"(?:limit\s*=\s*|\[:\s*)(\d{1,4})", source, flags=re.IGNORECASE)
        }
        if expected_count in declared_counts:
            score += 12
        elif declared_counts and max(declared_counts) < expected_count:
            score -= 12
    if _prompt_requests_browser_open(prompt):
        if re.search(r"Start-Process|webbrowser\s*\.\s*open|open[^\n]{0,80}\.ps1", source, flags=re.IGNORECASE):
            score += 8
    return score


PROJECT_GOAL_CLARIFICATION = (
    "Please specify the concrete project result you want, such as the command to run, artifact to produce, "
    "behavior to verify, or file or entrypoint to use."
)


def _project_use_prompt_lacks_concrete_goal(
    base: Any,
    prompt: str,
    entrypoint: dict[str, Any] | None,
    file_tokens: Sequence[str],
) -> bool:
    if entrypoint is not None or file_tokens:
        return False
    lowered = prompt.strip().lower()
    if not any(hint in lowered for hint in WORKSPACE_CONTEXT_HINTS):
        return False
    if base.READ_INTENT_PATTERN.search(prompt) or base.EDIT_INTENT_PATTERN.search(prompt) or base.VERIFY_INTENT_PATTERN.search(prompt):
        return False
    concrete_action = re.search(
        r"\b(build|compile|test|lint|run|start|serve|deploy|fetch|get|list|open|read|inspect|explain|summarize|find|search|convert|train|evaluate|benchmark|install|update|fix|create|delete|rename)\b",
        lowered,
    )
    if concrete_action is not None:
        return False
    return any(
        marker in lowered
        for marker in (
            "produce one concrete verified project result",
            "produce a project result",
            "use the active workspace",
            "use this project",
            "use the project",
            "use this workspace",
            "use the workspace",
        )
    )


def _discover_implicit_existing_entrypoint_spec(base: Any, workspace: Path, prompt: str) -> dict[str, Any] | None:
    if not _prompt_prefers_implicit_existing_entrypoint(prompt):
        return None
    lowered = prompt.lower()
    negative_edit_only = any(
        phrase in lowered
        for phrase in (
            "do not fix the project",
            "do not reimplement it",
            "do not create a replacement script",
            "do not create a new script",
        )
    )
    if base.VERIFY_INTENT_PATTERN.search(prompt):
        return None
    if base.EDIT_INTENT_PATTERN.search(prompt) and not negative_edit_only:
        return None
    prompt_tokens = _implicit_entrypoint_prompt_tokens(prompt)
    if not prompt_tokens:
        return None
    excluded_dirs = {str(item).casefold() for item in getattr(base, "EXCLUDED_DIRS", set())}
    best_path: Path | None = None
    best_relative: Path | None = None
    best_score = -999
    for root, dirs, files in os.walk(workspace):
        root_path = Path(root)
        relative_root = Path(".") if root_path == workspace else root_path.relative_to(workspace)
        if len(relative_root.parts) > 5:
            dirs[:] = []
            continue
        dirs[:] = [
            entry
            for entry in dirs
            if entry.casefold() not in excluded_dirs and not entry.startswith(".")
        ]
        for filename in files:
            candidate_path = root_path / filename
            if candidate_path.suffix.lower() not in SCRIPT_FILE_SUFFIXES:
                continue
            relative_path = candidate_path.relative_to(workspace)
            score = _score_implicit_existing_entrypoint_candidate(prompt_tokens, relative_path, candidate_path)
            score += _score_implicit_entrypoint_capabilities(prompt, candidate_path)
            if score > best_score:
                best_score = score
                best_path = candidate_path.resolve()
                best_relative = relative_path
    if best_path is None or best_relative is None or best_score < IMPLICIT_ENTRYPOINT_MIN_SCORE:
        return None
    return {
        "kind": "file",
        "path": best_path,
        "label": str(best_relative),
        "origin": "implicit_discovery",
        "score": best_score,
    }


def _collect_browser_helper_candidates(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for pattern in SCRIPT_PREFLIGHT_BROWSER_GLOB_PATTERNS:
        for candidate in workspace.glob(pattern):
            if not candidate.is_file():
                continue
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def _workspace_text_artifact_state(base: Any, workspace: Path) -> dict[str, tuple[int, int]]:
    state: dict[str, tuple[int, int]] = {}
    excluded_dirs = set(getattr(base, "EXCLUDED_DIRS", set()))
    for candidate in base.iter_workspace_files(workspace, excluded_dirs):
        if base.is_sensitive_path_name(candidate):
            continue
        with suppress(OSError):
            stat = candidate.stat()
            state[str(candidate.resolve())] = (stat.st_mtime_ns, stat.st_size)
    return state


def _changed_text_artifacts(
    base: Any,
    workspace: Path,
    before: dict[str, tuple[int, int]],
) -> list[tuple[Path, str]]:
    changed: list[tuple[int, Path, str]] = []
    text_suffixes = set(getattr(base, "TEXT_SUFFIXES", set())) | {".log", ".out"}
    excluded_dirs = set(getattr(base, "EXCLUDED_DIRS", set()))
    for candidate in base.iter_workspace_files(workspace, excluded_dirs):
        if base.is_sensitive_path_name(candidate):
            continue
        with suppress(OSError):
            stat = candidate.stat()
            key = str(candidate.resolve())
            current = (stat.st_mtime_ns, stat.st_size)
            if current == before.get(key):
                continue
            text = ""
            if candidate.suffix.lower() in text_suffixes and stat.st_size <= SCRIPT_PREFLIGHT_ARTIFACT_MAX_BYTES:
                text = candidate.read_text(encoding="utf-8", errors="ignore")
            changed.append((stat.st_mtime_ns, candidate.resolve(), text))
    changed.sort(key=lambda item: item[0], reverse=True)
    return [(path, text) for _, path, text in changed[:SCRIPT_PREFLIGHT_ARTIFACT_MAX_FILES]]


def _extract_start_process_urls(script_text: str) -> list[str]:
    urls: list[str] = []
    for match in START_PROCESS_URL_PATTERN.finditer(script_text):
        target = match.group("target").strip()
        if not target:
            continue
        urls.append(DIRECT_RESULT_MISSING_URL if target == "#" else target)
        if len(urls) >= SCRIPT_PREFLIGHT_MAX_URLS:
            break
    return urls


def _prompt_requests_browser_open(prompt: str) -> bool:
    lowered = prompt.lower()
    if re.search(r"\b(?:do not|don't|never|without)\b[^\n.;]{0,120}\bopen(?:ing)?\b", lowered):
        return False
    if "start-process" in lowered:
        return True
    return bool(
        re.search(r"\bopen(?:ing)?\b", lowered)
        and re.search(r"\b(?:browser|tabs?|windows?)\b", lowered)
        and re.search(r"\b(?:urls?|links?|them|those)\b", lowered)
    )


def _is_external_http_url(value: Any) -> bool:
    normalized = str(value or "").strip()
    return bool(re.fullmatch(r"https?://[^\s]+", normalized, flags=re.IGNORECASE))


def _validated_external_urls(values: Sequence[Any], limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        key = normalized.casefold()
        if not _is_external_http_url(normalized) or key in seen:
            continue
        seen.add(key)
        urls.append(normalized)
        if len(urls) >= limit:
            break
    return urls


def _extract_external_urls(text: str, limit: int = SCRIPT_PREFLIGHT_MAX_URLS) -> list[str]:
    candidates = re.findall(r"https?://[^\s'\"<>()[\]{}|]+", text, flags=re.IGNORECASE)
    return _validated_external_urls([value.rstrip(".,;:!?") for value in candidates], limit)


def _run_inline_browser_open(
    base: Any,
    workspace: Path,
    urls: Sequence[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    validated_urls = _validated_external_urls(urls, SCRIPT_PREFLIGHT_MAX_URLS)
    commands = [
        f"Start-Process {_powershell_single_quote(url)}"
        for url in validated_urls
    ]
    if not commands:
        return {"return_code": 1, "stderr": "No URLs were available for browser opening."}
    result = _run_powershell_command(base, workspace, "; ".join(commands), timeout_seconds)
    if int(result.get("return_code", result.get("returncode", 0))) == 0:
        result["opened_urls"] = validated_urls
        result["opened_count"] = len(validated_urls)
    return result


def _canonical_result_field(name: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(name).strip().lower()).strip()
    if not normalized:
        return None
    for canonical, aliases in DIRECT_RESULT_FIELD_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return None


def _requested_output_fields(prompt: str) -> list[str]:
    fields: list[str] = []
    seen: set[str] = set()
    collecting_bullets = False
    for raw_line in prompt.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if not line:
            collecting_bullets = False
            continue
        if "return these fields" in lowered:
            collecting_bullets = True
            continue
        if collecting_bullets:
            if line.startswith("-"):
                field = _canonical_result_field(line.lstrip("- ").strip())
                if field is not None and field not in seen:
                    seen.add(field)
                    fields.append(field)
                continue
            collecting_bullets = False
        if "return" not in lowered:
            continue
        positions: list[tuple[int, str]] = []
        for canonical, aliases in DIRECT_RESULT_FIELD_ALIASES.items():
            best: int | None = None
            for alias in aliases:
                if alias == "by":
                    continue
                match = re.search(rf"(?<![a-z0-9_]){re.escape(alias)}(?![a-z0-9_])", lowered)
                if match is None:
                    continue
                best = match.start() if best is None else min(best, match.start())
            if best is not None:
                positions.append((best, canonical))
        for _, field in sorted(positions):
            if field not in seen:
                seen.add(field)
                fields.append(field)
    return fields


def _normalize_result_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        canonical = _canonical_result_field(key)
        target = canonical or re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
        if not target or target in normalized:
            continue
        normalized[target] = value
    return normalized


def _rows_have_required_fields(rows: Sequence[dict[str, Any]], fields: Sequence[str]) -> bool:
    required = [field for field in fields if field]
    if not rows:
        return False
    if not required:
        return True
    return all(all(str(row.get(field, "")).strip() for field in required) for row in rows)


def _coerce_rows_from_json_payload(payload: Any) -> list[dict[str, Any]]:
    candidates: Any = payload
    if isinstance(payload, dict):
        for key in ("rows", "results", "items", "data", "records", "entries", "outputs", "stories"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        else:
            return [_normalize_result_row(payload)]
    if not isinstance(candidates, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, dict):
            rows.append(_normalize_result_row(item))
    return rows


def _extract_json_rows(text: str) -> list[dict[str, Any]]:
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        with suppress(Exception):
            rows = _coerce_rows_from_json_payload(json.loads(stripped))
            if rows:
                return rows
    for match in JSON_CODE_BLOCK_PATTERN.finditer(text):
        body = match.group("body").strip()
        if not body:
            continue
        with suppress(Exception):
            rows = _coerce_rows_from_json_payload(json.loads(body))
            if rows:
                return rows
    return []


def _extract_markdown_table_rows(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    for index in range(len(lines) - 1):
        header_line = lines[index].strip()
        separator_line = lines[index + 1].strip()
        if "|" not in header_line or "|" not in separator_line:
            continue
        header_cells = [cell.strip() for cell in header_line.strip("|").split("|")]
        separator_cells = [cell.strip() for cell in separator_line.strip("|").split("|")]
        if not header_cells or len(separator_cells) < len(header_cells):
            continue
        if not all(MARKDOWN_TABLE_SEPARATOR_PATTERN.match(cell) for cell in separator_cells[: len(header_cells)]):
            continue
        headers = [cell or f"column_{offset}" for offset, cell in enumerate(header_cells, start=1)]
        rows: list[dict[str, Any]] = []
        row_index = index + 2
        while row_index < len(lines):
            row_line = lines[row_index].strip()
            if "|" not in row_line:
                break
            cells = [cell.strip() for cell in row_line.strip("|").split("|")]
            if len(cells) != len(headers):
                break
            rows.append(_normalize_result_row(dict(zip(headers, cells))))
            row_index += 1
        if rows:
            return rows
    return []


def _extract_structured_result_rows(text: str) -> list[dict[str, Any]]:
    json_rows = _extract_json_rows(text)
    if json_rows:
        return json_rows
    markdown_rows = _extract_markdown_table_rows(text)
    if markdown_rows:
        return markdown_rows
    rows: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        match = RANKED_RESULT_LINE_PATTERN.match(raw_line.rstrip())
        if match is None:
            continue
        title = match.group("title").strip()
        if not title or title.lower() == "title":
            continue
        rows.append(
            {
                "rank": int(match.group("rank")),
                "score": int(match.group("score")),
                "author": match.group("author").strip(),
                "title": title,
            }
        )
    return rows


def _expected_result_count(prompt: str) -> int | None:
    match = re.search(r"\b(?:top|first)\s+(\d+)\b", prompt, flags=re.IGNORECASE)
    if match is None:
        match = re.search(
            r"\b(\d+)\s+(?:(?:external|news|story|article|result)\s+)?(?:items|results|rows|records|entries|files|objects|stories|urls?|links?|articles?|headlines?)\b",
            prompt,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    return int(match.group(1))


def _requested_count_postcondition(prompt: str) -> tuple[str, int] | None:
    expected = _expected_result_count(prompt)
    if expected is None or expected <= 0:
        return None
    lowered = prompt.lower()
    if _prompt_requests_browser_open(prompt) and re.search(r"\b(?:urls?|links?|tabs?)\b", lowered):
        return "opened_urls", expected
    if re.search(
        rf"\b{expected}\s+(?:new\s+)?(?:artifacts?|documents?|files?|folders?|outputs?|reports?)\b",
        lowered,
    ):
        return "created_outputs", expected
    return "result_count", expected


def _structured_evidence_count(value: Any) -> int:
    counts: list[int] = []
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in {"count", "opened_count", "result_count", "total", "total_count"}:
                with suppress(TypeError, ValueError):
                    counts.append(max(0, int(item)))
            elif normalized in {
                "artifacts",
                "data",
                "entries",
                "files",
                "items",
                "opened_urls",
                "outputs",
                "records",
                "results",
                "rows",
                "urls",
            } and isinstance(item, (list, tuple, set)):
                counts.append(len(item))
            counts.append(_structured_evidence_count(item))
    elif isinstance(value, (list, tuple, set)):
        counts.append(len(value))
        counts.extend(_structured_evidence_count(item) for item in value)
    return max(counts, default=0)


def _structured_evidence_urls(value: Any) -> list[str]:
    with suppress(TypeError, ValueError):
        rendered = json.dumps(value, ensure_ascii=False, default=str)
        return _extract_external_urls(rendered)
    return []


def _format_direct_result_answer(
    rows: Sequence[dict[str, Any]],
    *,
    browser_open_ran: bool,
    requested_browser_open: bool,
    requested_fields: Sequence[str] | None = None,
) -> str:
    columns = [
        field
        for field in (requested_fields or [])
        if any(str(row.get(field, "")).strip() for row in rows)
    ]
    if not columns:
        columns = [
            field
            for field in DIRECT_RESULT_FALLBACK_FIELDS
            if any(str(row.get(field, "")).strip() for row in rows)
        ]
    if not columns:
        inferred = {key for row in rows for key in row.keys()}
        columns = [field for field in DIRECT_RESULT_FALLBACK_FIELDS if field in inferred] or sorted(inferred)
    labels = {"url": "URL"}
    headers = [labels.get(field, field.replace("_", " ").title()) for field in columns]
    lines = ["## Direct Script Results", "", f"| {' | '.join(headers)} |", f"| {' | '.join(['---'] * len(columns))} |"]
    for row in rows:
        values: list[str] = []
        for column_name in columns:
            value = str(row.get(column_name, "")).strip()
            if column_name == "url":
                value = value.replace("|", "%7C")
            else:
                value = value.replace("|", "\\|")
            values.append(value)
        lines.append(f"| {' | '.join(values)} |")
    lines.extend(
        [
            "",
            (
                "Browser-opening step executed successfully."
                if browser_open_ran
                else (
                    "Structured results were collected directly from the existing script output. "
                    "Browser-opening step was not executed."
                    if requested_browser_open
                    else "Structured results were collected directly from the existing script output."
                )
            ),
        ]
    )
    return "\n".join(lines)


def _rewrite_answer_urls_from_helper(answer: str, urls: Sequence[str]) -> str:
    return answer


ROUTE_SAMPLING_PROFILES: dict[str, tuple[str, float, float, float, float]] = {
    "simple_question": ("simple_question", 0.35, 0.92, 0.02, 1.03),
    "mcp_tool": ("mcp_tool", 0.25, 0.90, 0.02, 1.05),
    "coding_edit": ("coding_edit", 0.25, 0.92, 0.02, 1.04),
    "debugging_reasoning": ("debugging_reasoning", 0.35, 0.92, 0.02, 1.02),
    "brainstorming_explanation": ("brainstorming_explanation", 0.70, 0.98, 0.01, 1.00),
}


def _sampling_profile_for_route(prompt: str, selected_route: str) -> tuple[str, float, float, float, float]:
    lowered = prompt.lower()
    if selected_route == "simple_answer":
        if "SIMPLE_DIRECT_QUESTION_TEMPERATURE" in globals():
            return ("deepseek_simple_question", float(globals()["SIMPLE_DIRECT_QUESTION_TEMPERATURE"]), 0.92, 0.02, 1.03)
        return ROUTE_SAMPLING_PROFILES["simple_question"]
    if selected_route in {"direct_existing_entrypoint", "tool_loop"}:
        return ROUTE_SAMPLING_PROFILES["mcp_tool"]
    if re.search(r"\b(edit|modify|change|update|fix|refactor|rewrite|implement|patch|code|coding)\b", lowered):
        return ROUTE_SAMPLING_PROFILES["coding_edit"]
    if re.search(r"\b(debug|diagnose|traceback|exception|failure|failed|failing|bug|regression|smoke test|verify|test)\b", lowered):
        return ROUTE_SAMPLING_PROFILES["debugging_reasoning"]
    if re.search(r"\b(brainstorm|ideas?|creative|explain|summary|summarize|compare|recommend|what do you think)\b", lowered):
        return ROUTE_SAMPLING_PROFILES["brainstorming_explanation"]
    if re.search(r"\b(mcp|tool|tools|browser|url|urls|open|tabs?|run|command|script|server)\b", lowered):
        return ROUTE_SAMPLING_PROFILES["mcp_tool"]
    return ROUTE_SAMPLING_PROFILES["debugging_reasoning"]


def _sampling_value(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _apply_sampling_profile(config: Any, profile: tuple[str, float, float, float, float]) -> None:
    _, temperature, top_p, min_p, repeat_penalty = profile
    setattr(config, "temperature", temperature)
    setattr(config, "top_p", top_p)
    setattr(config, "min_p", min_p)
    setattr(config, "repeat_penalty", repeat_penalty)


def _sampling_profile_status(profile: tuple[str, float, float, float, float]) -> str:
    name, temperature, top_p, min_p, repeat_penalty = profile
    return (
        "Sampling profile: "
        f"{name} temp={_sampling_value(temperature)} top_p={_sampling_value(top_p)} "
        f"min_p={_sampling_value(min_p)} repeat_penalty={_sampling_value(repeat_penalty)} "
        "(temporary route profile; original GUI/config values restore after this request)."
    )


def _prompt_requests_project_launch(base: Any, prompt: str) -> bool:
    cleaned = prompt.strip()
    if not cleaned or PROJECT_LAUNCH_EXPLANATION_PATTERN.search(cleaned):
        return False
    lowered = cleaned.lower()
    if re.search(
        r"\b(?:do not|don't|never|without)\b[^\n.;]{0,120}\b(?:run|start|launch)\b",
        lowered,
    ):
        return False
    mutation_forbidden = bool(
        re.search(
            r"\b(?:do not|don't|never|without)\b[^\n.;]{0,160}"
            r"\b(?:create|edit|modify|change|write|delete|remove|move|rename)\w*\b",
            lowered,
        )
    )
    if base.EDIT_INTENT_PATTERN.search(cleaned) and not mutation_forbidden:
        return False
    launch_match = PROJECT_LAUNCH_REQUEST_PATTERN.search(cleaned)
    if launch_match is None:
        return False
    leading_request = cleaned[: launch_match.start()]
    if not PROJECT_LAUNCH_BENIGN_LEADING_PATTERN.fullmatch(leading_request):
        return False
    trailing_request = cleaned[launch_match.end() :]
    return bool(PROJECT_LAUNCH_BENIGN_TRAILING_PATTERN.fullmatch(trailing_request))


def _project_launch_display_command(command: Sequence[str]) -> str:
    return subprocess.list2cmdline([str(item) for item in command]) if os.name == "nt" else shlex.join(
        str(item) for item in command
    )


def _project_launch_windows_argument(base: Any, value: str) -> str:
    candidate = Path(value)
    if not candidate.is_absolute() or not _path_exists_safely(candidate):
        return value
    translator = getattr(base, "wsl_path_to_windows", None)
    if callable(translator):
        translated = translator(candidate)
        if translated:
            return str(translated)
    fallback = getattr(base, "wsl_path_to_windows_path", None)
    if callable(fallback):
        return str(fallback(candidate))
    return value


def _project_launch_wrap_command(base: Any, workspace: Path, command: Sequence[str]) -> list[str]:
    normalized = [str(item) for item in command if str(item)]
    if not normalized:
        return []
    executable = normalized[0]
    executable_suffix = Path(executable).suffix.lower()
    if (
        callable(getattr(base, "in_wsl", None))
        and base.in_wsl()
        and shutil.which("powershell.exe") is not None
        and executable_suffix in {".exe", ".cmd", ".bat"}
    ):
        workspace_windows = _workspace_windows_path(base, workspace)
        translated = [_project_launch_windows_argument(base, part) for part in normalized]
        script = "; ".join(
            [
                "$ProgressPreference = 'SilentlyContinue'",
                f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
                "& " + " ".join(_powershell_single_quote(part) for part in translated),
            ]
        )
        return ["powershell.exe", "-NoProfile", "-Command", script]
    return normalized


def _project_launch_file_plan(base: Any, workspace: Path, script_path: Path, *, source: str) -> dict[str, Any]:
    suffix = script_path.suffix.lower()
    if suffix == ".py":
        interpreter = _preferred_project_python(workspace)
        if interpreter is None:
            return {
                "status": "missing",
                "reason": "No compatible project-local Python interpreter was found.",
            }
        command = _project_launch_wrap_command(base, workspace, [str(interpreter), str(script_path)])
    elif suffix == ".ps1":
        if shutil.which("powershell.exe") is not None:
            workspace_windows = _workspace_windows_path(base, workspace)
            script_windows = _project_launch_windows_argument(base, str(script_path))
            powershell_script = "; ".join(
                [
                    "$ProgressPreference = 'SilentlyContinue'",
                    f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
                    f"& {_powershell_single_quote(script_windows)}",
                ]
            )
            command = ["powershell.exe", "-NoProfile", "-Command", powershell_script]
        elif shutil.which("pwsh") is not None:
            command = [str(shutil.which("pwsh")), "-NoProfile", "-File", str(script_path)]
        else:
            return {"status": "missing", "reason": "PowerShell is unavailable for the selected .ps1 entrypoint."}
    elif suffix == ".sh":
        shell = shutil.which("bash") or shutil.which("sh")
        if shell is None:
            return {"status": "missing", "reason": "No compatible shell is available for the selected .sh entrypoint."}
        command = [str(shell), str(script_path)]
    elif suffix in {".cmd", ".bat"}:
        command = _project_launch_wrap_command(base, workspace, [str(script_path)])
        if command == [str(script_path)] and os.name != "nt":
            return {
                "status": "missing",
                "reason": "Windows command interop is unavailable for the selected batch entrypoint.",
            }
    else:
        return {"status": "missing", "reason": f"Unsupported project launch entrypoint: {script_path.name}"}
    return {
        "status": "ready",
        "label": str(script_path.relative_to(workspace)),
        "command": command,
        "source": source,
    }


def _project_launch_command_executable(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return str(resolved)
    return None


def _project_launch_manifest_candidates(base: Any, workspace: Path) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    package_path = workspace / "package.json"
    if package_path.is_file():
        with suppress(Exception):
            payload = json.loads(package_path.read_text(encoding="utf-8", errors="ignore"))
            scripts = payload.get("scripts") if isinstance(payload, dict) else None
            if isinstance(scripts, dict):
                manager = None
                if (workspace / "pnpm-lock.yaml").exists():
                    manager = _project_launch_command_executable("pnpm", "pnpm.cmd")
                manager = manager or _project_launch_command_executable("npm", "npm.cmd")
                if manager:
                    names = [name for name in ("start", "dev", "serve") if str(scripts.get(name, "")).strip()]
                    for name in names:
                        rank = 10 if name == "start" else 11
                        command = _project_launch_wrap_command(base, workspace, [manager, "run", name])
                        candidates.append(
                            {
                                "status": "ready",
                                "rank": rank,
                                "label": f"package.json script {name}",
                                "command": command,
                                "source": "package.json",
                            }
                        )

    for make_name in MAKEFILE_CANDIDATE_NAMES:
        make_path = workspace / make_name
        if not make_path.is_file():
            continue
        make_executable = _project_launch_command_executable("make", "make.exe")
        if make_executable is None:
            break
        with suppress(Exception):
            make_text = make_path.read_text(encoding="utf-8", errors="ignore")
            targets = set(re.findall(r"(?m)^([A-Za-z0-9_.-]+)\s*:(?![=])", make_text))
            for target in ("start", "run", "serve"):
                if target not in targets:
                    continue
                candidates.append(
                    {
                        "status": "ready",
                        "rank": 20 if target == "start" else 21,
                        "label": f"{make_name} target {target}",
                        "command": _project_launch_wrap_command(base, workspace, [make_executable, target]),
                        "source": make_name,
                    }
                )
        break

    pyproject_path = workspace / "pyproject.toml"
    uv_executable = _project_launch_command_executable("uv", "uv.exe")
    if pyproject_path.is_file() and uv_executable:
        with suppress(Exception):
            pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8", errors="ignore"))
            project = pyproject.get("project") if isinstance(pyproject, dict) else None
            scripts = project.get("scripts") if isinstance(project, dict) else None
            if isinstance(scripts, dict):
                for name in sorted(str(item) for item in scripts if str(item).strip()):
                    candidates.append(
                        {
                            "status": "ready",
                            "rank": 25,
                            "label": f"pyproject script {name}",
                            "command": _project_launch_wrap_command(
                                base,
                                workspace,
                                [uv_executable, "run", name],
                            ),
                            "source": "pyproject.toml",
                        }
                    )

    for filename in PROJECT_LAUNCH_ROOT_FILENAMES:
        path = workspace / filename
        if path.is_file():
            plan = _project_launch_file_plan(base, workspace, path.resolve(), source="root entrypoint")
            if plan.get("status") == "ready":
                plan["rank"] = 30
                candidates.append(plan)

    cargo = _project_launch_command_executable("cargo", "cargo.exe")
    if (workspace / "Cargo.toml").is_file() and cargo:
        candidates.append(
            {
                "status": "ready",
                "rank": 40,
                "label": "Cargo.toml via cargo run",
                "command": _project_launch_wrap_command(base, workspace, [cargo, "run"]),
                "source": "Cargo.toml",
            }
        )
    go = _project_launch_command_executable("go", "go.exe")
    if (workspace / "go.mod").is_file() and go:
        candidates.append(
            {
                "status": "ready",
                "rank": 40,
                "label": "go.mod via go run .",
                "command": _project_launch_wrap_command(base, workspace, [go, "run", "."]),
                "source": "go.mod",
            }
        )
    dotnet = _project_launch_command_executable("dotnet", "dotnet.exe")
    csproj_files = sorted(workspace.glob("*.csproj"))
    if len(csproj_files) == 1 and dotnet:
        project_path = csproj_files[0].resolve()
        candidates.append(
            {
                "status": "ready",
                "rank": 40,
                "label": f"{project_path.name} via dotnet run",
                "command": _project_launch_wrap_command(
                    base,
                    workspace,
                    [dotnet, "run", "--project", str(project_path)],
                ),
                "source": project_path.name,
            }
        )
    return candidates


def _resolve_project_launch_plan(base: Any, workspace: Path, prompt: str) -> dict[str, Any]:
    explicit = _resolve_existing_entrypoint_spec(base, workspace, prompt)
    if explicit is not None and str(explicit.get("origin", "")) != "implicit_discovery":
        if explicit.get("kind") == "file":
            return _project_launch_file_plan(
                base,
                workspace,
                Path(str(explicit["path"])).resolve(),
                source="explicit prompt entrypoint",
            )
        command_text = str(explicit.get("command", "")).strip()
        if command_text:
            try:
                command = shlex.split(command_text, posix=os.name != "nt")
            except ValueError as exc:
                return {"status": "missing", "reason": f"Unable to parse the explicit launch command: {exc}"}
            return {
                "status": "ready",
                "label": str(explicit.get("label", command_text)),
                "command": _project_launch_wrap_command(base, workspace, command),
                "source": "explicit prompt command",
            }

    candidates = _project_launch_manifest_candidates(base, workspace)
    if not candidates:
        return {
            "status": "missing",
            "reason": (
                "No deterministic existing launch command was found in package.json, pyproject.toml, a Makefile, "
                "a conventional root entrypoint, Cargo.toml, go.mod, or a single root .csproj."
            ),
        }
    best_rank = min(int(item.get("rank", 999)) for item in candidates)
    best = [item for item in candidates if int(item.get("rank", 999)) == best_rank]
    unique: dict[tuple[str, ...], dict[str, Any]] = {}
    for item in best:
        unique.setdefault(tuple(str(part) for part in item.get("command", [])), item)
    if len(unique) != 1:
        return {
            "status": "ambiguous",
            "candidates": [str(item.get("label", "launch command")) for item in unique.values()],
        }
    return next(iter(unique.values()))


def _truthy(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _path_hash(path: Path) -> str:
    return hashlib.sha256(path.resolve().as_posix().encode("utf-8")).hexdigest()[:16]


def _read_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return {}


def _normalize_thinking_effort(value: str | None) -> str:
    normalized = (value or "default").strip().lower()
    if normalized == "default":
        return "default"
    return normalized if normalized in THINKING_EFFORT_OPTIONS else "default"


def _display_thinking_effort(value: str | None) -> str:
    normalized = _normalize_thinking_effort(value)
    return "Default" if normalized == "default" else normalized


def _parse_thinking_effort_cli_value(value: str) -> str:
    normalized = _normalize_thinking_effort(value)
    if normalized == "default" or normalized == (value or "").strip().lower():
        return normalized
    choices = ", ".join(THINKING_EFFORT_DISPLAY_OPTIONS)
    raise SystemExit(f"--thinking-effort must be one of: {choices}")


def _thinking_effort_guidance(value: str | None) -> str:
    normalized = _normalize_thinking_effort(value)
    if normalized == "low":
        return textwrap.dedent(
            """
            Thinking effort preset: low
            - Prefer the shortest direct path to completion.
            - Avoid exploratory tool use unless tools are clearly necessary.
            - Keep answers compact and avoid extra verification for routine facts.
            """
        ).strip()
    if normalized == "medium":
        return textwrap.dedent(
            """
            Thinking effort preset: medium
            - Use balanced effort.
            - Use tools and workspace context when they are clearly helpful.
            - Do a light sanity check before finalizing non-trivial answers.
            """
        ).strip()
    if normalized == "high":
        return textwrap.dedent(
            """
            Thinking effort preset: high
            - Reason carefully before acting.
            - For project questions or tasks, inspect relevant files or tool results before concluding.
            - Verify important claims and outputs when practical before the final answer.
            """
        ).strip()
    if normalized == "xhigh":
        return textwrap.dedent(
            """
            Thinking effort preset: xhigh
            - Reason very carefully and persist longer on non-trivial tasks.
            - Inspect multiple relevant files or tool results before concluding when the task depends on project context.
            - Verify key assumptions and results before the final answer when practical.
            """
        ).strip()
    return ""


@dataclass
class LocalOnlyConfig:
    sandbox_default: str = "off"
    codeintel_enabled: bool = True
    codeintel_max_files: int = 4000
    plugins_enabled: bool = True
    background_jobs_enabled: bool = True
    background_job_sandbox: str = "off"
    local_only_install_wheelhouse: str = ""

    @classmethod
    def load(cls, runtime_workspace: Path, active_workspace: Path) -> "LocalOnlyConfig":
        config = cls()
        for root in (runtime_workspace, active_workspace):
            data = _read_toml(root / LOCAL_ONLY_DIR_NAME / LOCAL_ONLY_CONFIG_NAME)
            if not data:
                continue
            config.sandbox_default = str(data.get("sandbox_default", config.sandbox_default)).strip() or config.sandbox_default
            config.codeintel_enabled = _truthy(data.get("codeintel_enabled"), config.codeintel_enabled)
            with suppress(Exception):
                config.codeintel_max_files = max(1, int(data.get("codeintel_max_files", config.codeintel_max_files)))
            config.plugins_enabled = _truthy(data.get("plugins_enabled"), config.plugins_enabled)
            config.background_jobs_enabled = _truthy(
                data.get("background_jobs_enabled"),
                config.background_jobs_enabled,
            )
            background_job_sandbox = str(data.get("background_job_sandbox", config.background_job_sandbox)).strip()
            if background_job_sandbox:
                config.background_job_sandbox = background_job_sandbox
            wheelhouse = str(data.get("local_only_install_wheelhouse", config.local_only_install_wheelhouse)).strip()
            if wheelhouse:
                config.local_only_install_wheelhouse = wheelhouse
        return config


@dataclass
class LocalPluginManifest:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    allowed_tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "triggers": self.triggers,
            "allowed_tools": self.allowed_tools,
            "system_prompt": self.system_prompt,
            "path": str(self.path) if self.path is not None else "",
        }


class LocalPluginRegistry:
    def __init__(self, runtime_workspace: Path, active_workspace: Path) -> None:
        self.runtime_workspace = runtime_workspace
        self.active_workspace = active_workspace
        self.plugins: list[LocalPluginManifest] = []
        self.reload()

    def _plugin_dirs(self) -> list[Path]:
        return [
            self.runtime_workspace / LOCAL_ONLY_DIR_NAME / LOCAL_ONLY_PLUGINS_DIR,
            self.active_workspace / LOCAL_ONLY_DIR_NAME / LOCAL_ONLY_PLUGINS_DIR,
        ]

    def reload(self) -> None:
        plugins: list[LocalPluginManifest] = []
        for directory in self._plugin_dirs():
            if not directory.exists():
                continue
            for candidate in sorted(directory.glob("*.toml")):
                data = _read_toml(candidate)
                name = str(data.get("name", candidate.stem)).strip() or candidate.stem
                description = str(data.get("description", "")).strip()
                triggers_raw = data.get("triggers", [])
                allowed_tools_raw = data.get("allowed_tools", [])
                system_prompt = str(data.get("system_prompt", "")).strip()
                triggers = [str(item).strip().lower() for item in triggers_raw if str(item).strip()]
                allowed_tools = [str(item).strip() for item in allowed_tools_raw if str(item).strip()]
                plugins.append(
                    LocalPluginManifest(
                        name=name,
                        description=description,
                        triggers=triggers,
                        allowed_tools=allowed_tools,
                        system_prompt=system_prompt,
                        path=candidate,
                    )
                )
        self.plugins = plugins

    def list_plugins(self) -> list[dict[str, Any]]:
        return [plugin.to_dict() for plugin in self.plugins]

    def read_plugin(self, name: str) -> dict[str, Any]:
        normalized = name.strip().lower()
        for plugin in self.plugins:
            if plugin.name.lower() == normalized:
                return plugin.to_dict()
        raise KeyError(name)

    def select_for_prompt(self, prompt: str, max_results: int = 3) -> list[LocalPluginManifest]:
        normalized = prompt.lower()
        selected: list[tuple[int, LocalPluginManifest]] = []
        for plugin in self.plugins:
            score = 0
            if plugin.name.lower() in normalized:
                score += 10
            for trigger in plugin.triggers:
                if trigger and trigger in normalized:
                    score += 5
            if score > 0:
                selected.append((score, plugin))
        selected.sort(key=lambda item: (-item[0], item[1].name.lower()))
        return [plugin for _, plugin in selected[:max_results]]

    def render_for_prompt(self, prompt: str) -> str:
        chosen = self.select_for_prompt(prompt)
        if not chosen:
            return "None"
        blocks: list[str] = []
        for plugin in chosen:
            blocks.append(
                textwrap.dedent(
                    f"""
                    Plugin: {plugin.name}
                    Description: {plugin.description or "None"}
                    Allowed tools: {", ".join(plugin.allowed_tools) if plugin.allowed_tools else "Unspecified"}
                    Instructions:
                    {plugin.system_prompt or "None"}
                    """
                ).strip()
            )
        return "\n\n".join(blocks)


def _local_plugins_available(runtime_workspace: Path, active_workspace: Path) -> bool:
    for directory in (
        runtime_workspace / LOCAL_ONLY_DIR_NAME / LOCAL_ONLY_PLUGINS_DIR,
        active_workspace / LOCAL_ONLY_DIR_NAME / LOCAL_ONLY_PLUGINS_DIR,
    ):
        if not directory.exists():
            continue
        with suppress(Exception):
            if any(directory.glob("*.toml")):
                return True
    return False


def _local_skills_root(runtime_workspace: Path) -> Path:
    return runtime_workspace / ".skills"


def _local_skills_available(runtime_workspace: Path) -> bool:
    root = _local_skills_root(runtime_workspace)
    if not root.exists() or not root.is_dir():
        return False
    with suppress(Exception):
        for candidate in root.iterdir():
            if candidate.name.startswith("."):
                continue
            return True
    return False


def _sandbox_features_enabled(local_config: LocalOnlyConfig) -> bool:
    values = [
        getattr(local_config, "sandbox_default", ""),
        getattr(local_config, "background_job_sandbox", ""),
    ]
    return any(str(value).strip().lower() not in {"", "off", "none"} for value in values)


class _DisabledLocalPluginRegistry:
    def __init__(self) -> None:
        self.plugins: list[LocalPluginManifest] = []

    def reload(self) -> None:
        return None

    def list_plugins(self) -> list[dict[str, Any]]:
        return []

    def read_plugin(self, name: str) -> dict[str, Any]:
        raise KeyError(name)

    def select_for_prompt(self, prompt: str, max_results: int = 3) -> list[LocalPluginManifest]:
        return []

    def render_for_prompt(self, prompt: str) -> str:
        return "None"


class _DisabledLocalBackgroundJobManager:
    def list_jobs(self) -> list[dict[str, Any]]:
        return []

    def read_job(self, job_id: str, max_chars: int = 12000) -> dict[str, Any]:
        raise FileNotFoundError(f"Unknown local background job: {job_id}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        raise FileNotFoundError(f"Unknown local background job: {job_id}")

    def start(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local background jobs are disabled for this runtime.")

    def start_command(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local background jobs are disabled for this runtime.")


class _DisabledLocalSandboxManager:
    def list_sandboxes(self) -> list[dict[str, Any]]:
        return []

    def create(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def write_file(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def replace_text(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def delete_path(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def make_directory(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def move_path(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def run_command(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def diff(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def apply_back(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")

    def destroy(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Local sandboxes are disabled for this runtime.")


class LocalCodeIntel:
    def __init__(
        self,
        workspace: Path,
        runtime_workspace: Path,
        *,
        excluded_dirs: set[str],
        text_suffixes: set[str],
        max_files: int,
    ) -> None:
        self.workspace = workspace.resolve()
        self.runtime_workspace = runtime_workspace.resolve()
        self.excluded_dirs = set(excluded_dirs)
        self.text_suffixes = set(text_suffixes)
        self.max_files = max_files
        self.cache_dir = self.runtime_workspace / ".cache" / "codeintel"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.cache_dir / f"{_path_hash(self.workspace)}.json"
        self._cache: dict[str, Any] = {"workspace": self.workspace.as_posix(), "files": {}}
        self._loaded = False
        self._last_refresh = 0.0

    def _load_cache(self) -> None:
        if self._loaded:
            return
        if self.cache_path.exists():
            with suppress(Exception):
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8", errors="ignore"))
        self._loaded = True

    def _save_cache(self) -> None:
        self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=True, indent=2), encoding="utf-8")

    def _iter_files(self) -> list[Path]:
        candidates: list[Path] = []
        for root, dirnames, filenames in os.walk(self.workspace, topdown=True):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in self.excluded_dirs]
            root_path = Path(root)
            for filename in filenames:
                path = root_path / filename
                if path.suffix.lower() in self.text_suffixes:
                    candidates.append(path)
                    if len(candidates) >= self.max_files:
                        return candidates
        return candidates

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.workspace).as_posix()

    @staticmethod
    def _symbol_container(scope: list[str]) -> str:
        return ".".join(scope) if scope else "<module>"

    def _parse_python(self, path: Path, text: str) -> dict[str, Any]:
        tree = ast.parse(text, filename=str(path))
        symbols: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        calls: list[dict[str, Any]] = []
        type_hints: list[dict[str, Any]] = []
        scope: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                container = self_outer._symbol_container(scope)
                symbols.append(
                    {
                        "name": node.name,
                        "qualified_name": ".".join([*scope, node.name]) if scope else node.name,
                        "kind": "class",
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "container": container,
                    }
                )
                scope.append(node.name)
                self.generic_visit(node)
                scope.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                container = self_outer._symbol_container(scope)
                qualified = ".".join([*scope, node.name]) if scope else node.name
                symbols.append(
                    {
                        "name": node.name,
                        "qualified_name": qualified,
                        "kind": "method" if scope else "function",
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "container": container,
                    }
                )
                if node.returns is not None:
                    with suppress(Exception):
                        type_hints.append({"name": qualified, "type": ast.unparse(node.returns), "kind": "return"})
                for argument in [*node.args.args, *node.args.kwonlyargs]:
                    if argument.annotation is not None:
                        with suppress(Exception):
                            type_hints.append(
                                {
                                    "name": f"{qualified}.{argument.arg}",
                                    "type": ast.unparse(argument.annotation),
                                    "kind": "parameter",
                                }
                            )
                scope.append(node.name)
                self.generic_visit(node)
                scope.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.visit_FunctionDef(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if isinstance(node.target, ast.Name):
                    with suppress(Exception):
                        type_hints.append(
                            {
                                "name": ".".join([*scope, node.target.id]) if scope else node.target.id,
                                "type": ast.unparse(node.annotation),
                                "kind": "variable",
                            }
                        )
                self.generic_visit(node)

            def visit_Name(self, node: ast.Name) -> None:
                references.append(
                    {
                        "name": node.id,
                        "line": node.lineno,
                        "column": node.col_offset + 1,
                        "context": type(node.ctx).__name__,
                        "container": self_outer._symbol_container(scope),
                    }
                )
                self.generic_visit(node)

            def visit_Call(self, node: ast.Call) -> None:
                callee = ""
                if isinstance(node.func, ast.Name):
                    callee = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    callee = node.func.attr
                if callee:
                    calls.append(
                        {
                            "caller": self_outer._symbol_container(scope),
                            "callee": callee,
                            "line": node.lineno,
                        }
                    )
                self.generic_visit(node)

        self_outer = self
        Visitor().visit(tree)
        return {
            "symbols": symbols,
            "references": references,
            "calls": calls,
            "type_hints": type_hints,
        }

    def _parse_generic(self, path: Path, text: str) -> dict[str, Any]:
        symbols: list[dict[str, Any]] = []
        patterns = [
            (re.compile(r"^\s*class\s+([A-Za-z_][A-Za-z0-9_]*)"), "class"),
            (re.compile(r"^\s*(?:def|function)\s+([A-Za-z_][A-Za-z0-9_]*)"), "function"),
            (re.compile(r"^\s*(?:const|let|var|type|interface)\s+([A-Za-z_][A-Za-z0-9_]*)"), "symbol"),
        ]
        for index, line in enumerate(text.splitlines(), start=1):
            for pattern, kind in patterns:
                match = pattern.search(line)
                if match:
                    symbols.append(
                        {
                            "name": match.group(1),
                            "qualified_name": match.group(1),
                            "kind": kind,
                            "line": index,
                            "end_line": index,
                            "container": "<module>",
                        }
                    )
                    break
        return {"symbols": symbols, "references": [], "calls": [], "type_hints": []}

    def _refresh(self) -> None:
        self._load_cache()
        if time.time() - self._last_refresh < 2.0:
            return
        files = self._iter_files()
        cache_files = self._cache.setdefault("files", {})
        live: set[str] = set()
        changed = False
        for path in files:
            rel = self._relative(path)
            live.add(rel)
            stat = path.stat()
            cached = cache_files.get(rel)
            if cached and cached.get("mtime_ns") == stat.st_mtime_ns and cached.get("size") == stat.st_size:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            parsed = self._parse_python(path, text) if path.suffix.lower() == ".py" else self._parse_generic(path, text)
            cache_files[rel] = {
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "symbols": parsed["symbols"],
                "references": parsed["references"],
                "calls": parsed["calls"],
                "type_hints": parsed["type_hints"],
            }
            changed = True
        stale = [rel for rel in cache_files if rel not in live]
        for rel in stale:
            cache_files.pop(rel, None)
            changed = True
        if changed:
            self._save_cache()
        self._last_refresh = time.time()

    def document_symbols(self, path: str) -> dict[str, Any]:
        self._refresh()
        rel = self._relative((self.workspace / path).resolve()) if not Path(path).is_absolute() else self._relative(Path(path))
        data = self._cache["files"].get(rel, {})
        return {"path": rel, "symbols": data.get("symbols", [])}

    def workspace_symbols(self, query: str = "", max_results: int = 50) -> dict[str, Any]:
        self._refresh()
        normalized = query.strip().lower()
        results: list[dict[str, Any]] = []
        for rel, data in self._cache["files"].items():
            for symbol in data.get("symbols", []):
                haystack = f"{symbol['name']} {symbol.get('qualified_name', '')}".lower()
                if normalized and normalized not in haystack:
                    continue
                results.append({"path": rel, **symbol})
        results.sort(key=lambda item: (item["path"], item["line"], item["qualified_name"]))
        return {"query": query, "matches": results[:max_results]}

    def find_symbol_definitions(self, name: str, max_results: int = 50) -> dict[str, Any]:
        return self.workspace_symbols(query=name, max_results=max_results)

    def find_symbol_references(self, name: str, max_results: int = 100) -> dict[str, Any]:
        self._refresh()
        normalized = name.strip()
        if not normalized:
            return {"query": name, "matches": []}
        matches: list[dict[str, Any]] = []
        for rel, data in self._cache["files"].items():
            for reference in data.get("references", []):
                if reference.get("name") == normalized:
                    matches.append({"path": rel, **reference})
                    if len(matches) >= max_results:
                        return {"query": name, "matches": matches}
        token = re.compile(rf"\b{re.escape(normalized)}\b")
        for path in self._iter_files():
            rel = self._relative(path)
            if any(match["path"] == rel for match in matches):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if token.search(line):
                    matches.append(
                        {
                            "path": rel,
                            "line": line_number,
                            "column": token.search(line).start() + 1 if token.search(line) else 1,
                            "context": "TextSearch",
                            "container": "<text-search>",
                        }
                    )
                    if len(matches) >= max_results:
                        return {"query": name, "matches": matches}
        return {"query": name, "matches": matches}

    def find_callers(self, name: str, max_results: int = 50) -> dict[str, Any]:
        self._refresh()
        normalized = name.strip()
        matches: list[dict[str, Any]] = []
        for rel, data in self._cache["files"].items():
            for call in data.get("calls", []):
                if call.get("callee") == normalized or call.get("callee", "").endswith(f".{normalized}"):
                    matches.append({"path": rel, **call})
        return {"query": name, "matches": matches[:max_results]}

    def find_callees(self, name: str, max_results: int = 50) -> dict[str, Any]:
        self._refresh()
        normalized = name.strip()
        matches: list[dict[str, Any]] = []
        for rel, data in self._cache["files"].items():
            for call in data.get("calls", []):
                if call.get("caller") == normalized or call.get("caller", "").endswith(f".{normalized}"):
                    matches.append({"path": rel, **call})
        return {"query": name, "matches": matches[:max_results]}

    def symbol_type_info(self, name: str, path: str | None = None) -> dict[str, Any]:
        self._refresh()
        normalized = name.strip()
        matches: list[dict[str, Any]] = []
        target_rel = ""
        if path:
            candidate = Path(path)
            target_rel = self._relative((self.workspace / candidate).resolve()) if not candidate.is_absolute() else self._relative(candidate)
        for rel, data in self._cache["files"].items():
            if target_rel and rel != target_rel:
                continue
            for hint in data.get("type_hints", []):
                if hint.get("name") == normalized or hint.get("name", "").endswith(f".{normalized}"):
                    matches.append({"path": rel, **hint})
        return {"query": name, "path": target_rel or None, "matches": matches}

    def pyright_diagnostics(self, path: str) -> dict[str, Any]:
        target = (self.workspace / path).resolve()
        executable = shutil.which("basedpyright") or shutil.which("pyright")
        if executable is None:
            return {"path": self._relative(target), "available": False, "diagnostics": []}
        completed = subprocess.run(
            [executable, "--outputjson", str(target)],
            capture_output=True,
            text=True,
            cwd=self.workspace,
            timeout=120,
            check=False,
        )
        output = completed.stdout.strip() or completed.stderr.strip()
        if not output:
            return {"path": self._relative(target), "available": True, "diagnostics": []}
        try:
            payload = json.loads(output)
        except Exception:
            return {"path": self._relative(target), "available": True, "diagnostics": [{"raw": _shorten(output, 4000)}]}
        return {
            "path": self._relative(target),
            "available": True,
            "summary": payload.get("summary", {}),
            "diagnostics": payload.get("generalDiagnostics", []),
        }


@dataclass
class SandboxRecord:
    sandbox_id: str
    kind: str
    root: Path
    workspace: Path
    created_at: str
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "kind": self.kind,
            "root": self.root.as_posix(),
            "workspace": self.workspace.as_posix(),
            "created_at": self.created_at,
            "label": self.label,
        }


class LocalSandboxManager:
    def __init__(self, workspace: Path, runtime_workspace: Path, excluded_dirs: set[str], base: Any | None = None) -> None:
        self.workspace = workspace.resolve()
        self.runtime_workspace = runtime_workspace.resolve()
        self.excluded_dirs = set(excluded_dirs)
        self.base = base
        self.root = self.runtime_workspace / ".cache" / "sandboxes"
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, sandbox_id: str) -> Path:
        return self.root / sandbox_id / "meta.json"

    def _load_record(self, sandbox_id: str) -> SandboxRecord:
        meta_path = self._meta_path(sandbox_id)
        if not meta_path.exists():
            raise FileNotFoundError(f"Unknown sandbox: {sandbox_id}")
        payload = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
        return SandboxRecord(
            sandbox_id=payload["sandbox_id"],
            kind=payload["kind"],
            root=Path(payload["root"]),
            workspace=Path(payload["workspace"]),
            created_at=payload["created_at"],
            label=payload.get("label", ""),
        )

    def _save_record(self, record: SandboxRecord) -> None:
        meta_path = self._meta_path(record.sandbox_id)
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(record.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")

    def list_sandboxes(self) -> list[dict[str, Any]]:
        sandboxes: list[dict[str, Any]] = []
        for meta_path in sorted(self.root.glob("*/meta.json")):
            with suppress(Exception):
                payload = json.loads(meta_path.read_text(encoding="utf-8", errors="ignore"))
                sandboxes.append(payload)
        return sandboxes

    def create(self, label: str = "", mode: str = "auto") -> dict[str, Any]:
        sandbox_id = uuid.uuid4().hex[:12]
        sandbox_root = self.root / sandbox_id / "workspace"
        requested = (mode or "auto").strip().lower()
        kind = "copy"
        git_dir = self.workspace / ".git"
        if requested in {"auto", "git", "worktree"} and git_dir.exists() and shutil.which("git"):
            completed = subprocess.run(
                ["git", "-C", str(self.workspace), "worktree", "add", "--detach", str(sandbox_root), "HEAD"],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
            if completed.returncode == 0:
                kind = "git-worktree"
            elif requested in {"git", "worktree"}:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git worktree add failed")
        if not sandbox_root.exists():
            ignore = shutil.ignore_patterns(*sorted(self.excluded_dirs | {".git"}))
            shutil.copytree(self.workspace, sandbox_root, ignore=ignore)
            kind = "copy"
        record = SandboxRecord(
            sandbox_id=sandbox_id,
            kind=kind,
            root=sandbox_root,
            workspace=self.workspace,
            created_at=_now_stamp(),
            label=label.strip(),
        )
        self._save_record(record)
        return record.to_dict()

    def _resolve(self, sandbox_id: str, candidate: str) -> tuple[SandboxRecord, Path]:
        record = self._load_record(sandbox_id)
        raw = Path(candidate.strip()).expanduser()
        target = raw.resolve() if raw.is_absolute() else (record.root / raw).resolve()
        if not target.is_relative_to(record.root):
            raise ValueError(f"Sandbox path escapes the sandbox root: {candidate}")
        return record, target

    def write_file(self, sandbox_id: str, path: str, content: str, make_parents: bool = True) -> dict[str, Any]:
        record, target = self._resolve(sandbox_id, path)
        if make_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"sandbox_id": sandbox_id, "path": target.relative_to(record.root).as_posix(), "bytes_written": target.stat().st_size}

    def replace_text(self, sandbox_id: str, path: str, old_text: str, new_text: str, count: int = 0) -> dict[str, Any]:
        record, target = self._resolve(sandbox_id, path)
        original = target.read_text(encoding="utf-8", errors="ignore")
        replacements = original.count(old_text) if count == 0 else min(original.count(old_text), count)
        updated = original.replace(old_text, new_text, count) if count > 0 else original.replace(old_text, new_text)
        if updated != original:
            target.write_text(updated, encoding="utf-8")
        return {"sandbox_id": sandbox_id, "path": target.relative_to(record.root).as_posix(), "replacements": replacements}

    def delete_path(self, sandbox_id: str, path: str, recursive: bool = False) -> dict[str, Any]:
        record, target = self._resolve(sandbox_id, path)
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
        return {"sandbox_id": sandbox_id, "deleted": target.relative_to(record.root).as_posix(), "recursive": recursive}

    def make_directory(self, sandbox_id: str, path: str) -> dict[str, Any]:
        record, target = self._resolve(sandbox_id, path)
        target.mkdir(parents=True, exist_ok=True)
        return {"sandbox_id": sandbox_id, "path": target.relative_to(record.root).as_posix(), "created": True}

    def move_path(self, sandbox_id: str, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        record, source_path = self._resolve(sandbox_id, source)
        _, destination_path = self._resolve(sandbox_id, destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            if not overwrite:
                raise ValueError(f"Destination already exists: {destination}")
            if destination_path.is_dir():
                shutil.rmtree(destination_path)
            else:
                destination_path.unlink()
        shutil.move(str(source_path), str(destination_path))
        return {
            "sandbox_id": sandbox_id,
            "source": source_path.relative_to(record.root).as_posix(),
            "destination": destination_path.relative_to(record.root).as_posix(),
        }

    def run_command(self, sandbox_id: str, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        record = self._load_record(sandbox_id)
        payload = _run_shell_command(self.base, record.root, command, timeout_seconds)
        payload["sandbox_id"] = sandbox_id
        return payload

    def _copy_snapshot(self, root: Path) -> dict[str, str]:
        snapshot: dict[str, str] = {}
        for current_root, dirnames, filenames in os.walk(root, topdown=True):
            dirnames[:] = [dirname for dirname in dirnames if dirname not in self.excluded_dirs and dirname != ".git"]
            root_path = Path(current_root)
            for filename in filenames:
                path = root_path / filename
                rel = path.relative_to(root).as_posix()
                snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
        return snapshot

    def _changed_entries(self, record: SandboxRecord) -> list[dict[str, Any]]:
        if record.kind == "git-worktree":
            completed = subprocess.run(
                ["git", "-C", str(record.root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            entries: list[dict[str, Any]] = []
            for line in completed.stdout.splitlines():
                if len(line) < 4:
                    continue
                entries.append({"status": line[:2].strip(), "path": line[3:].strip()})
            return entries
        before = self._copy_snapshot(record.workspace)
        after = self._copy_snapshot(record.root)
        changed: list[dict[str, Any]] = []
        all_paths = sorted(set(before) | set(after))
        for rel in all_paths:
            if rel not in before:
                changed.append({"status": "A", "path": rel})
            elif rel not in after:
                changed.append({"status": "D", "path": rel})
            elif before[rel] != after[rel]:
                changed.append({"status": "M", "path": rel})
        return changed

    def diff(self, sandbox_id: str, max_chars: int = LOCAL_ONLY_MAX_DIFF_CHARS) -> dict[str, Any]:
        record = self._load_record(sandbox_id)
        changed = self._changed_entries(record)
        chunks: list[str] = []
        for item in changed[:25]:
            rel = item["path"]
            original = record.workspace / rel
            modified = record.root / rel
            if original.exists() and modified.exists() and original.suffix.lower() in {".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml", ".js", ".ts"}:
                original_lines = original.read_text(encoding="utf-8", errors="ignore").splitlines()
                modified_lines = modified.read_text(encoding="utf-8", errors="ignore").splitlines()
                diff_text = "\n".join(
                    difflib.unified_diff(
                        original_lines,
                        modified_lines,
                        fromfile=original.as_posix(),
                        tofile=modified.as_posix(),
                        lineterm="",
                    )
                )
                if diff_text:
                    chunks.append(diff_text)
            else:
                chunks.append(f"{item['status']} {rel}")
            if sum(len(chunk) for chunk in chunks) >= max_chars:
                break
        return {
            "sandbox_id": sandbox_id,
            "kind": record.kind,
            "changed_files": changed,
            "diff": _shorten("\n\n".join(chunks), max_chars),
        }

    def apply_back(self, sandbox_id: str, overwrite: bool = True) -> dict[str, Any]:
        record = self._load_record(sandbox_id)
        changed = self._changed_entries(record)
        applied: list[dict[str, Any]] = []
        for item in changed:
            rel = item["path"]
            source = record.root / rel
            destination = record.workspace / rel
            status = item["status"]
            if status == "D":
                if destination.exists():
                    if destination.is_dir():
                        shutil.rmtree(destination)
                    else:
                        destination.unlink()
                applied.append({"path": rel, "action": "deleted"})
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not overwrite and status != "M":
                raise FileExistsError(rel)
            shutil.copy2(source, destination)
            applied.append({"path": rel, "action": "copied"})
        return {"sandbox_id": sandbox_id, "applied": applied}

    def destroy(self, sandbox_id: str) -> dict[str, Any]:
        record = self._load_record(sandbox_id)
        if record.kind == "git-worktree":
            subprocess.run(
                ["git", "-C", str(record.workspace), "worktree", "remove", "--force", str(record.root)],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        else:
            shutil.rmtree(record.root, ignore_errors=True)
        shutil.rmtree(self.root / sandbox_id, ignore_errors=True)
        return {"sandbox_id": sandbox_id, "destroyed": True}


class LocalMCPServerManager:
    def __init__(self, workspace: Path, runtime_workspace: Path, base: Any | None = None) -> None:
        self.workspace = workspace.resolve()
        self.runtime_workspace = runtime_workspace.resolve()
        self.base = base
        self.root = self.runtime_workspace / ".cache" / "mcp_servers"
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, server_id: str) -> Path:
        return self.root / server_id / "meta.json"

    def _write_meta(self, server_id: str, payload: dict[str, Any]) -> None:
        path = self._meta_path(server_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _read_meta(self, server_id: str) -> dict[str, Any]:
        path = self._meta_path(server_id)
        if not path.exists():
            raise FileNotFoundError(server_id)
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))

    def _refresh_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        pid = int(payload.get("pid") or 0)
        if pid <= 0:
            return payload
        with suppress(Exception):
            os.kill(pid, 0)
            return payload
        if payload.get("status") not in {"stopped", "failed"}:
            payload["status"] = "stopped"
            payload["finished_at"] = payload.get("finished_at") or _now_stamp()
            self._write_meta(str(payload["server_id"]), payload)
        return payload

    def _resolve_workspace_path(self, workspace: Path, candidate: str, *, allow_missing: bool = True) -> Path:
        if self.base is not None and hasattr(self.base, "resolve_workspace_path"):
            return self.base.resolve_workspace_path(
                workspace,
                candidate,
                allow_missing=allow_missing,
                allow_external=True,
            )
        normalized = candidate.strip()
        if "\\" in normalized and not re.match(r"^[A-Za-z]:[\\/]", normalized):
            normalized = normalized.replace("\\", "/")
        raw = Path(normalized).expanduser()
        resolved = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
        if not allow_missing and not resolved.exists():
            raise FileNotFoundError(candidate)
        return resolved

    def _resolve_python(self, workspace: Path, python_path: str | None = None) -> Path:
        if python_path:
            return self._resolve_workspace_path(workspace, python_path, allow_missing=False)
        preferred = _preferred_project_python(workspace)
        if preferred is not None:
            return preferred.absolute()
        return Path(sys.executable).absolute()

    def _normalize_env(self, env: dict[str, str] | None = None) -> dict[str, str]:
        merged = {}
        if env:
            for key, value in env.items():
                normalized_key = str(key).strip()
                if normalized_key:
                    merged[normalized_key] = str(value)
        merged.setdefault("QUBITZ_ALLOW_EMBED_ONLINE", "1")
        return merged

    def _maybe_windows_arg(self, workspace: Path, value: str) -> str:
        if not callable(getattr(self.base, "in_wsl", None)) or not self.base.in_wsl():
            return value
        if isinstance(getattr(self.base, "WINDOWS_DRIVE_PATH_PATTERN", None), re.Pattern):
            if self.base.WINDOWS_DRIVE_PATH_PATTERN.match(value):
                return value.replace("/", "\\")
        candidate = Path(value).expanduser()
        resolved: Path | None = None
        if candidate.is_absolute():
            resolved = candidate.resolve()
        else:
            local_candidate = (workspace / candidate).resolve()
            if local_candidate.exists():
                resolved = local_candidate
        if resolved is None:
            return value
        translator = getattr(self.base, "wsl_path_to_windows", None)
        if callable(translator):
            translated = translator(resolved)
            if translated:
                return str(translated)
        fallback = getattr(self.base, "wsl_path_to_windows_path", None)
        if callable(fallback):
            return str(fallback(resolved))
        return str(resolved)

    def _build_python_command(self, workspace: Path, python_path: Path, arguments: Sequence[str]) -> list[str]:
        command = [str(python_path), *[str(item) for item in arguments]]
        if (
            callable(getattr(self.base, "in_wsl", None))
            and self.base.in_wsl()
            and shutil.which("powershell.exe") is not None
            and str(python_path).lower().endswith(".exe")
        ):
            workspace_windows = _workspace_windows_path(self.base, workspace)
            translated = [self._maybe_windows_arg(workspace, part) for part in command]
            script_lines = [
                "$ProgressPreference = 'SilentlyContinue'",
                f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
                "& " + " ".join(_powershell_single_quote(part) for part in translated),
            ]
            return ["powershell.exe", "-NoProfile", "-Command", "; ".join(script_lines)]
        return command

    def _run_python(
        self,
        workspace: Path,
        python_path: Path,
        arguments: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 120,
    ) -> subprocess.CompletedProcess[str]:
        merged_env = os.environ.copy()
        merged_env.update(self._normalize_env(env))
        command = self._build_python_command(workspace, python_path, arguments)
        return subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=max(1, timeout_seconds),
            check=False,
            env=merged_env,
        )

    def _popen_python(
        self,
        workspace: Path,
        python_path: Path,
        arguments: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        log_path: Path,
    ) -> tuple[subprocess.Popen[str], list[str]]:
        merged_env = os.environ.copy()
        merged_env.update(self._normalize_env(env))
        command = self._build_python_command(workspace, python_path, arguments)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("w", encoding="utf-8", errors="ignore")
        try:
            process = subprocess.Popen(
                command,
                cwd=workspace,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                env=merged_env,
            )
        except Exception:
            log_handle.close()
            raise
        return process, command

    def _resolve_reference(self, workspace: Path, server_reference: str) -> Path | str:
        reference = server_reference.strip()
        if not reference:
            raise ValueError("server_reference must not be empty.")
        if "\n" in reference or "\r" in reference:
            raise ValueError(
                "server_reference must be a URL, script path, or .mcp.json path, not inline script text."
            )
        if "://" in reference:
            return reference
        return self._resolve_workspace_path(workspace, reference, allow_missing=False)

    def _probe_tools(
        self,
        workspace: Path,
        *,
        server_reference: str,
        python_path: Path,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        reference = self._resolve_reference(workspace, server_reference)
        reference_mode = "path" if isinstance(reference, Path) else "text"
        reference_value = str(reference)
        script = textwrap.dedent(
            """
            import asyncio
            import json
            import os
            import sys
            from contextlib import asynccontextmanager
            from pathlib import Path
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client

            BEGIN = "QUBITZ_JSON_BEGIN"
            END = "QUBITZ_JSON_END"

            @asynccontextmanager
            async def open_session(ref_mode: str, ref_value: str):
                if ref_mode == "text":
                    async with streamable_http_client(ref_value) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                    return
                path = Path(ref_value)
                if path.suffix.lower() == ".json":
                    config = json.loads(path.read_text(encoding="utf-8"))
                    servers = config.get("mcpServers", config)
                    if not isinstance(servers, dict) or len(servers) != 1:
                        raise ValueError("The MCP config must contain exactly one server entry.")
                    entry = next(iter(servers.values()))
                    command = str(entry["command"])
                    args = [str(item) for item in entry.get("args", [])]
                    env = os.environ.copy()
                    env.update({str(key): str(value) for key, value in entry.get("env", {}).items()})
                    cwd = entry.get("cwd") or str(path.parent)
                else:
                    command = sys.executable if path.suffix.lower() == ".py" else str(path)
                    args = [str(path)] if path.suffix.lower() == ".py" else []
                    env = None
                    cwd = str(path.parent)
                parameters = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session

            async def main() -> None:
                ref_mode = sys.argv[1]
                ref_value = sys.argv[2]
                async with open_session(ref_mode, ref_value) as session:
                    response = await session.list_tools()
                    tools = response.tools
                serialized = []
                for tool in tools:
                    if hasattr(tool, "model_dump"):
                        item = tool.model_dump()
                    elif hasattr(tool, "dict"):
                        item = tool.dict()
                    else:
                        item = {"repr": repr(tool)}
                    if "name" not in item and hasattr(tool, "name"):
                        item["name"] = getattr(tool, "name")
                    serialized.append(item)
                print(BEGIN)
                print(json.dumps({"count": len(serialized), "tools": serialized}, ensure_ascii=False))
                print(END)

            asyncio.run(main())
            """
        ).strip()
        completed = self._run_python(
            workspace,
            python_path,
            ["-c", script, reference_mode, reference_value],
            env=env,
            timeout_seconds=timeout_seconds,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        begin = stdout.find("QUBITZ_JSON_BEGIN")
        end = stdout.find("QUBITZ_JSON_END")
        if completed.returncode == 0 and begin != -1 and end != -1 and end > begin:
            json_text = stdout[begin + len("QUBITZ_JSON_BEGIN") : end].strip()
            payload = json.loads(json_text)
            return {
                "reference": reference_value,
                "count": int(payload.get("count") or 0),
                "tools": payload.get("tools") or [],
                "returncode": completed.returncode,
                "stdout": _shorten(stdout, 12000),
                "stderr": _shorten(stderr, 12000),
            }
        raise RuntimeError(_shorten(stderr or stdout or "Failed to probe MCP tools.", 4000))

    def _invoke_tool(
        self,
        workspace: Path,
        *,
        server_reference: str,
        python_path: Path,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        reference = self._resolve_reference(workspace, server_reference)
        reference_mode = "path" if isinstance(reference, Path) else "text"
        reference_value = str(reference)
        script = textwrap.dedent(
            """
            import asyncio
            import json
            import os
            import sys
            from contextlib import asynccontextmanager
            from pathlib import Path
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from mcp.client.streamable_http import streamable_http_client

            BEGIN = "QUBITZ_JSON_BEGIN"
            END = "QUBITZ_JSON_END"

            @asynccontextmanager
            async def open_session(ref_mode: str, ref_value: str):
                if ref_mode == "text":
                    async with streamable_http_client(ref_value) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                    return
                path = Path(ref_value)
                if path.suffix.lower() == ".json":
                    config = json.loads(path.read_text(encoding="utf-8"))
                    servers = config.get("mcpServers", config)
                    if not isinstance(servers, dict) or len(servers) != 1:
                        raise ValueError("The MCP config must contain exactly one server entry.")
                    entry = next(iter(servers.values()))
                    command = str(entry["command"])
                    args = [str(item) for item in entry.get("args", [])]
                    env = os.environ.copy()
                    env.update({str(key): str(value) for key, value in entry.get("env", {}).items()})
                    cwd = entry.get("cwd") or str(path.parent)
                else:
                    command = sys.executable if path.suffix.lower() == ".py" else str(path)
                    args = [str(path)] if path.suffix.lower() == ".py" else []
                    env = None
                    cwd = str(path.parent)
                parameters = StdioServerParameters(command=command, args=args, env=env, cwd=cwd)
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session

            async def main() -> None:
                ref_mode, ref_value, tool_name, arguments_json = sys.argv[1:5]
                arguments = json.loads(arguments_json)
                async with open_session(ref_mode, ref_value) as session:
                    result = await session.call_tool(tool_name, arguments)
                if hasattr(result, "model_dump"):
                    serialized = result.model_dump(mode="json")
                elif hasattr(result, "dict"):
                    serialized = result.dict()
                else:
                    serialized = {"repr": repr(result)}
                print(BEGIN)
                print(json.dumps({"tool": tool_name, "result": serialized}, ensure_ascii=False))
                print(END)

            asyncio.run(main())
            """
        ).strip()
        completed = self._run_python(
            workspace,
            python_path,
            [
                "-c",
                script,
                reference_mode,
                reference_value,
                tool_name,
                json.dumps(arguments or {}, ensure_ascii=True),
            ],
            env=env,
            timeout_seconds=timeout_seconds,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        begin = stdout.find("QUBITZ_JSON_BEGIN")
        end = stdout.find("QUBITZ_JSON_END")
        if completed.returncode == 0 and begin != -1 and end != -1 and end > begin:
            json_text = stdout[begin + len("QUBITZ_JSON_BEGIN") : end].strip()
            payload = json.loads(json_text)
            return {
                "reference": reference_value,
                "tool": tool_name,
                "result": payload.get("result"),
                "returncode": completed.returncode,
                "stdout": _shorten(stdout, 12000),
                "stderr": _shorten(stderr, 12000),
            }
        raise RuntimeError(_shorten(stderr or stdout or f"Failed to invoke MCP tool {tool_name!r}.", 4000))

    def start_server(
        self,
        server_script: str,
        *,
        cwd: str = ".",
        python_path: str | None = None,
        arguments: list[str] | None = None,
        env: dict[str, str] | None = None,
        name: str = "",
        connection_uri: str = "",
        ready_substring: str = "",
        wait_seconds: int = 15,
    ) -> dict[str, Any]:
        workspace = self._resolve_workspace_path(self.workspace, cwd, allow_missing=False)
        script_path = self._resolve_workspace_path(workspace, server_script, allow_missing=False)
        interpreter = self._resolve_python(workspace, python_path)
        server_id = uuid.uuid4().hex[:12]
        log_path = self.root / server_id / "server.log"
        process, command = self._popen_python(
            workspace,
            interpreter,
            [str(script_path), *[str(item) for item in (arguments or [])]],
            env=env,
            log_path=log_path,
        )
        metadata = {
            "server_id": server_id,
            "name": (name or script_path.stem).strip() or script_path.stem,
            "status": "starting",
            "pid": process.pid,
            "workspace": workspace.as_posix(),
            "runtime_workspace": self.runtime_workspace.as_posix(),
            "server_script": script_path.as_posix(),
            "python_path": interpreter.as_posix(),
            "arguments": [str(item) for item in (arguments or [])],
            "environment": self._normalize_env(env),
            "command": command,
            "connection_uri": connection_uri.strip(),
            "ready_substring": ready_substring.strip(),
            "created_at": _now_stamp(),
            "finished_at": None,
            "returncode": None,
            "log_path": log_path.as_posix(),
        }
        self._write_meta(server_id, metadata)
        deadline = time.time() + max(0, wait_seconds)
        while time.time() < deadline:
            if process.poll() is not None:
                metadata["status"] = "failed"
                metadata["returncode"] = process.returncode
                metadata["finished_at"] = _now_stamp()
                self._write_meta(server_id, metadata)
                log_text = ""
                if log_path.exists():
                    log_text = _shorten(log_path.read_text(encoding="utf-8", errors="ignore"), 4000)
                raise RuntimeError(log_text or f"Server exited early with return code {process.returncode}.")
            if metadata["connection_uri"]:
                try:
                    self._probe_tools(
                        workspace,
                        server_reference=metadata["connection_uri"],
                        python_path=interpreter,
                        env=env,
                        timeout_seconds=10,
                    )
                    metadata["status"] = "ready"
                    self._write_meta(server_id, metadata)
                    return metadata
                except Exception:
                    pass
            elif metadata["ready_substring"]:
                with suppress(Exception):
                    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
                    if metadata["ready_substring"] in log_text:
                        metadata["status"] = "ready"
                        self._write_meta(server_id, metadata)
                        return metadata
            else:
                time.sleep(1.0)
                metadata["status"] = "running"
                self._write_meta(server_id, metadata)
                return metadata
            time.sleep(0.5)
        metadata["status"] = "running"
        self._write_meta(server_id, metadata)
        return metadata

    def list_tools(
        self,
        *,
        server_id: str = "",
        server_reference: str = "",
        cwd: str = ".",
        python_path: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if server_id:
            metadata = self._refresh_status(self._read_meta(server_id))
            workspace = Path(metadata["workspace"])
            interpreter = Path(metadata["python_path"])
            reference = server_reference.strip() or metadata.get("connection_uri") or metadata.get("server_script") or ""
            env = metadata.get("environment") or {}
        else:
            workspace = self._resolve_workspace_path(self.workspace, cwd, allow_missing=False)
            interpreter = self._resolve_python(workspace, python_path)
            reference = server_reference.strip()
            env = None
        if not reference:
            raise ValueError("Provide server_id or server_reference.")
        payload = self._probe_tools(
            workspace,
            server_reference=reference,
            python_path=interpreter,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if server_id:
            payload["server_id"] = server_id
        return payload

    def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        server_id: str = "",
        server_reference: str = "",
        cwd: str = ".",
        python_path: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if not tool_name.strip():
            raise ValueError("tool_name must not be empty.")
        if server_id:
            metadata = self._refresh_status(self._read_meta(server_id))
            workspace = Path(metadata["workspace"])
            interpreter = Path(metadata["python_path"])
            reference = server_reference.strip() or metadata.get("connection_uri") or metadata.get("server_script") or ""
            env = metadata.get("environment") or {}
        else:
            workspace = self._resolve_workspace_path(self.workspace, cwd, allow_missing=False)
            interpreter = self._resolve_python(workspace, python_path)
            reference = server_reference.strip()
            env = None
        if not reference:
            raise ValueError("Provide server_id or server_reference.")
        payload = self._invoke_tool(
            workspace,
            server_reference=reference,
            python_path=interpreter,
            tool_name=tool_name.strip(),
            arguments=arguments,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if server_id:
            payload["server_id"] = server_id
        return payload

    def stop_server(self, server_id: str) -> dict[str, Any]:
        metadata = self._read_meta(server_id)
        pid = int(metadata.get("pid") or 0)
        stopped = False
        if pid > 0:
            if (
                callable(getattr(self.base, "in_wsl", None))
                and self.base.in_wsl()
                and shutil.which("taskkill.exe") is not None
            ):
                with suppress(Exception):
                    completed = subprocess.run(
                        ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        text=True,
                        timeout=30,
                        check=False,
                    )
                    stopped = completed.returncode == 0
            if not stopped:
                with suppress(Exception):
                    os.kill(pid, 15)
                    stopped = True
        metadata["status"] = "stopped"
        metadata["finished_at"] = _now_stamp()
        self._write_meta(server_id, metadata)
        return {"server_id": server_id, "stopped": stopped}

    def read_log(self, server_id: str, max_chars: int = 12000) -> dict[str, Any]:
        metadata = self._refresh_status(self._read_meta(server_id))
        log_path = Path(metadata["log_path"])
        log_text = ""
        if log_path.exists():
            log_text = _shorten(log_path.read_text(encoding="utf-8", errors="ignore"), max_chars)
        return {"meta": metadata, "log": log_text}


class LocalBackgroundJobManager:
    def __init__(self, runtime_workspace: Path, launch_script: Path) -> None:
        self.runtime_workspace = runtime_workspace.resolve()
        self.launch_script = launch_script.resolve()
        self.root = self.runtime_workspace / ".cache" / "background_jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def _meta_path(self, job_id: str) -> Path:
        return self.root / job_id / "meta.json"

    def _write_meta(self, job_id: str, payload: dict[str, Any]) -> None:
        path = self._meta_path(job_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    def _read_meta(self, job_id: str) -> dict[str, Any]:
        path = self._meta_path(job_id)
        if not path.exists():
            raise FileNotFoundError(job_id)
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))

    def _build_command(self, config: Any, workspace: Path, prompt: str) -> list[str]:
        command = [sys.executable, str(self.launch_script), "--cli", "--workspace", str(workspace), "--prompt", prompt]
        with suppress(Exception):
            command.extend(["--max-steps", str(getattr(config, "max_steps"))])
        if hasattr(config, "model_name"):
            command.extend(["--model", str(getattr(config, "model_name"))])
        if hasattr(config, "embed_model_name"):
            command.extend(["--embed-model", str(getattr(config, "embed_model_name"))])
        if hasattr(config, "ollama_num_ctx"):
            command.extend(["--num-ctx", str(getattr(config, "ollama_num_ctx"))])
        elif hasattr(config, "num_ctx"):
            command.extend(["--num-ctx", str(getattr(config, "num_ctx"))])
        if hasattr(config, "ollama_num_predict"):
            command.extend(["--num-predict", str(getattr(config, "ollama_num_predict"))])
        elif hasattr(config, "num_predict"):
            command.extend(["--num-predict", str(getattr(config, "num_predict"))])
        if hasattr(config, "model_path") and getattr(config, "model_path"):
            command.extend(["--model-path", str(getattr(config, "model_path"))])
        if hasattr(config, "server_url") and getattr(config, "server_url"):
            command.extend(["--server-url", str(getattr(config, "server_url"))])
        if hasattr(config, "llama_server_path") and getattr(config, "llama_server_path"):
            command.extend(["--llama-server", str(getattr(config, "llama_server_path"))])
        return command

    def start(
        self,
        prompt: str,
        *,
        config: Any,
        workspace: Path,
        sandbox_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / "job.log"
        command = self._build_command(config, workspace, prompt)
        env = os.environ.copy()
        env.setdefault("QUBITZ_ALLOW_EMBED_ONLINE", "1")
        env["QUBITZ_BACKGROUND_JOB"] = "1"
        with log_path.open("w", encoding="utf-8", errors="ignore") as handle:
            process = subprocess.Popen(
                command,
                cwd=self.runtime_workspace,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                env=env,
            )
        meta = {
            "job_id": job_id,
            "status": "running",
            "prompt": prompt,
            "workspace": workspace.as_posix(),
            "runtime_workspace": self.runtime_workspace.as_posix(),
            "sandbox_id": sandbox_id,
            "pid": process.pid,
            "command": command,
            "launch_script": self.launch_script.as_posix(),
            "created_at": _now_stamp(),
            "finished_at": None,
            "returncode": None,
            "log_path": log_path.as_posix(),
        }
        self._write_meta(job_id, meta)

        def _watch() -> None:
            returncode = process.wait()
            latest = self._read_meta(job_id)
            latest["status"] = "completed" if returncode == 0 else "failed"
            latest["finished_at"] = _now_stamp()
            latest["returncode"] = returncode
            self._write_meta(job_id, latest)

        threading.Thread(target=_watch, daemon=True).start()
        return meta

    def start_command(
        self,
        command: Sequence[str],
        *,
        workspace: Path,
        label: str,
        wait_seconds: float = PROJECT_LAUNCH_READY_WAIT_SECONDS,
    ) -> dict[str, Any]:
        normalized_command = [str(item) for item in command if str(item)]
        if not normalized_command:
            raise ValueError("Project launch command must not be empty.")
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        log_path = job_dir / "job.log"
        env = os.environ.copy()
        env["QUBITZ_PROJECT_LAUNCH"] = "1"
        with log_path.open("w", encoding="utf-8", errors="ignore") as handle:
            process = subprocess.Popen(
                normalized_command,
                cwd=workspace,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                env=env,
                start_new_session=os.name != "nt",
            )
        meta = {
            "job_id": job_id,
            "job_type": "project_launch",
            "status": "starting",
            "label": label,
            "workspace": workspace.as_posix(),
            "runtime_workspace": self.runtime_workspace.as_posix(),
            "pid": process.pid,
            "command": normalized_command,
            "created_at": _now_stamp(),
            "finished_at": None,
            "returncode": None,
            "log_path": log_path.as_posix(),
            "detected_urls": [],
        }
        self._write_meta(job_id, meta)
        deadline = time.time() + max(0.0, float(wait_seconds))
        while time.time() < deadline and process.poll() is None:
            time.sleep(0.1)
        returncode = process.poll()
        if returncode is None:
            meta["status"] = "running"
        else:
            meta["status"] = "completed" if returncode == 0 else "failed"
            meta["returncode"] = returncode
            meta["finished_at"] = _now_stamp()
        log_text = ""
        with suppress(Exception):
            log_text = log_path.read_text(encoding="utf-8", errors="ignore")
        if PROJECT_LAUNCH_FATAL_LOG_PATTERN.search(log_text):
            returncode = process.poll()
            meta["status"] = "failed"
            meta["returncode"] = returncode
            if returncode is not None:
                meta["finished_at"] = _now_stamp()
            meta["startup_error_detected"] = True
        meta["detected_urls"] = list(dict.fromkeys(PROJECT_LAUNCH_LOCAL_URL_PATTERN.findall(log_text)))[:8]
        self._write_meta(job_id, meta)

        if returncode is None:
            def _watch() -> None:
                completed_code = process.wait()
                latest = self._read_meta(job_id)
                if latest.get("status") not in {"cancelling", "cancelled"}:
                    latest["status"] = "completed" if completed_code == 0 else "failed"
                    latest["finished_at"] = _now_stamp()
                    latest["returncode"] = completed_code
                    self._write_meta(job_id, latest)

            threading.Thread(target=_watch, daemon=True).start()
        result = dict(meta)
        result["log_excerpt"] = _shorten(log_text.strip(), 2000)
        return result

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for meta_path in sorted(self.root.glob("*/meta.json")):
            with suppress(Exception):
                jobs.append(json.loads(meta_path.read_text(encoding="utf-8", errors="ignore")))
        jobs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        return jobs

    def read_job(self, job_id: str, max_chars: int = 12000) -> dict[str, Any]:
        meta = self._read_meta(job_id)
        log_text = ""
        log_path = Path(meta["log_path"])
        if log_path.exists():
            log_text = _shorten(log_path.read_text(encoding="utf-8", errors="ignore"), max_chars)
        return {"meta": meta, "log": log_text}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        meta = self._read_meta(job_id)
        pid = meta.get("pid")
        if not pid:
            return {"job_id": job_id, "cancelled": False}
        previous_status = str(meta.get("status") or "running")
        meta["status"] = "cancelling"
        self._write_meta(job_id, meta)
        cancelled = False
        if meta.get("job_type") == "project_launch" and os.name != "nt":
            with suppress(Exception):
                os.killpg(int(pid), 15)
                cancelled = True
        elif meta.get("job_type") == "project_launch" and shutil.which("taskkill.exe") is not None:
            with suppress(Exception):
                completed = subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                cancelled = completed.returncode == 0
        else:
            with suppress(Exception):
                os.kill(int(pid), 15)
                cancelled = True
        if not cancelled:
            meta["status"] = previous_status
            self._write_meta(job_id, meta)
            return {"job_id": job_id, "cancelled": False}
        meta["status"] = "cancelled"
        meta["finished_at"] = _now_stamp()
        self._write_meta(job_id, meta)
        return {"job_id": job_id, "cancelled": True}


def _is_windows_backed_workspace(workspace: Path) -> bool:
    resolved = workspace.resolve().as_posix()
    if re.match(r"^[A-Za-z]:[/\\]", resolved):
        return True
    return resolved.startswith("/mnt/") and len(resolved) >= 7 and resolved[5].isalpha() and resolved[6] == "/"


def _path_exists_safely(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _preferred_project_linux_python(workspace: Path) -> Path | None:
    for candidate in (
        workspace / ".venv" / "bin" / "python",
        workspace / ".venv_linux" / "bin" / "python",
        workspace / ".venv_wsl" / "bin" / "python",
        workspace / "venv" / "bin" / "python",
        workspace / "env" / "bin" / "python",
    ):
        if _path_exists_safely(candidate):
            return candidate
    try:
        for child in sorted(workspace.iterdir()):
            candidate = child / "bin" / "python"
            if _path_exists_safely(child / "pyvenv.cfg") and _path_exists_safely(candidate):
                return candidate
    except OSError:
        return None
    return None


def _preferred_project_windows_python(workspace: Path) -> Path | None:
    for candidate in (
        workspace / ".venv312" / "Scripts" / "python.exe",
        workspace / ".venv313" / "Scripts" / "python.exe",
        workspace / ".venv" / "Scripts" / "python.exe",
        workspace / ".venv_win" / "Scripts" / "python.exe",
        workspace / "venv" / "Scripts" / "python.exe",
        workspace / "env" / "Scripts" / "python.exe",
    ):
        if _path_exists_safely(candidate):
            return candidate
    try:
        for child in sorted(workspace.iterdir()):
            candidate = child / "Scripts" / "python.exe"
            if _path_exists_safely(child / "pyvenv.cfg") and _path_exists_safely(candidate):
                return candidate
    except OSError:
        return None
    return None


def _preferred_project_python(workspace: Path) -> Path | None:
    linux_candidate = _preferred_project_linux_python(workspace)
    windows_candidate = _preferred_project_windows_python(workspace)
    candidates = (
        [windows_candidate, linux_candidate]
        if _is_windows_backed_workspace(workspace)
        else [linux_candidate, windows_candidate]
    )
    for candidate in candidates:
        if candidate is not None:
            return candidate
    return None


def _enable_local_only_environment() -> None:
    os.environ.setdefault("QUBITZ_ALLOW_EMBED_ONLINE", "1")


def _powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _workspace_windows_path(base: Any, workspace: Path) -> str:
    translator = getattr(base, "wsl_path_to_windows", None)
    if callable(translator):
        translated = translator(workspace)
        if translated:
            return str(translated)
    fallback = getattr(base, "wsl_path_to_windows_path", None)
    if callable(fallback):
        return str(fallback(workspace))
    return str(workspace)


def _extract_powershell_script(command: str) -> str:
    stripped = command.strip()
    lower = stripped.lower()
    for prefix in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):
        token = prefix + " "
        if lower.startswith(token):
            script = stripped[len(token) :].lstrip()
            while True:
                lowered_script = script.lower()
                if lowered_script.startswith("-noprofile "):
                    script = script[len("-noprofile ") :].lstrip()
                    continue
                if lowered_script.startswith("-command "):
                    script = script[len("-command ") :].lstrip()
                    continue
                if lowered_script.startswith("-c "):
                    script = script[len("-c ") :].lstrip()
                    continue
                break
            if len(script) >= 2 and script[0] == script[-1] and script[0] in {"'", '"'}:
                script = script[1:-1]
            return script
    return stripped


def _shell_quote_path(path: Path | str) -> str:
    text = str(path)
    if os.name == "nt":
        return f'"{text}"'
    return shlex.quote(text)


def _canonicalize_workspace_command(workspace: Path, command: str) -> str:
    normalized = command.strip()
    shell_match = re.match(
        r"^\s*(?P<shell>(?:bash|sh)(?:\.exe)?)\s+(?P<flag>-lc|-c)\s+(?P<script>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if shell_match:
        script = shell_match.group("script").strip()
        if len(script) >= 2 and script[0] == script[-1] and script[0] in {"'", '"'}:
            script = script[1:-1]
        rewritten = _canonicalize_workspace_command(workspace, script)
        return f"{shell_match.group('shell')} {shell_match.group('flag')} {shlex.quote(rewritten)}"
    python_match = re.match(r"^\s*(?P<launcher>python(?:\.exe)?|python3|py)\s+(?P<rest>.+)$", normalized, flags=re.IGNORECASE)
    if not python_match:
        return command
    interpreter = _preferred_project_python(workspace)
    if interpreter is None:
        return command
    rest = python_match.group("rest").strip()
    return f"{_shell_quote_path(interpreter)} {rest}".rstrip()


def _canonicalize_workspace_script(base: Any, workspace: Path, script: str) -> str:
    normalized = script.strip()
    activation_match = re.match(
        r"^\s*(?P<activate>\.[\\/][^;]*?[\\/](?:Scripts[\\/ ]Activate\.ps1|bin[\\/]activate))\s*;\s*(?P<rest>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if activation_match:
        activate_path = activation_match.group("activate")
        if "\\" in activate_path and not re.match(r"^[A-Za-z]:[\\/]", activate_path):
            activate_path = activate_path.replace("\\", "/")
        rest = activation_match.group("rest").strip()
        python_launcher_match = re.match(
            r"^(?:python(?:\.exe)?|py|(?:\.[\\/][^\s]*?(?:Scripts[\\/]python\.exe|bin[\\/]python)))\s+(?P<rest>.+)$",
            rest,
            flags=re.IGNORECASE,
        )
        if python_launcher_match:
            rest = python_launcher_match.group("rest").strip()
        activate_candidate = (workspace / activate_path).resolve()
        if activate_candidate.name.lower() == "activate.ps1":
            interpreter = activate_candidate.parent / "python.exe"
            if interpreter.exists():
                translated = _workspace_windows_path(base, interpreter)
                return f"& {_powershell_single_quote(translated)} {rest}"
        if activate_candidate.name.lower() == "activate":
            interpreter = activate_candidate.parent / "python"
            if interpreter.exists():
                return f"& {_powershell_single_quote(str(interpreter))} {rest}"
    python_launcher_match = re.match(
        r"^\s*(?P<launcher>python(?:\.exe)?|python3|py)\s+(?P<rest>.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if python_launcher_match:
        interpreter = _preferred_project_python(workspace)
        if interpreter is not None:
            rest = python_launcher_match.group("rest").strip()
            if interpreter.suffix.lower() == ".exe":
                translated = _workspace_windows_path(base, interpreter)
                return f"& {_powershell_single_quote(translated)} {rest}"
            return f"& {_powershell_single_quote(str(interpreter))} {rest}"
    python_match = re.match(
        r"^\s*(?P<python>\.[\\/][^;]*?[\\/]Scripts[\\/]python\.exe)\s*(?P<rest>.*)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if python_match:
        python_text = python_match.group("python")
        if "\\" in python_text and not re.match(r"^[A-Za-z]:[\\/]", python_text):
            python_text = python_text.replace("\\", "/")
        interpreter = (workspace / python_text).resolve()
        if interpreter.exists():
            translated = _workspace_windows_path(base, interpreter)
            rest = python_match.group("rest").strip()
            return f"& {_powershell_single_quote(translated)} {rest}".rstrip()
    return script


def _should_use_windows_shell(base: Any, workspace: Path, command: str) -> bool:
    if not callable(getattr(base, "in_wsl", None)) or not base.in_wsl():
        return False
    if shutil.which("powershell.exe") is None:
        return False
    stripped = command.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    first_token = stripped.split(maxsplit=1)[0].strip("'\"")
    first_token_posix = first_token.replace("\\", "/")
    if re.match(r"^\./[^;&|]*?/bin/python(?:3(?:\.\d+)?)?$", first_token_posix, flags=re.IGNORECASE):
        candidate = (workspace / first_token_posix).resolve()
        if candidate.is_file() and candidate.suffix.lower() != ".exe":
            return False
    if lower.startswith(("powershell ", "powershell.exe ", "pwsh ", "pwsh.exe ")):
        return True
    if any(token in lower for token in (".ps1", ".bat", ".cmd")):
        return True
    if _is_windows_backed_workspace(workspace) and re.search(r"(^|[\s;&|])\.[\\/]", stripped):
        return True
    pattern = getattr(base, "WINDOWS_DRIVE_PATH_PATTERN", None)
    if pattern is not None and pattern.match(stripped):
        return True
    if "\\" in first_token and first_token.lower().endswith((".exe", ".cmd", ".bat")):
        return True
    return False


class _CommandResult(dict[str, Any]):
    __slots__ = ("stdout_full", "stderr_full")

    def __init__(self, payload: dict[str, Any], *, stdout_full: str, stderr_full: str) -> None:
        super().__init__(payload)
        self.stdout_full = stdout_full
        self.stderr_full = stderr_full


def _terminate_windows_process_token(token: str) -> None:
    powershell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
    if powershell is None or not token:
        return
    escaped = token.replace("'", "''")
    script = (
        f"$needle = '{escaped}'; "
        "$matches = Get-CimInstance Win32_Process | Where-Object { "
        "$_.ProcessId -ne $PID -and $_.CommandLine -and "
        "$_.CommandLine.IndexOf($needle, [StringComparison]::Ordinal) -ge 0 }; "
        "$matches | ForEach-Object { & taskkill.exe /PID $_.ProcessId /T /F 2>$null | Out-Null }"
    )
    command = [powershell, "-NoProfile", "-Command", script]
    if os.name != "nt" and Path("/init").is_file() and powershell.lower().endswith(".exe"):
        command.insert(0, "/init")
    with suppress(Exception):
        subprocess.run(command, capture_output=True, timeout=15, check=False)


def _terminate_owned_process(process: subprocess.Popen[bytes], windows_token: str = "") -> None:
    if windows_token:
        _terminate_windows_process_token(windows_token)
    if process.poll() is not None:
        return
    with suppress(Exception):
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            os.killpg(os.getpgid(process.pid), 15)
    with suppress(Exception):
        process.wait(timeout=3)
    if process.poll() is None:
        with suppress(Exception):
            process.kill()


def _run_owned_process(
    command: Sequence[str] | str,
    *,
    cwd: Path,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
    shell: bool = False,
    windows_token: str = "",
) -> tuple[int, bytes, bytes]:
    popen_kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": shell,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    deadline = time.monotonic() + max(1, timeout_seconds)
    while True:
        try:
            stdout, stderr = process.communicate(timeout=0.2)
            return int(process.returncode or 0), stdout or b"", stderr or b""
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_owned_process(process, windows_token)
                with suppress(Exception):
                    process.communicate(timeout=2)
                raise InterruptedError("Command cancelled by the active Qubitz request.")
            if time.monotonic() >= deadline:
                _terminate_owned_process(process, windows_token)
                raise subprocess.TimeoutExpired(command, timeout_seconds)


def _run_shell_command(
    base: Any,
    workspace: Path,
    command: str,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    if _should_use_windows_shell(base, workspace, command):
        result = _run_powershell_command(base, workspace, command, timeout_seconds, cancel_event=cancel_event)
        result["returncode"] = int(result.get("return_code", 0))
        return result
    else:
        command = _canonicalize_workspace_command(workspace, command)
        return_code, stdout_data, stderr_data = _run_owned_process(
            command,
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            cancel_event=cancel_event,
            shell=True,
        )
    stdout_full = _decode_subprocess_output(stdout_data)
    stderr_full = _decode_subprocess_output(stderr_data)
    result = _CommandResult(
        {
            "command": command,
            "returncode": return_code,
            "stdout": _shorten(stdout_full, 12000),
            "stderr": _shorten(stderr_full, 12000),
        },
        stdout_full=stdout_full,
        stderr_full=stderr_full,
    )
    return result


def _run_powershell_command(
    base: Any,
    workspace: Path,
    command: str,
    timeout_seconds: int,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
    if powershell is None:
        raise RuntimeError("PowerShell was not found on this system.")
    use_windows_paths = powershell.lower().endswith(".exe") and callable(getattr(base, "in_wsl", None)) and base.in_wsl()
    launch_command = [powershell]
    if use_windows_paths and Path("/init").is_file():
        launch_command.insert(0, "/init")
    workspace_windows = _workspace_windows_path(base, workspace) if use_windows_paths else str(workspace)
    script_body = _canonicalize_workspace_script(base, workspace, _extract_powershell_script(command))
    windows_token = f"QUBITZ_TASK_{uuid.uuid4().hex}"
    script_lines = [
        f"$null = '{windows_token}'",
        "$ProgressPreference = 'SilentlyContinue'",
        f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
        script_body,
    ]
    return_code, stdout_data, stderr_data = _run_owned_process(
        [*launch_command, "-NoProfile", "-Command", "; ".join(line for line in script_lines if line)],
        cwd=workspace,
        timeout_seconds=timeout_seconds,
        cancel_event=cancel_event,
        windows_token=windows_token if use_windows_paths or os.name == "nt" else "",
    )
    stdout_full = _decode_subprocess_output(stdout_data)
    stderr_full = _decode_subprocess_output(stderr_data)
    result = _CommandResult(
        {
            "command": command,
            "return_code": return_code,
            "stdout": _shorten(stdout_full, 12000),
            "stderr": _shorten(stderr_full, 12000),
        },
        stdout_full=stdout_full,
        stderr_full=stderr_full,
    )
    return result


def _build_local_mcp_server(base: Any, workspace: Path, runtime_workspace: Path, launch_script: Path) -> Any:
    workspace = workspace.resolve()
    runtime_workspace = runtime_workspace.resolve()
    local_config = LocalOnlyConfig.load(runtime_workspace, workspace)
    plugins_enabled = local_config.plugins_enabled and _local_plugins_available(runtime_workspace, workspace)
    sandbox_tools_enabled = _sandbox_features_enabled(local_config)
    background_jobs_enabled = local_config.background_jobs_enabled
    plugin_registry: Any = (
        LocalPluginRegistry(runtime_workspace, workspace) if plugins_enabled else _DisabledLocalPluginRegistry()
    )
    skills = base.SkillRegistry(runtime_workspace) if _local_skills_available(runtime_workspace) else None
    sandboxes: Any = (
        LocalSandboxManager(workspace, runtime_workspace, getattr(base, "EXCLUDED_DIRS", set()), base=base)
        if sandbox_tools_enabled
        else _DisabledLocalSandboxManager()
    )
    jobs: Any = (
        LocalBackgroundJobManager(runtime_workspace, launch_script)
        if background_jobs_enabled
        else _DisabledLocalBackgroundJobManager()
    )
    mcp_servers = LocalMCPServerManager(workspace, runtime_workspace, base=base)
    server = base.FastMCP(
        "Qubitz Local-Only Tools",
        json_response=True,
        instructions="Local-only filesystem, code intelligence, sandbox, plugin, and background-job tools.",
    )
    memory_dir = runtime_workspace / ".memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    current_memory_path = memory_dir / getattr(base, "CURRENT_MEMORY_NAME", "MEMORY.md")
    codeintel = LocalCodeIntel(
        workspace,
        runtime_workspace,
        excluded_dirs=getattr(base, "EXCLUDED_DIRS", set()),
        text_suffixes=getattr(base, "TEXT_SUFFIXES", {".py", ".md", ".txt"}),
        max_files=local_config.codeintel_max_files,
    )

    def _resolve(candidate: str, *, allow_missing: bool = True, allow_external: bool = False) -> Path:
        return base.resolve_workspace_path(
            workspace,
            candidate,
            allow_missing=allow_missing,
            allow_external=allow_external,
        )

    def _read_text(candidate: Path) -> str:
        return candidate.read_text(encoding="utf-8", errors="ignore")

    @server.resource("workspace://summary")
    def workspace_summary() -> str:
        return json.dumps(
            {
                "workspace": workspace.as_posix(),
                "runtime_workspace": runtime_workspace.as_posix(),
                "memory_file": base.relative_path(current_memory_path, runtime_workspace),
                "skills_root": base.relative_path(_local_skills_root(runtime_workspace), runtime_workspace),
                "skill_count": skills.count() if skills is not None else 0,
                "skill_warnings": skills.warnings if skills is not None else [],
                "local_only_config": local_config.__dict__,
                "plugin_count": len(plugin_registry.plugins),
            },
            ensure_ascii=True,
            indent=2,
        )

    @server.resource("memory://current")
    def memory_resource() -> str:
        if not current_memory_path.exists():
            return ""
        return current_memory_path.read_text(encoding="utf-8", errors="ignore")

    if skills is not None:
        @server.resource("skills://index")
        def skills_index() -> str:
            return json.dumps(
                {
                    "skills_root": base.relative_path(skills.skills_root, runtime_workspace),
                    "count": skills.count(),
                    "warnings": skills.warnings,
                    "skills": skills.list_summaries(),
                },
                ensure_ascii=True,
                indent=2,
            )

    @server.resource("plugins://index")
    def plugins_index() -> str:
        return json.dumps({"plugins": plugin_registry.list_plugins()}, ensure_ascii=True, indent=2)

    @server.tool(description="List files or directories inside the active workspace.")
    def list_files(path: str = ".", recursive: bool = False, max_entries: int = 200) -> dict[str, Any]:
        root = _resolve(path, allow_missing=False, allow_external=True)
        iterator = root.rglob("*") if recursive else root.iterdir()
        entries: list[dict[str, Any]] = []
        for candidate in sorted(iterator):
            if any(part in getattr(base, "EXCLUDED_DIRS", set()) for part in candidate.parts if candidate != root):
                continue
            entries.append(
                {
                    "path": base.relative_path(candidate, workspace),
                    "is_dir": candidate.is_dir(),
                    "size": candidate.stat().st_size if candidate.is_file() else None,
                }
            )
            if len(entries) >= max_entries:
                break
        return {"root": base.relative_path(root, workspace), "entries": entries}

    @server.tool(description="Read a text file from the active workspace or an explicit absolute path.")
    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False, allow_external=True)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        lines = _read_text(target).splitlines()
        start_index = max(start_line - 1, 0)
        end_index = min(end_line, len(lines))
        excerpt = "\n".join(lines[start_index:end_index])
        return {
            "path": base.relative_path(target, workspace),
            "start_line": start_index + 1,
            "end_line": end_index,
            "content": excerpt,
        }

    @server.tool(description="Write or overwrite a file inside the active workspace.")
    def write_file(path: str, content: str, make_parents: bool = True) -> dict[str, Any]:
        target = _resolve(path, allow_external=True)
        if make_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": base.relative_path(target, workspace), "bytes_written": target.stat().st_size}

    @server.tool(description="Replace exact text inside a file in the active workspace.")
    def replace_text(path: str, old_text: str, new_text: str, count: int = 0) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False, allow_external=True)
        original = _read_text(target)
        replacements = original.count(old_text) if count == 0 else min(original.count(old_text), count)
        updated = original.replace(old_text, new_text, count) if count > 0 else original.replace(old_text, new_text)
        if updated != original:
            target.write_text(updated, encoding="utf-8")
        return {"path": base.relative_path(target, workspace), "replacements": replacements}

    @server.tool(description="Delete a file or directory inside the active workspace.")
    def delete_path(path: str, recursive: bool = False) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False, allow_external=True)
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
        return {"deleted": base.relative_path(target, workspace), "recursive": recursive}

    @server.tool(description="Create a directory inside the active workspace.")
    def make_directory(path: str) -> dict[str, Any]:
        target = _resolve(path, allow_external=True)
        target.mkdir(parents=True, exist_ok=True)
        return {"path": base.relative_path(target, workspace), "created": True}

    @server.tool(description="Move or rename a path inside the active workspace.")
    def move_path(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        source_path = _resolve(source, allow_missing=False, allow_external=True)
        destination_path = _resolve(destination, allow_external=True)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        if destination_path.exists():
            if not overwrite:
                raise ValueError(f"Destination already exists: {destination}")
            if destination_path.is_dir():
                shutil.rmtree(destination_path)
            else:
                destination_path.unlink()
        shutil.move(str(source_path), str(destination_path))
        return {
            "source": base.relative_path(source_path, workspace),
            "destination": base.relative_path(destination_path, workspace),
        }

    @server.tool(description="Search text content inside active workspace files.")
    def search_text(
        query: str,
        path: str = ".",
        file_glob: str = "*",
        max_results: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        root = _resolve(path, allow_missing=False, allow_external=True)
        hits: list[dict[str, Any]] = []
        needle = query if case_sensitive else query.lower()
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            if any(part in getattr(base, "EXCLUDED_DIRS", set()) for part in candidate.parts):
                continue
            if not candidate.match(file_glob) and not Path(base.relative_path(candidate, workspace)).match(file_glob):
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    hits.append(
                        {
                            "path": base.relative_path(candidate, workspace),
                            "line": line_number,
                            "content": line.strip(),
                        }
                    )
                    if len(hits) >= max_results:
                        return {"query": query, "matches": hits}
        return {"query": query, "matches": hits}

    @server.tool(
        description=(
            "Run a one-shot local shell command inside the active workspace. "
            "Prefer start_project_mcp_server or list_project_mcp_tools for MCP server lifecycle tasks."
        )
    )
    def run_command(command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        return _run_shell_command(base, workspace, command, timeout_seconds)

    @server.tool(
        description=(
            "Run a bounded PowerShell command inside the active workspace. "
            "Prefer this for Windows-backed workspaces, Activate.ps1, and other PowerShell-specific tasks."
        )
    )
    def run_powershell_command(command: str, cwd: str = ".", timeout_seconds: int = 120) -> dict[str, Any]:
        target_cwd = _resolve(cwd, allow_missing=False, allow_external=True)
        result = _run_powershell_command(base, target_cwd, command, timeout_seconds)
        result["cwd"] = base.relative_path(target_cwd, workspace)
        return result

    @server.tool(description="Install Python packages into the preferred active-workspace project-local virtual environment. Supports direct URLs, Git URLs, local paths, wheel files, and optional extra pip/uv arguments such as --index-url or --find-links.")
    def install_python_package(
        packages: list[str] | None = None,
        requirements_file: str | None = None,
        upgrade: bool = False,
        pip_args: list[str] | None = None,
    ) -> dict[str, Any]:
        python_executable = _preferred_project_python(workspace)
        if python_executable is None:
            raise RuntimeError("No project-local Python environment was found in the active workspace.")
        if not packages and not requirements_file:
            raise ValueError("Provide packages and/or requirements_file.")
        if shutil.which("uv") and python_executable.suffix.lower() != ".exe":
            command = ["uv", "pip", "install", "--python", str(python_executable)]
        else:
            command = [str(python_executable), "-m", "pip", "install"]
        if upgrade:
            command.append("--upgrade")
        if requirements_file:
            requirements_path = _resolve(requirements_file, allow_missing=False, allow_external=True)
            command.extend(["-r", str(requirements_path)])
        if pip_args:
            command.extend(str(item) for item in pip_args if str(item).strip())
        if packages:
            command.extend(packages)
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            timeout=1800,
            check=False,
        )
        return {
            "command": command,
            "returncode": completed.returncode,
            "stdout": _shorten(_decode_subprocess_output(completed.stdout), 12000),
            "stderr": _shorten(_decode_subprocess_output(completed.stderr), 12000),
        }

    if skills is not None:
        @server.tool(description="List runtime-root local skills discovered under .skills.")
        def list_skills() -> dict[str, Any]:
            return {
                "skills_root": base.relative_path(skills.skills_root, runtime_workspace),
                "count": skills.count(),
                "warnings": skills.warnings,
                "skills": skills.list_summaries(),
            }

        @server.tool(description="Read a runtime-root local skill's metadata and body.")
        def read_skill(skill_name: str) -> dict[str, Any]:
            skill = skills.get(skill_name)
            summary = skill.to_summary(runtime_workspace)
            summary["body"] = skill.body
            return summary

        @server.tool(description="Read a file or directory inside a runtime-root local skill root.")
        def read_skill_resource(skill_name: str, resource_path: str) -> dict[str, Any]:
            return skills.read_skill_resource(skill_name, resource_path)

    @server.tool(description="List local-only plugin manifests loaded from runtime and active workspace .qubitz/plugins.")
    def list_local_plugins() -> dict[str, Any]:
        plugin_registry.reload()
        return {"plugins": plugin_registry.list_plugins()}

    @server.tool(description="Read one local-only plugin manifest by name.")
    def read_local_plugin(name: str) -> dict[str, Any]:
        plugin_registry.reload()
        return plugin_registry.read_plugin(name)

    @server.tool(description="Return the merged local-only configuration for this runtime.")
    def read_local_only_config() -> dict[str, Any]:
        return local_config.__dict__.copy()

    @server.tool(description="List workspace symbols using the local code-intelligence index.")
    def workspace_symbols(query: str = "", max_results: int = 50) -> dict[str, Any]:
        return codeintel.workspace_symbols(query=query, max_results=max_results)

    @server.tool(description="List document symbols for a file in the active workspace.")
    def document_symbols(path: str) -> dict[str, Any]:
        return codeintel.document_symbols(path)

    @server.tool(description="Find symbol definitions in the active workspace.")
    def find_symbol_definitions(name: str, max_results: int = 50) -> dict[str, Any]:
        return codeintel.find_symbol_definitions(name=name, max_results=max_results)

    @server.tool(description="Find symbol references in the active workspace.")
    def find_symbol_references(name: str, max_results: int = 100) -> dict[str, Any]:
        return codeintel.find_symbol_references(name=name, max_results=max_results)

    @server.tool(description="Find caller sites for a function or method name in the active workspace.")
    def find_symbol_callers(name: str, max_results: int = 50) -> dict[str, Any]:
        return codeintel.find_callers(name=name, max_results=max_results)

    @server.tool(description="Find callees for a function or method name in the active workspace.")
    def find_symbol_callees(name: str, max_results: int = 50) -> dict[str, Any]:
        return codeintel.find_callees(name=name, max_results=max_results)

    @server.tool(description="Return type-hint information gathered from the active workspace.")
    def find_symbol_type_info(name: str, path: str | None = None) -> dict[str, Any]:
        return codeintel.symbol_type_info(name=name, path=path)

    @server.tool(description="Run local Pyright or BasedPyright diagnostics for a file if the tool is installed.")
    def codeintel_diagnostics(path: str) -> dict[str, Any]:
        return codeintel.pyright_diagnostics(path)

    @server.tool(description="Create a local sandbox for non-trivial changes or background jobs.")
    def create_sandbox(label: str = "", mode: str = "auto") -> dict[str, Any]:
        return sandboxes.create(label=label, mode=mode)

    @server.tool(
        description=(
            "Start a local MCP server as a managed background process using a direct project Python interpreter path. "
            "Prefer this over run_command when the task is to run a server."
        )
    )
    def start_project_mcp_server(
        server_script: str,
        cwd: str = ".",
        python_path: str | None = None,
        arguments: list[str] | None = None,
        name: str = "",
        connection_uri: str = "",
        ready_substring: str = "",
        wait_seconds: int = 15,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return mcp_servers.start_server(
            server_script,
            cwd=cwd,
            python_path=python_path,
            arguments=arguments,
            env=env,
            name=name,
            connection_uri=connection_uri,
            ready_substring=ready_substring,
            wait_seconds=wait_seconds,
        )

    @server.tool(
        description=(
            "List tools from a local MCP server using a direct project interpreter probe. "
            "Accepts either a managed server_id or an explicit server_reference such as a URL, script path, or .mcp.json path."
        )
    )
    def list_project_mcp_tools(
        server_id: str = "",
        server_reference: str = "",
        cwd: str = ".",
        python_path: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        return mcp_servers.list_tools(
            server_id=server_id,
            server_reference=server_reference,
            cwd=cwd,
            python_path=python_path,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(
        description=(
            "Invoke one named tool on a local MCP server using its direct project interpreter path. "
            "Accepts either a managed server_id or an explicit server_reference."
        )
    )
    def call_project_mcp_tool(
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        server_id: str = "",
        server_reference: str = "",
        cwd: str = ".",
        python_path: str | None = None,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        return mcp_servers.call_tool(
            tool_name=tool_name,
            arguments=arguments,
            server_id=server_id,
            server_reference=server_reference,
            cwd=cwd,
            python_path=python_path,
            timeout_seconds=timeout_seconds,
        )

    @server.tool(description="Stop a managed local MCP server by id.")
    def stop_project_mcp_server(server_id: str) -> dict[str, Any]:
        return mcp_servers.stop_server(server_id)

    @server.tool(description="Read the captured log for a managed local MCP server.")
    def read_project_mcp_server_log(server_id: str, max_chars: int = 12000) -> dict[str, Any]:
        return mcp_servers.read_log(server_id, max_chars=max_chars)

    @server.tool(description="List local sandboxes stored under the runtime-root cache.")
    def list_sandboxes() -> dict[str, Any]:
        return {"sandboxes": sandboxes.list_sandboxes()}

    @server.tool(description="Write or overwrite a file inside a sandbox.")
    def sandbox_write_file(sandbox_id: str, path: str, content: str, make_parents: bool = True) -> dict[str, Any]:
        return sandboxes.write_file(sandbox_id, path, content, make_parents)

    @server.tool(description="Replace exact text inside a file in a sandbox.")
    def sandbox_replace_text(
        sandbox_id: str,
        path: str,
        old_text: str,
        new_text: str,
        count: int = 0,
    ) -> dict[str, Any]:
        return sandboxes.replace_text(sandbox_id, path, old_text, new_text, count)

    @server.tool(description="Delete a path inside a sandbox.")
    def sandbox_delete_path(sandbox_id: str, path: str, recursive: bool = False) -> dict[str, Any]:
        return sandboxes.delete_path(sandbox_id, path, recursive)

    @server.tool(description="Create a directory inside a sandbox.")
    def sandbox_make_directory(sandbox_id: str, path: str) -> dict[str, Any]:
        return sandboxes.make_directory(sandbox_id, path)

    @server.tool(description="Move or rename a path inside a sandbox.")
    def sandbox_move_path(sandbox_id: str, source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        return sandboxes.move_path(sandbox_id, source, destination, overwrite)

    @server.tool(description="Run a local shell command inside a sandbox.")
    def sandbox_run_command(sandbox_id: str, command: str, timeout_seconds: int = 120) -> dict[str, Any]:
        return sandboxes.run_command(sandbox_id, command, timeout_seconds)

    @server.tool(description="Show a diff summary for a sandbox.")
    def sandbox_diff(sandbox_id: str, max_chars: int = LOCAL_ONLY_MAX_DIFF_CHARS) -> dict[str, Any]:
        return sandboxes.diff(sandbox_id, max_chars)

    @server.tool(description="Copy changed files from a sandbox back into the active workspace.")
    def sandbox_apply_back(sandbox_id: str, overwrite: bool = True) -> dict[str, Any]:
        return sandboxes.apply_back(sandbox_id, overwrite)

    @server.tool(description="Destroy a sandbox and remove its cached files.")
    def sandbox_destroy(sandbox_id: str) -> dict[str, Any]:
        return sandboxes.destroy(sandbox_id)

    @server.tool(description="List local background jobs started by the local-only wrapper.")
    def list_background_jobs() -> dict[str, Any]:
        return {"jobs": jobs.list_jobs()}

    @server.tool(description="Read one background job's metadata and log.")
    def read_background_job(job_id: str, max_chars: int = 12000) -> dict[str, Any]:
        return jobs.read_job(job_id, max_chars)

    @server.tool(description="Cancel a local background job by id.")
    def cancel_background_job(job_id: str) -> dict[str, Any]:
        return jobs.cancel_job(job_id)

    return server


def _llamacpp_listener_port_for_base(base: Any, base_url: str) -> int:
    host_port = base_url.split("://", 1)[1] if "://" in base_url else base_url
    _, _, port_text = host_port.partition(":")
    try:
        return int(port_text or str(getattr(base, "DEFAULT_LLAMACPP_PORT", 8001)))
    except ValueError as exc:
        raise RuntimeError(f"Unable to determine the llama.cpp listener port from {base_url!r}.") from exc


def _parse_listener_pid_output(output: str) -> list[int]:
    text = output.strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(payload, int):
        return [payload] if payload > 0 else []
    if isinstance(payload, list):
        return [int(item) for item in payload if str(item).strip().isdigit() and int(item) > 0]
    if isinstance(payload, str) and payload.strip().isdigit():
        value = int(payload.strip())
        return [value] if value > 0 else []
    return []


def _wsl_windows_executable_interop_available(base: Any) -> bool:
    return bool(_workspace_runtime_capabilities(base).get("windows_interop_available"))


def _wsl_windows_executable_interop_probe() -> bool:
    command = shutil.which("cmd.exe") or shutil.which("powershell.exe")
    if not command:
        return False
    launch_command = [command]
    if Path("/init").is_file() and command.lower().endswith(".exe"):
        launch_command.insert(0, "/init")
    probe_command = (
        [*launch_command, "/c", "exit", "0"]
        if Path(command).name.lower() == "cmd.exe"
        else [*launch_command, "-NoProfile", "-Command", "exit 0"]
    )
    try:
        subprocess.run(
            probe_command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except Exception:
        return False
    return True


def _workspace_runtime_capabilities(base: Any, workspace: Path | None = None) -> dict[str, Any]:
    in_wsl_session = bool(callable(getattr(base, "in_wsl", None)) and base.in_wsl())
    interop_available = False
    if in_wsl_session:
        cached = getattr(base, "_QUBITZ_WSL_WINDOWS_EXECUTABLE_INTEROP", None)
        if cached is None:
            cached = _wsl_windows_executable_interop_probe()
            setattr(base, "_QUBITZ_WSL_WINDOWS_EXECUTABLE_INTEROP", bool(cached))
        interop_available = bool(cached)
    workspace_is_windows_backed = bool(workspace is not None and _is_windows_backed_workspace(workspace))
    linux_python = _preferred_project_linux_python(workspace) if workspace is not None else None
    windows_python = _preferred_project_windows_python(workspace) if workspace is not None else None
    preferred_python_path: Path | None = None
    preferred_python_runner = "shell"
    if in_wsl_session:
        if linux_python is not None:
            preferred_python_path = linux_python
        elif workspace_is_windows_backed and interop_available and windows_python is not None:
            preferred_python_path = windows_python
            preferred_python_runner = "powershell"
    elif os.name == "nt":
        preferred_python_path = windows_python or linux_python
    else:
        preferred_python_path = linux_python or windows_python
    return {
        "in_wsl": in_wsl_session,
        "workspace_is_windows_backed": workspace_is_windows_backed,
        "windows_interop_available": interop_available,
        "workspace_has_wsl_python": linux_python is not None,
        "workspace_has_windows_python": windows_python is not None,
        "can_run_windows_project_python": bool(
            windows_python is not None
            and (
                os.name == "nt"
                or (in_wsl_session and workspace_is_windows_backed and interop_available)
            )
        ),
        "can_run_windows_powershell": bool(
            os.name == "nt" or (in_wsl_session and workspace_is_windows_backed and interop_available)
        ),
        "preferred_python_path": preferred_python_path,
        "preferred_python_runner": preferred_python_runner,
    }


def _select_direct_workspace_python(base: Any, workspace: Path) -> tuple[Path, str] | None:
    capabilities = _workspace_runtime_capabilities(base, workspace)
    interpreter = capabilities.get("preferred_python_path")
    runner = capabilities.get("preferred_python_runner")
    if isinstance(interpreter, Path) and interpreter.exists() and runner in {"shell", "powershell"}:
        return interpreter, str(runner)
    return None


def _build_powershell_management_command(base: Any, script: str) -> list[str] | None:
    in_wsl_fn = getattr(base, "in_wsl", None)
    in_wsl_session = callable(in_wsl_fn) and in_wsl_fn()
    candidates: list[str] = []
    if in_wsl_session:
        candidates.extend(
            [
                shutil.which("powershell.exe") or "",
                shutil.which("pwsh.exe") or "",
                "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
            ]
        )
    else:
        candidates.extend(
            [
                shutil.which("powershell.exe") or shutil.which("powershell") or "",
                shutil.which("pwsh.exe") or shutil.which("pwsh") or "",
            ]
        )
    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.anchor and not candidate_path.exists():
            continue
        command = [candidate, "-NoProfile", "-Command", script]
        if in_wsl_session and candidate.lower().endswith(".exe") and Path("/init").is_file():
            command.insert(0, "/init")
        return command
    return None


def _terminate_llamacpp_listener_processes(
    base: Any,
    base_url: str,
    *,
    managed_process: subprocess.Popen[str] | None = None,
) -> list[int]:
    terminated: set[int] = set()
    if managed_process is not None and managed_process.poll() is None:
        managed_pid = int(getattr(managed_process, "pid", 0) or 0)
        managed_process.terminate()
        try:
            managed_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            managed_process.kill()
            managed_process.wait(timeout=10)
        if managed_pid > 0:
            terminated.add(managed_pid)
    port = _llamacpp_listener_port_for_base(base, base_url)
    script = textwrap.dedent(
        f"""
        $ErrorActionPreference = 'Stop'
        $connections = @(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue)
        $terminated = @()
        $ownerPids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object {{ $_ -gt 0 }})
        foreach ($ownerPid in $ownerPids) {{
            $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
            if ($null -eq $process) {{ continue }}
            $name = [IO.Path]::GetFileName([string]$process.Name).ToLowerInvariant()
            $exeName = [IO.Path]::GetFileName([string]$process.ExecutablePath).ToLowerInvariant()
            if (@('llama-server', 'llama-server.exe') -notcontains $name -and
                @('llama-server', 'llama-server.exe') -notcontains $exeName) {{
                continue
            }}
            Stop-Process -Id $ownerPid -Force -ErrorAction Stop
            $deadline = [DateTime]::UtcNow.AddSeconds(10)
            while ((Get-Process -Id $ownerPid -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {{
                Start-Sleep -Milliseconds 100
            }}
            if (-not (Get-Process -Id $ownerPid -ErrorAction SilentlyContinue)) {{
                $terminated += [int]$ownerPid
            }}
        }}
        [Console]::Out.Write((ConvertTo-Json -InputObject @($terminated) -Compress))
        """
    ).strip()
    powershell_command = _build_powershell_management_command(base, script)
    if powershell_command:
        completed = subprocess.run(
            powershell_command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if completed.returncode == 0:
            terminated.update(_parse_listener_pid_output(completed.stdout or ""))
    if os.name != "nt" and shutil.which("lsof"):
        completed = subprocess.run(
            ["lsof", "-ti", f"TCP:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        for pid in _parse_listener_pid_output(completed.stdout or ""):
            if pid in terminated:
                continue
            proc_root = Path("/proc") / str(pid)
            try:
                names = {
                    (proc_root / "comm").read_text(encoding="utf-8", errors="ignore").strip().lower(),
                    (proc_root / "exe").resolve().name.lower(),
                    Path(
                        (proc_root / "cmdline")
                        .read_bytes()
                        .split(b"\0", 1)[0]
                        .decode("utf-8", errors="ignore")
                    ).name.lower(),
                }
            except OSError:
                continue
            if not names.intersection({"llama-server", "llama-server.exe"}):
                continue
            try:
                os.kill(pid, 15)
            except OSError:
                continue
            deadline = time.time() + 10.0
            while time.time() < deadline and proc_root.exists():
                time.sleep(0.1)
            if proc_root.exists():
                with suppress(OSError):
                    os.kill(pid, 9)
            if not proc_root.exists():
                terminated.add(pid)
    return sorted(terminated)


def _llamacpp_listener_port_is_bound(base: Any, base_url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(base.normalize_base_url(base_url))
    host = parts.hostname or "127.0.0.1"
    port = parts.port or _llamacpp_listener_port_for_base(base, base_url)
    try:
        with socket.create_connection((host, port), timeout=0.35):
            return True
    except OSError:
        return False


def _patch_local_only_dependencies(base: Any) -> None:
    if getattr(base, "_QUBITZ_STRICT_MODEL_PATCHED", False):
        return

    def _base_resolve_llama_server_executable(workspace: Path, configured_path: str | None = None) -> str | None:
        configured = (configured_path or os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME) or "").strip()
        resolved_configured: Path | None = None
        if configured:
            if base.in_wsl() and base.WINDOWS_DRIVE_PATH_PATTERN.match(configured):
                drive = configured[0].lower()
                remainder = configured[2:].replace("\\", "/").lstrip("/")
                resolved_configured = Path("/mnt") / drive / remainder if remainder else Path("/mnt") / drive
            else:
                resolved_configured = Path(configured).expanduser()
            if resolved_configured.exists():
                return str(resolved_configured.resolve())
            found = shutil.which(configured)
            if found:
                return found
        if base.in_wsl():
            interop_available = _wsl_windows_executable_interop_available(base)
            windows_runtime_executable = base.llamacpp_runtime_dir(workspace) / "llama-server.exe"
            if interop_available and windows_runtime_executable.exists():
                return str(windows_runtime_executable.resolve())
            native_executable = base.llamacpp_native_server_executable(workspace)
            if native_executable.exists():
                return str(native_executable.resolve())
            if interop_available:
                try:
                    ensured = base.ensure_project_local_llamacpp_runtime(workspace)
                except Exception:
                    native_info = base.ensure_project_local_native_llamacpp_runtime(workspace)
                    return str(Path(native_info["executable"]))
                return str(Path(ensured["executable"]))
            native_info = base.ensure_project_local_native_llamacpp_runtime(workspace)
            return str(Path(native_info["executable"]))
        ensured = base.ensure_project_local_llamacpp_runtime(workspace)
        return str(Path(ensured["executable"]))

    @staticmethod
    def _reachable_transports(base_url: str) -> list[Any]:
        transports: list[Any] = []
        direct = base.DirectHTTPTransport(base_url, "direct")
        try:
            probe = direct.probe_json("/v1/models")
            if int(probe.get("status_code") or 0) == 200 or base.LlamaCppClient._probe_reports_loading(probe):
                transports.append(direct)
        except Exception:
            pass
        if (
            callable(getattr(base, "in_wsl", None))
            and base.in_wsl()
            and _wsl_windows_executable_interop_available(base)
            and not base.llamacpp_native_server_executable(Path.cwd()).exists()
        ):
            bridge = base.WindowsBridgeHTTPTransport(base_url)
            try:
                bridge.get_json("/v1/models")
                transports.append(bridge)
            except Exception:
                pass
        return transports

    original_init = base.LlamaCppClient.__init__

    def __init__(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.last_model_resolution_note = ""
        self._qubitz_model_load_retry_attempted = False

    def _wait_for_model_state(
        self: Any,
        timeout_seconds: float,
    ) -> tuple[list[dict[str, Any]], bool, bool, dict[str, Any], dict[str, Any]]:
        deadline = time.time() + timeout_seconds
        models: list[dict[str, Any]] = []
        props_available = False
        saw_loading = False
        last_model_probe: dict[str, Any] = {}
        last_props_probe: dict[str, Any] = {}
        while time.time() < deadline:
            last_model_probe = self._probe_with_fallback("/v1/models")
            if int(last_model_probe.get("status_code") or 0) == 200:
                payload = last_model_probe.get("json") or {}
                data = payload.get("data")
                models = data if isinstance(data, list) else []
                if models:
                    return models, props_available, saw_loading, last_model_probe, last_props_probe
            saw_loading = saw_loading or self._probe_reports_loading(last_model_probe)
            last_props_probe = self._probe_with_fallback("/props")
            if int(last_props_probe.get("status_code") or 0) == 200:
                props = last_props_probe.get("json") or {}
                props_available = bool(props)
            else:
                props_available = False
            saw_loading = saw_loading or self._probe_reports_loading(last_props_probe)
            if props_available:
                return [], True, saw_loading, last_model_probe, last_props_probe
            if self.server_process is not None:
                process = self.server_process.process
                if process is not None and process.poll() is not None:
                    log_tail = self.server_process.read_log_tail()
                    log_hint = f"\nServer log tail:\n{log_tail}" if log_tail else ""
                    raise RuntimeError(f"llama-server exited while the model was loading.{log_hint}")
            time.sleep(0.5)
        return models, props_available, saw_loading, last_model_probe, last_props_probe

    def _raise_model_state_error(
        self: Any,
        timeout_seconds: float,
        *,
        models: Sequence[dict[str, Any]],
        props_available: bool,
        saw_loading: bool,
        last_model_probe: dict[str, Any],
        last_props_probe: dict[str, Any],
    ) -> None:
        if props_available:
            return
        available_ids = [(item.get("id") or "").strip() for item in models if (item.get("id") or "").strip()]
        if available_ids:
            available = ", ".join(available_ids)
            raise RuntimeError(
                f"Model alias {self.config.model_name!r} is not available from {self.base_url}. Available: {available}"
            )
        log_hint = ""
        if self.server_process is not None:
            log_tail = self.server_process.read_log_tail()
            if log_tail:
                log_hint = f" Server log tail:\n{log_tail}"
        if saw_loading:
            raise RuntimeError(
                f"llama.cpp server at {self.base_url} was still loading the model after {timeout_seconds:.0f}s."
                f"{log_hint}"
            )
        probe_hint = ""
        if last_model_probe or last_props_probe:
            probe_hint = (
                f" Last /v1/models probe: {base.shorten(json.dumps(last_model_probe, ensure_ascii=False), 800)}"
                f" Last /props probe: {base.shorten(json.dumps(last_props_probe, ensure_ascii=False), 800)}"
            )
        raise RuntimeError(
            f"No model was advertised by {self.base_url} within {timeout_seconds:.0f}s. "
            "Verify that llama-server was started with a GGUF model and wait until loading finishes."
            f"{probe_hint}"
            f"{log_hint}"
        )

    def _can_restart_mismatched_server(self: Any) -> bool:
        base_url = base.normalize_base_url(self.base_url)
        default_url = base.normalize_base_url(base.DEFAULT_LLAMACPP_BASE_URL)
        if base_url == default_url:
            return True
        from urllib.parse import urlsplit

        base_parts = urlsplit(base_url)
        default_parts = urlsplit(default_url)
        loopback_hosts = {"127.0.0.1", "localhost"}

        def _port(parts: Any) -> int:
            if parts.port is not None:
                return int(parts.port)
            return 443 if parts.scheme == "https" else 80

        return (
            (base_parts.hostname or "").lower() in loopback_hosts
            and (default_parts.hostname or "").lower() in loopback_hosts
            and _port(base_parts) == _port(default_parts)
        )

    def _is_transient_model_load_exit_error(self: Any, exc: Exception) -> bool:
        return isinstance(exc, RuntimeError) and str(exc).startswith("llama-server exited while the model was loading.")

    def _wait_for_listener_shutdown(self: Any, timeout_seconds: float = 12.0) -> bool:
        deadline = time.time() + max(1.0, timeout_seconds)
        while time.time() < deadline:
            if not _llamacpp_listener_port_is_bound(base, self.base_url):
                return True
            time.sleep(0.25)
        return False

    def _stop_verified_listener(self: Any, failure_message: str) -> None:
        managed_process = self.server_process.process if self.server_process is not None else None
        for _attempt in range(2):
            _terminate_llamacpp_listener_processes(base, self.base_url, managed_process=managed_process)
            if self._wait_for_listener_shutdown():
                if self.server_process is not None:
                    self.server_process.process = None
                return
            managed_process = None
        raise RuntimeError(
            f"{failure_message} The port remains bound after two verified PID cleanup attempts; "
            "refusing to terminate an unverified process."
        )

    def _wait_for_model_state_with_retry(
        self: Any,
        timeout_seconds: float,
        model_name: str,
    ) -> tuple[list[dict[str, Any]], bool, bool, dict[str, Any], dict[str, Any]]:
        try:
            return self._wait_for_model_state(timeout_seconds)
        except Exception as exc:
            if not self._is_transient_model_load_exit_error(exc):
                raise
            if self._qubitz_model_load_retry_attempted:
                raise
            self._qubitz_model_load_retry_attempted = True
            self.last_model_resolution_note = (
                f"llama.cpp load failed once during startup; retrying once after clean restart for {model_name!r}."
            )
            self._stop_verified_listener(
                f"Failed to stop the previous local llama-server listener at {self.base_url} before retrying model load."
            )
            self.server_process = base.LlamaCppServerProcess(self.config)
            self.server_process.ensure_started()
            self.transports = []
            self.transport = base.DirectHTTPTransport(self.base_url, "direct")
            return self._wait_for_model_state(timeout_seconds)

    def _restart_with_requested_model(self: Any, timeout_seconds: float) -> None:
        self._stop_verified_listener(
            f"Failed to stop the previous local llama-server listener at {self.base_url} before starting the requested model."
        )
        self.server_process = base.LlamaCppServerProcess(self.config)
        self.server_process.ensure_started()
        self.transports = []
        self.transport = base.DirectHTTPTransport(self.base_url, "direct")
        deadline = time.time() + min(timeout_seconds, 120.0)
        while time.time() < deadline:
            transports = self._reachable_transports(self.base_url)
            if transports:
                self.transports = list(transports)
                self.transport = self.transports[0]
                return
            process = self.server_process.process
            if process is not None and process.poll() is not None:
                log_tail = self.server_process.read_log_tail()
                log_hint = f"\nServer log tail:\n{log_tail}" if log_tail else ""
                raise RuntimeError(f"llama-server exited while restarting the requested model.{log_hint}")
            time.sleep(0.5)
        log_tail = self.server_process.read_log_tail()
        log_hint = f"\nServer log tail:\n{log_tail}" if log_tail else ""
        raise RuntimeError(
            "Restarted llama-server with the requested GGUF, but the server did not become reachable in time."
            f"{log_hint}"
        )

    def ensure_model(
        self: Any,
        model_name: str,
        timeout_seconds: float = 300.0,
    ) -> str:
        self.last_model_resolution_note = ""
        self._qubitz_model_load_retry_attempted = False
        models, props_available, saw_loading, last_model_probe, last_props_probe = self._wait_for_model_state_with_retry(
            timeout_seconds
            ,
            model_name,
        )
        if props_available and not models:
            return model_name
        available_ids = [(item.get("id") or "").strip() for item in models if (item.get("id") or "").strip()]
        if model_name in available_ids:
            return model_name
        if available_ids and self._can_restart_mismatched_server():
            available_text = ", ".join(available_ids)
            self.last_model_resolution_note = (
                f"Detected a mismatched loaded llama.cpp model ({available_text}); restarting the local server "
                f"with the requested GGUF for {model_name!r}."
            )
            self._restart_with_requested_model(timeout_seconds)
            models, props_available, saw_loading, last_model_probe, last_props_probe = self._wait_for_model_state_with_retry(
                timeout_seconds
                ,
                model_name,
            )
            if props_available and not models:
                return model_name
            available_ids = [(item.get("id") or "").strip() for item in models if (item.get("id") or "").strip()]
            if model_name in available_ids:
                return model_name
        if len(available_ids) == 1:
            raise RuntimeError(
                f"llama.cpp server at {self.base_url} is serving {available_ids[0]!r} instead of requested alias "
                f"{model_name!r}. Refusing to reuse a mismatched loaded model."
            )
        if available_ids:
            available = ", ".join(available_ids)
            raise RuntimeError(
                f"Model alias {model_name!r} is not available from {self.base_url}. Loaded models: {available}"
            )
        self._raise_model_state_error(
            timeout_seconds,
            models=models,
            props_available=props_available,
            saw_loading=saw_loading,
            last_model_probe=last_model_probe,
            last_props_probe=last_props_probe,
        )
        return model_name

    base.LlamaCppClient.__init__ = __init__
    base.LlamaCppClient._wait_for_model_state = _wait_for_model_state
    base.LlamaCppClient._is_transient_model_load_exit_error = _is_transient_model_load_exit_error
    base.LlamaCppClient._wait_for_listener_shutdown = _wait_for_listener_shutdown
    base.LlamaCppClient._stop_verified_listener = _stop_verified_listener
    base.LlamaCppClient._wait_for_model_state_with_retry = _wait_for_model_state_with_retry
    base.LlamaCppClient._raise_model_state_error = _raise_model_state_error
    base.LlamaCppClient._can_restart_mismatched_server = _can_restart_mismatched_server
    base.LlamaCppClient._restart_with_requested_model = _restart_with_requested_model
    base.LlamaCppClient.ensure_model = ensure_model
    base.LlamaCppClient._reachable_transports = _reachable_transports
    base.resolve_llama_server_executable = _base_resolve_llama_server_executable
    if not hasattr(base, "_QUBITZ_ORIGINAL_WSL_WINDOWS_EXECUTABLE_INTEROP_AVAILABLE"):
        base._QUBITZ_ORIGINAL_WSL_WINDOWS_EXECUTABLE_INTEROP_AVAILABLE = getattr(
            base, "wsl_windows_executable_interop_available", None
        )
    base.wsl_windows_executable_interop_available = lambda: _wsl_windows_executable_interop_available(base)
    base._QUBITZ_STRICT_MODEL_PATCHED = True


_EMBEDDED_BASE_MODULE_LABEL = f"{re.sub(r'[^A-Za-z0-9]+', '_', Path(__file__).stem).strip('_')}_Base"
_EMBEDDED_BASE_SOURCE = 'from __future__ import annotations\n\nimport argparse\nimport asyncio\nimport base64\nimport gc\nimport hashlib\nimport json\nimport locale\nimport os\nimport queue\nimport re\nimport shlex\nimport shutil\nimport subprocess\nimport sys\nimport tarfile\nimport textwrap\nimport threading\nimport time\nimport traceback\nimport zipfile\nfrom contextlib import AsyncExitStack\nfrom dataclasses import asdict, dataclass, field, replace\nfrom datetime import datetime\nfrom pathlib import Path\nfrom typing import Any, Callable, Iterable, Sequence\n\nfrom mcp import ClientSession, StdioServerParameters, types as mcp_types\nfrom mcp.client.stdio import stdio_client\nfrom mcp.server.fastmcp import FastMCP\n\n\nDEFAULT_MODEL = "mradermacher/AgenticQwen-30B-A3B-i1-GGUF"\nDEFAULT_HF_GGUF_REPO_ID = "mradermacher/AgenticQwen-30B-A3B-i1-GGUF"\nQ4_GGUF_MODEL_FILENAME = "AgenticQwen-30B-A3B.i1-Q4_K_M.gguf"\nDEFAULT_GGUF_MODEL_LABEL = "AgenticQwen-30B-A3B.i1-Q4_K_M"\nDEFAULT_GGUF_MODEL_FILENAME = "AgenticQwen-30B-A3B.i1-Q4_K_M.gguf"\nDEFAULT_LLAMACPP_HOST = "127.0.0.1"\nDEFAULT_LLAMACPP_PORT = 8001\nDEFAULT_LLAMACPP_BASE_URL = f"http://{DEFAULT_LLAMACPP_HOST}:{DEFAULT_LLAMACPP_PORT}"\nDEFAULT_LLAMACPP_RELEASE_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"\nDEFAULT_LLAMACPP_MODEL_READY_TIMEOUT_SECONDS = 300.0\nDEFAULT_LLAMACPP_PARALLEL = 1\nGGUF_MODEL_PATH_ENV_NAME = "QUBITZ_GGUF_MODEL_PATH"\nLLAMACPP_SERVER_PATH_ENV_NAME = "QUBITZ_LLAMA_SERVER_PATH"\nLLAMACPP_SERVER_URL_ENV_NAME = "QUBITZ_LLAMACPP_URL"\nLLAMACPP_CHAT_TEMPLATE_ENV_NAME = "QUBITZ_LLAMACPP_CHAT_TEMPLATE"\nLLAMACPP_CHAT_TEMPLATE_FILE_ENV_NAME = "QUBITZ_LLAMACPP_CHAT_TEMPLATE_FILE"\nWSL_LLAMACPP_NATIVE_ARCH = "86"\nLLAMACPP_RUNTIME_REQUIRED_FILES = (\n    "llama-server.exe",\n    "ggml-cuda.dll",\n    "ggml.dll",\n    "ggml-base.dll",\n    "llama.dll",\n    "llama-common.dll",\n    "cublas64_12.dll",\n    "cublasLt64_12.dll",\n    "cudart64_12.dll",\n)\nDEFAULT_EMBED_MODEL = "BAAI/bge-code-v1"\nDEFAULT_HARNESS_NAME = "HARNESS.txt"\nDEFAULT_ENCRYPTED_HARNESS_NAME = "HARNESS.enc"\nHARNESS_KEY_ENV_NAME = "QUBITZ_HARNESS_KEY"\nLOCAL_HARNESS_KEY_NAME = "QUBITZ_HARNESS_KEY.local.txt"\nCURRENT_MEMORY_NAME = "MEMORY.md"\nARCHIVE_MEMORY_PREFIX = "MEMORY_"\nHISTORY_TURNS = 6\nMAX_TOOL_STEPS = 0\nMAX_ADAPTIVE_TOOL_STEPS = 24\nMAX_STEPS_ENV_NAME = "QUBITZ_MAX_STEPS"\nMAX_TOOL_RESULT_CHARS = 6000\nMAX_DIRECT_READ_FILES = 4\nMAX_DIRECT_READ_CHARS = 12000\nDEFAULT_NUM_CTX = 202752\nDEFAULT_NUM_PREDICT = 16384\nDEFAULT_CHAT_TEMPERATURE = 0.5\nDEFAULT_CHAT_TOP_P = 1.0\nDEFAULT_CHAT_MIN_P = 0.01\nDEFAULT_REPEAT_PENALTY = 1.0\nMAX_HISTORY_SUMMARY_CHARS = 6000\nMAX_RULES_SUMMARY_CHARS = 2400\nMAX_RULES_SECTION_ITEMS = 5\nMAX_MEMORY_CONTEXT_CHARS = 1200\nMAX_MEMORY_RESULT_SNIPPET_CHARS = 320\nMAX_MEMORY_CONTEXT_RESULTS = 2\nQUERY_INSTRUCTION = (\n    "Given a repository task or question, retrieve code and project files that help solve it."\n)\nREAD_INTENT_PATTERN = re.compile(\n    r"\\b(read|open|show|view|inspect|examine|review|check|display|print|cat|look\\s+at)\\b",\n    re.IGNORECASE,\n)\nDIRECT_READ_FAST_PATTERN = re.compile(r"^\\s*(read|open|show|view|display|print|cat)\\b", re.IGNORECASE)\nDIRECT_READ_ANALYSIS_PATTERN = re.compile(\n    r"\\b(summarize|summary|explain|analysis|analy[sz]e|compare|find|search|grep|locate|review|inspect|check|tell\\s+me|what|why|how)\\b",\n    re.IGNORECASE,\n)\nEDIT_INTENT_PATTERN = re.compile(\n    r"\\b(edit|modify|change|update|fix|refactor|rewrite|implement|create|add|remove|delete|rename|patch)\\b",\n    re.IGNORECASE,\n)\nVERIFY_INTENT_PATTERN = re.compile(\n    r"\\b(test|verify|validate|validation|lint|ruff|pytest|smoke\\s+test|regression)\\b",\n    re.IGNORECASE,\n)\nFILE_TOKEN_PATTERN = re.compile(\n    r"`([^`]+)`|\\"([^\\"]+)\\"|\'([^\']+)\'|(?<!\\w)([A-Za-z]:[A-Za-z0-9_ .\\-\\\\/]+(?:\\.[A-Za-z0-9_]+)|[A-Za-z0-9_.\\-\\\\/]+(?:\\.[A-Za-z0-9_]+))(?!\\w)"\n)\nWINDOWS_DRIVE_PATH_PATTERN = re.compile(r"^[A-Za-z]:[\\\\/]")\nWINDOWS_CUDA_RUNTIME_PATTERN = re.compile(r"^llama-.*-bin-win-cuda-(12(?:\\.\\d+)?)-x64\\.zip$")\nWINDOWS_CUDART_RUNTIME_PATTERN = re.compile(r"^cudart-llama-bin-win-cuda-(12(?:\\.\\d+)?)-x64\\.zip$")\nTEXT_SUFFIXES = {\n    ".bat",\n    ".c",\n    ".cfg",\n    ".cpp",\n    ".css",\n    ".csv",\n    ".h",\n    ".hpp",\n    ".html",\n    ".ini",\n    ".java",\n    ".js",\n    ".json",\n    ".md",\n    ".ps1",\n    ".py",\n    ".rb",\n    ".rs",\n    ".sh",\n    ".sql",\n    ".toml",\n    ".ts",\n    ".tsx",\n    ".txt",\n    ".xml",\n    ".yaml",\n    ".yml",\n}\nEXCLUDED_DIRS = {\n    ".cache",\n    ".git",\n    ".memory",\n    ".ruff_cache",\n    ".venv",\n    ".venv312",\n    "__pycache__",\n    "data",\n    "models",\n    "node_modules",\n}\nEXCLUDED_RETRIEVAL_FILENAMES = {\n    "changelog.txt",\n    "files_list.txt",\n    "launcher_err.txt",\n    "launcher_out.txt",\n}\nEXCLUDED_RETRIEVAL_SUFFIXES = ("HARNESS_KEY.local.txt",)\nVENV_DIR_PATTERN = re.compile(r"^\\.?(?:venv|env)(?:[0-9._-]+)?$", re.IGNORECASE)\nSKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")\nALLOWED_COMMANDS = {\n    ".venv/bin/python",\n    "git",\n    "ls",\n    "nvidia-smi",\n    "ollama",\n    "pwd",\n    "py",\n    "pytest",\n    "python",\n    "python3",\n    "rg",\n    "ruff",\n    "sed",\n    "tail",\n    "uv",\n}\nUI_BG = "#141416"\nUI_PANEL = "#1b1c1f"\nUI_PANEL_ALT = "#23252a"\nUI_TEXT = "#ffffff"\nUI_TEXT_MUTED = "#d7d7d9"\nUI_BORDER = "#2d3036"\nUI_SELECT = "#3c4048"\n\n\ndef now_stamp() -> str:\n    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")\n\n\ndef decode_subprocess_output(data: bytes | str | None) -> str:\n    if data is None or isinstance(data, str):\n        return data or ""\n    encodings = ["utf-8", "utf-8-sig", "cp1252"]\n    preferred = locale.getpreferredencoding(False) or "utf-8"\n    if preferred not in encodings:\n        encodings.append(preferred)\n    for encoding in encodings:\n        try:\n            return data.decode(encoding)\n        except UnicodeDecodeError:\n            continue\n    return data.decode("utf-8", errors="replace")\n\n\ndef env_float(name: str, default: float) -> float:\n    raw = os.environ.get(name, "").strip()\n    if not raw:\n        return default\n    try:\n        return float(raw)\n    except ValueError:\n        return default\n\n\ndef env_int(name: str, default: int) -> int:\n    raw = os.environ.get(name, "").strip()\n    if not raw:\n        return default\n    try:\n        return int(raw)\n    except ValueError:\n        return default\n\n\ndef env_str(name: str, default: str) -> str:\n    raw = os.environ.get(name, "").strip()\n    return raw or default\n\n\ndef _fernet_classes() -> tuple[type[Any], type[Exception]]:\n    try:\n        from cryptography.fernet import Fernet, InvalidToken\n    except ImportError as exc:\n        raise RuntimeError(\n            "Encrypted harness support requires the \'cryptography\' package. "\n            "Install requirements.txt in the active environment."\n        ) from exc\n    return Fernet, InvalidToken\n\n\ndef _harness_key_candidates(workspace: Path | None = None) -> list[tuple[str, bytes]]:\n    candidates: list[tuple[str, bytes]] = []\n    env_raw = os.environ.get(HARNESS_KEY_ENV_NAME, "").strip()\n    if env_raw:\n        candidates.append((HARNESS_KEY_ENV_NAME, env_raw.encode("utf-8")))\n    if workspace is not None:\n        helper_path = workspace / LOCAL_HARNESS_KEY_NAME\n        if helper_path.exists():\n            helper_raw = helper_path.read_text(encoding="utf-8", errors="ignore").strip()\n            if helper_raw:\n                candidates.append((LOCAL_HARNESS_KEY_NAME, helper_raw.encode("utf-8")))\n    return candidates\n\n\ndef _validated_harness_keys(workspace: Path | None = None) -> list[tuple[str, bytes]]:\n    Fernet, _ = _fernet_classes()\n    valid: list[tuple[str, bytes]] = []\n    invalid: list[str] = []\n    seen: set[bytes] = set()\n    for source, key in _harness_key_candidates(workspace):\n        if key in seen:\n            continue\n        seen.add(key)\n        try:\n            Fernet(key)\n        except Exception as exc:\n            invalid.append(f"{source}: {type(exc).__name__}: {exc}")\n            continue\n        valid.append((source, key))\n    if valid:\n        return valid\n    if invalid:\n        raise RuntimeError(f"No valid Fernet harness key was found. Tried: {\'; \'.join(invalid)}")\n    raise RuntimeError(\n        f"{HARNESS_KEY_ENV_NAME} is required when {DEFAULT_ENCRYPTED_HARNESS_NAME} is present. "\n        f"Set it in the environment or provide {LOCAL_HARNESS_KEY_NAME} in the workspace root."\n    )\n\n\ndef harness_key_bytes(workspace: Path | None = None) -> bytes:\n    return _validated_harness_keys(workspace)[0][1]\n\n\ndef encrypt_harness_text(plaintext: str, workspace: Path | None = None) -> bytes:\n    Fernet, _ = _fernet_classes()\n    return Fernet(harness_key_bytes(workspace)).encrypt(plaintext.encode("utf-8"))\n\n\ndef generate_harness_key() -> str:\n    Fernet, _ = _fernet_classes()\n    return Fernet.generate_key().decode("utf-8")\n\n\ndef decrypt_harness_bytes(ciphertext: bytes, workspace: Path | None = None) -> str:\n    Fernet, InvalidToken = _fernet_classes()\n    failed_sources: list[str] = []\n    for source, key in _validated_harness_keys(workspace):\n        try:\n            plaintext = Fernet(key).decrypt(ciphertext)\n            return plaintext.decode("utf-8")\n        except InvalidToken:\n            failed_sources.append(source)\n            continue\n    raise RuntimeError(\n        f"Failed to decrypt {DEFAULT_ENCRYPTED_HARNESS_NAME}. "\n        f"Tried valid key source(s): {\', \'.join(failed_sources) or HARNESS_KEY_ENV_NAME}."\n    )\n\n\ndef _sync_encrypted_harness_from_plaintext(workspace: Path, plaintext: str) -> None:\n    encrypted_path = workspace / DEFAULT_ENCRYPTED_HARNESS_NAME\n    encrypted_path.write_bytes(encrypt_harness_text(plaintext, workspace))\n\n\ndef load_harness_text(workspace: Path) -> str:\n    encrypted_path = workspace / DEFAULT_ENCRYPTED_HARNESS_NAME\n    plaintext_path = workspace / DEFAULT_HARNESS_NAME\n    plaintext_text = plaintext_path.read_text(encoding="utf-8", errors="ignore") if plaintext_path.exists() else None\n    if encrypted_path.exists():\n        try:\n            encrypted_text = decrypt_harness_bytes(encrypted_path.read_bytes(), workspace)\n        except Exception:\n            if plaintext_text is not None:\n                try:\n                    _sync_encrypted_harness_from_plaintext(workspace, plaintext_text)\n                except Exception:\n                    pass\n                return plaintext_text\n            raise\n        if plaintext_text is not None and plaintext_text != encrypted_text:\n            try:\n                _sync_encrypted_harness_from_plaintext(workspace, plaintext_text)\n            except Exception:\n                pass\n            return plaintext_text\n        return encrypted_text\n    if plaintext_text is not None:\n        return plaintext_text\n    raise FileNotFoundError(\n        f"Missing harness file. Expected {DEFAULT_ENCRYPTED_HARNESS_NAME} or {DEFAULT_HARNESS_NAME} in {workspace}."\n    )\n\n\ndef write_encrypted_harness(workspace: Path) -> Path:\n    plaintext_path = workspace / DEFAULT_HARNESS_NAME\n    if not plaintext_path.exists():\n        raise FileNotFoundError(\n            f"Cannot create {DEFAULT_ENCRYPTED_HARNESS_NAME} because {DEFAULT_HARNESS_NAME} is missing in {workspace}."\n        )\n    encrypted_path = workspace / DEFAULT_ENCRYPTED_HARNESS_NAME\n    encrypted_path.write_bytes(\n        encrypt_harness_text(plaintext_path.read_text(encoding="utf-8", errors="ignore"), workspace)\n    )\n    return encrypted_path\n\n\ndef token_set(text: str) -> set[str]:\n    return set(re.findall(r"[a-z0-9_./:-]+", text.lower()))\n\n\ndef shorten(text: str, limit: int = 1200) -> str:\n    if len(text) <= limit:\n        return text\n    return text[: limit - 3].rstrip() + "..."\n\n\ndef summarize_instruction_markdown(\n    text: str,\n    max_chars: int = MAX_RULES_SUMMARY_CHARS,\n    max_items_per_section: int = MAX_RULES_SECTION_ITEMS,\n) -> str:\n    if not text.strip():\n        return ""\n    sections: list[tuple[str, list[str]]] = []\n    heading = ""\n    items: list[str] = []\n\n    def flush_section() -> None:\n        nonlocal heading, items\n        if heading or items:\n            sections.append((heading, items))\n        heading = ""\n        items = []\n\n    for raw_line in text.splitlines():\n        line = raw_line.strip()\n        if not line:\n            continue\n        if line.startswith("#"):\n            flush_section()\n            heading = line.lstrip("#").strip()\n            continue\n        items.append(shorten(line, 220))\n    flush_section()\n\n    output: list[str] = []\n    current_chars = 0\n    for section_heading, section_items in sections:\n        block: list[str] = []\n        if section_heading:\n            block.append(f"{section_heading}:")\n        for item in section_items[:max_items_per_section]:\n            normalized = item\n            if not (normalized.startswith(("-", "*")) or re.match(r"^\\d+\\.", normalized)):\n                normalized = f"- {normalized}"\n            block.append(normalized)\n        for entry in block:\n            projected = current_chars + len(entry) + 1\n            if projected > max_chars:\n                return "\\n".join(output).strip() or shorten(text, max_chars)\n            output.append(entry)\n            current_chars = projected\n    return "\\n".join(output).strip() or shorten(text, max_chars)\n\n\ndef summarize_memory_markdown(text: str, limit: int = MAX_MEMORY_CONTEXT_CHARS) -> str:\n    if not text.strip():\n        return ""\n    reduced_lines: list[str] = []\n    for raw_line in text.splitlines():\n        line = raw_line.strip()\n        if not line or line in {"# Qubitz Memory", "## Notes", "## Recent Turns"}:\n            continue\n        if line.startswith(("Updated:", "Workspace:")):\n            continue\n        if line.startswith("### "):\n            reduced_lines.append(line)\n        elif line.startswith("- "):\n            reduced_lines.append(shorten(line, 180))\n        else:\n            reduced_lines.append(shorten(line, 240))\n    if not reduced_lines:\n        return ""\n    return shorten("\\n".join(reduced_lines[-12:]), limit)\n\n\ndef format_bytes(value: int | float | None) -> str:\n    if value is None:\n        return "unknown"\n    size = float(value)\n    units = ["B", "KiB", "MiB", "GiB", "TiB"]\n    for unit in units:\n        if size < 1024.0 or unit == units[-1]:\n            return f"{size:.2f} {unit}"\n        size /= 1024.0\n    return f"{size:.2f} TiB"\n\n\ndef gib_to_bytes(value: float) -> int:\n    return int(max(0.0, value) * 1024**3)\n\n\ndef estimate_tokens(text: str) -> int:\n    return max(1, (len(text) + 3) // 4)\n\n\ndef is_sensitive_path_name(path: Path) -> bool:\n    name = path.name.lower()\n    if name == ".env" or name.startswith(".env."):\n        return True\n    return path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}\n\n\ndef extract_file_tokens(text: str) -> list[str]:\n    tokens: list[str] = []\n    for match in FILE_TOKEN_PATTERN.finditer(text):\n        candidate = next((group for group in match.groups() if group), "")\n        candidate = candidate.strip().strip("()[]{}<>.,:;")\n        if not candidate:\n            continue\n        if candidate.startswith(("http://", "https://")):\n            continue\n        tokens.append(candidate)\n    return tokens\n\n\ndef is_explicit_absolute_path_text(candidate: str) -> bool:\n    normalized = candidate.strip()\n    if not normalized:\n        return False\n    if WINDOWS_DRIVE_PATH_PATTERN.match(normalized):\n        return True\n    return Path(normalized).expanduser().is_absolute()\n\n\ndef describe_tool_action(name: str, arguments: dict[str, Any]) -> str:\n    if name == "read_file":\n        path = arguments.get("path", "?")\n        start_line = arguments.get("start_line", 1)\n        end_line = arguments.get("end_line", 200)\n        return f"Reading {path} lines {start_line}-{end_line}"\n    if name == "write_file":\n        return f"Writing {arguments.get(\'path\', \'?\')}"\n    if name == "replace_text":\n        return f"Modifying {arguments.get(\'path\', \'?\')}"\n    if name == "delete_path":\n        return f"Deleting {arguments.get(\'path\', \'?\')}"\n    if name == "make_directory":\n        return f"Creating directory {arguments.get(\'path\', \'?\')}"\n    if name == "move_path":\n        return f"Moving {arguments.get(\'source\', \'?\')} -> {arguments.get(\'destination\', \'?\')}"\n    if name == "search_text":\n        return f"Searching text for {arguments.get(\'query\', \'\')!r}"\n    if name == "list_files":\n        return f"Listing files under {arguments.get(\'path\', \'.\')}"\n    if name == "install_python_package":\n        if arguments.get("requirements_file"):\n            return f"Installing Python dependencies from {arguments[\'requirements_file\']}"\n        return f"Installing Python packages {arguments.get(\'packages\', [])}"\n    if name == "run_project_command":\n        return f"Running command {arguments.get(\'command\', [])}"\n    if name == "run_powershell_command":\n        return f"Running PowerShell command in {arguments.get(\'cwd\', \'.\')}"\n    if name == "read_memory":\n        return "Reading persistent memory"\n    if name == "search_memory":\n        return f"Searching memory for {arguments.get(\'query\', \'\')!r}"\n    return f"Calling tool {name}"\n\n\ndef summarize_tool_result(name: str, payload: dict[str, Any]) -> str:\n    if payload.get("is_error"):\n        return f"{name} failed: {shorten(payload.get(\'content_text\', \'tool error\'), 240)}"\n    structured = payload.get("structured_content") or {}\n    if name == "read_file":\n        return (\n            f"Loaded {structured.get(\'path\', \'?\')} "\n            f"lines {structured.get(\'start_line\', \'?\')}-{structured.get(\'end_line\', \'?\')}"\n        )\n    if name == "write_file":\n        return f"Wrote {structured.get(\'path\', \'?\')}"\n    if name == "replace_text":\n        return f"Updated {structured.get(\'path\', \'?\')} with {structured.get(\'replacements\', 0)} replacements"\n    if name == "delete_path":\n        return f"Deleted {structured.get(\'deleted\', \'?\')}"\n    if name == "make_directory":\n        return f"Created {structured.get(\'path\', \'?\')}"\n    if name == "move_path":\n        return f"Moved to {structured.get(\'destination\', \'?\')}"\n    if name == "search_text":\n        matches = structured.get("matches") or []\n        return f"Search found {len(matches)} matches"\n    if name == "list_files":\n        entries = structured.get("entries") or []\n        return f"Listed {len(entries)} entries"\n    if name == "install_python_package":\n        return f"Install command exited with code {structured.get(\'return_code\', \'?\')}"\n    if name == "run_project_command":\n        return f"Command exited with code {structured.get(\'return_code\', \'?\')}"\n    if name == "run_powershell_command":\n        return f"PowerShell exited with code {structured.get(\'return_code\', \'?\')}"\n    return shorten(payload.get("content_text", f"{name} finished"), 240)\n\n\ndef in_wsl() -> bool:\n    try:\n        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="ignore")\n    except OSError:\n        return False\n    lowered = version_text.lower()\n    return "microsoft" in lowered or "wsl" in lowered\n\n\ndef configure_project_environment(workspace: Path) -> None:\n    cache_root = workspace / ".cache"\n    hf_home = cache_root / "huggingface"\n    os.environ["HF_HOME"] = str(hf_home)\n    os.environ["HUGGINGFACE_HUB_CACHE"] = str(hf_home / "hub")\n    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"\n    if os.environ.get("QUBITZ_OFFLINE") == "1":\n        os.environ["HF_HUB_OFFLINE"] = "1"\n        os.environ["TRANSFORMERS_OFFLINE"] = "1"\n    cache_root.mkdir(parents=True, exist_ok=True)\n    hf_home.mkdir(parents=True, exist_ok=True)\n\n\ndef ensure_display_environment() -> None:\n    if not in_wsl():\n        return\n    if os.environ.get("DISPLAY"):\n        return\n    os.environ["DISPLAY"] = ":0"\n\n\ndef configure_tk_environment() -> None:\n    if getattr(sys, "frozen", False):\n        base_path = Path(getattr(sys, "_MEIPASS"))\n    else:\n        base_path = Path(sys.prefix or getattr(sys, "base_prefix", sys.executable))\n        if not (base_path / "tcl").exists() and getattr(sys, "base_prefix", None):\n            base_path = Path(sys.base_prefix)\n    os.environ["TCL_LIBRARY"] = str(base_path / "tcl" / "tcl8.6")\n    os.environ["TK_LIBRARY"] = str(base_path / "tcl" / "tk8.6")\n\n\ndef import_tk_modules():\n    ensure_display_environment()\n    configure_tk_environment()\n    import tkinter as tk\n    from tkinter import filedialog, messagebox, scrolledtext, ttk\n\n    return tk, ttk, scrolledtext, messagebox, filedialog\n\n\ndef wsl_path_to_windows(path: Path) -> str | None:\n    if not in_wsl():\n        return None\n    parts = path.expanduser().resolve().as_posix().split("/")\n    if len(parts) < 3 or parts[1] != "mnt":\n        return None\n    drive = parts[2]\n    if len(drive) != 1 or not drive.isalpha():\n        return None\n    remainder = "\\\\".join(parts[3:])\n    return f"{drive.upper()}:\\\\{remainder}" if remainder else f"{drive.upper()}:\\\\"\n\n\ndef normalize_workspace_directory(selection: str) -> Path:\n    normalized = selection.strip().strip(\'"\')\n    if in_wsl() and WINDOWS_DRIVE_PATH_PATTERN.match(normalized):\n        drive = normalized[0].lower()\n        remainder = normalized[2:].replace("\\\\", "/").lstrip("/")\n        return Path("/mnt") / drive / remainder if remainder else Path("/mnt") / drive\n    return Path(normalized).expanduser()\n\n\ndef pick_workspace_directory(current_workspace: Path, filedialog: Any, parent: Any | None = None) -> Path | None:\n    if parent is not None:\n        try:\n            parent.update_idletasks()\n            parent.lift()\n            parent.focus_force()\n        except Exception:\n            pass\n    if in_wsl():\n        powershell = shutil.which("powershell.exe")\n        if powershell:\n            script_lines = [\n                "Add-Type -AssemblyName System.Windows.Forms",\n                "Add-Type -AssemblyName System.Drawing",\n                "$owner = New-Object System.Windows.Forms.Form",\n                "$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual",\n                "$owner.Location = New-Object System.Drawing.Point(-32000, -32000)",\n                "$owner.Size = New-Object System.Drawing.Size(1, 1)",\n                "$owner.ShowInTaskbar = $false",\n                "$owner.TopMost = $true",\n                "$owner.Opacity = 0",\n                "[void]$owner.Show()",\n                "$owner.Activate()",\n                "$dialog = New-Object System.Windows.Forms.FolderBrowserDialog",\n                "$dialog.Description = \'Select the workspace directory\'",\n                "$dialog.ShowNewFolderButton = $true",\n            ]\n            initial_windows_path = wsl_path_to_windows(current_workspace)\n            if initial_windows_path:\n                escaped = initial_windows_path.replace("\'", "\'\'")\n                script_lines.append(f"$dialog.SelectedPath = \'{escaped}\'")\n            script_lines.extend(\n                [\n                    "$result = $dialog.ShowDialog($owner)",\n                    "$owner.Close()",\n                    "$owner.Dispose()",\n                    "if ($result -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::Out.Write($dialog.SelectedPath) }",\n                ]\n            )\n            completed = subprocess.run(\n                [powershell, "-NoProfile", "-STA", "-Command", "; ".join(script_lines)],\n                capture_output=True,\n                text=True,\n                check=False,\n            )\n            selected = completed.stdout.strip()\n            if completed.returncode == 0 and selected:\n                return normalize_workspace_directory(selected).resolve()\n    initial_directory = current_workspace.as_posix() if in_wsl() else str(current_workspace)\n    dialog_kwargs: dict[str, Any] = {"initialdir": initial_directory, "mustexist": True}\n    if parent is not None:\n        dialog_kwargs["parent"] = parent\n    selected = filedialog.askdirectory(**dialog_kwargs)\n    if not selected:\n        return None\n    return normalize_workspace_directory(selected).resolve()\n\n\ndef relative_path(path: Path, workspace: Path) -> str:\n    try:\n        return path.resolve().relative_to(workspace.resolve()).as_posix()\n    except ValueError:\n        return path.resolve().as_posix()\n\n\ndef iter_workspace_files(workspace: Path, excluded_dirs: set[str]) -> Iterable[Path]:\n    for root, dirnames, filenames in os.walk(workspace, topdown=True):\n        dirnames[:] = [dirname for dirname in dirnames if not is_excluded_dir_name(dirname, excluded_dirs)]\n        root_path = Path(root)\n        for filename in filenames:\n            yield root_path / filename\n\n\ndef resolve_workspace_path(\n    workspace: Path,\n    candidate: str,\n    *,\n    allow_missing: bool = True,\n    allow_external: bool = False,\n) -> Path:\n    normalized = normalize_workspace_text(candidate)\n    translated_windows_path: Path | None = None\n    if in_wsl() and WINDOWS_DRIVE_PATH_PATTERN.match(normalized):\n        drive = normalized[0].lower()\n        remainder = normalized[2:].replace("\\\\", "/").lstrip("/")\n        translated_windows_path = Path("/mnt") / drive / remainder if remainder else Path("/mnt") / drive\n    raw = translated_windows_path or Path(normalized).expanduser()\n    explicit_absolute = translated_windows_path is not None or raw.is_absolute()\n    resolved = raw.resolve() if explicit_absolute else (workspace / raw).resolve()\n    workspace_root = workspace.resolve()\n    if not allow_external and not resolved.is_relative_to(workspace_root):\n        raise ValueError(f"Path escapes workspace: {candidate}")\n    if not allow_missing and not resolved.exists():\n        raise FileNotFoundError(candidate)\n    return resolved\n\n\ndef normalize_workspace_text(candidate: str) -> str:\n    normalized = candidate.strip()\n    if in_wsl() and normalized and not WINDOWS_DRIVE_PATH_PATTERN.match(normalized):\n        normalized = normalized.replace("\\\\", "/")\n    return normalized\n\n\ndef is_windows_backed_workspace(workspace: Path) -> bool:\n    resolved = workspace.resolve().as_posix()\n    if WINDOWS_DRIVE_PATH_PATTERN.match(resolved):\n        return True\n    return resolved.startswith("/mnt/") and len(resolved) >= 7 and resolved[5].isalpha() and resolved[6] == "/"\n\n\ndef project_python_candidates(workspace: Path) -> list[Path]:\n    linux_candidates = [workspace / ".venv" / "bin" / "python"]\n    windows_candidates = [\n        workspace / ".venv312" / "Scripts" / "python.exe",\n        workspace / ".venv313" / "Scripts" / "python.exe",\n        workspace / ".venv" / "Scripts" / "python.exe",\n    ]\n    ordered = windows_candidates + linux_candidates if is_windows_backed_workspace(workspace) else linux_candidates + windows_candidates\n    candidates: list[Path] = []\n    seen: set[Path] = set()\n    for candidate in ordered:\n        resolved_candidate = candidate.resolve(strict=False)\n        if resolved_candidate in seen:\n            continue\n        seen.add(resolved_candidate)\n        candidates.append(candidate)\n    return candidates\n\n\ndef preferred_project_python(workspace: Path) -> Path | None:\n    for candidate in project_python_candidates(workspace):\n        if candidate.exists():\n            return candidate.resolve()\n    return None\n\n\ndef resolve_allowed_project_command(workspace: Path, command: Sequence[str]) -> list[str]:\n    if not command:\n        raise ValueError("Command cannot be empty.")\n    normalized_command = list(command)\n    executable = normalize_workspace_text(command[0])\n    normalized_command[0] = executable\n    if executable in ALLOWED_COMMANDS:\n        return normalized_command\n    try:\n        resolved_executable = resolve_workspace_path(workspace, executable, allow_missing=False, allow_external=True)\n    except Exception:\n        resolved_executable = None\n    if resolved_executable is not None:\n        allowed_pythons = {candidate.resolve() for candidate in project_python_candidates(workspace) if candidate.exists()}\n        if resolved_executable in allowed_pythons:\n            normalized_command[0] = str(resolved_executable)\n            return normalized_command\n    raise ValueError(f"Command is not allowed: {command[0]}")\n\n\ndef normalize_base_url(value: str | None) -> str:\n    normalized = (value or "").strip()\n    if not normalized:\n        return DEFAULT_LLAMACPP_BASE_URL\n    if "://" not in normalized:\n        normalized = f"http://{normalized}"\n    normalized = normalized.rstrip("/")\n    if normalized.endswith("/v1"):\n        normalized = normalized[: -len("/v1")]\n    return normalized\n\n\ndef wsl_path_to_windows_path(value: str | Path) -> str:\n    text = str(value)\n    if WINDOWS_DRIVE_PATH_PATTERN.match(text):\n        return text.replace("/", "\\\\")\n    if text.startswith("/mnt/") and len(text) >= 7 and text[5].isalpha() and text[6] == "/":\n        drive = text[5].upper()\n        remainder = text[7:].replace("/", "\\\\")\n        return f"{drive}:\\\\{remainder}"\n    return text\n\n\ndef powershell_single_quote(value: str) -> str:\n    return "\'" + value.replace("\'", "\'\'") + "\'"\n\n\ndef strip_leading_python_launcher(command: str) -> str:\n    stripped = command.strip()\n    patterns = (\n        r"^(?:python(?:\\.exe)?|py)\\s+(?P<rest>.+)$",\n        r"^(?:\\.[\\\\/][^\\s]*?(?:Scripts[\\\\/]python\\.exe|bin[\\\\/]python))\\s+(?P<rest>.+)$",\n    )\n    for pattern in patterns:\n        match = re.match(pattern, stripped, flags=re.IGNORECASE)\n        if match:\n            return match.group("rest").strip()\n    return stripped\n\n\ndef extract_powershell_script(command: str) -> str:\n    stripped = command.strip()\n    lower = stripped.lower()\n    for prefix in ("powershell.exe", "powershell", "pwsh.exe", "pwsh"):\n        token = prefix + " "\n        if lower.startswith(token):\n            script = stripped[len(token) :].lstrip()\n            while True:\n                lowered_script = script.lower()\n                if lowered_script.startswith("-noprofile "):\n                    script = script[len("-noprofile ") :].lstrip()\n                    continue\n                if lowered_script.startswith("-command "):\n                    script = script[len("-command ") :].lstrip()\n                    continue\n                if lowered_script.startswith("-c "):\n                    script = script[len("-c ") :].lstrip()\n                    continue\n                break\n            if len(script) >= 2 and script[0] == script[-1] and script[0] in {"\'", \'"\'}:\n                script = script[1:-1]\n            return script\n    return stripped\n\n\ndef canonicalize_workspace_script(working_directory: Path, script: str) -> str:\n    normalized = script.strip()\n    activation_match = re.match(\n        r"^\\s*(?P<activate>\\.[\\\\/][^;]*?[\\\\/](?:Scripts[\\\\/]Activate\\.ps1|bin[\\\\/]activate))\\s*;\\s*(?P<rest>.+)$",\n        normalized,\n        flags=re.IGNORECASE,\n    )\n    if activation_match:\n        activate_path = normalize_workspace_text(activation_match.group("activate"))\n        rest = strip_leading_python_launcher(activation_match.group("rest"))\n        activate_candidate = (working_directory / Path(activate_path)).resolve()\n        if activate_candidate.name.lower() == "activate.ps1":\n            interpreter = activate_candidate.parent / "python.exe"\n            if interpreter.exists():\n                translated = wsl_path_to_windows_path(interpreter)\n                return f"& {powershell_single_quote(translated)} {rest}"\n    python_match = re.match(\n        r"^\\s*(?P<python>\\.[\\\\/][^;]*?[\\\\/](?:Scripts[\\\\/]python\\.exe|bin[\\\\/]python))\\s*(?P<rest>.*)$",\n        normalized,\n        flags=re.IGNORECASE,\n    )\n    if python_match:\n        interpreter = (working_directory / Path(normalize_workspace_text(python_match.group("python")))).resolve()\n        if interpreter.exists():\n            rest = python_match.group("rest").strip()\n            target = wsl_path_to_windows_path(interpreter) if interpreter.suffix.lower() == ".exe" else str(interpreter)\n            return f"& {powershell_single_quote(target)} {rest}".rstrip()\n    return script\n\n\ndef wrap_windows_command_for_wsl(command: Sequence[str]) -> list[str]:\n    shell_command = " ".join(shlex.quote(part) for part in command)\n    return ["/bin/bash", "-lc", shell_command]\n\n\ndef render_windows_command_for_wsl_shell(command: Sequence[str]) -> str:\n    return " ".join(shlex.quote(part) for part in command)\n\n\ndef resolve_gguf_model_path(\n    workspace: Path,\n    configured_path: str | None = None,\n    preferred_filename: str = DEFAULT_GGUF_MODEL_FILENAME,\n) -> Path | None:\n    candidate_text = (configured_path or os.environ.get(GGUF_MODEL_PATH_ENV_NAME) or "").strip()\n    if candidate_text:\n        candidate_name = Path(candidate_text).name\n        if candidate_name != preferred_filename:\n            raise ValueError(\n                f"Configured GGUF model must be {preferred_filename}, got {candidate_name!r}."\n            )\n        return resolve_workspace_path(\n            workspace,\n            candidate_text,\n            allow_missing=False,\n            allow_external=True,\n        )\n    preferred = [\n        workspace / "models" / preferred_filename,\n        workspace / preferred_filename,\n        workspace / ".cache" / "models" / "mradermacher" / "AgenticQwen-30B-A3B-i1-GGUF" / preferred_filename,\n    ]\n    search_roots = [\n        workspace / "models",\n        workspace / ".cache" / "models",\n        workspace,\n    ]\n    seen: set[Path] = set()\n    for candidate in preferred:\n        if candidate.exists():\n            resolved = candidate.resolve()\n            if resolved not in seen:\n                seen.add(resolved)\n                return resolved\n    for root in search_roots:\n        if not root.exists():\n            continue\n        for candidate in sorted(root.rglob(preferred_filename)):\n            if path_has_excluded_dir(candidate, EXCLUDED_DIRS):\n                continue\n            resolved = candidate.resolve()\n            if resolved not in seen:\n                seen.add(resolved)\n                return resolved\n    return None\n\n\ndef download_default_gguf_model(workspace: Path, filename: str = DEFAULT_GGUF_MODEL_FILENAME) -> Path:\n    if os.environ.get("QUBITZ_OFFLINE") == "1":\n        raise RuntimeError("Automatic GGUF download is disabled because QUBITZ_OFFLINE=1.")\n    try:\n        from huggingface_hub import hf_hub_download\n    except ImportError as exc:\n        raise RuntimeError(\n            "Automatic GGUF download requires the \'huggingface_hub\' package in the active runtime."\n        ) from exc\n    local_dir = workspace / ".cache" / "models" / "mradermacher" / "AgenticQwen-30B-A3B-i1-GGUF"\n    local_dir.mkdir(parents=True, exist_ok=True)\n    downloaded = hf_hub_download(\n        repo_id=DEFAULT_HF_GGUF_REPO_ID,\n        filename=filename,\n        local_dir=local_dir,\n    )\n    return Path(downloaded).resolve()\n\n\ndef llamacpp_native_runtime_dir(workspace: Path) -> Path:\n    return workspace / ".cache" / "llama.cpp-linux"\n\n\ndef llamacpp_native_source_dir(workspace: Path, release_tag: str) -> Path:\n    return workspace / ".cache" / "src" / f"llama.cpp-{release_tag}"\n\n\ndef llamacpp_native_build_dir(workspace: Path) -> Path:\n    return llamacpp_native_runtime_dir(workspace) / "build"\n\n\ndef llamacpp_native_server_executable(workspace: Path) -> Path:\n    return llamacpp_native_build_dir(workspace) / "bin" / "llama-server"\n\n\ndef llamacpp_native_build_log_path(workspace: Path) -> Path:\n    return llamacpp_native_runtime_dir(workspace) / "build.log"\n\n\ndef llamacpp_native_server_log_path(workspace: Path) -> Path:\n    return llamacpp_native_runtime_dir(workspace) / "llama-server.log"\n\n\ndef ensure_wsl_native_build_tools(workspace: Path) -> dict[str, Path]:\n    candidate_bin_dirs: list[Path] = []\n    project_venv_bin = workspace / ".venv" / "bin"\n    if project_venv_bin.exists():\n        candidate_bin_dirs.append(project_venv_bin)\n    python_bin_dir = Path(sys.executable).resolve().parent\n    if python_bin_dir not in candidate_bin_dirs:\n        candidate_bin_dirs.append(python_bin_dir)\n\n    def resolve_tool(name: str) -> Path | None:\n        for bin_dir in candidate_bin_dirs:\n            candidate = bin_dir / name\n            if candidate.exists():\n                return candidate.resolve()\n        found = shutil.which(name)\n        return Path(found).resolve() if found else None\n\n    cmake_path = resolve_tool("cmake")\n    ninja_path = resolve_tool("ninja")\n    if cmake_path is None or ninja_path is None:\n        install_python = project_venv_bin / "python"\n        if not install_python.exists():\n            install_python = Path(sys.executable).resolve()\n        result = subprocess.run(\n            [str(install_python), "-m", "pip", "install", "cmake", "ninja"],\n            capture_output=True,\n            text=True,\n            timeout=1800,\n            check=False,\n        )\n        if result.returncode != 0:\n            raise RuntimeError(\n                "Failed to install cmake/ninja for the WSL native llama.cpp build: "\n                + shorten(result.stderr or result.stdout, 2000)\n            )\n        cmake_path = resolve_tool("cmake")\n        ninja_path = resolve_tool("ninja")\n    if cmake_path is None or ninja_path is None:\n        raise RuntimeError("Failed to locate cmake/ninja for the WSL native llama.cpp build after installation.")\n    return {\n        "cmake": cmake_path,\n        "ninja": ninja_path,\n    }\n\n\ndef extract_tar_to_dir(tar_path: Path, destination_dir: Path) -> None:\n    if destination_dir.exists():\n        shutil.rmtree(destination_dir)\n    destination_dir.mkdir(parents=True, exist_ok=True)\n    with tarfile.open(tar_path, mode="r:gz") as archive:\n        archive.extractall(destination_dir)\n\n\ndef _httpx_client():\n    import httpx\n\n    return httpx.Client(\n        timeout=120.0,\n        follow_redirects=True,\n        headers={\n            "Accept": "application/vnd.github+json",\n            "User-Agent": "AI-Agent-Qubitz",\n        },\n    )\n\n\ndef fetch_latest_llamacpp_release() -> dict[str, Any]:\n    if os.environ.get("QUBITZ_OFFLINE") == "1":\n        raise RuntimeError("Automatic llama.cpp runtime download is disabled because QUBITZ_OFFLINE=1.")\n    with _httpx_client() as client:\n        response = client.get(DEFAULT_LLAMACPP_RELEASE_API_URL)\n        response.raise_for_status()\n        payload = response.json()\n    if not isinstance(payload, dict):\n        raise RuntimeError("Unexpected GitHub release payload for llama.cpp.")\n    return payload\n\n\ndef parse_cuda_release_version(version_text: str) -> tuple[int, ...]:\n    return tuple(int(part) for part in version_text.split("."))\n\n\ndef select_latest_llamacpp_cuda_assets(release: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:\n    assets = release.get("assets")\n    if not isinstance(assets, list):\n        raise RuntimeError("Latest llama.cpp release did not include an assets list.")\n    main_assets: dict[str, dict[str, Any]] = {}\n    cudart_assets: dict[str, dict[str, Any]] = {}\n    for asset in assets:\n        if not isinstance(asset, dict):\n            continue\n        name = str(asset.get("name") or "")\n        main_match = WINDOWS_CUDA_RUNTIME_PATTERN.fullmatch(name)\n        if main_match is not None:\n            main_assets[main_match.group(1)] = asset\n            continue\n        cudart_match = WINDOWS_CUDART_RUNTIME_PATTERN.fullmatch(name)\n        if cudart_match is not None:\n            cudart_assets[cudart_match.group(1)] = asset\n    available_versions = sorted(set(main_assets) & set(cudart_assets), key=parse_cuda_release_version)\n    if not available_versions:\n        raise RuntimeError("Latest llama.cpp release does not expose a matching Windows CUDA 12 asset pair.")\n    selected_version = available_versions[-1]\n    return main_assets[selected_version], cudart_assets[selected_version]\n\n\ndef download_url_to_path(url: str, destination: Path) -> None:\n    destination.parent.mkdir(parents=True, exist_ok=True)\n    with _httpx_client() as client:\n        with client.stream("GET", url) as response:\n            response.raise_for_status()\n            with destination.open("wb") as handle:\n                for chunk in response.iter_bytes():\n                    if chunk:\n                        handle.write(chunk)\n\n\ndef extract_zip_to_dir(zip_path: Path, destination_dir: Path) -> None:\n    if destination_dir.exists():\n        shutil.rmtree(destination_dir)\n    destination_dir.mkdir(parents=True, exist_ok=True)\n    with zipfile.ZipFile(zip_path) as archive:\n        archive.extractall(destination_dir)\n\n\ndef normalize_archive_root(extracted_dir: Path) -> Path:\n    current = extracted_dir\n    while True:\n        children = sorted(current.iterdir())\n        file_children = [child for child in children if child.is_file()]\n        dir_children = [child for child in children if child.is_dir()]\n        if file_children or len(dir_children) != 1:\n            return current\n        current = dir_children[0]\n\n\ndef ensure_project_local_native_llamacpp_runtime(workspace: Path) -> dict[str, Any]:\n    if not in_wsl():\n        raise RuntimeError("Native WSL llama.cpp bootstrap is only available inside WSL.")\n    executable = llamacpp_native_server_executable(workspace)\n    build_log = llamacpp_native_build_log_path(workspace)\n    if executable.exists():\n        return {\n            "executable": executable.resolve(),\n            "built": False,\n            "release_tag": None,\n            "runtime_dir": llamacpp_native_runtime_dir(workspace).resolve(),\n            "build_log": build_log.resolve(),\n        }\n    if os.environ.get("QUBITZ_OFFLINE") == "1":\n        raise RuntimeError("Native WSL llama.cpp build requires online source download, but QUBITZ_OFFLINE=1.")\n    build_tools = ensure_wsl_native_build_tools(workspace)\n    for required in ("gcc", "g++", "nvcc", "git"):\n        if shutil.which(required) is None:\n            raise RuntimeError(f"Required WSL build tool is missing: {required}")\n    release = fetch_latest_llamacpp_release()\n    release_tag = str(release.get("tag_name") or "").strip() or "latest"\n    downloads_dir = workspace / ".cache" / "downloads" / "llama.cpp" / release_tag\n    tarball = downloads_dir / f"llama.cpp-{release_tag}.tar.gz"\n    if not tarball.exists():\n        download_url_to_path(\n            f"https://github.com/ggml-org/llama.cpp/archive/refs/tags/{release_tag}.tar.gz",\n            tarball,\n        )\n    tmp_root = workspace / ".cache" / "tmp" / f"llama.cpp-src-{release_tag}"\n    extract_tar_to_dir(tarball, tmp_root)\n    extracted_dirs = [item for item in tmp_root.iterdir() if item.is_dir()]\n    if not extracted_dirs:\n        raise RuntimeError(f"Failed to extract llama.cpp source archive for {release_tag}.")\n    source_dir = llamacpp_native_source_dir(workspace, release_tag)\n    if source_dir.exists():\n        shutil.rmtree(source_dir)\n    shutil.move(str(extracted_dirs[0]), str(source_dir))\n    shutil.rmtree(tmp_root, ignore_errors=True)\n    runtime_dir = llamacpp_native_runtime_dir(workspace)\n    build_dir = llamacpp_native_build_dir(workspace)\n    runtime_dir.mkdir(parents=True, exist_ok=True)\n    env = os.environ.copy()\n    path_entries = [\n        str(build_tools["cmake"].parent),\n        str(build_tools["ninja"].parent),\n        env.get("PATH", ""),\n    ]\n    env["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)\n    configure_cmd = [\n        str(build_tools["cmake"]),\n        "-S",\n        str(source_dir),\n        "-B",\n        str(build_dir),\n        "-G",\n        "Ninja",\n        "-DGGML_CUDA=ON",\n        "-DCMAKE_BUILD_TYPE=Release",\n        "-DGGML_NATIVE=ON",\n        f"-DCMAKE_CUDA_ARCHITECTURES={WSL_LLAMACPP_NATIVE_ARCH}",\n    ]\n    build_cmd = [\n        str(build_tools["cmake"]),\n        "--build",\n        str(build_dir),\n        "--config",\n        "Release",\n        "--target",\n        "llama-server",\n        "-j",\n        "8",\n    ]\n    with build_log.open("w", encoding="utf-8", errors="ignore") as handle:\n        handle.write(f"[{now_stamp()}] Building native WSL llama.cpp runtime\\n")\n        handle.write("Configure: " + " ".join(configure_cmd) + "\\n")\n        handle.write("Build: " + " ".join(build_cmd) + "\\n\\n")\n        configure = subprocess.run(\n            configure_cmd,\n            cwd=workspace,\n            env=env,\n            stdout=handle,\n            stderr=subprocess.STDOUT,\n            text=True,\n            timeout=1800,\n            check=False,\n        )\n        if configure.returncode != 0:\n            raise RuntimeError(\n                "Failed to configure native WSL llama.cpp build. Build log tail:\\n"\n                + shorten(build_log.read_text(encoding="utf-8", errors="ignore"), 4000)\n            )\n        build = subprocess.run(\n            build_cmd,\n            cwd=workspace,\n            env=env,\n            stdout=handle,\n            stderr=subprocess.STDOUT,\n            text=True,\n            timeout=7200,\n            check=False,\n        )\n    if build.returncode != 0 or not executable.exists():\n        raise RuntimeError(\n            "Failed to build native WSL llama.cpp runtime. Build log tail:\\n"\n            + shorten(build_log.read_text(encoding="utf-8", errors="ignore"), 4000)\n        )\n    return {\n        "executable": executable.resolve(),\n        "built": True,\n        "release_tag": release_tag,\n        "runtime_dir": runtime_dir.resolve(),\n        "build_log": build_log.resolve(),\n        "source_dir": source_dir.resolve(),\n    }\n\n\ndef llamacpp_runtime_dir(workspace: Path) -> Path:\n    return workspace / ".cache"\n\n\ndef llamacpp_runtime_missing_files(workspace: Path) -> list[str]:\n    runtime_dir = llamacpp_runtime_dir(workspace)\n    return [name for name in LLAMACPP_RUNTIME_REQUIRED_FILES if not (runtime_dir / name).exists()]\n\n\ndef ensure_project_local_llamacpp_runtime(workspace: Path) -> dict[str, Any]:\n    runtime_dir = llamacpp_runtime_dir(workspace)\n    missing_before = llamacpp_runtime_missing_files(workspace)\n    runtime_executable = runtime_dir / "llama-server.exe"\n    if not missing_before and runtime_executable.exists():\n        return {\n            "executable": runtime_executable.resolve(),\n            "downloaded": False,\n            "release_tag": None,\n            "runtime_dir": runtime_dir.resolve(),\n            "missing_before": [],\n        }\n    release = fetch_latest_llamacpp_release()\n    release_tag = str(release.get("tag_name") or "").strip() or "latest"\n    main_asset, cudart_asset = select_latest_llamacpp_cuda_assets(release)\n    downloads_dir = workspace / ".cache" / "downloads" / "llama.cpp" / release_tag\n    tmp_root = workspace / ".cache" / "tmp" / f"llama.cpp-{release_tag}"\n    main_zip = downloads_dir / str(main_asset["name"])\n    cudart_zip = downloads_dir / str(cudart_asset["name"])\n    if not main_zip.exists():\n        download_url_to_path(str(main_asset["browser_download_url"]), main_zip)\n    if not cudart_zip.exists():\n        download_url_to_path(str(cudart_asset["browser_download_url"]), cudart_zip)\n    main_extract = tmp_root / "main"\n    cudart_extract = tmp_root / "cudart"\n    extract_zip_to_dir(main_zip, main_extract)\n    extract_zip_to_dir(cudart_zip, cudart_extract)\n    runtime_dir.mkdir(parents=True, exist_ok=True)\n    for source_dir in (main_extract, cudart_extract):\n        archive_root = normalize_archive_root(source_dir)\n        for source in archive_root.iterdir():\n            target = runtime_dir / source.name\n            if source.is_dir():\n                if target.exists():\n                    shutil.rmtree(target)\n                shutil.copytree(source, target)\n            else:\n                shutil.copy2(source, target)\n    missing_after = llamacpp_runtime_missing_files(workspace)\n    if missing_after or not runtime_executable.exists():\n        raise RuntimeError(\n            "Automatic llama.cpp runtime download finished, but required files are still missing: "\n            + ", ".join(missing_after or ["llama-server.exe"])\n        )\n    return {\n        "executable": runtime_executable.resolve(),\n        "downloaded": True,\n        "release_tag": release_tag,\n        "runtime_dir": runtime_dir.resolve(),\n        "missing_before": missing_before,\n    }\n\n\ndef resolve_llama_server_executable(workspace: Path, configured_path: str | None = None) -> str | None:\n    configured = (configured_path or os.environ.get(LLAMACPP_SERVER_PATH_ENV_NAME) or "").strip()\n    resolved_configured: Path | None = None\n    if configured:\n        if in_wsl() and WINDOWS_DRIVE_PATH_PATTERN.match(configured):\n            drive = configured[0].lower()\n            remainder = configured[2:].replace("\\\\", "/").lstrip("/")\n            resolved_configured = Path("/mnt") / drive / remainder if remainder else Path("/mnt") / drive\n        else:\n            resolved_configured = Path(configured).expanduser()\n        if resolved_configured.exists():\n            return str(resolved_configured.resolve())\n        found = shutil.which(configured)\n        if found:\n            return found\n    if in_wsl():\n        windows_runtime_executable = llamacpp_runtime_dir(workspace) / "llama-server.exe"\n        if windows_runtime_executable.exists():\n            return str(windows_runtime_executable.resolve())\n        native_executable = llamacpp_native_server_executable(workspace)\n        if native_executable.exists():\n            return str(native_executable.resolve())\n        try:\n            ensured = ensure_project_local_llamacpp_runtime(workspace)\n        except Exception:\n            native_info = ensure_project_local_native_llamacpp_runtime(workspace)\n            return str(Path(native_info["executable"]))\n        return str(Path(ensured["executable"]))\n    ensured = ensure_project_local_llamacpp_runtime(workspace)\n    return str(Path(ensured["executable"]))\n\n\ndef is_probably_text_file(path: Path) -> bool:\n    if path.suffix.lower() in TEXT_SUFFIXES:\n        return True\n    try:\n        sample = path.read_bytes()[:2048]\n    except OSError:\n        return False\n    return b"\\x00" not in sample\n\n\ndef is_excluded_dir_name(name: str, excluded_dirs: set[str] | None = None) -> bool:\n    normalized = name.strip().lower()\n    if not normalized:\n        return False\n    if excluded_dirs is not None and normalized in excluded_dirs:\n        return True\n    return bool(VENV_DIR_PATTERN.fullmatch(normalized))\n\n\ndef path_has_excluded_dir(path: Path, excluded_dirs: set[str] | None = None) -> bool:\n    return any(is_excluded_dir_name(part, excluded_dirs) for part in path.parts)\n\n\ndef workspace_cache_key(workspace: Path) -> str:\n    normalized = workspace.resolve().as_posix().encode("utf-8")\n    return hashlib.sha256(normalized).hexdigest()[:16]\n\n\ndef is_excluded_retrieval_file(path: Path) -> bool:\n    if path.name.endswith(".bak"):\n        return True\n    if path.name in EXCLUDED_RETRIEVAL_FILENAMES:\n        return True\n    return any(path.name.endswith(suffix) for suffix in EXCLUDED_RETRIEVAL_SUFFIXES)\n\n\ndef serialize_mcp_result(result: mcp_types.CallToolResult) -> dict[str, Any]:\n    payload: dict[str, Any] = {\n        "is_error": bool(result.isError),\n        "structured_content": result.structuredContent,\n        "content": [],\n    }\n    for item in result.content:\n        if hasattr(item, "text"):\n            payload["content"].append(item.text)\n        else:\n            payload["content"].append(item.model_dump())\n    payload["content_text"] = shorten(\n        "\\n".join(part for part in payload["content"] if isinstance(part, str)),\n        MAX_TOOL_RESULT_CHARS,\n    )\n    return payload\n\n\ndef build_model_tools(tools: Sequence[mcp_types.Tool]) -> list[dict[str, Any]]:\n    model_tools: list[dict[str, Any]] = []\n    for tool in tools:\n        parameters = tool.inputSchema or {"type": "object", "properties": {}}\n        model_tools.append(\n            {\n                "type": "function",\n                "function": {\n                    "name": tool.name,\n                    "description": tool.description or "",\n                    "parameters": parameters,\n                },\n            }\n        )\n    return model_tools\n\n\ndef clean_arguments(arguments: Any) -> dict[str, Any]:\n    if arguments is None:\n        return {}\n    if isinstance(arguments, dict):\n        return arguments\n    if isinstance(arguments, str):\n        try:\n            parsed = json.loads(arguments)\n        except json.JSONDecodeError:\n            return {"raw": arguments}\n        return parsed if isinstance(parsed, dict) else {"value": parsed}\n    return {"value": arguments}\n\n\ndef default_embed_device() -> str:\n    return os.environ.get("QUBITZ_EMBED_DEVICE", "auto").lower()\n\n\ndef default_local_files_only() -> bool:\n    raw = os.environ.get("QUBITZ_ALLOW_EMBED_ONLINE", "").strip().lower()\n    if raw in {"1", "true", "yes", "on"}:\n        return False\n    if raw in {"0", "false", "no", "off"}:\n        return True\n    return os.environ.get("QUBITZ_OFFLINE", "").strip() == "1"\n\n\ndef default_retrieval_backend() -> str:\n    return env_str("QUBITZ_RETRIEVAL_BACKEND", "auto").lower()\n\n\n@dataclass\nclass AgentConfig:\n    workspace: Path\n    runtime_workspace: Path | None = None\n    model_name: str = DEFAULT_MODEL\n    model_path: str | None = None\n    selected_model_filename: str = DEFAULT_GGUF_MODEL_FILENAME\n    server_url: str = DEFAULT_LLAMACPP_BASE_URL\n    llama_server_path: str | None = None\n    embed_model_name: str = DEFAULT_EMBED_MODEL\n    max_steps: int = MAX_TOOL_STEPS\n    num_ctx: int = DEFAULT_NUM_CTX\n    num_predict: int = DEFAULT_NUM_PREDICT\n    temperature: float = DEFAULT_CHAT_TEMPERATURE\n    top_p: float = DEFAULT_CHAT_TOP_P\n    min_p: float = DEFAULT_CHAT_MIN_P\n    repeat_penalty: float = DEFAULT_REPEAT_PENALTY\n    local_files_only: bool = field(default_factory=default_local_files_only)\n    embed_device: str = field(default_factory=default_embed_device)\n    min_repo_chunks: int = field(default_factory=lambda: env_int("QUBITZ_MIN_REPO_CHUNKS", 4))\n    max_repo_chunks: int = field(default_factory=lambda: env_int("QUBITZ_MAX_REPO_CHUNKS", 24))\n    repo_chunk_ctx_tokens: int = field(default_factory=lambda: env_int("QUBITZ_REPO_CHUNK_CTX_TOKENS", 16384))\n    embed_min_free_vram_gib: float = field(default_factory=lambda: env_float("QUBITZ_EMBED_MIN_FREE_VRAM_GIB", 0.0))\n    retrieval_gpu_reserve_gib: float = field(default_factory=lambda: env_float("QUBITZ_RETRIEVAL_GPU_RESERVE_GIB", 1.0))\n    retrieval_backend: str = field(default_factory=default_retrieval_backend)\n    retrieval_ivf_nlist: int = field(default_factory=lambda: env_int("QUBITZ_RETRIEVAL_IVF_NLIST", 256))\n    retrieval_ivf_nprobe: int = field(default_factory=lambda: env_int("QUBITZ_RETRIEVAL_IVF_NPROBE", 16))\n    retrieval_ivfpq_m: int = field(default_factory=lambda: env_int("QUBITZ_RETRIEVAL_IVFPQ_M", 16))\n    retrieval_ivfpq_bits: int = field(default_factory=lambda: env_int("QUBITZ_RETRIEVAL_IVFPQ_BITS", 8))\n    retrieval_cagra_graph_degree: int = field(\n        default_factory=lambda: env_int("QUBITZ_RETRIEVAL_CAGRA_GRAPH_DEGREE", 64)\n    )\n    retrieval_cagra_intermediate_graph_degree: int = field(\n        default_factory=lambda: env_int("QUBITZ_RETRIEVAL_CAGRA_INTERMEDIATE_DEGREE", 128)\n    )\n    retrieval_cagra_search_width: int = field(\n        default_factory=lambda: env_int("QUBITZ_RETRIEVAL_CAGRA_SEARCH_WIDTH", 32)\n    )\n    retrieval_rmm_pool_gib: float = field(default_factory=lambda: env_float("QUBITZ_RETRIEVAL_RMM_POOL_GIB", 0.0))\n\n\ndef config_runtime_workspace(config: AgentConfig) -> Path:\n    return (config.runtime_workspace or config.workspace).resolve()\n\n\ndef strip_yaml_scalar(value: str) -> str:\n    cleaned = value.strip()\n    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"\'", \'"\'}:\n        return cleaned[1:-1]\n    return cleaned\n\n\ndef parse_skill_frontmatter(frontmatter: str) -> dict[str, Any]:\n    parsed: dict[str, Any] = {}\n    current_map_key: str | None = None\n    for raw_line in frontmatter.splitlines():\n        if not raw_line.strip() or raw_line.lstrip().startswith("#"):\n            continue\n        if raw_line[:1].isspace():\n            if current_map_key is None or not isinstance(parsed.get(current_map_key), dict):\n                continue\n            nested = raw_line.strip()\n            key, separator, value = nested.partition(":")\n            if not separator:\n                raise ValueError(f"Invalid nested frontmatter line: {raw_line}")\n            parsed[current_map_key][key.strip()] = strip_yaml_scalar(value)\n            continue\n        key, separator, value = raw_line.partition(":")\n        if not separator:\n            raise ValueError(f"Invalid frontmatter line: {raw_line}")\n        normalized_key = key.strip()\n        cleaned_value = value.strip()\n        if normalized_key == "metadata":\n            parsed[normalized_key] = {}\n            current_map_key = normalized_key\n            if cleaned_value:\n                raise ValueError("metadata must be a mapping block, not an inline scalar.")\n            continue\n        parsed[normalized_key] = strip_yaml_scalar(cleaned_value)\n        current_map_key = None\n    return parsed\n\n\n@dataclass\nclass SkillDefinition:\n    name: str\n    description: str\n    root: Path\n    skill_file: Path\n    body: str\n    license: str = ""\n    compatibility: str = ""\n    metadata: dict[str, str] = field(default_factory=dict)\n    allowed_tools: str = ""\n\n    def to_summary(self, workspace: Path) -> dict[str, Any]:\n        resource_paths: list[str] = []\n        for folder in ("scripts", "references", "assets"):\n            base = self.root / folder\n            if not base.exists():\n                continue\n            for candidate in sorted(base.rglob("*")):\n                if candidate.is_file():\n                    resource_paths.append(relative_path(candidate, workspace))\n        return {\n            "name": self.name,\n            "description": self.description,\n            "license": self.license or None,\n            "compatibility": self.compatibility or None,\n            "allowed_tools": self.allowed_tools or None,\n            "metadata": self.metadata,\n            "root": relative_path(self.root, workspace),\n            "skill_file": relative_path(self.skill_file, workspace),\n            "resources": resource_paths,\n        }\n\n\nclass SkillRegistry:\n    def __init__(self, workspace: Path) -> None:\n        self.workspace = workspace\n        self.skills_root = workspace / ".skills"\n        self.skills: dict[str, SkillDefinition] = {}\n        self.warnings: list[str] = []\n        self.reload()\n\n    def reload(self) -> None:\n        self.skills = {}\n        self.warnings = []\n        if not self.skills_root.exists():\n            return\n        for skill_dir in sorted(self.skills_root.iterdir()):\n            if not skill_dir.is_dir():\n                continue\n            skill_file = skill_dir / "SKILL.md"\n            if not skill_file.exists():\n                continue\n            try:\n                skill = self._load_skill(skill_dir, skill_file)\n            except Exception as exc:\n                self.warnings.append(f"{relative_path(skill_file, self.workspace)}: {type(exc).__name__}: {exc}")\n                continue\n            self.skills[skill.name] = skill\n\n    def _load_skill(self, skill_dir: Path, skill_file: Path) -> SkillDefinition:\n        text = skill_file.read_text(encoding="utf-8", errors="ignore")\n        if not text.startswith("---\\n") and text != "---":\n            raise ValueError("SKILL.md must start with YAML frontmatter delimited by ---")\n        lines = text.splitlines()\n        closing_index: int | None = None\n        for index in range(1, len(lines)):\n            if lines[index].strip() == "---":\n                closing_index = index\n                break\n        if closing_index is None:\n            raise ValueError("SKILL.md frontmatter is missing a closing --- delimiter")\n        frontmatter = "\\n".join(lines[1:closing_index])\n        body = "\\n".join(lines[closing_index + 1 :]).strip()\n        parsed = parse_skill_frontmatter(frontmatter)\n        name = str(parsed.get("name", "")).strip()\n        description = str(parsed.get("description", "")).strip()\n        license_value = str(parsed.get("license", "")).strip()\n        compatibility = str(parsed.get("compatibility", "")).strip()\n        allowed_tools = str(parsed.get("allowed-tools", "")).strip()\n        metadata = parsed.get("metadata", {})\n        if not isinstance(metadata, dict):\n            raise ValueError("metadata must be a mapping")\n        if not name or len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):\n            raise ValueError("name must match the Agent Skills lowercase-hyphen naming rules")\n        if name != skill_dir.name:\n            raise ValueError("name must match the parent skill directory name")\n        if not description or len(description) > 1024:\n            raise ValueError("description must be 1-1024 characters")\n        if compatibility and len(compatibility) > 500:\n            raise ValueError("compatibility must be 500 characters or fewer")\n        normalized_metadata = {str(key): str(value) for key, value in metadata.items()}\n        return SkillDefinition(\n            name=name,\n            description=description,\n            root=skill_dir,\n            skill_file=skill_file,\n            body=body,\n            license=license_value,\n            compatibility=compatibility,\n            metadata=normalized_metadata,\n            allowed_tools=allowed_tools,\n        )\n\n    def count(self) -> int:\n        return len(self.skills)\n\n    def list_summaries(self) -> list[dict[str, Any]]:\n        return [skill.to_summary(self.workspace) for skill in self.skills.values()]\n\n    def get(self, skill_name: str) -> SkillDefinition:\n        normalized = skill_name.strip().lower()\n        if normalized in self.skills:\n            return self.skills[normalized]\n        raise KeyError(skill_name)\n\n    def _resolve_skill_resource(self, skill_name: str, resource_path: str) -> tuple[SkillDefinition, Path]:\n        skill = self.get(skill_name)\n        target = (skill.root / Path(resource_path)).resolve()\n        if not target.is_relative_to(skill.root.resolve()):\n            raise ValueError("Skill resource path escapes the skill root")\n        if not target.exists():\n            raise FileNotFoundError(resource_path)\n        return skill, target\n\n    def read_skill_resource(self, skill_name: str, resource_path: str) -> dict[str, Any]:\n        skill, target = self._resolve_skill_resource(skill_name, resource_path)\n        if target.is_dir():\n            entries = [relative_path(candidate, self.workspace) for candidate in sorted(target.iterdir())]\n            return {\n                "skill": skill.name,\n                "path": relative_path(target, self.workspace),\n                "is_dir": True,\n                "entries": entries,\n            }\n        if not is_probably_text_file(target):\n            return {\n                "skill": skill.name,\n                "path": relative_path(target, self.workspace),\n                "is_dir": False,\n                "binary": True,\n                "size": target.stat().st_size,\n            }\n        text = target.read_text(encoding="utf-8", errors="ignore")\n        return {\n            "skill": skill.name,\n            "path": relative_path(target, self.workspace),\n            "is_dir": False,\n            "binary": False,\n            "content": shorten(text, 12000),\n        }\n\n    def select_for_prompt(self, prompt: str, max_results: int = 3) -> list[SkillDefinition]:\n        prompt_lower = prompt.lower()\n        prompt_tokens = token_set(prompt)\n        ranked: list[tuple[int, str, SkillDefinition]] = []\n        for skill in self.skills.values():\n            score = 0\n            aliases = {\n                skill.name,\n                skill.name.replace("-", " "),\n                skill.name.replace("-", "_"),\n            }\n            if any(alias and alias in prompt_lower for alias in aliases):\n                score += 50\n            skill_tokens = token_set(f"{skill.name.replace(\'-\', \' \')} {skill.description}")\n            overlap = prompt_tokens & skill_tokens\n            score += len(overlap) * 3\n            if skill.compatibility:\n                score += len(prompt_tokens & token_set(skill.compatibility))\n            if score <= 0:\n                continue\n            ranked.append((score, skill.name, skill))\n        ranked.sort(key=lambda item: (-item[0], item[1]))\n        return [skill for _, _, skill in ranked[:max_results]]\n\n    def render_active_context(self, active_skills: Sequence[SkillDefinition]) -> str:\n        if not active_skills:\n            return "None"\n        sections: list[str] = []\n        for skill in active_skills:\n            sections.append(\n                textwrap.dedent(\n                    f"""\n                    Skill: {skill.name}\n                    Description: {skill.description}\n                    Compatibility: {skill.compatibility or "None"}\n                    Allowed tools: {skill.allowed_tools or "None"}\n                    Root: {relative_path(skill.root, self.workspace)}\n\n                    Instructions:\n                    {skill.body or "No body content provided."}\n                    """\n                ).strip()\n            )\n        return "\\n\\n".join(sections)\n\nclass MemoryStore:\n    def __init__(self, workspace: Path) -> None:\n        self.workspace = workspace\n        self.memory_dir = workspace / ".memory"\n        self.memory_dir.mkdir(parents=True, exist_ok=True)\n        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")\n        self.current_path = self.memory_dir / CURRENT_MEMORY_NAME\n        self.archive_path = self.memory_dir / f"{ARCHIVE_MEMORY_PREFIX}{self.session_id}.md"\n        self.notes: list[dict[str, str]] = []\n        self.turns: list[dict[str, str]] = []\n        self.flush()\n\n    def add_turn(self, role: str, content: str) -> None:\n        self.turns.append(\n            {\n                "timestamp": now_stamp(),\n                "role": role,\n                "content": shorten(content.strip(), 4000),\n            }\n        )\n        self.turns = self.turns[-20:]\n        self.flush()\n\n    def add_note(self, note: str, category: str = "note") -> None:\n        cleaned = note.strip()\n        if not cleaned:\n            return\n        self.notes.append({"timestamp": now_stamp(), "category": category, "text": shorten(cleaned, 1000)})\n        self.notes = self.notes[-40:]\n        self.flush()\n\n    def render(self) -> str:\n        lines = [\n            "# Qubitz Memory",\n            "",\n            f"Session ID: {self.session_id}",\n            f"Updated: {now_stamp()}",\n            f"Workspace: {self.workspace.as_posix()}",\n            "",\n            "## Notes",\n        ]\n        if self.notes:\n            for note in self.notes[-20:]:\n                lines.append(f"- [{note[\'timestamp\']}] ({note[\'category\']}) {note[\'text\']}")\n        else:\n            lines.append("- No notes recorded yet.")\n        lines.extend(["", "## Recent Turns"])\n        if self.turns:\n            for turn in self.turns[-12:]:\n                lines.append(f"### {turn[\'role\'].title()} [{turn[\'timestamp\']}]")\n                lines.append(turn["content"])\n                lines.append("")\n        else:\n            lines.append("No conversation turns recorded yet.")\n        return "\\n".join(lines).rstrip() + "\\n"\n\n    def flush(self) -> None:\n        rendered = self.render()\n        self.current_path.write_text(rendered, encoding="utf-8")\n        self.archive_path.write_text(rendered, encoding="utf-8")\n\n    def search(self, query: str, limit: int = 5, include_current: bool = True) -> list[dict[str, str]]:\n        query_tokens = token_set(query)\n        results: list[dict[str, str]] = []\n        memory_files = sorted(\n            self.memory_dir.glob(f"{ARCHIVE_MEMORY_PREFIX}*.md"),\n            key=lambda item: item.stat().st_mtime,\n            reverse=True,\n        )\n        if include_current and self.current_path.exists():\n            memory_files.insert(0, self.current_path)\n        seen: set[Path] = set()\n        for path in memory_files:\n            if path in seen or not path.exists():\n                continue\n            seen.add(path)\n            text = path.read_text(encoding="utf-8", errors="ignore")\n            lowered = text.lower()\n            if query_tokens:\n                score = sum(lowered.count(token) for token in query_tokens)\n            else:\n                score = 1\n            if score <= 0:\n                continue\n            results.append(\n                {\n                    "path": relative_path(path, self.workspace),\n                    "score": str(score),\n                    "snippet": summarize_memory_markdown(text, MAX_MEMORY_RESULT_SNIPPET_CHARS),\n                }\n            )\n        return results[:limit]\n\n    def build_context(self, query: str) -> str:\n        current = self.current_path.read_text(encoding="utf-8", errors="ignore") if self.current_path.exists() else ""\n        archived = self.search(query, limit=MAX_MEMORY_CONTEXT_RESULTS, include_current=False)\n        current_summary = summarize_memory_markdown(current, MAX_MEMORY_CONTEXT_CHARS)\n        sections = ["Current session memory:", current_summary or "None"]\n        if archived:\n            sections.append("Relevant archived memory:")\n            for item in archived:\n                sections.append(f"- {item[\'path\']} (score {item[\'score\']}): {item[\'snippet\']}")\n        return "\\n\\n".join(section for section in sections if section.strip())\n\n\n@dataclass\nclass RepoChunk:\n    chunk_id: str\n    path: str\n    start_line: int\n    end_line: int\n    text: str\n\n\nclass BGECodeEmbedder:\n    def __init__(\n        self,\n        workspace: Path,\n        model_name: str,\n        *,\n        device: str = "auto",\n        local_files_only: bool = False,\n        min_free_vram_gib: float = 4.0,\n        reserve_vram_gib: float = 1.0,\n        progress_callback: Callable[[str], None] | None = None,\n    ) -> None:\n        self.workspace = workspace\n        self.model_name = model_name\n        self.requested_device = device.lower()\n        self.min_free_vram_bytes = gib_to_bytes(min_free_vram_gib)\n        self.reserve_vram_bytes = gib_to_bytes(reserve_vram_gib)\n        self.device = "cpu"\n        self.local_files_only = local_files_only\n        self.progress_callback = progress_callback\n        self.hf_cache_dir = self.workspace / ".cache" / "huggingface"\n        self._loaded = False\n        self._released_from_gpu = False\n        self._torch: Any = None\n        self._torch_f: Any = None\n        self._tokenizer: Any = None\n        self._model: Any = None\n        self.uses_flash_attention = False\n        self.uses_xformers = False\n        self.load_warning: str | None = None\n\n    def _report(self, message: str) -> None:\n        if self.progress_callback is not None:\n            self.progress_callback(message)\n\n    @staticmethod\n    def _cuda_memory_snapshot(torch: Any) -> tuple[int | None, int | None]:\n        if not torch.cuda.is_available():\n            return None, None\n        try:\n            return torch.cuda.mem_get_info()\n        except Exception:\n            return None, None\n\n    def _resolve_device(self, torch: Any) -> str:\n        if self.requested_device == "auto":\n            if not torch.cuda.is_available():\n                return "cpu"\n            free_bytes, _ = self._cuda_memory_snapshot(torch)\n            if self.min_free_vram_bytes > 0 and free_bytes is not None and free_bytes < self.min_free_vram_bytes:\n                self.load_warning = (\n                    f"Free CUDA VRAM {format_bytes(free_bytes)} is below the embedder threshold "\n                    f"{format_bytes(self.min_free_vram_bytes)}; using CPU fallback."\n                )\n                self._report(self.load_warning)\n                return "cpu"\n            return "cuda"\n        return self.requested_device\n\n    @staticmethod\n    def _has_flash_attention() -> bool:\n        try:\n            import flash_attn  # noqa: F401\n        except Exception:\n            return False\n        return True\n\n    @staticmethod\n    def _has_xformers() -> bool:\n        try:\n            import xformers  # noqa: F401\n        except Exception:\n            return False\n        return True\n\n    def _load_model(self, auto_model_cls: Any, load_kwargs: dict[str, Any]) -> Any:\n        return auto_model_cls.from_pretrained(self.model_name, **load_kwargs)\n\n    def _download_missing_embedder_files(self) -> None:\n        cache_dir = str(self.hf_cache_dir)\n        cache_rel = relative_path(self.hf_cache_dir, self.workspace)\n        self._report(\n            f"Missing local Hugging Face files for embedder {self.model_name}. Downloading them into {cache_rel}."\n        )\n        try:\n            from huggingface_hub import snapshot_download\n        except Exception as exc:\n            raise RuntimeError(\n                "Automatic embedder download requires the \'huggingface_hub\' package in the active runtime."\n            ) from exc\n        try:\n            snapshot_download(\n                repo_id=self.model_name,\n                cache_dir=cache_dir,\n                local_files_only=False,\n            )\n        except Exception as exc:\n            raise RuntimeError(\n                f"Automatic download of embedder {self.model_name} failed: {type(exc).__name__}: {exc}"\n            ) from exc\n        self._report(f"Downloaded embedder snapshot for {self.model_name} into {cache_rel}.")\n\n    def _enable_optional_attention_kernels(self) -> None:\n        if self.device != "cuda":\n            return\n        if self.uses_flash_attention:\n            return\n        if self._has_xformers() and hasattr(self._model, "enable_xformers_memory_efficient_attention"):\n            try:\n                self._model.enable_xformers_memory_efficient_attention()\n                self.uses_xformers = True\n                self._report("xFormers memory-efficient attention enabled for the embedder.")\n            except Exception as exc:\n                self._report(f"xFormers was detected but not enabled: {type(exc).__name__}: {exc}")\n\n    def _cuda_batch_size(self, text_count: int) -> int:\n        if self.device != "cuda":\n            return 2\n        torch = self._torch\n        free_bytes, _ = self._cuda_memory_snapshot(torch)\n        if free_bytes is None:\n            return 1\n        free_bytes = max(0, free_bytes - self.reserve_vram_bytes)\n        if text_count <= 1:\n            return 1\n        if free_bytes >= 12 * 1024**3:\n            return 4\n        if free_bytes >= 7 * 1024**3:\n            return 2\n        return 1\n\n    def _report_cuda_device(self) -> None:\n        try:\n            props = self._torch.cuda.get_device_properties(0)\n            self._report(\n                f"Embedder active on CUDA device 0: {props.name} with {format_bytes(props.total_memory)} total VRAM."\n            )\n        except Exception:\n            self._report("Embedder active on CUDA device 0.")\n\n    def _activate_cuda_model(self) -> None:\n        assert self._torch is not None\n        assert self._model is not None\n        self._model.to("cuda")\n        self.device = "cuda"\n        self._released_from_gpu = False\n        self.load_warning = None\n        self._torch.backends.cuda.matmul.allow_tf32 = True\n        self._torch.backends.cudnn.allow_tf32 = True\n        self._enable_optional_attention_kernels()\n        self._report_cuda_device()\n\n    def load(self) -> None:\n        if self._loaded and not self._released_from_gpu:\n            return\n        if self._loaded and self._released_from_gpu:\n            assert self._torch is not None\n            target_device = self._resolve_device(self._torch)\n            if target_device != "cuda":\n                self._report("Keeping the embedder on CPU for this retrieval to preserve GPU headroom.")\n                self.device = "cpu"\n                return\n            try:\n                self._report("Restoring the embedder to CUDA for retrieval.")\n                self._activate_cuda_model()\n                return\n            except Exception as exc:\n                self.load_warning = f"CUDA embedder restore failed, keeping CPU copy: {type(exc).__name__}: {exc}"\n                self._report(self.load_warning)\n                self.device = "cpu"\n                return\n        configure_project_environment(self.workspace)\n        import torch\n        import torch.nn.functional as torch_f\n        from transformers import AutoModel, AutoTokenizer\n\n        self._torch = torch\n        self._torch_f = torch_f\n        tokenizer_load_kwargs: dict[str, Any] = {\n            "trust_remote_code": True,\n            "local_files_only": self.local_files_only,\n            "cache_dir": str(self.hf_cache_dir),\n        }\n        try:\n            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **tokenizer_load_kwargs)\n        except Exception:\n            if not self.local_files_only:\n                raise\n            try:\n                self._download_missing_embedder_files()\n                self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **tokenizer_load_kwargs)\n            except Exception as download_exc:\n                raise RuntimeError(\n                    "Automatic embedder download failed after required local Hugging Face files were not found."\n                ) from download_exc\n        base_load_kwargs: dict[str, Any] = {\n            "trust_remote_code": True,\n            "local_files_only": self.local_files_only,\n            "low_cpu_mem_usage": True,\n            "cache_dir": str(self.hf_cache_dir),\n        }\n        target_device = self._resolve_device(torch)\n        cache_mode = "local cache only" if self.local_files_only else "network allowed"\n        self._report(f"Loading embedder {self.model_name} on {target_device} ({cache_mode}).")\n        load_kwargs = dict(base_load_kwargs)\n        if target_device == "cuda":\n            load_kwargs["dtype"] = torch.float16\n            if self._has_flash_attention():\n                load_kwargs["attn_implementation"] = "flash_attention_2"\n                self._report("FlashAttention2 is available for the embedder.")\n        try:\n            self._model = self._load_model(AutoModel, load_kwargs)\n            self.device = target_device\n        except Exception as exc:\n            if target_device != "cuda":\n                raise\n            self.load_warning = f"CUDA embedder load failed, falling back to CPU: {type(exc).__name__}: {exc}"\n            self._report(self.load_warning)\n            if torch.cuda.is_available():\n                torch.cuda.empty_cache()\n            self._model = self._load_model(AutoModel, base_load_kwargs)\n            self.device = "cpu"\n            load_kwargs = dict(base_load_kwargs)\n        self._model.eval()\n        if self.device == "cuda":\n            self._activate_cuda_model()\n        else:\n            self._report("Embedder active on CPU.")\n        self.uses_flash_attention = load_kwargs.get("attn_implementation") == "flash_attention_2"\n        self._loaded = True\n\n    def release_gpu(self) -> bool:\n        if not self._loaded or self._model is None or self.device != "cuda":\n            return False\n        self._report(\n            f"Releasing embedder GPU memory before generation and preserving {format_bytes(self.reserve_vram_bytes)} of headroom."\n        )\n        try:\n            self._model.to("cpu")\n        except Exception:\n            self._model = None\n            self._loaded = False\n        self.device = "cpu"\n        self._released_from_gpu = True\n        torch = self._torch\n        if torch is not None and torch.cuda.is_available():\n            gc.collect()\n            try:\n                torch.cuda.empty_cache()\n            except Exception:\n                pass\n            try:\n                torch.cuda.ipc_collect()\n            except Exception:\n                pass\n            free_bytes, total_bytes = self._cuda_memory_snapshot(torch)\n            if free_bytes is not None and total_bytes is not None:\n                self._report(\n                    f"Embedder GPU memory released. CUDA free VRAM now {format_bytes(free_bytes)} of {format_bytes(total_bytes)}."\n                )\n            else:\n                self._report("Embedder GPU memory released.")\n        return True\n\n    def _last_token_pool(self, last_hidden_states: Any, attention_mask: Any) -> Any:\n        torch = self._torch\n        left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]\n        if bool(left_padding):\n            return last_hidden_states[:, -1]\n        sequence_lengths = attention_mask.sum(dim=1) - 1\n        batch_size = last_hidden_states.shape[0]\n        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]\n\n    def _encode(self, texts: Sequence[str], *, prompt: str | None = None) -> Any:\n        self.load()\n        import numpy as np\n\n        torch = self._torch\n        encoded_batches: list[Any] = []\n        batch_size = self._cuda_batch_size(len(texts))\n        kind = "query" if prompt else "document"\n        self._report(f"Encoding {len(texts)} {kind} chunk(s) on {self.device} with batch size {batch_size}.")\n        for start in range(0, len(texts), batch_size):\n            batch = list(texts[start : start + batch_size])\n            if prompt:\n                batch = [f"{prompt}{text}" for text in batch]\n            tokenize_kwargs: dict[str, Any] = {\n                "max_length": 2048,\n                "padding": True,\n                "return_tensors": "pt",\n                "truncation": True,\n            }\n            if self.device == "cuda":\n                tokenize_kwargs["pad_to_multiple_of"] = 8\n            batch_dict = self._tokenizer(batch, **tokenize_kwargs)\n            batch_dict = {\n                key: value.to(self.device) if hasattr(value, "to") else value\n                for key, value in batch_dict.items()\n            }\n            with torch.inference_mode():\n                outputs = self._model(**batch_dict)\n                embeddings = self._last_token_pool(outputs.last_hidden_state, batch_dict["attention_mask"])\n                embeddings = self._torch_f.normalize(embeddings, p=2, dim=1)\n            encoded_batches.append(embeddings.detach().cpu().float().numpy())\n        return np.vstack(encoded_batches) if encoded_batches else np.empty((0, 0), dtype="float32")\n\n    def encode_queries(self, texts: Sequence[str]) -> Any:\n        prompt = f"<instruct>{QUERY_INSTRUCTION}\\n<query>"\n        return self._encode(texts, prompt=prompt)\n\n    def encode_documents(self, texts: Sequence[str]) -> Any:\n        return self._encode(texts)\n\n\nclass RepoRetriever:\n    def __init__(self, config: AgentConfig, progress_callback: Callable[[str], None] | None = None) -> None:\n        self.config = config\n        self.workspace = config.workspace.resolve()\n        self.runtime_workspace = config_runtime_workspace(config)\n        self.progress_callback = progress_callback\n        self.cache_dir = self.runtime_workspace / ".cache" / "retrieval" / workspace_cache_key(self.workspace)\n        self.cache_dir.mkdir(parents=True, exist_ok=True)\n        self.manifest_path = self.cache_dir / "manifest.json"\n        self.vectors_path = self.cache_dir / "vectors.npy"\n        self.embedder = BGECodeEmbedder(\n            self.runtime_workspace,\n            config.embed_model_name,\n            device=config.embed_device,\n            local_files_only=config.local_files_only,\n            min_free_vram_gib=config.embed_min_free_vram_gib,\n            reserve_vram_gib=config.retrieval_gpu_reserve_gib,\n            progress_callback=self._report,\n        )\n        self._chunks: list[RepoChunk] = []\n        self._vectors: Any = None\n        self._backend = "lexical"\n        self._last_error: str | None = None\n        self._faiss: Any = None\n        self._faiss_gpu_resources: Any = None\n        self._faiss_index: Any = None\n        self._faiss_search_params: Any = None\n        self._rmm_pool: Any = None\n        self._rmm_configured = False\n        self._rmm_attempted = False\n\n    def _report(self, message: str) -> None:\n        if self.progress_callback is not None:\n            self.progress_callback(message)\n\n    @staticmethod\n    def _normalize_backend_name(name: str) -> str:\n        normalized = name.strip().lower().replace("-", "_")\n        aliases = {\n            "": "auto",\n            "flat_cuvs": "flat",\n            "gpu_flat": "flat",\n            "ivfflat": "ivf_flat",\n            "ivf": "ivf_flat",\n            "ivfpq": "ivf_pq",\n            "pq": "ivf_pq",\n            "ann": "cagra",\n        }\n        return aliases.get(normalized, normalized)\n\n    def _effective_nlist(self) -> int:\n        vector_count = int(self._vectors.shape[0]) if self._vectors is not None else 0\n        if vector_count <= 1:\n            return 1\n        suggested = max(1, int(vector_count**0.5))\n        return max(1, min(self.config.retrieval_ivf_nlist, suggested, vector_count))\n\n    def _effective_nprobe(self, nlist: int) -> int:\n        return max(1, min(self.config.retrieval_ivf_nprobe, nlist))\n\n    def _effective_pq_m(self, dimension: int) -> int:\n        requested = max(1, min(self.config.retrieval_ivfpq_m, dimension))\n        for candidate in range(requested, 0, -1):\n            if dimension % candidate == 0:\n                return candidate\n        return 1\n\n    def recommended_result_limit(self) -> int:\n        upper = max(1, self.config.max_repo_chunks)\n        lower = max(1, min(self.config.min_repo_chunks, upper))\n        tokens_per_chunk = max(1024, self.config.repo_chunk_ctx_tokens)\n        adaptive = max(1, self.config.num_ctx // tokens_per_chunk)\n        return max(lower, min(upper, adaptive))\n\n    def _configure_rmm_pool(self) -> None:\n        if self._rmm_attempted or self.config.retrieval_rmm_pool_gib <= 0:\n            return\n        self._rmm_attempted = True\n        try:\n            import rmm\n        except Exception as exc:  # pragma: no cover - optional dependency path\n            self._report(\n                "RMM pooling was requested but the rmm-cu12 package is unavailable: "\n                f"{type(exc).__name__}: {exc}"\n            )\n            return\n        pool_bytes = int(self.config.retrieval_rmm_pool_gib * 1024**3)\n        if pool_bytes <= 0:\n            return\n        try:\n            pool = rmm.mr.PoolMemoryResource(\n                rmm.mr.CudaMemoryResource(),\n                initial_pool_size=pool_bytes,\n            )\n            try:\n                rmm.mr.set_per_device_resource(0, pool)\n            except TypeError:\n                rmm.mr.set_per_device_resource(pool)\n            self._rmm_pool = pool\n            self._rmm_configured = True\n            self._report(\n                "Enabled RMM pooling for cuVS-backed retrieval with initial pool size "\n                f"{format_bytes(pool_bytes)}."\n            )\n        except Exception as exc:  # pragma: no cover - optional dependency path\n            self._report(f"RMM pool setup failed; continuing without pooling: {type(exc).__name__}: {exc}")\n\n    def _new_gpu_resources(self, faiss: Any) -> Any:\n        self._configure_rmm_pool()\n        return faiss.StandardGpuResources()\n\n    def _build_gpu_flat_index(self, faiss: Any) -> tuple[Any, str, Any]:\n        cpu_index = faiss.IndexFlatIP(int(self._vectors.shape[1]))\n        cpu_index.add(self._vectors)\n        co = faiss.GpuClonerOptions()\n        backend = "faiss-gpu-flat"\n        if hasattr(co, "use_cuvs"):\n            co.use_cuvs = True\n            backend = "faiss-gpu-flat-cuvs"\n        return faiss.index_cpu_to_gpu(self._faiss_gpu_resources, 0, cpu_index, co), backend, None\n\n    def _build_gpu_ivf_flat_index(self, faiss: Any) -> tuple[Any, str, Any]:\n        dimension = int(self._vectors.shape[1])\n        nlist = self._effective_nlist()\n        nprobe = self._effective_nprobe(nlist)\n        config = faiss.GpuIndexIVFFlatConfig()\n        config.use_cuvs = True\n        index = faiss.GpuIndexIVFFlat(\n            self._faiss_gpu_resources,\n            dimension,\n            nlist,\n            faiss.METRIC_INNER_PRODUCT,\n            config,\n        )\n        index.train(self._vectors)\n        index.add(self._vectors)\n        index.nprobe = nprobe\n        backend = f"faiss-gpu-ivf-flat-cuvs(nlist={nlist},nprobe={nprobe})"\n        return index, backend, None\n\n    def _build_gpu_ivf_pq_index(self, faiss: Any) -> tuple[Any, str, Any]:\n        dimension = int(self._vectors.shape[1])\n        nlist = self._effective_nlist()\n        nprobe = self._effective_nprobe(nlist)\n        pq_m = self._effective_pq_m(dimension)\n        pq_bits = max(4, min(16, self.config.retrieval_ivfpq_bits))\n        config = faiss.GpuIndexIVFPQConfig()\n        config.use_cuvs = True\n        index = faiss.GpuIndexIVFPQ(\n            self._faiss_gpu_resources,\n            dimension,\n            nlist,\n            pq_m,\n            pq_bits,\n            faiss.METRIC_INNER_PRODUCT,\n            config,\n        )\n        index.train(self._vectors)\n        index.add(self._vectors)\n        index.nprobe = nprobe\n        backend = f"faiss-gpu-ivf-pq-cuvs(nlist={nlist},nprobe={nprobe},m={pq_m},bits={pq_bits})"\n        return index, backend, None\n\n    def _build_gpu_cagra_index(self, faiss: Any) -> tuple[Any, str, Any]:\n        dimension = int(self._vectors.shape[1])\n        config = faiss.GpuIndexCagraConfig()\n        config.use_cuvs = True\n        config.graph_degree = max(1, self.config.retrieval_cagra_graph_degree)\n        config.intermediate_graph_degree = max(\n            config.graph_degree,\n            self.config.retrieval_cagra_intermediate_graph_degree,\n        )\n        index = faiss.GpuIndexCagra(\n            self._faiss_gpu_resources,\n            dimension,\n            faiss.METRIC_INNER_PRODUCT,\n            config,\n        )\n        index.train(self._vectors)\n        params = faiss.SearchParametersCagra()\n        params.search_width = max(1, self.config.retrieval_cagra_search_width)\n        backend = (\n            "faiss-gpu-cagra("\n            f"graph_degree={config.graph_degree},"\n            f"intermediate_degree={config.intermediate_graph_degree},"\n            f"search_width={params.search_width})"\n        )\n        return index, backend, params\n\n    def _iter_files(self) -> Iterable[Path]:\n        for path in iter_workspace_files(self.workspace, EXCLUDED_DIRS):\n            if is_excluded_retrieval_file(path):\n                continue\n            if not is_probably_text_file(path):\n                continue\n            yield path\n\n    def _chunk_file(self, path: Path) -> list[RepoChunk]:\n        text = path.read_text(encoding="utf-8", errors="ignore")\n        lines = text.splitlines()\n        if not lines:\n            return []\n        chunks: list[RepoChunk] = []\n        chunk_size = 80\n        overlap = 20\n        start = 0\n        rel = relative_path(path, self.workspace)\n        while start < len(lines):\n            end = min(len(lines), start + chunk_size)\n            chunk_lines = lines[start:end]\n            chunk_text = "\\n".join(chunk_lines).strip()\n            if chunk_text:\n                chunk_id = f"{rel}:{start + 1}-{end}"\n                chunks.append(\n                    RepoChunk(\n                        chunk_id=chunk_id,\n                        path=rel,\n                        start_line=start + 1,\n                        end_line=end,\n                        text=chunk_text,\n                    )\n                )\n            if end >= len(lines):\n                break\n            start = max(end - overlap, start + 1)\n        return chunks\n\n    def _file_state(self) -> dict[str, dict[str, int]]:\n        state: dict[str, dict[str, int]] = {}\n        for path in self._iter_files():\n            stat = path.stat()\n            state[relative_path(path, self.workspace)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}\n        return state\n\n    def _build_chunks(self) -> list[RepoChunk]:\n        chunks: list[RepoChunk] = []\n        files = sorted(self._iter_files())\n        self._report(f"Scanning {len(files)} workspace file(s) for retrieval chunks.")\n        for path in files:\n            chunks.extend(self._chunk_file(path))\n        self._report(f"Built {len(chunks)} retrieval chunk(s).")\n        return chunks\n\n    def _load_manifest(self) -> dict[str, Any] | None:\n        if not self.manifest_path.exists():\n            return None\n        try:\n            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))\n        except json.JSONDecodeError:\n            return None\n        return manifest if isinstance(manifest, dict) else None\n\n    def _load_vectors_array(self) -> Any:\n        if not self.vectors_path.exists():\n            return None\n        import numpy as np\n\n        return np.load(self.vectors_path, allow_pickle=False)\n\n    def _hydrate_cached_manifest(self, manifest: dict[str, Any], vectors: Any | None = None) -> bool:\n        chunk_dicts = manifest.get("chunks") or []\n        try:\n            self._chunks = [RepoChunk(**chunk_dict) for chunk_dict in chunk_dicts]\n        except TypeError:\n            return False\n        if vectors is None and self.vectors_path.exists():\n            try:\n                vectors = self._load_vectors_array()\n            except Exception:\n                return False\n        if vectors is not None:\n            if len(vectors) != len(self._chunks):\n                return False\n            self._vectors = vectors\n            self._backend = manifest.get("backend", "embedding")\n            self._report(\n                f"Loaded cached retrieval vectors for {len(self._chunks)} chunk(s) from {relative_path(self.vectors_path, self.workspace)}."\n            )\n        else:\n            self._vectors = None\n            self._backend = str(manifest.get("backend", "lexical"))\n        return True\n\n    def _load_cached(self, state: dict[str, dict[str, int]]) -> bool:\n        manifest = self._load_manifest()\n        if manifest is None:\n            return False\n        if manifest.get("file_state") != state:\n            return False\n        return self._hydrate_cached_manifest(manifest)\n\n    def _reuse_cached_chunks(self, state: dict[str, dict[str, int]]) -> bool:\n        manifest = self._load_manifest()\n        if manifest is None:\n            return False\n        cached_state = manifest.get("file_state")\n        if not isinstance(cached_state, dict) or cached_state == state:\n            return False\n        chunk_dicts = manifest.get("chunks") or []\n        try:\n            cached_chunks = [RepoChunk(**chunk_dict) for chunk_dict in chunk_dicts]\n        except TypeError:\n            return False\n        cached_vectors = None\n        if self.vectors_path.exists():\n            try:\n                cached_vectors = self._load_vectors_array()\n            except Exception:\n                cached_vectors = None\n        if cached_vectors is not None and len(cached_vectors) != len(cached_chunks):\n            cached_vectors = None\n\n        cached_by_path: dict[str, list[tuple[RepoChunk, Any | None]]] = {}\n        for index, chunk in enumerate(cached_chunks):\n            vector = cached_vectors[index] if cached_vectors is not None else None\n            cached_by_path.setdefault(chunk.path, []).append((chunk, vector))\n\n        files = sorted(self._iter_files())\n        new_chunks: list[RepoChunk] = []\n        vector_slots: list[Any | None] = []\n        pending_embed_chunks: list[RepoChunk] = []\n        pending_embed_positions: list[int] = []\n        reused_files = 0\n        changed_files = 0\n        reused_chunks = 0\n        regenerated_chunks = 0\n\n        for path in files:\n            rel = relative_path(path, self.workspace)\n            cached_entries = cached_by_path.get(rel, [])\n            if cached_state.get(rel) == state.get(rel) and cached_entries:\n                reused_files += 1\n                for chunk, vector in cached_entries:\n                    new_chunks.append(chunk)\n                    if vector is not None:\n                        vector_slots.append(vector)\n                        reused_chunks += 1\n                    else:\n                        vector_slots.append(None)\n                        pending_embed_chunks.append(chunk)\n                        pending_embed_positions.append(len(vector_slots) - 1)\n                        regenerated_chunks += 1\n                continue\n\n            changed_files += 1\n            cached_by_id = {chunk.chunk_id: (chunk, vector) for chunk, vector in cached_entries}\n            for chunk in self._chunk_file(path):\n                new_chunks.append(chunk)\n                cached_entry = cached_by_id.get(chunk.chunk_id)\n                if cached_entry is not None and cached_entry[0].text == chunk.text and cached_entry[1] is not None:\n                    vector_slots.append(cached_entry[1])\n                    reused_chunks += 1\n                else:\n                    vector_slots.append(None)\n                    pending_embed_chunks.append(chunk)\n                    pending_embed_positions.append(len(vector_slots) - 1)\n                    regenerated_chunks += 1\n\n        if not new_chunks:\n            self._chunks = []\n            self._vectors = None\n            self._backend = "lexical"\n            self._last_error = None\n            self._reset_faiss_index()\n            self._save_cached(state)\n            self._report("Repository retrieval cache reused with no remaining source chunks.")\n            return True\n\n        self._chunks = new_chunks\n        self._vectors = None\n        self._backend = "lexical"\n        self._last_error = None\n        self._reset_faiss_index()\n        self._report(\n            "Retrieval cache changed; "\n            f"reusing {reused_chunks} cached chunk vector(s) across {reused_files} unchanged file(s) "\n            f"and rebuilding {regenerated_chunks} chunk(s) from {changed_files} changed or new file(s)."\n        )\n\n        if any(vector is not None for vector in vector_slots):\n            try:\n                import numpy as np\n\n                if pending_embed_chunks:\n                    self._report(\n                        f"Generating repository embeddings for {len(pending_embed_chunks)} changed or uncached chunk(s)."\n                    )\n                    generated_vectors = self.embedder.encode_documents([chunk.text for chunk in pending_embed_chunks])\n                    for position, vector in zip(pending_embed_positions, generated_vectors, strict=False):\n                        vector_slots[position] = vector\n                if all(vector is not None for vector in vector_slots):\n                    self._vectors = np.asarray(vector_slots, dtype="float32")\n                    self._backend = "embedding-numpy"\n                    if self.embedder.load_warning:\n                        self._last_error = self.embedder.load_warning\n                    self._ensure_faiss_index()\n            except Exception as exc:  # pragma: no cover - runtime fallback path\n                self._last_error = f"{type(exc).__name__}: {exc}"\n                self._vectors = None\n                self._backend = "lexical"\n        elif pending_embed_chunks:\n            try:\n                self._report(\n                    f"Generating repository embeddings for {len(pending_embed_chunks)} changed or uncached chunk(s)."\n                )\n                self._vectors = self.embedder.encode_documents([chunk.text for chunk in pending_embed_chunks])\n                self._backend = "embedding-numpy"\n                if self.embedder.load_warning:\n                    self._last_error = self.embedder.load_warning\n                self._ensure_faiss_index()\n            except Exception as exc:  # pragma: no cover - runtime fallback path\n                self._last_error = f"{type(exc).__name__}: {exc}"\n                self._vectors = None\n                self._backend = "lexical"\n\n        self._save_cached(state)\n        return True\n\n    def _save_cached(self, state: dict[str, dict[str, int]]) -> None:\n        manifest = {\n            "file_state": state,\n            "chunks": [asdict(chunk) for chunk in self._chunks],\n            "backend": self._backend,\n        }\n        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")\n        if self._vectors is not None:\n            import numpy as np\n\n            np.save(self.vectors_path, self._vectors, allow_pickle=False)\n        elif self.vectors_path.exists():\n            self.vectors_path.unlink()\n\n    def _reset_faiss_index(self) -> None:\n        self._faiss_index = None\n        self._faiss_gpu_resources = None\n        self._faiss_search_params = None\n\n    def _ensure_faiss_index(self) -> bool:\n        if self._vectors is None:\n            return False\n        if self._faiss_index is not None:\n            return True\n        try:\n            import faiss\n        except Exception as exc:  # pragma: no cover - optional dependency path\n            self._last_error = f"{type(exc).__name__}: {exc}"\n            return False\n        try:\n            requested_backend = self._normalize_backend_name(self.config.retrieval_backend)\n            cpu_index = faiss.IndexFlatIP(int(self._vectors.shape[1]))\n            cpu_index.add(self._vectors)\n            backend = "faiss-cpu-flat"\n            gpu_count = faiss.get_num_gpus()\n            if gpu_count > 0 and hasattr(faiss, "StandardGpuResources") and hasattr(faiss, "index_cpu_to_gpu"):\n                try:\n                    self._faiss_gpu_resources = self._new_gpu_resources(faiss)\n                    if requested_backend in {"auto", "flat"}:\n                        self._faiss_index, backend, self._faiss_search_params = self._build_gpu_flat_index(faiss)\n                    elif requested_backend == "ivf_flat":\n                        self._faiss_index, backend, self._faiss_search_params = self._build_gpu_ivf_flat_index(faiss)\n                    elif requested_backend == "ivf_pq":\n                        self._faiss_index, backend, self._faiss_search_params = self._build_gpu_ivf_pq_index(faiss)\n                    elif requested_backend == "cagra":\n                        self._faiss_index, backend, self._faiss_search_params = self._build_gpu_cagra_index(faiss)\n                    else:\n                        self._report(\n                            f"Unknown retrieval backend \'{self.config.retrieval_backend}\', using flat cuVS search instead."\n                        )\n                        self._faiss_index, backend, self._faiss_search_params = self._build_gpu_flat_index(faiss)\n                except Exception as exc:\n                    self._last_error = f"{type(exc).__name__}: {exc}"\n                    self._faiss_gpu_resources = None\n                    self._faiss_index = cpu_index\n                    self._faiss_search_params = None\n                    backend = "faiss-cpu-flat"\n                    self._report(\n                        "FAISS GPU index allocation failed, falling back to CPU FAISS: "\n                        f"{self._last_error}"\n                    )\n            else:\n                if requested_backend not in {"auto", "flat"}:\n                    self._report(\n                        f"Retrieval backend \'{requested_backend}\' requires FAISS GPU support; using CPU flat search."\n                    )\n                self._faiss_index = cpu_index\n            self._faiss = faiss\n            self._backend = backend\n            self._report(f"Retrieval index ready with backend {backend} over {len(self._chunks)} chunk(s).")\n            return True\n        except Exception as exc:  # pragma: no cover - runtime fallback path\n            self._last_error = f"{type(exc).__name__}: {exc}"\n            self._reset_faiss_index()\n            return False\n\n    def ensure_index(self) -> None:\n        state = self._file_state()\n        if self._load_cached(state):\n            if not self._chunks and state:\n                self._report("Cached retrieval chunk metadata is missing. Rebuilding repository index.")\n            else:\n                self._reset_faiss_index()\n                if self._vectors is None and self._chunks:\n                    self._report("Cached retrieval vectors are missing. Rebuilding embeddings from cached repository chunks.")\n                    try:\n                        self._vectors = self.embedder.encode_documents([chunk.text for chunk in self._chunks])\n                        self._backend = "embedding-numpy"\n                        if self.embedder.load_warning:\n                            self._last_error = self.embedder.load_warning\n                        self._ensure_faiss_index()\n                    except Exception as exc:  # pragma: no cover - runtime fallback path\n                        self._last_error = f"{type(exc).__name__}: {exc}"\n                        self._vectors = None\n                        self._backend = "lexical"\n                    self._save_cached(state)\n                    return\n                self._ensure_faiss_index()\n                return\n        if self._reuse_cached_chunks(state):\n            return\n        self._report("Retrieval cache is stale or missing. Rebuilding repository index.")\n        self._chunks = self._build_chunks()\n        self._vectors = None\n        self._backend = "lexical"\n        self._last_error = None\n        self._reset_faiss_index()\n        if self._chunks:\n            try:\n                self._report("Generating repository embeddings.")\n                self._vectors = self.embedder.encode_documents([chunk.text for chunk in self._chunks])\n                self._backend = "embedding-numpy"\n                if self.embedder.load_warning:\n                    self._last_error = self.embedder.load_warning\n                self._ensure_faiss_index()\n            except Exception as exc:  # pragma: no cover - runtime fallback path\n                self._last_error = f"{type(exc).__name__}: {exc}"\n                self._vectors = None\n                self._backend = "lexical"\n        self._save_cached(state)\n\n    def _lexical_scores(self, query: str) -> list[tuple[float, RepoChunk]]:\n        query_tokens = token_set(query)\n        scored: list[tuple[float, RepoChunk]] = []\n        for chunk in self._chunks:\n            lowered = chunk.text.lower()\n            score = float(sum(lowered.count(token) for token in query_tokens))\n            if any(token in chunk.path.lower() for token in query_tokens):\n                score += 2.0\n            if score > 0:\n                scored.append((score, chunk))\n        scored.sort(key=lambda item: item[0], reverse=True)\n        return scored\n\n    def query(self, query: str, limit: int | None = None) -> dict[str, Any]:\n        import numpy as np\n\n        self.ensure_index()\n        top_k = limit or self.recommended_result_limit()\n        results: list[dict[str, Any]] = []\n        if not self._chunks:\n            return {"backend": "empty", "results": [], "error": self._last_error}\n        if self._vectors is not None:\n            try:\n                self._report(f"Retrieving repository context for query: {shorten(query, 100)}")\n                query_vector = self.embedder.encode_queries([query])[0]\n                if self._ensure_faiss_index():\n                    search_kwargs: dict[str, Any] = {}\n                    if self._faiss_search_params is not None:\n                        search_kwargs["params"] = self._faiss_search_params\n                    scores_array, indices_array = self._faiss_index.search(\n                        np.ascontiguousarray(query_vector.reshape(1, -1), dtype="float32"),\n                        top_k,\n                        **search_kwargs,\n                    )\n                    top_indices = [int(index) for index in indices_array[0] if int(index) >= 0]\n                    score_map = {\n                        int(index): float(score)\n                        for index, score in zip(indices_array[0], scores_array[0], strict=False)\n                        if int(index) >= 0\n                    }\n                else:\n                    scores = self._vectors @ query_vector\n                    top_indices = [int(index) for index in np.argsort(scores)[::-1][:top_k]]\n                    score_map = {int(index): float(scores[int(index)]) for index in top_indices}\n                for index in top_indices:\n                    chunk = self._chunks[int(index)]\n                    results.append(\n                        {\n                            "path": chunk.path,\n                            "start_line": chunk.start_line,\n                            "end_line": chunk.end_line,\n                            "score": score_map[int(index)],\n                            "text": shorten(chunk.text, 1400),\n                        }\n                    )\n            except Exception as exc:  # pragma: no cover - runtime fallback path\n                self._last_error = f"{type(exc).__name__}: {exc}"\n                self._reset_faiss_index()\n                if self._vectors is not None:\n                    scores = self._vectors @ query_vector\n                    top_indices = [int(index) for index in np.argsort(scores)[::-1][:top_k]]\n                    self._backend = "embedding-numpy"\n                    for index in top_indices:\n                        chunk = self._chunks[index]\n                        results.append(\n                            {\n                                "path": chunk.path,\n                                "start_line": chunk.start_line,\n                                "end_line": chunk.end_line,\n                                "score": float(scores[index]),\n                                "text": shorten(chunk.text, 1400),\n                            }\n                        )\n        if not results:\n            for score, chunk in self._lexical_scores(query)[:top_k]:\n                results.append(\n                    {\n                        "path": chunk.path,\n                        "start_line": chunk.start_line,\n                        "end_line": chunk.end_line,\n                        "score": score,\n                        "text": shorten(chunk.text, 1400),\n                    }\n                )\n        if results:\n            preview = ", ".join(f"{item[\'path\']}:{item[\'start_line\']}-{item[\'end_line\']}" for item in results[:3])\n            self._report(f"Repository context ready from {self._backend}: {preview}")\n        return {"backend": self._backend, "results": results, "error": self._last_error}\n\n    def format_context(self, query: str) -> str:\n        query_result = self.query(query)\n        lines = [f"Repository retrieval backend: {query_result[\'backend\']}"]\n        if query_result.get("error"):\n            lines.append(f"Retrieval warning: {query_result[\'error\']}")\n        if not query_result["results"]:\n            lines.append("No repository context was retrieved.")\n            return "\\n".join(lines)\n        for item in query_result["results"]:\n            lines.append(\n                f"- {item[\'path\']}:{item[\'start_line\']}-{item[\'end_line\']} score={item[\'score\']:.3f}\\n"\n                f"  {item[\'text\']}"\n            )\n        return "\\n".join(lines)\n\n    def release_gpu_resources(self) -> None:\n        released: list[str] = []\n        if self._faiss_index is not None and self._backend.startswith("faiss-gpu"):\n            released.append("FAISS GPU index")\n            self._backend = "embedding-numpy" if self._vectors is not None else self._backend\n        self._reset_faiss_index()\n        if self.embedder.release_gpu():\n            released.append("embedder")\n        if released:\n            self._report(f"Released retrieval GPU resources before generation: {\', \'.join(released)}.")\n\n\nclass DirectHTTPTransport:\n    def __init__(self, base_url: str, label: str) -> None:\n        self.base_url = base_url.rstrip("/")\n        self.label = label\n\n    def probe_json(self, path: str, timeout: float = 20.0) -> dict[str, Any]:\n        import httpx\n\n        with httpx.Client(timeout=timeout) as client:\n            response = client.get(f"{self.base_url}{path}")\n            text = response.text\n            try:\n                payload = response.json()\n            except ValueError:\n                payload = {}\n            return {\n                "status_code": response.status_code,\n                "json": payload if isinstance(payload, dict) else {},\n                "text": text,\n            }\n\n    def get_json(self, path: str) -> dict[str, Any]:\n        probe = self.probe_json(path)\n        status_code = int(probe.get("status_code") or 0)\n        if status_code < 200 or status_code >= 300:\n            raise RuntimeError(\n                f"HTTP {status_code} for {self.base_url}{path}: "\n                f"{shorten(str(probe.get(\'text\') or probe.get(\'json\') or \'\'), 300)}"\n            )\n        return probe.get("json") or {}\n\n    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:\n        import httpx\n\n        with httpx.Client(timeout=600.0) as client:\n            response = client.post(f"{self.base_url}{path}", json=payload)\n            response.raise_for_status()\n            return response.json()\n\n\nclass WindowsBridgeHTTPTransport:\n    def __init__(self, base_url: str = DEFAULT_LLAMACPP_BASE_URL) -> None:\n        self.base_url = base_url.rstrip("/")\n        self.label = "windows-bridge"\n\n    @staticmethod\n    def _encoded(script: str) -> str:\n        return base64.b64encode(script.encode("utf-16le")).decode("ascii")\n\n    def _run_script(self, script: str) -> str:\n        result = subprocess.run(\n            ["powershell.exe", "-NoProfile", "-EncodedCommand", self._encoded(script)],\n            capture_output=True,\n            text=True,\n            timeout=600,\n            check=False,\n        )\n        if result.returncode != 0:\n            message = result.stderr.strip() or result.stdout.strip() or "Unknown PowerShell error"\n            raise RuntimeError(message)\n        return result.stdout.strip()\n\n    def get_json(self, path: str) -> dict[str, Any]:\n        script = textwrap.dedent(\n            f"""\n            $ProgressPreference = \'SilentlyContinue\'\n            $response = Invoke-RestMethod -Uri \'{self.base_url}{path}\' -Method Get\n            $response | ConvertTo-Json -Depth 100 -Compress\n            """\n        )\n        output = self._run_script(script)\n        return json.loads(output) if output else {}\n\n    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:\n        body = json.dumps(payload, ensure_ascii=True)\n        script = textwrap.dedent(\n            f"""\n            $ProgressPreference = \'SilentlyContinue\'\n            $body = @\'\n            {body}\n            \'@\n            $response = Invoke-RestMethod -Uri \'{self.base_url}{path}\' -Method Post -ContentType \'application/json\' -Body $body\n            $response | ConvertTo-Json -Depth 100 -Compress\n            """\n        )\n        output = self._run_script(script)\n        return json.loads(output) if output else {}\n\n\nclass LlamaCppServerProcess:\n    def __init__(self, config: AgentConfig) -> None:\n        self.config = config\n        self.runtime_workspace = config_runtime_workspace(config)\n        self.process: subprocess.Popen[str] | None = None\n        self.base_url = normalize_base_url(config.server_url)\n        self.model_path: Path | None = None\n        self.log_path = self.runtime_workspace / ".cache" / "llama.cpp" / "llama-server.log"\n\n    def resolve_model_path(self) -> Path:\n        if self.model_path is None:\n            self.model_path = resolve_gguf_model_path(\n                self.runtime_workspace,\n                self.config.model_path,\n                self.config.selected_model_filename,\n            )\n        if self.model_path is None:\n            raise RuntimeError(\n                "No local GGUF model path was found. "\n                f"Set {GGUF_MODEL_PATH_ENV_NAME} or pass --model-path, "\n                f"or place {DEFAULT_GGUF_MODEL_FILENAME} under models/."\n            )\n        return self.model_path\n\n    def resolve_executable(self) -> str:\n        executable = resolve_llama_server_executable(self.runtime_workspace, self.config.llama_server_path)\n        if executable is None:\n            raise RuntimeError(\n                "No llama-server executable was found. "\n                f"Install llama.cpp or set {LLAMACPP_SERVER_PATH_ENV_NAME}."\n            )\n        return executable\n\n    def _launch_command(self) -> list[str]:\n        base_url = self.base_url\n        if "://" in base_url:\n            host_port = base_url.split("://", 1)[1]\n        else:\n            host_port = base_url\n        host, _, port_text = host_port.partition(":")\n        port = port_text or str(DEFAULT_LLAMACPP_PORT)\n        executable = self.resolve_executable()\n        model_path = self.resolve_model_path()\n        command = [\n            executable,\n            "--model",\n            str(model_path),\n            "--alias",\n            self.config.model_name,\n            "--host",\n            host or DEFAULT_LLAMACPP_HOST,\n            "--port",\n            port,\n            "--ctx-size",\n            str(self.config.num_ctx),\n            "--parallel",\n            str(DEFAULT_LLAMACPP_PARALLEL),\n            "--jinja",\n            "--flash-attn",\n            "on",\n            "--no-warmup",\n            "--temp",\n            str(self.config.temperature),\n            "--top-p",\n            str(self.config.top_p),\n            "--min-p",\n            str(self.config.min_p),\n            "--repeat-penalty",\n            str(self.config.repeat_penalty),\n        ]\n        chat_template = (os.environ.get(LLAMACPP_CHAT_TEMPLATE_ENV_NAME) or "").strip()\n        chat_template_file = (os.environ.get(LLAMACPP_CHAT_TEMPLATE_FILE_ENV_NAME) or "").strip()\n        if chat_template_file:\n            command.extend(["--chat-template-file", chat_template_file])\n        elif chat_template:\n            command.extend(["--chat-template", chat_template])\n        if in_wsl() and executable.lower().endswith(".exe") and shutil.which("powershell.exe"):\n            windows_command = list(command)\n            windows_command[0] = wsl_path_to_windows_path(executable)\n            windows_command[2] = wsl_path_to_windows_path(model_path)\n            if "--chat-template-file" in windows_command:\n                index = windows_command.index("--chat-template-file")\n                if index + 1 < len(windows_command):\n                    windows_command[index + 1] = wsl_path_to_windows_path(windows_command[index + 1])\n            workspace_windows = wsl_path_to_windows_path(self.runtime_workspace)\n            script_lines = [\n                "$ProgressPreference = \'SilentlyContinue\'",\n                f"Set-Location -LiteralPath {powershell_single_quote(workspace_windows)}",\n                "& " + " ".join(powershell_single_quote(part) for part in windows_command),\n            ]\n            script = "\\n".join(script_lines)\n            return [\n                "powershell.exe",\n                "-NoProfile",\n                "-EncodedCommand",\n                base64.b64encode(script.encode("utf-16le")).decode("ascii"),\n            ]\n        return command\n\n    def ensure_started(self) -> None:\n        if self.process is not None and self.process.poll() is None:\n            return\n        creationflags = 0\n        startupinfo: subprocess.STARTUPINFO | None = None\n        if os.name == "nt":\n            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)\n            startupinfo = subprocess.STARTUPINFO()\n            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW\n        launch_command = self._launch_command()\n        command_path = Path(launch_command[0])\n        if launch_command[0].lower() == "powershell.exe" or command_path.suffix.lower() == ".exe":\n            self.log_path = self.runtime_workspace / ".cache" / "llama.cpp" / "llama-server.log"\n        else:\n            self.log_path = llamacpp_native_server_log_path(self.runtime_workspace)\n        self.log_path.parent.mkdir(parents=True, exist_ok=True)\n        with self.log_path.open("w", encoding="utf-8", errors="ignore") as log_handle:\n            log_handle.write(f"[{now_stamp()}] Starting llama-server\\n")\n            log_handle.write("Command: " + " ".join(launch_command) + "\\n\\n")\n        log_handle = self.log_path.open("a", encoding="utf-8", errors="ignore")\n        popen_command = launch_command\n        if in_wsl() and popen_command and popen_command[0].lower() == "powershell.exe":\n            self.process = subprocess.Popen(\n                render_windows_command_for_wsl_shell(popen_command),\n                cwd=self.runtime_workspace,\n                stdout=log_handle,\n                stderr=subprocess.STDOUT,\n                stdin=subprocess.DEVNULL,\n                text=True,\n                shell=True,\n                executable="/bin/bash",\n                creationflags=creationflags,\n                startupinfo=startupinfo,\n            )\n            return\n        if in_wsl() and popen_command and (\n            popen_command[0].lower().endswith(".exe") or Path(popen_command[0]).suffix.lower() == ".exe"\n        ):\n            popen_command = wrap_windows_command_for_wsl(popen_command)\n        self.process = subprocess.Popen(\n            popen_command,\n            cwd=self.runtime_workspace,\n            stdout=log_handle,\n            stderr=subprocess.STDOUT,\n            stdin=subprocess.DEVNULL,\n            text=True,\n            creationflags=creationflags,\n            startupinfo=startupinfo,\n        )\n\n    def read_log_tail(self, max_chars: int = 4000) -> str:\n        if not self.log_path.exists():\n            return ""\n        text = self.log_path.read_text(encoding="utf-8", errors="ignore")\n        return shorten(text, max_chars)\n\n    def shutdown(self) -> None:\n        process = self.process\n        if process is None:\n            return\n        if process.poll() is None:\n            process.terminate()\n            try:\n                process.wait(timeout=10)\n            except subprocess.TimeoutExpired:\n                process.kill()\n                process.wait(timeout=10)\n        self.process = None\n\n\nclass LlamaCppClient:\n    def __init__(\n        self,\n        config: AgentConfig,\n        transports: Sequence[DirectHTTPTransport | WindowsBridgeHTTPTransport],\n        *,\n        server_process: LlamaCppServerProcess | None = None,\n    ) -> None:\n        if not transports:\n            raise ValueError("At least one llama.cpp transport is required.")\n        self.config = config\n        self.transports = list(transports)\n        self.transport = self.transports[0]\n        self.server_process = server_process\n\n    @classmethod\n    def detect(cls, config: AgentConfig) -> "LlamaCppClient":\n        base_url = normalize_base_url(config.server_url or os.environ.get(LLAMACPP_SERVER_URL_ENV_NAME))\n        transports = cls._reachable_transports(base_url)\n        server_process: LlamaCppServerProcess | None = None\n        if not transports:\n            server_process = LlamaCppServerProcess(config)\n            server_process.ensure_started()\n            deadline = time.time() + 120.0\n            while time.time() < deadline:\n                transports = cls._reachable_transports(base_url)\n                if transports:\n                    break\n                process = server_process.process\n                if process is not None and process.poll() is not None:\n                    log_tail = server_process.read_log_tail()\n                    log_hint = f"\\nServer log tail:\\n{log_tail}" if log_tail else ""\n                    raise RuntimeError(f"llama-server exited before it became ready.{log_hint}")\n                time.sleep(0.5)\n        if not transports:\n            log_hint = ""\n            if server_process is not None:\n                log_tail = server_process.read_log_tail()\n                if log_tail:\n                    log_hint = f"\\nServer log tail:\\n{log_tail}"\n            raise RuntimeError(\n                "No reachable llama.cpp server was found. "\n                f"Set {LLAMACPP_SERVER_URL_ENV_NAME} to an existing server, or install llama.cpp "\n                f"and set {GGUF_MODEL_PATH_ENV_NAME}."\n                f"{log_hint}"\n            )\n        return cls(config, transports, server_process=server_process)\n\n    @staticmethod\n    def _reachable_transports(base_url: str) -> list[DirectHTTPTransport | WindowsBridgeHTTPTransport]:\n        transports: list[DirectHTTPTransport | WindowsBridgeHTTPTransport] = []\n        direct = DirectHTTPTransport(base_url, "direct")\n        try:\n            probe = direct.probe_json("/v1/models")\n            if int(probe.get("status_code") or 0) == 200 or LlamaCppClient._probe_reports_loading(probe):\n                transports.append(direct)\n        except Exception:\n            pass\n        if in_wsl() and shutil.which("powershell.exe") and not llamacpp_native_server_executable(Path.cwd()).exists():\n            bridge = WindowsBridgeHTTPTransport(base_url)\n            try:\n                bridge.get_json("/v1/models")\n                transports.append(bridge)\n            except Exception:\n                pass\n        return transports\n\n    @property\n    def label(self) -> str:\n        return self.transport.label\n\n    @property\n    def base_url(self) -> str:\n        return self.transport.base_url\n\n    def shutdown(self) -> None:\n        if self.server_process is not None:\n            self.server_process.shutdown()\n\n    def _call_with_fallback(self, method_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:\n        errors: list[str] = []\n        for index, transport in enumerate(list(self.transports)):\n            try:\n                result = getattr(transport, method_name)(*args, **kwargs)\n            except Exception as exc:\n                errors.append(f"{transport.label}: {type(exc).__name__}: {exc}")\n                continue\n            if index != 0:\n                self.transports.insert(0, self.transports.pop(index))\n            self.transport = self.transports[0]\n            return result\n        raise RuntimeError(" ; ".join(errors) or "All llama.cpp transports failed.")\n\n    @staticmethod\n    def _probe_reports_loading(probe: dict[str, Any]) -> bool:\n        status_code = int(probe.get("status_code") or 0)\n        if status_code == 200:\n            return False\n        payload = probe.get("json") or {}\n        message = ""\n        if isinstance(payload, dict):\n            error_payload = payload.get("error")\n            if isinstance(error_payload, dict):\n                message = str(error_payload.get("message") or "")\n        if not message:\n            message = str(probe.get("text") or "")\n        lowered = message.lower()\n        return status_code in {425, 429, 503} and ("loading model" in lowered or "loading" in lowered)\n\n    def _probe_with_fallback(self, path: str) -> dict[str, Any]:\n        errors: list[str] = []\n        for index, transport in enumerate(list(self.transports)):\n            try:\n                if hasattr(transport, "probe_json"):\n                    result = transport.probe_json(path)\n                else:\n                    result = {\n                        "status_code": 200,\n                        "json": transport.get_json(path),\n                        "text": "",\n                    }\n            except Exception as exc:\n                errors.append(f"{transport.label}: {type(exc).__name__}: {exc}")\n                continue\n            if index != 0:\n                self.transports.insert(0, self.transports.pop(index))\n            self.transport = self.transports[0]\n            return result\n        return {"status_code": 0, "json": {}, "text": " ; ".join(errors)}\n\n    def list_models(self) -> list[dict[str, Any]]:\n        payload = self._call_with_fallback("get_json", "/v1/models")\n        data = payload.get("data")\n        return data if isinstance(data, list) else []\n\n    def ensure_model(\n        self,\n        model_name: str,\n        timeout_seconds: float = DEFAULT_LLAMACPP_MODEL_READY_TIMEOUT_SECONDS,\n    ) -> str:\n        deadline = time.time() + timeout_seconds\n        models: list[dict[str, Any]] = []\n        props_available = False\n        saw_loading = False\n        last_model_probe: dict[str, Any] = {}\n        last_props_probe: dict[str, Any] = {}\n        while time.time() < deadline:\n            last_model_probe = self._probe_with_fallback("/v1/models")\n            if int(last_model_probe.get("status_code") or 0) == 200:\n                payload = last_model_probe.get("json") or {}\n                data = payload.get("data")\n                models = data if isinstance(data, list) else []\n                if models:\n                    break\n            saw_loading = saw_loading or self._probe_reports_loading(last_model_probe)\n            last_props_probe = self._probe_with_fallback("/props")\n            if int(last_props_probe.get("status_code") or 0) == 200:\n                props = last_props_probe.get("json") or {}\n                props_available = bool(props)\n            else:\n                props_available = False\n            saw_loading = saw_loading or self._probe_reports_loading(last_props_probe)\n            if props_available:\n                return model_name\n            if self.server_process is not None:\n                process = self.server_process.process\n                if process is not None and process.poll() is not None:\n                    log_tail = self.server_process.read_log_tail()\n                    log_hint = f"\\nServer log tail:\\n{log_tail}" if log_tail else ""\n                    raise RuntimeError(f"llama-server exited while the model was loading.{log_hint}")\n            time.sleep(0.5)\n        if not models:\n            log_hint = ""\n            if self.server_process is not None:\n                log_tail = self.server_process.read_log_tail()\n                if log_tail:\n                    log_hint = f" Server log tail:\\n{log_tail}"\n            if saw_loading:\n                raise RuntimeError(\n                    f"llama.cpp server at {self.base_url} was still loading the model after {timeout_seconds:.0f}s."\n                    f"{log_hint}"\n                )\n            probe_hint = ""\n            if last_model_probe or last_props_probe:\n                probe_hint = (\n                    f" Last /v1/models probe: {shorten(json.dumps(last_model_probe, ensure_ascii=False), 800)}"\n                    f" Last /props probe: {shorten(json.dumps(last_props_probe, ensure_ascii=False), 800)}"\n                )\n            raise RuntimeError(\n                f"No model was advertised by {self.base_url} within {timeout_seconds:.0f}s. "\n                "Verify that llama-server was started with a GGUF model and wait until loading finishes."\n                f"{probe_hint}"\n                f"{log_hint}"\n            )\n        for item in models:\n            candidate = (item.get("id") or "").strip()\n            if candidate == model_name:\n                return model_name\n        if len(models) == 1:\n            only_id = (models[0].get("id") or "").strip()\n            if only_id:\n                return only_id\n        available = ", ".join(item.get("id", "?") for item in models) or "none"\n        raise RuntimeError(\n            f"Model alias {model_name!r} is not available from {self.base_url}. Available: {available}"\n        )\n\n    def runtime_status(self, model_name: str) -> dict[str, Any] | None:\n        for model in self.list_models():\n            if (model.get("id") or "").strip() == model_name:\n                return model\n        return None\n\n    def startup_diagnostics(self, model_name: str) -> dict[str, Any]:\n        diagnostics: dict[str, Any] = {\n            "transport": self.label,\n            "base_url": self.base_url,\n            "runtime": self.runtime_status(model_name),\n            "launched": bool(\n                self.server_process and self.server_process.process and self.server_process.process.poll() is None\n            ),\n        }\n        if self.server_process is not None:\n            try:\n                diagnostics["model_path"] = str(self.server_process.resolve_model_path())\n            except Exception as exc:\n                diagnostics["model_path_error"] = f"{type(exc).__name__}: {exc}"\n            try:\n                diagnostics["server_executable"] = self.server_process.resolve_executable()\n            except Exception as exc:\n                diagnostics["server_executable_error"] = f"{type(exc).__name__}: {exc}"\n            diagnostics["server_log_path"] = str(self.server_process.log_path)\n            diagnostics["server_log_tail"] = self.server_process.read_log_tail()\n        try:\n            diagnostics["props"] = self._call_with_fallback("get_json", "/props")\n        except Exception as exc:\n            diagnostics["props_error"] = f"{type(exc).__name__}: {exc}"\n        return diagnostics\n\n    @staticmethod\n    def _normalize_tool_calls(raw_tool_calls: Any) -> list[dict[str, Any]]:\n        if not isinstance(raw_tool_calls, list):\n            return []\n        normalized: list[dict[str, Any]] = []\n        for index, raw in enumerate(raw_tool_calls):\n            if not isinstance(raw, dict):\n                continue\n            function = raw.get("function")\n            if isinstance(function, dict):\n                name = (function.get("name") or raw.get("name") or "").strip()\n                arguments = function.get("arguments", raw.get("arguments"))\n            else:\n                name = (raw.get("name") or "").strip()\n                arguments = raw.get("arguments")\n            if not name:\n                continue\n            if not isinstance(arguments, str):\n                arguments = json.dumps(arguments or {}, ensure_ascii=True)\n            normalized.append(\n                {\n                    "id": raw.get("id") or f"call_{index + 1}",\n                    "type": "function",\n                    "function": {\n                        "name": name,\n                        "arguments": arguments,\n                    },\n                }\n            )\n        return normalized\n\n    def chat(\n        self,\n        *,\n        model_name: str,\n        messages: list[dict[str, Any]],\n        tools: list[dict[str, Any]],\n        num_predict: int,\n        temperature: float,\n        top_p: float,\n        min_p: float,\n        repeat_penalty: float,\n    ) -> dict[str, Any]:\n        payload = {\n            "model": model_name,\n            "messages": messages,\n            "stream": False,\n            "tools": tools,\n            "tool_choice": "auto",\n            "parallel_tool_calls": False,\n            "parse_tool_calls": True,\n            "max_tokens": num_predict,\n            "temperature": temperature,\n            "top_p": top_p,\n            "min_p": min_p,\n            "repeat_penalty": repeat_penalty,\n        }\n        raw = self._call_with_fallback("post_json", "/v1/chat/completions", payload)\n        choices = raw.get("choices") or []\n        choice = choices[0] if choices else {}\n        message = choice.get("message") or {}\n        content = message.get("content") or ""\n        if not isinstance(content, str):\n            content = json.dumps(content, ensure_ascii=False)\n        return {\n            "message": {\n                "role": "assistant",\n                "content": content,\n                "tool_calls": self._normalize_tool_calls(message.get("tool_calls")),\n            },\n            "usage": raw.get("usage") or {},\n            "timings": raw.get("timings") or {},\n            "finish_reason": choice.get("finish_reason"),\n        }\n\n\nclass MCPHost:\n    def __init__(self, workspace: Path) -> None:\n        self.workspace = workspace\n        self._stack = AsyncExitStack()\n        self.session: ClientSession | None = None\n\n    def _server_parameters(self) -> StdioServerParameters:\n        env = os.environ.copy()\n        env["QUBITZ_MCP_WORKSPACE"] = str(self.workspace.resolve())\n        if getattr(sys, "frozen", False):\n            command = sys.executable\n            args = ["--serve-mcp", "--workspace", "."]\n        else:\n            command = sys.executable\n            args = [str(Path(__file__).resolve()), "--serve-mcp", "--workspace", "."]\n        return StdioServerParameters(command=command, args=args, cwd=self.workspace, env=env)\n\n    async def __aenter__(self) -> "MCPHost":\n        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(self._server_parameters()))\n        self.session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))\n        await self.session.initialize()\n        return self\n\n    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:\n        await self._stack.aclose()\n\n    async def list_tools(self) -> list[mcp_types.Tool]:\n        assert self.session is not None\n        result = await self.session.list_tools()\n        return list(result.tools)\n\n    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:\n        assert self.session is not None\n        return await self.session.call_tool(name, arguments)\n\n\nclass RunCancelled(Exception):\n    pass\n\n\nclass AgentRunner:\n    def __init__(self, config: AgentConfig) -> None:\n        self.config = config\n        self.workspace = config.workspace.resolve()\n        self.runtime_workspace = config_runtime_workspace(config)\n        self.memory = MemoryStore(self.runtime_workspace)\n        self.skills = SkillRegistry(self.runtime_workspace)\n        self.retriever = RepoRetriever(config, progress_callback=self._report_retrieval)\n        self.history: list[dict[str, str]] = []\n        self.history_summary = ""\n        self.harness_text = load_harness_text(self.runtime_workspace)\n        self._active_callback: Callable[[str, str], None] | None = None\n        self._llm: LlamaCppClient | None = None\n        self._validated_model_keys: set[tuple[str, str]] = set()\n        self._startup_diagnostics_cache: dict[tuple[str, str], dict[str, Any]] = {}\n        self._tool_definitions_cache: list[dict[str, Any]] | None = None\n        self._tool_count_cache = 0\n        self._cancel_event = threading.Event()\n        self._side_notes_lock = threading.Lock()\n        self._pending_side_notes: list[str] = []\n\n    def _emit(self, callback: Callable[[str, str], None] | None, kind: str, message: str) -> None:\n        if callback is not None:\n            callback(kind, message)\n\n    def request_cancel(self) -> None:\n        if self._cancel_event.is_set():\n            return\n        self._cancel_event.set()\n        self._emit(\n            self._active_callback,\n            "status",\n            "Cancellation requested. Waiting for the current model or tool step to finish.",\n        )\n\n    def _raise_if_cancelled(self) -> None:\n        if self._cancel_event.is_set():\n            raise RunCancelled()\n\n    def submit_side_note(self, note: str) -> bool:\n        cleaned = note.strip()\n        if not cleaned:\n            return False\n        callback = self._active_callback\n        if callback is None:\n            return False\n        with self._side_notes_lock:\n            self._pending_side_notes.append(cleaned)\n        self._emit(\n            callback,\n            "status",\n            "Received /btw note. It will steer the active task after the current step finishes.",\n        )\n        return True\n\n    def _drain_side_notes(self) -> list[str]:\n        with self._side_notes_lock:\n            notes = list(self._pending_side_notes)\n            self._pending_side_notes.clear()\n        return notes\n\n    def _apply_side_notes(\n        self,\n        messages: list[dict[str, Any]],\n        callback: Callable[[str, str], None] | None,\n        *,\n        assistant_content: str | None = None,\n    ) -> bool:\n        notes = self._drain_side_notes()\n        if not notes:\n            return False\n        if assistant_content:\n            messages.append({"role": "assistant", "content": assistant_content})\n        for note in notes:\n            user_note = f"/btw {note}"\n            self.memory.add_turn("user", user_note)\n            self.history.append({"role": "user", "content": user_note})\n        note_lines = "\\n".join(f"- {note}" for note in notes)\n        messages.append(\n            {\n                "role": "user",\n                "content": (\n                    "Additional user /btw updates for the current task:\\n"\n                    f"{note_lines}\\n\\n"\n                    "Apply these updates to the active task before continuing."\n                ),\n            }\n        )\n        count = len(notes)\n        suffix = "s" if count != 1 else ""\n        self._emit(callback, "status", f"Applied {count} /btw update{suffix} to the active task.")\n        return True\n\n    def _get_llm(self) -> LlamaCppClient:\n        if self._llm is None:\n            self._llm = LlamaCppClient.detect(self.config)\n        return self._llm\n\n    def reset_runtime(self) -> None:\n        if self._llm is not None:\n            self._llm.shutdown()\n        self._llm = None\n        self._validated_model_keys.clear()\n        self._startup_diagnostics_cache.clear()\n\n    def _report_retrieval(self, message: str) -> None:\n        self._emit(self._active_callback, "status", message)\n\n    def _system_prompt(\n        self,\n        memory_context: str,\n        active_skill_context: str = "",\n        history_summary: str = "",\n    ) -> str:\n        history_section = history_summary or "None"\n        skill_section = active_skill_context or "None"\n        return textwrap.dedent(\n            f"""\n            You are AI Agent Qubitz, a local standalone coding agent running inside the repository workspace.\n\n            Governing harness:\n            {self.harness_text}\n\n            Operating rules for this runtime:\n            - Use tools directly whenever file inspection, file edits, file deletion, package installation, or command execution is needed.\n            - Prefer relative workspace paths when possible.\n            - Use the available tools to read current files before changing them.\n            - When the user explicitly asks to read a named file, call read_file yourself instead of assuming the runtime already loaded it.\n            - Keep final answers concise and factual.\n            - Do not expose hidden chain-of-thought.\n            - The current memory context is below.\n\n            Memory context:\n            {memory_context}\n\n            Active local skills:\n            {skill_section}\n\n            Condensed earlier conversation summary:\n            {history_section}\n            """\n        ).strip()\n\n    def _compact_history(self) -> None:\n        max_recent_messages = HISTORY_TURNS * 2\n        if len(self.history) <= max_recent_messages:\n            return\n        overflow = self.history[:-max_recent_messages]\n        self.history = self.history[-max_recent_messages:]\n        lines = [self.history_summary.strip()] if self.history_summary.strip() else []\n        for item in overflow:\n            label = "User" if item.get("role") == "user" else "Assistant"\n            lines.append(f"- {label}: {shorten(item.get(\'content\', \'\'), 240)}")\n        self.history_summary = shorten("\\n".join(line for line in lines if line), MAX_HISTORY_SUMMARY_CHARS)\n\n    def _estimate_active_context(\n        self,\n        messages: Sequence[dict[str, Any]],\n        tool_definitions: Sequence[dict[str, Any]],\n    ) -> int:\n        payload = json.dumps({"messages": list(messages), "tools": list(tool_definitions)}, ensure_ascii=False)\n        return estimate_tokens(payload)\n\n    def _user_message(\n        self,\n        prompt: str,\n        repo_context: str,\n        active_skills: Sequence[SkillDefinition] | None = None,\n    ) -> str:\n        skill_list = ", ".join(skill.name for skill in active_skills or []) or "None"\n        return textwrap.dedent(\n            f"""\n            User request:\n            {prompt}\n\n            Retrieved repository context:\n            {repo_context}\n\n            Activated skills:\n            {skill_list}\n\n            Use tools when they are helpful. Finish with a direct answer when the task is complete.\n            """\n        ).strip()\n\n    def _adaptive_step_budget(self, prompt: str) -> tuple[int, list[str]]:\n        if self.config.max_steps <= 0:\n            return 0, ["unlimited mode"]\n        base_steps = max(1, self.config.max_steps)\n        if base_steps >= MAX_ADAPTIVE_TOOL_STEPS:\n            return base_steps, []\n        file_mentions = len(extract_file_tokens(prompt))\n        multiline_request = len(prompt.splitlines()) >= 8\n        long_request = len(prompt) >= 600\n        reasons: list[str] = []\n        complexity = 0\n        if file_mentions >= 3:\n            complexity += 3\n            reasons.append("multiple file references")\n        elif file_mentions >= 2:\n            complexity += 2\n            reasons.append("more than one file reference")\n        if EDIT_INTENT_PATTERN.search(prompt):\n            complexity += 3\n            reasons.append("edit workflow")\n        if VERIFY_INTENT_PATTERN.search(prompt):\n            complexity += 2\n            reasons.append("verification requested")\n        if multiline_request or long_request:\n            complexity += 1\n            reasons.append("larger request")\n        if complexity >= 5:\n            return MAX_ADAPTIVE_TOOL_STEPS, reasons\n        if complexity >= 2:\n            return min(MAX_ADAPTIVE_TOOL_STEPS, max(base_steps, 20)), reasons\n        return base_steps, reasons\n\n    def _should_skip_repo_retrieval(self, prompt: str) -> bool:\n        return bool(READ_INTENT_PATTERN.search(prompt) and extract_file_tokens(prompt))\n\n    async def _run_async(\n        self,\n        prompt: str,\n        callback: Callable[[str, str], None] | None = None,\n    ) -> str:\n        self._active_callback = callback\n        try:\n            self.memory.add_turn("user", prompt)\n            self._raise_if_cancelled()\n            if self.skills.warnings:\n                for warning in self.skills.warnings:\n                    self._emit(callback, "status", f"Skill warning: {warning}")\n            self._emit(callback, "status", f"Loaded {self.skills.count()} local skill(s) from .skills.")\n            active_skills = self.skills.select_for_prompt(prompt)\n            active_skill_context = self.skills.render_active_context(active_skills)\n            if active_skills:\n                names = ", ".join(skill.name for skill in active_skills)\n                self._emit(callback, "status", f"Activated local skill(s): {names}")\n            try:\n                existing_model_path = resolve_gguf_model_path(\n                    self.runtime_workspace,\n                    self.config.model_path,\n                    self.config.selected_model_filename,\n                )\n            except (FileNotFoundError, ValueError) as exc:\n                raise RuntimeError(f"Configured GGUF model path is invalid: {exc}") from exc\n            if existing_model_path is None:\n                self._emit(\n                    callback,\n                    "status",\n                    (\n                        f"No local GGUF model was found for {DEFAULT_GGUF_MODEL_LABEL}. Downloading "\n                        f"{self.config.selected_model_filename} from Hugging Face into .cache/models/..."\n                    ),\n                )\n                downloaded_model_path = download_default_gguf_model(\n                    self.runtime_workspace,\n                    self.config.selected_model_filename,\n                )\n                self.config.model_path = str(downloaded_model_path)\n                self._emit(callback, "status", f"Downloaded GGUF model to {downloaded_model_path}")\n            elif self.config.model_path is None:\n                self.config.model_path = str(existing_model_path)\n            self._emit(\n                callback,\n                "status",\n                (\n                    "Retrieval GPU policy: prefer CUDA whenever it is available, "\n                    "fall back to CPU only if CUDA loading or allocation fails, and preserve "\n                    f"{self.config.retrieval_gpu_reserve_gib:.1f} GiB of headroom before generation."\n                ),\n            )\n            self._emit(\n                callback,\n                "status",\n                (\n                    "Retrieval backend preference: "\n                    f"{self.config.retrieval_backend} "\n                    "(default auto stays on exact flat cuVS search; set "\n                    "QUBITZ_RETRIEVAL_BACKEND=ivf_flat|ivf_pq|cagra to opt into ANN)."\n                ),\n            )\n            self._emit(\n                callback,\n                "status",\n                (\n                    "Adaptive repository context budget: "\n                    f"up to {self.retriever.recommended_result_limit()} retrieved chunk(s) "\n                    f"for num_ctx={self.config.num_ctx}."\n                ),\n            )\n            self._emit(\n                callback,\n                "status",\n                "Preparing repository context before loading the generation model to keep more VRAM free for embeddings.",\n            )\n            skip_repo_retrieval = self._should_skip_repo_retrieval(prompt)\n            if skip_repo_retrieval:\n                self._emit(\n                    callback,\n                    "status",\n                    "Skipping repository retrieval for this explicit file-read request so the model can inspect the target file with read_file first.",\n                )\n                repo_context = (\n                    "Repository retrieval was skipped for this request because the user explicitly asked to read "\n                    "named file(s); inspect those files directly with read_file before relying on repo retrieval."\n                )\n            else:\n                repo_context = self.retriever.format_context(prompt)\n                self.retriever.release_gpu_resources()\n            memory_context = self.memory.build_context(prompt)\n            if skip_repo_retrieval:\n                self._emit(callback, "status", "Memory context prepared; repository retrieval was skipped.")\n            else:\n                self._emit(callback, "status", "Repository and memory context prepared.")\n            self._raise_if_cancelled()\n\n            configured_server_path = (\n                self.config.llama_server_path or os.environ.get(LLAMACPP_SERVER_PATH_ENV_NAME) or ""\n            ).strip()\n            configured_server_url = (os.environ.get(LLAMACPP_SERVER_URL_ENV_NAME) or "").strip()\n            if (\n                not configured_server_path\n                and not configured_server_url\n                and normalize_base_url(self.config.server_url) == DEFAULT_LLAMACPP_BASE_URL\n            ):\n                missing_runtime = llamacpp_runtime_missing_files(self.runtime_workspace)\n                if missing_runtime:\n                    try:\n                        self._emit(\n                            callback,\n                            "status",\n                            (\n                                "Project-local llama.cpp runtime is missing required backend files. "\n                                "Downloading the latest official Windows CUDA 12 runtime from GitHub releases into .cache..."\n                            ),\n                        )\n                        runtime_info = ensure_project_local_llamacpp_runtime(self.runtime_workspace)\n                        self.config.llama_server_path = str(runtime_info["executable"])\n                        self._emit(\n                            callback,\n                            "status",\n                            f"Downloaded llama.cpp runtime {runtime_info[\'release_tag\']} to {runtime_info[\'runtime_dir\']}",\n                        )\n                    except Exception as exc:\n                        if not in_wsl():\n                            raise\n                        self._emit(\n                            callback,\n                            "status",\n                            (\n                                "Windows CUDA runtime bootstrap was unavailable; "\n                                "downloading the llama.cpp source tarball and building the native WSL backend instead. "\n                                f"Reason: {type(exc).__name__}: {shorten(str(exc), 300)}"\n                            ),\n                        )\n                        runtime_info = ensure_project_local_native_llamacpp_runtime(self.runtime_workspace)\n                        self.config.llama_server_path = str(runtime_info["executable"])\n                        self._emit(\n                            callback,\n                            "status",\n                            (\n                                f"Native WSL llama.cpp backend ready at {runtime_info[\'executable\']} "\n                                f"(log: {runtime_info[\'build_log\']})"\n                            ),\n                        )\n                else:\n                    local_runtime_executable = llamacpp_runtime_dir(self.runtime_workspace) / "llama-server.exe"\n                    if local_runtime_executable.exists():\n                        self.config.llama_server_path = str(local_runtime_executable.resolve())\n                        self._emit(\n                            callback,\n                            "status",\n                            f"Using cached Windows llama.cpp runtime at {local_runtime_executable.parent}",\n                        )\n\n            self._emit(callback, "status", "Detecting the local llama.cpp GGUF backend and validating the target model.")\n            llm = self._get_llm()\n            resolved_model_name = llm.ensure_model(self.config.model_name)\n            model_key = (llm.label, resolved_model_name)\n            self._validated_model_keys.add(model_key)\n            self._emit(callback, "status", f"llama.cpp transport: {llm.label} @ {llm.base_url}")\n            if resolved_model_name != self.config.model_name:\n                self._emit(\n                    callback,\n                    "status",\n                    (\n                        f"Using the single loaded server model id {resolved_model_name!r} "\n                        f"instead of requested alias {self.config.model_name!r}."\n                    ),\n                )\n            diagnostics = self._startup_diagnostics_cache.get(model_key)\n            if diagnostics is None:\n                diagnostics = llm.startup_diagnostics(resolved_model_name)\n                self._startup_diagnostics_cache[model_key] = diagnostics\n            if diagnostics.get("model_path"):\n                self._emit(callback, "status", f"GGUF model path: {diagnostics[\'model_path\']}")\n            if diagnostics.get("model_path_error"):\n                self._emit(callback, "status", f"GGUF model path warning: {diagnostics[\'model_path_error\']}")\n            if diagnostics.get("server_executable"):\n                self._emit(callback, "status", f"llama.cpp executable: {diagnostics[\'server_executable\']}")\n            if diagnostics.get("server_executable_error"):\n                self._emit(callback, "status", f"llama.cpp executable warning: {diagnostics[\'server_executable_error\']}")\n            if diagnostics.get("launched"):\n                self._emit(callback, "status", "llama.cpp server was auto-started for this session.")\n            if diagnostics.get("server_log_path"):\n                self._emit(callback, "status", f"llama.cpp server log: {diagnostics[\'server_log_path\']}")\n            if diagnostics.get("props_error"):\n                self._emit(callback, "status", f"llama.cpp server warning: {diagnostics[\'props_error\']}")\n            runtime = diagnostics.get("runtime")\n            if runtime is not None:\n                self._emit(\n                    callback,\n                    "status",\n                    (\n                        f"llama.cpp model ready: {runtime.get(\'id\', resolved_model_name)} "\n                        f"owned_by={runtime.get(\'owned_by\', \'llama.cpp\')}"\n                    ),\n                )\n            self._raise_if_cancelled()\n            self._emit(callback, "status", "Starting model loop.")\n\n            async with AsyncExitStack() as exit_stack:\n                mcp_host: MCPHost | None = None\n                if self._tool_definitions_cache is None:\n                    mcp_host = await exit_stack.enter_async_context(MCPHost(self.workspace))\n                    tools = await mcp_host.list_tools()\n                    self._tool_definitions_cache = build_model_tools(tools)\n                    self._tool_count_cache = len(tools)\n                self._emit(callback, "status", f"Loaded {self._tool_count_cache} MCP tool(s) for the model.")\n                tool_definitions = list(self._tool_definitions_cache or [])\n                messages: list[dict[str, Any]] = [\n                    {\n                        "role": "system",\n                        "content": self._system_prompt(\n                            memory_context,\n                            active_skill_context,\n                            self.history_summary,\n                        ),\n                    }\n                ]\n                messages.extend(self.history[-(HISTORY_TURNS * 2) :])\n                messages.append(\n                    {\n                        "role": "user",\n                        "content": self._user_message(prompt, repo_context, active_skills),\n                    }\n                )\n\n                step_budget, step_budget_reasons = self._adaptive_step_budget(prompt)\n                unlimited_steps = step_budget <= 0\n                if unlimited_steps:\n                    self._emit(callback, "status", "Adaptive tool-step budget for this request: unlimited.")\n                elif step_budget > self.config.max_steps:\n                    reason_text = ", ".join(dict.fromkeys(step_budget_reasons)) or "request complexity"\n                    self._emit(\n                        callback,\n                        "status",\n                        f"Adaptive tool-step budget raised to {step_budget} for this request ({reason_text}).",\n                    )\n                else:\n                    self._emit(callback, "status", f"Adaptive tool-step budget for this request: {step_budget}.")\n\n                repeated_calls: dict[str, int] = {}\n                step_limit_label = "unlimited" if unlimited_steps else str(step_budget)\n                step = 1\n                while unlimited_steps or step <= step_budget:\n                    self._raise_if_cancelled()\n                    self._apply_side_notes(messages, callback)\n                    active_context_tokens = self._estimate_active_context(messages, tool_definitions)\n                    self._emit(\n                        callback,\n                        "status",\n                        (\n                            f"Model step {step}/{step_limit_label}: waiting for the next answer or tool call. "\n                            f"Estimated active context ~{active_context_tokens} tokens, "\n                            f"num_ctx={self.config.num_ctx}, num_predict={self.config.num_predict}."\n                        ),\n                    )\n                    response = llm.chat(\n                        model_name=resolved_model_name,\n                        messages=messages,\n                        tools=tool_definitions,\n                        num_predict=self.config.num_predict,\n                        temperature=self.config.temperature,\n                        top_p=self.config.top_p,\n                        min_p=self.config.min_p,\n                        repeat_penalty=self.config.repeat_penalty,\n                    )\n                    self._raise_if_cancelled()\n                    message = response.get("message") or {}\n                    tool_calls = message.get("tool_calls") or []\n                    content = (message.get("content") or "").strip()\n                    if tool_calls:\n                        assistant_message = {\n                            "role": "assistant",\n                            "content": content,\n                            "tool_calls": tool_calls,\n                        }\n                        messages.append(assistant_message)\n                        for tool_call in tool_calls:\n                            function = tool_call.get("function") or {}\n                            tool_call_id = tool_call.get("id") or ""\n                            name = function.get("name", "").strip()\n                            arguments = clean_arguments(function.get("arguments"))\n                            call_key = json.dumps({"name": name, "arguments": arguments}, sort_keys=True)\n                            repeated_calls[call_key] = repeated_calls.get(call_key, 0) + 1\n                            if repeated_calls[call_key] > 2:\n                                tool_payload = {\n                                    "is_error": True,\n                                    "content_text": f"Refusing repeated tool call for {name} after 2 repeats.",\n                                }\n                            else:\n                                if mcp_host is None:\n                                    mcp_host = await exit_stack.enter_async_context(MCPHost(self.workspace))\n                                self._emit(callback, "tool", describe_tool_action(name, arguments))\n                                self._raise_if_cancelled()\n                                tool_result = await mcp_host.call_tool(name, arguments)\n                                self._raise_if_cancelled()\n                                tool_payload = serialize_mcp_result(tool_result)\n                                self._emit(callback, "tool", summarize_tool_result(name, tool_payload))\n                            messages.append(\n                                {\n                                    "role": "tool",\n                                    "tool_call_id": tool_call_id,\n                                    "content": shorten(\n                                        json.dumps(tool_payload, ensure_ascii=True, indent=2),\n                                        MAX_TOOL_RESULT_CHARS,\n                                    ),\n                                }\n                            )\n                        step += 1\n                        continue\n                    if content:\n                        if self._apply_side_notes(messages, callback, assistant_content=content):\n                            step += 1\n                            continue\n                        self._raise_if_cancelled()\n                        self.memory.add_turn("assistant", content)\n                        self.history.extend(\n                            [\n                                {"role": "user", "content": prompt},\n                                {"role": "assistant", "content": content},\n                            ]\n                        )\n                        self._compact_history()\n                        return content\n                    messages.append({"role": "assistant", "content": ""})\n                    messages.append(\n                        {\n                            "role": "user",\n                            "content": "You returned neither tool calls nor a final answer. Either call a tool or answer directly.",\n                        }\n                    )\n                    step += 1\n            final_error = (\n                "Stopped after reaching the maximum tool steps for this request."\n                if step_budget > 0\n                else "Stopped without a final answer."\n            )\n            self.memory.add_turn("assistant", final_error)\n            return final_error\n        except RunCancelled:\n            final_error = "Interrupted by user."\n            self.memory.add_turn("assistant", final_error)\n            return final_error\n        finally:\n            with self._side_notes_lock:\n                self._pending_side_notes.clear()\n            self._cancel_event.clear()\n            self._active_callback = None\n\n    def run_sync(self, prompt: str, callback: Callable[[str, str], None] | None = None) -> str:\n        with self._side_notes_lock:\n            self._pending_side_notes.clear()\n        self._cancel_event.clear()\n        return asyncio.run(self._run_async(prompt, callback))\n\n\ndef build_mcp_server(workspace: Path) -> FastMCP:\n    workspace = workspace.resolve()\n    memory_dir = workspace / ".memory"\n    memory_dir.mkdir(parents=True, exist_ok=True)\n    current_memory_path = memory_dir / CURRENT_MEMORY_NAME\n    skills = SkillRegistry(workspace)\n    server = FastMCP("Qubitz Local Tools", json_response=True, instructions="Local offline filesystem and workspace tools.")\n\n    def _resolve(candidate: str, *, allow_missing: bool = True, allow_external: bool = False) -> Path:\n        return resolve_workspace_path(\n            workspace,\n            candidate,\n            allow_missing=allow_missing,\n            allow_external=allow_external,\n        )\n\n    def _read_text(candidate: Path) -> str:\n        return candidate.read_text(encoding="utf-8", errors="ignore")\n\n    @server.resource("workspace://summary")\n    def workspace_summary() -> str:\n        return json.dumps(\n            {\n                "workspace": workspace.as_posix(),\n                "memory_file": relative_path(current_memory_path, workspace),\n                "excluded_dirs": sorted(EXCLUDED_DIRS),\n                "skill_count": skills.count(),\n                "skills_root": relative_path(skills.skills_root, workspace),\n                "skill_warnings": skills.warnings,\n            },\n            ensure_ascii=True,\n            indent=2,\n        )\n\n    @server.resource("memory://current")\n    def memory_resource() -> str:\n        if not current_memory_path.exists():\n            return ""\n        return current_memory_path.read_text(encoding="utf-8", errors="ignore")\n\n    @server.resource("skills://index")\n    def skills_index() -> str:\n        return json.dumps(\n            {\n                "skills_root": relative_path(skills.skills_root, workspace),\n                "count": skills.count(),\n                "warnings": skills.warnings,\n                "skills": skills.list_summaries(),\n            },\n            ensure_ascii=True,\n            indent=2,\n        )\n\n    @server.tool(description="List files or directories inside the workspace or at an explicit absolute path.")\n    def list_files(path: str = ".", recursive: bool = False, max_entries: int = 200) -> dict[str, Any]:\n        root = _resolve(path, allow_missing=False, allow_external=True)\n        iterator = root.rglob("*") if recursive else root.iterdir()\n        entries: list[dict[str, Any]] = []\n        for candidate in sorted(iterator):\n            if any(is_excluded_dir_name(part, EXCLUDED_DIRS) for part in candidate.parts if candidate != root):\n                continue\n            entries.append(\n                {\n                    "path": relative_path(candidate, workspace),\n                    "is_dir": candidate.is_dir(),\n                    "size": candidate.stat().st_size if candidate.is_file() else None,\n                }\n            )\n            if len(entries) >= max_entries:\n                break\n        return {"root": relative_path(root, workspace), "entries": entries}\n\n    @server.tool(description="List local Agent Skills discovered under .skills.")\n    def list_skills() -> dict[str, Any]:\n        return {\n            "skills_root": relative_path(skills.skills_root, workspace),\n            "count": skills.count(),\n            "warnings": skills.warnings,\n            "skills": skills.list_summaries(),\n        }\n\n    @server.tool(description="Read a local skill\'s metadata and SKILL.md body.")\n    def read_skill(skill_name: str) -> dict[str, Any]:\n        skill = skills.get(skill_name)\n        summary = skill.to_summary(workspace)\n        summary["body"] = skill.body\n        return summary\n\n    @server.tool(description="Read a file or directory inside a local skill root such as references/, scripts/, or assets/.")\n    def read_skill_resource(skill_name: str, resource_path: str) -> dict[str, Any]:\n        return skills.read_skill_resource(skill_name, resource_path)\n\n    @server.tool(description="Read a text file from the workspace or an explicit absolute path.")\n    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:\n        target = _resolve(path, allow_missing=False, allow_external=True)\n        if not target.is_file():\n            raise ValueError(f"Not a file: {path}")\n        text = _read_text(target)\n        lines = text.splitlines()\n        start_index = max(start_line - 1, 0)\n        end_index = min(end_line, len(lines))\n        excerpt = "\\n".join(lines[start_index:end_index])\n        return {\n            "path": relative_path(target, workspace),\n            "start_line": start_index + 1,\n            "end_line": end_index,\n            "content": excerpt,\n        }\n\n    @server.tool(description="Write or overwrite a file inside the workspace or at an explicit absolute path.")\n    def write_file(path: str, content: str, make_parents: bool = True) -> dict[str, Any]:\n        target = _resolve(path, allow_external=True)\n        if make_parents:\n            target.parent.mkdir(parents=True, exist_ok=True)\n        target.write_text(content, encoding="utf-8")\n        return {"path": relative_path(target, workspace), "bytes_written": target.stat().st_size}\n\n    @server.tool(description="Replace exact text inside a file in the workspace or at an explicit absolute path.")\n    def replace_text(path: str, old_text: str, new_text: str, count: int = 0) -> dict[str, Any]:\n        target = _resolve(path, allow_missing=False, allow_external=True)\n        original = _read_text(target)\n        replacements = original.count(old_text) if count == 0 else min(original.count(old_text), count)\n        updated = original.replace(old_text, new_text, count) if count > 0 else original.replace(old_text, new_text)\n        if updated == original:\n            return {"path": relative_path(target, workspace), "replacements": 0}\n        target.write_text(updated, encoding="utf-8")\n        return {"path": relative_path(target, workspace), "replacements": replacements}\n\n    @server.tool(description="Delete a file or directory inside the workspace or at an explicit absolute path.")\n    def delete_path(path: str, recursive: bool = False) -> dict[str, Any]:\n        target = _resolve(path, allow_missing=False, allow_external=True)\n        if target.is_dir():\n            if recursive:\n                shutil.rmtree(target)\n            else:\n                target.rmdir()\n        else:\n            target.unlink()\n        return {"deleted": relative_path(target, workspace), "recursive": recursive}\n\n    @server.tool(description="Create a directory inside the workspace or at an explicit absolute path.")\n    def make_directory(path: str) -> dict[str, Any]:\n        target = _resolve(path, allow_external=True)\n        target.mkdir(parents=True, exist_ok=True)\n        return {"path": relative_path(target, workspace), "created": True}\n\n    @server.tool(description="Move or rename a path inside the workspace or at an explicit absolute path.")\n    def move_path(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:\n        source_path = _resolve(source, allow_missing=False, allow_external=True)\n        destination_path = _resolve(destination, allow_external=True)\n        destination_path.parent.mkdir(parents=True, exist_ok=True)\n        if destination_path.exists():\n            if not overwrite:\n                raise ValueError(f"Destination already exists: {destination}")\n            if destination_path.is_dir():\n                shutil.rmtree(destination_path)\n            else:\n                destination_path.unlink()\n        shutil.move(str(source_path), str(destination_path))\n        return {\n            "source": relative_path(source_path, workspace),\n            "destination": relative_path(destination_path, workspace),\n        }\n\n    @server.tool(description="Search text content inside workspace files or under an explicit absolute path.")\n    def search_text(\n        query: str,\n        path: str = ".",\n        file_glob: str = "*",\n        max_results: int = 50,\n        case_sensitive: bool = False,\n    ) -> dict[str, Any]:\n        root = _resolve(path, allow_missing=False, allow_external=True)\n        hits: list[dict[str, Any]] = []\n        needle = query if case_sensitive else query.lower()\n        for candidate in sorted(root.rglob("*")):\n            if not candidate.is_file():\n                continue\n            if any(is_excluded_dir_name(part, EXCLUDED_DIRS) for part in candidate.parts):\n                continue\n            if not candidate.match(file_glob) and not Path(relative_path(candidate, workspace)).match(file_glob):\n                continue\n            if not is_probably_text_file(candidate):\n                continue\n            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()\n            for index, line in enumerate(lines, start=1):\n                haystack = line if case_sensitive else line.lower()\n                if needle in haystack:\n                    hits.append(\n                        {\n                            "path": relative_path(candidate, workspace),\n                            "line": index,\n                            "content": line.strip(),\n                        }\n                    )\n                    if len(hits) >= max_results:\n                        return {"query": query, "matches": hits}\n        return {"query": query, "matches": hits}\n\n    @server.tool(description="Install Python packages into the preferred workspace project-local virtual environment. Supports direct URLs, Git URLs, local paths, wheel files, and optional extra pip/uv arguments such as --index-url or --find-links.")\n    def install_python_package(\n        packages: list[str] | None = None,\n        requirements_file: str | None = None,\n        upgrade: bool = False,\n        pip_args: list[str] | None = None,\n    ) -> dict[str, Any]:\n        python_executable = preferred_project_python(workspace)\n        if python_executable is None:\n            raise FileNotFoundError("No supported project-local Python interpreter was found in the workspace.")\n        if not packages and not requirements_file:\n            raise ValueError("Provide packages and/or requirements_file.")\n        if shutil.which("uv") and python_executable.suffix.lower() != ".exe":\n            command = ["uv", "pip", "install", "--python", str(python_executable)]\n        else:\n            command = [str(python_executable), "-m", "pip", "install"]\n        if upgrade:\n            command.append("--upgrade")\n        if requirements_file:\n            requirements_path = _resolve(requirements_file, allow_missing=False)\n            command.extend(["-r", str(requirements_path)])\n        if pip_args:\n            command.extend(str(item) for item in pip_args if str(item).strip())\n        if packages:\n            command.extend(packages)\n        result = subprocess.run(\n            command,\n            cwd=workspace,\n            capture_output=True,\n            timeout=1800,\n            check=False,\n        )\n        return {\n            "command": command,\n            "return_code": result.returncode,\n            "stdout": shorten(decode_subprocess_output(result.stdout), 4000),\n            "stderr": shorten(decode_subprocess_output(result.stderr), 4000),\n        }\n\n    @server.tool(description="Run a bounded project command without using a shell. Project-local Python interpreters such as .venv/bin/python and .venv312/Scripts/python.exe are allowed.")\n    def run_project_command(command: list[str], cwd: str = ".", timeout_seconds: int = 300) -> dict[str, Any]:\n        normalized_command = resolve_allowed_project_command(workspace, command)\n        target_cwd = _resolve(cwd, allow_missing=False)\n        result = subprocess.run(\n            normalized_command,\n            cwd=target_cwd,\n            capture_output=True,\n            timeout=timeout_seconds,\n            check=False,\n        )\n        return {\n            "command": normalized_command,\n            "cwd": relative_path(target_cwd, workspace),\n            "return_code": result.returncode,\n            "stdout": shorten(decode_subprocess_output(result.stdout), 4000),\n            "stderr": shorten(decode_subprocess_output(result.stderr), 4000),\n        }\n\n    @server.tool(description="Run a bounded PowerShell command inside the workspace. Prefer this for Windows-backed workspaces, Activate.ps1, and other PowerShell-specific tasks.")\n    def run_powershell_command(command: str, cwd: str = ".", timeout_seconds: int = 300) -> dict[str, Any]:\n        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")\n        if powershell is None:\n            raise RuntimeError("PowerShell was not found on this system.")\n        target_cwd = _resolve(cwd, allow_missing=False)\n        script_body = canonicalize_workspace_script(target_cwd, extract_powershell_script(command))\n        cwd_text = wsl_path_to_windows_path(target_cwd) if in_wsl() and powershell.lower().endswith(".exe") else str(target_cwd)\n        script_lines = [\n            "$ProgressPreference = \'SilentlyContinue\'",\n            f"Set-Location -LiteralPath {powershell_single_quote(cwd_text)}",\n            script_body,\n        ]\n        result = subprocess.run(\n            [powershell, "-NoProfile", "-Command", "; ".join(line for line in script_lines if line)],\n            capture_output=True,\n            timeout=timeout_seconds,\n            check=False,\n        )\n        return {\n            "command": command,\n            "cwd": relative_path(target_cwd, workspace),\n            "return_code": result.returncode,\n            "stdout": shorten(decode_subprocess_output(result.stdout), 4000),\n            "stderr": shorten(decode_subprocess_output(result.stderr), 4000),\n        }\n\n    @server.tool(description="Read the current persistent memory file.")\n    def read_memory() -> dict[str, Any]:\n        text = current_memory_path.read_text(encoding="utf-8", errors="ignore") if current_memory_path.exists() else ""\n        return {"path": relative_path(current_memory_path, workspace), "content": shorten(text, 6000)}\n\n    @server.tool(description="Search across memory markdown files.")\n    def search_memory(query: str, max_results: int = 5) -> dict[str, Any]:\n        query_tokens = token_set(query)\n        matches: list[dict[str, Any]] = []\n        for candidate in sorted(memory_dir.glob("MEMORY*.md"), key=lambda item: item.stat().st_mtime, reverse=True):\n            text = candidate.read_text(encoding="utf-8", errors="ignore")\n            lowered = text.lower()\n            score = sum(lowered.count(token) for token in query_tokens)\n            if score <= 0:\n                continue\n            matches.append(\n                {\n                    "path": relative_path(candidate, workspace),\n                    "score": score,\n                    "snippet": shorten(text, 1200),\n                }\n            )\n            if len(matches) >= max_results:\n                break\n        return {"query": query, "matches": matches}\n\n    return server\n\n\nclass QubitzGUI:\n    def __init__(self, config: AgentConfig) -> None:\n        tk, ttk, scrolledtext, messagebox, filedialog = import_tk_modules()\n        self.tk = tk\n        self.ttk = ttk\n        self.scrolledtext = scrolledtext\n        self.messagebox = messagebox\n        self.filedialog = filedialog\n        self.config = config\n        self.agent = AgentRunner(config)\n        self.root = tk.Tk()\n        self.root.title("AI Agent Qubitz")\n        self.root.geometry("800x800")\n        self.root.minsize(800, 800)\n        self.root.protocol("WM_DELETE_WINDOW", self._handle_close)\n        self.root.configure(bg=UI_BG, bd=0, highlightthickness=0)\n        self._apply_theme()\n        self.status_var = tk.StringVar(master=self.root, value="Ready")\n        self.num_ctx_var = tk.StringVar(master=self.root, value=str(self.config.num_ctx))\n        self.num_predict_var = tk.StringVar(master=self.root, value=str(self.config.num_predict))\n        self.event_queue: queue.Queue[tuple[str, str]] = queue.Queue()\n        self.busy = False\n        self.cancel_requested = False\n        self.pending_prompts: list[str] = []\n        self._build_layout()\n        self.root.bind("<Escape>", self._handle_cancel_shortcut)\n        self.root.bind_all("<Control-c>", self._handle_copy_shortcut, add="+")\n        self.root.bind_all("<Control-C>", self._handle_copy_shortcut, add="+")\n        self.root.after_idle(self._present_window)\n        self._append_transcript("system", f"Workspace: {config.workspace.as_posix()}")\n        self._append_transcript("system", f"Model alias: {config.model_name}")\n        self._append_transcript("system", f"GGUF model: {DEFAULT_GGUF_MODEL_LABEL} ({config.selected_model_filename})")\n        self._append_transcript("system", f"Embedding Model: {config.embed_model_name}")\n        self._append_transcript(\n            "system",\n            f"Runtime defaults: num_ctx={config.num_ctx}, num_predict={config.num_predict}.",\n        )\n        self._append_transcript(\n            "system",\n            "The agent uses a local llama.cpp GGUF backend plus retrieval, FAISS, and local tools for reading, editing, deleting, installing, and running bounded commands.",\n        )\n        self._append_transcript("system", f"Local skills discovered: {self.agent.skills.count()}")\n        for warning in self.agent.skills.warnings:\n            self._append_transcript("status", f"Skill warning: {warning}")\n        self.root.after(100, self._poll_events)\n\n    def _apply_theme(self) -> None:\n        self.root.configure(bg=UI_BG)\n        self.root.option_add("*Background", UI_BG)\n        self.root.option_add("*Foreground", UI_TEXT)\n        self.root.option_add("*selectBackground", UI_SELECT)\n        self.root.option_add("*selectForeground", UI_TEXT)\n        style = self.ttk.Style(self.root)\n        style.theme_use("clam")\n        style.configure(".", background=UI_BG, foreground=UI_TEXT)\n        style.configure("TFrame", background=UI_BG)\n        style.configure("TLabel", background=UI_BG, foreground=UI_TEXT)\n        style.configure(\n            "TEntry",\n            fieldbackground=UI_PANEL,\n            foreground=UI_TEXT,\n            bordercolor=UI_BORDER,\n            lightcolor=UI_PANEL,\n            darkcolor=UI_PANEL,\n            insertcolor=UI_TEXT,\n            padding=(6, 6),\n        )\n        style.map(\n            "TEntry",\n            fieldbackground=[("disabled", UI_PANEL), ("readonly", UI_PANEL)],\n            foreground=[("disabled", UI_TEXT_MUTED)],\n        )\n        style.configure(\n            "TButton",\n            background=UI_PANEL_ALT,\n            foreground=UI_TEXT,\n            bordercolor=UI_BORDER,\n            lightcolor=UI_PANEL_ALT,\n            darkcolor=UI_PANEL_ALT,\n            focuscolor=UI_PANEL_ALT,\n            padding=(10, 8),\n        )\n        style.map(\n            "TButton",\n            background=[("active", UI_SELECT), ("pressed", UI_SELECT), ("disabled", UI_PANEL)],\n            foreground=[("disabled", UI_TEXT_MUTED)],\n        )\n\n    def _maximize_window(self) -> None:\n        if in_wsl():\n            self._fit_window_to_screen()\n            return\n        try:\n            self.root.state("zoomed")\n            return\n        except self.tk.TclError:\n            pass\n        try:\n            self.root.wm_attributes("-zoomed", True)\n            return\n        except self.tk.TclError:\n            pass\n        self.root.update_idletasks()\n        width = max(800, self.root.winfo_screenwidth())\n        height = max(800, self.root.winfo_screenheight())\n        self.root.geometry(f"{width}x{height}+0+0")\n\n    def _fit_window_to_screen(self) -> None:\n        self.root.update_idletasks()\n        screen_width = max(800, self.root.winfo_screenwidth())\n        screen_height = max(800, self.root.winfo_screenheight())\n        width = min(max(1100, screen_width - 120), screen_width)\n        height = min(max(800, screen_height - 120), screen_height)\n        x = max(0, (screen_width - width) // 2)\n        y = max(0, (screen_height - height) // 2)\n        try:\n            self.root.state("normal")\n        except self.tk.TclError:\n            pass\n        self.root.geometry(f"{width}x{height}+{x}+{y}")\n\n    def _present_window(self) -> None:\n        try:\n            self.root.deiconify()\n        except self.tk.TclError:\n            pass\n        self._maximize_window()\n        self.root.update_idletasks()\n        try:\n            self.root.lift()\n        except self.tk.TclError:\n            pass\n        try:\n            self.root.attributes("-topmost", True)\n            self.root.after(250, lambda: self.root.attributes("-topmost", False))\n        except self.tk.TclError:\n            pass\n        try:\n            self.root.focus_force()\n        except self.tk.TclError:\n            pass\n        self.root.after(500, self._ensure_window_visible)\n        self.root.after(1500, self._ensure_window_visible)\n\n    def _ensure_window_visible(self) -> None:\n        self.root.update_idletasks()\n        try:\n            x = self.root.winfo_x()\n            y = self.root.winfo_y()\n            width = max(1, self.root.winfo_width())\n            height = max(1, self.root.winfo_height())\n            screen_width = max(800, self.root.winfo_screenwidth())\n            screen_height = max(800, self.root.winfo_screenheight())\n        except self.tk.TclError:\n            return\n        if (\n            x < 0\n            or y < 0\n            or width <= 1\n            or height <= 1\n            or x >= screen_width\n            or y >= screen_height\n            or x + min(width, 120) > screen_width\n            or y + min(height, 120) > screen_height\n        ):\n            try:\n                self._fit_window_to_screen()\n                self.root.lift()\n            except self.tk.TclError:\n                pass\n\n    def _build_layout(self) -> None:\n        frame = self.ttk.Frame(self.root, padding=10)\n        frame.pack(fill="both", expand=True)\n\n        header = self.ttk.Frame(frame)\n        header.pack(fill="x", pady=(0, 10))\n        title = self.ttk.Label(header, text="AI Agent Qubitz", font=("TkDefaultFont", 14, "bold"))\n        title.pack(side="left")\n        header_actions = self.ttk.Frame(header)\n        header_actions.pack(side="right")\n        self.change_workspace_button = self.ttk.Button(\n            header_actions,\n            text="Change Workspace",\n            command=self._change_workspace,\n        )\n        self.change_workspace_button.pack(side="left", padx=(0, 10))\n        self.ttk.Label(header_actions, textvariable=self.status_var).pack(side="left")\n\n        controls = self.ttk.Frame(frame)\n        controls.pack(fill="x", pady=(0, 10))\n        self.ttk.Label(controls, text=f"GGUF {DEFAULT_GGUF_MODEL_LABEL}").pack(side="left")\n        self.ttk.Label(controls, text="Context").pack(side="left")\n        self.num_ctx_entry = self.ttk.Entry(controls, textvariable=self.num_ctx_var, width=10)\n        self.num_ctx_entry.pack(side="left", padx=(6, 16))\n        self.ttk.Label(controls, text="Max Output").pack(side="left")\n        self.num_predict_entry = self.ttk.Entry(controls, textvariable=self.num_predict_var, width=10)\n        self.num_predict_entry.pack(side="left", padx=(6, 0))\n\n        self.transcript = self.scrolledtext.ScrolledText(frame, wrap="word", state="disabled", height=30)\n        self.transcript.configure(\n            background=UI_PANEL,\n            foreground=UI_TEXT,\n            insertbackground=UI_TEXT,\n            selectbackground=UI_SELECT,\n            selectforeground=UI_TEXT,\n            highlightbackground=UI_BORDER,\n            highlightcolor=UI_BORDER,\n            highlightthickness=1,\n            borderwidth=0,\n            relief="flat",\n        )\n        self.transcript.vbar.configure(\n            background=UI_PANEL_ALT,\n            troughcolor=UI_BG,\n            activebackground=UI_SELECT,\n            highlightbackground=UI_BG,\n        )\n        self.transcript.pack(fill="both", expand=True)\n        self.transcript.bind("<Control-c>", self._handle_copy_shortcut)\n        self.transcript.bind("<Control-C>", self._handle_copy_shortcut)\n\n        composer = self.ttk.Frame(frame)\n        composer.pack(fill="both", pady=(10, 0))\n        self.prompt_box = self.tk.Text(composer, wrap="word", height=8)\n        self.prompt_box.configure(\n            background=UI_PANEL,\n            foreground=UI_TEXT,\n            insertbackground=UI_TEXT,\n            selectbackground=UI_SELECT,\n            selectforeground=UI_TEXT,\n            highlightbackground=UI_BORDER,\n            highlightcolor=UI_BORDER,\n            highlightthickness=1,\n            borderwidth=0,\n            relief="flat",\n        )\n        self.prompt_box.pack(fill="both", expand=True, side="left")\n        self.prompt_box.bind("<Return>", self._handle_send_shortcut)\n        self.prompt_box.bind("<Shift-Return>", self._handle_newline_shortcut)\n        self.prompt_box.bind("<Escape>", self._handle_cancel_shortcut)\n        self.prompt_box.bind("<Control-c>", self._handle_copy_shortcut)\n        self.prompt_box.bind("<Control-C>", self._handle_copy_shortcut)\n\n        buttons = self.ttk.Frame(composer)\n        buttons.pack(fill="y", side="left", padx=(10, 0))\n        self.send_button = self.ttk.Button(buttons, text="Send", command=self.send_prompt)\n        self.send_button.pack(fill="x")\n        self.clear_button = self.ttk.Button(buttons, text="Clear", command=self.clear_input)\n        self.clear_button.pack(fill="x", pady=(8, 0))\n\n    def _append_transcript(self, role: str, message: str) -> None:\n        self.transcript.configure(state="normal")\n        self.transcript.insert("end", f"[{role}] {message.strip()}\\n\\n")\n        self.transcript.configure(state="disabled")\n        self.transcript.see("end")\n\n    def _set_busy(self, value: bool) -> None:\n        self.busy = value\n        self.cancel_requested = False\n        self._update_send_button()\n        self.clear_button.configure(state="normal")\n        self.change_workspace_button.configure(state="disabled" if value else "normal")\n        self.prompt_box.configure(state="normal")\n        settings_state = "disabled" if value else "normal"\n        self.num_ctx_entry.configure(state=settings_state)\n        self.num_predict_entry.configure(state=settings_state)\n        self.status_var.set("Working (Esc to cancel)" if value else "Ready")\n\n    def _update_send_button(self) -> None:\n        if self.busy:\n            label = f"Queue ({len(self.pending_prompts)})" if self.pending_prompts else "Queue"\n        else:\n            label = "Send"\n        self.send_button.configure(text=label, state="normal")\n\n    def _sync_runtime_settings(self) -> None:\n        try:\n            num_ctx = int(self.num_ctx_var.get().strip())\n            num_predict = int(self.num_predict_var.get().strip())\n        except ValueError as exc:\n            raise ValueError("Context and Max Output must be integers.") from exc\n        if num_ctx <= 0 or num_predict <= 0:\n            raise ValueError("Context and Max Output must be positive integers.")\n        self.config.num_ctx = num_ctx\n        self.config.num_predict = num_predict\n\n    def clear_input(self) -> None:\n        self.prompt_box.delete("1.0", "end")\n\n    def _change_workspace(self) -> None:\n        if self.busy:\n            self.messagebox.showinfo(\n                "AI Agent Qubitz",\n                "Wait for the current task to finish or cancel it before changing the workspace.",\n            )\n            return\n        selected = pick_workspace_directory(self.config.workspace, self.filedialog, parent=self.root)\n        if selected is None:\n            return\n        workspace = selected.resolve()\n        if not workspace.exists():\n            self.messagebox.showerror("AI Agent Qubitz", f"Selected workspace does not exist:\\n{workspace}")\n            return\n        if not workspace.is_dir():\n            self.messagebox.showerror("AI Agent Qubitz", f"Selected path is not a directory:\\n{workspace}")\n            return\n        if workspace == self.config.workspace.resolve():\n            self._append_transcript("status", "Workspace unchanged.")\n            self.prompt_box.focus_set()\n            return\n        self.status_var.set("Switching workspace")\n        candidate_config = replace(\n            self.config,\n            workspace=workspace,\n            runtime_workspace=self.agent.runtime_workspace,\n        )\n        try:\n            new_agent = AgentRunner(candidate_config)\n        except Exception as exc:\n            self.status_var.set("Ready")\n            self.messagebox.showerror(\n                "AI Agent Qubitz",\n                f"Failed to switch workspace to:\\n{workspace}\\n\\n{exc}",\n            )\n            return\n        configure_project_environment(new_agent.runtime_workspace)\n        try:\n            self.agent.reset_runtime()\n        except Exception as exc:\n            self._append_transcript("status", f"Workspace switch warning: failed to reset the old runtime: {exc}")\n        self.config = candidate_config\n        self.agent = new_agent\n        self.pending_prompts.clear()\n        self.cancel_requested = False\n        self._update_send_button()\n        self.status_var.set("Ready")\n        self._append_transcript("status", f"Workspace changed to {workspace.as_posix()}")\n        self._append_transcript("system", f"Workspace: {workspace.as_posix()}")\n        self._append_transcript("system", f"Local skills discovered: {self.agent.skills.count()}")\n        for warning in self.agent.skills.warnings:\n            self._append_transcript("status", f"Skill warning: {warning}")\n        self.prompt_box.focus_set()\n\n    def _handle_close(self) -> None:\n        self.agent.reset_runtime()\n        self.root.destroy()\n\n    def _handle_send_shortcut(self, _event: Any) -> str:\n        self.send_prompt()\n        return "break"\n\n    def _handle_copy_shortcut(self, event: Any) -> str | None:\n        def _selected_text(widget: Any) -> str:\n            try:\n                return widget.get("sel.first", "sel.last")\n            except Exception:\n                pass\n            try:\n                if hasattr(widget, "selection_present") and not widget.selection_present():\n                    return ""\n                return widget.selection_get()\n            except Exception:\n                return ""\n\n        candidates: list[Any] = []\n        widget = getattr(event, "widget", None)\n        if widget is not None:\n            candidates.append(widget)\n        try:\n            focused = self.root.focus_get()\n        except Exception:\n            focused = None\n        if focused is not None and focused not in candidates:\n            candidates.append(focused)\n        selected_text = ""\n        for candidate in candidates:\n            selected_text = _selected_text(candidate)\n            if selected_text:\n                break\n        if not selected_text:\n            try:\n                selected_text = self.root.selection_get()\n            except Exception:\n                return None\n        if not selected_text:\n            return None\n        self.root.clipboard_clear()\n        self.root.clipboard_append(selected_text)\n        self.root.update_idletasks()\n        return "break"\n\n    def _handle_newline_shortcut(self, _event: Any) -> str:\n        self.prompt_box.insert("insert", "\\n")\n        return "break"\n\n    def _handle_cancel_shortcut(self, _event: Any) -> str:\n        self._request_cancel()\n        return "break"\n\n    def _request_cancel(self) -> None:\n        if not self.busy or self.cancel_requested:\n            return\n        self.cancel_requested = True\n        self.status_var.set("Cancelling")\n        self.agent.request_cancel()\n\n    def _start_prompt(self, prompt: str, *, queued: bool = False) -> None:\n        if queued:\n            remaining = len(self.pending_prompts)\n            remaining_text = f"{remaining} more queued after this." if remaining else "Queue will be empty after this task."\n            self._append_transcript("status", f"Starting queued task. {remaining_text}")\n        self._append_transcript("user", prompt)\n        self._set_busy(True)\n        worker = threading.Thread(target=self._worker_run, args=(prompt,), daemon=True)\n        worker.start()\n\n    def send_prompt(self) -> None:\n        raw_prompt = self.prompt_box.get("1.0", "end").strip()\n        if not raw_prompt:\n            return\n        is_side_note = raw_prompt.lower() == "/btw" or raw_prompt.lower().startswith("/btw ")\n        prompt = raw_prompt[4:].strip() if is_side_note else raw_prompt\n        if not prompt:\n            return\n        if self.busy and is_side_note:\n            self.prompt_box.delete("1.0", "end")\n            if self.agent.submit_side_note(prompt):\n                self._append_transcript("btw", prompt)\n                self._append_transcript("status", "Attached /btw update to the active task.")\n                return\n        if not self.busy:\n            try:\n                self._sync_runtime_settings()\n            except ValueError as exc:\n                self.messagebox.showerror("AI Agent Qubitz", str(exc))\n                return\n        self.prompt_box.delete("1.0", "end")\n        if self.busy:\n            self.pending_prompts.append(prompt)\n            self._append_transcript("queued", raw_prompt)\n            self._append_transcript("status", f"Queued task to run next ({len(self.pending_prompts)} waiting).")\n            self._update_send_button()\n            return\n        self._start_prompt(prompt)\n\n    def _worker_emit(self, kind: str, message: str) -> None:\n        self.event_queue.put((kind, message))\n\n    def _worker_run(self, prompt: str) -> None:\n        try:\n            answer = self.agent.run_sync(prompt, self._worker_emit)\n        except Exception as exc:  # pragma: no cover - runtime GUI path\n            details = "".join(traceback.format_exception(exc))\n            self.event_queue.put(("error", details))\n        else:\n            self.event_queue.put(("answer", answer))\n        finally:\n            self.event_queue.put(("done", ""))\n\n    def _poll_events(self) -> None:\n        while True:\n            try:\n                kind, message = self.event_queue.get_nowait()\n            except queue.Empty:\n                break\n            if kind == "status":\n                self.status_var.set(message)\n                self._append_transcript("status", message)\n            elif kind == "tool":\n                self._append_transcript("tool", message)\n            elif kind == "answer":\n                self._append_transcript("assistant", message)\n            elif kind == "error":\n                self._append_transcript("error", message)\n                self.messagebox.showerror("AI Agent Qubitz", message)\n            elif kind == "done":\n                self._set_busy(False)\n                if self.pending_prompts:\n                    next_prompt = self.pending_prompts.pop(0)\n                    self._update_send_button()\n                    self._start_prompt(next_prompt, queued=True)\n        self.root.after(100, self._poll_events)\n\n    def run(self) -> None:\n        self.root.mainloop()\n\n\ndef run_cli(config: AgentConfig, initial_prompt: str | None = None) -> None:\n    runner = AgentRunner(config)\n\n    def emit(kind: str, message: str) -> None:\n        print(f"[{kind}] {message}")\n\n    if initial_prompt:\n        print(runner.run_sync(initial_prompt, emit))\n        return\n    print("AI Agent Qubitz CLI. Type \'exit\' to stop.")\n    while True:\n        try:\n            prompt = input("> ").strip()\n        except EOFError:\n            print()\n            break\n        if prompt.lower() in {"exit", "quit"}:\n            break\n        if not prompt:\n            continue\n        answer = runner.run_sync(prompt, emit)\n        print(answer)\n\n\ndef serve_mcp(workspace: Path) -> None:\n    server = build_mcp_server(workspace)\n    server.run(transport="stdio")\n\n\ndef parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:\n    parser = argparse.ArgumentParser(description="AI Agent Qubitz")\n    parser.add_argument("--workspace", default=".", help="Workspace root. Defaults to the current working directory.")\n    parser.add_argument("--model", default=DEFAULT_MODEL, help="Served model alias to send to llama.cpp.")\n    parser.add_argument(\n        "--model-path",\n        help=(\n            "Local path to the fixed Q4 GGUF model. Must point to "\n            f"{DEFAULT_GGUF_MODEL_FILENAME}. Defaults to the {GGUF_MODEL_PATH_ENV_NAME} "\n            f"environment variable or models/{DEFAULT_GGUF_MODEL_FILENAME}."\n        ),\n    )\n    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help="Embedding model name.")\n    parser.add_argument(\n        "--server-url",\n        default=None,\n        help=(\n            "llama.cpp OpenAI-compatible base URL. "\n            f"Defaults to {DEFAULT_LLAMACPP_BASE_URL} or {LLAMACPP_SERVER_URL_ENV_NAME}."\n        ),\n    )\n    parser.add_argument(\n        "--llama-server",\n        help=(\n            "Path to the llama-server executable. "\n            f"Defaults to {LLAMACPP_SERVER_PATH_ENV_NAME} or PATH lookup."\n        ),\n    )\n    parser.add_argument("--num-ctx", type=int, default=DEFAULT_NUM_CTX, help="Context window to request for each chat call.")\n    parser.add_argument(\n        "--num-predict",\n        type=int,\n        default=DEFAULT_NUM_PREDICT,\n        help="Maximum output tokens to request from llama.cpp for each chat call.",\n    )\n    parser.add_argument(\n        "--max-steps",\n        type=int,\n        default=env_int(MAX_STEPS_ENV_NAME, MAX_TOOL_STEPS),\n        help=f"Maximum model tool steps. Use 0 or a negative value for unlimited. Defaults to {MAX_STEPS_ENV_NAME} or {MAX_TOOL_STEPS}.",\n    )\n    parser.add_argument("--cli", action="store_true", help="Run the terminal interface instead of the Tk GUI.")\n    parser.add_argument("--prompt", help="Run a single CLI prompt and exit.")\n    parser.add_argument(\n        "--generate-harness-key",\n        action="store_true",\n        help=f"Generate a Fernet key suitable for {HARNESS_KEY_ENV_NAME}, then exit.",\n    )\n    parser.add_argument(\n        "--encrypt-harness",\n        action="store_true",\n        help=(\n            f"Encrypt {DEFAULT_HARNESS_NAME} into {DEFAULT_ENCRYPTED_HARNESS_NAME} "\n            f"using the {HARNESS_KEY_ENV_NAME} environment variable, then exit."\n        ),\n    )\n    parser.add_argument("--serve-mcp", action="store_true", help="Run the local MCP server over stdio.")\n    return parser.parse_args(argv)\n\n\ndef main(argv: Sequence[str] | None = None) -> None:\n    args = parse_args(argv)\n    workspace = Path(args.workspace).resolve()\n    configure_project_environment(workspace)\n    ensure_display_environment()\n    config = AgentConfig(\n        workspace=workspace,\n        model_name=args.model,\n        model_path=args.model_path or os.environ.get(GGUF_MODEL_PATH_ENV_NAME),\n        selected_model_filename=DEFAULT_GGUF_MODEL_FILENAME,\n        server_url=normalize_base_url(args.server_url or os.environ.get(LLAMACPP_SERVER_URL_ENV_NAME)),\n        llama_server_path=args.llama_server,\n        embed_model_name=args.embed_model,\n        max_steps=args.max_steps,\n        num_ctx=args.num_ctx,\n        num_predict=args.num_predict,\n    )\n    if args.generate_harness_key:\n        print(generate_harness_key())\n        return\n    if args.encrypt_harness:\n        encrypted_path = write_encrypted_harness(workspace)\n        print(f"Wrote encrypted harness to {encrypted_path}")\n        return\n    if args.serve_mcp:\n        serve_mcp(workspace)\n        return\n    if args.cli or (not os.environ.get("DISPLAY") and sys.platform.startswith("linux")):\n        run_cli(config, initial_prompt=args.prompt)\n        return\n    gui = QubitzGUI(config)\n    gui.run()\n\n\nif __name__ == "__main__":\n    main()\n'


_EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(
    "        import torch\n"
    "        import torch.nn.functional as torch_f\n"
    "        from transformers import AutoModel, AutoTokenizer\n",
    "        import torch\n"
    "        import torch.nn.functional as torch_f\n"
    "        import transformers.modeling_utils as transformers_modeling_utils\n"
    "        from transformers.pytorch_utils import Conv1D as TransformersConv1D\n"
    "        if not hasattr(transformers_modeling_utils, \"Conv1D\"):\n"
    "            transformers_modeling_utils.Conv1D = TransformersConv1D\n"
    "        from transformers import AutoModel, AutoTokenizer\n",
)
_EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(
    "        except Exception as exc:\n"
    "            if target_device != \"cuda\":\n"
    "                raise\n"
    "            self.load_warning = f\"CUDA embedder load failed, falling back to CPU: {type(exc).__name__}: {exc}\"\n"
    "            self._report(self.load_warning)\n"
    "            if torch.cuda.is_available():\n"
    "                torch.cuda.empty_cache()\n"
    "            self._model = self._load_model(AutoModel, base_load_kwargs)\n"
    "            self.device = \"cpu\"\n"
    "            load_kwargs = dict(base_load_kwargs)\n",
    "        except Exception as exc:\n"
    "            if target_device != \"cuda\":\n"
    "                raise\n"
    "            if load_kwargs.get(\"attn_implementation\") == \"flash_attention_2\":\n"
    "                self._report(\n"
    "                    f\"CUDA embedder load with FlashAttention2 failed: {type(exc).__name__}: {exc}; retrying CUDA without FlashAttention2.\"\n"
    "                )\n"
    "                if torch.cuda.is_available():\n"
    "                    torch.cuda.empty_cache()\n"
    "                load_kwargs = dict(base_load_kwargs)\n"
    "                load_kwargs[\"dtype\"] = torch.float16\n"
    "                try:\n"
    "                    self._model = self._load_model(AutoModel, load_kwargs)\n"
    "                    self.device = target_device\n"
    "                except Exception as retry_exc:\n"
    "                    self.load_warning = f\"CUDA embedder load failed, falling back to CPU: {type(retry_exc).__name__}: {retry_exc}\"\n"
    "                    self._report(self.load_warning)\n"
    "                    if torch.cuda.is_available():\n"
    "                        torch.cuda.empty_cache()\n"
    "                    self._model = self._load_model(AutoModel, base_load_kwargs)\n"
    "                    self.device = \"cpu\"\n"
    "                    load_kwargs = dict(base_load_kwargs)\n"
    "            else:\n"
    "                self.load_warning = f\"CUDA embedder load failed, falling back to CPU: {type(exc).__name__}: {exc}\"\n"
    "                self._report(self.load_warning)\n"
    "                if torch.cuda.is_available():\n"
    "                    torch.cuda.empty_cache()\n"
    "                self._model = self._load_model(AutoModel, base_load_kwargs)\n"
    "                self.device = \"cpu\"\n"
    "                load_kwargs = dict(base_load_kwargs)\n",
)


_EMBEDDED_POWERSHELL_LAUNCH_OLD = """        if in_wsl() and popen_command and popen_command[0].lower() == "powershell.exe":
            self.process = subprocess.Popen(
                render_windows_command_for_wsl_shell(popen_command),
                cwd=self.runtime_workspace,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                shell=True,
                executable="/bin/bash",
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
            return
"""
_EMBEDDED_POWERSHELL_LAUNCH_NEW = """        if in_wsl() and popen_command and popen_command[0].lower() == "powershell.exe":
            powershell = shutil.which("powershell.exe") or popen_command[0]
            popen_command[0] = powershell
            if Path("/init").is_file():
                popen_command.insert(0, "/init")
            else:
                self.process = subprocess.Popen(
                    render_windows_command_for_wsl_shell(popen_command),
                    cwd=self.runtime_workspace,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    shell=True,
                    executable="/bin/bash",
                    creationflags=creationflags,
                    startupinfo=startupinfo,
                )
                return
"""
if _EMBEDDED_BASE_SOURCE.count(_EMBEDDED_POWERSHELL_LAUNCH_OLD) != 1:
    raise RuntimeError("Embedded llama-server PowerShell launch block was not found exactly once.")
_EMBEDDED_BASE_SOURCE = _EMBEDDED_BASE_SOURCE.replace(
    _EMBEDDED_POWERSHELL_LAUNCH_OLD,
    _EMBEDDED_POWERSHELL_LAUNCH_NEW,
)


def _load_embedded_base_module() -> Any:
    internal_name = f"{_EMBEDDED_BASE_MODULE_LABEL}__embedded__{Path(__file__).stem}"
    existing = sys.modules.get(internal_name)
    if existing is not None:
        return existing
    module = types.ModuleType(internal_name)
    module.__file__ = str(Path(__file__).resolve())
    module.__package__ = ""
    module.__dict__["__builtins__"] = __builtins__
    sys.modules[internal_name] = module
    code = compile(_EMBEDDED_BASE_SOURCE, f"{_EMBEDDED_BASE_MODULE_LABEL}.py", "exec")
    exec(code, module.__dict__)
    return module


def _patch_harness_loader(base: Any) -> None:
    def _load_harness_text(workspace: Path) -> str:
        encrypted_name = getattr(base, "DEFAULT_ENCRYPTED_HARNESS_NAME", "HARNESS.enc")
        plaintext_name = getattr(base, "DEFAULT_HARNESS_NAME", "HARNESS.txt")
        encrypted_path = workspace / encrypted_name
        plaintext_path = workspace / plaintext_name
        if plaintext_path.exists():
            return plaintext_path.read_text(encoding="utf-8", errors="ignore")
        if encrypted_path.exists():
            return base.decrypt_harness_bytes(encrypted_path.read_bytes(), workspace)
        raise FileNotFoundError(
            f"Missing harness file. Expected {encrypted_name} or {plaintext_name} in {workspace}."
        )

    excluded = set(getattr(base, "EXCLUDED_RETRIEVAL_FILENAMES", set()))
    excluded.add(getattr(base, "DEFAULT_ENCRYPTED_HARNESS_NAME", "HARNESS.enc"))
    base.EXCLUDED_RETRIEVAL_FILENAMES = excluded
    excluded_dirs = set(getattr(base, "EXCLUDED_DIRS", set()))
    excluded_dirs.update({".qubitz", ".ump", "backup"})
    base.EXCLUDED_DIRS = excluded_dirs
    original_is_excluded_dir_name = base.is_excluded_dir_name

    def _is_excluded_dir_name(name: str, configured: set[str] | None = None) -> bool:
        normalized = name.strip().lower()
        if normalized.startswith("tmp_smoke_results_"):
            return True
        if re.fullmatch(
            r"\.?(?:venv|env)(?:(?:[._-][a-z0-9][a-z0-9._-]*)|(?:[0-9][a-z0-9._-]*))?",
            normalized,
            re.IGNORECASE,
        ):
            return True
        return original_is_excluded_dir_name(name, configured)

    def _is_python_environment_root(path: Path) -> bool:
        return bool(
            (path / "pyvenv.cfg").is_file()
            or (path / "Scripts" / "python.exe").is_file()
            or (path / "bin" / "python").is_file()
        )

    def _iter_workspace_files(workspace: Path, configured: set[str]) -> Any:
        for root, dirnames, filenames in os.walk(workspace, topdown=True):
            root_path = Path(root)
            dirnames[:] = [
                dirname
                for dirname in dirnames
                if not _is_excluded_dir_name(dirname, configured)
                and not _is_python_environment_root(root_path / dirname)
            ]
            for filename in filenames:
                yield root_path / filename

    base.is_excluded_dir_name = _is_excluded_dir_name
    base.iter_workspace_files = _iter_workspace_files
    base.load_harness_text = _load_harness_text


def _patch_streaming_chat(base: Any) -> None:
    if getattr(base.LlamaCppClient, "_qubitz_streaming_patched", False):
        return
    import httpx

    original_chat = base.LlamaCppClient.chat
    stream_state = threading.local()

    def _chat(self: Any, **kwargs: Any) -> dict[str, Any]:
        callback = getattr(stream_state, "callback", None)
        transport = getattr(self, "transport", None)
        if callback is None or getattr(transport, "label", "") != "direct":
            return original_chat(self, **kwargs)
        payload = {
            "model": kwargs["model_name"],
            "messages": kwargs["messages"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": kwargs["tools"],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "parse_tool_calls": True,
            "max_tokens": kwargs["num_predict"],
            "temperature": kwargs["temperature"],
            "top_p": kwargs["top_p"],
            "min_p": kwargs["min_p"],
            "repeat_penalty": kwargs["repeat_penalty"],
        }
        reasoning_budget = getattr(stream_state, "reasoning_budget", None)
        if reasoning_budget is not None:
            payload["reasoning_budget"] = int(reasoning_budget)
        reasoning_mode = getattr(stream_state, "reasoning_mode", None)
        if reasoning_mode is not None:
            payload["reasoning"] = reasoning_mode
        content_parts: list[str] = []
        pending_parts: list[str] = []
        pending_chars = 0
        last_emit_at = time.monotonic()
        tool_parts: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None
        received_event = False
        try:
            with httpx.Client(timeout=600.0) as client:
                with client.stream(
                    "POST",
                    f"{transport.base_url}/v1/chat/completions",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        event = json.loads(data)
                        received_event = True
                        if isinstance(event.get("usage"), dict):
                            usage = event["usage"]
                        choices = event.get("choices") or []
                        if not choices:
                            continue
                        choice = choices[0]
                        finish_reason = choice.get("finish_reason") or finish_reason
                        delta = choice.get("delta") or {}
                        content = delta.get("content") or ""
                        if content:
                            content = str(content)
                            content_parts.append(content)
                            pending_parts.append(content)
                            pending_chars += len(content)
                            now = time.monotonic()
                            if pending_chars >= 64 or "\n" in content or now - last_emit_at >= 1.0:
                                callback("".join(pending_parts))
                                pending_parts.clear()
                                pending_chars = 0
                                last_emit_at = now
                        for item in delta.get("tool_calls") or []:
                            index = int(item.get("index", len(tool_parts)))
                            current = tool_parts.setdefault(
                                index,
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if item.get("id"):
                                current["id"] = item["id"]
                            function = item.get("function") or {}
                            if function.get("name"):
                                current["function"]["name"] += str(function["name"])
                            if function.get("arguments"):
                                current["function"]["arguments"] += str(function["arguments"])
                        if choice.get("finish_reason") is not None:
                            break
        except Exception:
            if received_event:
                raise
            return original_chat(self, **kwargs)
        if pending_parts:
            callback("".join(pending_parts))
        tool_calls = [tool_parts[index] for index in sorted(tool_parts)][:1]
        return {
            "message": {
                "role": "assistant",
                "content": "".join(content_parts),
                "tool_calls": base.LlamaCppClient._normalize_tool_calls(tool_calls),
            },
            "usage": usage,
            "timings": {},
            "finish_reason": finish_reason,
        }

    def _set_stream_callback(callback: Callable[[str], None] | None) -> None:
        stream_state.callback = callback

    def _set_reasoning_budget(reasoning_budget: int | None) -> None:
        stream_state.reasoning_budget = reasoning_budget

    def _set_reasoning_mode(reasoning_mode: str | None) -> None:
        stream_state.reasoning_mode = reasoning_mode

    base.LlamaCppClient.chat = _chat
    base.LlamaCppClient._qubitz_streaming_patched = True
    base.set_qubitz_stream_callback = _set_stream_callback
    base.set_qubitz_reasoning_budget = _set_reasoning_budget
    base.set_qubitz_reasoning_mode = _set_reasoning_mode


class WorkspaceDiscoveryMemory:
    """Local evidence-backed project discoveries with lazy cache invalidation."""

    _SCHEMA_VERSION = 1
    _MAX_RECORDS = 256
    _MAX_CONTEXT_RECORDS = 6
    _MAX_CONTEXT_CHARS = 1400
    _MAX_VALUE_CHARS = 800
    _HASH_LIMIT_BYTES = 4 * 1024 * 1024
    _CONFIG_NAMES = (
        "AGENTS.md",
        "CMakeLists.txt",
        "Cargo.toml",
        "Dockerfile",
        "Makefile",
        "compose.yml",
        "docker-compose.yml",
        "go.mod",
        "package.json",
        "pom.xml",
        "pyproject.toml",
        "requirements.txt",
        "setup.cfg",
        "setup.py",
    )
    _MCP_CONFIG_NAMES = (".mcp.json", "mcp.json", "mcp_servers.json")
    _PYTHON_CANDIDATES = (
        ".venv/bin/python",
        ".venv_linux/bin/python",
        ".venv_wsl/bin/python",
        "venv/bin/python",
        ".venv312/Scripts/python.exe",
        ".venv313/Scripts/python.exe",
        ".venv/Scripts/python.exe",
        ".venv_win/Scripts/python.exe",
        "venv/Scripts/python.exe",
    )
    _STOPWORDS = {
        "a",
        "an",
        "and",
        "as",
        "at",
        "for",
        "from",
        "in",
        "it",
        "of",
        "on",
        "project",
        "repo",
        "repository",
        "the",
        "this",
        "to",
        "use",
        "workspace",
    }
    _SENSITIVE_VALUE_PATTERN = re.compile(
        r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|authorization)\b\s*[:=]",
        re.IGNORECASE,
    )

    def __init__(self, workspace: Path, runtime_workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.runtime_workspace = runtime_workspace.resolve()
        self.workspace_id = self._canonical_workspace_id(self.workspace)
        workspace_hash = hashlib.sha256(self.workspace_id.encode("utf-8")).hexdigest()[:24]
        self.path = self.runtime_workspace / ".cache" / "workspace_discoveries" / f"{workspace_hash}.json"
        self._records: dict[str, dict[str, Any]] = {}
        self._loaded = False
        self._bootstrapped = False
        self._lock = threading.RLock()

    @staticmethod
    def _canonical_workspace_id(path: Path) -> str:
        rendered = path.resolve().as_posix()
        wsl_match = re.match(r"^/mnt/([A-Za-z])/(.*)$", rendered)
        if wsl_match:
            return f"{wsl_match.group(1).lower()}:/{wsl_match.group(2)}".casefold()
        return rendered.casefold() if os.name == "nt" else rendered

    @staticmethod
    def _timestamp() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    @classmethod
    def _safe_text(cls, value: Any, limit: int | None = None) -> str:
        rendered = re.sub(r"\s+", " ", str(value or "")).strip()
        if cls._SENSITIVE_VALUE_PATTERN.search(rendered):
            return ""
        maximum = limit or cls._MAX_VALUE_CHARS
        return rendered if len(rendered) <= maximum else rendered[: maximum - 3].rstrip() + "..."

    @staticmethod
    def _is_sensitive_path(path: Path) -> bool:
        lowered = path.name.casefold()
        return (
            lowered == ".env"
            or lowered.startswith(".env.")
            or path.suffix.casefold() in {".key", ".p12", ".pem", ".pfx"}
        )

    def _workspace_path(self, value: Any) -> Path | None:
        if value is None or not str(value).strip():
            return None
        candidate = Path(str(value)).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        with suppress(OSError, RuntimeError, ValueError):
            resolved = candidate.resolve()
            resolved.relative_to(self.workspace)
            if not self._is_sensitive_path(resolved):
                return resolved
        return None

    def _fingerprint(self, path: Path) -> dict[str, Any] | None:
        with suppress(OSError):
            stat = path.stat()
            fingerprint: dict[str, Any] = {
                "path": path.relative_to(self.workspace).as_posix(),
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "is_dir": path.is_dir(),
            }
            if path.is_file() and stat.st_size <= self._HASH_LIMIT_BYTES:
                fingerprint["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            return fingerprint
        return None

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self.path.is_file():
            return
        with suppress(OSError, json.JSONDecodeError, TypeError, ValueError):
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == self._SCHEMA_VERSION
                and payload.get("workspace_id") == self.workspace_id
                and isinstance(payload.get("records"), dict)
            ):
                self._records = {
                    str(key): value
                    for key, value in payload["records"].items()
                    if isinstance(value, dict)
                }

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": self._SCHEMA_VERSION,
            "workspace_id": self.workspace_id,
            "workspace": self.workspace.as_posix(),
            "updated_at": self._timestamp(),
            "records": self._records,
        }
        temporary = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def _prune(self) -> None:
        if len(self._records) <= self._MAX_RECORDS:
            return
        ordered = sorted(
            self._records.items(),
            key=lambda item: (
                item[1].get("state") != "stale",
                str(item[1].get("last_verified", "")),
            ),
        )
        for key, _record in ordered[: len(self._records) - self._MAX_RECORDS]:
            self._records.pop(key, None)

    def _upsert(
        self,
        kind: str,
        key: str,
        value: Any,
        *,
        evidence_path: Any = None,
        state: str = "verified",
        source: str = "wrapper",
        tags: Sequence[str] = (),
        save: bool = True,
    ) -> bool:
        rendered_key = self._safe_text(key, 240)
        rendered_value = self._safe_text(value)
        if not rendered_key or not rendered_value:
            return False
        path = self._workspace_path(evidence_path)
        fingerprint = self._fingerprint(path) if path is not None else None
        record_id = hashlib.sha256(f"{kind}\0{rendered_key}".encode("utf-8")).hexdigest()[:32]
        previous = self._records.get(record_id, {})
        now = self._timestamp()
        rendered_tags = sorted(
            {
                *[str(tag) for tag in previous.get("tags", [])],
                *[self._safe_text(tag, 80) for tag in tags if self._safe_text(tag, 80)],
            }
        )
        last_verified = now
        if state == "configured" and previous.get("evidence") == fingerprint:
            last_verified = str(previous.get("last_verified", now))
            if previous.get("state") == "verified":
                state = "verified"
                source = str(previous.get("source", source))
                rendered_value = str(previous.get("value", rendered_value))
        record = {
            "kind": kind,
            "key": rendered_key,
            "value": rendered_value,
            "state": state,
            "source": source,
            "tags": rendered_tags,
            "evidence": fingerprint,
            "first_seen": previous.get("first_seen", now),
            "last_verified": last_verified,
        }
        if record == previous:
            return False
        self._records[record_id] = record
        self._prune()
        if save:
            self._save()
        return True

    def _refresh(self) -> None:
        changed = False
        current_states: dict[str, tuple[int, int, bool] | None] = {}
        for record in self._records.values():
            evidence = record.get("evidence")
            if not isinstance(evidence, dict) or record.get("state") == "stale":
                continue
            evidence_path = str(evidence.get("path", ""))
            if evidence_path not in current_states:
                path = self._workspace_path(evidence_path)
                current_state: tuple[int, int, bool] | None = None
                if path is not None:
                    with suppress(OSError):
                        stat = path.stat()
                        current_state = (stat.st_mtime_ns, stat.st_size, path.is_dir())
                current_states[evidence_path] = current_state
            expected_state = (
                int(evidence.get("mtime_ns", -1)),
                int(evidence.get("size", -1)),
                bool(evidence.get("is_dir", False)),
            )
            if current_states[evidence_path] != expected_state:
                record["state"] = "stale"
                record["stale_at"] = self._timestamp()
                changed = True
        if changed:
            self._save()

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = True
        changed = False
        for name in self._CONFIG_NAMES:
            path = self.workspace / name
            if path.is_file():
                changed |= self._upsert(
                    "project_config",
                    name.casefold(),
                    f"Configured project file: {name}",
                    evidence_path=path,
                    state="configured",
                    source="workspace_scan",
                    tags=(name, "configuration"),
                    save=False,
                )
        for name in self._MCP_CONFIG_NAMES:
            path = self.workspace / name
            if path.is_file():
                changed |= self._upsert(
                    "mcp_config",
                    name.casefold(),
                    f"Configured MCP file: {name}",
                    evidence_path=path,
                    state="configured",
                    source="workspace_scan",
                    tags=("mcp", "tools", name),
                    save=False,
                )
        for name in PROJECT_LAUNCH_ROOT_FILENAMES:
            path = self.workspace / name
            if path.is_file():
                changed |= self._upsert(
                    "entrypoint",
                    name.casefold(),
                    f"Existing root entrypoint: {name}",
                    evidence_path=path,
                    state="configured",
                    source="workspace_scan",
                    tags=("run", "launch", "execute", name),
                    save=False,
                )
        for relative in self._PYTHON_CANDIDATES:
            path = self.workspace / relative
            if path.is_file():
                changed |= self._upsert(
                    "python_environment",
                    relative.casefold(),
                    f"Workspace Python interpreter: {relative}",
                    evidence_path=path,
                    state="configured",
                    source="workspace_scan",
                    tags=("python", "venv", "environment"),
                    save=False,
                )
        package_path = self.workspace / "package.json"
        if package_path.is_file():
            with suppress(OSError, json.JSONDecodeError, TypeError):
                package_data = json.loads(package_path.read_text(encoding="utf-8"))
                scripts = package_data.get("scripts", {}) if isinstance(package_data, dict) else {}
                if isinstance(scripts, dict):
                    runner = "pnpm" if (self.workspace / "pnpm-lock.yaml").is_file() else "npm"
                    for script_name in list(scripts)[:12]:
                        command = f"{runner} run {script_name}"
                        changed |= self._upsert(
                            "configured_command",
                            command.casefold(),
                            command,
                            evidence_path=package_path,
                            state="configured",
                            source="package.json",
                            tags=("node", "script", str(script_name)),
                            save=False,
                        )
        if changed:
            self._save()

    @classmethod
    def _tokens(cls, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9_.:+/-]{2,}", text.casefold())
            if token not in cls._STOPWORDS
        }

    def context_for(self, prompt: str) -> str:
        with self._lock:
            self._load()
            self._bootstrap()
            self._refresh()
            prompt_tokens = self._tokens(prompt)
            generic_project_task = bool(re.search(r"\b(?:run|execute|start|launch|use|test|build|fix|edit)\b", prompt, re.IGNORECASE))
            base_scores = {
                "test_command": 13,
                "entrypoint": 11,
                "python_environment": 10,
                "mcp_server": 10,
                "mcp_config": 9,
                "successful_command": 8,
                "configured_command": 7,
                "important_file": 6,
                "project_config": 5,
            }
            ranked: list[tuple[int, str, dict[str, Any]]] = []
            for record_id, record in self._records.items():
                if record.get("state") == "stale":
                    continue
                haystack = " ".join(
                    [
                        str(record.get("kind", "")),
                        str(record.get("key", "")),
                        str(record.get("value", "")),
                        " ".join(str(tag) for tag in record.get("tags", [])),
                    ]
                )
                overlap = len(prompt_tokens.intersection(self._tokens(haystack)))
                score = base_scores.get(str(record.get("kind", "")), 3) + overlap * 9
                if generic_project_task and record.get("kind") in {"entrypoint", "python_environment", "configured_command"}:
                    score += 4
                if "mcp" in prompt_tokens and str(record.get("kind", "")).startswith("mcp"):
                    score += 12
                if re.search(r"\b(?:test|pytest|lint|ruff|check)\b", prompt, re.IGNORECASE) and record.get("kind") == "test_command":
                    score += 14
                if score >= 9:
                    ranked.append((score, record_id, record))
            ranked.sort(key=lambda item: (-item[0], str(item[2].get("value", ""))))
            lines = [
                "Wrapper cache-validated workspace discoveries:",
                "Use these as task hints. Re-verify commands or outcomes when the current task requires proof.",
            ]
            for _score, _record_id, record in ranked[: self._MAX_CONTEXT_RECORDS]:
                state = str(record.get("state", "verified"))
                evidence = record.get("evidence")
                evidence_text = f"; evidence={evidence.get('path')}" if isinstance(evidence, dict) else ""
                line = f"- [{state}/{record.get('kind')}] {record.get('value')}{evidence_text}"
                if len("\n".join([*lines, line])) > self._MAX_CONTEXT_CHARS:
                    break
                lines.append(line)
            if len(lines) == 2:
                return ""
            return "\n".join(lines)

    def record_entrypoint(self, entrypoint: dict[str, Any], command: str, *, source: str) -> None:
        with self._lock:
            self._load()
            label = self._safe_text(entrypoint.get("label") or entrypoint.get("path") or command, 240)
            if not label:
                return
            evidence_path = entrypoint.get("path")
            value = f"Previously successful entrypoint {label}: {command}"
            self._upsert(
                "entrypoint",
                label.casefold(),
                value,
                evidence_path=evidence_path,
                state="verified",
                source=source,
                tags=("run", "launch", "execute", label),
            )

    def observe_tool_evidence(self, prompt: str, evidence_items: Sequence[dict[str, Any]]) -> None:
        with self._lock:
            self._load()
            changed = False
            prompt_tokens = self._tokens(prompt)
            for evidence in evidence_items:
                if not isinstance(evidence, dict) or not evidence.get("success"):
                    continue
                tool = self._safe_text(evidence.get("tool"), 120)
                categories = {str(item) for item in evidence.get("categories", [])}
                command = self._safe_text(evidence.get("command"))
                evidence_path = evidence.get("evidence_path")
                if command:
                    kind = "test_command" if "tests" in categories else "successful_command"
                    changed |= self._upsert(
                        kind,
                        command.casefold(),
                        f"Previously successful command: {command}",
                        evidence_path=evidence_path,
                        state="verified",
                        source=tool or "tool",
                        tags=tuple(sorted(prompt_tokens))[:12],
                        save=False,
                    )
                for raw_path in evidence.get("paths", []):
                    path = self._workspace_path(raw_path)
                    if path is None:
                        continue
                    relative = path.relative_to(self.workspace).as_posix()
                    changed |= self._upsert(
                        "important_file",
                        relative.casefold(),
                        f"Task-relevant workspace path: {relative}",
                        evidence_path=path,
                        state="verified",
                        source=tool or "tool",
                        tags=tuple(sorted(prompt_tokens))[:12],
                        save=False,
                    )
                if tool in {"start_project_mcp_server", "list_project_mcp_tools", "call_project_mcp_tool"}:
                    mcp_target = self._safe_text(evidence.get("mcp_target") or evidence_path or tool, 300)
                    if mcp_target:
                        changed |= self._upsert(
                            "mcp_server",
                            mcp_target.casefold(),
                            f"Previously verified MCP path: {mcp_target}",
                            evidence_path=evidence_path,
                            state="verified",
                            source=tool,
                            tags=("mcp", "tools", "server"),
                            save=False,
                        )
            if changed:
                self._prune()
                self._save()


class LocalOnlyApp:
    def __init__(self, base_module_name: str, wrapper_script: str, display_name: str) -> None:
        self.base = _load_embedded_base_module()
        self.wrapper_script = Path(wrapper_script).resolve()
        self.runtime_workspace = self.wrapper_script.parent.resolve()
        self.display_name = display_name
        _patch_local_only_dependencies(self.base)
        _patch_streaming_chat(self.base)
        _patch_harness_loader(self.base)
        self.base.MCPHost = self._build_mcp_host_class()
        self.base.AgentRunner = self._build_agent_runner_class()
        self.base.QubitzGUI = self._build_gui_class()

    def _build_mcp_host_class(self) -> type[Any]:
        base = self.base
        wrapper_script = self.wrapper_script
        runtime_workspace = self.runtime_workspace

        class EnhancedMCPHost(base.MCPHost):
            def _server_parameters(self_nonlocal: Any) -> Any:
                env = os.environ.copy()
                env["QUBITZ_MCP_WORKSPACE"] = str(self_nonlocal.workspace.resolve())
                env["QUBITZ_RUNTIME_ROOT"] = str(runtime_workspace)
                env["QUBITZ_LAUNCH_SCRIPT"] = str(wrapper_script)
                command = sys.executable
                args = [str(wrapper_script), "--serve-mcp", "--workspace", "."]
                return base.StdioServerParameters(command=command, args=args, cwd=self_nonlocal.workspace, env=env)

        return EnhancedMCPHost

    def _build_agent_runner_class(self) -> type[Any]:
        base = self.base
        runtime_workspace = self.runtime_workspace
        wrapper_script = self.wrapper_script

        class EnhancedAgentRunner(base.AgentRunner):
            def _thinking_effort(self) -> str:
                return _normalize_thinking_effort(getattr(self.config, "thinking_effort", "default"))

            def _effective_max_steps(self) -> int:
                configured = int(getattr(self.config, "max_steps", getattr(base, "MAX_TOOL_STEPS", 0)))
                override = THINKING_EFFORT_STEP_CAPS.get(self._thinking_effort())
                if override is None:
                    return configured
                if override <= 0:
                    return 0
                if configured <= 0:
                    return override
                return min(configured, override)

            def _effective_prompt_max_steps(self, prompt: str) -> int:
                effective = self._effective_max_steps()
                prompt_limit: int | None = None
                retry_override = int(getattr(self, "_prompt_step_retry_override", 0) or 0)
                selected_route = self._selected_route_name(prompt)
                if retry_override < 0:
                    prompt_limit = 0
                elif retry_override > 0:
                    prompt_limit = retry_override
                elif selected_route == ROUTE_SIMPLE_ANSWER:
                    prompt_limit = SIMPLE_DIRECT_QUESTION_STEP_CAP
                elif selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    prompt_limit = FOREGROUND_EXISTING_SCRIPT_STEP_CAP
                if prompt_limit is None:
                    return effective
                if prompt_limit <= 0:
                    return 0
                if effective <= 0:
                    return prompt_limit
                return min(effective, prompt_limit)

            def _adaptive_step_budget(self, prompt: str) -> tuple[int, list[str]]:
                original = int(getattr(self.config, "max_steps", getattr(base, "MAX_TOOL_STEPS", 0)))
                setattr(self.config, "max_steps", self._effective_prompt_max_steps(prompt))
                try:
                    return super()._adaptive_step_budget(prompt)
                finally:
                    setattr(self.config, "max_steps", original)

            def _is_retryable_final_answer(self, prompt: str, answer: str) -> bool:
                normalized = answer.strip()
                if not normalized:
                    return True
                if normalized.startswith("Stopped after reaching the maximum tool steps"):
                    return True
                if normalized.startswith("Stopped without a final answer."):
                    return True
                lowered = normalized.lower()
                if "you returned neither tool calls nor a final answer" in lowered:
                    return True
                selected_route = self._selected_route_name(prompt)
                if selected_route not in {ROUTE_SIMPLE_ANSWER, ROUTE_DIRECT_EXISTING_ENTRYPOINT}:
                    return False
                obvious_non_answer_markers = (
                    "i do not have enough context",
                    "i don't have enough context",
                    "i cannot answer that from the provided information",
                    "i can't answer that from the provided information",
                    "please provide more context",
                    "please provide the missing information",
                    "not enough information",
                )
                return any(marker in lowered for marker in obvious_non_answer_markers)

            def _retry_step_cap_for_failed_answer(self, prompt: str, answer: str) -> tuple[int | None, str]:
                if getattr(self, "_prompt_step_retry_override", None):
                    return None, ""
                if not self._is_retryable_final_answer(prompt, answer):
                    return None, ""
                selected_route = self._selected_route_name(prompt)
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    return SIMPLE_DIRECT_QUESTION_RETRY_STEP_CAP, "simple direct question"
                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    return FOREGROUND_EXISTING_SCRIPT_RETRY_STEP_CAP, "foreground existing-entrypoint task"
                return None, ""

            def _should_bypass_embedding_retrieval(self, prompt: str) -> bool:
                cleaned = prompt.strip()
                if not cleaned or "\n" in cleaned or len(cleaned) > 160:
                    return False
                lowered = cleaned.lower()
                if not lowered.startswith(SIMPLE_DIRECT_QUESTION_PREFIXES):
                    return False
                if any(token in lowered for token in SIMPLE_DIRECT_QUESTION_BLOCKERS):
                    return False
                if any(hint in lowered for hint in WORKSPACE_CONTEXT_HINTS):
                    return False
                if base.READ_INTENT_PATTERN.search(cleaned):
                    return False
                if base.EDIT_INTENT_PATTERN.search(cleaned) or base.VERIFY_INTENT_PATTERN.search(cleaned):
                    return False
                if getattr(base, "extract_file_tokens", lambda _text: [])(cleaned):
                    return False
                return True

            def _is_foreground_existing_script_task(self, prompt: str) -> bool:
                cleaned = prompt.strip()
                if not cleaned:
                    return False
                lowered = cleaned.lower()
                if "/bg" in lowered or "background job" in lowered:
                    return False
                entrypoint = _resolve_existing_entrypoint_spec(base, self.workspace, cleaned)
                if entrypoint is None:
                    return False
                if re.search(r"\b(?:do not|without)\s+(?:run|execute|launch|start|invoke)\b", lowered):
                    return False
                semantic = _analyze_semantic_intent(cleaned)
                if semantic.requests("mcp_call", "mcp_discover"):
                    return False
                if _prompt_has_explicit_entrypoint_command(cleaned):
                    return True
                edit_match = base.EDIT_INTENT_PATTERN.search(cleaned)
                execution_match = re.search(r"\b(run|execute|launch|start|invoke|test|use)\b", lowered)
                if edit_match is not None and (
                    execution_match is None or edit_match.start() < execution_match.start()
                ):
                    return False
                if any(hint in lowered for hint in FOREGROUND_EXISTING_SCRIPT_HINTS):
                    return True
                if execution_match is not None:
                    return True
                return (
                    str(entrypoint.get("origin", "") or "") == "implicit_discovery"
                    and _prompt_prefers_implicit_existing_entrypoint(cleaned)
                )

            def _resolve_existing_entrypoint_for_prompt(self, prompt: str) -> dict[str, Any] | None:
                return _resolve_existing_entrypoint_spec(base, self.workspace, prompt)

            def _resolve_read_only_prompt_files(self, prompt: str) -> list[Path]:
                files: list[Path] = []
                seen: set[str] = set()
                max_files = int(getattr(base, "MAX_DIRECT_READ_FILES", 4))
                for raw_token in getattr(base, "extract_file_tokens", lambda _text: [])(prompt):
                    token = _normalize_prompt_path_token(raw_token)
                    if not token or token.startswith(("http://", "https://")):
                        continue
                    try:
                        candidate = base.resolve_workspace_path(
                            self.workspace,
                            token,
                            allow_missing=False,
                            allow_external=True,
                        )
                    except Exception:
                        continue
                    if not candidate.is_file():
                        continue
                    resolved = candidate.resolve()
                    key = str(resolved).casefold()
                    if key in seen:
                        continue
                    if callable(getattr(base, "is_sensitive_path_name", None)) and base.is_sensitive_path_name(resolved):
                        continue
                    text_checker = getattr(base, "is_probably_text_file", None)
                    if callable(text_checker):
                        with suppress(Exception):
                            if not text_checker(resolved):
                                continue
                    elif resolved.suffix.lower() not in getattr(base, "TEXT_SUFFIXES", {".md", ".py", ".txt"}):
                        continue
                    seen.add(key)
                    files.append(resolved)
                    if len(files) >= max_files:
                        break
                return files

            def _build_read_only_workspace_context(self, prompt: str) -> str:
                files = self._resolve_read_only_prompt_files(prompt)
                if not files:
                    return ""
                total_budget = max(1200, int(getattr(base, "MAX_DIRECT_READ_CHARS", 12000)))
                per_file_budget = max(800, total_budget // max(1, len(files)))
                parts = [
                    "Wrapper read-only workspace context:",
                    "The wrapper read these exact text file excerpts before model or tool orchestration.",
                ]
                used_chars = 0
                for path in files:
                    try:
                        text = path.read_text(encoding="utf-8", errors="ignore")
                    except Exception as exc:
                        rel_error = str(path)
                        with suppress(Exception):
                            rel_error = str(base.relative_path(path, self.workspace))
                        parts.append(f"File read failed: {rel_error} ({type(exc).__name__}: {exc})")
                        continue
                    if not text.strip():
                        continue
                    rel = str(path)
                    with suppress(Exception):
                        rel = str(base.relative_path(path, self.workspace))
                    remaining = max(0, total_budget - used_chars)
                    if remaining <= 0:
                        break
                    file_budget = min(per_file_budget, remaining)
                    numbered_lines: list[str] = []
                    char_count = 0
                    for line_number, line in enumerate(text.splitlines(), start=1):
                        numbered = f"{line_number}: {line}"
                        projected = char_count + len(numbered) + 1
                        if projected > file_budget:
                            break
                        numbered_lines.append(numbered)
                        char_count = projected
                    if not numbered_lines:
                        continue
                    used_chars += char_count
                    parts.append(f"File: {rel}\nExcerpt:\n" + "\n".join(numbered_lines))
                if len(parts) <= 2:
                    return ""
                parts.append(
                    "Use only this read-only context unless it is insufficient; do not edit files or run commands for this route."
                )
                return "\n\n".join(parts)

            def _build_focused_file_context(self, prompt: str) -> str:
                context = self._build_read_only_workspace_context(prompt)
                if not context:
                    return ""
                return (
                    context.replace("Wrapper read-only workspace context:", "Wrapper focused file context:", 1)
                    .replace(
                        "The wrapper read these exact text file excerpts before model or tool orchestration.",
                        "The wrapper read these exact text file excerpts before semantic retrieval or tool orchestration.",
                        1,
                    )
                    .replace(
                        "Use only this read-only context unless it is insufficient; do not edit files or run commands for this route.",
                        "Use this focused context first. Use file or command tools only when the task requires them, and widen to semantic retrieval only if these excerpts are insufficient.",
                        1,
                    )
                )

            def _build_metadata_or_lexical_context(self, prompt: str) -> str:
                lowered = prompt.lower()
                metadata_names: list[str] = []
                metadata_groups = (
                    (r"\b(python|dependency|dependencies|package|install|pytest|ruff)\b", ("pyproject.toml", "requirements.txt", "setup.cfg", "setup.py")),
                    (r"\b(node|npm|pnpm|yarn|javascript|typescript)\b", ("package.json", "pnpm-workspace.yaml", "tsconfig.json")),
                    (r"\b(make|cmake|build configuration)\b", ("Makefile", "CMakeLists.txt")),
                    (r"\b(docker|container|compose)\b", ("Dockerfile", "docker-compose.yml", "compose.yml")),
                )
                for pattern, names in metadata_groups:
                    if re.search(pattern, lowered):
                        metadata_names.extend(names)
                candidates: list[tuple[Path, int]] = []
                for name in dict.fromkeys(metadata_names):
                    path = (self.workspace / name).resolve()
                    if path.is_file():
                        candidates.append((path, 1))

                symbols: list[str] = []
                for match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_.:-]{2,80})`", prompt):
                    token = match.group(1)
                    if "/" not in token and "\\" not in token and not Path(token).suffix:
                        symbols.append(token)
                for match in re.finditer(
                    r"\b(?:class|function|method|symbol|variable|component|module)\s+([A-Za-z_][A-Za-z0-9_.:]{2,80})",
                    prompt,
                    re.IGNORECASE,
                ):
                    symbols.append(match.group(1))
                rg = shutil.which("rg")
                if rg is not None:
                    for symbol in list(dict.fromkeys(symbols))[:3]:
                        completed = subprocess.run(
                            [
                                rg,
                                "-n",
                                "--fixed-strings",
                                "--max-count",
                                "3",
                                "--glob",
                                "!**/.git/**",
                                "--glob",
                                "!**/.venv*/**",
                                "--glob",
                                "!**/node_modules/**",
                                symbol,
                                ".",
                            ],
                            cwd=self.workspace,
                            capture_output=True,
                            timeout=10,
                            check=False,
                        )
                        output = base.decode_subprocess_output(completed.stdout)
                        for raw_line in output.splitlines():
                            path_text, separator, remainder = raw_line.partition(":")
                            if not separator:
                                continue
                            line_text, separator, _ = remainder.partition(":")
                            if not separator or not line_text.isdigit():
                                continue
                            path = (self.workspace / path_text).resolve()
                            try:
                                path.relative_to(self.workspace)
                            except ValueError:
                                continue
                            if path.is_file():
                                candidates.append((path, int(line_text)))

                unique_candidates: list[tuple[Path, int]] = []
                seen_paths: set[str] = set()
                for path, line_number in candidates:
                    key = str(path).casefold()
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    unique_candidates.append((path, line_number))
                    if len(unique_candidates) >= 4:
                        break
                if not unique_candidates:
                    return ""

                parts = [
                    "Wrapper staged metadata and lexical context:",
                    "These exact local matches were resolved before semantic retrieval.",
                ]
                remaining_budget = max(1200, int(getattr(base, "MAX_DIRECT_READ_CHARS", 12000)))
                for path, line_number in unique_candidates:
                    try:
                        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
                    except OSError:
                        continue
                    start = max(1, line_number - 25)
                    end = min(len(lines), line_number + 25)
                    excerpt_lines: list[str] = []
                    for current in range(start, end + 1):
                        rendered = f"{current}: {lines[current - 1]}"
                        if len(rendered) + 1 > remaining_budget:
                            break
                        excerpt_lines.append(rendered)
                        remaining_budget -= len(rendered) + 1
                    if excerpt_lines:
                        parts.append(
                            f"File: {base.relative_path(path, self.workspace)}\nExcerpt:\n" + "\n".join(excerpt_lines)
                        )
                    if remaining_budget <= 0:
                        break
                if len(parts) <= 2:
                    return ""
                parts.append("Use these exact matches first; use tools to retrieve more context only if needed.")
                return "\n\n".join(parts)

            def _selected_route_name(self, prompt: str) -> str:
                decision = getattr(self, "_route_decision", None)
                if isinstance(decision, RouteDecision):
                    return decision.selected_route
                if self._should_bypass_embedding_retrieval(prompt):
                    return ROUTE_SIMPLE_ANSWER
                if self._is_foreground_existing_script_task(prompt):
                    return ROUTE_DIRECT_EXISTING_ENTRYPOINT
                return ROUTE_RETRIEVAL_PLUS_MODEL

            def _extract_route_features(self, prompt: str) -> tuple[RouteFeatures, dict[str, Any] | None]:
                cleaned = _normalize_project_use_task_prompt(prompt.strip())
                lowered = cleaned.lower()
                semantic = _analyze_semantic_intent(cleaned)
                mutation_forbidden = not semantic.requests("copy", "create", "delete", "edit", "move")
                execution_forbidden = not semantic.requests(
                    "execute",
                    "clone_repository",
                    "mcp_call",
                    "mcp_discover",
                    "open_browser",
                    "open_project",
                    "restart",
                    "start",
                    "stop",
                    "verify",
                    "install",
                    "uninstall",
                    "upgrade",
                )
                file_tokens = getattr(base, "extract_file_tokens", lambda _text: [])(cleaned)
                entrypoint = self._resolve_existing_entrypoint_for_prompt(cleaned)
                missing_context_markers = (
                    "that file",
                    "that script",
                    "that function",
                    "that class",
                    "that error",
                    "the above",
                    "above code",
                    "previous answer",
                    "last answer",
                    "which one",
                )
                features = RouteFeatures(
                    prompt_length=len(cleaned),
                    line_count=max(1, cleaned.count("\n") + 1),
                    simple_question_candidate=self._should_bypass_embedding_retrieval(cleaned),
                    explicit_existing_entrypoint=self._is_foreground_existing_script_task(cleaned),
                    read_intent=semantic.requests("fetch_url", "read")
                    or bool(
                        re.search(
                            r"\b(analy[sz]e|explain|summarize|compare|assess|understand)\b",
                            lowered,
                        )
                    ),
                    edit_intent=not mutation_forbidden,
                    verify_intent=semantic.requests("verify"),
                    workspace_context_needed=bool(
                        any(hint in lowered for hint in WORKSPACE_CONTEXT_HINTS)
                        or file_tokens
                        or base.READ_INTENT_PATTERN.search(cleaned)
                    ),
                    file_token_count=len(file_tokens),
                    mentions_tools_or_mcp=bool(semantic.objects.intersection({"mcp", "service"}))
                    and not execution_forbidden,
                    powershell_or_side_effect=bool(
                        any(token in lowered for token in ("start-process", "powershell", "activate.ps1", ".ps1"))
                    )
                    or semantic.requests("open_browser", "restart", "start", "stop"),
                    plugin_or_skill_request=bool(
                        any(token in lowered for token in ("skill", ".skills", "plugin", ".qubitz/plugins"))
                    ),
                    has_entrypoint_spec=entrypoint is not None,
                    entrypoint_kind=str(entrypoint.get("kind", "") or "") if entrypoint is not None else "",
                    needs_missing_context=bool(
                        (
                            any(marker in lowered for marker in missing_context_markers)
                            and not file_tokens
                            and entrypoint is None
                        )
                        or _project_use_prompt_lacks_concrete_goal(base, cleaned, entrypoint, file_tokens)
                    ),
                )
                return features, entrypoint

            def _score_routes(self, features: RouteFeatures) -> dict[str, int]:
                scores = {
                    ROUTE_SIMPLE_ANSWER: 0,
                    ROUTE_DIRECT_EXISTING_ENTRYPOINT: 0,
                    ROUTE_READ_ONLY_WORKSPACE: 0,
                    ROUTE_RETRIEVAL_PLUS_MODEL: 0,
                    ROUTE_TOOL_LOOP: 0,
                    ROUTE_ASK_USER_MISSING_INFO: 0,
                }
                if features.simple_question_candidate:
                    scores[ROUTE_SIMPLE_ANSWER] += 30
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 8
                    scores[ROUTE_TOOL_LOOP] -= 10
                if features.explicit_existing_entrypoint:
                    scores[ROUTE_DIRECT_EXISTING_ENTRYPOINT] += 28
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 4
                    scores[ROUTE_TOOL_LOOP] -= 6
                elif features.has_entrypoint_spec:
                    scores[ROUTE_READ_ONLY_WORKSPACE] += 8 if features.read_intent else 0
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 8
                if features.read_intent:
                    scores[ROUTE_READ_ONLY_WORKSPACE] += 10
                if features.file_token_count:
                    scores[ROUTE_READ_ONLY_WORKSPACE] += 12
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 4
                if features.workspace_context_needed:
                    scores[ROUTE_READ_ONLY_WORKSPACE] += 5
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 10
                    scores[ROUTE_SIMPLE_ANSWER] -= 12
                if features.edit_intent:
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 12
                    scores[ROUTE_TOOL_LOOP] += 6
                    scores[ROUTE_SIMPLE_ANSWER] -= 20
                    scores[ROUTE_READ_ONLY_WORKSPACE] -= 4
                if features.verify_intent:
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 10
                    scores[ROUTE_TOOL_LOOP] += 8
                    scores[ROUTE_SIMPLE_ANSWER] -= 20
                if features.mentions_tools_or_mcp:
                    scores[ROUTE_TOOL_LOOP] += 14
                    scores[ROUTE_SIMPLE_ANSWER] -= 12
                if features.powershell_or_side_effect:
                    scores[ROUTE_DIRECT_EXISTING_ENTRYPOINT] += 12
                    scores[ROUTE_TOOL_LOOP] += 18
                    scores[ROUTE_READ_ONLY_WORKSPACE] -= 30
                if features.plugin_or_skill_request:
                    scores[ROUTE_TOOL_LOOP] += 10
                    scores[ROUTE_SIMPLE_ANSWER] -= 8
                if features.line_count > 1 or features.prompt_length > 220:
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 4
                    scores[ROUTE_SIMPLE_ANSWER] -= 6
                if features.needs_missing_context:
                    scores[ROUTE_ASK_USER_MISSING_INFO] += 18
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] -= 4
                if max(scores.values()) <= 0:
                    scores[ROUTE_RETRIEVAL_PLUS_MODEL] += 3
                return scores

            def _fallback_routes_for_selection(self, selected_route: str) -> list[str]:
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]
                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]
                if selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    return [ROUTE_RETRIEVAL_PLUS_MODEL, ROUTE_TOOL_LOOP]
                if selected_route == ROUTE_TOOL_LOOP:
                    return [ROUTE_RETRIEVAL_PLUS_MODEL]
                if selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    return [ROUTE_READ_ONLY_WORKSPACE, ROUTE_RETRIEVAL_PLUS_MODEL]
                return [ROUTE_TOOL_LOOP]

            def _decide_route(self, prompt: str) -> RouteDecision:
                features, entrypoint = self._extract_route_features(prompt)
                scores = self._score_routes(features)
                selected_route = max(
                    scores.items(),
                    key=lambda item: (
                        item[1],
                        item[0] == ROUTE_SIMPLE_ANSWER,
                        item[0] == ROUTE_DIRECT_EXISTING_ENTRYPOINT,
                        item[0] == ROUTE_RETRIEVAL_PLUS_MODEL,
                    ),
                )[0]
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    reason = "Short direct question with no workspace, file, or tool cues."
                elif selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    entry_label = str((entrypoint or {}).get("label", "") or "existing entrypoint")
                    reason = f"Prompt points at {entry_label} and asks to use the existing workspace path first."
                elif selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    reason = "Read-oriented workspace prompt with explicit repo or file context and no edit requirement."
                elif selected_route == ROUTE_TOOL_LOOP:
                    reason = "Prompt explicitly leans on tools, commands, MCP, plugins, or verification."
                elif selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    reason = "Prompt appears to reference missing context that should be clarified before proceeding."
                else:
                    reason = "Default project route: retrieve focused context first, then reason or use tools as needed."
                return RouteDecision(
                    selected_route=selected_route,
                    reason=reason,
                    scores=scores,
                    features=features.to_trace_dict(),
                    fallback_routes=self._fallback_routes_for_selection(selected_route),
                )

            def _route_trace_path(self) -> Path:
                return self.runtime_workspace / LOCAL_ONLY_DIR_NAME / ROUTE_TRACE_FILE_NAME

            def _record_route_trace(
                self,
                prompt: str,
                status: str,
                *,
                answer: str = "",
                error: str = "",
            ) -> None:
                decision = getattr(self, "_route_decision", None)
                if not isinstance(decision, RouteDecision):
                    return
                payload = {
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "trace_id": decision.trace_id,
                    "variant": wrapper_script.name,
                    "workspace": str(self.workspace),
                    "profile": decision.profile,
                    "selected_route": decision.selected_route,
                    "fallback_routes": list(decision.fallback_routes),
                    "fallback_chain": list(getattr(self, "_route_fallback_chain", [])),
                    "reason": decision.reason,
                    "scores": dict(decision.scores),
                    "features": dict(decision.features),
                    "status": status,
                    "prompt_preview": _shorten(" ".join(prompt.split()), ROUTE_TRACE_PROMPT_PREVIEW_CHARS),
                    "answer_preview": _shorten(" ".join(answer.split()), ROUTE_TRACE_ANSWER_PREVIEW_CHARS) if answer else "",
                    "error": error,
                    "timings": dict(getattr(self, "_route_phase_timings", {})),
                    "existing_script_preflight_succeeded": bool(self._existing_script_preflight_succeeded),
                    "used_local_plugins": self._local_plugin_context != "None",
                }
                trace_path = self._route_trace_path()
                with suppress(Exception):
                    trace_path.parent.mkdir(parents=True, exist_ok=True)
                    with trace_path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

            def _route_status_message(self) -> str:
                decision = getattr(self, "_route_decision", None)
                if not isinstance(decision, RouteDecision):
                    return ""
                if decision.selected_route == ROUTE_SIMPLE_ANSWER:
                    return (
                        "Wrapper route: simple_answer. Hard-skip repository retrieval, embeddings, local plugins or skills, "
                        "and MCP tool loading unless the direct answer path fails."
                    )
                if decision.selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    return (
                        "Wrapper route: direct_existing_entrypoint. Run the existing workspace entrypoint first, then use "
                        "model or tool reasoning only as fallback."
                    )
                if decision.selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    return "Wrapper route: read_only_workspace. Prefer focused repository context and read-only inspection first."
                if decision.selected_route == ROUTE_TOOL_LOOP:
                    return "Wrapper route: tool_loop. This prompt explicitly favors grounded local tool results."
                if decision.selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    return "Wrapper route: ask_user_missing_info. Missing context appears more important than immediate tool use."
                return "Wrapper route: retrieval_plus_model. Retrieve focused project context before broader orchestration."

            def _run_project_launch_path(
                self,
                prompt: str,
                callback: Callable[[str, str], None] | None,
            ) -> str:
                self._emit(
                    callback,
                    "status",
                    "Wrapper route: project_launch. Resolve and start the existing project launch path without retrieval or model orchestration.",
                )
                plan = _resolve_project_launch_plan(base, self.workspace, prompt)
                status = str(plan.get("status", "missing"))
                if status == "ambiguous":
                    labels = [str(item) for item in plan.get("candidates", []) if str(item)]
                    rendered = "\n".join(f"- {label}" for label in labels)
                    self._emit(
                        callback,
                        "status",
                        "Project launch is ambiguous; no command was executed.",
                    )
                    return (
                        "I found multiple plausible existing project launch commands:\n"
                        f"{rendered}\n\nSpecify which one to run."
                    ).strip()
                if status != "ready":
                    reason = str(plan.get("reason", "No deterministic existing project launch command was found."))
                    self._emit(callback, "status", f"Project launch was not started: {reason}")
                    return f"I could not safely determine one project launch command. {reason} Specify the command or entrypoint to run."
                command = [str(item) for item in plan.get("command", []) if str(item)]
                if not command:
                    return "I could not safely determine one project launch command. Specify the command or entrypoint to run."
                label = str(plan.get("label", "project launch command"))
                source = str(plan.get("source", "existing project metadata"))
                display_command = _project_launch_display_command(command)
                self._emit(
                    callback,
                    "status",
                    f"Wrapper project launch: starting {label} from {source} as a managed local background process.",
                )
                try:
                    result = self.background_jobs.start_command(
                        command,
                        workspace=self.workspace,
                        label=label,
                    )
                except Exception as exc:
                    self._emit(
                        callback,
                        "status",
                        f"Project launch failed before readiness: {type(exc).__name__}: {exc}",
                    )
                    return (
                        f"Project launch failed before readiness. Command: `{display_command}`. "
                        f"{type(exc).__name__}: {exc}"
                    )
                job_status = str(result.get("status", "unknown"))
                job_id = str(result.get("job_id", ""))
                pid = int(result.get("pid") or 0)
                log_path = str(result.get("log_path", ""))
                returncode = result.get("returncode")
                urls = [str(item) for item in result.get("detected_urls", []) if str(item)]
                log_excerpt = str(result.get("log_excerpt", "") or "")
                lines = [
                    f"Project launch status: {job_status}.",
                    f"Command: `{display_command}`",
                    f"Job ID: `{job_id}`",
                    f"PID: `{pid}`",
                    f"Log: `{log_path}`",
                ]
                if returncode is not None:
                    lines.append(f"Exit code: `{returncode}`")
                if urls:
                    lines.append("Detected local URL(s): " + ", ".join(urls))
                if log_excerpt:
                    lines.append(f"Initial output:\n```text\n{log_excerpt}\n```")
                if job_status == "running":
                    lines.append("Use `/jobs` to inspect managed background jobs or `/cancel-job <job_id>` to stop this launch.")
                if job_status in {"ready", "running", "started"}:
                    self.discovery_memory.record_entrypoint(plan, display_command, source="project_launch")
                self._emit(
                    callback,
                    "status",
                    f"Wrapper project launch finished readiness check with status {job_status}; model loading was skipped.",
                )
                return "\n\n".join(lines)

            def _run_existing_script_preflight(
                self,
                prompt: str,
                callback: Callable[[str, str], None] | None,
            ) -> str:
                entrypoint = self._resolve_existing_entrypoint_for_prompt(prompt)
                if entrypoint is None:
                    return ""
                entrypoint_label = str(entrypoint.get("label", "") or "entrypoint")
                if str(entrypoint.get("origin", "") or "") == "implicit_discovery":
                    self._emit(
                        callback,
                        "status",
                        f"Wrapper direct path: discovered workspace entrypoint {entrypoint_label} from the project-use prompt.",
                    )
                self._emit(
                    callback,
                    "status",
                    f"Wrapper direct path: running existing entrypoint {entrypoint_label}.",
                )
                before_helpers = {
                    str(candidate.resolve()): candidate.stat().st_mtime
                    for candidate in _collect_browser_helper_candidates(self.workspace)
                }
                before_artifacts = _workspace_text_artifact_state(base, self.workspace)
                try:
                    entrypoint_kind = str(entrypoint.get("kind", ""))
                    if entrypoint_kind == "file":
                        script_path = Path(str(entrypoint["path"]))
                        suffix = script_path.suffix.lower()
                        if suffix == ".py":
                            direct_python = self._direct_workspace_python_plan()
                            if direct_python is None:
                                self._emit(
                                    callback,
                                    "status",
                                    (
                                        "Wrapper direct path: no compatible workspace-local Python interpreter was found "
                                        "for this session; falling back to the model loop."
                                    ),
                                )
                                return ""
                            interpreter_path, launch_mode = direct_python
                            if launch_mode == "powershell":
                                command = (
                                    f"& {_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, interpreter_path))} "
                                    f"{_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, script_path))}"
                                )
                                result = _run_powershell_command(
                                    base,
                                    self.workspace,
                                    command,
                                    SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                                    cancel_event=self._cancel_event,
                                )
                                runner_kind = "powershell"
                            else:
                                relative_text = str(base.relative_path(script_path, self.workspace))
                                command = f"{_shell_quote_path(interpreter_path)} {_shell_quote_path(relative_text)}"
                                result = _run_shell_command(
                                    base,
                                    self.workspace,
                                    command,
                                    SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                                    cancel_event=self._cancel_event,
                                )
                                runner_kind = "shell"
                        elif suffix == ".ps1":
                            command = f"& {_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, script_path))}"
                            result = _run_powershell_command(
                                base,
                                self.workspace,
                                command,
                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                                cancel_event=self._cancel_event,
                            )
                            runner_kind = "powershell"
                        elif suffix == ".sh":
                            relative_text = str(base.relative_path(script_path, self.workspace))
                            command = (
                                f"bash {_shell_quote_path(relative_text)}"
                                if shutil.which("bash") is not None or os.name != "nt"
                                else _shell_quote_path(relative_text)
                            )
                            result = _run_shell_command(
                                base,
                                self.workspace,
                                command,
                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                                cancel_event=self._cancel_event,
                            )
                            runner_kind = "shell"
                        elif suffix in {".bat", ".cmd"}:
                            command = _workspace_relative_command_path(base, self.workspace, script_path)
                            result = _run_shell_command(
                                base,
                                self.workspace,
                                command,
                                SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                                cancel_event=self._cancel_event,
                            )
                            runner_kind = "shell"
                        else:
                            return ""
                    elif entrypoint_kind in {"uv_run", "package_script", "make_target"}:
                        command = str(entrypoint.get("command", "")).strip()
                        if not command:
                            return ""
                        result = _run_shell_command(
                            base,
                            self.workspace,
                            command,
                            SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                            cancel_event=self._cancel_event,
                        )
                        runner_kind = "shell"
                    else:
                        return ""
                except Exception as exc:
                    self._emit(
                        callback,
                        "status",
                        (
                            f"Wrapper direct path: existing entrypoint {entrypoint_label} failed before completion "
                            f"({type(exc).__name__}: {exc}); falling back to the model loop."
                        ),
                    )
                    return ""
                return_code = int(result.get("return_code", result.get("returncode", 0)))
                stderr_text = str(getattr(result, "stderr_full", result.get("stderr", "")) or "")
                stdout_text = str(getattr(result, "stdout_full", result.get("stdout", "")) or "")
                browser_helper_path: Path | None = None
                browser_helper_text = ""
                for candidate in _collect_browser_helper_candidates(self.workspace):
                    candidate_key = str(candidate.resolve())
                    mtime = candidate.stat().st_mtime
                    if candidate_key not in before_helpers or mtime > before_helpers[candidate_key]:
                        browser_helper_path = candidate
                        break
                if browser_helper_path is None:
                    for candidate in _collect_browser_helper_candidates(self.workspace):
                        with suppress(Exception):
                            candidate_text = candidate.read_text(encoding="utf-8", errors="ignore")
                            if START_PROCESS_URL_PATTERN.search(candidate_text):
                                browser_helper_path = candidate
                                browser_helper_text = candidate_text
                                break
                if browser_helper_path is not None and not browser_helper_text:
                    with suppress(Exception):
                        browser_helper_text = browser_helper_path.read_text(encoding="utf-8", errors="ignore")
                changed_artifacts = _changed_text_artifacts(base, self.workspace, before_artifacts)
                requested_browser_open = _prompt_requests_browser_open(prompt)
                browser_urls = _extract_start_process_urls(browser_helper_text)
                browser_result: dict[str, Any] | None = None
                evidence_texts = [stderr_text, stdout_text, browser_helper_text]
                evidence_texts.extend(text for _, text in changed_artifacts)
                result_rows: list[dict[str, Any]] = []
                seen_rows: set[str] = set()
                for evidence_text in evidence_texts:
                    for row in _extract_structured_result_rows(evidence_text):
                        normalized_row = _normalize_result_row(dict(row))
                        key = json.dumps(normalized_row, ensure_ascii=True, sort_keys=True, default=str)
                        if key not in seen_rows:
                            seen_rows.add(key)
                            result_rows.append(normalized_row)
                output_urls = _validated_external_urls(
                    [
                        *browser_urls,
                        *(url for evidence_text in evidence_texts for url in _extract_external_urls(evidence_text)),
                    ],
                    SCRIPT_PREFLIGHT_MAX_URLS,
                )
                self._existing_script_browser_urls = output_urls
                structured_rows: list[dict[str, Any]] = []
                for index, row in enumerate(result_rows):
                    enriched = _normalize_result_row(dict(row))
                    if index < len(output_urls) and _is_external_http_url(output_urls[index]):
                        enriched["url"] = output_urls[index]
                    structured_rows.append(enriched)
                direct_rows = structured_rows or [
                    {"url": url}
                    for url in output_urls
                ]
                self._existing_script_preflight_succeeded = return_code == 0
                expected_count = _expected_result_count(prompt)
                requested_fields = _requested_output_fields(prompt)
                requires_url = requested_browser_open or "url" in requested_fields
                target_count = expected_count or len(direct_rows)
                selected_rows = direct_rows[:target_count]
                selected_urls = _validated_external_urls(
                    [row.get("url", "") for row in selected_rows],
                    target_count,
                )
                reported_opened_urls = _reported_entrypoint_browser_urls(
                    entrypoint,
                    stdout_text,
                    browser_helper_text,
                    output_urls,
                    requested_browser_open=requested_browser_open,
                    return_code=return_code,
                )
                if (
                    requested_browser_open
                    and not reported_opened_urls
                    and len(selected_urls) >= target_count
                    and (
                        self._wsl_windows_interop_available()
                        or not (callable(getattr(base, "in_wsl", None)) and base.in_wsl())
                    )
                ):
                    browser_result = _run_inline_browser_open(
                        base,
                        self.workspace,
                        selected_urls,
                        SCRIPT_PREFLIGHT_TIMEOUT_SECONDS,
                    )
                inline_browser_open_ran = browser_result is not None and int(
                    browser_result.get("return_code", browser_result.get("returncode", 0))
                ) == 0
                opened_urls = reported_opened_urls or (selected_urls if inline_browser_open_ran else [])
                browser_open_ran = bool(opened_urls)
                broker = _TOOL_CALL_CONTEXT.get()
                if broker is not None and hasattr(broker, "evidence"):
                    broker.provenance_urls.update(url.casefold() for url in output_urls if _is_external_http_url(url))
                    categories = ["result_count"] if return_code == 0 and direct_rows else []
                    count_requirement = _requested_count_postcondition(prompt)
                    if return_code == 0 and changed_artifacts and count_requirement and count_requirement[0] == "created_outputs":
                        categories.append("created_outputs")
                    if browser_open_ran:
                        categories.append("opened_urls")
                    broker.evidence.append(
                        {
                            "turn_id": broker.turn_id,
                            "tool": "wrapper_direct_existing_entrypoint",
                            "success": return_code == 0,
                            "categories": categories,
                            "paths": [str(path) for path, _ in changed_artifacts],
                            "status": "completed" if return_code == 0 else "failed",
                            "return_code": return_code,
                            "result_count": len(direct_rows),
                            "opened_count": len(opened_urls),
                            "urls": opened_urls,
                        }
                    )
                if (
                    return_code == 0
                    and target_count > 0
                    and len(direct_rows) >= target_count
                    and (not requires_url or len(selected_urls) >= target_count)
                    and (not requested_browser_open or browser_open_ran)
                    and (not requested_fields or _rows_have_required_fields(selected_rows, requested_fields))
                ):
                    self._existing_script_direct_answer = _format_direct_result_answer(
                        selected_rows,
                        browser_open_ran=browser_open_ran,
                        requested_browser_open=requested_browser_open,
                        requested_fields=requested_fields,
                    )
                    self._emit(
                        callback,
                        "status",
                        (
                            "Wrapper direct path completed the existing-script task without entering the model loop. "
                            f"Rows: {target_count}. Browser step: "
                            f"{'verified entrypoint-reported opens' if reported_opened_urls else 'inline Start-Process with validated URLs' if inline_browser_open_ran else 'none'}."
                        ),
                    )
                self._emit(
                    callback,
                    "status",
                    (
                        f"Wrapper direct path: {entrypoint_label} exited {return_code} via {runner_kind}; "
                        f"captured {len(direct_rows) or len(result_rows)} row(s) and {len(output_urls)} distinct external URL(s)."
                    ),
                )
                verified_count = len(selected_urls) if requires_url else len(direct_rows)
                missing_count = max(0, target_count - verified_count)
                context_parts = [
                    "Wrapper preflight direct existing-entrypoint execution already ran before the model loop.",
                    f"Entrypoint: {entrypoint_label}",
                    f"Launch path: {runner_kind}",
                    f"Exit code: {return_code}",
                    f"Requested result count: {target_count}",
                    f"Verified direct result count: {verified_count}",
                    f"Missing result count: {missing_count}",
                ]
                if browser_result is not None:
                    browser_exit = int(browser_result.get("return_code", browser_result.get("returncode", 0)))
                    context_parts.append(f"Browser-opening helper exit code: {browser_exit}")
                if reported_opened_urls:
                    context_parts.append(
                        f"Entrypoint-reported browser opens verified conservatively: {len(reported_opened_urls)}."
                    )
                    remaining_urls = [url for url in output_urls if url not in set(reported_opened_urls)]
                    if remaining_urls:
                        context_parts.append("URLs not covered by the entrypoint's reported browser-open count:")
                        context_parts.append("\n".join(remaining_urls[:SCRIPT_PREFLIGHT_MAX_URLS]))
                if structured_rows:
                    context_parts.append(
                        "Structured rows extracted from the script output and the generated browser helper. "
                        "Preserve these exact fields and prefer these exact external URLs:"
                    )
                    context_parts.append(
                        "\n".join(json.dumps(row, ensure_ascii=True) for row in structured_rows[:SCRIPT_PREFLIGHT_MAX_URLS])
                    )
                elif output_urls:
                    context_parts.append("External URLs extracted from the generated browser helper, in order:")
                    context_parts.append("\n".join(output_urls[:SCRIPT_PREFLIGHT_MAX_URLS]))
                if stdout_text:
                    context_parts.append(
                        "Script stdout excerpt:\n"
                        + _shorten(stdout_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)
                    )
                if stderr_text:
                    context_parts.append(
                        "Script stderr excerpt:\n"
                        + _shorten(stderr_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)
                    )
                if browser_helper_path is not None and browser_helper_text:
                    context_parts.append(
                        "Generated browser-opening helper excerpt:\n"
                        + _shorten(browser_helper_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS)
                    )
                for artifact_path, artifact_text in changed_artifacts[:4]:
                    artifact_label = base.relative_path(artifact_path, self.workspace)
                    context_parts.append(
                        (
                            f"Changed output artifact {artifact_label} excerpt:\n"
                            + _shorten(artifact_text, SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS // 4)
                        )
                        if artifact_text
                        else f"Changed non-text or oversized output artifact: {artifact_label}"
                    )
                context_parts.append(
                    "Continue only the missing postconditions from these exact verified preflight results. Preserve valid existing results, "
                    "avoid broad repository retrieval, and do not rerun the same entrypoint unless new evidence shows that a rerun can "
                    "produce the missing result. Do not claim completion until every requested quantity and side effect is verified."
                )
                return _shorten(
                    "\n\n".join(part for part in context_parts if part).strip(),
                    SCRIPT_PREFLIGHT_MAX_CONTEXT_CHARS,
                )

            def _wsl_windows_interop_available(self) -> bool:
                capabilities = self._runtime_capabilities()
                return bool(capabilities.get("in_wsl") and capabilities.get("windows_interop_available"))

            def _wsl_windows_interop_unavailable(self) -> bool:
                capabilities = self._runtime_capabilities()
                return bool(capabilities.get("in_wsl") and not capabilities.get("windows_interop_available"))

            def _runtime_capabilities(self) -> dict[str, Any]:
                return _workspace_runtime_capabilities(base, self.workspace)

            def _direct_workspace_python_plan(self) -> tuple[Path, str] | None:
                return _select_direct_workspace_python(base, self.workspace)

            def _task_class_label(self, prompt: str) -> str:
                selected_route = self._selected_route_name(prompt)
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    return "simple_direct_question"
                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    return "foreground_existing_script_task"
                if selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    return "read_only_workspace_task"
                if selected_route == ROUTE_TOOL_LOOP:
                    return "tool_or_verification_task"
                if selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    return "missing_context_task"
                lowered = prompt.strip().lower()
                if any(token in lowered for token in ("start-process", "powershell", "activate.ps1", ".ps1")):
                    return "powershell_or_side_effect_task"
                if base.EDIT_INTENT_PATTERN.search(prompt):
                    return "edit_or_refactor_task"
                if "mcp" in lowered or "tool" in lowered:
                    return "mcp_or_tool_task"
                if base.READ_INTENT_PATTERN.search(prompt):
                    return "read_or_repo_analysis_task"
                return "general_project_task"

            def _preferred_llama_runtime_label(self, capabilities: dict[str, Any]) -> str:
                configured_server_path = (
                    getattr(self.config, "llama_server_path", "") or os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME) or ""
                ).strip()
                configured_server_url = (os.environ.get(base.LLAMACPP_SERVER_URL_ENV_NAME) or "").strip()
                if configured_server_path or configured_server_url:
                    return "explicit_configured_llamacpp"
                if not capabilities.get("in_wsl"):
                    return "default_local_runtime"
                if capabilities.get("windows_interop_available"):
                    return "cached_windows_llamacpp"
                return "native_wsl_llamacpp"

            def _runtime_fact_block(self, prompt: str) -> str:
                capabilities = self._runtime_capabilities()
                semantic = _analyze_semantic_intent(prompt)
                decision = getattr(self, "_route_decision", None)
                preferred_python = "none"
                preferred_python_path = capabilities.get("preferred_python_path")
                if preferred_python_path:
                    with suppress(Exception):
                        preferred_python = str(base.relative_path(Path(str(preferred_python_path)), self.workspace))
                if capabilities.get("in_wsl"):
                    interop_state = "available" if capabilities.get("windows_interop_available") else "unavailable"
                else:
                    interop_state = "not_applicable"
                lines = [
                    f"- Task class: {self._task_class_label(prompt)}",
                    f"- Semantic speech act: {semantic.speech_act}",
                    f"- Requested actions: {', '.join(sorted(semantic.requested_actions)) or 'none'}",
                    f"- Prohibited actions: {', '.join(sorted(semantic.prohibited_actions)) or 'none'}",
                    f"- Action objects and scope: {', '.join(sorted(semantic.objects)) or 'none'}; {semantic.execution_scope}",
                    f"- Selected route: {decision.selected_route if isinstance(decision, RouteDecision) else self._selected_route_name(prompt)}",
                    f"- Route profile: {decision.profile if isinstance(decision, RouteDecision) else ROUTE_PROFILE_AGENTIC_QWEN_24G}",
                    f"- Runtime: {'wsl' if capabilities.get('in_wsl') else 'non_wsl'}",
                    f"- Workspace kind: {'windows_backed' if capabilities.get('workspace_is_windows_backed') else 'wsl_native'}",
                    f"- Windows executable interop: {interop_state}",
                    f"- Workspace WSL python: {'present' if capabilities.get('workspace_has_wsl_python') else 'absent'}",
                    f"- Workspace Windows python: {'present' if capabilities.get('workspace_has_windows_python') else 'absent'}",
                    f"- Preferred project python: {preferred_python}",
                    f"- Preferred project runner: {capabilities.get('preferred_python_runner', 'none') or 'none'}",
                    f"- Preferred llama runtime: {self._preferred_llama_runtime_label(capabilities)}",
                    (
                        f"- Route reason: {decision.reason}"
                        if isinstance(decision, RouteDecision)
                        else "- Route reason: heuristic selection based on the current prompt."
                    ),
                    "- These runtime facts are authoritative for this task. Do not override or re-decide them.",
                ]
                return "\n".join(lines)

            def _select_task_guidance(self, prompt: str) -> str:
                selected_route = self._selected_route_name(prompt)
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    return (
                        "- This is a simple direct question.\n"
                        "- Answer directly.\n"
                        "- Do not use tools unless they are truly necessary.\n"
                    )
                if selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    capabilities = self._runtime_capabilities()
                    guidance = [
                        "- This is a foreground existing-project task.",
                        "- Prefer the named existing entrypoint before exploratory tool use.",
                        "- Use the wrapper-detected execution facts below; do not invent a different environment story.",
                    ]
                    if capabilities.get("workspace_has_wsl_python"):
                        guidance.append("- This workspace has a WSL project interpreter at .venv/bin/python; prefer that path first.")
                    if capabilities.get("can_run_windows_project_python"):
                        guidance.append("- This workspace also has a Windows project interpreter that is executable from the current session.")
                    if self._wsl_windows_interop_unavailable():
                        guidance.append(
                            "- WSL Windows-executable interop is unavailable here, so do not use powershell.exe, Activate.ps1, or Windows .exe paths."
                        )
                    elif self._wsl_windows_interop_available():
                        guidance.append(
                            "- WSL Windows-executable interop is available here, but still prefer the shortest wrapper-approved path."
                        )
                    guidance.extend(
                        [
                            "- Do not create a temporary helper script when an existing workspace entrypoint already covers the task unless the direct entrypoint path has already failed.",
                            "- Do not modify or replace an existing workspace entrypoint unless the user explicitly asked for edits.",
                            "- Do not claim the task is complete unless you actually have the requested final result in hand.",
                        ]
                    )
                    return "\n".join(guidance)
                if selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    return (
                        "- This is a read-only workspace task.\n"
                        "- Read the minimum relevant files or retrieval context first.\n"
                        "- Do not edit files or trigger heavier execution unless it becomes necessary.\n"
                        "- Return only the requested result, concisely.\n"
                        "- Do not add reasoning, assumptions, evidence-status, or next-step sections unless the user requested them.\n"
                    )
                if selected_route == ROUTE_TOOL_LOOP:
                    return (
                        "- This prompt explicitly leans on tools, commands, MCP, or verification.\n"
                        "- Prefer grounded local tool results over speculation.\n"
                        "- Do not loop on the same failing route.\n"
                    )
                if selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    return (
                        "- Essential context appears to be missing.\n"
                        "- Ask the user for the missing identifier, file, script, or target before making edits or claims.\n"
                    )
                return ""

            @staticmethod
            def _tool_name(tool: Any) -> str:
                if isinstance(tool, dict):
                    function = tool.get("function")
                    if isinstance(function, dict):
                        return str(function.get("name", "") or "")
                    return str(tool.get("name", "") or "")
                return str(getattr(tool, "name", "") or "")

            def _filter_tools_by_intent(self, prompt: str, tools: Sequence[Any]) -> list[Any]:
                lowered = prompt.lower()
                mutation_forbidden = bool(
                    re.search(
                        r"\b(?:do not|don't|never|without)\b[^\n.;]{0,160}\b(?:create|edit|modify|change|write|delete|remove|move|rename)\w*\b",
                        lowered,
                    )
                )
                execution_forbidden = bool(
                    re.search(
                        r"\b(?:do not|don't|never|without)\b[^\n.;]{0,160}\b(?:run|execute|start|serve|build|compile|test|lint|benchmark|deploy|command)\w*\b",
                        lowered,
                    )
                )
                read_intent = bool(base.READ_INTENT_PATTERN.search(prompt)) or bool(
                    re.search(r"\b(explain|summarize|analyse|analyze|compare|find|search|review|inspect)\b", lowered)
                )
                edit_intent = bool(base.EDIT_INTENT_PATTERN.search(prompt)) and not mutation_forbidden
                verify_intent = bool(base.VERIFY_INTENT_PATTERN.search(prompt))
                execute_intent = bool(
                    re.search(r"\b(run|execute|start|serve|build|compile|test|lint|benchmark|deploy)\b", lowered)
                ) and not execution_forbidden
                install_intent = bool(re.search(r"\b(install|dependency|dependencies|package|requirements)\b", lowered))
                browser_intent = bool(re.search(r"\b(browser|tab|tabs|url|urls|http|https)\b", lowered))
                mcp_intent = bool(re.search(r"\b(mcp|model context protocol)\b", lowered))
                skill_intent = bool(re.search(r"\b(skill|skills)\b", lowered))
                memory_intent = bool(re.search(r"\b(memory|remember|recall)\b", lowered))
                sandbox_intent = bool(re.search(r"\b(sandbox|isolated)\b", lowered))
                background_intent = bool(re.search(r"\b(background|job|jobs)\b", lowered))
                known_intent = any(
                    (
                        edit_intent,
                        read_intent,
                        verify_intent,
                        execute_intent,
                        install_intent,
                        browser_intent,
                        mcp_intent,
                        skill_intent,
                        memory_intent,
                        sandbox_intent,
                        background_intent,
                    )
                )
                if not known_intent:
                    return list(tools)
                destructive_intent = not mutation_forbidden and bool(re.search(r"\b(delete|remove|erase)\b", lowered))
                move_intent = not mutation_forbidden and bool(re.search(r"\b(move|rename)\b", lowered))
                create_intent = not mutation_forbidden and bool(re.search(r"\b(create|add|make|mkdir)\b", lowered))
                selected: list[Any] = []
                for tool in tools:
                    name = self._tool_name(tool)
                    normalized = name.lower()
                    allowed = normalized in {"read_file", "list_files", "search_text"}
                    if memory_intent and ("memory" in normalized or "recall" in normalized):
                        allowed = True
                    if skill_intent and "skill" in normalized:
                        allowed = True
                    if edit_intent and normalized in {"write_file", "replace_text"}:
                        allowed = True
                    if edit_intent and create_intent and normalized in {"make_directory", "write_file"}:
                        allowed = True
                    if edit_intent and move_intent and normalized == "move_path":
                        allowed = True
                    if edit_intent and destructive_intent and normalized == "delete_path":
                        allowed = True
                    if (execute_intent or verify_intent or edit_intent) and (
                        normalized.startswith("run_") or "command" in normalized
                    ):
                        allowed = True
                    if install_intent and ("install" in normalized or "package" in normalized):
                        allowed = True
                    if browser_intent and ("browser" in normalized or "url" in normalized):
                        allowed = True
                    if mcp_intent and "mcp" in normalized:
                        allowed = True
                    if sandbox_intent and "sandbox" in normalized:
                        allowed = True
                    if background_intent and ("background" in normalized or "job" in normalized):
                        allowed = True
                    if allowed:
                        selected.append(tool)
                return selected

            def _filter_cached_tool_definitions(self, prompt: str) -> None:
                if self._tool_definitions_cache is None:
                    return
                filtered = self._prioritize_tools_for_prompt(prompt, self._tool_definitions_cache)
                self._tool_definitions_cache = list(filtered)
                self._tool_count_cache = len(filtered)

            def _prioritize_tools_for_prompt(self, prompt: str, tools: Sequence[Any]) -> list[Any]:
                selected_route = self._selected_route_name(prompt)
                if selected_route == ROUTE_SIMPLE_ANSWER:
                    return []
                if selected_route == ROUTE_READ_ONLY_WORKSPACE:
                    if self._build_read_only_workspace_context(prompt):
                        return []
                    read_only_names = {"read_file", "list_files", "search_text"}
                    return [
                        tool
                        for tool in tools
                        if self._tool_name(tool) in read_only_names
                    ]
                if selected_route != ROUTE_DIRECT_EXISTING_ENTRYPOINT:
                    return self._filter_tools_by_intent(prompt, tools)
                preflight_ready = bool(self._existing_script_preflight_context)
                capabilities = self._runtime_capabilities()
                in_wsl = bool(capabilities.get("in_wsl"))
                interop_unavailable = bool(in_wsl and not capabilities.get("windows_interop_available"))
                powershell_available_for_workspace = bool(
                    not in_wsl
                    or (
                        capabilities.get("workspace_is_windows_backed")
                        and capabilities.get("windows_interop_available")
                    )
                )
                primary_names = {
                    "read_file",
                    "list_files",
                    "search_text",
                    "run_project_command",
                }
                if powershell_available_for_workspace:
                    primary_names.add("run_powershell_command")
                fallback_names = {
                    "start_project_mcp_server",
                    "list_project_mcp_tools",
                    "stop_project_mcp_server",
                    "read_project_mcp_server_log",
                }
                mutation_names = {
                    "write_file",
                    "replace_text",
                    "delete_path",
                    "move_path",
                    "make_directory",
                    "sandbox_write_file",
                    "sandbox_replace_text",
                    "sandbox_delete_path",
                    "sandbox_move_path",
                    "sandbox_make_directory",
                }
                ordered_tools: list[tuple[tuple[int, int], Any]] = []
                for index, tool in enumerate(tools):
                    name = self._tool_name(tool)
                    if name in mutation_names:
                        continue
                    if name.startswith("sandbox_") or name == "list_sandboxes":
                        continue
                    if preflight_ready and name in {"run_project_command", "start_project_mcp_server", "list_project_mcp_tools"}:
                        continue
                    if (
                        not powershell_available_for_workspace
                        and (name == "run_powershell_command" or name.startswith("sandbox_run_powershell"))
                    ) or (
                        interop_unavailable
                        and name in {
                            "start_project_mcp_server",
                            "list_project_mcp_tools",
                            "stop_project_mcp_server",
                            "read_project_mcp_server_log",
                        }
                    ):
                        continue
                    if name in primary_names:
                        bucket = 0
                    elif name in fallback_names:
                        bucket = 2
                    else:
                        continue
                    ordered_tools.append(((bucket, index), tool))
                ordered_tools.sort(key=lambda item: item[0])
                return [tool for _, tool in ordered_tools]

            def __init__(self, config: Any) -> None:
                if getattr(config, "runtime_workspace", None) is None:
                    config.runtime_workspace = runtime_workspace
                super().__init__(config)
                self._foreground_run_lock = threading.RLock()
                self.local_only_config = LocalOnlyConfig.load(self.runtime_workspace, self.workspace)
                plugins_enabled = self.local_only_config.plugins_enabled and _local_plugins_available(
                    self.runtime_workspace,
                    self.workspace,
                )
                sandbox_tools_enabled = _sandbox_features_enabled(self.local_only_config)
                self.local_plugins = (
                    LocalPluginRegistry(self.runtime_workspace, self.workspace)
                    if plugins_enabled
                    else _DisabledLocalPluginRegistry()
                )
                self.background_jobs = (
                    LocalBackgroundJobManager(self.runtime_workspace, wrapper_script)
                    if self.local_only_config.background_jobs_enabled
                    else _DisabledLocalBackgroundJobManager()
                )
                self.local_sandboxes = (
                    LocalSandboxManager(
                        self.workspace,
                        self.runtime_workspace,
                        getattr(base, "EXCLUDED_DIRS", set()),
                        base=base,
                    )
                    if sandbox_tools_enabled
                    else _DisabledLocalSandboxManager()
                )
                self._local_plugin_context = "None"
                self._task_case_guidance = ""
                self._runtime_fact_context = ""
                self._simple_direct_mode = False
                self._existing_script_preflight_context = ""
                self._existing_script_browser_urls = []
                self._existing_script_preflight_succeeded = False
                self._existing_script_direct_answer = ""
                self._route_decision: RouteDecision | None = None
                self._route_fallback_chain: list[str] = []
                self._route_phase_timings: dict[str, float] = {}

            def run_sync(
                self,
                prompt: str,
                callback: Callable[[str, str], None] | None = None,
            ) -> str:
                with self._foreground_run_lock:
                    return self._run_sync_locked(prompt, callback)

            def _run_sync_locked(
                self,
                prompt: str,
                callback: Callable[[str, str], None] | None = None,
            ) -> str:
                started_at = time.perf_counter()
                marks: dict[str, float] = {}

                def mark(name: str) -> None:
                    marks.setdefault(name, time.perf_counter())

                def timed_callback(kind: str, message: str) -> None:
                    if kind == "status":
                        if message.startswith("Wrapper route:") or "asking for clarification" in message:
                            mark("route_ready")
                        if message.startswith(("Preparing repository context", "Retrieval cache", "Loaded cached retrieval")):
                            mark("retrieval_start")
                        if message.startswith(("Loading embedder ", "Restoring the embedder to CUDA")):
                            mark("embedder_start")
                        if message.startswith(("Embedder active on ", "Keeping the embedder on CPU")):
                            mark("embedder_ready")
                        if message.startswith("Encoding ") and " query chunk(s)" in message:
                            mark("query_embedding_start")
                        if message.startswith("Repository context ready"):
                            mark("retrieval_ready")
                            mark("query_embedding_ready")
                        if message.startswith("Detecting the local llama.cpp"):
                            mark("model_ready_start")
                        if message.startswith("llama.cpp model ready:"):
                            mark("model_ready")
                        if message.startswith("Model step 1/"):
                            mark("first_model_step")
                    elif kind in {"tool", "assistant_delta"}:
                        mark("first_model_response")
                    if callback is not None:
                        callback(kind, message)

                try:
                    return super().run_sync(prompt, timed_callback)
                finally:
                    finished_at = time.perf_counter()
                    if "first_model_step" in marks and "first_model_response" not in marks:
                        marks["first_model_response"] = finished_at
                    timings: dict[str, float] = {
                        "total_seconds": round(finished_at - started_at, 4),
                    }

                    def add_span(name: str, start_name: str, end_name: str) -> None:
                        if start_name in marks and end_name in marks:
                            timings[name] = round(max(0.0, marks[end_name] - marks[start_name]), 4)

                    if "route_ready" in marks:
                        timings["route_seconds"] = round(max(0.0, marks["route_ready"] - started_at), 4)
                    add_span("retrieval_seconds", "retrieval_start", "retrieval_ready")
                    add_span("embedder_load_or_restore_seconds", "embedder_start", "embedder_ready")
                    add_span("query_embedding_seconds", "query_embedding_start", "query_embedding_ready")
                    add_span("model_ready_seconds", "model_ready_start", "model_ready")
                    add_span("first_model_response_seconds", "first_model_step", "first_model_response")
                    self._last_phase_timings = timings
                    if callback is not None:
                        rendered = ", ".join(
                            f"{name.removesuffix('_seconds')}={value:.3f}s" for name, value in timings.items()
                        )
                        callback("status", f"Phase timings: {rendered}.")

            async def _run_async(self, prompt: str, callback: Callable[[str, str], None] | None = None) -> str:
                user_prompt = prompt
                run_started_at = time.perf_counter()
                if _prompt_requests_project_launch(base, user_prompt):
                    return self._run_project_launch_path(user_prompt, callback)
                route_started_at = time.perf_counter()
                self._route_decision = self._decide_route(user_prompt)
                self._route_fallback_chain = [self._route_decision.selected_route]
                self._route_phase_timings = {
                    "route_detect_seconds": round(time.perf_counter() - route_started_at, 4)
                }
                if self._route_decision.selected_route == ROUTE_ASK_USER_MISSING_INFO:
                    self._emit(
                        callback,
                        "status",
                        "The project-use request lacks a concrete result or target; skipping retrieval and asking for clarification.",
                    )
                    return PROJECT_GOAL_CLARIFICATION
                bypass_retrieval = self._route_decision.selected_route == ROUTE_SIMPLE_ANSWER
                foreground_existing_script_task = self._route_decision.selected_route == ROUTE_DIRECT_EXISTING_ENTRYPOINT
                read_only_workspace_task = self._route_decision.selected_route == ROUTE_READ_ONLY_WORKSPACE
                self._local_plugin_context = (
                    self.local_plugins.render_for_prompt(user_prompt)
                    if self.local_only_config.plugins_enabled and not bypass_retrieval and not read_only_workspace_task
                    else "None"
                )
                self._task_case_guidance = self._select_task_guidance(user_prompt)
                self._runtime_fact_context = self._runtime_fact_block(user_prompt)
                if self._local_plugin_context != "None":
                    self._emit(callback, "status", "Activated local plugin guidance from .qubitz/plugins.")
                self._emit(
                    callback,
                    "status",
                    (
                        "Local-only mode active: use local runtimes and tools, and do not rely on paid cloud "
                        "services, hosted inference APIs, or remote execution."
                    ),
                )
                route_status = self._route_status_message()
                if route_status:
                    self._emit(callback, "status", route_status)
                runtime_capabilities = self._runtime_capabilities()
                in_wsl_session = bool(runtime_capabilities.get("in_wsl"))
                interop_available = bool(runtime_capabilities.get("windows_interop_available"))
                configured_server_path = (
                    getattr(self.config, "llama_server_path", "") or os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME) or ""
                ).strip()
                configured_server_url = (os.environ.get(base.LLAMACPP_SERVER_URL_ENV_NAME) or "").strip()
                if in_wsl_session and interop_available:
                    self._emit(
                        callback,
                        "status",
                        "WSL Windows-executable interop probe succeeded; direct Windows PowerShell and .exe paths are available from this session.",
                    )
                if (
                    in_wsl_session
                    and not interop_available
                    and not configured_server_path
                    and not configured_server_url
                    and base.normalize_base_url(self.config.server_url) == base.DEFAULT_LLAMACPP_BASE_URL
                ):
                    self._emit(
                        callback,
                        "status",
                        (
                            "WSL Windows-executable interop is unavailable in this session; "
                            "using the native WSL llama.cpp backend instead of the cached Windows runtime."
                        ),
                    )
                    runtime_info = base.ensure_project_local_native_llamacpp_runtime(self.runtime_workspace)
                    self.config.llama_server_path = str(runtime_info["executable"])
                    self._emit(
                        callback,
                        "status",
                        (
                            f"Native WSL llama.cpp backend ready at {runtime_info['executable']} "
                            f"(log: {runtime_info['build_log']})"
                        ),
                    )
                original_format_context = None
                original_should_skip_repo_retrieval = self._should_skip_repo_retrieval
                original_memory_build_context = self.memory.build_context
                original_skills = self.skills
                original_emit = self._emit
                original_cache = self._tool_definitions_cache
                original_count = self._tool_count_cache
                original_list_tools = base.MCPHost.list_tools
                original_temperature = getattr(self.config, "temperature", None)
                original_top_p = getattr(self.config, "top_p", None)
                original_min_p = getattr(self.config, "min_p", None)
                original_repeat_penalty = getattr(self.config, "repeat_penalty", None)
                if bypass_retrieval:
                    class _DisabledSkillRegistry:
                        warnings: list[str] = []

                        def count(self) -> int:
                            return 0

                        def select_for_prompt(self, _prompt: str) -> list[Any]:
                            return []

                        def render_active_context(self, _active_skills: list[Any]) -> str:
                            return ""

                    def _filtered_emit(
                        callback_arg: Callable[[str, str], None] | None,
                        kind: str,
                        message: str,
                    ) -> None:
                        if kind == "status":
                            if message == "Preparing repository context before loading the generation model to keep more VRAM free for embeddings.":
                                return
                            if " local skill(s) from .skills." in message:
                                return
                            if message.startswith("Retrieval GPU policy:"):
                                return
                            if message.startswith("Retrieval backend preference:"):
                                return
                            if message.startswith("Adaptive repository context budget:"):
                                return
                            if message == "Skipping repository retrieval for this explicit file-read request so the model can inspect the target file with read_file first.":
                                message = "Repository retrieval and memory-context preparation were skipped for this simple direct question."
                            elif message == "Memory context prepared; repository retrieval was skipped.":
                                return
                        return original_emit(callback_arg, kind, message)

                    self._simple_direct_mode = True
                    self._local_plugin_context = "None"
                    original_format_context = self.retriever.format_context
                    self.skills = _DisabledSkillRegistry()
                    self._emit = _filtered_emit
                    self._should_skip_repo_retrieval = lambda _prompt: True
                    self.memory.build_context = lambda _prompt: ""
                    self.retriever.format_context = lambda _prompt: (
                        "Repository retrieval was intentionally skipped because this is a simple direct question "
                        "that does not require workspace context."
                    )
                    self._tool_definitions_cache = []
                    self._tool_count_cache = 0
                elif foreground_existing_script_task:
                    preflight_started_at = time.perf_counter()
                    self._existing_script_preflight_context = self._run_existing_script_preflight(prompt, callback)
                    self._route_phase_timings["preflight_seconds"] = round(
                        time.perf_counter() - preflight_started_at,
                        4,
                    )
                    if self._existing_script_direct_answer:
                        direct_answer = self._existing_script_direct_answer
                        self._route_phase_timings["total_seconds"] = round(time.perf_counter() - run_started_at, 4)
                        self._record_route_trace(
                            user_prompt,
                            "direct_preflight_answer",
                            answer=direct_answer,
                        )
                        self._simple_direct_mode = False
                        self._local_plugin_context = "None"
                        self._task_case_guidance = ""
                        self._runtime_fact_context = ""
                        self._existing_script_preflight_context = ""
                        self._existing_script_browser_urls = []
                        self._existing_script_preflight_succeeded = False
                        self._existing_script_direct_answer = ""
                        self._prompt_step_retry_override = None
                        self._route_decision = None
                        self._route_fallback_chain = []
                        self._route_phase_timings = {}
                        return direct_answer
                    if self._existing_script_preflight_context:
                        self._route_fallback_chain.append("model_loop_with_preflight_context")
                        prompt = (
                            f"{prompt.rstrip()}\n\n"
                            "Wrapper preflight execution context:\n"
                            f"{self._existing_script_preflight_context}"
                        )
                        original_format_context = self.retriever.format_context
                        self._should_skip_repo_retrieval = lambda _prompt: True
                        self.memory.build_context = lambda _prompt: ""
                        self.retriever.format_context = lambda _prompt: self._existing_script_preflight_context
                        self._emit(
                            callback,
                            "status",
                            "Wrapper focused continuation: preserving verified entrypoint results and skipping broad retrieval while completing only missing postconditions.",
                        )
                    self._tool_definitions_cache = None
                    self._tool_count_cache = 0
                elif read_only_workspace_task:
                    read_only_context = self._build_read_only_workspace_context(user_prompt)
                    if read_only_context:
                        class _DisabledSkillRegistry:
                            warnings: list[str] = []

                            def count(self) -> int:
                                return 0

                            def select_for_prompt(self, _prompt: str) -> list[Any]:
                                return []

                            def render_active_context(self, _active_skills: list[Any]) -> str:
                                return ""

                        def _filtered_emit(
                            callback_arg: Callable[[str, str], None] | None,
                            kind: str,
                            message: str,
                        ) -> None:
                            if kind == "status":
                                if message == "Preparing repository context before loading the generation model to keep more VRAM free for embeddings.":
                                    message = "Wrapper read-only path prepared direct file context without embedding generation."
                                elif " local skill(s) from .skills." in message:
                                    return
                                elif message.startswith("Retrieval GPU policy:"):
                                    return
                                elif message.startswith("Retrieval backend preference:"):
                                    return
                                elif message.startswith("Adaptive repository context budget:"):
                                    return
                                elif message == "Repository and memory context prepared.":
                                    message = "Read-only file context prepared; memory context was skipped."
                            return original_emit(callback_arg, kind, message)

                        self._emit(
                            callback,
                            "status",
                            "Wrapper read-only path: using explicit file excerpts and skipping embeddings, local skills, memory context, and MCP tool loading.",
                        )
                        self._route_fallback_chain.append("model_loop_with_read_only_file_context")
                        original_format_context = self.retriever.format_context
                        self.skills = _DisabledSkillRegistry()
                        self._emit = _filtered_emit
                        self._should_skip_repo_retrieval = lambda _prompt: False
                        self.memory.build_context = lambda _prompt: ""
                        self.retriever.format_context = lambda _prompt: read_only_context
                        self._tool_definitions_cache = []
                        self._tool_count_cache = 0
                    else:
                        self._emit(
                            callback,
                            "status",
                            "Wrapper read-only path found no explicit readable file target; falling back to focused retrieval.",
                        )

                if not bypass_retrieval and not foreground_existing_script_task and not read_only_workspace_task:
                    staged_context = self._build_focused_file_context(user_prompt)
                    staged_label = "focused-file"
                    if not staged_context:
                        staged_context = self._build_metadata_or_lexical_context(user_prompt)
                        staged_label = "metadata/lexical"
                    if staged_context:
                        original_format_context = self.retriever.format_context
                        self.retriever.format_context = lambda _prompt: staged_context
                        self._emit(
                            callback,
                            "status",
                            f"Wrapper {staged_label} path: using verified local context before semantic retrieval.",
                        )

                async def _prompt_aware_list_tools(host_self: Any) -> list[Any]:
                    tools = await original_list_tools(host_self)
                    return self._prioritize_tools_for_prompt(prompt, tools)

                sampling_profile = _sampling_profile_for_route(user_prompt, self._route_decision.selected_route)
                _apply_sampling_profile(self.config, sampling_profile)
                self._emit(callback, "status", _sampling_profile_status(sampling_profile))
                base.MCPHost.list_tools = _prompt_aware_list_tools
                self._filter_cached_tool_definitions(prompt)
                base.set_qubitz_stream_callback(
                    lambda delta: self._emit(callback, "assistant_delta", delta)
                )
                try:
                    first_pass_started_at = time.perf_counter()
                    answer = await super()._run_async(prompt, callback)
                    self._route_phase_timings["first_model_pass_seconds"] = round(
                        time.perf_counter() - first_pass_started_at,
                        4,
                    )
                    retry_step_cap, retry_label = self._retry_step_cap_for_failed_answer(prompt, answer)
                    if retry_step_cap is not None:
                        self._route_fallback_chain.append(f"{self._route_decision.selected_route}_retry_{retry_step_cap}")
                        self._emit(
                            callback,
                            "status",
                            f"First {retry_label} attempt did not produce a usable final answer; retrying once with step cap {retry_step_cap}.",
                        )
                        self._prompt_step_retry_override = retry_step_cap
                        retry_started_at = time.perf_counter()
                        answer = await super()._run_async(prompt, callback)
                        self._route_phase_timings["retry_model_pass_seconds"] = round(
                            time.perf_counter() - retry_started_at,
                            4,
                        )
                        if self._selected_route_name(prompt) == ROUTE_SIMPLE_ANSWER and self._is_retryable_final_answer(prompt, answer):
                            self._route_fallback_chain.append("simple_answer_unlimited_fallback")
                            self._emit(
                                callback,
                                "status",
                                "Fast simple direct question paths did not produce a usable final answer; continuing with an unlimited fallback path until a final answer is produced.",
                            )
                            self._prompt_step_retry_override = -1
                            fallback_started_at = time.perf_counter()
                            answer = await super()._run_async(prompt, callback)
                            self._route_phase_timings["unlimited_fallback_seconds"] = round(
                                time.perf_counter() - fallback_started_at,
                                4,
                            )
                    if self._existing_script_browser_urls:
                        answer = _rewrite_answer_urls_from_helper(answer, self._existing_script_browser_urls)
                    self._route_phase_timings["total_seconds"] = round(time.perf_counter() - run_started_at, 4)
                    self._record_route_trace(user_prompt, "answered", answer=answer)
                    return answer
                except Exception as exc:
                    self._route_phase_timings["total_seconds"] = round(time.perf_counter() - run_started_at, 4)
                    self._record_route_trace(
                        user_prompt,
                        "error",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    raise
                finally:
                    base.set_qubitz_stream_callback(None)
                    base.set_qubitz_reasoning_budget(None)
                    base.set_qubitz_reasoning_mode(None)
                    base.MCPHost.list_tools = original_list_tools
                    if original_temperature is not None:
                        self.config.temperature = original_temperature
                    if original_top_p is not None:
                        self.config.top_p = original_top_p
                    if original_min_p is not None:
                        self.config.min_p = original_min_p
                    if original_repeat_penalty is not None:
                        self.config.repeat_penalty = original_repeat_penalty
                    self._tool_definitions_cache = original_cache
                    self._tool_count_cache = original_count
                    if original_format_context is not None:
                        self.retriever.format_context = original_format_context
                    self.skills = original_skills
                    self._emit = original_emit
                    self._should_skip_repo_retrieval = original_should_skip_repo_retrieval
                    self.memory.build_context = original_memory_build_context
                    self._simple_direct_mode = False
                    self._local_plugin_context = "None"
                    self._task_case_guidance = ""
                    self._runtime_fact_context = ""
                    self._existing_script_preflight_context = ""
                    self._existing_script_browser_urls = []
                    self._existing_script_preflight_succeeded = False
                    self._existing_script_direct_answer = ""
                    self._prompt_step_retry_override = None
                    self._route_decision = None
                    self._route_fallback_chain = []
                    self._route_phase_timings = {}

            def _system_prompt(
                self,
                memory_context: str,
                active_skill_context: str = "",
                history_summary: str = "",
            ) -> str:
                if self._simple_direct_mode:
                    effort_section = _thinking_effort_guidance(getattr(self.config, "thinking_effort", "default"))
                    prompt = textwrap.dedent(
                        """
                        You are AI Agent Qubitz.
                        The user asked a short direct question that does not require workspace context.
                        Answer directly and concisely.
                        Do not use tools unless absolutely necessary.
                        """
                    ).strip()
                    if effort_section:
                        prompt = f"{prompt}\n\nAdditional effort guidance:\n{effort_section}"
                    return prompt
                original = super()._system_prompt("", "", "")
                effort_section = _thinking_effort_guidance(getattr(self.config, "thinking_effort", "default"))
                overlay_lines = [
                    "",
                    "Local-only overlay for this copy:",
                    "- Use only local files, local shells, local MCP servers, and local model runtimes.",
                    "- Prefer the shortest direct project path before broader tool orchestration.",
                    "- Prefer direct interpreter paths over activation-script chains when a local project interpreter is available.",
                    "- If the wrapper reports Windows-executable interop is available, prefer that direct path instead of assuming WSL-only execution.",
                    (
                        "- A direct existing-script preflight already ran for this task; use its exact outputs and "
                        "prefer its captured external URLs."
                        if self._existing_script_preflight_context
                        else "- If an existing script was already executed by the wrapper, prefer its exact outputs over rerunning it."
                    ),
                ]
                if self._runtime_fact_context:
                    overlay_lines.extend(
                        [
                            "",
                            "Authoritative runtime facts for this task:",
                            self._runtime_fact_context,
                        ]
                    )
                if self._task_case_guidance:
                    overlay_lines.extend(
                        [
                            "",
                            "Prompt-specific routing guidance:",
                            self._task_case_guidance,
                        ]
                    )
                if self._local_plugin_context != "None":
                    overlay_lines.extend(
                        [
                            "",
                            "Active local plugins:",
                            self._local_plugin_context,
                        ]
                    )
                if memory_context:
                    overlay_lines.extend(["", "Current memory context:", memory_context])
                if active_skill_context:
                    overlay_lines.extend(["", "Active local skill context:", active_skill_context])
                if history_summary:
                    overlay_lines.extend(["", "Condensed earlier conversation summary:", history_summary])
                overlay = "\n".join(overlay_lines).rstrip()
                combined = original + overlay
                if effort_section:
                    combined = f"{combined}\n\nAdditional effort guidance:\n{effort_section}"
                return combined

        return EnhancedAgentRunner

    def _build_gui_class(self) -> type[Any]:
        base = self.base

        class EnhancedGUI(base.QubitzGUI):
            def _apply_theme(self) -> None:
                super()._apply_theme()
                style = self.ttk.Style(self.root)
                style.configure(
                    "TCombobox",
                    fieldbackground=getattr(base, "UI_PANEL", "#1b1c1f"),
                    background=getattr(base, "UI_PANEL_ALT", "#23252a"),
                    foreground=getattr(base, "UI_TEXT", "#ffffff"),
                    arrowcolor=getattr(base, "UI_TEXT", "#ffffff"),
                    bordercolor=getattr(base, "UI_BORDER", "#2d3036"),
                    lightcolor=getattr(base, "UI_PANEL", "#1b1c1f"),
                    darkcolor=getattr(base, "UI_PANEL", "#1b1c1f"),
                    padding=(4, 4),
                )
                style.map(
                    "TCombobox",
                    fieldbackground=[
                        ("readonly", getattr(base, "UI_PANEL", "#1b1c1f")),
                        ("disabled", getattr(base, "UI_PANEL", "#1b1c1f")),
                    ],
                    foreground=[
                        ("readonly", getattr(base, "UI_TEXT", "#ffffff")),
                        ("disabled", getattr(base, "UI_TEXT_MUTED", "#d7d7d9")),
                    ],
                    selectbackground=[("readonly", getattr(base, "UI_SELECT", "#3c4048"))],
                    selectforeground=[("readonly", getattr(base, "UI_TEXT", "#ffffff"))],
                    background=[("readonly", getattr(base, "UI_PANEL_ALT", "#23252a"))],
                )

            def __init__(self, config: Any) -> None:
                super().__init__(config)
                self._last_user_prompt = ""
                self._link_tag_serial = 0
                self._transcript_tags_ready = False
                self.thinking_effort_var = self.tk.StringVar(
                    master=self.root,
                    value=_display_thinking_effort(getattr(self.config, "thinking_effort", "default")),
                )
                buttons = self.clear_button.master
                self.ttk.Label(buttons, text="Effort").pack(fill="x", pady=(12, 0))
                self.thinking_effort_combo = self.ttk.Combobox(
                    buttons,
                    textvariable=self.thinking_effort_var,
                    values=THINKING_EFFORT_DISPLAY_OPTIONS,
                    state="readonly",
                    width=8,
                )
                self.thinking_effort_combo.pack(fill="x", pady=(4, 0))
                self.thinking_effort_combo.bind("<<ComboboxSelected>>", self._apply_thinking_effort)
                self.transcript.configure(exportselection=False)
                self.prompt_box.configure(exportselection=False)
                self.transcript.bind("<Control-c>", self._handle_copy_shortcut)
                self.transcript.bind("<Control-C>", self._handle_copy_shortcut)
                self.transcript.bind("<Control-a>", self._handle_transcript_select_all)
                self.transcript.bind("<Control-A>", self._handle_transcript_select_all)
                self.prompt_box.bind("<Control-c>", self._handle_copy_shortcut)
                self.prompt_box.bind("<Control-C>", self._handle_copy_shortcut)
                self.root.bind_all("<Control-c>", self._handle_copy_shortcut, add="+")
                self.root.bind_all("<Control-C>", self._handle_copy_shortcut, add="+")
                self.prompt_box.bind("<Up>", self._handle_history_up)
                self._ensure_transcript_tags()
                self._append_transcript(
                    "system",
                    (
                        "Local-only wrapper active. This copy keeps execution local, avoids paid cloud/API "
                        "dependencies, and adds sandbox, code-intel, plugin, and background-job support."
                    ),
                )

            def _handle_transcript_select_all(self, _event: Any) -> str:
                self.transcript.focus_set()
                self.transcript.tag_add("sel", "1.0", "end-1c")
                self.transcript.mark_set("insert", "1.0")
                return "break"

            def _handle_copy_shortcut(self, event: Any) -> str | None:
                def _selected_text(widget: Any) -> str:
                    try:
                        ranges = widget.tag_ranges("sel")
                        if ranges:
                            return widget.get(ranges[0], ranges[-1])
                    except Exception:
                        pass
                    try:
                        if hasattr(widget, "selection_present") and not widget.selection_present():
                            return ""
                        return widget.selection_get()
                    except Exception:
                        return ""

                candidates: list[Any] = []
                event_widget = getattr(event, "widget", None)
                if event_widget is not None:
                    candidates.append(event_widget)
                with suppress(Exception):
                    focused = self.root.focus_get()
                    if focused is not None and focused not in candidates:
                        candidates.append(focused)
                for widget in (self.transcript, self.prompt_box):
                    if widget not in candidates:
                        candidates.append(widget)
                for widget in candidates:
                    selected_text = _selected_text(widget)
                    if not selected_text:
                        continue
                    self.root.clipboard_clear()
                    self.root.clipboard_append(selected_text)
                    self.root.update_idletasks()
                    return "break"
                return None

            def _start_prompt(self, prompt: str, *, queued: bool = False) -> None:
                if queued:
                    remaining = len(self.pending_prompts)
                    remaining_text = (
                        f"{remaining} more queued after this."
                        if remaining
                        else "Queue will be empty after this task."
                    )
                    self._append_transcript("status", f"Starting queued task. {remaining_text}")
                self._append_transcript("user", prompt)
                self._set_busy(True)
                self._gui_run_serial = int(getattr(self, "_gui_run_serial", 0)) + 1
                self._active_gui_run_id = self._gui_run_serial
                worker = threading.Thread(
                    target=self._worker_run_tagged,
                    args=(prompt, self._active_gui_run_id),
                    daemon=True,
                )
                worker.start()

            def _worker_run_tagged(self, prompt: str, run_id: int) -> None:
                def emit(kind: str, message: str) -> None:
                    self.event_queue.put((run_id, kind, message))

                try:
                    answer = self.agent.run_sync(prompt, emit)
                except Exception as exc:
                    details = "".join(base.traceback.format_exception(exc))
                    self.event_queue.put((run_id, "error", details))
                else:
                    self.event_queue.put((run_id, "answer", answer))
                finally:
                    self.event_queue.put((run_id, "done", ""))

            def _append_stream_delta(self, message: str) -> None:
                if not message:
                    return
                self._ensure_transcript_tags()
                if getattr(self, "_stream_preview_start", None) is None:
                    self.transcript.configure(state="normal")
                    self._stream_preview_start = self.transcript.index("end-1c")
                    self.transcript.insert("end", "[assistant] ", ("role_assistant",))
                else:
                    self.transcript.configure(state="normal")
                self.transcript.insert("end", message, ("role_assistant",))
                self.transcript.configure(state="disabled")
                self.transcript.see("end")

            def _clear_stream_preview(self) -> None:
                start = getattr(self, "_stream_preview_start", None)
                if start is None:
                    return
                self.transcript.configure(state="normal")
                self.transcript.delete(start, "end-1c")
                self.transcript.configure(state="disabled")
                self._stream_preview_start = None

            def _poll_events(self) -> None:
                while True:
                    try:
                        item = self.event_queue.get_nowait()
                    except base.queue.Empty:
                        break
                    if len(item) == 3:
                        run_id, kind, message = item
                        if run_id != getattr(self, "_active_gui_run_id", run_id):
                            continue
                    else:
                        kind, message = item
                    if kind == "assistant_delta":
                        self.status_var.set("Generating")
                        self._append_stream_delta(message)
                        continue
                    self._clear_stream_preview()
                    if kind == "status":
                        self.status_var.set(message)
                        self._append_transcript("status", message)
                    elif kind == "tool":
                        self._append_transcript("tool", message)
                    elif kind == "answer":
                        self._append_transcript("assistant", message)
                    elif kind == "error":
                        self._append_transcript("error", message)
                        self.messagebox.showerror("AI Agent Qubitz", message)
                    elif kind == "done":
                        self._set_busy(False)
                        if self.pending_prompts:
                            next_prompt = self.pending_prompts.pop(0)
                            self._update_send_button()
                            self._start_prompt(next_prompt, queued=True)
                self.root.after(100, self._poll_events)

            def _ensure_transcript_tags(self) -> None:
                if getattr(self, "_transcript_tags_ready", False):
                    return
                self.transcript.tag_configure("role_assistant", foreground="#ffffff")
                self.transcript.tag_configure("role_process", foreground="#7fdc8b")
                self.transcript.tag_configure("role_user", foreground="#ffffff")
                self.transcript.tag_configure("role_link", foreground="#7cc9ff", underline=True)
                self.transcript.tag_bind("role_link", "<Enter>", lambda _event: self.transcript.configure(cursor="hand2"))
                self.transcript.tag_bind("role_link", "<Leave>", lambda _event: self.transcript.configure(cursor="xterm"))
                self._transcript_tags_ready = True

            def _open_transcript_link(self, url: str) -> None:
                with suppress(Exception):
                    webbrowser.open(url)

            def _append_transcript(self, role: str, message: str) -> None:
                self._ensure_transcript_tags()
                role_tag = "role_process"
                if role == "user":
                    role_tag = "role_user"
                elif role == "assistant":
                    role_tag = "role_assistant"
                link_pattern = re.compile(r"https?://[^\s<>()]+")
                text = message.strip()
                self.transcript.configure(state="normal")
                self.transcript.insert("end", f"[{role}] ", tuple(tag for tag in (role_tag,) if tag))
                cursor = 0
                for match in link_pattern.finditer(text):
                    start, end = match.span()
                    if start > cursor:
                        self.transcript.insert("end", text[cursor:start], tuple(tag for tag in (role_tag,) if tag))
                    url = match.group(0).rstrip(".,;:!?)]}")
                    trimmed_end = start + len(url)
                    self._link_tag_serial += 1
                    link_tag = f"link_{self._link_tag_serial}"
                    self.transcript.tag_bind(link_tag, "<Double-Button-1>", lambda _event, target=url: self._open_transcript_link(target))
                    self.transcript.insert(
                        "end",
                        url,
                        tuple(tag for tag in (role_tag, "role_link", link_tag) if tag),
                    )
                    if trimmed_end < end:
                        self.transcript.insert("end", text[trimmed_end:end], tuple(tag for tag in (role_tag,) if tag))
                    cursor = end
                if cursor < len(text):
                    self.transcript.insert("end", text[cursor:], tuple(tag for tag in (role_tag,) if tag))
                self.transcript.insert("end", "\n\n")
                self.transcript.configure(state="disabled")
                self.transcript.see("end")

            def _handle_history_up(self, _event: Any) -> str:
                last_prompt = getattr(self, "_last_user_prompt", "").strip()
                if not last_prompt:
                    return "break"
                self.prompt_box.delete("1.0", "end")
                self.prompt_box.insert("1.0", last_prompt)
                self.prompt_box.mark_set("insert", "end-1c")
                return "break"

            def _apply_thinking_effort(self, _event: Any = None) -> None:
                effort = _normalize_thinking_effort(self.thinking_effort_var.get())
                setattr(self.config, "thinking_effort", effort)
                setattr(self.agent.config, "thinking_effort", effort)

            def _set_busy(self, value: bool) -> None:
                super()._set_busy(value)
                if hasattr(self, "thinking_effort_combo"):
                    self.thinking_effort_combo.configure(state="readonly")

            def _sync_runtime_settings(self) -> None:
                super()._sync_runtime_settings()
                self._apply_thinking_effort()

            def _change_workspace(self) -> None:
                super()._change_workspace()
                if hasattr(self, "thinking_effort_var"):
                    effort = _normalize_thinking_effort(self.thinking_effort_var.get())
                    setattr(self.config, "thinking_effort", effort)
                    setattr(self.agent.config, "thinking_effort", effort)

            def send_prompt(self) -> None:
                raw_prompt = self.prompt_box.get("1.0", "end").strip()
                if raw_prompt:
                    self._last_user_prompt = raw_prompt
                lowered = raw_prompt.lower()
                if lowered.startswith("/bg "):
                    prompt = raw_prompt[4:].strip()
                    if not prompt:
                        return
                    self.prompt_box.delete("1.0", "end")
                    if not self.agent.local_only_config.background_jobs_enabled:
                        self._append_transcript("status", "Background jobs are disabled by local_only.toml.")
                        return
                    sandbox_mode = self.agent.local_only_config.background_job_sandbox
                    sandbox_id = None
                    workspace = self.agent.workspace
                    if sandbox_mode and sandbox_mode.lower() not in {"", "off", "none"}:
                        sandbox = self.agent.local_sandboxes.create(label="background-job", mode=sandbox_mode)
                        sandbox_id = sandbox["sandbox_id"]
                        workspace = Path(sandbox["root"])
                    meta = self.agent.background_jobs.start(
                        prompt,
                        config=self.agent.config,
                        workspace=workspace,
                        sandbox_id=sandbox_id,
                    )
                    self._append_transcript("queued", raw_prompt)
                    if sandbox_id:
                        self._append_transcript(
                            "status",
                            f"Started local background job {meta['job_id']} in sandbox {sandbox_id}.",
                        )
                    else:
                        self._append_transcript("status", f"Started local background job {meta['job_id']}.")
                    return
                if lowered in {"/jobs", "/bg-jobs"}:
                    self.prompt_box.delete("1.0", "end")
                    jobs = self.agent.background_jobs.list_jobs()
                    if not jobs:
                        self._append_transcript("status", "No local background jobs recorded.")
                        return
                    lines = [f"{job['job_id']}: {job['status']} ({job['workspace']})" for job in jobs[:20]]
                    self._append_transcript("status", "Local background jobs:\n" + "\n".join(lines))
                    return
                if lowered.startswith("/job "):
                    self.prompt_box.delete("1.0", "end")
                    job_id = raw_prompt.split(None, 1)[1].strip()
                    if not job_id:
                        return
                    payload = self.agent.background_jobs.read_job(job_id)
                    summary = json.dumps(payload["meta"], ensure_ascii=False, indent=2)
                    self._append_transcript("status", summary)
                    if payload["log"]:
                        self._append_transcript("tool", payload["log"])
                    return
                if lowered.startswith("/cancel-job "):
                    self.prompt_box.delete("1.0", "end")
                    job_id = raw_prompt.split(None, 1)[1].strip()
                    if not job_id:
                        return
                    payload = self.agent.background_jobs.cancel_job(job_id)
                    self._append_transcript(
                        "status",
                        f"Background job {job_id} cancellation requested: {bool(payload.get('cancelled'))}.",
                    )
                    return
                return super().send_prompt()

        return EnhancedGUI

    def _build_config(self, args: Any, workspace: Path) -> Any:
        kwargs: dict[str, Any] = {
            "workspace": workspace,
            "runtime_workspace": self.runtime_workspace,
        }
        if hasattr(args, "model"):
            kwargs["model_name"] = args.model
        if hasattr(args, "embed_model"):
            kwargs["embed_model_name"] = args.embed_model
        if hasattr(args, "max_steps"):
            kwargs["max_steps"] = args.max_steps
        if hasattr(args, "num_ctx"):
            if "ollama_num_ctx" in getattr(self.base.AgentConfig, "__dataclass_fields__", {}):
                kwargs["ollama_num_ctx"] = args.num_ctx
            elif "num_ctx" in getattr(self.base.AgentConfig, "__dataclass_fields__", {}):
                kwargs["num_ctx"] = args.num_ctx
        if hasattr(args, "num_predict"):
            if "ollama_num_predict" in getattr(self.base.AgentConfig, "__dataclass_fields__", {}):
                kwargs["ollama_num_predict"] = args.num_predict
            elif "num_predict" in getattr(self.base.AgentConfig, "__dataclass_fields__", {}):
                kwargs["num_predict"] = args.num_predict
        if hasattr(args, "model_path"):
            kwargs["model_path"] = args.model_path or os.environ.get(getattr(self.base, "GGUF_MODEL_PATH_ENV_NAME", ""))
        if hasattr(args, "server_url"):
            server_url = args.server_url or os.environ.get(getattr(self.base, "LLAMACPP_SERVER_URL_ENV_NAME", ""))
            if server_url and hasattr(self.base, "normalize_base_url"):
                kwargs["server_url"] = self.base.normalize_base_url(server_url)
        if hasattr(args, "llama_server"):
            kwargs["llama_server_path"] = args.llama_server
        if "selected_model_filename" in getattr(self.base.AgentConfig, "__dataclass_fields__", {}):
            kwargs["selected_model_filename"] = getattr(self.base, "DEFAULT_GGUF_MODEL_FILENAME")
        config = self.base.AgentConfig(**kwargs)
        setattr(config, "thinking_effort", _normalize_thinking_effort(getattr(args, "thinking_effort", "default")))
        return config

    def run_cli(self, config: Any, initial_prompt: str | None = None) -> None:
        for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
            reconfigure = getattr(stream, "reconfigure", None)
            if callable(reconfigure):
                try:
                    reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

        def _write_console(text: Any = "") -> None:
            rendered = str(text)
            try:
                print(rendered)
                return
            except UnicodeEncodeError:
                stream = getattr(sys, "stdout", None)
                encoding = getattr(stream, "encoding", None) or "utf-8"
                data = (rendered + os.linesep).encode(encoding, errors="replace")
                buffer = getattr(stream, "buffer", None)
                if buffer is not None:
                    buffer.write(data)
                    buffer.flush()
                    return
                if stream is not None:
                    stream.write(data.decode(encoding, errors="replace"))
                    stream.flush()

        runner = self.base.AgentRunner(config)

        def emit(kind: str, message: str) -> None:
            _write_console(f"[{kind}] {message}")

        if initial_prompt:
            if initial_prompt.lower().startswith("/bg "):
                prompt = initial_prompt[4:].strip()
                if not runner.local_only_config.background_jobs_enabled:
                    raise RuntimeError("Background jobs are disabled by local_only.toml.")
                sandbox_mode = runner.local_only_config.background_job_sandbox
                sandbox_id = None
                workspace = runner.workspace
                if sandbox_mode and sandbox_mode.lower() not in {"", "off", "none"}:
                    sandbox = runner.local_sandboxes.create(label="background-job", mode=sandbox_mode)
                    sandbox_id = sandbox["sandbox_id"]
                    workspace = Path(sandbox["root"])
                meta = runner.background_jobs.start(prompt, config=config, workspace=workspace, sandbox_id=sandbox_id)
                _write_console(json.dumps(meta, ensure_ascii=False, indent=2))
                return
            if initial_prompt.lower() in {"/jobs", "/bg-jobs"}:
                _write_console(json.dumps(runner.background_jobs.list_jobs(), ensure_ascii=False, indent=2))
                return
            _write_console(runner.run_sync(initial_prompt, emit))
            return
        _write_console(f"{self.display_name} CLI. Type 'exit' to stop.")
        while True:
            try:
                prompt = input("> ").strip()
            except EOFError:
                _write_console()
                break
            if prompt.lower() in {"exit", "quit"}:
                break
            if not prompt:
                continue
            if prompt.lower().startswith("/bg "):
                payload = runner.background_jobs.start(
                    prompt[4:].strip(),
                    config=config,
                    workspace=runner.workspace,
                )
                _write_console(json.dumps(payload, ensure_ascii=False, indent=2))
                continue
            if prompt.lower() in {"/jobs", "/bg-jobs"}:
                _write_console(json.dumps(runner.background_jobs.list_jobs(), ensure_ascii=False, indent=2))
                continue
            answer = runner.run_sync(prompt, emit)
            _write_console(answer)

    def serve_mcp(self, workspace: Path) -> None:
        server = _build_local_mcp_server(self.base, workspace, self.runtime_workspace, self.wrapper_script)
        server.run(transport="stdio")

    def parse_args(self, argv: Sequence[str] | None = None) -> Any:
        raw_args = list(argv) if argv is not None else sys.argv[1:]
        filtered_args: list[str] = []
        thinking_effort = "default"
        index = 0
        while index < len(raw_args):
            arg = raw_args[index]
            if arg == "--thinking-effort":
                if index + 1 >= len(raw_args):
                    raise SystemExit("--thinking-effort requires a value.")
                thinking_effort = _parse_thinking_effort_cli_value(raw_args[index + 1])
                index += 2
                continue
            if arg.startswith("--thinking-effort="):
                thinking_effort = _parse_thinking_effort_cli_value(arg.split("=", 1)[1])
                index += 1
                continue
            filtered_args.append(arg)
            index += 1
        args = self.base.parse_args(filtered_args)
        setattr(args, "thinking_effort", thinking_effort)
        return args

    def main(self, argv: Sequence[str] | None = None) -> None:
        args = self.parse_args(argv)
        workspace = Path(args.workspace).resolve()
        _enable_local_only_environment()
        self.base.configure_project_environment(self.runtime_workspace)
        self.base.ensure_display_environment()
        config = self._build_config(args, workspace)
        if getattr(args, "generate_harness_key", False):
            print(self.base.generate_harness_key())
            return
        if getattr(args, "encrypt_harness", False):
            encrypted_path = self.base.write_encrypted_harness(self.runtime_workspace)
            print(f"Wrote encrypted harness to {encrypted_path}")
            return
        if getattr(args, "serve_mcp", False):
            self.serve_mcp(workspace)
            return
        if getattr(args, "cli", False) or (not os.environ.get("DISPLAY") and sys.platform.startswith("linux")):
            self.run_cli(config, initial_prompt=getattr(args, "prompt", None))
            return
        gui = self.base.QubitzGUI(config)
        gui.run()


def build_local_only_app(base_module_name: str, wrapper_script: str, display_name: str) -> LocalOnlyApp:
    return LocalOnlyApp(base_module_name, wrapper_script, display_name)

_APP = build_local_only_app(
    _EMBEDDED_BASE_MODULE_LABEL,
    __file__,
    f"{Path(__file__).stem.replace('_', ' ')} Local-Only",
)


def _install_workspace_picker_interop_patch(app: LocalOnlyApp) -> None:
    base = app.base
    original_picker = base.pick_workspace_directory
    if getattr(original_picker, "_QUBITZ_WSL_INTEROP_FALLBACK", False):
        return

    def _pick_workspace_directory(
        current_workspace: Path,
        filedialog: Any,
        parent: Any | None = None,
    ) -> Path | None:
        try:
            return original_picker(current_workspace, filedialog, parent=parent)
        except OSError:
            if not base.in_wsl():
                raise
            dialog_kwargs: dict[str, Any] = {
                "initialdir": current_workspace.as_posix(),
                "mustexist": True,
            }
            if parent is not None:
                dialog_kwargs["parent"] = parent
            selected = filedialog.askdirectory(**dialog_kwargs)
            if not selected:
                return None
            return base.normalize_workspace_directory(selected).resolve()

    _pick_workspace_directory._QUBITZ_WSL_INTEROP_FALLBACK = True
    base.pick_workspace_directory = _pick_workspace_directory


_install_workspace_picker_interop_patch(_APP)

def _install_gui_temperature_status_patch(app: LocalOnlyApp) -> None:
    base = app.base
    original_gui = base.QubitzGUI
    if getattr(original_gui, "_QUBITZ_TEMPERATURE_STATUS_PATCHED", False):
        return

    class TemperatureStatusGUI(original_gui):
        _QUBITZ_TEMPERATURE_STATUS_PATCHED = True

        def __init__(self, config: Any) -> None:
            super().__init__(config)
            if not hasattr(self, "temperature_var"):
                self.temperature_var = self.tk.StringVar(
                    master=self.root,
                    value=f"{float(getattr(self.config, 'temperature', base.DEFAULT_CHAT_TEMPERATURE)):g}",
                )
                controls = self.num_predict_entry.master
                self.ttk.Label(controls, text="Temperature").pack(side="left", padx=(16, 0))
                self.temperature_entry = self.ttk.Entry(controls, textvariable=self.temperature_var, width=8)
                self.temperature_entry.pack(side="left", padx=(6, 0))
            backend_url = getattr(config, "server_url", "") or base.DEFAULT_LLAMACPP_BASE_URL
            self._append_transcript("system", f"Local llama.cpp backend: {backend_url}")

        def _format_local_skill_status(self) -> str:
            skills = getattr(getattr(self, "agent", None), "skills", None)
            if skills is None:
                return ""
            try:
                count = int(skills.count())
            except Exception:
                count = 0
            if count <= 0:
                return ""
            names: list[str] = []
            try:
                summaries = skills.list_summaries()
            except Exception:
                summaries = []
            for item in summaries:
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("skill_name") or "").strip()
                else:
                    name = str(getattr(item, "name", "") or "").strip()
                if name:
                    names.append(name)
            if names:
                rendered = ", ".join(names[:12])
                if len(names) > 12:
                    rendered += f", +{len(names) - 12} more"
                return f"Local skills loaded: {rendered}"
            return f"Local skills loaded: {count}"

        def _filter_gui_transcript_message(self, role: str, message: str) -> str | None:
            text = message.strip()
            if role in {"status", "system"}:
                if text.startswith("Thinking effort preset:"):
                    return None
                if text == "Adaptive tool-step budget for this request: unlimited.":
                    return None
                if text == "Local skills discovered: 0":
                    return None
                if re.fullmatch(r"Loaded 0 local skill\(s\) from \.skills\.", text):
                    return None
                if text.startswith("Local skills discovered:") or re.fullmatch(
                    r"Loaded \d+ local skill\(s\) from \.skills\.",
                    text,
                ):
                    return self._format_local_skill_status() or None
            return text

        def _append_transcript(self, role: str, message: str) -> None:
            filtered = self._filter_gui_transcript_message(role, message)
            if filtered is None:
                return
            super()._append_transcript(role, filtered)

        def _set_busy(self, value: bool) -> None:
            super()._set_busy(value)
            if hasattr(self, "temperature_entry"):
                self.temperature_entry.configure(state="disabled" if value else "normal")

        def _open_transcript_link(self, url: str) -> None:
            if sys.platform.startswith("linux") and base.in_wsl():
                with suppress(Exception):
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", "", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    return
            with suppress(Exception):
                webbrowser.open(url)

        def _sync_runtime_settings(self) -> None:
            super()._sync_runtime_settings()
            if hasattr(self, "temperature_var"):
                try:
                    temperature = float(self.temperature_var.get().strip())
                except ValueError as exc:
                    raise ValueError("Temperature must be a number.") from exc
                if temperature < 0:
                    raise ValueError("Temperature must be a non-negative number.")
                setattr(self.config, "temperature", temperature)
                setattr(self.agent.config, "temperature", temperature)

    base.QubitzGUI = TemperatureStatusGUI


_install_gui_temperature_status_patch(_APP)


TOOL_EXPOSURE_MAX = 8
TOOL_SEARCH_RESULT_MAX = 8
TRANSACTIONAL_PATCH_MAX_BYTES = 16 * 1024 * 1024
TRANSACTIONAL_PATCH_MAX_REPLACEMENTS = 64
TOOL_AUDIT_FILE_NAME = "tool_audit.jsonl"
_TOOL_CALL_CONTEXT: ContextVar[Any | None] = ContextVar("qubitz_tool_call_broker", default=None)
_TOOL_AUDIT_LOCK = threading.Lock()


def _tool_annotations(base: Any, name: str) -> Any:
    normalized = name.lower()
    destructive = any(token in normalized for token in ("delete", "destroy", "apply_back", "install"))
    read_only = normalized in {
        "fetch_url",
        "search_tools",
        "list_files",
        "read_file",
        "read_file_snapshot",
        "search_text",
        "list_skills",
        "read_skill",
        "read_skill_resource",
        "list_local_plugins",
        "read_local_plugin",
        "read_local_only_config",
        "workspace_symbols",
        "document_symbols",
        "find_symbol_definitions",
        "find_symbol_references",
        "find_symbol_callers",
        "find_symbol_callees",
        "find_symbol_type_info",
        "codeintel_diagnostics",
        "list_project_mcp_tools",
        "read_project_mcp_server_log",
        "list_sandboxes",
        "sandbox_diff",
        "list_background_jobs",
        "read_background_job",
    }
    return base.mcp_types.ToolAnnotations(
        title=name.replace("_", " ").title(),
        readOnlyHint=read_only,
        destructiveHint=destructive,
        idempotentHint=read_only,
        openWorldHint=False,
    )


def _strict_tool_schema(schema: Any) -> Any:
    if isinstance(schema, list):
        return [_strict_tool_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    tightened = {key: _strict_tool_schema(value) for key, value in schema.items()}
    properties = tightened.get("properties")
    if tightened.get("type") == "object" and isinstance(properties, dict):
        tightened.setdefault("additionalProperties", False)
        for name, property_schema in properties.items():
            if not isinstance(property_schema, dict):
                continue
            if name in {"max_entries", "max_results"} and property_schema.get("type") == "integer":
                property_schema.setdefault("minimum", 1)
                property_schema.setdefault("maximum", 500)
            elif name in {"start_line", "end_line"} and property_schema.get("type") == "integer":
                property_schema.setdefault("minimum", 1)
            elif name in {"timeout_seconds", "wait_seconds"} and property_schema.get("type") == "integer":
                property_schema.setdefault("minimum", 1)
                property_schema.setdefault("maximum", 1800)
    return tightened


def _json_schema_errors(value: Any, schema: Any, root_schema: Any | None = None, path: str = "$") -> list[str]:
    if not isinstance(schema, dict):
        return []
    root = root_schema if isinstance(root_schema, dict) else schema
    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#/"):
        target: Any = root
        for part in reference[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                return [f"{path}: unresolved schema reference {reference}"]
            target = target[part]
        return _json_schema_errors(value, target, root, path)
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        if any(not _json_schema_errors(value, candidate, root, path) for candidate in alternatives):
            return []
        return [f"{path}: value does not match any allowed schema"]
    expected_type = schema.get("type")
    valid_type = True
    if expected_type == "object":
        valid_type = isinstance(value, dict)
    elif expected_type == "array":
        valid_type = isinstance(value, list)
    elif expected_type == "string":
        valid_type = isinstance(value, str)
    elif expected_type == "integer":
        valid_type = isinstance(value, int) and not isinstance(value, bool)
    elif expected_type == "number":
        valid_type = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif expected_type == "boolean":
        valid_type = isinstance(value, bool)
    elif expected_type == "null":
        valid_type = value is None
    if not valid_type:
        return [f"{path}: expected {expected_type}, received {type(value).__name__}"]
    errors: list[str] = []
    if expected_type == "object" and isinstance(value, dict):
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for required_name in schema.get("required") or []:
            if required_name not in value:
                errors.append(f"{path}: missing required property {required_name}")
        if schema.get("additionalProperties") is False:
            for extra_name in set(value).difference(properties):
                errors.append(f"{path}: unexpected property {extra_name}")
        for name, item in value.items():
            item_schema = properties.get(name)
            if item_schema is None and isinstance(schema.get("additionalProperties"), dict):
                item_schema = schema["additionalProperties"]
            if item_schema is not None:
                errors.extend(_json_schema_errors(item, item_schema, root, f"{path}.{name}"))
    elif expected_type == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_json_schema_errors(item, schema["items"], root, f"{path}[{index}]"))
    return errors


def _schema_reference_target(schema: Any, root_schema: Any) -> Any:
    if not isinstance(schema, dict):
        return schema
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    target: Any = root_schema
    for part in reference[2:].split("/"):
        if not isinstance(target, dict) or part not in target:
            return schema
        target = target[part]
    return target


def _normalized_schema_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _repair_schema_value(
    value: Any,
    schema: Any,
    root_schema: Any | None = None,
    path: str = "$",
    depth: int = 0,
) -> tuple[Any, list[str]]:
    """Apply one conservative, schema-guided repair pass without inventing values."""
    if not isinstance(schema, dict) or depth > 16:
        return value, []
    root = root_schema if isinstance(root_schema, dict) else schema
    schema = _schema_reference_target(schema, root)
    if not isinstance(schema, dict):
        return value, []
    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(alternatives, list):
        for candidate in alternatives:
            repaired, changes = _repair_schema_value(value, candidate, root, path, depth + 1)
            if not _json_schema_errors(repaired, candidate, root, path):
                return repaired, changes
        return value, []

    expected_type = schema.get("type")
    changes: list[str] = []
    if expected_type == "object":
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        if isinstance(value, str):
            with suppress(Exception):
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    value = parsed
                    changes.append(f"{path}: decoded JSON object string")
        if isinstance(value, dict) and len(value) == 1:
            wrapper_key = next(iter(value))
            if wrapper_key in {"arguments", "input"} and wrapper_key not in properties:
                wrapped = value[wrapper_key]
                if isinstance(wrapped, str):
                    with suppress(Exception):
                        wrapped = json.loads(wrapped)
                if isinstance(wrapped, dict):
                    value = wrapped
                    changes.append(f"{path}: unwrapped {wrapper_key}")
        if not isinstance(value, dict):
            return value, changes

        normalized_properties: dict[str, list[str]] = {}
        for property_name in properties:
            normalized_properties.setdefault(_normalized_schema_key(property_name), []).append(property_name)
        repaired_object: dict[str, Any] = {}
        for raw_name, raw_item in value.items():
            name = str(raw_name)
            target_name = name
            if name not in properties:
                matches = normalized_properties.get(_normalized_schema_key(name), [])
                if len(matches) == 1:
                    target_name = matches[0]
                    changes.append(f"{path}: renamed {name} to {target_name}")
                elif schema.get("additionalProperties") is False:
                    changes.append(f"{path}: removed unexpected property {name}")
                    continue
            item_schema = properties.get(target_name)
            if item_schema is None and isinstance(schema.get("additionalProperties"), dict):
                item_schema = schema["additionalProperties"]
            repaired_item, item_changes = _repair_schema_value(
                raw_item,
                item_schema,
                root,
                f"{path}.{target_name}",
                depth + 1,
            )
            repaired_object[target_name] = repaired_item
            changes.extend(item_changes)
        for property_name, property_schema in properties.items():
            if property_name not in repaired_object and isinstance(property_schema, dict) and "default" in property_schema:
                repaired_object[property_name] = property_schema["default"]
                changes.append(f"{path}.{property_name}: applied schema default")
        return repaired_object, changes

    if expected_type == "array":
        if isinstance(value, str):
            with suppress(Exception):
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    value = parsed
                    changes.append(f"{path}: decoded JSON array string")
        if not isinstance(value, list) and not isinstance(value, (dict, type(None))):
            value = [value]
            changes.append(f"{path}: wrapped scalar as one-item array")
        if isinstance(value, list) and isinstance(schema.get("items"), dict):
            repaired_array: list[Any] = []
            for index, item in enumerate(value):
                repaired_item, item_changes = _repair_schema_value(
                    item,
                    schema["items"],
                    root,
                    f"{path}[{index}]",
                    depth + 1,
                )
                repaired_array.append(repaired_item)
                changes.extend(item_changes)
            return repaired_array, changes
        return value, changes

    if expected_type == "integer" and isinstance(value, str) and re.fullmatch(r"[+-]?\d+", value.strip()):
        changes.append(f"{path}: converted string to integer")
        return int(value.strip()), changes
    if expected_type == "number" and isinstance(value, str) and re.fullmatch(
        r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", value.strip()
    ):
        changes.append(f"{path}: converted string to number")
        return float(value.strip()), changes
    if expected_type == "boolean" and isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        changes.append(f"{path}: converted string to boolean")
        return value.strip().lower() == "true", changes
    return value, changes


def _repair_tool_arguments(arguments: Any, schema: Any) -> tuple[Any, list[str], list[str]]:
    repaired, changes = _repair_schema_value(arguments, schema)
    repaired_errors = _json_schema_errors(repaired, schema)
    return repaired, changes, repaired_errors


def _redacted_tool_arguments(value: Any, key: str = "") -> Any:
    if any(token in key.lower() for token in ("password", "secret", "token", "credential", "api_key")):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(item_key): _redacted_tool_arguments(item_value, str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redacted_tool_arguments(item, key) for item in value]
    return value


@dataclass
class _PendingToolApproval:
    approval_id: str
    fingerprint: str
    tool_name: str
    arguments: dict[str, Any]
    preview: str
    request_prompt: str


class _TextReplacement(TypedDict):
    old_text: str
    new_text: str


class _FileTextPatch(TypedDict):
    path: str
    replacements: list[_TextReplacement]


def _reported_entrypoint_browser_urls(
    entrypoint: dict[str, Any],
    stdout_text: str,
    browser_helper_text: str,
    output_urls: Sequence[str],
    *,
    requested_browser_open: bool,
    return_code: int,
) -> list[str]:
    if not requested_browser_open or return_code != 0 or not output_urls:
        return []
    source_parts = [browser_helper_text]
    raw_path = entrypoint.get("path")
    if raw_path:
        with suppress(OSError, UnicodeDecodeError):
            path = Path(raw_path)
            if path.is_file() and path.stat().st_size <= 2_000_000:
                source_parts.append(path.read_text(encoding="utf-8", errors="ignore"))
    source_text = "\n".join(part for part in source_parts if part)
    if not re.search(
        r"\b(?:Start-Process|webbrowser\.(?:open|open_new|open_new_tab)|os\.startfile)\b"
        r"|\bsubprocess\.(?:call|Popen|run)\b.{0,240}\b(?:firefox|chrome|msedge|browser)\b",
        source_text,
        re.IGNORECASE | re.DOTALL,
    ):
        return []
    matches = list(
        re.finditer(
            r"\b(?:successfully\s+)?opened\s+(\d+)\s+(?:valid\s+)?(?:news\s+)?(?:urls?|links?|tabs?)\b",
            stdout_text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return []
    opened_count = int(matches[-1].group(1))
    if opened_count <= 0 or opened_count > len(output_urls):
        return []
    return list(output_urls[:opened_count])


class _ToolPermissionBroker:
    _READ_TOOLS = {
        "fetch_url",
        "search_tools",
        "list_files",
        "read_file",
        "read_file_snapshot",
        "search_text",
        "list_skills",
        "read_skill",
        "read_skill_resource",
        "list_local_plugins",
        "read_local_plugin",
        "read_local_only_config",
        "workspace_symbols",
        "document_symbols",
        "find_symbol_definitions",
        "find_symbol_references",
        "find_symbol_callers",
        "find_symbol_callees",
        "find_symbol_type_info",
        "codeintel_diagnostics",
        "list_project_mcp_tools",
        "read_project_mcp_server_log",
        "list_sandboxes",
        "sandbox_diff",
        "list_background_jobs",
        "read_background_job",
    }
    _RESTRICTED_MODE_TOOLS = _READ_TOOLS - {"list_project_mcp_tools"}
    _MUTATION_TOOLS = {
        "apply_text_patches",
        "apply_text_patch",
        "copy_path",
        "write_file",
        "replace_text",
        "make_directory",
        "move_path",
        "delete_path",
        "sandbox_write_file",
        "sandbox_replace_text",
        "sandbox_make_directory",
        "sandbox_move_path",
        "sandbox_delete_path",
        "sandbox_apply_back",
        "sandbox_destroy",
    }
    _EXECUTION_TOOLS = {
        "cancel_background_job",
        "call_project_mcp_tool",
        "open_urls_in_browser",
        "run_existing_entrypoint",
        "run_command",
        "run_cmd_command",
        "run_project_command",
        "run_powershell_command",
        "sandbox_run_command",
        "start_project_mcp_server",
        "stop_project_mcp_server",
    }
    _ALWAYS_APPROVAL = {
        "install_python_package",
        "delete_path",
        "sandbox_delete_path",
        "sandbox_apply_back",
        "sandbox_destroy",
    }
    _PATH_KEYS = {
        "path",
        "source",
        "destination",
        "cwd",
        "requirements_file",
        "server_script",
        "python_path",
    }

    def __init__(self, base: Any, workspace: Path, runtime_workspace: Path) -> None:
        self.base = base
        self.workspace = workspace.resolve()
        self.audit_path = runtime_workspace.resolve() / LOCAL_ONLY_DIR_NAME / TOOL_AUDIT_FILE_NAME
        self.pending: dict[str, _PendingToolApproval] = {}
        self.current_prompt = ""
        self.approved_once: set[str] = set()
        self.callback: Callable[[str, str], None] | None = None
        self.evidence: list[dict[str, Any]] = []
        self.schema_repairs: list[dict[str, Any]] = []
        self.approval_context_prompt = ""
        self.required_postconditions: set[str] = set()
        self.focused_missing_postconditions: set[str] = set()
        self.transaction_recovery_required = False
        self.turn_id = ""
        self.provenance_urls: set[str] = set()
        self.invalid_call_counts: dict[str, int] = {}
        self.invalid_stop_requested = False
        self.stop_callback: Callable[[], None] | None = None
        self.transaction_originals: dict[str, dict[str, Any]] = {}
        self.transaction_snapshot_hashes: dict[str, str] = {}
        self.transaction_changed_hashes: dict[str, str] = {}
        self.transaction_explicit_file_targets: set[str] = set()
        self.transaction_recovery_callback: Callable[[], None] | None = None
        self.transaction_recovery_interrupt_requested = False
        self.transaction_nonprogress_calls = 0
        self.transaction_progress_token = ""
        self.access_mode = DEFAULT_ACCESS_MODE

    def set_access_mode(self, value: Any) -> None:
        self.access_mode = _normalize_access_mode(value)

    @staticmethod
    def _fingerprint(name: str, arguments: dict[str, Any]) -> str:
        encoded = json.dumps({"name": name, "arguments": arguments}, ensure_ascii=True, sort_keys=True, default=str)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def begin_turn(self, prompt: str, callback: Callable[[str, str], None] | None) -> None:
        self.turn_id = uuid.uuid4().hex
        self.current_prompt = prompt
        self.callback = callback
        self.approved_once.clear()
        self.evidence.clear()
        self.schema_repairs.clear()
        self.approval_context_prompt = ""
        self.required_postconditions = self._completion_requirements(prompt)
        self.focused_missing_postconditions.clear()
        self.transaction_recovery_required = False
        self.provenance_urls = {url.casefold() for url in _extract_external_urls(prompt)}
        self.invalid_call_counts.clear()
        self.invalid_stop_requested = False
        self.transaction_originals.clear()
        self.transaction_snapshot_hashes.clear()
        self.transaction_changed_hashes.clear()
        self.transaction_explicit_file_targets.clear()
        for candidate in self.base.extract_file_tokens(prompt):
            with suppress(Exception):
                target = self.base.resolve_workspace_path(
                    self.workspace,
                    candidate,
                    allow_missing=False,
                    allow_external=False,
                ).resolve()
                if target.is_file():
                    self.transaction_explicit_file_targets.add(str(target))
        self.transaction_recovery_interrupt_requested = False
        self.transaction_nonprogress_calls = 0
        self.transaction_progress_token = ""
        approval_match = re.search(r"\b(?:approve|approved|allow|proceed|confirm)(?:\s+(?:action\s+)?)?([a-f0-9]{8,64})?\b", prompt.lower())
        if approval_match is None:
            return
        requested_id = (approval_match.group(1) or "").strip()
        candidates = [
            item
            for item in self.pending.values()
            if not requested_id or item.approval_id.startswith(requested_id) or item.fingerprint.startswith(requested_id)
        ]
        if len(candidates) == 1:
            self.approved_once.add(candidates[0].fingerprint)
            self.approval_context_prompt = candidates[0].request_prompt

    def end_turn(self) -> None:
        self.current_prompt = ""
        self.callback = None
        self.approved_once.clear()
        self.evidence.clear()
        self.schema_repairs.clear()
        self.approval_context_prompt = ""
        self.required_postconditions.clear()
        self.focused_missing_postconditions.clear()
        self.transaction_recovery_required = False
        self.turn_id = ""
        self.provenance_urls.clear()
        self.invalid_call_counts.clear()
        self.invalid_stop_requested = False
        self.transaction_originals.clear()
        self.transaction_snapshot_hashes.clear()
        self.transaction_changed_hashes.clear()
        self.transaction_explicit_file_targets.clear()
        self.transaction_recovery_interrupt_requested = False
        self.transaction_nonprogress_calls = 0
        self.transaction_progress_token = ""

    def _emit(self, message: str) -> None:
        if self.callback is not None:
            self.callback("status", message)

    def _audit(self, status: str, name: str, arguments: dict[str, Any], fingerprint: str, reason: str = "") -> None:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": status,
            "tool": name,
            "fingerprint": fingerprint,
            "workspace": str(self.workspace),
            "arguments": _redacted_tool_arguments(arguments),
            "reason": reason,
            "access_mode": self.access_mode,
        }
        with suppress(Exception), _TOOL_AUDIT_LOCK:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True) + "\n")

    def record_schema_repair(self, name: str, original: Any, repaired: Any, changes: list[str]) -> None:
        record = {
            "tool": name.lower(),
            "original": _redacted_tool_arguments(original),
            "repaired": _redacted_tool_arguments(repaired),
            "changes": list(changes),
        }
        self.schema_repairs.append(record)
        fingerprint = self._fingerprint(name.lower(), repaired if isinstance(repaired, dict) else {})
        self._audit("schema_repaired", name.lower(), repaired if isinstance(repaired, dict) else {}, fingerprint, "; ".join(changes))
        self._emit(f"Corrected one malformed {name} tool call using its declared schema.")

    @staticmethod
    def _invalid_call_key(name: str, arguments: dict[str, Any], reason: str) -> str:
        normalized_arguments = dict(arguments)
        for key, value in list(normalized_arguments.items()):
            if key.lower() in {"url", "uri"} and not _is_external_http_url(value):
                normalized_arguments[key] = "<invalid-url>"
            elif key.lower() in {"urls", "uris"} and isinstance(value, list):
                normalized_arguments[key] = [
                    str(item) if _is_external_http_url(item) else "<invalid-url>" for item in value
                ]
        normalized_reason = re.sub(r"\d+", "#", reason.strip().lower())
        encoded = json.dumps(
            {"tool": name.lower(), "arguments": normalized_arguments, "reason": normalized_reason},
            ensure_ascii=True,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def register_invalid_call(self, name: str, arguments: dict[str, Any], reason: str) -> int:
        key = self._invalid_call_key(name, arguments, reason)
        count = self.invalid_call_counts.get(key, 0) + 1
        self.invalid_call_counts[key] = count
        if count > 1:
            self._emit(
                f"Blocked repeated equivalent invalid {name} call after one correction attempt; "
                "use materially corrected arguments or report the unresolved postcondition."
            )
            if not self.invalid_stop_requested and self.stop_callback is not None:
                self.invalid_stop_requested = True
                self.stop_callback()
        return count

    def _transaction_progress_token(self) -> str:
        verified_categories = sorted(
            {
                category
                for evidence in self._current_turn_evidence()
                if evidence.get("success")
                for category in evidence.get("categories", [])
            }
        )
        payload = {
            "snapshots": sorted(self.transaction_snapshot_hashes.items()),
            "changes": sorted(self.transaction_changed_hashes.items()),
            "verified_categories": verified_categories,
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _track_transaction_recovery_progress(self) -> None:
        if not self.transaction_recovery_required:
            self.transaction_nonprogress_calls = 0
            self.transaction_progress_token = self._transaction_progress_token()
            return
        current_token = self._transaction_progress_token()
        if current_token != self.transaction_progress_token:
            self.transaction_progress_token = current_token
            self.transaction_nonprogress_calls = 0
            return
        self.transaction_nonprogress_calls += 1
        if (
            self.transaction_nonprogress_calls >= 4
            and not self.transaction_recovery_interrupt_requested
            and self.transaction_recovery_callback is not None
        ):
            self.transaction_recovery_interrupt_requested = True
            self._emit(
                "Transactional edit made no verified progress across four tool results; "
                "switching to a focused snapshot-and-patch continuation."
            )
            self.transaction_recovery_callback()

    def transaction_recovery_context(self) -> str:
        if self.transaction_changed_hashes:
            return (
                "Wrapper-owned transactional verification continuation:\n"
                "The atomic edit is already committed. Do not edit or create files. "
                "Run only the requested tests or checks with `run_existing_entrypoint`, "
                "then report the verified result."
            )
        return (
            "Wrapper-owned transactional edit continuation:\n"
            "For multiple existing text files, call `apply_text_patches` directly with only each path and its "
            "exact old_text/new_text replacements; the wrapper owns snapshots, hashes, ordering, atomicity, and rollback. "
            "For one existing text file, use the normal snapshot-bound `apply_text_patch` path. "
            "Command tools are verification-only during this continuation.\n"
            "After a successful patch, run only the requested tests or checks and report verified results."
        )

    @staticmethod
    def semantic_argument_error(name: str, arguments: dict[str, Any]) -> str:
        normalized = name.lower()
        if normalized == "fetch_url" and not _is_external_http_url(arguments.get("url")):
            return "fetch_url requires one concrete absolute HTTP or HTTPS URL."
        if normalized == "open_urls_in_browser":
            values = arguments.get("urls")
            if not isinstance(values, list) or not _validated_external_urls(values, SCRIPT_PREFLIGHT_MAX_URLS):
                return "open_urls_in_browser requires at least one concrete absolute HTTP or HTTPS URL."
        return ""

    @staticmethod
    def _structured_result(result: Any) -> dict[str, Any]:
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        return structured if isinstance(structured, dict) else {}

    def _resolved_argument_path(self, arguments: dict[str, Any], key: str) -> Path | None:
        raw_value = arguments.get(key)
        if not isinstance(raw_value, str) or not raw_value.strip():
            return None
        with suppress(Exception):
            return self.base.resolve_workspace_path(
                self.workspace,
                raw_value,
                allow_missing=True,
                allow_external=True,
            ).resolve()
        return None

    def register_transaction_original(self, target: Path, data: bytes, mode: int) -> None:
        resolved = target.resolve()
        digest = hashlib.sha256(data).hexdigest()
        key = str(resolved)
        self.transaction_originals.setdefault(
            key,
            {"data": data, "mode": mode, "sha256": digest},
        )
        self.transaction_snapshot_hashes[key] = digest

    def record_result(self, name: str, arguments: dict[str, Any], result: Any) -> None:
        normalized = name.lower()
        structured = self._structured_result(result)
        status = str(structured.get("status", "")).strip().lower()
        return_code = structured.get("returncode", structured.get("return_code", structured.get("exit_code")))
        success = not bool(getattr(result, "isError", False))
        success = success and return_code in {None, 0}
        success = success and status not in {
            "ambiguous",
            "approval_required",
            "blocked",
            "cancelled",
            "canceled",
            "denied",
            "error",
            "failed",
            "invalid",
            "missing",
            "not_found",
            "timed_out",
            "timeout",
        }
        categories: set[str] = set()
        verified_paths: list[str] = []

        if normalized == "read_file_snapshot" and success:
            target = self._resolved_argument_path(arguments, "path")
            snapshot_hash = str(structured.get("sha256", "")).strip().lower()
            if target is not None and target.is_file() and re.fullmatch(r"[a-f0-9]{64}", snapshot_hash):
                with suppress(OSError):
                    current_data = target.read_bytes()
                    if hashlib.sha256(current_data).hexdigest() == snapshot_hash:
                        key = str(target)
                        self.transaction_originals.setdefault(
                            key,
                            {"data": current_data, "mode": target.stat().st_mode, "sha256": snapshot_hash},
                        )
                        self.transaction_snapshot_hashes[key] = snapshot_hash

        if normalized in {"read_file", "read_file_snapshot"} and success:
            target = self._resolved_argument_path(arguments, "path")
            if target is not None and target.is_file():
                verified_paths.append(str(target))

        if normalized in self._MUTATION_TOOLS and success:
            target: Path | None = None
            if normalized == "apply_text_patches":
                files = structured.get("files")
                success = isinstance(files, list) and len(files) >= 2
                for item in files if isinstance(files, list) else []:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        success = False
                        continue
                    item_target = self._resolved_argument_path({"path": item["path"]}, "path")
                    new_hash = str(item.get("new_sha256", "")).strip().lower()
                    item_verified = bool(
                        item_target is not None
                        and item_target.is_file()
                        and re.fullmatch(r"[a-f0-9]{64}", new_hash)
                        and hashlib.sha256(item_target.read_bytes()).hexdigest() == new_hash
                    )
                    success = success and item_verified
                    if item_verified and item_target is not None:
                        verified_paths.append(str(item_target))
                        if str(item_target) in self.transaction_originals:
                            self.transaction_changed_hashes[str(item_target)] = new_hash
            elif normalized == "sandbox_apply_back":
                applied = structured.get("applied")
                success = isinstance(applied, list) and bool(applied)
                for item in applied if isinstance(applied, list) else []:
                    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                        success = False
                        continue
                    target = self._resolved_argument_path({"path": item["path"]}, "path")
                    action = str(item.get("action", "")).lower()
                    item_verified = target is not None and (
                        (action == "deleted" and not target.exists())
                        or (action != "deleted" and target.exists())
                    )
                    success = success and item_verified
                    if item_verified and target is not None:
                        verified_paths.append(str(target))
                if success and any(str(item.get("action", "")).lower() == "copied" for item in applied):
                    categories.add("created_outputs")
            elif normalized.startswith("sandbox_"):
                success = False
            else:
                path_key = "destination" if normalized in {"copy_path", "move_path"} else "path"
                target = self._resolved_argument_path(arguments, path_key)
                if normalized == "delete_path":
                    success = target is not None and not target.exists()
                else:
                    success = target is not None and target.exists()
                    expected_hash = structured.get("new_sha256")
                    if success and isinstance(expected_hash, str) and target is not None and target.is_file():
                        success = hashlib.sha256(target.read_bytes()).hexdigest() == expected_hash
                if success and target is not None:
                    verified_paths.append(str(target))
            if success:
                self.transaction_recovery_required = False
                categories.add("changed_files")
                if normalized in {"copy_path", "write_file", "make_directory"}:
                    categories.add("created_outputs")
                if normalized == "apply_text_patch" and target is not None:
                    new_hash = str(structured.get("new_sha256", "")).strip().lower()
                    if str(target) in self.transaction_originals and re.fullmatch(r"[a-f0-9]{64}", new_hash):
                        self.transaction_changed_hashes[str(target)] = new_hash

        command_value = arguments.get("command", "")
        command_text = " ".join(str(item) for item in command_value) if isinstance(command_value, list) else str(command_value)
        if not command_text:
            command_text = json.dumps(arguments, ensure_ascii=True, sort_keys=True, default=str)
        raw_evidence_urls = _validated_external_urls(
            [*_structured_evidence_urls(structured), *_extract_external_urls(command_text)],
            SCRIPT_PREFLIGHT_MAX_URLS,
        )
        browser_action = normalized == "open_urls_in_browser" or bool(
            raw_evidence_urls
            and re.search(r"\b(?:start-process|webbrowser|firefox|chrome|edge|open)\b", command_text, re.IGNORECASE)
        )
        if browser_action:
            evidence_urls = [url for url in raw_evidence_urls if url.casefold() in self.provenance_urls]
        else:
            evidence_urls = raw_evidence_urls
            if success:
                self.provenance_urls.update(url.casefold() for url in evidence_urls)
        result_count = _structured_evidence_count(structured)
        if browser_action:
            result_count = len(evidence_urls)
        if not result_count and evidence_urls:
            result_count = len(evidence_urls)
        if success and result_count > 0:
            categories.add("result_count")
        if success and normalized in self._EXECUTION_TOOLS:
            if structured.get("test_runner") or re.search(
                r"\b(?:pytest|unittest|ruff|lint|test|check)\b",
                command_text,
                re.IGNORECASE,
            ):
                categories.add("tests")
            if normalized == "start_project_mcp_server" and status in {"ready", "running", "started"}:
                categories.add("service_started")
            elif re.search(r"\b(?:serve|server|service|mcp)\b", command_text, re.IGNORECASE):
                categories.add("service_started")
        if success and normalized == "stop_project_mcp_server" and bool(structured.get("stopped")):
            categories.add("service_stopped")
        if success and normalized == "list_project_mcp_tools":
            with suppress(TypeError, ValueError):
                if int(structured.get("count") or 0) > 0:
                    categories.add("mcp_ready")
        if success and normalized == "call_project_mcp_tool" and structured.get("result") is not None:
            categories.add("mcp_tool_called")
        explicit_browser_evidence = bool(
            any(key in structured for key in ("opened_urls", "opened_count", "browser"))
            or (
                evidence_urls
                and re.search(r"\b(?:start-process|webbrowser|firefox|chrome|edge|open)\b", command_text, re.IGNORECASE)
            )
        )
        if success and explicit_browser_evidence and evidence_urls:
            categories.add("opened_urls")

        evidence = {
            "turn_id": self.turn_id,
            "tool": normalized,
            "success": success,
            "categories": sorted(categories),
            "paths": verified_paths,
            "status": status,
            "return_code": return_code,
            "result_count": result_count,
            "opened_count": len(evidence_urls) if browser_action and success else 0,
            "urls": evidence_urls,
            "command": (
                _shorten(command_text, WorkspaceDiscoveryMemory._MAX_VALUE_CHARS)
                if normalized in self._EXECUTION_TOOLS
                else ""
            ),
            "evidence_path": str(
                arguments.get("path")
                or arguments.get("server_script")
                or arguments.get("python_path")
                or ""
            ),
            "mcp_target": str(arguments.get("tool_name") or arguments.get("server_script") or ""),
        }
        self.evidence.append(evidence)
        fingerprint = self._fingerprint(normalized, arguments)
        self._audit("completed" if success else "failed", normalized, arguments, fingerprint, status)
        self._track_transaction_recovery_progress()

    @staticmethod
    def _positive_requirement_match(prompt: str, action_pattern: str, target_pattern: str) -> bool:
        denial_expected = bool(
            re.search(
                r"\b(?:must|should)\s+(?:be\s+)?(?:block(?:ed)?|deny|denied|prevent(?:ed)?)\b"
                r"|\b(?:report|verify)\b.{0,50}\b(?:blocked|denial|denied|prevented)\b",
                prompt,
                re.IGNORECASE,
            )
        )
        clauses = [
            clause.strip()
            for clause in re.split(r"(?:[!?;]+(?:\s+|$)|\.(?:\s+|$)|\n+)", prompt)
            if clause.strip()
        ]
        for clause in clauses:
            lowered = clause.lower()
            if not re.search(target_pattern, lowered, re.IGNORECASE):
                continue
            for action_match in re.finditer(action_pattern, lowered, re.IGNORECASE):
                prefix = lowered[max(0, action_match.start() - 100) : action_match.start()]
                suffix = lowered[action_match.end() : action_match.end() + 60]
                negated_before = re.search(
                    r"(?:\bdo\s+not|\bdon't|\bnever|\bmust\s+not|\bshould\s+not|\bwithout"
                    r"|\bavoid(?:ed|ing)?|\bblock(?:ed|ing)?|\bden(?:y|ied|ying)"
                    r"|\bprevent(?:ed|ing)?|\brefus(?:e|ed|ing))"
                    r"(?:\W+\w+){0,8}\W*$",
                    prefix,
                    re.IGNORECASE,
                )
                negated_after = re.match(
                    r"\W*(?:(?:must|should)\s+be|is|was|were|be)\s+"
                    r"(?:blocked|denied|prevented|refused|avoided)\b",
                    suffix,
                    re.IGNORECASE,
                )
                denied_attempt = (
                    denial_expected
                    and bool(re.search(r"\b(?:attempt|try)\b", lowered))
                    and bool(re.search(r"\bwithout\b.{0,40}\bapproval\b", lowered))
                )
                if not negated_before and not negated_after and not denied_attempt:
                    return True
        return False

    @staticmethod
    def _completion_requirements(prompt: str) -> set[str]:
        semantic = _analyze_semantic_intent(prompt)
        requirements = set(semantic.required_postconditions)
        if semantic.speech_act != "request":
            return requirements
        positive = _ToolPermissionBroker._positive_requirement_match
        file_target = (
            r"\b(?:class|code|config|directory|document|feature|file|folder|function|method|module|project|readme|repo|repository|script|source)\b"
            r"|(?:^|[\s`'\"])[a-z0-9_.\\/-]+\.[a-z0-9]{1,12}(?:[\s`'\"]|$)"
        )
        if positive(
            prompt,
            r"\b(?:add|create|delete|edit|fix|implement|modify|move|overwrite|patch|refactor|remove|rename|replace|update|write)\b",
            file_target,
        ):
            requirements.add("changed_files")
        if positive(
            prompt,
            r"\b(?:run|execute|rerun|pass|fix|verify)\b",
            r"\b(?:tests?|pytest|unittest|ruff|lint|checks?)\b",
        ):
            requirements.add("tests")
        if positive(
            prompt,
            r"\b(?:start|launch|run|serve)\b",
            r"\b(?:mcp|server|service|application)\b|\bapp\b(?!\.[a-z0-9])",
        ):
            requirements.add("service_started")
        if positive(
            prompt,
            r"\b(?:stop|shutdown|terminate|close)\b",
            r"\b(?:mcp|server|service|application)\b|\bapp\b(?!\.[a-z0-9])",
        ):
            requirements.add("service_stopped")
        if positive(
            prompt,
            r"\b(?:create|generate|export|save|write)\b",
            r"\b(?:artifact|dataset|document|file|output|report)\b",
        ):
            requirements.add("created_outputs")
        if positive(
            prompt,
            r"\bopen\b",
            r"\b(?:browser|chrome|edge|firefox|links?|tabs?|urls?)\b",
        ):
            requirements.add("opened_urls")
        if positive(
            prompt,
            r"\b(?:discover|inspect|list|verify)\b",
            r"\b(?:mcp|model context protocol)\b.{0,40}\btools?\b|\btools?\b.{0,40}\b(?:mcp|model context protocol)\b",
        ):
            requirements.add("mcp_ready")
        if re.search(r"\b(?:mcp|model context protocol)\b", prompt, re.IGNORECASE) and positive(
            prompt,
            r"\b(?:call|invoke)\b",
            r"\b(?:mcp\s+)?tools?\b|\b[a-z][a-z0-9_]{2,}\b",
        ):
            requirements.add("mcp_tool_called")
        count_requirement = _requested_count_postcondition(prompt)
        if count_requirement is not None:
            requirements.add(count_requirement[0])
        return requirements

    def _current_turn_evidence(self) -> list[dict[str, Any]]:
        return [evidence for evidence in self.evidence if evidence.get("turn_id") == self.turn_id]

    def completion_report(self, prompt: str) -> tuple[set[str], set[str]]:
        effective_prompt = f"{self.approval_context_prompt}\n{prompt}" if self.approval_context_prompt else prompt
        required = self._completion_requirements(effective_prompt)
        evidence_items = self._current_turn_evidence()
        verified = {
            category
            for evidence in evidence_items
            if evidence.get("success")
            for category in evidence.get("categories", [])
        }
        count_requirement = _requested_count_postcondition(effective_prompt)
        if count_requirement is not None:
            category, expected_count = count_requirement
            if category == "opened_urls":
                unique_urls = {
                    str(url).strip().casefold()
                    for evidence in evidence_items
                    if evidence.get("success") and "opened_urls" in evidence.get("categories", [])
                    for url in evidence.get("urls", [])
                    if _is_external_http_url(url)
                }
                observed_count = max(
                    len(unique_urls),
                    max(
                        (
                            int(evidence.get("opened_count") or 0)
                            for evidence in evidence_items
                            if evidence.get("success") and "opened_urls" in evidence.get("categories", [])
                        ),
                        default=0,
                    ),
                )
            elif category == "created_outputs":
                observed_count = len(
                    {
                        path
                        for evidence in evidence_items
                        if evidence.get("success") and "created_outputs" in evidence.get("categories", [])
                        for path in evidence.get("paths", [])
                    }
                )
            else:
                observed_count = max(
                    (
                        int(evidence.get("result_count") or 0)
                        for evidence in evidence_items
                        if evidence.get("success") and "result_count" in evidence.get("categories", [])
                    ),
                    default=0,
                )
            if observed_count < expected_count:
                verified.discard(category)
        semantic = _analyze_semantic_intent(effective_prompt)
        if semantic.requests("restart"):
            stop_index = next(
                (
                    index
                    for index, evidence in enumerate(evidence_items)
                    if evidence.get("success") and "service_stopped" in evidence.get("categories", [])
                ),
                None,
            )
            started_after_stop = stop_index is not None and any(
                evidence.get("success") and "service_started" in evidence.get("categories", [])
                for evidence in evidence_items[stop_index + 1 :]
            )
            if not started_after_stop:
                verified.discard("service_started")
        return required, required.difference(verified)

    def update_focused_postconditions(self, prompt: str) -> set[str]:
        required, missing = self.completion_report(prompt)
        observed = {
            category
            for evidence in self._current_turn_evidence()
            if evidence.get("success")
            for category in evidence.get("categories", [])
        }
        self.focused_missing_postconditions = set(missing) if missing and observed.intersection(required) else set()
        return set(self.focused_missing_postconditions)

    def mark_transaction_recovery_required(self) -> None:
        self.transaction_recovery_required = True
        self.focused_missing_postconditions.add("changed_files")

    def focused_evidence_context(self, prompt: str) -> str:
        missing = self.update_focused_postconditions(prompt)
        if not missing:
            return ""
        urls = {
            str(url).strip()
            for evidence in self.evidence
            if evidence.get("success")
            for url in evidence.get("urls", [])
            if _is_external_http_url(url)
        }
        result_count = max(
            (int(evidence.get("result_count") or 0) for evidence in self.evidence if evidence.get("success")),
            default=0,
        )
        lines = [
            "Wrapper structured preflight evidence is available.",
            f"Missing postconditions: {', '.join(sorted(missing))}.",
            f"Verified result count: {result_count}.",
        ]
        if urls:
            lines.append("Verified external URLs from prior steps:")
            lines.extend(sorted(urls)[:SCRIPT_PREFLIGHT_MAX_URLS])
        return "\n".join(lines)

    def completion_evidence_summary(self, prompt: str) -> str:
        required, missing = self.completion_report(prompt)
        verified = sorted(required.difference(missing))
        parts = [item.replace("_", " ") for item in verified]
        count_requirement = _requested_count_postcondition(prompt)
        if count_requirement is not None:
            category, expected = count_requirement
            observed = 0
            if category == "opened_urls":
                urls = {
                    str(url).strip().casefold()
                    for evidence in self.evidence
                    if evidence.get("success")
                    for url in evidence.get("urls", [])
                    if _is_external_http_url(url)
                }
                observed = max(
                    len(urls),
                    max(
                        (int(evidence.get("opened_count") or 0) for evidence in self.evidence if evidence.get("success")),
                        default=0,
                    ),
                )
            elif category == "created_outputs":
                observed = len(
                    {
                        path
                        for evidence in self.evidence
                        if evidence.get("success")
                        for path in evidence.get("paths", [])
                    }
                )
            else:
                observed = max(
                    (int(evidence.get("result_count") or 0) for evidence in self.evidence if evidence.get("success")),
                    default=0,
                )
            parts.append(f"{category.replace('_', ' ')} {observed}/{expected}")
        return "; ".join(dict.fromkeys(parts)) or "the requested postconditions"

    def partial_progress_answer(self, prompt: str) -> str:
        _required, missing = self.completion_report(prompt)
        if not self.evidence or not missing:
            return ""
        opened_urls = {
            str(url).strip()
            for evidence in self.evidence
            if evidence.get("success") and "opened_urls" in evidence.get("categories", [])
            for url in evidence.get("urls", [])
            if _is_external_http_url(url)
        }
        opened_count = max(
            len(opened_urls),
            max((int(evidence.get("opened_count") or 0) for evidence in self.evidence if evidence.get("success")), default=0),
        )
        result_count = max(
            (int(evidence.get("result_count") or 0) for evidence in self.evidence if evidence.get("success")),
            default=0,
        )
        verified_paths = {
            str(path)
            for evidence in self.evidence
            if evidence.get("success")
            for path in evidence.get("paths", [])
            if str(path).strip()
        }
        progress: list[str] = []
        if opened_count:
            progress.append(f"opened URLs: {opened_count}")
        if result_count:
            progress.append(f"verified results: {result_count}")
        if verified_paths:
            progress.append(f"verified paths: {len(verified_paths)}")
        if not progress:
            return ""
        missing_text = ", ".join(sorted(item.replace("_", " ") for item in missing))
        return (
            "The task was cancelled after preserving verified partial progress.\n\n"
            f"Verified before cancellation: {'; '.join(progress)}.\n\n"
            f"[Completion status: partial] Unverified postconditions: {missing_text}."
        )

    def rollback_transaction(self) -> dict[str, list[str]]:
        restored: list[str] = []
        skipped: list[str] = []
        for key, expected_hash in reversed(list(self.transaction_changed_hashes.items())):
            target = Path(key)
            original = self.transaction_originals.get(key)
            if original is None or not target.is_file():
                skipped.append(key)
                continue
            with suppress(OSError):
                current_hash = hashlib.sha256(target.read_bytes()).hexdigest()
                if current_hash != expected_hash:
                    skipped.append(key)
                    continue
                temporary = target.with_name(f".{target.name}.qubitz-rollback-{os.getpid()}-{threading.get_ident()}.tmp")
                try:
                    temporary.write_bytes(original["data"])
                    with suppress(OSError):
                        temporary.chmod(int(original["mode"]))
                    os.replace(temporary, target)
                finally:
                    with suppress(FileNotFoundError):
                        temporary.unlink()
                restored.append(key)
                continue
            skipped.append(key)
        return {"restored": restored, "skipped": skipped}

    def _outside_workspace_paths(self, arguments: dict[str, Any]) -> list[str]:
        outside: list[str] = []
        for key in self._PATH_KEYS:
            raw_value = arguments.get(key)
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            try:
                target = self.base.resolve_workspace_path(
                    self.workspace,
                    raw_value,
                    allow_missing=True,
                    allow_external=True,
                ).resolve()
                target.relative_to(self.workspace)
            except ValueError:
                outside.append(raw_value)
            except Exception:
                continue
        return outside

    @staticmethod
    def _command_text(name: str, arguments: dict[str, Any]) -> str:
        if name not in {"run_command", "run_project_command", "run_powershell_command"}:
            return ""
        value = arguments.get("command", "")
        return " ".join(str(item) for item in value) if isinstance(value, list) else str(value)

    @staticmethod
    def _has_shell_file_redirection(command: str) -> bool:
        quote = ""
        escaped = False
        index = 0
        while index < len(command):
            character = command[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if quote:
                if character == "\\" and quote == '"':
                    escaped = True
                elif character == quote:
                    quote = ""
                index += 1
                continue
            if character in {"'", '"'}:
                quote = character
                index += 1
                continue
            if character != ">" or (index > 0 and command[index - 1] in {">", "="}):
                index += 1
                continue
            cursor = index + 1
            if cursor < len(command) and command[cursor] == ">":
                cursor += 1
            while cursor < len(command) and command[cursor].isspace():
                cursor += 1
            if cursor >= len(command) or command[cursor] == "&":
                index = cursor + 1
                continue
            end = cursor
            while end < len(command) and command[end] not in " \t\r\n;&|":
                end += 1
            target = command[cursor:end].strip("'\"").casefold()
            if target and target not in {"/dev/null", "nul", "$null"}:
                return True
            index = max(end, cursor + 1)
        return False

    @staticmethod
    def _contains_nested_mutation_command(command: str) -> bool:
        mutation_words = (
            r"sed\s*['\"]?\s*,\s*['\"]-i|sed\s+-i|"
            r"perl\s+-pi|"
            r"(?:subprocess\.(?:run|call|Popen)|os\.system)\s*\([^)]*"
            r"(?:sed\s*['\"]?\s*,\s*['\"]-i|sed\s+-i|perl\s+-pi|"
            r"python(?:3)?\s+-c\s+[^)]*(?:write_text|write_bytes|open\s*\([^)]*['\"](?:a|w|x)[bt+]*['\"]))|"
            r"(?:Start-Process|&)\s+(?:sed|perl|python|python3)\b[\s\S]{0,400}"
            r"(?:-i\b|-pi\b|write_text|write_bytes|open\s*\([^)]*['\"](?:a|w|x)[bt+]*['\"])"
        )
        return re.search(mutation_words, command, re.IGNORECASE) is not None

    @staticmethod
    def _command_side_effects(command: str) -> set[str]:
        effects: set[str] = set()
        if not command.strip():
            return effects
        if _ToolPermissionBroker._has_shell_file_redirection(command):
            effects.add("mutate")
        if _ToolPermissionBroker._contains_nested_mutation_command(command):
            effects.add("mutate")
        if re.search(
            r"\b(?:pip(?:3)?\s+(?:install|uninstall)|uv\s+(?:add|remove|pip\s+(?:install|uninstall))|"
            r"npm\s+(?:add|install|remove|uninstall|update)|pnpm\s+(?:add|install|remove|update)|"
            r"yarn\s+(?:add|remove|upgrade))\b",
            command,
            re.IGNORECASE,
        ):
            effects.add("install")
        if re.search(
            r"\b(?:Remove-Item|del|erase|rm|rmdir|unlink)\b|\.unlink\s*\(|shutil\.rmtree\s*\(",
            command,
            re.IGNORECASE,
        ):
            effects.update({"delete", "mutate"})
        if re.search(
            r"\b(?:Set-Content|Add-Content|Out-File|New-Item|Copy-Item|Move-Item|Rename-Item|"
            r"mkdir|touch|cp|mv|sed\s+-i|git\s+(?:apply|checkout|clean|commit|merge|mv|rebase|reset|restore))\b"
            r"|(?:Path\([^\n]+\)|[A-Za-z_][A-Za-z0-9_.]*)\.(?:write_text|write_bytes|rename|replace)\s*\("
            r"|open\s*\([^\n]+,[^\n]*['\"](?:a|w|x)[bt+]*['\"]"
            r"|(?:cat|echo|printf|type)\b[\s\S]*?\s>{1,2}\s*[^\s&]"
            r"|\|\s*tee(?:\s+-[A-Za-z]+)*\s+[^\s-]",
            command,
            re.IGNORECASE,
        ):
            effects.add("mutate")
        return effects

    def repair_command_arguments(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        command = self._command_text(name, arguments)
        if not command:
            return arguments
        repaired = command.strip()
        changes: list[str] = []
        nested = re.fullmatch(r'\s*&\s+"(&\s+.+)"\s*', repaired, re.DOTALL)
        if name == "run_powershell_command" and nested:
            repaired = nested.group(1).strip()
            changes.append("removed a duplicated quoted PowerShell call operator")
        corrected_wsl_mirror = re.sub(
            r"(?i)\b[A-Z]:[\\/]+mnt[\\/]+([a-z])[\\/]+",
            lambda match: f"{match.group(1).upper()}:\\",
            repaired,
        )
        if corrected_wsl_mirror != repaired:
            repaired = corrected_wsl_mirror
            changes.append("translated an invalid C:\\mnt\\<drive> Windows path to its real drive path")
        browser_parts = [part.strip() for part in re.split(r"\s*&&\s*", repaired) if part.strip()]
        browser_matches = [
            re.fullmatch(
                r"(?:start(?:-process)?\s+)?(?:firefox(?:\.exe)?|chrome(?:\.exe)?|msedge(?:\.exe)?|edge(?:\.exe)?)\s+['\"]?(https?://[^'\"\s]+)['\"]?",
                part,
                re.IGNORECASE,
            )
            for part in browser_parts
        ]
        if len(browser_parts) > 1 and all(browser_matches):
            urls = [match.group(1) for match in browser_matches if match is not None]
            quoted = ", ".join(_powershell_single_quote(url) for url in urls)
            repaired = f"$urls = @({quoted}); foreach ($url in $urls) {{ Start-Process $url }}"
            changes.append("normalized a browser command chain for PowerShell")
        direct_script = re.fullmatch(r"\s*&?\s*['\"]?([^'\"\r\n]+\.py)['\"]?(\s+.*)?", repaired)
        if direct_script:
            candidate_text = direct_script.group(1).strip()
            candidate = Path(candidate_text)
            if not candidate.is_absolute():
                candidate = (self.workspace / candidate).resolve()
            if candidate.is_file():
                python_path = _preferred_project_python(self.workspace)
                if python_path is not None:
                    rest = direct_script.group(2) or ""
                    if name == "run_powershell_command":
                        repaired = f"& {_powershell_single_quote(str(python_path))} {_powershell_single_quote(str(candidate))}{rest}"
                    else:
                        repaired = f"{_shell_quote_path(python_path)} {_shell_quote_path(str(candidate))}{rest}"
                    changes.append("prefixed a direct Python script with the workspace interpreter")
        if not changes or repaired == command:
            return arguments
        updated = dict(arguments)
        updated["command"] = repaired
        fingerprint = self._fingerprint(name.lower(), updated)
        self._audit("command_repaired", name.lower(), updated, fingerprint, "; ".join(changes))
        self._emit(f"Corrected one malformed {name} command deterministically: {'; '.join(changes)}.")
        return updated

    def _requires_command_approval(self, name: str, arguments: dict[str, Any]) -> bool:
        if name == "run_powershell_command":
            return True
        if name not in {"run_command", "run_project_command"}:
            return False
        command_value = arguments.get("command", "")
        if isinstance(command_value, list):
            command_text = " ".join(str(item) for item in command_value)
            executable = str(command_value[0]).lower() if command_value else ""
        else:
            command_text = str(command_value)
            with suppress(ValueError):
                parts = shlex.split(command_text, posix=True)
                executable = parts[0].lower() if parts else ""
            if "executable" not in locals():
                executable = ""
        if re.search(r"(?:^|\s)(?:rm|rmdir|del|erase|shutdown|reboot|format|sudo|runas)(?:\s|$)|reset\s+--hard|clean\s+-[a-z]*f", command_text, re.IGNORECASE):
            return True
        if re.search(
            r"\b(?:pip(?:3)?\s+(?:install|uninstall)|uv\s+(?:add|remove|pip\s+install)|"
            r"npm\s+(?:add|install|remove|uninstall|update)|pnpm\s+(?:add|install|remove|update)|"
            r"yarn\s+(?:add|remove|upgrade))\b",
            command_text,
            re.IGNORECASE,
        ):
            return True
        if re.search(r"[;&|`]|\$\(", command_text):
            return True
        allowed = {
            "git",
            "make",
            "node",
            "npm",
            "npx",
            "pnpm",
            "py",
            "pytest",
            "python",
            "python3",
            "rg",
            "ruff",
            "uv",
        }
        return Path(executable).name.lower() not in allowed

    def authorize(self, name: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        normalized = name.lower()
        fingerprint = self._fingerprint(normalized, arguments)
        if self.access_mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN} and normalized not in self._RESTRICTED_MODE_TOOLS:
            reason = f"{_access_mode_label(self.access_mode)} permits inspection and research but not side effects."
            self._audit("mode_denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "access_mode_denied", "tool": normalized, "reason": reason}
        if self.access_mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN} and self._outside_workspace_paths(arguments):
            reason = "Explicit paths outside the active workspace require Full Access."
            self._audit("mode_denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "access_mode_denied", "tool": normalized, "reason": reason}
        if fingerprint in self.approved_once:
            self.approved_once.remove(fingerprint)
            self.pending.pop(fingerprint, None)
            self._audit("approved", normalized, arguments, fingerprint)
            self._emit(f"Approved tool action {fingerprint[:10]} is executing once.")
            return True, None

        semantic = _analyze_semantic_intent(self.current_prompt)
        if (
            normalized == "apply_text_patch"
            and len(self.transaction_explicit_file_targets) >= 2
            and not self.transaction_changed_hashes
        ):
            reason = (
                "The user named multiple existing files, so this edit must be one atomic apply_text_patches batch. "
                "Submit every intended path and its exact replacements together."
            )
            self._audit("atomic_batch_required", normalized, arguments, fingerprint, reason)
            return False, {
                "status": "atomic_batch_required",
                "tool": normalized,
                "reason": reason,
                "file_unchanged": True,
            }
        creation_after_commit = False
        if "created_outputs" in self.required_postconditions and normalized in {
            "copy_path",
            "make_directory",
            "write_file",
        }:
            creation_target = self._resolved_argument_path(
                arguments,
                "destination" if normalized == "copy_path" else "path",
            )
            creation_after_commit = creation_target is not None and not creation_target.exists()
        if normalized in self._MUTATION_TOOLS and self.transaction_changed_hashes and not creation_after_commit:
            reason = (
                "The wrapper has already committed this turn's atomic existing-file edit batch. "
                "Run the requested verification now; do not start a second patch transaction."
            )
            self.mark_transaction_recovery_required()
            if (
                not self.transaction_recovery_interrupt_requested
                and self.transaction_recovery_callback is not None
            ):
                self.transaction_recovery_interrupt_requested = True
                self.transaction_recovery_callback()
            self._audit("verification_required", normalized, arguments, fingerprint, reason)
            return False, {
                "status": "verification_required",
                "tool": normalized,
                "reason": reason,
                "files_unchanged": True,
            }
        if normalized in {"run_cmd_command", "run_powershell_command", "sandbox_run_powershell"}:
            capabilities = _workspace_runtime_capabilities(self.base, self.workspace)
            if capabilities.get("in_wsl") and not capabilities.get("workspace_is_windows_backed"):
                shell_name = "Windows cmd" if normalized == "run_cmd_command" else "PowerShell"
                reason = (
                    f"{shell_name} is not a valid execution environment for this WSL-native workspace. "
                    "Use run_command, run_project_command, or the workspace's WSL Python interpreter."
                )
                self._audit("environment_denied", normalized, arguments, fingerprint, reason)
                return False, {"status": "environment_mismatch", "tool": normalized, "reason": reason}
        edit_requested = "changed_files" in self.required_postconditions or semantic.requests(
            "copy", "create", "delete", "edit", "move"
        )
        execution_requested = semantic.requests(
            "execute",
            "clone_repository",
            "mcp_call",
                    "mcp_discover",
                    "open_browser",
                    "open_project",
                    "restart",
                    "start",
                    "stop",
                    "verify",
                    "install",
                    "uninstall",
                    "upgrade",
        )
        install_requested = semantic.requests("install", "uninstall", "upgrade")
        command_effects = self._command_side_effects(self._command_text(normalized, arguments))
        if "mutate" in command_effects and normalized in {
            "run_command",
            "run_project_command",
            "run_powershell_command",
            "run_cmd_command",
        }:
            reason = (
                "Direct file mutation through command tools bypasses transactional edit verification. "
                "Use read_file_snapshot and apply_text_patch for existing text files, or a dedicated file tool."
            )
            self.mark_transaction_recovery_required()
            self._audit("transaction_required", normalized, arguments, fingerprint, reason)
            return False, {
                "status": "transaction_required",
                "tool": normalized,
                "reason": reason,
            }
        if "install" in command_effects and not install_requested:
            reason = "The command contains a dependency operation that the current user prompt did not request."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if "delete" in command_effects and not semantic.requests("delete"):
            reason = "The command contains deletion that the current user prompt did not authorize."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if "mutate" in command_effects and not edit_requested:
            reason = "The command contains filesystem or repository mutation that the current user prompt did not authorize."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if normalized in self._MUTATION_TOOLS and not edit_requested:
            reason = "The current user prompt did not authorize workspace modification."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if normalized in {"cancel_background_job", "stop_project_mcp_server"} and semantic.execution_scope != "managed_service":
            reason = "Foreground cancellation does not authorize stopping a managed service or background job."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if normalized in self._EXECUTION_TOOLS and not execution_requested:
            reason = "The current user prompt did not authorize command or entrypoint execution."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}
        if normalized == "install_python_package" and not install_requested:
            reason = "The current user prompt did not request dependency installation."
            self._audit("denied", normalized, arguments, fingerprint, reason)
            return False, {"status": "scope_denied", "tool": normalized, "reason": reason}

        target = self._resolved_argument_path(arguments, "path")
        if normalized in {"write_file", "replace_text"} and target is not None and target.is_file():
            transactional = False
            with suppress(OSError, UnicodeDecodeError):
                data = target.read_bytes()
                data.decode("utf-8")
                transactional = len(data) <= TRANSACTIONAL_PATCH_MAX_BYTES
            if transactional:
                reason = (
                    "Existing UTF-8 files within the transactional size limit must be read with "
                    "read_file_snapshot and changed with apply_text_patch."
                )
                self.mark_transaction_recovery_required()
                self._audit("transaction_required", normalized, arguments, fingerprint, reason)
                return False, {
                    "status": "transaction_required",
                    "tool": normalized,
                    "reason": reason,
                    "instruction": "Call read_file_snapshot for this path, then apply_text_patch with its returned SHA-256.",
                }
        if normalized == "apply_text_patches":
            patches = arguments.get("patches")
            if not isinstance(patches, list) or not 2 <= len(patches) <= 16:
                reason = "apply_text_patches requires 2-16 file patches."
                self._audit("invalid", normalized, arguments, fingerprint, reason)
                return False, {"status": "invalid", "tool": normalized, "reason": reason, "file_unchanged": True}
            seen_targets: set[str] = set()
            for index, patch in enumerate(patches, start=1):
                if not isinstance(patch, dict):
                    reason = f"Patch {index} must be an object."
                    self._audit("invalid", normalized, arguments, fingerprint, reason)
                    return False, {"status": "invalid", "tool": normalized, "reason": reason, "file_unchanged": True}
                patch_target = self._resolved_argument_path(patch, "path")
                target_key = str(patch_target) if patch_target is not None else ""
                if not target_key or target_key in seen_targets:
                    reason = f"Patch {index} requires a unique valid file path."
                    self._audit("invalid", normalized, arguments, fingerprint, reason)
                    return False, {
                        "status": "invalid",
                        "tool": normalized,
                        "reason": reason,
                        "file_unchanged": True,
                        "path": str(patch.get("path", "")),
                    }
                seen_targets.add(target_key)
        if normalized == "apply_text_patch":
            expected_hash = str(arguments.get("expected_sha256", "")).strip().lower()
            recorded_hash = self.transaction_snapshot_hashes.get(str(target)) if target is not None else None
            if recorded_hash is None or expected_hash != recorded_hash:
                reason = "apply_text_patch requires a successful current-turn read_file_snapshot for the same path and SHA-256."
                self.mark_transaction_recovery_required()
                self._audit("transaction_required", normalized, arguments, fingerprint, reason)
                return False, {
                    "status": "transaction_required",
                    "tool": normalized,
                    "reason": reason,
                    "file_unchanged": True,
                    "current_snapshot_sha256": recorded_hash,
                    "instruction": (
                        "The rejected call made no file change. Retry once with current_snapshot_sha256 "
                        "as expected_sha256."
                    ),
                }

        outside_paths = self._outside_workspace_paths(arguments)
        if self.access_mode == ACCESS_MODE_FULL:
            self._audit("allowed_full_access", normalized, arguments, fingerprint)
            return True, None
        requires_approval = normalized in self._ALWAYS_APPROVAL or bool(outside_paths)
        if normalized == "write_file":
            with suppress(Exception):
                target = self.base.resolve_workspace_path(self.workspace, str(arguments.get("path", "")), allow_missing=True, allow_external=True)
                requires_approval = requires_approval or target.exists()
        if normalized in {"copy_path", "move_path", "sandbox_move_path"} and bool(arguments.get("overwrite")):
            requires_approval = True
        requires_approval = requires_approval or self._requires_command_approval(normalized, arguments)
        if not requires_approval:
            self._audit("allowed", normalized, arguments, fingerprint)
            return True, None

        redacted = _redacted_tool_arguments(arguments)
        preview = f"{normalized}({json.dumps(redacted, ensure_ascii=True, sort_keys=True, default=str)})"
        if outside_paths:
            preview += f" outside_workspace={outside_paths}"
        pending = _PendingToolApproval(
            approval_id=fingerprint[:10],
            fingerprint=fingerprint,
            tool_name=normalized,
            arguments=dict(arguments),
            preview=preview,
            request_prompt=self.current_prompt,
        )
        self.pending[fingerprint] = pending
        reason = "This action requires explicit approval before execution."
        self._audit("approval_required", normalized, arguments, fingerprint, reason)
        self._emit(f"Tool action {pending.approval_id} requires approval and was not executed.")
        return False, {
            "status": "approval_required",
            "approval_id": pending.approval_id,
            "tool": normalized,
            "preview": preview,
            "reason": reason,
            "instruction": f"Ask the user to reply `approve {pending.approval_id}` to execute this exact action once.",
        }


_ORIGINAL_LOCAL_MCP_SERVER_BUILDER = _build_local_mcp_server


def _build_local_mcp_server(base: Any, workspace: Path, runtime_workspace: Path, launch_script: Path) -> Any:
    server = _ORIGINAL_LOCAL_MCP_SERVER_BUILDER(base, workspace, runtime_workspace, launch_script)
    workspace = workspace.resolve()

    def annotations(name: str) -> Any:
        return _tool_annotations(base, name)

    @server.tool(
        description="Search the local tool catalog by capability and return matching tool names and input schemas.",
        annotations=annotations("search_tools"),
        structured_output=True,
    )
    def search_tools(query: str, max_results: int = TOOL_SEARCH_RESULT_MAX) -> dict[str, Any]:
        query_tokens = set(re.findall(r"[a-z0-9_]+", query.lower()))
        ranked: list[tuple[int, str, Any]] = []
        for tool in server._tool_manager.list_tools():
            if tool.name == "search_tools":
                continue
            haystack = f"{tool.name} {tool.description or ''}".lower()
            name_tokens = set(re.findall(r"[a-z0-9_]+", tool.name.lower().replace("_", " ")))
            score = 4 * len(query_tokens.intersection(name_tokens))
            score += sum(1 for token in query_tokens if token and token in haystack)
            if score > 0:
                ranked.append((score, tool.name, tool))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        matches = [
            {
                "name": tool.name,
                "description": tool.description or "",
                "input_schema": _strict_tool_schema(tool.parameters),
            }
            for _, _, tool in ranked[: max(1, min(int(max_results), TOOL_SEARCH_RESULT_MAX))]
        ]
        return {"query": query, "matches": matches, "usage": "Call a returned tool by its exact name and schema."}

    @server.tool(
        description="Read a UTF-8 workspace text file with a whole-file SHA-256 snapshot for transactional editing.",
        annotations=annotations("read_file_snapshot"),
        structured_output=True,
    )
    def read_file_snapshot(path: str, start_line: int = 1, end_line: int = 4000) -> dict[str, Any]:
        target = base.resolve_workspace_path(
            workspace,
            path,
            allow_missing=False,
            allow_external=_full_access_enabled(),
        )
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        data = target.read_bytes()
        if len(data) > TRANSACTIONAL_PATCH_MAX_BYTES:
            raise ValueError(f"File exceeds the {TRANSACTIONAL_PATCH_MAX_BYTES}-byte transactional edit limit: {path}")
        text = data.decode("utf-8")
        lines = text.splitlines()
        first = max(1, int(start_line))
        last = min(max(first, int(end_line)), len(lines))
        return {
            "path": base.relative_path(target, workspace),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "start_line": first,
            "end_line": last,
            "content": "\n".join(lines[first - 1 : last]),
        }

    @server.tool(
        description="Atomically apply unique exact-text replacements to a workspace file after verifying its SHA-256 snapshot.",
        annotations=annotations("apply_text_patch"),
        structured_output=True,
    )
    def apply_text_patch(path: str, expected_sha256: str, replacements: list[_TextReplacement]) -> dict[str, Any]:
        target = base.resolve_workspace_path(
            workspace,
            path,
            allow_missing=False,
            allow_external=_full_access_enabled(),
        )
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise ValueError("expected_sha256 must contain exactly 64 hexadecimal characters.")
        if not 1 <= len(replacements) <= TRANSACTIONAL_PATCH_MAX_REPLACEMENTS:
            raise ValueError(f"Provide 1-{TRANSACTIONAL_PATCH_MAX_REPLACEMENTS} replacements.")
        original_bytes = target.read_bytes()
        if len(original_bytes) > TRANSACTIONAL_PATCH_MAX_BYTES:
            raise ValueError(f"File exceeds the {TRANSACTIONAL_PATCH_MAX_BYTES}-byte transactional edit limit: {path}")
        original_hash = hashlib.sha256(original_bytes).hexdigest()
        if original_hash.lower() != expected_sha256.lower():
            raise ValueError(f"Stale file snapshot for {path}: expected {expected_sha256}, found {original_hash}.")
        original = original_bytes.decode("utf-8")
        updated = original
        for index, replacement in enumerate(replacements, start=1):
            if set(replacement) != {"old_text", "new_text"}:
                raise ValueError(f"Replacement {index} must contain only old_text and new_text.")
            old_text = str(replacement["old_text"])
            new_text = str(replacement["new_text"])
            if not old_text:
                raise ValueError(f"Replacement {index} old_text cannot be empty.")
            occurrences = updated.count(old_text)
            if occurrences != 1:
                raise ValueError(f"Replacement {index} expected one exact match, found {occurrences}.")
            updated = updated.replace(old_text, new_text, 1)
        updated_bytes = updated.encode("utf-8")
        temporary = target.with_name(f".{target.name}.qubitz-{os.getpid()}-{threading.get_ident()}.tmp")
        try:
            temporary.write_bytes(updated_bytes)
            with suppress(OSError):
                temporary.chmod(target.stat().st_mode)
            os.replace(temporary, target)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                updated.splitlines(keepends=True),
                fromfile=f"a/{base.relative_path(target, workspace)}",
                tofile=f"b/{base.relative_path(target, workspace)}",
            )
        )
        return {
            "path": base.relative_path(target, workspace),
            "old_sha256": original_hash,
            "new_sha256": hashlib.sha256(updated_bytes).hexdigest(),
            "replacement_count": len(replacements),
            "bytes_written": len(updated_bytes),
            "diff": _shorten(diff, LOCAL_ONLY_MAX_DIFF_CHARS),
        }

    @server.tool(
        description=(
            "Atomically apply exact-text replacements to 2-16 workspace files. Provide only path and replacements; "
            "the wrapper snapshots, hashes, orders, validates, and restores originals if application fails."
        ),
        annotations=annotations("apply_text_patches"),
        structured_output=True,
    )
    def apply_text_patches(patches: list[_FileTextPatch]) -> dict[str, Any]:
        if not 2 <= len(patches) <= 16:
            raise ValueError("Provide 2-16 file patches.")
        prepared: list[dict[str, Any]] = []
        seen: set[Path] = set()
        ordered_patches = sorted(patches, key=lambda item: str(item.get("path", "")).casefold())
        for patch_index, patch in enumerate(ordered_patches, start=1):
            if set(patch) != {"path", "replacements"}:
                raise ValueError(f"Patch {patch_index} must contain only path and replacements.")
            target = base.resolve_workspace_path(
                workspace,
                str(patch["path"]),
                allow_missing=False,
                allow_external=_full_access_enabled(),
            )
            if target in seen:
                raise ValueError(f"Patch {patch_index} repeats target {patch['path']}.")
            seen.add(target)
            if not target.is_file():
                raise ValueError(f"Not a file: {patch['path']}")
            replacements = patch["replacements"]
            if not 1 <= len(replacements) <= TRANSACTIONAL_PATCH_MAX_REPLACEMENTS:
                raise ValueError(
                    f"Patch {patch_index} must provide 1-{TRANSACTIONAL_PATCH_MAX_REPLACEMENTS} replacements."
                )
            original_bytes = target.read_bytes()
            if len(original_bytes) > TRANSACTIONAL_PATCH_MAX_BYTES:
                raise ValueError(
                    f"File exceeds the {TRANSACTIONAL_PATCH_MAX_BYTES}-byte transactional edit limit: {patch['path']}"
                )
            original_hash = hashlib.sha256(original_bytes).hexdigest()
            original = original_bytes.decode("utf-8")
            updated = original
            for replacement_index, replacement in enumerate(replacements, start=1):
                if set(replacement) != {"old_text", "new_text"}:
                    raise ValueError(
                        f"Patch {patch_index} replacement {replacement_index} must contain only old_text and new_text."
                    )
                old_text = str(replacement["old_text"])
                new_text = str(replacement["new_text"])
                if not old_text:
                    raise ValueError(f"Patch {patch_index} replacement {replacement_index} old_text cannot be empty.")
                occurrences = updated.count(old_text)
                if occurrences != 1:
                    raise ValueError(
                        f"Patch {patch_index} replacement {replacement_index} expected one exact match, "
                        f"found {occurrences}."
                    )
                updated = updated.replace(old_text, new_text, 1)
            updated_bytes = updated.encode("utf-8")
            diff = "".join(
                difflib.unified_diff(
                    original.splitlines(keepends=True),
                    updated.splitlines(keepends=True),
                    fromfile=f"a/{base.relative_path(target, workspace)}",
                    tofile=f"b/{base.relative_path(target, workspace)}",
                )
            )
            prepared.append(
                {
                    "target": target,
                    "original_bytes": original_bytes,
                    "mode": target.stat().st_mode,
                    "updated_bytes": updated_bytes,
                    "result": {
                        "path": base.relative_path(target, workspace),
                        "old_sha256": original_hash,
                        "new_sha256": hashlib.sha256(updated_bytes).hexdigest(),
                        "replacement_count": len(replacements),
                        "bytes_written": len(updated_bytes),
                        "diff": _shorten(diff, LOCAL_ONLY_MAX_DIFF_CHARS),
                    },
                }
            )
        broker = _TOOL_CALL_CONTEXT.get()
        if broker is not None:
            for item in prepared:
                broker.register_transaction_original(item["target"], item["original_bytes"], item["mode"])
        staged: list[tuple[Path, Path]] = []
        applied: list[dict[str, Any]] = []
        try:
            for index, item in enumerate(prepared, start=1):
                target = item["target"]
                temporary = target.with_name(
                    f".{target.name}.qubitz-multi-{os.getpid()}-{threading.get_ident()}-{index}.tmp"
                )
                temporary.write_bytes(item["updated_bytes"])
                with suppress(OSError):
                    temporary.chmod(item["mode"])
                staged.append((target, temporary))
            for item, (target, temporary) in zip(prepared, staged, strict=True):
                os.replace(temporary, target)
                applied.append(item)
        except Exception:
            for index, item in enumerate(reversed(applied), start=1):
                target = item["target"]
                temporary = target.with_name(
                    f".{target.name}.qubitz-multi-rollback-{os.getpid()}-{threading.get_ident()}-{index}.tmp"
                )
                try:
                    temporary.write_bytes(item["original_bytes"])
                    with suppress(OSError):
                        temporary.chmod(item["mode"])
                    os.replace(temporary, target)
                finally:
                    with suppress(FileNotFoundError):
                        temporary.unlink()
            raise
        finally:
            for _, temporary in staged:
                with suppress(FileNotFoundError):
                    temporary.unlink()
        return {"file_count": len(prepared), "files": [item["result"] for item in prepared]}

    @server.tool(
        description=(
            "Run one validated existing workspace entrypoint without a shell. "
            "Python test files or directories are executed through pytest so test functions actually run."
        ),
        annotations=annotations("run_existing_entrypoint"),
        structured_output=True,
    )
    def run_existing_entrypoint(
        path: str,
        arguments: list[str] | None = None,
        cwd: str = ".",
        timeout_seconds: int = 300,
    ) -> dict[str, Any]:
        requested_path = path.strip()
        command_path_tokens = re.findall(r"(?:^|\s)[\"']?([^\"'\s]+\.py)[\"']?(?=\s|$)", requested_path)
        if command_path_tokens:
            requested_path = command_path_tokens[-1]
        try:
            base.resolve_workspace_path(
                workspace,
                requested_path,
                allow_missing=False,
                allow_external=_full_access_enabled(),
            )
        except Exception:
            basename = Path(requested_path.replace("\\", "/")).name
            matches = [
                item
                for item in workspace.rglob(basename)
                if item.is_file() and not any(part in {".git", ".venv", "__pycache__"} for part in item.parts)
            ]
            if requested_path.lower() in {"pytest", "python -m pytest", "python3 -m pytest"}:
                matches = sorted({*workspace.rglob("test_*.py"), *workspace.rglob("*_test.py")})
            if len(matches) != 1:
                raise
            requested_path = str(matches[0])
        target = base.resolve_workspace_path(
            workspace,
            requested_path,
            allow_missing=False,
            allow_external=_full_access_enabled(),
        )
        target_cwd = base.resolve_workspace_path(
            workspace,
            cwd,
            allow_missing=False,
            allow_external=_full_access_enabled(),
        )
        args = [str(item) for item in (arguments or [])]
        suffix = target.suffix.lower()
        test_runner = ""
        command: list[str] | None = None
        if target.is_dir():
            test_files = sorted({*target.rglob("test_*.py"), *target.rglob("*_test.py")})
            if not test_files:
                raise ValueError(f"Not a Python test directory: {requested_path}")
            python_path = _preferred_project_python(workspace)
            if python_path is None:
                raise RuntimeError("No project-local Python interpreter was found for this test directory.")
            test_path = str(target)
            if base.in_wsl() and python_path.suffix.lower() == ".exe":
                test_path = base.wsl_path_to_windows_path(target)
            test_runner = "pytest"
            command = [str(python_path), "-m", "pytest", test_path, *args]
        elif not target.is_file():
            raise ValueError(f"Not a file or Python test directory: {requested_path}")
        if command is not None:
            pass
        elif suffix == ".py":
            python_path = _preferred_project_python(workspace)
            if python_path is None:
                raise RuntimeError("No project-local Python interpreter was found for this entrypoint.")
            script_path = str(target)
            if base.in_wsl() and python_path.suffix.lower() == ".exe":
                script_path = base.wsl_path_to_windows_path(target)
            is_python_test = (
                target.name.startswith("test_")
                or target.name.endswith("_test.py")
                or target.parent.name.lower() in {"test", "tests"}
            )
            if is_python_test:
                test_runner = "pytest"
            command = (
                [str(python_path), "-m", "pytest", script_path, *args]
                if is_python_test
                else [str(python_path), script_path, *args]
            )
        elif suffix == ".sh":
            bash = shutil.which("bash")
            if bash is None:
                raise RuntimeError("bash was not found for the requested shell entrypoint.")
            command = [bash, str(target), *args]
        elif suffix == ".ps1":
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
            if powershell is None:
                raise RuntimeError("PowerShell was not found for the requested entrypoint.")
            script_path = base.wsl_path_to_windows_path(target) if base.in_wsl() and powershell.lower().endswith(".exe") else str(target)
            command = [powershell, "-NoProfile", "-File", script_path, *args]
        elif suffix in {".bat", ".cmd"}:
            cmd = shutil.which("cmd.exe")
            if cmd is None:
                raise RuntimeError("cmd.exe was not found for the requested entrypoint.")
            script_path = base.wsl_path_to_windows_path(target) if base.in_wsl() else str(target)
            command = [cmd, "/d", "/s", "/c", subprocess.list2cmdline([script_path, *args])]
        elif suffix == ".exe" or os.access(target, os.X_OK):
            command = [str(target), *args]
        else:
            raise ValueError(f"Unsupported existing entrypoint type: {suffix or '<none>'}")
        completed = subprocess.run(
            command,
            cwd=target_cwd,
            capture_output=True,
            timeout=max(1, min(int(timeout_seconds), 1800)),
            check=False,
        )
        return {
            "path": base.relative_path(target, workspace),
            "cwd": base.relative_path(target_cwd, workspace),
            "command": command,
            "test_runner": test_runner,
            "returncode": completed.returncode,
            "stdout": _shorten(_decode_subprocess_output(completed.stdout), 12000),
            "stderr": _shorten(_decode_subprocess_output(completed.stderr), 12000),
        }

    return server


def _install_agent_tool_hardening(app: LocalOnlyApp) -> None:
    global _build_local_mcp_server

    base = app.base
    original_local_mcp_server_builder = _build_local_mcp_server
    original_build_model_tools = base.build_model_tools
    original_chat = base.LlamaCppClient.chat
    original_host = base.MCPHost
    original_runner = base.AgentRunner
    original_gui = base.QubitzGUI

    def build_semantic_mcp_server(
        tool_base: Any,
        workspace: Path,
        runtime_workspace: Path,
        launch_script: Path,
    ) -> Any:
        server = original_local_mcp_server_builder(tool_base, workspace, runtime_workspace, launch_script)

        @server.tool(
            description="Run one bounded Windows cmd.exe command in the requested local directory.",
            annotations=_tool_annotations(tool_base, "run_cmd_command"),
            structured_output=True,
        )
        def run_cmd_command(command: str, cwd: str = ".", timeout_seconds: int = 120) -> dict[str, Any]:
            cmd = shutil.which("cmd.exe")
            if cmd is None:
                raise RuntimeError("cmd.exe is unavailable in this runtime.")
            target_cwd = tool_base.resolve_workspace_path(
                workspace,
                cwd,
                allow_missing=False,
                allow_external=_full_access_enabled(),
            )
            bounded_timeout = max(1, min(int(timeout_seconds), 1800))
            in_wsl_session = bool(callable(getattr(tool_base, "in_wsl", None)) and tool_base.in_wsl())
            command_text = command
            launch_command = [cmd, "/d", "/s", "/c"]
            process_cwd = target_cwd
            if in_wsl_session:
                windows_cwd = tool_base.wsl_path_to_windows_path(target_cwd)
                command_text = f'cd /d "{windows_cwd}" && {command}'
                process_cwd = workspace
                if Path("/init").is_file():
                    launch_command.insert(0, "/init")
            completed = subprocess.run(
                [*launch_command, command_text],
                cwd=process_cwd,
                capture_output=True,
                timeout=bounded_timeout,
                check=False,
            )
            return {
                "command": command,
                "cwd": str(target_cwd),
                "return_code": completed.returncode,
                "stdout": _shorten(_decode_subprocess_output(completed.stdout), 12000),
                "stderr": _shorten(_decode_subprocess_output(completed.stderr), 12000),
            }

        @server.tool(
            description="Fetch bounded text content directly from an HTTP or HTTPS URL using the local network.",
            annotations=_tool_annotations(tool_base, "fetch_url"),
            structured_output=True,
        )
        def fetch_url(url: str, timeout_seconds: int = 30, max_bytes: int = 1000000) -> dict[str, Any]:
            import urllib.request
            from urllib.parse import urlsplit

            parsed = urlsplit(url.strip())
            if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
                raise ValueError("fetch_url requires an absolute HTTP or HTTPS URL.")
            bounded_timeout = max(1, min(int(timeout_seconds), 120))
            bounded_bytes = max(1, min(int(max_bytes), 2000000))
            request = urllib.request.Request(
                url.strip(),
                headers={"User-Agent": "Qubitz-Local-Agent/1.0"},
            )
            with urllib.request.urlopen(request, timeout=bounded_timeout) as response:
                payload = response.read(bounded_bytes + 1)
                truncated = len(payload) > bounded_bytes
                payload = payload[:bounded_bytes]
                charset = response.headers.get_content_charset() or "utf-8"
                return {
                    "requested_url": url.strip(),
                    "final_url": response.geturl(),
                    "status": int(getattr(response, "status", 200) or 200),
                    "content_type": response.headers.get_content_type(),
                    "body": payload.decode(charset, errors="replace"),
                    "bytes_read": len(payload),
                    "truncated": truncated,
                }

        @server.tool(
            description=(
                "Open validated absolute HTTP or HTTPS URLs as browser tabs using deterministic local wrapper logic. "
                "Use browser='default', 'firefox', 'chrome', or 'edge'."
            ),
            annotations=_tool_annotations(tool_base, "open_urls_in_browser"),
            structured_output=True,
        )
        def open_urls_in_browser(
            urls: list[str],
            browser: str = "default",
            max_urls: int = SCRIPT_PREFLIGHT_MAX_URLS,
        ) -> dict[str, Any]:
            browser_name = str(browser or "default").strip().lower()
            aliases = {
                "default": "default",
                "system": "default",
                "firefox": "firefox",
                "chrome": "chrome",
                "google-chrome": "chrome",
                "edge": "edge",
                "msedge": "edge",
            }
            if browser_name not in aliases:
                raise ValueError("browser must be default, firefox, chrome, or edge.")
            browser_name = aliases[browser_name]
            bounded_limit = max(1, min(int(max_urls), SCRIPT_PREFLIGHT_MAX_URLS))
            validated_urls = _validated_external_urls(urls, bounded_limit)
            if not validated_urls:
                raise ValueError("No valid absolute HTTP or HTTPS URLs were provided.")

            windows_browser_names = {
                "firefox": ("firefox.exe", "firefox"),
                "chrome": ("chrome.exe", "chrome"),
                "edge": ("msedge.exe", "msedge"),
            }
            windows_browser_paths = {
                "firefox": (
                    "$env:ProgramFiles\\Mozilla Firefox\\firefox.exe",
                    "${env:ProgramFiles(x86)}\\Mozilla Firefox\\firefox.exe",
                    "$env:LOCALAPPDATA\\Mozilla Firefox\\firefox.exe",
                ),
                "chrome": (
                    "$env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe",
                    "${env:ProgramFiles(x86)}\\Google\\Chrome\\Application\\chrome.exe",
                    "$env:LOCALAPPDATA\\Google\\Chrome\\Application\\chrome.exe",
                ),
                "edge": (
                    "$env:ProgramFiles\\Microsoft\\Edge\\Application\\msedge.exe",
                    "${env:ProgramFiles(x86)}\\Microsoft\\Edge\\Application\\msedge.exe",
                    "$env:LOCALAPPDATA\\Microsoft\\Edge\\Application\\msedge.exe",
                ),
            }
            powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
            if os.name == "nt" or (callable(getattr(tool_base, "in_wsl", None)) and tool_base.in_wsl() and powershell):
                if browser_name == "default":
                    script = "; ".join(
                        f"Start-Process {_powershell_single_quote(url)}" for url in validated_urls
                    )
                else:
                    candidates = windows_browser_names[browser_name]
                    candidate_array = ", ".join(_powershell_single_quote(item) for item in candidates)
                    executable_name = candidates[0]
                    app_path_keys = (
                        f"HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{executable_name}",
                        f"HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\{executable_name}",
                    )
                    app_path_array = ", ".join(_powershell_single_quote(item) for item in app_path_keys)
                    standard_path_array = ", ".join(f'"{item}"' for item in windows_browser_paths[browser_name])
                    url_array = ", ".join(_powershell_single_quote(url) for url in validated_urls)
                    script = (
                        f"$browserCommand = @({candidate_array}) | ForEach-Object {{ Get-Command $_ -ErrorAction SilentlyContinue }} "
                        "| Select-Object -First 1; $browserPath = if ($browserCommand) { $browserCommand.Source } else { $null }; "
                        f"if (-not $browserPath) {{ $browserPath = @({app_path_array}) | ForEach-Object {{ "
                        "$item = Get-ItemProperty -LiteralPath $_ -ErrorAction SilentlyContinue; "
                        "if ($item) { $item.'(default)' } } | Where-Object { $_ -and (Test-Path -LiteralPath $_) } "
                        "| Select-Object -First 1 }; "
                        f"if (-not $browserPath) {{ $browserPath = @({standard_path_array}) "
                        "| Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1 }; "
                        f"if (-not $browserPath) {{ throw '{browser_name} browser executable was not found.' }}; "
                        f"Start-Process -FilePath $browserPath -ArgumentList @({url_array})"
                    )
                result = _run_powershell_command(tool_base, workspace, script, 120)
                return_code = int(result.get("return_code", result.get("returncode", 1)))
                if return_code != 0:
                    raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "Browser launch failed."))
            else:
                linux_candidates = {
                    "default": ("xdg-open",),
                    "firefox": ("firefox",),
                    "chrome": ("google-chrome", "chromium", "chromium-browser"),
                    "edge": ("microsoft-edge", "microsoft-edge-stable"),
                }
                executable = next((shutil.which(name) for name in linux_candidates[browser_name] if shutil.which(name)), None)
                if executable is None:
                    raise RuntimeError(f"No local {browser_name} browser launcher was found.")
                if browser_name == "default":
                    for url in validated_urls:
                        subprocess.Popen([executable, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen([executable, *validated_urls], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            return {
                "status": "opened",
                "browser": browser_name,
                "opened_urls": validated_urls,
                "opened_count": len(validated_urls),
            }

        @server.tool(
            description=(
                "Copy one workspace file or directory to a distinct workspace path. "
                "Directory destinations must not already exist; file overwrite must be explicit."
            ),
            annotations=_tool_annotations(tool_base, "copy_path"),
            structured_output=True,
        )
        def copy_path(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
            source_path = tool_base.resolve_workspace_path(
                workspace,
                source,
                allow_missing=False,
                allow_external=_full_access_enabled(),
            )
            destination_path = tool_base.resolve_workspace_path(
                workspace,
                destination,
                allow_missing=True,
                allow_external=_full_access_enabled(),
            )
            if source_path == destination_path:
                raise ValueError("Source and destination must be different paths.")
            if source_path.is_dir():
                try:
                    destination_path.relative_to(source_path)
                except ValueError:
                    pass
                else:
                    raise ValueError("A directory cannot be copied into itself.")
                if destination_path.exists():
                    raise FileExistsError(
                        "Directory copy destinations must not already exist; choose a new destination."
                    )
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_path, destination_path)
                copied_directory = True
            elif source_path.is_file():
                if destination_path.exists() and not overwrite:
                    raise FileExistsError(f"Destination already exists: {destination}")
                if destination_path.exists() and not destination_path.is_file():
                    raise ValueError("A file cannot overwrite a directory.")
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination_path.with_name(
                    f".{destination_path.name}.qubitz-copy-{os.getpid()}-{threading.get_ident()}.tmp"
                )
                try:
                    shutil.copy2(source_path, temporary)
                    os.replace(temporary, destination_path)
                finally:
                    with suppress(FileNotFoundError):
                        temporary.unlink()
                copied_directory = False
            else:
                raise ValueError(f"Unsupported source path: {source}")
            return {
                "source": tool_base.relative_path(source_path, workspace),
                "destination": tool_base.relative_path(destination_path, workspace),
                "copied_directory": copied_directory,
                "overwrite": bool(overwrite),
            }

        return server

    def build_model_tools(tools: Sequence[Any]) -> list[dict[str, Any]]:
        definitions = original_build_model_tools(tools)
        for definition in definitions:
            function = definition.get("function") or {}
            name = str(function.get("name", ""))
            function["parameters"] = _strict_tool_schema(function.get("parameters") or {"type": "object"})
            annotation = _tool_annotations(base, name)
            attributes: list[str] = []
            if annotation.readOnlyHint:
                attributes.append("read-only")
            if annotation.destructiveHint:
                attributes.append("destructive; approval-gated")
            if attributes:
                description = str(function.get("description", "")).strip()
                function["description"] = f"[{'; '.join(attributes)}] {description}".strip()
        return definitions

    def one_tool_chat(self: Any, **kwargs: Any) -> dict[str, Any]:
        response = original_chat(self, **kwargs)
        message = response.get("message") if isinstance(response, dict) else None
        if isinstance(message, dict) and isinstance(message.get("tool_calls"), list):
            message["tool_calls"] = message["tool_calls"][:1]
        return response

    class HardenedMCPHost(original_host):
        async def list_tools(self) -> list[Any]:
            tools = await super().list_tools()
            annotated = [tool.model_copy(update={"annotations": _tool_annotations(base, tool.name)}) for tool in tools]
            self._qubitz_tool_contracts = {tool.name: tool for tool in annotated}
            return annotated

        @staticmethod
        def _error_result(payload: dict[str, Any]) -> Any:
            rendered = json.dumps(payload, ensure_ascii=True, sort_keys=True)
            return base.mcp_types.CallToolResult(
                content=[base.mcp_types.TextContent(type="text", text=rendered)],
                structuredContent=payload,
                isError=True,
            )

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            contract = getattr(self, "_qubitz_tool_contracts", {}).get(name)
            broker = _TOOL_CALL_CONTEXT.get()
            if contract is not None:
                strict_schema = _strict_tool_schema(contract.inputSchema)
                argument_errors = _json_schema_errors(arguments, strict_schema)
                if argument_errors:
                    repaired_arguments, repairs, repaired_errors = _repair_tool_arguments(arguments, strict_schema)
                    if repairs and not repaired_errors and isinstance(repaired_arguments, dict):
                        if broker is not None:
                            broker.record_schema_repair(name, arguments, repaired_arguments, repairs)
                        arguments = repaired_arguments
                    else:
                        error_list = (repaired_errors or argument_errors)[:12]
                        invalid_count = (
                            broker.register_invalid_call(name, arguments, "; ".join(error_list))
                            if broker is not None
                            else 1
                        )
                        payload = {
                            "status": "repeated_invalid_arguments" if invalid_count > 1 else "invalid_arguments",
                            "tool": name,
                            "errors": error_list,
                            "repair_attempted": True,
                        }
                        if invalid_count > 1:
                            payload["instruction"] = (
                                "Do not repeat this equivalent call. Use materially corrected arguments or report the blocker."
                            )
                        if repairs:
                            payload["repairs"] = repairs[:12]
                        return self._error_result(payload)
            if broker is not None:
                arguments = broker.repair_command_arguments(name, arguments)
                semantic_error = broker.semantic_argument_error(name, arguments)
                if semantic_error:
                    invalid_count = broker.register_invalid_call(name, arguments, semantic_error)
                    invalid_result = self._error_result(
                        {
                            "status": "repeated_invalid_arguments" if invalid_count > 1 else "invalid_arguments",
                            "tool": name,
                            "errors": [semantic_error],
                            "repair_attempted": True,
                            "instruction": (
                                "Use materially corrected concrete arguments."
                                if invalid_count == 1
                                else "Do not repeat this equivalent call; report the unresolved postcondition."
                            ),
                        }
                    )
                    broker.record_result(name, arguments, invalid_result)
                    return invalid_result
                allowed, error_payload = broker.authorize(name, arguments)
                if not allowed:
                    assert error_payload is not None
                    denied_result = self._error_result(error_payload)
                    broker.record_result(name, arguments, denied_result)
                    return denied_result
            result = await super().call_tool(name, arguments)
            if contract is not None and contract.outputSchema and not result.isError:
                output_errors = _json_schema_errors(result.structuredContent, contract.outputSchema)
                if output_errors:
                    invalid_result = self._error_result(
                        {"status": "invalid_tool_result", "tool": name, "errors": output_errors[:12]}
                    )
                    if broker is not None:
                        broker.record_result(name, arguments, invalid_result)
                    return invalid_result
            if broker is not None:
                broker.record_result(name, arguments, result)
            return result

    class HardenedAgentRunner(original_runner):
        def _request_transaction_recovery(self) -> None:
            super().request_cancel()

        def request_cancel(self) -> None:
            prompt = self._tool_permission_broker.current_prompt
            partial_answer = self._tool_permission_broker.partial_progress_answer(prompt) if prompt else ""
            self._cancel_partial_answer = partial_answer or (
                "The task was cancelled before any requested result was verified.\n\n"
                "[Completion status: cancelled]"
            )
            super().request_cancel()

        def run_sync(self, prompt: str, callback: Any = None) -> str:
            self.set_access_mode(getattr(self.config, "access_mode", self.access_mode))
            started_at = base.time.perf_counter()
            answer = super().run_sync(prompt, callback)
            elapsed_seconds = max(0, int(round(base.time.perf_counter() - started_at)))
            minutes, seconds = divmod(elapsed_seconds, 60)
            return f"{str(answer).rstrip()}\n\n({minutes:02d}:{seconds:02d})"

        def __init__(self, config: Any) -> None:
            super().__init__(config)
            self._tool_permission_broker = _ToolPermissionBroker(
                base,
                self.workspace,
                getattr(self, "runtime_workspace", self.workspace),
            )
            self._tool_permission_broker.stop_callback = self.request_cancel
            self._tool_permission_broker.transaction_recovery_callback = self._request_transaction_recovery
            self.discovery_memory = WorkspaceDiscoveryMemory(
                self.workspace,
                getattr(self, "runtime_workspace", self.workspace),
            )
            self._active_discovery_context = ""
            initial_access_mode = getattr(
                config,
                "access_mode",
                os.environ.get(ACCESS_MODE_ENV_NAME, DEFAULT_ACCESS_MODE),
            )
            self.set_access_mode(initial_access_mode)

        def set_access_mode(self, value: Any) -> None:
            self.access_mode = _normalize_access_mode(value)
            setattr(self.config, "access_mode", self.access_mode)
            os.environ[ACCESS_MODE_ENV_NAME] = self.access_mode
            broker = getattr(self, "_tool_permission_broker", None)
            if broker is not None:
                broker.set_access_mode(self.access_mode)

        def _run_existing_script_preflight(self, prompt: str, callback: Any = None) -> str:
            context = super()._run_existing_script_preflight(prompt, callback)
            broker = self._tool_permission_broker
            focused = broker.update_focused_postconditions(prompt)
            self._focused_missing_postconditions = frozenset(focused)
            if focused and not context:
                context = broker.focused_evidence_context(prompt)
            direct_succeeded = any(
                evidence.get("tool") == "wrapper_direct_existing_entrypoint" and evidence.get("success")
                for evidence in broker._current_turn_evidence()
            )
            if direct_succeeded:
                entrypoint = self._resolve_existing_entrypoint_for_prompt(prompt)
                if isinstance(entrypoint, dict):
                    command = str(
                        entrypoint.get("command")
                        or entrypoint.get("path")
                        or entrypoint.get("label")
                        or ""
                    )
                    if command:
                        self.discovery_memory.record_entrypoint(
                            entrypoint,
                            command,
                            source="direct_preflight",
                        )
            return context

        def _runtime_fact_block(self, prompt: str) -> str:
            block = super()._runtime_fact_block(prompt)
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            if mode == ACCESS_MODE_FULL:
                access_fact = (
                    "- Access mode: Full Access; Qubitz per-action approvals are disabled, and explicit external "
                    "paths, HTTP/HTTPS access, local shell commands, PowerShell, and cmd.exe are available when "
                    "the runtime provides them and the task requires them."
                )
            elif mode == ACCESS_MODE_PLAN:
                access_fact = "- Access mode: Plan Mode; inspect and research, but do not perform side effects."
            else:
                access_fact = "- Access mode: Read-only; read and inspect, but do not perform side effects."
            return f"{block}\n{access_fact}"

        def _select_task_guidance(self, prompt: str) -> str:
            guidance = super()._select_task_guidance(prompt)
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            if mode == ACCESS_MODE_PLAN:
                return f"{guidance}\n- Produce a concise executable plan only; do not modify files, run projects, install packages, or create external side effects."
            if mode == ACCESS_MODE_READ_ONLY:
                return f"{guidance}\n- Read and inspect only; do not modify files, run projects, install packages, or create external side effects."
            return guidance

        def _selected_route_name(self, prompt: str) -> str:
            selected = super()._selected_route_name(prompt)
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            if mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN} and selected == "direct_existing_entrypoint":
                return "retrieval_plus_model"
            return selected

        def _decide_route(self, prompt: str) -> Any:
            decision = super()._decide_route(prompt)
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            if mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN} and decision.selected_route == "direct_existing_entrypoint":
                return base.replace(
                    decision,
                    selected_route="retrieval_plus_model",
                    reason=f"{_access_mode_label(mode)} disables direct entrypoint execution.",
                    fallback_routes=["read_only_workspace", "retrieval_plus_model"],
                )
            return decision

        async def _run_async(self, prompt: str, callback: Any = None) -> str:
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            if mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN} and _prompt_requests_project_launch(base, prompt):
                prefix = (
                    "Plan the following request without executing it:"
                    if mode == ACCESS_MODE_PLAN
                    else "Answer the following request using read-only inspection without executing it:"
                )
                prompt = f"{prefix}\n\n{prompt}"
            return await super()._run_async(prompt, callback)

        @staticmethod
        def _tool_description(tool: Any) -> str:
            if isinstance(tool, dict):
                function = tool.get("function") or {}
                return str(function.get("description", "") or "")
            return str(getattr(tool, "description", "") or "")

        def _filter_tools_by_intent(self, prompt: str, tools: Sequence[Any]) -> list[Any]:
            lowered = prompt.lower()
            mode = _normalize_access_mode(getattr(self, "access_mode", DEFAULT_ACCESS_MODE))
            capabilities = self._runtime_capabilities()
            if capabilities.get("in_wsl") and not capabilities.get("workspace_is_windows_backed"):
                tools = [
                    tool
                    for tool in tools
                    if self._tool_name(tool)
                    not in {"run_cmd_command", "run_powershell_command", "sandbox_run_powershell"}
                ]
            explicit_tool_selection = False
            only_clause = re.search(
                r"\buse\s+([a-z][a-z0-9_]*(?:\s*(?:,|and)\s*[a-z][a-z0-9_]*)*)\s+only\b",
                lowered,
            )
            if only_clause is not None:
                requested_tool_names = set(re.findall(r"[a-z][a-z0-9_]*", only_clause.group(1)))
                available_tool_names = {self._tool_name(tool) for tool in tools}
                requested_tool_names.intersection_update(available_tool_names)
                if requested_tool_names:
                    tools = [tool for tool in tools if self._tool_name(tool) in requested_tool_names]
                    explicit_tool_selection = True
            if mode in {ACCESS_MODE_READ_ONLY, ACCESS_MODE_PLAN}:
                return [
                    tool
                    for tool in tools
                    if self._tool_name(tool) in _ToolPermissionBroker._RESTRICTED_MODE_TOOLS
                ]
            if explicit_tool_selection:
                return list(tools)
            focused_postconditions = self._tool_permission_broker.update_focused_postconditions(
                self._tool_permission_broker.current_prompt or prompt
            )
            self._focused_missing_postconditions = frozenset(focused_postconditions)
            transaction_recovery = bool(getattr(self._tool_permission_broker, "transaction_recovery_required", False))
            if transaction_recovery and not self._tool_permission_broker.transaction_changed_hashes:
                focused_postconditions.add("changed_files")
                self._focused_missing_postconditions = frozenset(focused_postconditions)
            if focused_postconditions:
                focused_names = {"read_file", "read_file_snapshot", "search_text", "list_files"}
                if not transaction_recovery:
                    focused_names.add("search_tools")
                focused_tool_map = {
                    "opened_urls": {"open_urls_in_browser", "fetch_url"},
                    "result_count": {"fetch_url", "run_existing_entrypoint"},
                    "created_outputs": {
                        "apply_text_patch",
                        "apply_text_patches",
                        "copy_path",
                        "run_existing_entrypoint",
                        "write_file",
                    },
                    "changed_files": {
                        "apply_text_patch",
                        "apply_text_patches",
                        "read_file_snapshot",
                        "run_existing_entrypoint",
                    },
                    "tests": {"run_existing_entrypoint"},
                    "service_started": {"run_existing_entrypoint", "start_project_mcp_server"},
                    "service_stopped": {"cancel_background_job", "stop_project_mcp_server"},
                    "mcp_ready": {"list_project_mcp_tools", "start_project_mcp_server"},
                    "mcp_tool_called": {"call_project_mcp_tool", "list_project_mcp_tools"},
                }
                for postcondition in focused_postconditions:
                    focused_names.update(focused_tool_map.get(postcondition, set()))
                if (
                    "changed_files" in focused_postconditions
                    and not self._tool_permission_broker.transaction_changed_hashes
                ):
                    focused_names.difference_update(_ToolPermissionBroker._EXECUTION_TOOLS)
                if transaction_recovery:
                    if self._tool_permission_broker.transaction_changed_hashes:
                        focused_names.difference_update(_ToolPermissionBroker._MUTATION_TOOLS)
                        focused_names.difference_update(_ToolPermissionBroker._EXECUTION_TOOLS)
                        focused_names.update(
                            {
                                "read_file",
                                "read_file_snapshot",
                                "search_text",
                                "list_files",
                                "codeintel_diagnostics",
                                "run_existing_entrypoint",
                            }
                        )
                    else:
                        focused_names.update(
                            {
                                "read_file_snapshot",
                                "apply_text_patch",
                                "apply_text_patches",
                                "read_file",
                                "search_text",
                                "list_files",
                                "codeintel_diagnostics",
                            }
                        )
                        focused_names.difference_update(_ToolPermissionBroker._EXECUTION_TOOLS)
                elif focused_postconditions == {"tests"}:
                    focused_names.update({"run_command", "run_project_command", "run_powershell_command"})
                focused_tools = [tool for tool in tools if self._tool_name(tool) in focused_names]
                if focused_tools:
                    return focused_tools
            tokens = set(re.findall(r"[a-z0-9_]+", lowered))
            semantic = _analyze_semantic_intent(prompt)
            read_intent = semantic.requests("fetch_url", "read") or bool(
                re.search(r"\b(?:analy[sz]e|compare|explain|find|search|summarize)\b", lowered)
            )
            edit_intent = (
                "changed_files" in self._tool_permission_broker.required_postconditions
                or semantic.requests("copy", "create", "delete", "edit", "move")
            )
            execute_intent = semantic.requests(
                "execute",
                "clone_repository",
                "mcp_call",
                    "mcp_discover",
                    "open_browser",
                    "open_project",
                    "restart",
                    "start",
                    "stop",
                    "verify",
                    "install",
                    "uninstall",
                    "upgrade",
            )
            install_intent = semantic.requests("install", "uninstall", "upgrade")
            mcp_intent = bool(re.search(r"\b(?:mcp|model context protocol)\b", lowered))
            skill_intent = bool(re.search(r"\bskills?\b", lowered))
            memory_intent = bool(re.search(r"\b(?:memory|recall|remember)\b", lowered))
            sandbox_intent = bool(re.search(r"\b(?:sandbox|isolated)\b", lowered))
            job_intent = bool(re.search(r"\b(?:background|job|jobs)\b", lowered))
            destructive_intent = bool(re.search(r"\b(?:delete|destroy|erase|remove)\b", lowered))

            def category(name: str) -> str:
                normalized = name.lower()
                if normalized == "search_tools":
                    return "catalog"
                if "skill" in normalized:
                    return "skill"
                if "memory" in normalized or "recall" in normalized:
                    return "memory"
                if "sandbox" in normalized:
                    return "sandbox"
                if "background" in normalized or "job" in normalized:
                    return "job"
                if "mcp" in normalized:
                    return "mcp"
                if "install" in normalized or "package" in normalized:
                    return "install"
                if normalized in _ToolPermissionBroker._MUTATION_TOOLS:
                    return "edit"
                if normalized in _ToolPermissionBroker._EXECUTION_TOOLS or normalized.startswith("run_"):
                    return "execute"
                if normalized in _ToolPermissionBroker._READ_TOOLS or normalized.startswith(("read_", "list_", "find_")):
                    return "read"
                return "other"

            allowed_categories = {"catalog", "read"}
            intent_categories: set[str] = set()
            category_boosts: dict[str, int] = {}
            if edit_intent:
                allowed_categories.add("edit")
                intent_categories.add("edit")
                category_boosts["edit"] = 150
            if execute_intent or edit_intent:
                allowed_categories.add("execute")
                intent_categories.add("execute")
                category_boosts["execute"] = 140
            if install_intent:
                allowed_categories.add("install")
                intent_categories.add("install")
                category_boosts["install"] = 170
            if mcp_intent:
                allowed_categories.add("mcp")
                intent_categories.add("mcp")
                category_boosts["mcp"] = 170
            if skill_intent:
                allowed_categories.add("skill")
                intent_categories.add("skill")
                category_boosts["skill"] = 160
            if memory_intent:
                allowed_categories.add("memory")
                intent_categories.add("memory")
                category_boosts["memory"] = 160
            if sandbox_intent:
                allowed_categories.add("sandbox")
                intent_categories.add("sandbox")
                category_boosts["sandbox"] = 160
            if job_intent:
                allowed_categories.add("job")
                intent_categories.add("job")
                category_boosts["job"] = 160
            if read_intent:
                intent_categories.add("read")
                category_boosts["read"] = 70

            preferred_order = {
                "apply_text_patches": 101,
                "read_file_snapshot": 100,
                "apply_text_patch": 99,
                "run_existing_entrypoint": 98,
                "fetch_url": 97,
                "open_urls_in_browser": 97,
                "run_cmd_command": 96,
                "read_file": 90,
                "search_text": 89,
                "list_files": 88,
                "workspace_symbols": 80,
                "find_symbol_definitions": 79,
                "find_symbol_references": 78,
                "codeintel_diagnostics": 77,
                "search_tools": 10,
            }
            ranked: list[tuple[int, int, Any]] = []
            for index, tool in enumerate(tools):
                name = self._tool_name(tool)
                tool_category = category(name)
                if tool_category not in allowed_categories:
                    continue
                if tool_category == "edit" and not edit_intent:
                    continue
                if edit_intent and name in {
                    "run_command",
                    "run_project_command",
                    "run_powershell_command",
                    "run_cmd_command",
                }:
                    continue
                if name in {"delete_path", "sandbox_delete_path", "sandbox_destroy"} and not destructive_intent:
                    continue
                haystack = f"{name} {self._tool_description(tool)}".lower()
                name_tokens = set(re.findall(r"[a-z0-9_]+", name.lower().replace("_", " ")))
                score = preferred_order.get(name, 40)
                score += category_boosts.get(tool_category, 0)
                score += 8 * len(tokens.intersection(name_tokens))
                score += sum(1 for token in tokens if len(token) > 2 and token in haystack)
                if tool_category == "catalog":
                    score = preferred_order["search_tools"]
                ranked.append((score, -index, tool))
            ranked.sort(key=lambda item: (-item[0], -item[1]))
            selected_limit = TOOL_EXPOSURE_MAX if intent_categories else 5
            if install_intent:
                pinned_names = ["read_local_only_config", "list_files", "read_file_snapshot", "install_python_package"]
            elif mcp_intent:
                pinned_names = [
                    "list_project_mcp_tools",
                    "call_project_mcp_tool",
                    "start_project_mcp_server",
                    "read_project_mcp_server_log",
                    "stop_project_mcp_server",
                ]
            elif edit_intent:
                pinned_names = [
                    "read_file_snapshot",
                    "apply_text_patches",
                    "search_text",
                    "apply_text_patch",
                    "list_files",
                    "run_existing_entrypoint",
                    "codeintel_diagnostics",
                ]
                if destructive_intent:
                    pinned_names.extend(["move_path", "delete_path"])
                else:
                    pinned_names.append("write_file")
            elif execute_intent:
                pinned_names = [
                    "run_existing_entrypoint",
                    "read_file_snapshot",
                    "read_file",
                    "search_text",
                    "list_files",
                    "run_command",
                    "codeintel_diagnostics",
                ]
            else:
                pinned_names = [
                    "read_file_snapshot",
                    "read_file",
                    "search_text",
                    "list_files",
                    "workspace_symbols",
                    "find_symbol_definitions",
                    "find_symbol_references",
                ]
            ranked_by_name = {self._tool_name(tool): tool for _, _, tool in ranked}
            selected = [ranked_by_name[name] for name in pinned_names if name in ranked_by_name]
            selected_names = {self._tool_name(tool) for tool in selected}
            for _, _, tool in ranked:
                name = self._tool_name(tool)
                if name in selected_names:
                    continue
                selected.append(tool)
                selected_names.add(name)
                if len(selected) >= selected_limit:
                    break
            selected = selected[:selected_limit]
            if edit_intent and not self._tool_permission_broker.transaction_changed_hashes:
                selected = [
                    tool
                    for tool in selected
                    if self._tool_name(tool) not in _ToolPermissionBroker._EXECUTION_TOOLS
                ]
            catalog_tool = next((tool for tool in tools if self._tool_name(tool) == "search_tools"), None)
            if (
                catalog_tool is not None
                and not (edit_intent and not self._tool_permission_broker.transaction_changed_hashes)
                and all(self._tool_name(tool) != "search_tools" for tool in selected)
            ):
                selected = selected[: max(0, selected_limit - 1)] + [catalog_tool]
            if not selected and tools:
                selected = [tool for tool in tools if self._tool_name(tool) == "search_tools"][:1]
            return selected

        def _prioritize_tools_for_prompt(self, prompt: str, tools: Sequence[Any]) -> list[Any]:
            focused = self._tool_permission_broker.update_focused_postconditions(
                self._tool_permission_broker.current_prompt or prompt
            )
            self._focused_missing_postconditions = frozenset(focused)
            if focused:
                return self._filter_tools_by_intent(prompt, tools)
            return super()._prioritize_tools_for_prompt(prompt, tools)

        async def _run_async(self, prompt: str, callback: Callable[[str, str], None] | None = None) -> str:
            self._cancel_partial_answer = ""
            self._focused_missing_postconditions = frozenset()
            self._active_discovery_context = ""
            with suppress(Exception):
                if self._selected_route_name(prompt) != "simple_answer":
                    self._active_discovery_context = self.discovery_memory.context_for(prompt)
            self._tool_permission_broker.begin_turn(prompt, callback)
            broker_context_token = _TOOL_CALL_CONTEXT.set(self._tool_permission_broker)

            def rollback_interrupted_transaction(reason: str) -> dict[str, list[str]]:
                if not self._tool_permission_broker.transaction_changed_hashes:
                    return {"restored": [], "skipped": []}
                rollback = self._tool_permission_broker.rollback_transaction()
                if rollback["restored"] and callback is not None:
                    callback(
                        "status",
                        f"Transactional rollback restored {len(rollback['restored'])} file(s) after {reason}.",
                    )
                return rollback

            try:
                try:
                    answer = await super()._run_async(prompt, callback)
                except (Exception, asyncio.CancelledError):
                    if (
                        self._tool_permission_broker.transaction_recovery_interrupt_requested
                        and not self._cancel_partial_answer
                    ):
                        answer = ""
                    else:
                        rollback_interrupted_transaction("an interrupted request")
                        if self._cancel_partial_answer:
                            return self._cancel_partial_answer
                        raise
                if self._cancel_partial_answer:
                    rollback_interrupted_transaction("request cancellation")
                    return self._cancel_partial_answer
                if self._tool_permission_broker.transaction_recovery_required:
                    recovery_context = self._tool_permission_broker.transaction_recovery_context()
                    focused = set(self._tool_permission_broker.required_postconditions)
                    if not self._tool_permission_broker.transaction_changed_hashes:
                        focused.add("changed_files")
                    self._focused_missing_postconditions = frozenset(focused)
                    self._tool_permission_broker.transaction_recovery_interrupt_requested = False
                    self._tool_permission_broker.transaction_nonprogress_calls = 0
                    if callback is not None:
                        callback(
                            "status",
                            "Retrying once with wrapper-owned transactional patch tools and preserved snapshots.",
                        )
                    try:
                        answer = await super()._run_async(
                            f"{prompt.rstrip()}\n\n{recovery_context}",
                            callback,
                        )
                    except (Exception, asyncio.CancelledError):
                        if (
                            self._tool_permission_broker.transaction_recovery_interrupt_requested
                            and not self._cancel_partial_answer
                        ):
                            answer = self._tool_permission_broker.partial_progress_answer(prompt)
                        else:
                            rollback_interrupted_transaction("an interrupted transactional continuation")
                            if self._cancel_partial_answer:
                                return self._cancel_partial_answer
                            raise
                required, missing = self._tool_permission_broker.completion_report(prompt)
                rollback = {"restored": [], "skipped": []}
                if missing.intersection({"changed_files", "tests"}) and self._tool_permission_broker.transaction_changed_hashes:
                    rollback = self._tool_permission_broker.rollback_transaction()
                    if rollback["restored"] and callback is not None:
                        callback(
                            "status",
                            f"Transactional rollback restored {len(rollback['restored'])} file(s) after incomplete verification.",
                        )
                if missing:
                    missing_text = ", ".join(sorted(item.replace("_", " ") for item in missing))
                    if callback is not None:
                        callback("status", f"Completion verification is incomplete: {missing_text}.")
                    rollback_text = ""
                    if rollback["restored"]:
                        rollback_text = f" Transaction rollback restored {len(rollback['restored'])} file(s)."
                    if rollback["skipped"]:
                        rollback_text += f" Rollback skipped {len(rollback['skipped'])} concurrently changed or unavailable file(s)."
                    rendered = re.sub(
                        r"\n\n\[Completion status: (?:partial|verified|full)\].*\Z",
                        "",
                        str(answer).rstrip(),
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    evidence_summary = self._tool_permission_broker.completion_evidence_summary(prompt)
                    return (
                        f"{rendered}\n\nVerified current-turn evidence: {evidence_summary}.\n\n"
                        f"[Completion status: partial] "
                        f"Unverified postconditions: {missing_text}.{rollback_text}"
                    )
                if required and callback is not None:
                    callback("status", "Completion verification passed for the requested postconditions.")
                rendered_answer = str(answer).rstrip()
                conflict = bool(
                    re.search(
                        r"\[Completion status:\s*partial\]|\bunverified postconditions?\b",
                        rendered_answer,
                        re.IGNORECASE,
                    )
                )
                count_requirement = _requested_count_postcondition(prompt)
                if count_requirement is not None:
                    _category, expected_count = count_requirement
                    for match in re.finditer(r"\b(\d+)\s*(?:/|of)\s*(\d+)\b", rendered_answer, re.IGNORECASE):
                        observed_text, expected_text = match.groups()
                        if int(expected_text) == expected_count and int(observed_text) < expected_count:
                            conflict = True
                            break
                if required and conflict:
                    rendered_answer = re.sub(
                        r"\n\n\[Completion status: (?:partial|verified|full)\].*\Z",
                        "",
                        rendered_answer,
                        flags=re.IGNORECASE | re.DOTALL,
                    )
                    summary = self._tool_permission_broker.completion_evidence_summary(prompt)
                    rendered_answer = (
                        f"{rendered_answer}\n\n[Completion evidence correction] Structured runtime evidence verified: "
                        f"{summary}. This supersedes any contradictory completion wording above."
                    )
                return rendered_answer
            finally:
                with suppress(Exception):
                    self.discovery_memory.observe_tool_evidence(
                        prompt,
                        self._tool_permission_broker._current_turn_evidence(),
                    )
                self._tool_permission_broker.end_turn()
                self._active_discovery_context = ""
                _TOOL_CALL_CONTEXT.reset(broker_context_token)

        def _system_prompt(self, *args: Any, **kwargs: Any) -> str:
            prompt = super()._system_prompt(*args, **kwargs)
            discovery_context = getattr(self, "_active_discovery_context", "")
            discovery_section = (
                "\n\nRelevant dynamic workspace memory:\n" + discovery_context
                if discovery_context
                else ""
            )
            return (
                f"{prompt}{discovery_section}\n\n"
                "Runtime tool contract:\n"
                "- The visible tools are a bounded task-relevant subset; use search_tools only when a required capability is missing.\n"
                "- For one existing UTF-8 file within the transaction limit, use read_file_snapshot then apply_text_patch. For multiple files, call apply_text_patches once with only paths and exact replacements; the wrapper owns snapshots, hashes, ordering, atomicity, and rollback. Direct overwrite tools are rejected.\n"
                "- During an edit transaction, inspect and submit edits before verification; command and test tools become available after a transactional edit succeeds.\n"
                "- Required tests or checks must pass after edits. If final verification is incomplete, transactional edits are rolled back when the file has not changed concurrently.\n"
                "- For MCP work: establish readiness, list tools, invoke the intended tool when requested, verify its result, and stop managed servers started for the task.\n"
                "- Execute state-changing tools sequentially. Approval-required results mean the action did not run; ask for the exact approval id.\n"
                "- Claim completion only for postconditions verified by successful tool results; otherwise report the unverified remainder explicitly."
            )

    cancel_pattern = re.compile(
        r"^\s*(?:stop|cancel|abort|interrupt)(?:\s+(?:this|the|my)?\s*(?:current\s+)?(?:task|request|run|operation))?[.!]?\s*$",
        re.IGNORECASE,
    )
    status_pattern = re.compile(r"^\s*(?:status|task status|what are you doing|are you running)[?!.]?\s*$", re.IGNORECASE)

    class HardenedGUI(original_gui):
        def __init__(self, config: Any) -> None:
            if not hasattr(config, "access_mode"):
                setattr(
                    config,
                    "access_mode",
                    os.environ.get(ACCESS_MODE_ENV_NAME, DEFAULT_ACCESS_MODE),
                )
            super().__init__(config)
            self.access_mode_var = self.tk.StringVar(
                master=self.root,
                value=_access_mode_label(getattr(config, "access_mode", DEFAULT_ACCESS_MODE)),
            )
            access_bar = self.ttk.Frame(self.prompt_box.master.master)
            access_bar.pack(fill="x", pady=(8, 0))
            self.ttk.Label(access_bar, text="Access").pack(side="left")
            self.access_mode_entry = self.ttk.Combobox(
                access_bar,
                textvariable=self.access_mode_var,
                values=ACCESS_MODE_VALUES,
                state="readonly",
                width=14,
            )
            self.access_mode_entry.pack(side="left", padx=(6, 0))
            self.access_mode_entry.bind("<<ComboboxSelected>>", self._apply_access_mode)
            self._apply_access_mode()

        def _apply_access_mode(self, _event: Any = None) -> None:
            mode = _normalize_access_mode(self.access_mode_var.get())
            setattr(self.config, "access_mode", mode)
            self.agent.set_access_mode(mode)

        def _sync_runtime_settings(self) -> None:
            super()._sync_runtime_settings()
            self._apply_access_mode()

        def _set_busy(self, value: bool) -> None:
            super()._set_busy(value)
            if hasattr(self, "access_mode_entry"):
                self.access_mode_entry.configure(state="disabled" if value else "readonly")

        def _finalize_elapsed_message(self, message: str) -> str:
            started_at = getattr(self, "_qubitz_prompt_started_at", base.time.perf_counter())
            elapsed_seconds = max(0, int(round(base.time.perf_counter() - started_at)))
            minutes, seconds = divmod(elapsed_seconds, 60)
            rendered = re.sub(r"\n\n\(\d+:\d{2}\)\s*$", "", str(message).rstrip())
            return f"{rendered}\n\n({minutes:02d}:{seconds:02d})"

        def _start_prompt(self, prompt: str, *, queued: bool = False) -> None:
            if not getattr(self, "_qubitz_prompt_timer_armed", False):
                self._qubitz_prompt_started_at = base.time.perf_counter()
            self._qubitz_prompt_timer_armed = False
            super()._start_prompt(prompt, queued=queued)

        def _worker_run_tagged(self, prompt: str, run_id: int) -> None:
            started_at = getattr(self, "_qubitz_prompt_started_at", base.time.perf_counter())

            def emit(kind: str, message: str) -> None:
                self.event_queue.put((run_id, kind, message))

            try:
                answer = self.agent.run_sync(prompt, emit)
            except Exception as exc:
                details = "".join(base.traceback.format_exception(exc))
                self.event_queue.put((run_id, "error", details))
            else:
                elapsed_seconds = max(0, int(round(base.time.perf_counter() - started_at)))
                minutes, seconds = divmod(elapsed_seconds, 60)
                rendered = re.sub(r"\n\n\(\d+:\d{2}\)\s*$", "", str(answer).rstrip())
                self.event_queue.put((run_id, "answer", f"{rendered}\n\n({minutes:02d}:{seconds:02d})"))
            finally:
                self.event_queue.put((run_id, "done", ""))

        def _poll_events(self) -> None:
            while True:
                try:
                    item = self.event_queue.get_nowait()
                except base.queue.Empty:
                    break
                if len(item) == 3:
                    run_id, kind, message = item
                    if run_id != getattr(self, "_active_gui_run_id", run_id):
                        continue
                else:
                    kind, message = item
                if kind == "assistant_delta":
                    self.status_var.set("Generating")
                    self._append_stream_delta(message)
                    continue
                self._clear_stream_preview()
                if kind == "status":
                    self.status_var.set(message)
                    self._append_transcript("status", message)
                elif kind == "tool":
                    self._append_transcript("tool", message)
                elif kind == "answer":
                    self._append_transcript("assistant", self._finalize_elapsed_message(message))
                elif kind == "error":
                    self._append_transcript("error", message)
                    self.messagebox.showerror("AI Agent Qubitz", message)
                elif kind == "done":
                    self._set_busy(False)
                    if self.pending_prompts:
                        next_prompt = self.pending_prompts.pop(0)
                        self._update_send_button()
                        self._start_prompt(next_prompt, queued=True)
            self.root.after(100, self._poll_events)

        def send_prompt(self) -> None:
            raw_prompt = self.prompt_box.get("1.0", "end").strip()
            if not raw_prompt:
                return
            if cancel_pattern.fullmatch(raw_prompt):
                self.prompt_box.delete("1.0", "end")
                self._append_transcript("user", raw_prompt)
                if self.busy:
                    queued = len(self.pending_prompts)
                    self.pending_prompts.clear()
                    self._request_cancel()
                    suffix = f" and cleared {queued} queued task(s)" if queued else ""
                    self._append_transcript("status", f"Cancelling the active Qubitz task{suffix}.")
                else:
                    self._append_transcript("assistant", "No foreground Qubitz task is running.")
                return
            if status_pattern.fullmatch(raw_prompt):
                self.prompt_box.delete("1.0", "end")
                self._append_transcript("user", raw_prompt)
                state = "running" if self.busy else "idle"
                self._append_transcript("assistant", f"Foreground task: {state}. Queued tasks: {len(self.pending_prompts)}.")
                return
            if not self.busy:
                self._qubitz_prompt_started_at = base.time.perf_counter()
                self._qubitz_prompt_timer_armed = True
            super().send_prompt()

    _build_local_mcp_server = build_semantic_mcp_server
    base.build_model_tools = build_model_tools
    base.LlamaCppClient.chat = one_tool_chat
    base.MCPHost = HardenedMCPHost
    base.AgentRunner = HardenedAgentRunner
    base.QubitzGUI = HardenedGUI


_install_agent_tool_hardening(_APP)

parse_args = _APP.parse_args
run_cli = _APP.run_cli
serve_mcp = _APP.serve_mcp
main = _APP.main


if __name__ == "__main__":
    main()
