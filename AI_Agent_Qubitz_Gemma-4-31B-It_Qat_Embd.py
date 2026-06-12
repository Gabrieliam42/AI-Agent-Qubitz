from __future__ import annotations

# Standalone local-only wrapper with vendored support code and embedded base module.
# This file intentionally embeds both the wrapper implementation and its
# corresponding base app so it can run independently.

import ast
import base64
import difflib
import gzip
import hashlib
import json
import locale
import os
import re
import shlex
import shutil
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    import qubitz_ump_local as qubitz_ump
except Exception:
    qubitz_ump = None


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
THINKING_EFFORT_OPTIONS = ("default", "low", "medium", "high", "xhigh")
THINKING_EFFORT_DISPLAY_OPTIONS = ("Default", "low", "medium", "high", "xhigh")
SIMPLE_DIRECT_QUESTION_STEP_CAP = 2
THINKING_EFFORT_STEP_CAPS: dict[str, int | None] = {
    "default": None,
    "low": 8,
    "medium": 16,
    "high": 24,
    "xhigh": 0,
}
DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS = 300
DIRECT_SCRIPT_COMPLETION_MAX_URLS = 64
DIRECT_RESULT_MISSING_URL = "(no URL)"
SCRIPT_BROWSER_GLOB_PATTERNS = ("open*.ps1", "open*.bat", "open*.cmd")
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
    relative = str(base.relative_path(target, workspace)).replace("/", "\\")
    if not relative.startswith(".") and not re.match(r"^[A-Za-z]:[\\/]", relative):
        relative = f".\\{relative}"
    return relative


def _normalize_prompt_path_token(token: str) -> str:
    candidate = str(token).strip().strip("`\"'")
    if candidate.startswith("\\") and not candidate.startswith("\\\\"):
        return f".{candidate}"
    return candidate


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
    return specs[0] if specs else None


def _prompt_has_explicit_entrypoint_command(prompt: str) -> bool:
    return bool(
        UV_RUN_COMMAND_PATTERN.search(prompt)
        or PACKAGE_SCRIPT_COMMAND_PATTERN.search(prompt)
        or MAKE_TARGET_COMMAND_PATTERN.search(prompt)
    )


def _collect_browser_helper_candidates(workspace: Path) -> list[Path]:
    candidates: list[Path] = []
    seen: set[str] = set()
    for pattern in SCRIPT_BROWSER_GLOB_PATTERNS:
        for candidate in workspace.glob(pattern):
            if not candidate.is_file():
                continue
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    return sorted(candidates, key=lambda item: item.stat().st_mtime, reverse=True)


def _extract_start_process_urls(script_text: str) -> list[str]:
    urls: list[str] = []
    for match in START_PROCESS_URL_PATTERN.finditer(script_text):
        target = match.group("target").strip()
        if not target:
            continue
        urls.append(DIRECT_RESULT_MISSING_URL if target == "#" else target)
        if len(urls) >= DIRECT_SCRIPT_COMPLETION_MAX_URLS:
            break
    return urls


def _prompt_requests_browser_open(prompt: str) -> bool:
    lowered = prompt.lower()
    if "start-process" in lowered:
        return True
    return bool(re.search(r"\bopen(?:ing)?\b.*\burls?\b", lowered))


def _run_inline_browser_open(
    base: Any,
    workspace: Path,
    urls: Sequence[str],
    timeout_seconds: int,
) -> dict[str, Any]:
    commands = [
        f"Start-Process {_powershell_single_quote(url)}"
        for url in urls
        if str(url).strip() and str(url).strip() not in {"#", DIRECT_RESULT_MISSING_URL}
    ][:DIRECT_SCRIPT_COMPLETION_MAX_URLS]
    if not commands:
        return {"return_code": 1, "stderr": "No URLs were available for browser opening."}
    return _run_powershell_command(base, workspace, "; ".join(commands), timeout_seconds)


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
                position = lowered.find(alias)
                if position == -1:
                    continue
                best = position if best is None else min(best, position)
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
            r"\b(\d+)\s+(?:items|results|rows|records|entries|files|objects|stories)\b",
            prompt,
            flags=re.IGNORECASE,
        )
    if match is None:
        return None
    return int(match.group(1))


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
            return preferred.resolve()
        return Path(sys.executable).resolve()

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
            import sys
            from pathlib import Path
            from fastmcp import Client

            BEGIN = "QUBITZ_JSON_BEGIN"
            END = "QUBITZ_JSON_END"

            async def main() -> None:
                ref_mode = sys.argv[1]
                ref_value = sys.argv[2]
                ref = Path(ref_value) if ref_mode == "path" else ref_value
                async with Client(ref) as client:
                    tools = await client.list_tools()
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
        with suppress(Exception):
            os.kill(int(pid), 15)
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


def _preferred_project_linux_python(workspace: Path) -> Path | None:
    candidate = workspace / ".venv" / "bin" / "python"
    if _path_exists_safely(candidate):
        return candidate
    return None


def _preferred_project_windows_python(workspace: Path) -> Path | None:
    for candidate in (
        workspace / ".venv312" / "Scripts" / "python.exe",
        workspace / ".venv313" / "Scripts" / "python.exe",
        workspace / ".venv" / "Scripts" / "python.exe",
    ):
        if _path_exists_safely(candidate):
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


def _should_use_windows_shell(base: Any, command: str) -> bool:
    if not callable(getattr(base, "in_wsl", None)) or not base.in_wsl():
        return False
    if shutil.which("powershell.exe") is None:
        return False
    stripped = command.strip()
    if not stripped:
        return False
    lower = stripped.lower()
    if lower.startswith(("powershell ", "powershell.exe ", "pwsh ", "pwsh.exe ")):
        return True
    if any(token in lower for token in (".ps1", ".bat", ".cmd")):
        return True
    if re.search(r"(^|[\s;&|])\.[\\/]", stripped):
        return True
    pattern = getattr(base, "WINDOWS_DRIVE_PATH_PATTERN", None)
    if pattern is not None and pattern.match(stripped):
        return True
    first_token = stripped.split(maxsplit=1)[0]
    if "\\" in first_token and first_token.lower().endswith((".exe", ".cmd", ".bat")):
        return True
    return False


def _run_shell_command(base: Any, workspace: Path, command: str, timeout_seconds: int) -> dict[str, Any]:
    if _should_use_windows_shell(base, command):
        workspace_windows = _workspace_windows_path(base, workspace)
        script_body = _canonicalize_workspace_script(base, workspace, _extract_powershell_script(command))
        script_lines = [
            "$ProgressPreference = 'SilentlyContinue'",
            f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
            script_body,
        ]
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "; ".join(line for line in script_lines if line)],
            capture_output=True,
            timeout=max(1, timeout_seconds),
            check=False,
        )
    else:
        command = _canonicalize_workspace_command(workspace, command)
        completed = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            shell=True,
            timeout=max(1, timeout_seconds),
            check=False,
        )
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout": _shorten(_decode_subprocess_output(completed.stdout), 12000),
        "stderr": _shorten(_decode_subprocess_output(completed.stderr), 12000),
    }


def _run_powershell_command(base: Any, workspace: Path, command: str, timeout_seconds: int) -> dict[str, Any]:
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or shutil.which("pwsh")
    if powershell is None:
        raise RuntimeError("PowerShell was not found on this system.")
    use_windows_paths = powershell.lower().endswith(".exe") and callable(getattr(base, "in_wsl", None)) and base.in_wsl()
    workspace_windows = _workspace_windows_path(base, workspace) if use_windows_paths else str(workspace)
    script_body = _canonicalize_workspace_script(base, workspace, _extract_powershell_script(command))
    script_lines = [
        "$ProgressPreference = 'SilentlyContinue'",
        f"Set-Location -LiteralPath {_powershell_single_quote(workspace_windows)}",
        script_body,
    ]
    completed = subprocess.run(
        [powershell, "-NoProfile", "-Command", "; ".join(line for line in script_lines if line)],
        capture_output=True,
        timeout=max(1, timeout_seconds),
        check=False,
    )
    return {
        "command": command,
        "return_code": completed.returncode,
        "stdout": _shorten(_decode_subprocess_output(completed.stdout), 12000),
        "stderr": _shorten(_decode_subprocess_output(completed.stderr), 12000),
    }


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
    skills = base.SkillRegistry(runtime_workspace)
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
    ump_store = (
        qubitz_ump.LocalUMPStore(
            runtime_workspace=runtime_workspace,
            workspace=workspace,
            projection_path=current_memory_path,
            agent_name=launch_script.stem,
        )
        if qubitz_ump is not None
        else None
    )
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
                "ump_store": ump_store.store_path.as_posix() if ump_store is not None else "",
                "ump_project": ump_store.project_key if ump_store is not None else "",
                "ump_record_count": ump_store.count() if ump_store is not None else 0,
                "skills_root": base.relative_path(skills.skills_root, runtime_workspace),
                "skill_count": skills.count(),
                "skill_warnings": skills.warnings,
                "local_only_config": local_config.__dict__,
                "plugin_count": len(plugin_registry.plugins),
            },
            ensure_ascii=True,
            indent=2,
        )

    @server.resource("memory://current")
    def memory_resource() -> str:
        if ump_store is not None:
            return ump_store.refresh_projection()
        if not current_memory_path.exists():
            return ""
        return current_memory_path.read_text(encoding="utf-8", errors="ignore")

    @server.tool(description="Read scoped local UMP memory for the active workspace and global identity context.")
    def read_memory(
        query: str = "",
        kind: str = "",
        limit: int = 8,
        max_chars: int = 1200,
    ) -> dict[str, Any]:
        if ump_store is None:
            return {"enabled": False, "summary": "", "items": []}
        kinds = [kind] if kind.strip() else None
        items = ump_store.search(query=query, kinds=kinds, limit=limit, project_only=True)
        return {
            "enabled": True,
            "query": query,
            "count": len(items),
            "items": items,
            "summary": ump_store.render_summary(query=query, kinds=kinds, limit=limit, max_chars=max_chars),
        }

    @server.tool(description="Search scoped local UMP memory records for the active workspace and global identity context.")
    def search_memory(query: str, kind: str = "", limit: int = 8) -> dict[str, Any]:
        if ump_store is None:
            return {"enabled": False, "query": query, "count": 0, "items": []}
        kinds = [kind] if kind.strip() else None
        items = ump_store.search(query=query, kinds=kinds, limit=limit, project_only=True)
        return {"enabled": True, "query": query, "count": len(items), "items": items}

    @server.tool(description="Store a scoped local UMP memory record for the active workspace or as a global identity note.")
    def remember_memory(
        kind: str,
        content: str,
        title: str = "",
        tags: list[str] | None = None,
        project_only: bool = True,
    ) -> dict[str, Any]:
        if ump_store is None:
            raise RuntimeError("Local UMP memory support is unavailable in this runtime.")
        record = ump_store.remember(kind=kind, content=content, title=title, tags=tags, project_only=project_only)
        return {"record": record, "record_count": ump_store.count()}

    @server.tool(description="Revise an existing scoped local UMP memory record by id.")
    def revise_memory(
        record_id: str,
        content: str = "",
        title: str = "",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if ump_store is None:
            raise RuntimeError("Local UMP memory support is unavailable in this runtime.")
        record = ump_store.revise(
            record_id,
            content=content if content.strip() else None,
            title=title if title.strip() else None,
            tags=tags,
        )
        return {"record": record}

    @server.tool(description="Forget a scoped local UMP memory record by id.")
    def forget_memory(record_id: str, reason: str = "") -> dict[str, Any]:
        if ump_store is None:
            raise RuntimeError("Local UMP memory support is unavailable in this runtime.")
        record = ump_store.forget(record_id, reason=reason)
        return {"record": record}

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
    probe_command = (
        [command, "/c", "exit", "0"]
        if Path(command).name.lower() == "cmd.exe"
        else [command, "-NoProfile", "-Command", "exit 0"]
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
    in_wsl_fn = getattr(base, "in_wsl", None)
    powershell = shutil.which("powershell.exe") if callable(in_wsl_fn) and in_wsl_fn() else (shutil.which("powershell.exe") or shutil.which("powershell"))
    if powershell:
        script = textwrap.dedent(
            f"""
            $ErrorActionPreference = 'SilentlyContinue'
            $connections = Get-NetTCPConnection -LocalPort {port} -State Listen
            $pids = @()
            if ($connections) {{
                $pids = @($connections | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object {{ $_ -gt 0 }})
            }}
            foreach ($processId in $pids) {{
                Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
            }}
            $pids | ConvertTo-Json -Compress
            """
        ).strip()
        powershell_command = [powershell, "-NoProfile", "-Command", script]
        if callable(in_wsl_fn) and in_wsl_fn() and str(powershell).lower().endswith(".exe"):
            powershell_command = base.wrap_windows_command_for_wsl(powershell_command)
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
            try:
                os.kill(pid, 15)
                terminated.add(pid)
            except OSError:
                continue
    return sorted(terminated)


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
            managed_process = self.server_process.process if self.server_process is not None else None
            _terminate_llamacpp_listener_processes(base, self.base_url, managed_process=managed_process)
            if self.server_process is not None:
                self.server_process.process = None
            self.server_process = base.LlamaCppServerProcess(self.config)
            self.server_process.ensure_started()
            self.transports = []
            self.transport = base.DirectHTTPTransport(self.base_url, "direct")
            return self._wait_for_model_state(timeout_seconds)

    def _restart_with_requested_model(self: Any, timeout_seconds: float) -> None:
        managed_process = self.server_process.process if self.server_process is not None else None
        _terminate_llamacpp_listener_processes(base, self.base_url, managed_process=managed_process)
        if self.server_process is not None:
            self.server_process.process = None
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


_EMBEDDED_BASE_MODULE_LABEL = 'AI_Agent_Qubitz_Gemma_4_31B_It_Qat_Embd_Base'
_EMBEDDED_BASE_SOURCE = gzip.decompress(base64.b85decode(
    """\
ABzY8;Myu`0{`s2YhxS7ktq6|zhW|pmlzp<NJ?>h5Yt(jgk&C*6vKxdABsC?2n@&x@d{=jilOk|Z&mgCH2^3%o4xmXH{xKXySlr&y1Kf$9)sCr6s754Ud?BDnnuNFI+;~bHXcu^tSTnsa$`fho83;cS((e<<>R<lOvKNdtjxc8
Eq>kh#M50?-VKWz@$>KHWGtSClU_EI@Ft~r`o}z<%hxmc1aR_?>c_k)hVs|^W;&bn@>0SmALV0}%?5>Pr^-K853_74f8WjWtY3_8<%?pZ3eU1$ev|d?#fyIyQvx<X#Pud)ga*SCh}%P?bUzl=d4-jzR6m2~VOExTDe+tOi(b`K
pPJF2$cOzV40k$&CS}bWdn2le-|Xu&tM0U>PvIx~P(4mzII>IQ$7Zyj4TssyFmFbORX$@+=Q$vH3~jMmqux|h*&i19xH<=P0tG<9s$Wdb^Vx@dcACwyQ4S!W$IuLnC@ZmMN<So^c8?&mOROoN(vOsVQ6)=)+a6?PrS;$q{5;-2
-PqVT=)T#zJi17a-yL+1qTMK-kIUhtx_fz>k49PRb?epk53QnV{UfVdZ{J?NiDiu+-=z2=J?oynOAilLs{7a1tPZu7zBxSVp6nfWv8t!a<v|O||CIjisC|1oAE=f!5J!7Iux?+nrdw|{IEPj1uX`67950CH_TuPh?|5(j^fdkP
-T4Jp-ro6Bdkg+=+f(NB-5Hkoc57?f{rJP)c{ja0!*LAayQ-RYUcP+tHnZqFc|k8cO`dg+x(Imh^pFE5*d+kZrbYX<sP5)B?cQYc5*Edze0h628n!00+m}GfqpaPVPT|8a2fkCj9Ky;dD^DW~g=gKpgJ08&!{hF|%Zv29yZ`Rw
;2au!wFUU_AY<=r@92o;ZF@tTiPOD{AJgv1&#9P>zh3@uc=3Nyt4O>dA)R;6e(s(HD^fYVkXscn6Re5F;Ja37|Hr+H^rCxwdbD@Zt*vL23_?KKTOAA)k2iifKT-uw_AU;8hDko#{}Jnd`;FGt+2sk&2~5vlFAvYU2UH*DrLhsg
e=$vTi*tzlV;(ozd!mHiyq~rE!=e7t{@ub|Nq@;m$9~ZQA{$M{`upDe2Ke$fuhZ=v_w`Zb`Owd1#_L4VRrmM@7@K5Zy-7bWv)lZocxVk_Z_QgDb~N6#cXrY}KbL6dzuVPEjmWzv`)9wNUI6S+abWN9hWPkX_gCLkYM)_h-tF%l
>91-^w`u=oUE04qJL{eRm+l_FJ5!a|&-SPvZy*66A@CBXXWci4e^X^r=;z`21wLI|o}C~Oe6w-9_qX)o-Mgdo{Gxk`ueRvb-of4}5tZ?6=QVw#pT2R*kFkP#);&i=0ui54i(6Z)g#)0*{R?6nk|tj}UqrR-9jqs2<>c}>-M_%0
?R>Md{ra`>0fv2WxR0E6`<qwas&8@`b<g%-{MhfU_MfcJ?|?I5O^s#IhvUN&`T)F4F>l!Qy7283wvMBVUwJhgfk5;5<?-<z{B;I@b_oM^KJUCXKLZvH-<_m~&}sbqNBYL+64>iS_iq;lg7ohk?dK<lr>9+2?A6Ye4+<+yec1Tx
Wf#62o}6EtT~Y@Wvv^y4$j4C@(PmLpli4Fke&u~MnV~>gRwT;I@@iJ(AMyy-9*DX92;|1U=e;UIsjdWJFawb_A5NokGK6A9)sA5$({vv~cYwr!O<#1+u;E#byVDdZvM$Wx_csj`CO=K4`S??LH+lH<p~xRT72|RWZG6f<W}{-9
f12g=x_6iN?mzX55=6eoPt#d3u0Hj$>eFyCxqn}NomI*E8_r*#sl&G??|}AT$0XjOQ|R7#pj)%}zuuQGynS+-rN+#L%x3n0l72luJYR-^at<=etoUdCi9J2SxS^?H{3#n}!^g5H*~3-&&+GgX*3V}7r$I69e=75A*1P+33v>7h
xl1K+B~Zw4R^`JXU}%(odVmRoe;z-<z-rJ1tL|dO3C#ON^=UNe7lTJ=EgRqFpXO7PyrGL9p*w@D2gC&r53>SZg1|b=M>*mU&<roKejh%LCLi)oeGpG*2xAbMKY_g7yQ}FV?DmImeqFJbDDHfMZ4rC<AsZIdLH+=g{%MG_I-3s$
pQevgwj53FbHEw>nB{<ClJ0BzLwo<lyPvuzwVnCDjjR9qzt>+U|M%1T7#`op@ccghv<c6f@VNP@@t5y@_x>SiT<x{~PuBY9b%#G}wf<KcwcoeizkhlCb>lCc_igP1)cmCVtSOc>{(|6fnf`Kka`5h#^Yq{hcLx%>Ld!yR2I0o4
?*8S$o)k<%wVBk=e$l!q#;phVA8!Ax#`X^N?|uL4<S$9<<2SG0x8c0_2MhYy#Zvf8nrN|RR&9fr<@x0s*jH)8dSZOBeUnu}N@@4h)8JM;Pp9g+EVbtk^7&3ZX{GO~kraX2#ki2qf6qQ->PM+PPR8;F$OYnYT5hZ7NBKCrQI85p
sX9~sL$zN`w9Zte_V`gfe^ki$s1<+A^dI!R*y#Rk|LF1n<SSr<I!1dLupwC*4k~Iyu<&PE_hA91W@*@@J{Uh<ZSRQRX*zwR(rGGQp@u*_15?U}a#&;dmBOnz>|17U&$<_9huxp|)X`DvJY#9Y$*qnG+WgXCQC9lfVKyH_XJ%<W
n|a?)=Ayi}b&VDqv0rd(BHII54G<jO!P<WTl1Eg1g8#vC?_9ysZ>KF-{D1j_yJwyMba-?`(qDM>GxkCJq$PhOFK`*b$?x4SFpm9qAONvr;*&OAFJU>p1fF`Q=vX5FZF)Ww{Q?dzBe^Cfw4BAS>4PMGMemGPYrnKs*soXO=S(hN
Br@?_<`Qa^6_TRoA83v)57QssqDcJv?bq;sa1?_#r+X*tT=M(vo9*8AK)p;sa-<JCuXg^plhFrcGW2dh|H*gh@g*EY=u7`k@PGe{zWng+?4WzbVDw*Yy<#n%!@+-_L3!1Cz4iLrcw=LupAVw(<RLAqY&2~oQS1AttY#g)HC6N3
SgOC;P_B`*;qw50HsY^-ZGAOrebtY?`myuXap$XZm_`P%pQE6cD*YhR>~vl=a9wqx8^m|?30s1HPbTBsY`7SZiY_Wj`iQb*QGzH^W#e8>Fq&96VWrKkW>`9o=?k30Ca7X8VbE9cyc)E=1-fMqt+KeqAHC@|U^%{~3R93XVAbV)
sKC^u?c2PPZw0)@n`~G@pRpwdRdibc7*DFG7^^O*zRFkaYzo3*zac9nRAvBRMVSDIYWAppSmSRq<TpgML^b@emrtwca$I1611i>?%_cJw7OGc^@jTxM0G5c%v2M8=bLFX=eORvPU^vOD1`1#uoT?^}#2}jwt4>5;Xuj!*lUnuw
D4&#YbpBAxCSyc9)rJ|0iRKERPJv~_E!1$QpS7{!poo8Gor0PS6355S*>G;-!2+np1@fJDRPdh01b_dPBf{={)_9;F-tpk_GMT~Z<Kc~A4IpK+%@inooL4E=%rlNt%|V#ILS6RN-uUsF>A0`DG#n6?udh2U0@zK!>~UI6ZfDu_
?y=1vOI0@gYDR}+63;I{3?J)QI;3KRCRC=wTMb)TQRdOve2jdKiZqNB5_iYcGA#4E44Ns+sGLvvy~#i3#f*-$d9<nbZ!?-^z5DDoZ%45S?GQQJa472Hsiwqj8W%r;fs+p$vLlXo{M=R$5{3!rzm9wF==lurE~oeTBM3?3J{@Ap
#=~TGUqTmw6!1=iGiI1`$)8bBxngrg#6oObOpb!o2^B;xe9am2htx|@KBbv=!Egv_&Q$w?tp<Scnr-5blA9%o1T7JP`wuQmM1X1t)f6Cv;WZHSUlo2C`L%emB7oNFw)2mGC0GC^^W`NNa`fV;Oi1zCOEUIdhY=UI<H;<ykmWXS
;H?IK{_!+5FaacTB2*v+>4_tAUnj!PCOtvyDb#TI3zeXDSD?#cte1o`+P5;#$G{i!>WWo^FYu$G_Uv*ppY?zp!jN#~E?sBZ%){L9TGUE-I~E;jXZ?Nysw82NfMLZf#Cf0OtPbbXkRZrl5I?bzEjrN?GUf)<NZM(N%s)->ExcI7
Nqw&km<<hAYT+^5+j|zh6-C4Z)v@Y9d@^CRI1VI}MjSuDkO!0bxZjR0W<?Hj{j~Y#XtVwINilA4@QD~r-9`X-@iCYMat+SO`_aQ)K8~L1Zw{yi$bcm>c5QDM#OJu3$!$R6|G2M$WDRGXJbLmk(}k#}IMi%15j%$v)pQyqvR+{=
QaR&<uykD~S6kOt+t*U=pnXx6C9X!QN_^sy$Xfka&H;>lu~9SZBx&>hsV+Hejt}HEALla|wmxd>47Fy@+7cvzvP5p}cx51v4WU;|@A4T#A-!1ziB3m#i6PkM0UUB+ve_ywz3kG*5toul%?U?N1~j3uwl>FwHu}VOGE#GCqtxP!
`uNQrn1dFqjQy-(e)T-$4FQd+iI~)-+`?oL#KSnZLo~DQG|B||T2t^fv+Hzl_3|!@JEUk<ljcI$m9vHtXDTsjvy5hBu4SS#B||B29uca3?4J5<x(?tgpq{a4%R#PSI1u*D;?`iyMe@0Qmwl<LUsH?IzX@h7(U6oBW_2NmM)?p3
6(>pw*#@dziPc%9ss`_>pH*3aX#ng?qR&!LZt2*Pt%V}5;2!L>_-|cuZ(7YJ4%GU7OV$Rg)%jraEo#B!Y{*zPRzd>1ay38yZr7SEvvXc7eOU}G$Bu)UdaUrT=CC%_AdxV5#>L+N8$Fp+Z%}@8Sov{LmZ-85(k5#6+b}x3Km{JX
R0;&9{N&>SB*{HVoZ3v9F+gWdyo?MVI-kLbxTY$=Ky0bJ23rhc`(*GMu^Zgw2)fC8*}TkySSq6tp8_w!7}9>@Eoj5ygn_+iea7wZMMHWtd&DWDLK!B-L#nwK;~5r(HA9BSVza!B_5j&%*qFsv;tu#_r*-``^oXRV;p8EoH4?dj
mUj~ny2cGnavv6>f@ws#4T_`OAMy%G^xZBmZZAQ#@YL_C4l5kBqF2}L8CP;dUq^Af-4>S_(hMTS3xnCbhnyvaSNHwN!<fyF>MZ>nWgpWX%u^{k9WcjHrD48DWr|9WGN%SSHFZm!=7xg<Bz(dsABKYgeuu#u@MzVf(5-7Q?wC<E
-tzEE3GAvPq0=Ws*I+m=@5Cq?mZ*mRjVEKe2@#N+45F#kVr}9z4Yg)0b1?vU_BR0~iKbbz`Y!vgbsEnVv+N-q7O<@qV~)e=u&D5j!G);Et}N|n#rQ1Xw<RhUq*(?bn^omQQQbA--^Z5f&O)W7))PY`I78MIgyGfIxg9_($Rh_|
a|o&#?d)uE9NHbAm+a!rdqnHaXR~}<G1@0HKY4ItFj6^H;su)x(o=D=lwG_5GP(D*!!c+9HIYZPb(ND@c+t^LY6DJaT0dCFt~%bud2KcY{B#88rGL^b9~QQSNhFGfDy7eQBW~fP;fpw-LliK~5t<j#eR==u_igy7Dkh05(HM;n
;#Tye-YnuUN;Iej9$@}vbhu{dH;>HB#*UgY=lW}6!BiLd=e8qG%bMS7+%Y7h#_z{5J7+RtlQfMe(l>~v0%p=Xt0%_F`eiN%i&SL?H$J-`_O`)nNz{%J+Sx*em(S+(v%Ej=!46J*ET~iY^1KhRBOpG-zmNVpzbUGJM#tQyCW?Rm
d)N~yG<Turv%H5Y$P4It886o7oQ?RB4Ey>W1o(>-Q)7F*tj30+M>Z!mFe#`Ce`qJlp$}MVHBcr4-u`wg(E*qF+8}mbZzbkkut5t!f?<*y2c^j0sD0Ji-sxN?oO%Vl50IZ!Txk7(H^z*vKe0>lkXZ(m6m}VIzvkonF{~Lbxxi{d
CP$a<RFMsFJ}xR|k?{i(z)uDJIc9&}vOgE_N6|diK<cEL#;pmp@m;jNwez~Y6^Wv|yR0Y-{n~3Mfg23e?6h|Vi|7f<YP^?VzTD+NH$v60i$V`v+@#gSM5Z!xP$mKD&K~$rW2?Q@WC%^77u4E|7q6rZMOhUi)B}?cUs@8p4HCg^
H=~9UtiO(4CDF^5(Q5&)C}BGsmjzxW(;<QGZyNZd9T0COlOcnQUy;$`IR&4E<S(g|zJsWS_qQ>ko=WMf3BR;sm)~8?Oyd%2qnr;0#YfQ!<3;UhK0<0}-{-`L+EX;z!@q-%@j_tgBYF!~DIT}|*pi%-n-Nf%AcLh19ou&eap`mf
62puk*>m{}pvHKfYuwHz^C`h&PsNx4ZqrZHwgC?5IidS)FDUse*f;+hH<GLCr^R>Qx0{_m#|~4M)t0kUy#<Xyb7wQeweSsF&Yc-v1%s22jdLn1qev1>)QetGrP)n68O|$Cnsg>s!;mwnk*6+4Ie5}psVeCZBAIcCvSBM`F5_t;
*iw?l1*KPL!$6^v^Ad@>4FebBZKyk)vNtPkkc210G-EQnW|@=CZs%kf(uvTCl^p@mTE>+(#L6V~0xpYK^_MF=Bs8+Ri195>_Fpt&B7#V{9ATAL-=NmEw)u_wfg0iqlt+z#W0@77@e|S$_GLj^a~VCUh8C?S0b>z6LuD!$YpfFX
FYuLMo1NOkrtp&FFPn)6CO2y%V)Q{hCTE59;WGCGB6J_v1N^)`)KPYyr~P7<qnpH|5AZ&!*$}WSwQduW58-YfPd@lM&90mTOe9siL?5@hF}Y8Kn`Ry=!KpZ>&)7ccc9HGc&HQ7Y&mOUX&E$78PYbvk5CN6G29Ao7P{-Vr=Hq@o
3nH)W9YMj2yi#UQoN`;R*pOzdHKW^uG1^W7(hRpC!ycXzd|)TYh4XQ*fc}z&nCkA;rUQBNS`&b3!18XFp24#ZfPkxO?`oREpxLP{MZZtrhCW1}&BtSGj~8*;;qU94K^daF%K_Ufv~-H4&#|;@6on7F-G_d7Zi&%y9|Tt@7MyJg
odPn#vlA~HBJ&U^ozPjCl}TN30Y0I)%|uVw^1@Y}(jGL+%lXi-cAaLAcwxG92jb-z72__Cx@?ai2=X*_fM2b5fZy}8j1FOw;ysMnWeg6Bx=t`R6LxmugJk;NIkVq3I-$O#99m3Tfv3evyC(0ieSdKT@bP9xL3i1xW$ZbK<<4f-
Q9GTe*>cK=4bbG1>M~%R9dUv;E_EGWe?#QQ`X%^45z{TgVG|WYQJB(5fAzdw)YlhQum9N_>9TRIzkM<@5Ko7ny+4xNn&Vm90vhu#Y!PT`T8q&zOS$L?&y~CiOpK_!+8&D9yu#ZF{w3TA+;wx_JTDB*i)!m{vG&F$8Gin;z=nu4
$wk>ipI9};k2MQqwLNbCH)wUGEl=C(KBH|*?PTy-omGRVuNZtWE}(n;m@{l~0vx9g<*;F$?F`d{54dZ>Y;t=NI#|Uo(XHqu{}d-{U8L$x0PoKIeiG_nNcsrcPxOu$$`G;7kBZ)GQcea{i~@wHh(1CvM*SThJm6|j++qMIK{J|#
r}z3^DczX6A{umBx7Wf;l3fN4(!0rsX%b|4D04Tzy#*W%&}oNV5=krktN6z^>5uP@yYV$!u?-F+aSL9)eS3KF_RZdYH~sPQhjf2$|3{}9YwjjayzM}(gTwQ^A5hWKJ?b8JFV21?2=TV0bIU_O#FcpW=FQRJNf(&ME)`dE=xIb0
)<N4vFV6N(&fmN{JMNyHdn+g$wnz7{K{U{T9`>ePJp4B!vVuq__w+N-@xMxOVHsN@J@6m_20d5=z?B<f0ofEBLLOlI^l0zb*k3_MhbT!H=xoIf(N_0M`UeeW6;ML?ST>^=&LIEH$FLrm@0@mTn3cJ(!=Tk|)=Q7Oho^hz=P_51
oBDO9GE{1#KV9(=SDp!q6{e5S4=kB~%zN_+<8mdA7OkOR0}WTbVVo#aIb`2;z)5*--EjK4s?~B=$i@CqdUW{1*&bgMxU8H`@xR};zwxZGi=S2maL?*<u1u~`IKkY{S<qJ<d2Vo>V1POpQSj()_h<w|ZdcX)hIHl<uX!nyBl<-)
oZL3yuv22ho}0->m^a|EL-@ol@+yE}C>ZbQon1@^pa2UCs&dHHj+6AE822X+rL7lNo~kP?KV?NZMXjpPqZ>*<I9_3_6L`wX6l14+MEx#a=E43JC%UZ+0ZF3oqE}@2kH4<AuhD`p9*sSsxNN;YL&vdQUS;Q62TI=(1hP%qdl(MA
Y8PcToZe-1qnhQTtRN)_bm9Gbe(l6sdDXd2tV<IHcRrosz!#nO@1G=qMU4GX@W=+$S4BB1bERr3w>>4TH%gH1xj~N587rGNLuV{YTDtYkcr#JnCR{wh<^DYVu$Te$@oMYZ5Fc!OY9)6%*KK|ZY9N|{%>ZRc>yekSqetc9(TL8s
FL~=PHTW*FeN|RgW%Egy7QK7#e29B{$3$E$!VU|S$aY8i;c-*YycaYr1J>{xa=PvVRJhGT%A`df4uM&DUs`rIywtE5RQ@l6NpD`Jg9+N$uWFtwjaW5`(iHMiaz~=1@S5$1y8`GbR-Y&eo3TTwZ<e)H4@iUQ5$KN9vDt|C`u*0$
<1~+2du5rAZibI1c#=4OgtOMD{R`uN?Kd#tBB;U|)emMFaL`*{#Xn3Q#zf#J`9tg74Ml(QHA?@60kqFiQF4m0rqF>6h8P55l)UbAj<fMR8`gk6!q`U)s@q9EwD#!)FK}D0F#1?CVt<pm{<3TEFhDGTx3}B1Q0^uVhvN&N*_#ZJ
_=f?CQ8g7VCe!0diN}LKz`?Gr<U3fGMfHdVV7^jUA0|crTI)umuAh78tDNDzmbVaF@+%R+5IEEiGkBBF4yaZQ!1h7T2%k;f<~cEU%l%DqrOi4>I6~+gZ+~7@1ks1XYs+gA$SfNQ0YX|9dt?iTDLRh2K47l-6^uEXGAlMud9`zF
;>NML8N2=oI=4{9cM=tZ#k_yYhO+s@8(i3+3@{*XAsDBvE3fZE{0F9HMjUH1z-GL`h{{JwI?e45C#9d@^il^Pwbqscyf;K|TKPW$jD_{=jEVtIdG}KiJw;dhlQBqhIlh=zZM<*H8$L89(ZbWbNlCUTC`LMCMtmxsN_#dRdr+))
1(@;H$>el4Aq_D8I=|SXzg+u-&wq}%<fczxa^3VehfgU|HM6T@Cs^0724+6+iNeS2(#fw`GuD!03KbS&*ci+h10Drj+EqCwu8=Nk7g!m|Yyx1=ba54F5@jAKtZ(RRU0tLq14{`<6YC9Gl7ucii3QjIqnh;oA)DPgm;D$ODdx>X
>v6~J3(V_i4up$GRrrn*vRFlAGTOb0Sy4JRvd0Z;hLi*cBfVEhd-1}6WY}W3^WmN>o;fc}5S``2Oj<Z;hd9R-*1WUwg;wD_R2<SI<Z7aHbLzW(id!)li`9TupC7~FFsu!q^y;p!CA2e~_wzoYr|d}I4jP{bI(~&kx%Cs-kWMBT
1-+P|a<*i`I6b10a#FS*vf;h%#8pkEXbQ_zRXW8m09PF{q2fQO1@R2<L@9Y@MynsyXJ&@R3t7WSv^xRlvY|b}7p5t+7zH*eM`W)2M~wEa!FnkRO8TMc(9lkVL_loRn_*8hf07sMPd0>OUdmmPI;7u*&MOAVzT<WYV;{-_oJ-c7
U45&=G?_{`S!d&NNDkMAB;j!e{~vhd4UA4*5`39cFhybswFP8Xe?Dfcz%p{H0jN7|GHY+kT8!b2sI<=vycG{2pIxUh8ec=dB*2JK)|RFS$-Y#u_dd3YP2weLVRp-`XV_^k$0YEMr=a4JAbt{YGEuYC7u}Pj?nUhBi}3w(y0W@^
qTgmw0IE?yh3pmO2D6&EqOth2Dq={_TaSL$(f2Gf?+)Bo)|!G+F*64et{A`fvP#|OY2@mmDtqHx#S~LOTyBUdsX#YO#$^t}CA^|4RDz4X-;Mr6uf?mYKVB<^sQKlaYr1-RDKwMf7LQ*)heK;AqhxJg2HAW5QLBVIs(G<TAx!b<
rWn&-9;lLSl45R52ogOmC=7s#Kh8<^FExl-%>Pi;SI@1wydE3QWY*{RuAYH??P9A~kwCfp?bQF;(~du`8O@n5ER^_QMuk%L+$dYIQR!66h$;1(eTzkce4<ye1C*f#^weK7h?EN<sfnrP=a5dIFB#D?H5WzDDc#WRI%r#AWtqXW
6bAiK*6e;;dz^kKG37$qYP4Z-V+&U-=N~#U-H(#WEq4TAw9hMqcSdP9(qnG(H?XyjrqyGcGj7GPMYqV^w1l0B{S?xOxbv))Bv}=Z+(N+N697=qWY7==AEwR@>+3LV7qIU{#%a{(dq7s#&Mm5V(-ac8D#DUtw43{|M&I8TU4-_P
@SeCOF%elKzkc#i<nxw;e-$jeyu+L^t5+f4S(6M<pW%V$4aF3gagQV@2%ck)_Y5UL+4e%-S}RhfEAIJhC|t2ZVTKLCh!tkRgOfG=NP?N0MEw{YXhcY0A0W#uSR{LLe1n6soOM1b2Krav+(PpCupKWK4X-*;3*{nK{JK^w^eDL5
Iy8{)QMoXCE+ibyH*gi<&+@&}0z@d0mRy%Wu9Z-^18!IbWNvd({-bykj_gNorQGKpc||RLZ|yzoQkg&L$6T|so~v{F%G?f&A$DD51c*#-n5X}kPvFp~X2KiaJKl^z{GmdgI|B@_u_-OYk;>8`GiVq5N5rLX$UV<#iRw*-ViyY;
D-7rbIdCbn%CHm{|JC?QhszC^djZ%=@|RE3$K-wab>lCm-vPg=zHfh>{6P~qS*7=FN-uHsU+>H77Z}B!3!L<pK_qaWK$6zpB_YU4n&UksF3dY^ncK3U_l#0`k)|AfGa3xD+j7?mov$%vs^}F~H1|Pt4Jj*s)Me3><sGTh1|j45
vqhR5wsfhOq1quNG5agPk#mO)M5W%)uZQwZet~D5J5@XeA%_Qqs2S6v6$?Vr7d2H#d>7(ccdux*Q^^Qc;m~)n@NieaW{Bc|dvm~zI8z0f9vlUi-BDS^^f(_+rtFf&aXoiOKo8V^vtDo<LGgdZ&Ivhky{@TRzg@@H?b2s;=4O^>
_x8;b0%mQ{wz0!5oLHk<v_<eAt?g^;8#y;4huw@f7oCk|L)h*B2p>ye-)mMsNPv@Z(X%Cd&H?ZR@^N{wA`UO?`!Y3_oEL@BjNSgTh)WhT)Oj>!#{xdfi|5}<hW%&Y*Ys#HZDCgM9y8WRwk_%-<9_jH4%D|TsD7(=9!{)tdvwRM
Mx#mYykVhm4j!Xy5Q2XT0CA#k`laMk!96wvkZlNw+e2Nk;LdpTlIgFF0VTF_*u;kh(})Z}QjsV_nT71Q(y$o<FRy6#@BrhtlcrM0HI0x^0*8C^ZoqGY!9-LIRuZ})9>o6<J%uDI)l9O8o^TGhaK@)?l|{gcE?)xma*Ia#FTMcj
>;2a>E5;0lsVTS|bg+VQxfU}?LS(mm#`((eLH7tsl14E7XSFJQ*gdi0Rj^1HK(Fe4KS*6GQ5JPhQI!mLx8d@C5eG7x%I@<m7%dg)N?~<%l;K2#=DpHJ1oMEmS?|!&H{k;Dj1IMiiivmcDyFPWa2%)L0<B>W&F*#h*HXQP2b)mu
Q>s%p2Iok#W~WUhALesTDeKqL+h1Yt-scyqO4Y!XLQONTP%7@5S<&67Ch@d(2W+TECBqaxQ4OmbsKl@wKSJ4*lYqj*Y{L`iB<1m8XuX@I6WSHL>R49mce_4>xUp?@Wst+3Q9C3?9pUgInz0CNUr;sr<TkNrMz@nnQ?gNaemBzt
gqlW4<|UHXD!{qwFb$kc)R^CVDoIZPLp|BFTz>V8Re{?cp1%$rU6lB*YE~^LoXyNuM7no+4yAmrX+_+tkVh+C7ne)NwsQA81<gj+xXh9LU-RlgUWSIQA3Qo*p*wVMcD$E4L1{+bW$UIu46s6X$99DxpEv(-oHLVyrV`ym`(&@v
p4|>7Hw|~<?bt$Oss<bKU1k~!DNfUjj4Y8J5zu>@`!~YAd&z`0Ruqt)r_9neqK4OjV#STbG9`MBPo*b`LA*DwCLn7SJ;r`T?@?RdFG^JZ_hratvw_`hacAYG^wNA&)4TbN@CHLy%K1$y8u?<Gyh4L8^LSaElBv9`3|%wUPB=Rk
Mbbq=4tVW8gOQqWSM^L4<D`{f;Z{4O<kLx7^mk>v?Kh0P&bp`X(!&GoK*5P|*WE8<*LLMo(~VzEVub3HYuHIiJbTk=I%YOS9M!}W>LZ==7DDJIz?h1+MK%F(pgk7H$k!YuLWR?M2|Ijt>vKC>(t&c;!@mQqCr=v0MW{zN^J3Uv
ef%pSFd_321RTUeKKp>FY2_8)^IG?GKG98ESPEv=SUm#J(%8KB{eoC%quWV6CVs2-bqwg;;6Au`EHdzbL2>++M#_R*GdEIlDWII&4*Seo#9cGm?qE@eaT!)g8)p1qS<53lxE)aGP|eDTu2JEIbsuW&x>>U-(~Q=YK10;z`?&`R
tMt!+m8e+i;+TL_p~5El4{<_N5<Obd8#^+jsP-~4&RQ$VpevZbu>Q?S1z>n3lDOt|JEh`@X^qN2sKhdi_9(;-SDhM2i|LcFXDP;i&-}&cle*m10BR|NwfauLS~FCP3Mj4UT-n#z(y&=KfMTbCdUDm3z|0$}&e!LPsh9K?Fp6l4
f~8K2DgNP~DaA1%6&@JcuI&@*65pPHUk~yvsI$BM?benB(dX@Bo2|hpb(fvr?M7QZ6+l>YBk@h8&niSn9~|sU8YwL>6wiJ+KZ+Q$jN~R85!>uo;kod?uVrW(-p#V=!*PYhdlxTY<P^zrD+#C$RwVM@n$UfMyFwa@WWZ0>!&#IK
C`<!GINe3GP21ro**qU>N9(yF2|l`xHURd~4((fIGt|xECW()_TaOkU3%`4V<rD+Em}k*)CN~>ZvpjED<+;#o0a;PjrWi}mZ^{QWnd0p;A4HRMR=e@6bNf#;MJXeS?)O2r5o0gf92J<F#p#>K>swXRk7;jMP{56j9&v*{8F4b`
O?#ge-f-6R4hE;zHUd&a!HJzT{iVeXFh@f^D|er)qg4-&XE41nY@EY_xmLgYFz&Z+0VDI9um4^GcQsi@d<hayYws3a&!FPoA$xAInDkoVMbk)vG{~#oU5Ym`fPE=saWT4Ki9zT{_gfu>)mq`A-Sf`HL^IZmB7LxD!y0iq8JA==
OrK~Fjy>90_o#~|X?v%KXqJ{Z_1iQmQn<sauI74R1Rmgv@g`OpXU?>aD@x)A<6bTTf;9=_Nj>5&$GOOl^|q*fg!4Y1GubNgBZ*Xt&+szB{3)Bk5PI`|CMG`RF}oUi>@Fpgv0OUE7&v*m-8R!9(Ko!p@mw1ik-&JwmbS_Y%DfXD
Vx*2?5>4Pcyno(6nygpmho)7K_mx4Ti6MB5g4x?RG(GGyOCPjkc8+KWI!*J*0g;i5LZq9Eva#q3LKU-w_G)~@`be#zpxNt!=cDOk49qoZ3`pfu<}@w5o+GYVLwD%Xt1RT#8z^W|Erwcr8;d&=&F-sESG~e~asTpQFFm_Fxi~!T
s`&Bq;gEak7-BRTen}v~?1dUauT*({)w!J@V(4rA&^VfX9X`8Qr^^PQV5f~)z0&IhI-Y$1`2^XdSiTfHl4+4q9j{>_iT;B$^hO&AC7j(2vjfw4%xUepYhsc!6aYt?!0(J%?4e5<k01~silglvPO{SsWC%`1!lPMq0ukbL>`Isc
NsB;54mJQ_87AVL0=Ck5J{zKtMY;@l<{^JIVZ$q1Y2W#gdo8hOg)NSSsAQWPbSck9jreW%0?sqgOR6EZCFi1L>DDIU>%$-sEq=IR64@ObkcS>KK)UC+n~(32^~ko!XPaj-_1mZ6y1{DxuOv=Ui!7^PgUTkBJp9jMD&%2&{GTWf
L%&HL{y)VO{V?S?K357>rYF1(U1>FVMRtqiRnYNh4CK<HmE8!Lg97kje@6DHoHua@L<5Kj%^HhxlV~-r=$X?HKB4+Yd?pG(<zvBOvo|z1cyyS54jk%_k&Vsl5(-%u6HWuqLc{s}rr1a~tn6Hv?pFhFwRLUkEQOART?BCLr4rT|
n_Z&9G-UTc2tE(jK4B+WJk)BZS-k9oxt`+YH5m^d70<=fZPBL+?bzctwT4=p)t1U?PvwvzJ8@mAe9p}FnVZg0stTN=wE9xxWSzUP4|wGASRusPZ3L81RxPT4(qcn_)pXpoJt1rWDEaIwz4)R>I6sAuzC+#x0*zpD6LuFQcg`jh
tAZyvO*@agWi{=+vN+&v_n9^QZ4XVT;@hvkCUxEi{<ElJ)Bn-X*a{Gds~rj;1GBO4W)U9@3lw!<<=E|@Wg08Akf$;x3GFUBj_ak7s-y#%c8Y=5sUdFb$^2plIyUYy%Ow}9vYdJE7gJ;i*_RD&8hDdYL_QOE%{5&c>a=hF85>#u
7#Po@^-5h<q5zS3iD^sPy~*h1?d@pTn#^ur%1*rG+uX}pJ}6&8N6MGpZZ&PwN9>rbovlXGx|q@wDRwlqZ!61L&%Uu_{L-hqqh@78iC&TieZg&wvU}=jK1Bt!xsp95hUYSOH;#D|WbMiyX!=lIP2&K3t#d51bQ=bKg#a(_hkd()
=knONHkO=Tm0T*4siM3G>?>v&K!0&l<SEgSm;8|QdPYSKR$#grv4H6*kDspOH;tcr#_H$x8Y}cy^IZtrt*t6x!e#(1XuCphGJRC8W_PJdNpPj5yP7pLUVOzh*Vp`1qwPCRNq(*3mc;yb#LUp<GNwFly0sa6zG9!QS%0x}Wj<x5
t1Pptk>l0qJjw+sQL2i(+#B^*8kpA4xOE<z<I{*lFZM(1LxWxFtyU^NA>`<v58l2#J|cC)?z<E7%l`4+Pu=u~%fq9C^y1gk?(P{|BcA#vdl!d4cTETb34*jQduRJU9$s|!FD}oz=etj^l_`xVt4>80M&JoHq-P<q#VjXQK~am%
mp(4vP+QClTz_X+M(Ab#uK)Vh7!#eilq|&$xR@JZX|@F45!g7O4&tk)@#F!|)+5-R7uV4b*bu5c#y*jXujlt;O@U?rRCc|cC}vK7Y(W?#PJXF1Kz`u2dpZC)wY;)XT|!@S_iFUQy7zqO?*`0(VX5!J|IC-{8gZ9(-1!7Ev8#3D
{Nmu<<;8D)Gb@MpMK5O+a>qe2yUhqE!ZT6@H9POeb$7G+(O~uDAI<1>&7Cb1bnE9|64Af+-2Z83jk$+;XH#I!xrhs2fBsx%VyVc0;kS-0EG2?3O#q>L>ov~L^6YHRhIMS#e(7b5ld~@Cgc@4uXdJ8k8;WW|rB!xe+qQiXv&s-q
esc6jvh?{P1tiTH5mR^`$v~7MAf&nq<@XzB-M?NQo^=n%O8Q(T{m`3a##JioN3A;R*?O$?2PyD)lfwveU!?%1O^MnJreloTv_6z>m>fw~`xF$-f%!a5l?P5pPo%7czl_>y$>O6L=E$Ma)wQjU`fpN#sJkHZ;zwq>Smi!C`OWkx
&r&RSlnOM=e-=}ZW(JX`uyDohYI*c3j*)Oh9c5-!LG!%fRdqRbZ*Ev5`&2KC#|H`6XifEZo%YN@1Y1<VwQG8z`Pfx+g}w-xFzV%%{KeQuQ|+VOQHdcoji!=Nan--3)$Fr10=N@s8XDB4>RoZ#ac-Uj^#-n&Y+a1mCK)Ga#g!7P
<9A3lnK1*>_8I!}PeKLO@rBV~P-`rKJvt!fe7q4Th3XVd1=Wmv<;^@@9tb<$>Vm^E8^d+CHngKK0B`|zwe4wjU!fW+FOlhx-3mzcJxd%7bPDXI7!H*d-4<_AO~j6?&PcA@ZTWglNp`jU)!bWubFtRhRm~Tss4DDZ2^GuTQJO3G
XnQk!scNfvSW5x(ynFU@_iP0>YZZA@0<VkAt(7vPBdim!TLG~cfrwzH3iyI{+p)~F(rX0n4h;Sa2k!PWp_+Cz+_*8kop`IVaD9kTBQT;?NhJ=bcel_wW!&ClS${Hk{1Q$#B|<EC4*^RKAK`-mZ!QdcIg)DXdT_;W4L<KqUMAAD
U@h;gTgB5yCkGijH}=&v@Tw=sRWpt8ZWZIfB-F@KtBWKwW)|5+wQ!~HEZ2$S6C~^KVSCGeCQ4RqN=7gT@Hg=Gk<>-Zy2i@%sKS<WCv-c~3kY);-M?L==a+BZ9R96)9u7fn5I~t>q8gD2l2lgQPVB0)v-SGh>-F*y^A|Vq`;S{&
GPEdbL=D$Xv?1Nnf|)}+d!xdM888%sKZ)##`c|<#VyYSlnYU;iF)|=naqwY;W~Y}$f5(nDe#Tw+;PA|6(yHR+%T$KmxC6S0-E;89`;(0y8{TO_({elciD7e2RKxOsig-UV7zs;Ee4jryYLna;b5|dTzfFo%*vOe0)mUtRo4a9g
(=P9_oj-nKWWKn|KlY2;ysVI@x4*ge5F!AX75N8P+p8@;qA?Y}wtbV`$2C#^^=gPV86@JYdvSKy{drGCML1tdJvdvQPB4DJriVQTS`r*WIR?xMc6c21P!WW_WZcILWL|hxJx=qoy`K$-7k~(sV=SmQ<jxPi3{gt7M${uGiCKCX
;n7x<#9s`Ju}uG-@+9sg-E)68@ok@d((0+gLKmh?^M&;W2g8dR;ww-SB~iwrATm<k_{7eiNdvu-O<fC5T(bY5kV7>R-;(Mq&QpJmmzT+guL4VEPr+dL)q@)S`>~e$)>^Q3vn)q}<|czn9(_@?X*k~dTYB;C-4RgHIePHy|G0N{
?unORdcLr`05%e4Nk;!_k+KwZA!TWSzr21W8l;Z5nDP%NcR{iY8%L~y<~KBhbpvNtG_=5P?P5HgSLeX2Mj0N6o?`40`gu(#6C`=B&L-1*Ruy>(@1GVbV7b;OF@MH1Ke_jh0{UP+rcA%Sw2$&z#{+>mDWWp4!v<i6wlLMvDg&h`
t-WIBzd%)mD5$A^xTP27tA+pUuVG4r0fjUAIL&6ab4<ovHslkf+pV*(l_fo%rCie>%`kOG8&S=50^_Vz6sWI^tD`=tZ%Mlu`lGc;r>kl^LNgWm-{<d64szVwED1{=QasBZU@9c2g`2vK8g>?RnG3f!b~t^CDXcb|R`|FubTsm#
))05f-~oX0(G5xi`G=y%y%_CCcLCei?(q*88L9j8;eHpFRW`3Cu@chER+y|!8mbh{pc}UELMgBUEm498-rl<tGPrH(st#K)L|_#Eb{osFw5n132<sjGz}i{V%Zjr47MABpaAA>7sQO^wsc&!VOU#9h-gDDo3t>u#p+qylioA1!
p#<<~B3e+A!4G@;KXp$IyfgEk{jAD*LrfY?|3>5i%Zy?=zEFgQQr!CHQ6(|<;0ql^QKPgn0d<+_u*8b45sivhr?a{6Gtz4Kb0`?vzPf&~6%!sKBUEjq*+-b8d|F~AP1L>19r*mBdwR|U=lLk@RUbtO0qW%PINiVan=CR#|L9)j
D|UL;JviLI;E4}Lu)}2)88{7qY3YSwzrRQ?y2q#8vpqChV>KqzbQ&uA?le8+n)7k6)bZg7FEz`jc~+&ce`dq#(Jg+~J?%n;Q<$owi(j?iQi?suOoao?rq~dRVvtb`*xe9UNwoQ498JtkJsiD8B1cN0smM7>&YT|%5YA0M>(F9E
cV4j=5hWDIYX8T}lb_CEDZWk;NfMtA9>~Z9za!u)*hp0=g`$6EE2;&gL?8y>D%jTcH?O`G&G8ihI2+9JJpBOTMS5G@sHtDxB$c%G7R2A1vu-#2`E2hveS7!=G`rQ_l4BGc@^(7MqYkdz&udn3&$p+SC?BJqoj1JgX_&L-)ricg
W<x7J4AL=*!q1&3(fPxl-=wfjo}&XJoSzeKL#U?8zer1`XYYRK5=Ho0n*JjleSv09|C%1xwQ*Ba<(Fv&ULt*e>onBMZf98v|C`<cueb$DHyc#9=WDmTzxVcRFNOb|{s<D)TcCO9!8flH^HgT^gF-)#Fg<dW|CXIPJh|we9mC$T
chOaS+ur%Mu5&!p=R?uQZS;%w=e&ys)#)#X2W0R5YRBJ?*$8$^*h9V)LpVEzEf)7|oelA$56MlbBOdXKm5wf5`7PXd$+iOm&vg8o&#lc!ne&*9F!4$@%x1xKJakH<;|$BiV})c!|4d#)OE|&b@Qf4Umq<CGeGTNC5FVYpph`7i
elS98l=sCDc6Jb;s}W_mX=u-CV1A_)9)o;3tA*}`n~t(6Dl|h<-hidXrG)jA?pd|Z0&-9$`$pnMvgMU@J1Y53BnL54jr=}d7Ak_BN(jVNXB$&SF$!~I+FGMCVmR3`R1i;eKCvn#-uM;4fVY5#N+b$4MsAb>xNz9cQTG6guE_`x
HT+bY^v(sUF&c3vcB166#O}PVC(xXm*X?i&4PXcn);VqVFfk{l`W3xhSj{oKTv<4|zJh<mz^-Ku4QJ6M^%EJwf<#>a5A`^x8UKm{*AnI-)$B&vXqEp0Td1k%w97-Byl@N}9B@Z@mEox~_NiB{%nI6KwcNiYS?^kHILkoq>IwuS
`zM3DV{DD-6n#u?;FxjWWRW~DEf;gpr8#fMOXfQ`K9_uqE+-q|kxLe3Xo`Ht`g7!>2RYgcsheDRWEBAxgaT2$L<=(Q;Ln*c9J92SH<SLOsxgF<6Nbm&l5or|`X(bdNEbH+u**m5n?ShEsOgR?C`T8<I!~mJ<l18iRz}IJCTTez
jk4Keqs)hcX0)oIv@qWwQ#*2%>nMY9rI8l<!(cKbixKpjE_uS?CYmn|@)_#H>6eI=WK!V`GvLMUwYiNWgXUv>Sh&Wz&dZfULDC5p3z8nch!ej-p9<M214Q_Z`b=aAt+`CgJ~dJ|X!GFS<y$7(TqREUaaH1$Dx6AbudKqHI7LDJ
6SqW<#kN7SqKfe{P|=`DQPFrAsHj*Yt0R8frsO~+%o<L^t)w3vw^l{9eErqc_ZlE#qDyvVr%k$UoxY(np0oTGC{`%cDSlvzoG49eR;76XK>btFuy&_x7NH@Oe*16sE%sH=d5^^_tv!f!B%6(4LkRN(HXAubI5&d!(i{958YAmZ
O`B#zQXDYHUTalkS`E{jb3<hr_{xH&qRe!>0-1nOV;t;dO^V=<1InIY*7lzckB-`-ejIL(Li()93!PgX*$csfQ!-ztR}I;trq-lod2HE8IY|M$b12y3Abzrzp|%1|l1~;Ou;C?R1HdQkloZZsif`eCExLv=uBP^i{t!>S7U)-`
YT$tusl<lN+-FGxMABYKL(jph=58HVI-B5~6uYff3tenw=3MkC#Fz0B0a;QSllKTD^I`sV@A$}&n)~^%7!`PkxOt2awCWWd0eQY%<4~xH%o8#*Mr%^8-FJ_g(v|@WkugIyzRerkO*)VhNY4_a_g7Ts+PF({hwt<RTBpBD(om8h
tyimw?&3wRjz<;O1!QXu7V#+&Jkv0%SKQTXAlRU{J4UxO3w4xAJr&GRd>w5^oohE^o4o5?K?jle!Z>P2Y9_$YMW5S{erxa6)Mc9h04~DFlb8)cNIZe+ra0qPH)Wsz5L2{qf$;AwCJzOG$YU2rI<G55q9$yJNlmy-DJ%Ij7VDhy
TwX)-%7d1XlV<dN^v!FbNFfp@!~0Me%Vm8b_|y*;-4Y{Wlcta{(~>2p?SZ)7x_g}7<zws-u9?|<n3q<6dCoJnC9jb0%-aK=Ad_v5D(fQi5VTQbS)WjNH}wh;w!O9UdRaGgC}Li=TUayd-DMPDW@e4oBw&$W-FS<w|8Z+;S>q-o
(L7Yt+QzXB@&|)KX(xJ(hM#Z|pa!j@Wt}J9PKqD4T!F!hDkx%&V83vzKdk+<VcB4h@?G~(q~UQlco;&#ZdXY-0l92flSs^CEAZD{{KtIF`OU7um#o+TI-+-tUuHppn_UlzJ-O=y2ii4$>EQ>YaMPR2$CVPp3p2_!OP+!mmzA)j
RYsAYYZVIE;w6-@iJR^$&V(*YFQJKfN+@SZz+?~Fgi7WaF1ms&QWwt5JRm_)%*?DTYkI9{97UehEx+4>CS|Ic*OvF&Px&KTd#Vc>np5F?#lDK8rhmLm^C)VE-L^1cB&Tss0EASnM~=rN`zn+*<O^W59?C7tqqL!eB%G6^;9pHN
fSPZ|QrU_+Em|g1<OmjfHsy)L*q}&R5b8$Ylt@isq|}p1^#)yhSc~Q$9TyC2PWV^qM_zG^c<vO2)N@9I^7s~_8rhiDj)59AJDyXl7RW28*77Z8pYBaGB+u2}l<OWdQyK^AhveDKV`qxJax^@wNvDgR>TLLqGp2I#WQf36CG06=
DJrEjpDPS^#O?e$4%`#rfyG2A#=x?}m??qHp+PR2U5o%t7knwc4<U)4i+P=Aor(R5(`6X@3~be6N@2<OQJL@y19!?*8*Nj58$*RCSO||PfkCx_jz&|l=dmB0c`!pNH2s&>AFtH5Z0qzi!*K$V(w4_kGr>=12jr!g`4m~0!$XSE
;Mo^#F0%1GUOH2=TG9uvYSD9(P`mY;i>2(bh%8%{EL6Zrp)_1G43-&KJnEZrt!?3HEsB#hN~d<|g{`hYPZ#PWiR<i%GTdqtLAe&h7d|47j*nsB>s=7jJ@Tt(wg&N&r{|lk&1SS2ZDIy=_F0pfZPnW3Lp~d3Q%NhLng1|a-SBo3
g-TvOiC#pnoDd7txOrI?9yCI;`=EyEft5Jd=?vC)PRO-zPFo^xp}%ZU{iaS=>^ZR)L>o6gJP>gk8*<m_M2!~yz1q5N(u=EYk8dXvnTaIQCjP%%A`J9ZM_c>X*ShFIiQZYhROV>(QSc7_QfU9B?~1)bO{*C_m4gAmw~585Ak%Lf
$oHfE9#Iah2D|{|^-K{v%EK(10u}d-j7w(7-#GSuAPjUOZJjK<<qq`wV$ZViR@yi3fuB4?O5Q7kIzNa#cDmyZKmvqwy;cX}jBd4ks&{^aW~<&eykHK;%va6K<6-y-F`%T2WPM_KNxlgp2l-IIEVTn2_03V@HL7t|4GTHRRG)%k
5^*bcoR226$MXsn@4w(qZbYql13a-$aj5*NA$QcxMqX03qykU2D05SszwzXO<gnR*{JP_>e*J3nRX_de$FGjRI*%<7!pwrF$g@ZAGTOg9JL{fYq{rRkcW1w*(#=3Z5aD-%5MURgaIrsk1(4IT?wiBEEuO4FEQ}j4b_G7Ea@SD~
xry%z5=mk8(u3i=ywmS1vp%dS0KrLOHW`YunsBd_r!)^~P~$86&%w-4SXWHZr$W4Xgly~uimj8OPXu@6Oa4YI8y=Lg-n-2lgP276an;({>a2n|oGFetd=!WSqPzuCkk%^rE%u;LS!iMjzj0M1O4VNTD!LJq@f1WiAdq(u^C<F1
;VY}*lr;-tJK}PYc-j(3qaRnT*UOQ{_G_h3GqzoE!**pJCw?EXY<--i*%|(<--Gy^t*z+r0JjcnwK=6Pr^rJ<nm3{jJH>uctn$fZikh4IVoBfP-~S#cph8n2`QEta*OtFy--EB^p@Cx@$<*DJ2#@OixfNkV<to40loYXfy||7V
{H-7Xc$q|^B+vr<j_Vwujb@+~*!dV$%yJMJF%|q{UbXETRLr@96|Q1DN&rI6@*eJ!7r5~B_)`KMjK4YPpdC=4gRk1{ofXjWdua0sD_m_Nf7!flS4D+JPYb{kdvD;$bM0<Gq$<iQdr9ed4V0cxpkvIhMQMp5#;8KrRYHM&8=ACd
;<}gxxK?YCEzW?U3R|d5d&~@7XyMjuq%2Z4Q*4OZZba}TypOI5KvNoEb<Tf5xaS-fQxT2|=~(l}rbsK8^6pB9Gm<VmdVShF6)p(0@2E*1>|Dz$?ZsSHU{Ty;#wXW|Lz9!piZ3Wuyg)I++VPyD7zHR$DytFFxpiZdBe~^PAn3#l
5N05gPWUb{_|?w(u-nDB%;CVe)pV=tRFaQbOcaqw9uaRwMP!+Z#|CuAf<hERfZ|@arl&R5HTk#=dw;|1r>5pp0eRL{@-gqo2fpse(8L!p4MwArWJrC0amPmu4zJDb!>C5Wqb%I##6fjfE5%&o(6>Kd)4&!?@Ra)$if`aiYO?&t
OBKLOZQ5gT0Vy*DJ_ieq3+Nh2MeH&VW34bMgJ1U;){yHq(La-~NFHkA{N(WTw97+ocp6yLAMQAAt~$ibG?fPr7;mUL6!UYXDwRylU8U<#soapg>zu_W(9W`)v&#*HGi&N8=g_+v()Qn-P!ueNymN<Vvq1B%r98i4WyBtsAzs#)
B+(w59Y=$M+&bRpnB(Fj=P#YO>9j7D5r#A;P*AVip+p@YGUTKJWul?Pk$OY~96x9uc$EL1Nb$Cb69y7Z#Asf;r5`B!b>tgH^``t6);Tb(7JnuV%W+5qL(k%9<BdmfmQN@9DupHUWG?#3-$33KG6w}T-%FW$OTm){!a%JzGBdL@
Q~u#?cOS!ap!+h&79ORDA)2TKYexXJLddAwP5I$PQ?vfkJ=0lq2K6=Q3U6Jj)OcIJ))T7>$7r|Mwq9&Ho7_UtGKJQM=%Cz*Fh~@Jx4R;(Ws{^qD+C!28}u1UB8e|3qkh;H)xaXkO1kKyl7}9gt&qVqFhddWpau*bqd&8Ncn<Os
yG0FYUI;c?Eda8kac?@ebUF46V_Zq=m07`=8+Hp}zB8-b4S2{lrhc=g!widlH^03FHa~zbR!>tFpjQx|^^wQr?W2k02%Yc)<*EsACJF;&8@+1Z(?M+s5JZ5D&uYpL@r1w6fn%owSoL=)+Ha1z?WL!`RPrM*un`L@<tai&(%h=s
=Rc}jvrO>@gl)-if-JM!JfuAno2fh9!Nk}8@(tb)KNtNciuXltlvj6?zUmqU=jZ!kInJi#-2{a}F;`~#dOpCWX2E>zT#iuXHo`?)3Yq9M5ag7>GiJ)M)8|Dsz|i6JHm@i=Xk*o&rEZI^y%CRq#AeDugMAYe;7Wp-d%Y!AM2^0%
8mxm^MFYzs{?m+7Y5NkMNcZ9|#2>$pw&>KRT5y77YM;If0t$FQ=nDo-sRDOXZvYs}V2%+#q9<I0S;aZ7YCl<E=WcS~{$%o$^Zgrsz--=4hW#iGv|xe^jUrk6IhvDw`u^!<G~mms?YWil4_9CI#@Lh9=URS%*^M`RmUcD))~<F8
H)^pzI|i3`1r{S5NhGU|qv-cQ7yrmQ(VN#>+v_brla=bRvzB#T@Z)v*EP42E*JI@}L)(wIWd<g|<3sg^EC3!HZ?M!o(7|nK$0ndL!GjyZ&H&V=eRhg5E;0~oxx<zcnwrsz7g{U2QKd}KA;R8^CBt(Z1T=nz2vIQ|y&+m8R#6ZM
(3#!!G1MzLNAeBt;<2>t$J}>>M5X9QCbPi&0OMsUGfHDAIKh*Jn?*Z1;5fi)z%}s9R868M+3|(8zH1_E<0M3y$dumAZv@2&?kn-Vv6jQwN*V7|h6p)WnHWN5kw&h#X?1cF4o$s#5DO@dCKO=C1~M(0$$k<xQ0QYA70z%Q9`-h!
z`<?TCRJA%B&cIo(TS@zx5VUKkEvPm<#9gHkS=jO8Prf4>=~GluHJWV8NQ9xFo+Li!VJ~0m;shF*y)8!;W|^u$0&fL6G9H)Rc;LFeLfrK!ysD_f&`a)(B+PHsoLvEQOPHWuT;_A2T&#vp?$R@Koc!hX<(wPkd5-epy(B-qmd2U
Uip4UQ3brcI_O3~Hn5KuGIV56iikhH;iXw?E!kS6jxwxceWu3MWFk94#d@Briuz<99-u-$uP8Dj<)5MKjjZ)$zN*&Y;|9Hs$Vyg--DoCET4~bSxLz9TP+qj7I2Q`TQeLKC8=o&zV~z|%T=uv|vYu>vT0?zD_7Sf-w0EtL`oXkU
muN!G`u370&2D}V6?UQ*<e2s1#Vc>!*WtQ<T2ggq!|8{87-qQDj$0lM<A&j#O{S$egX}DYXF&!lRdJmgR{GVjT*278PymW85tap=LO7Xi0R>YBm|Yz66E;s0>~l4#fR8e=fkO)Yq@#?zxvnULnrvWMFHP=Fg)lZ8BV{R`da#WV
N@kUgTmA6oFGc{p#dLOAO&V;0hSg)n@n$W>l69yIX_neN++xWU2w63nbIZZB8ob)Ed2p!ec)YaI@*UML>l#Z!_c^Nx8zHwvE)%dk7F7s^t8K9wtd-!bNIA-nN@;LSza5Poa6pdk!UPat{qCyyDbJ@|<X3VNE*(7C(`H$eDsCXA
5T0`O8@+`hcX`&IO(vsu9N5nTn%)4Wmk1f6(`Rt5pUj+YL+5D4Q*1iX<zUaeEFf8>hwBLm);b)-)JNIr8I2|9B5^ai=M#bxfJyq+5R#vxV0p$Qsr>6ARS*y6jRDjfp~EXb1Deu>{;~}mMC1YhN&}p7&G&|JwuICDxD@gsuaD=C
V<z3=<Au<NZ}_+t*`w&4Fx2egtJbVkuctLi3dgeq1FM%9)^;>O5`*4(J)~m$&?dt$S?_I+A#n^8m-3G8!A^_oKcku#2rvo*fvf0Lyy#hV3)ds{I9GfAxOA&Bi)Bik5L@^ykrCn^vaen?vdSTRsq%yImx!gZlu0pz>LN6Op{b#4
c0S!MTaJ3qH}vRj|4xeq#S-9bD0-%Iy!!D&f99H6W#_i`Yz7fY2bnIL1bP?IsHDW<+=Vimk#SxjhZP?FaY!d}#PxYKni!zJCu0PQsPUw_w@3_gR5hh|$qaMau&3JNIjmm*d1Ml;@>SfYj4{`O3%BVqe)}8mMU7u+@3Cgp8$ca>
T40Vg+^xt;l$whzK<Unsy)}M=74}5=9hB_kwN<U2A;5&$k--SK#bgp~cMCEVa~n7CzMjHw9ZAw<$EyfD8aHav<q-uD#d}<o-%c;r5O~%Rc5q;;-!W=M^U<_=WIUjeJagW{t7H`!Y^`NysN+EXZ6qP}P+&c-A{@JBj?2&Dr<ZN}
oExw@(8k4MC3zMt3+7d`YAq8D^HxeEO@<?zIzgKEX1Zu1z>wLNalRMPqgU6v`E5mfcRFX(NrQponTsh<B9@vty3NO!%Y@c5oJ;xX3@dpqb3K}~1*+=RF>aMR9!G@%1iPNjMywg<7`)uBrp|Q8s~RyB5p}9g!6BTh{T2DfZLi&%
4ACp!;uBsWbtMi{k4t9hSC(rvR!n=Tznjl!OxF%G?VeEHu5Ei!V_Km@4%+%GS7chsOl>I%vT1Yu0oqxRiN$#GP-kWVE64n*^)y*5HHZipi~BjLue+|XseSjJmD~-UvOVy71bTZ{0N63QZ;|UHUBW9qI?Bp>W#D66zSk|GVLm`B
=YF4U{U*GtPBUt4U$@c5p18O98Pl0g^Q$exAed5S>QMd0QtpU&V;X;8zU=8RAKzAYW#l#w-Rc*k-EA1Mc0ZtQVQ^=1+Hrdw?h;#t84F*j;5NC6de;M;CtYef{l7MDDk)X0x+Kzl_nC3(3nMUyCsD33#Zr`NIQpCMd^CMT$#Fd0
ScxQ9=lbk6OFHTDQ=sPGW8hii9@;I~kjF(SwQvac1!ZiH$x5CMPHdd~q|~4ED6e1eh}7j){LzzwzKEU>?1C9qH_C)gN!sqi?>MMy5q0@Vb#tLFOEUDXctTP(@mnlzDQl{!joS%<UJ+6eU##>MgVf;=zt-wrJN%SSw;RHggS{HW
Ppr)13861yc4$G59Ox?*GvhA_-NveidOA~M6beSNaHz&C+ui_0w6wz!gxHcwVY!wQO3@ilD_<(nYBnGDG9LBZg|+Y=BkQGM&%m#+!)WR}nh&dDiZ>bqIzxWz&R>c+(pCo74zbm*D~!Ac0|SJMTuRTvvP@vOG&Pt+&+E&A4FYl|
G50y4H|{;36NXVWF9I>vpp#Yv<8Z~82lyvB&v(3<%&Y0VQgQ-2U%YssT6DE@Oa|t`zPX%eZEqZG+hL?lZ7ie%&=|kZ0oBtM*9x~~<i3U~1k81J2V<<_?qP*TGq|5{F;P`v#wrl}8c7?(S@@SFSW4Q&XB%$Zm+`dy0fyAOZ&;vd
&aHQBx}ge5m<?n-TbR8D&)2&#HT7yozZPZnQ)H$hilcu=arX?)cJaLbUBP33eE;;<%kJ5)>EX%w#o6Wl#o@b?#ryGh#H+veTWyNXj2ac2)~<*rWp`At_w~9OY%h_nFX&-&#;%aOjxVe^PX?>zGhIbOP9m%cD8wGZ9wc@iEKjsW
b_8^iXzeab(Up9*zlGy$;r7CGx{%j*_)8YznpogcjanN?+1%QpQbyUh804iH=tTAYC2nY}L>rSCTVllr3I|+<EAs;F@zf6QB2W0cw%e7*$JxQ29ZQx-!A`_#hr=bA(<sZ{S+Q$vqe5PzUghq15Sa}*pKYIN(|br%4(7CM+SV{8
G4AR@Uo#ZdL}nW$303;o`C`JPd?ITcZn$j(>Gy%+pev4Q3U?{;84V>_Q)LHPQP#Ca-vw4!CX%J4dC>hOrFgDFN{U_wQ)CoeQbb-J2i>Ni5N^=G@3k`jBV2~0Xdot^mR-Y)KWsS57%_)^(T3ePf*Z?18pbFeTV9j&elX0?l;#5o
(D=hHfaF8(1K*1e1MwMiqy~yC`8@rHC;(4(?9G~Q*%*cTm=b8lzI!;~6u{6zierV|riN<6$agTvSx`AjEgOh_%~m_NDa70m{I)@`0jUH~F5lvlq0Az2Y)dCk1M&*iisk(FR^$^z`^ar_Icw-(UcA_9|Irm3dFfHXBX~P3Rp!@i
s#cmt2a^EiC|9ls>LfGt27`#N))NBC;RPDT+_}725WV4WNKy9!O><`;g}2)F5wr$1F;DbH@M3nVmkof3jhZJnA&CSQeWd}|^~Qb>sXs-r8naOhZZjHT@RvRh_n1<sfB-4a;Q%sINYD{rW!=`VeIN8XHsY*g;7!(shB6i?BZZf&
hyb;<^V;lzH!ruw@%q`6u9Ljo=Q-k|moJ?Ly^DcH#YRINZc6wDxhZQWvBNDjIW)I0aXI9WnVy5!69dE-uguvU&C88|rx#ROdI^kPVAEN4JIXNh8HkW@?ulBFu+;=y;K3CROOF&>U=91>`0V&N!f~RO4b9`#T15MJyd{SYwtDmJ
9p!>kM)R?}Xz6(T6$kO^z9V&Lr3nz17Lu{o&y}?CHm+7RNJ}TvHQ;7J44|3zXx2W3Uo3RnnF!VHvItgmf8NimqMpbp6u1?JL48Dh;jZd!vj$n-%Ou`%nOC&JxOuq@Di7=rfqU<kE?~;YfN5>0E=W`C#4{-T36wCTPRFHW3H^4t
?}$#vv<u9CK5vnv*Vim$Ne{;H&8C$U#9j(EZRF*QI9Er>zi#;s<Z{KZQS&Lc)cCUqM?{MlhB$$x%aO6VTgy0qFwZD__g1Hyz+Hro3p-4mptR2`*fg_Q|Lt^sCY#Ya!J+{PS(lxH{QY3={d6<t$+Un5Wg=S`Yx)p>y#b(yr;U(|
XvA`sASgVRQC!bPB^!V-?GItknZ2WV)fKYZag)lnV1O+W!F3)bbi%y}946*v&W;04dhNFO<XbJlmRbbNKPxm!vcan+(&)l_Y$f?gA_jGAbV)*OI?)N#T#6IN^XF5^XJH9{ThkSjWGdCe_CZ1`N0O|sAd{P!glkz>>r+l-b@=le
q%5XnY}mzv6J`>q1<a#a^3I5<Hxg(vU$c{gX&gXp9(OO!4)@c;lauaQdV2Qm;Bx=Mr>^qG&ZD(K=C+~Lv2^*c0&)`AgJ;<cG-?K7QWVW5X!nW!ESd}e{G`8&#36W2x2usXoHPEX6D%H;Jca3SrVdbg@tdL&{(Y~R0KWZAGpaj_
VU1*!HFEmbFSbHX|N0xR4?1JoZ*ZjlZ(2svf2^>Gnxow(#KxkD!>4C__?*Q>dZK^b+6qwQKK5c=isYZ6=JXEa;9Imt@}79rDq*A5YdEdJDDv4T?-yv)>4ELLLC}je-Ng{-MzEzIZTj){C70j7WDzkPxg5+nY0gjaWsZ3g*`Vy=
Jj`SIQ1t22XoY#!YxpF<hNbtF3zDigte{D;K1I7vo~d6n?JC9;TQ!5z?}E_>0;}G|ht2_#WxRmZ*SG;D60;+?9Kt)ginWs6=}~d1p1O2Y5Y&ZZGwS|r|LF3ddypO+o}IhezM@2r7>vh>YB@1~l;exS;I0_&wKiGUI)EOFeAs7a
KTWqqsy2eflSfh9NSWEfx(IX9RzE*(@AA~peB|`ns}$Gn<W{=4V}s=eX5T6;yh>fPqh2x#D?N*pFLsYNg06~!d50<78Km#DT-A00J<>^I^l@3DDJ{HIr%mMtdoQravN%(n4#2zi6y-CfVx4Oc{K-==kF2cfp)wwHl=ghg8nfs;
iC%9nT2Igqk9#VcsbKhG{7=rG|Fhoqu6Sq$i{cdRLhr&QRCLr`+5d295jS-gSx&GNqHI@T&tEWEVjfKPJber+5uzPp`$kG~opy02K>#g5;)<-c9cIcVM&k|06`1jqVxG$2Pdx#<*CW4EjZU~EURN%>T$gRC8qc;9yH%OwIV>W(
^Etm-VSQ2;Vv6LRj!Sa&%PJY%9bPqM%0lsl%sL(OA#Bi*JsBRY{I@<9x)RT_qTRbFN<wz6=e=w^mUgXFI$3CWh8WXNOka7iJY#>fcu$ykUV&$*(99^b9V*zz9{^(&>ma{!TG)MoE_$ro9b#o(SKKnkN3Ud{`pLbpksdyV;ZG<9
tcsHv*5nI7Fl%TbL}t_?l%ml_c;8GMeJ?@!-{<d64sx_s^Qh2$eUq16yhkLKsNC@UHO~#tE~l;Y6ya4^HD$9|_Nb6+XlZp+>Q+!|V6x7@Tp2&gm>)(nP38)lPK)0CFc*=}wHS$y{TVs4p)r<4DNTtW+oD<Aesi-I*Iv(b7-Mvg
AeeT8+li3}8>84b!P`J>eFt2tSZQPhYBPNCLc!-LbmebcoLcMG8>?NrcpcKDQG$%wq~e{dFtgqpBLUEGk~GO`U00+P`tCRIfilQ&#NiE{^<-9aar3=mbB;L}43Iy0%Bb_1NgDh;n)gVv;CQ_NO-IaMj#0P9LPfVI{GBsO&A)+M
`JX(2Da}P@X8;UeQw97(6`lf~e+o(HfFDGZu&7ItRf3N)yodzbO9T|p!q!;2?&K?&h;e37%(f=rnM1YaK$Ykf6MeESuN;4sJI4oDo_H{m%9AdNmDD7de@ATbUQ)-LwWMRtO}H($J{^YG{>ZFH2faGpzXanqkpJJq`FR%tYgAab
Ja-RppntKgU}?Xt0=9c8$)83Mxhoj6l6Ys#YSzZ)&11@xDr(_qNwDQ4H#G_4+ILD4x1nS|RbW8N`3U{K^V&YL&Y^|B(b+44cD)2QN+9$Mn~w|aHGqX=^I^qg_%_=quC5cA4!Oq$$eIwgRcoGZ&~jj<6BD;rEQgay^EDKhHOpf6
WMH7xz?)StiJ_jp7+IoOo;Yh?W-Z&&cm|pc`o8cgk`w4(xU;|cdDXMp`Wh^4zVk5(6{R92K>*0*P``px?&yy+hAIjslC!EexC+g7d=i+3$*=8PTGBF8r!``ZOTe1MC1}>N8nlKbpx3#g`{+?X=PMyt$D{!7Si|n)bq&+OC9Ep*
EQb{kQnM^XZ6$EC{*Z@t-S{0k_aZ?mx-FH2zOtN(ihN0T`NX)EN}*)2t5v9KvEd~{rPQ)DBJQlMOoCd=kH%sw`m~<R;kfnHs$?MS%~1b%wRPQQdgEO-BKFHEwtc;PE%?aM0^%o#6&6EyA*{Cup1A<N9Px(;-xg)H-LO`BaQ%5l
FxPR(=e!h#gbrGj?rh|ZpHyZaa(P6<eWB*;8{@tV_+?Qrq^6J}r!hid_Lt)c{2XNkX+YUMu}8P*JvMsqsL;K!5-!(w01Nm_)!;9z5{S<%#BhfA=g7W(MU0V(*jQr=p+{g$jExbM1X*u3DN9+J`0FB?k2!p)Qns5xelg2QJ<eOR
soc>-7(9F-MnFkO^9Rw85v<6_A~=3*xO+4)X2rsdCAq@+rA|GmTY>vzO7CJ{9;}9;?=27T1gM)S>eoEfrFO^Hc4A-;!y~A<HE~#fHAMlA<g{6*I2q;6am&8a_6=iW^z}8c1CUtDxvIVi|Ff7j0=&2B_La<h;0(r<!>b8SR|4X-
RPFgDJsS=`i{L_BWjt+XB|8-BwLKpEVQ3l~k~GsIDvm?)tvRPgHn;FCs>{*BU~0OXk4v=j4sRN3TEKHxX_=+UHGdEEehmkcl{VMaHimq-5)WA(BYWfDgP9~t5%n80ko6bAGZw(-5&!?E34dc{VbHJOH!7^IvNjJYn#$VprPdV6
%pAap8O-#%o#-knDKaNJC<IS|<+TMs+-h`8B>6m?J(tK2v!cq`Ei&B^_kpHM?P`}(RaW+jf``9A+8S58J4rKgE}mUq#^v{EOXMQh!*Gs**GX!B_Fdr`d~sF%vs-RH9u~l&%sUTTxDL2=Iw2EQj=->TSOqsRs)rkd!<0HRwfe=A
JGkIP>+5fs$0-5)7dWjwmvMQ~;YqJ5GDvFIlGJV>(u3b<mlv_rs-N#Ywz3DA*K4ci_=k~*kEZiH%mM2bhJr@dYWPyR7J{n0NRcXB%&2xVihcfgapEm%JBh`<z|20$XW1&`lje9IQt6<Y*PVV~<xf1c29J=jSSAr`OG`6VuHyuJ
8}NB4*7TPcq=#U4wO|~-eP1mvyyy29BZ2%&dMh2Up4V3vj^wxRs_xEG+ta!$fz>n)?4WQtz8_B>#+sH*414pbmd!0Tn>;x%5&}6%M{INv-oNKrKcI66>mB~Jt7+xzK3_(DUQ2?g#Ry&pi0xo$4X^y+tF+?Au^x@ZZ}tw)&nc!c
>pIGQEOKXEYJiBUb)vXNUG`*8$zl~1mw(}Smp$2%thy1bs^mNKEHkI+#imMOs}NYurwH9h$&D+I&G5<!zSYS|D=DOe+G_;<R2y?Z4xcrRwy}>Q$S#BPB#=PX?6u}>bvpjv>@@v(oM5#J6GLp(Vqwx*q5crV&87I<Dck<UpXJUx
gNJiAiAxeP5p~U_+zXt2+eVQcl1F)!^|LC&;|I^z)Q-;7rGcibV&ZPRPAK(ytk#{Pv|N8H>+;<(3ZU66_l{22zv2CdG~|?n_FrBt6^g&4!p%@(EZeBp)jEHM=K0HLpVv^t{iX`JFDqEAte{)(3V5vxT$6cid<N1(X*s>-s~*PX
kEkVYmd#sJU;qGPNXcE+<w(~3u`Yx1?j`>|H^~x>&%0<?dZiFf!1<f=r2k*}|3Bl_E67JQLw;5#Eqjxhj2lIP#8qfj&9IhkMw`l)RCiINT0FQQC7Q$d@vuG`^g&yh*i^q$57zS63lXQC@($B0A}$nRi-@nLScqM~X`!g_n4yGM
ZIjZiL49YJ4fKf3>cdMMg&o>P$fC+(SF~OO%EJe!cVF*DJMAqewE}(n-WS}G;Z>Rk(@$>MoY>fJqit~GK7ZUDW}};ahQ9fu4*h$zb=?Gx0|Z~@Rw!@b2tgne5z%lQ(m{jlQNd!CdVIW0m$1%C?rLoP>teYc40D>^lg~CZDuY7?
qj>6r!q>vo*zA@?h8_vstIL1VZl7Yx5rF4e-VoktU87SHvIcoQrJbvvaapu{JCSEWYd=9-Pr?5A+&z_dXgEG*>$VwfZ*3(D&t}7@Sen9n_ZpG$;ED=gCwf93XP7>29oid6%MwnQvZ%Ge@ak$-GCMXJ#)KtUm6DVM0s99)?3OuZ
(A8nl6F)6jObB8S!<r7VXd%1Z+xcY9ZCmsS!n6f=)qyckTHBsv6*W%xxU#~ZU%W7o(p<yv2_f^b<3hvR85(Y)Tr((ch>jkhAab}>WxwBzJX9sIoQ^06#Z&lvBtdn=hJf|LM={NBgt238G-da*X-qSFq1#H;o!G4Ld5bRAu-Iii
k+dZ^%6}TG_;V%+DG;WgV#rfl$yKM*+P=Q(5dB=QFhhZfD77}PtyZ<pEjEJlWR>(|NQ6+<xx3zS;P6E}y*BU${`XXS%#Kl1fUQ9D^r<heVr$4+>eZ6EBGQJeDPAmV6Xyc=D=}p{a+cJ^Ee5OdjYUaku)Dm5?Im1&v5J!U-&u%%
u9mERFZy>~&d*X(t+!5V)+##R)p)<Gwp5od*XJzR&t0~kvviH+YcW<tS+YX)=da-KDmGU#7p{dk?)d)b=NWYjw9c>Ws40ll=k2zf2*Q)U&ZIwgvY#`_CU34O_q<tLY}C%0cgCi0f-Ld}<N<KjVWF{rf6@VS6EU}Wz3A|t3N7%m
#b1h1!z-3S5noeY2-e(H=hbymfA{4aa@h43bA>Qi6*6(&<Aoa*=P77Rs2}S1Q|-x49+`QBa^5k&+QrCd!}vODLohye?zXfmB6iu;!0*(<yAA`^L0)b0_lR!ez^j*HuJ>$s*2#>T70XJ$coaH5yFG$}tvMXPC?^qoUDIBCGVz^h
SyBX+Y_1=;yi|ePSRgRdIh>Q+`aSXl6KO@yqkguicSULZv3cETzZxvwkA2baBhe1x01+IBB@#k_FPFO&S>AIIDsm@tH45hAb95)QMv>Z$ASXq}E=#<vZm3|Jv2*#rlQs)luuLGlAzx}Floc`!88Du4BOsL|8ipIoh9xS+7VqU~
ujhoUkj4cNJ<1x!NqG;eb+Z|5GP)3LB$R<yWO6(xW`Lg`FD_0mX4$yJrh@q#Z*Y+F*-+>OvzvSv$}L8H7Zu3LfjzcoESO^a(!LNPaFj$E{wngN5XdQ7ZyP~4sRt<kPv#YuhA2d3dutW7<Xu%wKPrVJSyIru_WrQQQFF{7@AAJ1
o<(}}$(~<8*|?-Ek@T5(t1z<>gI+vgr)}jMk2xto&<4I-9XQP%@y0~2NXYS?rGCa-wSkNx3Xr8{6Wis6PZp}?Wr`|6wo|h9wb!1k!;}cJBbzg$tns%-khQ0UFLyB$dE9W3+HQ=txIRLur3fRMyGCr}iEbE(@FDV$*Ho14q&iwC
lFgRMEVR1c0f}x!EbFy=`TcJ6YRk@u1FR>H&e$23X3)18;ku2UG*}Dfq2!~!K$;*SL}EZGH72&}&|tKeh#2;Ab2DjTr;}y-l7=QC1gia{O&V<XRbY8@;;<FNUvz<gvqdap<>igt4X*DdMIzkg{Z71~+BCE20KFAy-uy;P_Tm=&
`WM)ZCJ*Hgv!Z{SKZ9+<NDjJh_AZYu(xaoj<GuaU)AWbE^KN>1cJxKrc>KV6+Pa~x#<i&-d17Wb&jQdYAUku+U>Cl5-M;zeHP21VD)Fm|2Knl2e=~%on6&#jdq)mFMy{RNe4Mh1!Y``6X(6!BZ-Am<vn?^pthpqw;wjz|mUsDZ
*v>!Zcw%auOipJLG$^BAT{h1B$!L^Os5U*xvKYRthU;b13NKVx`tDsfaXW?6^}JRjZUMZ@d-qIv?G5EF&Vp@*apF<Gb8>d^oS$qX!qR00a?MNxOO7aVs`vo?&_77~=~KkmIbw_j!;Y6RL78dPT`z7^pSd-#>EL0wA7<GU=n0*}
8kPrtZ1(yOr#wIE=`81&-T}tuIS|3P8b0pxuq>NqC5_b_jz3K9^VV5jR>!nZqSocC5SHZ@)7gw#ywKac3V{3+L8Bk?S#>dK{cpfR)H(p^;0~_@qZSb36nkrBN%cjRXA~$EcKRA(lxxq57UGrFr3|1P4Bza3v#sD}(ti|TW|`xk
lr|i{zvRDX(s>vE(|rAeuT7b3^FO~dwVVR7aq{7eJ+3egUbG2klwrZl%wN)0AGLnK4pKM&XOb4*HIIhbDBGV-&oLwR?38ivb@J9fEC2i#$t6!c)%Ts#37jO^$-E{dwzg;M-gxXKZ>&uT!e{u#&ht)L(N(&Gx|W8MTO*_UvfTAU
oQrmEI#s_~tRo02))g1!WcVRZRcDme0z2h8d8(3rH&46Ju59D>c0SM>^4|h2Ni1(sh1ODiIe3OMxJ*Wm(t?|iK3RLTs~jOv<wmLMqqlD_-$VqG1<6CL+h8&u_uJlc!+BmsPgo~Ces|D4N>BGLeoVV3Kc^>q$K3^;8*t&ZS_HDi
Jeqxguq3ANj-G^EszH5oc+_P8p(FW>^{;%{UUKNL(BqO6NN2ryg^5lSNf`p4DxeVw(Q3*UjRnF(8^#<Pb<MLSCNfaXecn`Rt4RmCWHJx&I1VD1T$;@A3T9ZI5zf13KVw!PW15%EgX)29s+eI+q_oFmU@H{>vNdf7S4_-cYC7uT
&dZlEYIo%^$LP}?$`o`Y)DAC{QKdmOo6&Z1wS8R^n%M_oL;u)}Qq1W(t3(zNS))D8W)%g<fyT6h5{IVhkV0VAa872Y@6Ij~KT&eKIES89af8tr#WhB!C&Ho$L3OXCN)oWG&<uQSTbbx7FtvoAICUrieH>1g#p=5J52_+wt(y1>
vFBj!$9Lx!4gf^B1pr?;<$Be}7Uj5bhvrGC?T!&G#)d;LYTPN_JKH-t>K?gJ{$7m#p2g1V0Z24=t*olX_P5E{Ej^yJ9<teJK6O7~n(QSb#3HCm24UIlUp1MwmI9be)2Ul^RE(Ea#k}dBs<V8WXH^SiplpZ+nYA!j;glEFfa!UU
GF*fs*o4RhtT8Fu`S?RI1Mw2oR&t{Df84uBFS^I4M|&4t$phJOHg<Ca8E|P&UmZAZd&^s(3`BM#cCK1l(;@-M71B0TX^AQ=u9<M*TwB3Kj1ucLV#B{Sh5)ntP!1a`<bmQf!r~gQ&H3FC=~P&`@@`%g!}h~n(Sr|`+Qqtp;d-c4
@i$87$$utwWzk4+H-hdFFT=14q5$zu)!TX~JGD?WW@%fA`=<*xJF5wgZ!Y&ywF<IK!wo-RuSF9B^mVk&lXO`C1D3ru3M7<TRD^3;2$sp`c#S%se(B71B)~JKOc-|M3RkP1@8hOlSZ*B&2kzDp#_-ODbgp_*w40(2)evp1Csp2X
hL|iopyU6DV!%%<Ofy2zP!36W0PI=iAs~y-UVE@(q({>c=G9+&P-tsM-4eRC)brKWp1R4tp064XjbEfxVr@EJ#dNow_e0m|;;6>Y?*5B%aj>6GhR6qN0u-suWtI~$fh=%q20^C|&8Nk9Ffp_S=NEft7ni4pCvV;b4)drzDcdM`
QRt4b9T3HC6ZS6(Poo-v1K$2w7dNnzcj+&OCkO9-0p7orxarqe2Tgst*26}yO1uB?hT8w+)_s#+o_EiGeD@1$TA9)q2M(25!90gJg0PUBah*2I!muwGdzkZ8`U@c@ExM;v8d?+Way}RoAB`$h(4)Kf(z=%LsmE@P>H;8;%qcuR
Kgo&`OS!B856?w-nKrub7qbTYQS$g}6tgUy+*@i{y1_GnpdPFdKg4dJHaD{-UhrYXJHWavB&qi-UR+}kUp<W{54gBTAaE_NqjRJ{bh*({2+T042NZKw)iJrWNiGR~dOtRxDM(_btx;zyjaoZ!LM>eaGI|gc$INQZn<a$qAt=EI
cO8Xh!(-!#DxcKasOaJp_j+olVf4pi_t<;r@2;%6qr0PBg?n!i!11oZc@ExPUU*9uW4+)(_ve$#qa(Lu;P#Gy;1~nnl<>J5zq~2NFJbe$^X!QR+uk*P`Fbrm?XLD5(nR{b&X<r-!#aQa3CflGfo&2iwreCo2CA+ZI^i}^bev_=
5S>{Rw(%@-HegtHu`3ej65?EqFjphW070%yNK1*xFiy<+w24$%F_ifRKc;Xx!MuV6McRA4wYBA{Xmu}6$sKF+DQXu_TvKOOepaexp>pPCG|UQH4nZ3#o>_sy#vxfzBPNZu^x<k!<WdPy6|a>mMH$RVUX0C%H(?VrUg;q#lp)Rb
me&GJBen}xviosb%<_I1?0td#UB6XpNwSck)&=0cX5?es{25T1nk?vC1va&anB4RRDD@npQNf|`%41Lo0(aPKM>F<#(bRguE(*l`pZ5aO6y#ZD{~-hyiq>1t-GZ_dY{Yw2gr=fZguvCEfCPY~#3OC>owy#gH=?Q!LJgzdB!Qx0
L8y|t_c47ku|*a17u@uSD8aHk>gQFCQHh6T_}Vu%i6{2nPpopuid$Gn0m$!HF3*mX;xF+~8d;*1XcY`XKJNah`qmIlC!a${tI~~wbT4e@UfZqOw$9RK**-93R5PGO1}yqE{?kC2c^i#=O|=kZl-EmrC#(BHr9Km<8h%2!kp$vo
AGbNZfe4%D`+2{iL2#LTy~m;0Yu(1ptvCtRzGIv60M0CIrtxirl{)XoPXfS#EbwJ(WRMa2{RTakn}gW8$%NBXU)Z-76+HD&PeG1A?I+PIUvvYahH{wa)5cc&kIAaUtqs)bHK}wA%CE)w^YN{ruk+cH@vPfqxgNAlfX~P()&X_A
ZGj;xvoSmEQx+nE+l<JE)V_`OHNn(pbw&tnuf4M(mioJE;0T;|H8m<}TC=!o{!D7ETx<D@6{lxyF{0xYOP&YIbouF1bu=7y;mGbcqnMS8lW@dvVQbGy>elx0%MaTxnRQ3(T!Sn(x5qOdH>@`|>vqZ-VNNt91#bj$W8jjyO+k3<
u)Ze2$e7j$GgD+Y&cgM#R#XHTNkG-6-SOi&xD6-AMv??hJk01CP#vD-z|Q8C_!+piw4x19mmi6&{<@iK4P^3VsbKS^1~WO$XVs%zW@LaKFzeCngCwN3=5geLlj7Vu5T%{S%BMeZQ?TmZ;wjvgZRm#XJz%9NDpFDrC_x;vi93;{
1vj#N!NTzDjUVCpg^0=nKWoiw*~vKQFsMaY(GC!LhF&@cSR#J1O3pE9l`pO$WPM|FNrMKa#<W`430LDr169tkPZkBC_i;^Ni6oq$(|hIWTHJe%QEk{Pfm|C~P3OyWGUXv5Jg$=~o~|4N`;8hIB98tnG~5hT%yMDR*vd7jn?<s2
$Mu_L4gdC9gPZ9;zLeh<+h0~l8lt@FLY_~q|AZ!C)_KR7trLn<tvU8&_;x#lr9d0iy$zUsM9UhZ4r_=Ph=q}gEnoBRA<q%h>5Mt~IiOTHKwi;_1t^%XHenco{PoTso6+l?|J98Cxb<qml+Q7zAZ8s$wiE(BHp{>2?{#L%h|$MQ
SMw<O@8gv)A0nTK#wBAVg2e$#4#{K{w+nhnE{|U&p*__`+|ayCcWmz^pL40|n-ozz)`vca(l}lvSjPXqNBlqSeR*>mN3!St`V>9A8)3i(p<`wo4$i>)Xo|8KLrNNwvd61YbTokm*-Ko5ZiwRWVL$ukQTNf^py=bpK1Wyt(A9Nh
Wo2b$W&ZL(dxXT@uxAwdA?NLWJJ2)XZ68Kx5b`~{tyYf5u!HotACSZXUdUI3QpqhCoEKDS33S~NuK1gO0l@I=mW;)p2GmOt*$hkFbs(V@W7cQw3(FeX6DPC}jt0OVlS&7`d+w!tFg`mvc=>a7aeR98=G_IH#}D7UetB*fj4hQm
YH@Q~wY%8~c@wqkD$75@&M(}U?cKA=Z{-&6JtF`X;Vc*0{p~<@D$UJxDBaz1==MUfS7>W!J=MCkwH<JqqWHsg1yzq7xZ9p%JiB+}^T7Ti*rcPO)Mf?qn*@c8sD>^b`u_YGe9<z0RI*|2jG7)WclffLIj^S1ne!JC>b*Vsxl8JY
wuSilGhmFzBcIdyegw+ikbk}jc89Za;CA&lHoW^i`2Abog^au|M3mSqxm3}N$4&4p=J$|DXfeI-IPafH_T7x|Np~AOjzzDizzXPKqi2h@XPXB_z={2(xvJ_CPvT+;>v_zlH6{#lRf^%W>9&fbM~a)#eg7a95zz_t_EV58-rgNr
&AQSUVUiQP!`54HA`}4yd16F{yWMa?#2M&!kbL7Ko_OuCRBvm`7~XDe^&bEe^LlBl*?fZG+9d)>UEcYe0vjNnox|)9xQR)BEY{`p4j3x0?f64xI=a(k_s!f4GDR7Rt{e&WUGwF%T$I&yQH3vW^t^8$cDZB1!G(m+7`jzXL?jRv
<h{6@q|q=O8H%Id9GQyy4tpCe2VfQM7pV=;?bnx!*<DtmvL_0m!(7)6ikG7u63^9RQ#sJ!j}}ef*Z-CF14QM%EI=ZsPr9<|G(Ba;zyQJIUeo6f>#daWx=#lBqhj7+a-dX-<X)BCra{j$;cq8Ec!W*dK5zwWIl@<B-{L6dXbEO&
o>l$aU_5itS=)ts+#{%+Bw4uLWs@?$S}Z~OkGqf3I1K8yLq{k(I!_r0Bx)wvJiLtFJYBMkx<RyyiTRPGoaxd-a3ciSwYfl$Pg04)))7aAP_Y<cy|C@M<6>vao=~dtT{W4YNza&?_o<E|<1_{`5Yp3k{0QI&UWY`QXN*+p#125)
?E~~~L3O%@$QXk`(GMS@*MWzPG*qYbKy92lS*YKsI#f9uC=}Luy;-no=d<vK?glmY@Z5>+ev`Y?S(@Z@RgG6n+IO!8i9zG4dTY?m^;dlj+H7<b=NiK}V%H<gB*BX$!)l^>H0fehGp-GwHT%NAZa$o<((Y~aGAL+@o`STcxg25J
ysgRK8ZV$^p=fg)iI%8>TsQv8o2F_`{}j`HNS#?<ak?~QBdbdFBpv8V^^g57_7vIK&*)UB>CkM2^@7+vfiu6VZpvX2pt{*3<IJLeCVNyPf|kv|Q7Gl`4G`)?`<fO~Lz<=}ZSGY>@HTR9lcIdU+XTggk#g$!e$!nPP2+yz!%<kJ
^o;Fm0!HqY{GYeYj%b>cTDQaC?bxNkmmTg8#6@bvW^ddxfaGW`gfJMZb%D<*V#9b{!$*iKih-5rYvuh)pby@!HjIPyxEb43L)_X*B47UQCsD8wNgymEJ~}0!xgwv#a|cI}nGorQ_mcQG@GKi&FUv9UDW}P{i%9$sLvlk=KO_eW
Rz+(mj7H%sb;!zkw%m^C*&?J}_01aq%p464{<=-!{2|fsudC5pvq%8K<_{q~8Mm`0bweQr$_qSP7*EH``D#|+;zp6~p|LXUEs-mJVp;S4gHx0e5mEt>wMy0?Vdi|t1}+iskuXY+8GFz0Yv8`{&vNr<P%T8skSN!68$w)_57?W$
OWWXj2nx$|y__*CS5=8!t3%QIa#;JaZy}IplaYDWgSweTFXN@Ya~O1lZMqR-Q1iYqC&p#-a*p{~&EoQ>@C$6ZLN8yw+%|c@erBBsWr))0;oBd8oujCPDlm{C`C^EKCfK0*?4}Z}F<;UsIjHUy<D*YyeU3l4S$8X-GJ(jmEA2Ud
b%I57Zxy!L8pLT`tc{EM^LkP)MOcV>Y<R^iK0<XyWXyQEx-)tMKfh1^{_eZui~pSgm9n4Soc-<m?ZM%ZeO^+dl_x;+Xj@J-V8I<C6-?L5e-|h>nE!*2Hdy;zH8fz%994<HLef39c<#-|D?<6*Q#~QOS=xjywo^+uREmMkI7fZf
H-<_rckPH+<-v)O&^RL*uuAQZ*oRWZs~R|WFh3*sUl`YNWGcbHIq(heM5b9G6>OfGF0^nO<?R%>90TXYHyCnGCSrqYhg>5yqVulcFfvBy?uo)4H}?Bs{_fn0wG4XEUK`CA^@_%ZxTH%GXuTk8VTgvG3@HEy-3(P+;igOZ(dbis
sn4jkSnx-ek7vuO=y?-HmP^uE948xKC~CDDUcggB!veuotnDG1RxBStJ3tbnRgWa(?<TOFsfgESQVZ~rAdunDtW@!!qo^L!Ayk3mI-_<TGC1mMU}(B?q+{wHX+Zb0&EgPubI|uDJ!KjZ@`ya(Wl4%lgyM^0{fk4_`jE9D@%q?s
xAS6-iGN1PDSbGvm+PV%vPgwN!zd%q@xxhh1$-lt8MnSGa|<ChKIU3FliyYR&q`oFk(z;;=g3UWlBs+wPQ};Ii6y3ja?Vfa`01E%%<@^a0l7<bNS!3$*ZF#Z{Se{nlset~t=oXJd`y8Qax}2R!zbKz-s=tyh-VrxhO^mhzV8Iw
G?oVv(G{6Qy1gmx5Zkzr`NJ*hV9N2nuei8(8k`bctZ=8ALE_uZ3<aF)B>fSV5=mw_GRO*pcDJU{u_B6z$GHj{mr0yqaHVI!T%?=V*SL$I1H}>k;t#i+6rcgu1^U*I#-`smS^63q9;NW1@uKHzY$RR41lmGubmxRYa#JFr4Lz3b
-H{|f<y6%izuS-{dRVWj(!z-N*QTgyLA<sA8jHrmvZB=W{YI>Wz<VL)BYFiAG&!ruvlHNjm3zV$BA*fA!~l!QF!>3#5V{7(JoCUp<INhuk;DxnC7)l;fZD|h11R}aF-(Kd8W|@VDW_s&vv!!WPPCKPxO&$zK`u6zKqbw8a#Mzn
nn~;sjq$9=7v$cLr9vo|$Ty-?hX@SCaij43#JDpCso_+wfvDW&8A{&eFGy(*6$5H#vYq(qtQZ$C*h%vAvc6@4CdV~&dPZDRFjp}}k~*($g!_-lYs6jJ4<nQ!L{J88O$NhFfHxM#P#dwGyV8+<muL|&qV~Iy9%&wk8=;5BI4}>w
ojHz}dv-@F>MqhXWX4r?l}wmUIFnBd{li{fl737SZ;HHy#=A!QI)LJSzd~U?isSD?h9c82<kh#e9e1#6l+@Vo;s!Kz8WCL*67%2VSLgE8&@zYlWRl^y&<a$np(m=205=KM%pZz)*BFJW#^^nMRp_D`MU%2gayR_}_MgNE`K0+8
#pzp<DK6|fmmW+eqzmOU8Vw7R%?eew%G;VyitLASSPGvkJ@zf3+(nwiHHY=W<|12y9nO_j0s=zZcA*{=@Q`y8#QE}zv@{ed#iHjBo593znksUl<)VKWCC@!KT0>$hA*}^7j2#D6f-t_PO135)@420AV35PKP0->kJLuk8zJ}n9
tc@K?H>AX%$0Ij%USQ)GhsAKB1$^5|u>idjN03&9L-ea5#u<y4k<FowcRXr%^1tO!p~{;O4)%#p#Zouw!3Lv*RYrqreEm18?qEyCTbt#4RmV3Ylxosg<=pp)_UM#s6`?bxF6W#;-6DRhkb0qzUI6YP7B4YgTaJL-3;jl@bAi_+
0KiUZnSNewk{kra!7-aelD}^*%lhAgBo}E<aKD-4Gu%iS@-4}!9568EJ4P@P*eq#@8mWo3E#!&S-1p0m1?5Rj#Hrk7=zH%FzqQ|gLsxG2{(3W0vKHaCcs<$G?IrI(Ady*2Kx%*py}d3LWXvGQdFcMC5PlZ)LjjV#5}#pR6#NK=
a)TFF0(vFqUo}vzK%0~XumVUw89IGoU~EifORyE1Mid)cQO7XFS5+dsduSji29<*+5342=nu*$Mu-_`t?f5!hTnT6Pxm)8;*c?_#J6Q1vQ(KitAds-S-kj`(6RMYw!EBBu`rIuyYn(A63zlAz&a;_I2YN9pNj~G0<Y<q9%F5d9
%-;%DsdMyut`PSFx3Dpuq>#E(SOS}T=DziENrDmjzJ!9;<pdZW2VE}qmealZdbzo}_L{mdPmOF(lWVF(%FULy4%41mr5uj6#frnO2fS&Cl|&{um*LH|?!0}tT)=?P4;`c|Y&pnnm`beTQ?Zmmc7=|>*~U|g`AGl~ffE?A&O{}#
-3jl@VKo5-GpTxknEK)P{Nl~o&)LPhv)AXzm&ps)Z`)K9`^jHNp?Vta5GXvRFh5dT7j?bg-wV_fwjH{!hTh!&;Afc+*&ihVKd9|GQosxPGabU^pxiJ@ktOsU>Zs%zrX0&jePtL|(qM(&b)XInO&RM!p;m#`GWJ4M{e{qcF)21C
uzm3-46*39Sz%>8c8+x#C4E4w03swZ0(^j)PY?c)NrpLpcY1mNe-D2+I6F7lu>f6TjtMuUIv_5ZT92CwY_idD$24lTy+f;@G*w6KJ7ahd+?$RubRw7E@%mAEy)dw&(}VuDliz`&2|KLxl=p@v8y&DjyD?g^uH<MzxPf<2kW6CH
b;3(EiVx94x1|-a)I%~@u@m$&BkUvZ&{t$0t8w-~=|7eNgzHNh7#&f{G;)Npy!@D$>P~oTFv%I(7*P9e+7*yK&U;#)xfR>@9jR~E#cVa*%!bJ;GE2#DB7scCTdI16Pl_dOvE&EjC>w2?L_Wz^JRfzj$}Tq(lpF%&Lvs~PI$t-S
%bT`veeh2?GNBQOVyv^my&;Sx^K6j3PdAHMIj4|IJh~W{_7o|>HPGhLdp-y^U>S9cx4s>bU^;kt@b=>P$D{1x&6|_#{Nm{Cd9XM7&4DG|(e(gtF>zUoLo~3!8h2KDCYLTz%eW4oZ)SCg0x}Z}DPHD+oRy6PX|LbMzJ6msz$~tU
FHyO#pFK032`Z9z5r`G$iBDyHXI;+#o$O&@PIk{zL%#VHJah;~t;%gErID^MObPW!l`+P0hM$Gne9_eAi{>^_C9XjwCfHuPVH=p|=;iT6cKrI{==B9cCSDw!y&hJegN?5xRUbZbJYSINk9)q|?2kuh$FF{Vc*D)Z|A=9TB73dA
QL%gAGazo^25&Vr|GcsJS-!r~lGbk4s0XI_&%w18S(gLZP`thu!TR8g(u>xKvcp{5FP@?MklRu~3dkacZ30dqvkC59D>9CFrV&?W+SX~=2D(TfNJ%qyh#X-JqQ>=}!92qBbtgq(_lxnUCJ-|BpkIW08RdoEG@7c#cmvk5z@=Y5
97InW^YAt&dqn`216369RE0LSzSPMqN=lWi8}xMX$;A$6t>=V~+xKSI$kN*DZ_Pt#uj+HWs4V0(Wb0oeYp9%?m$E?!`&)?iK@*l;kw|CasQOmZ-bC42X)x%d+Fir!KiPT4v_5B(6()~5;lVzH!ucfDZ}zYZ%yB=XLA-2-Ow4ZC
PT83KNPH38)_VozUoY!d2#!oq&I1EmzaI@X48m}*1J?$@>D=_ppx%}XGIA^IFy?Xi?6Oga@WIP~rfcZt>*(fdY589-8bUJ-CZbj-)5Oi81koo4-yNN7hsn$3?LtBbqz$dycbMrd6Y_`66&V*_p$QXo{AI$4wVui()WZlmM!$_!
X!8^uR;`QWAb-cBa}Mf?u`V8WNj#9v<10(JVh0EI0Fm=j)njr5%)))J1y=%*dL8L#Gw;U+7Fb2m{pv~gRTEz^k;~aLsbD4FzkQdima}qvw+|WtS>F%ey*$uY043Rcl}&vRSsEz@)Fh;t0DTXkR>Xop$wAc_s83K%rm;lyR?r9;
3aKD>>m+&yZ0F<^{m-sen@qf#U6q&n!{^g&@_qRo+S#v*e6n7`G8DG{D;Uz6T5iAb{f(yuD=e5S;`p_{D`i9#+t`Hlgmg$SdJONd?kbow5cIIoC(~J8C*#eJ=Lu`ie<7UwNKGQF;_T?+?D*)%gOlvLgTucay?!|=KTb2O=|Azq
>R<mk&ac)v^2c&jvp76>{ks1<os0uv!wDF%61M2Xr({E}b@#SwFnq$RFx25D8T+GWWTin>bFFM<<Lk}h2Gz}2<>ckTX~ReNfn4I--<qK5TOe7!rYpz*?RYVoAX0i^Y7u>d*Ii_f`0?!E6t>fX_0eK}SxoRv%dIRhDE~C6JeV2|
oFUB|cLBfXudDXpfoHXHNUBGsE_vl8B^ht_=zdb#DXOJ`*ab&Bd9f?(oonD|Hu;ccR9|6E%XL)`(=NNZY0izJYNcnP5fIW7j<J{+SJtBIn5$(Yu6F}>vG9t#jE#sWeM1W?>o3O0@m7>Cg!S#WJ$%8h>tc3CJJ^Cose3+b!`BK&
`#Yp^M8y<C7Hf6H3vs0%djOv>=T;#%#pPx_w%qf49}n5GFcMsDN>~?7tv+BagmkmizA(mp4pX>J{t{+ygE@w27wWb(Z?GV0AM<c;hG}Q>h#NU8&RjZ%jC8<Wqq<1QXVx3-$-b;~HEy`CeLd0KLry`~+a<SH3s{&xSaQM+(#FS-
5Q$8J9gURsvZvW+75O$@3wt2P-yNJE;qYwY1>o$<3e%!X?=&;5u{=S^A|zKj<&KxOWxMXMX)I_L>AD4V+lsnvN$n?cLm^oH9}up4d^*=?lah!a>c!YqQb45w6BUOICvw$_d4R{AVul`|HJk*duvI|2B99H9NS?n?t<zEc`?CIF
bD0PcTA8Qy=qbJ7v>x9h)dnC~LS!))_u4C_R7RA_T-NH$Ox?r_A4<)RyiZMVtp4wc&#d#j&qMdtIKGg3VVz%n@qt`q-2&np?E6q4-E|5(1Aw{M3nH7-e&bkakNK~*%B}S!r{!|Ftbxn&6>Z|1g|a04rOT3@xJz0C)sl9Ddg~=x
9&iUB?M0U4#8=5r=O;>%2p7CAvSbPx0$x)^Q!Pk;9=7U?`-9|<aa`A5Tuz8AU;iyGc4F9R`dTxI<}ArB?2m~^$hP3nLq@^Ug_nA>CEiil)v&G&PoS3mBl1l=aSHQ_?1dgiNQVKb7~`JgX<~tU+j-I*kr!(-l5=iXW{g%JfbaNy
>|yUn%OSmDqBIhJH$%3I4h|0|2vFR_7Q0-7rY|W>LkU?&OC#I4Toov2MPP(1FKh=4O6we%IJkn_mmwz({4~#$G{g%OJdD_|(wq2pbwX~n&$cp515Ywo17pXAng|{2g7F^tWYSY#cf4E<q$~q5pgOiq{)4|M^kE;ZFGQU`jFK=l
em^&H%;m@&_+eH^w{`*REx?W^=m6-A53!n??%xbgdgY@d5ud|!ff^rr&t?SlX!Bt-*awfy5oUq8H0z4qAK493;8FmDxs{_kgeJXLJ^euWR5j3Pyx#(iOale?_jkXRbMB#GNtl4GU+CIVFYoWr8-eor2z@d<OCo*awz~Du#~pr1
7qv9e6}kQMT6$myKZBB?ZpO$NX}4Ra6-xK+y-b72sPQ6fPi7lXHfV{%)18b8iuv=%SE)oG5wgj4x{=GoL4K5uMds9K$M2Z7ntA<`VD0`EsEtJN94)`qMyIL5!h;SCTG!{0k#h1ySP5y~=-})N#V_whc5U)!j6hM%e-S%-w(f8p
eg4{3)H$vjl@t@WcDYh%S(TZVoGR&l#f1%I^9jO!;P-&pb-c>15!z5<97bStyy)OCG*5j0H5*e{W4dQSR5#Hj2SPA%^x)V8S+i^^j9}(ZKx8;0Hhu`+;%WreSxa3u*bsWfN8a1bmC~zxixZbH$XZ_k00hqrq2MDMy(2;e4tt4Z
pl!%57$i~yq_adv;Hc_`bC`jXAhfY?9CnZkt(&^$b7jbRwE2xtoLa+qvP*_j{O?1Q`f%c}(aqhCE$n$Ak^N-9VGg5E;2mcyf@cw(FDp=tXKi5J-3}bb*sg?fL?ca!4i*}iUj*c<K7a(ni(+@gDilbC{TOdi7*R-gI&VG5OIy3p
;CkI7Od!#$d&9r48w^PU_QW%~{cSjCDV8u8$tMBtw5Kx)f+1f1;6c9^Ab6jzIHebYj+sc?$noUi#-w&b(zJoFw2x2N`$!LH>$_C+?hUeR`@=|kyVw`oUBGwX2dn#@CLug13bNLS>>66Gmy6ng>9dv?F<Pd=hZX0g0mkRRVZsO2
{pknmP2Bh%{Esspx|O0|J-+c*YcV!awN*cJqD5TzDTP3<I57OW9a|t`W0c^DMH}ndy^!7CbfxrKjIo?8jHZJ4ZTs}z<&f<@rI<4T^%f{dM8K$o)5G4jT`W!0BSF83#OjeAC;#_8NO-)r#J_{qnj0Mg2PR^_Uqn3EHr#UeyGjRP
(OtpROEa#WXu{jr)oExHI3dNSR<hhEoLj-uxJNE$#><<;iqNW|F`e_kdO4a4!S3lQTiLJq2@-5?l!fy`AZvmzG-bYok?lGhPNmR3;X8^bo#H@pN^_C}?_59v-_3boV)#2L7#z<hk=3BrlrkZnZ$~IdMqZ~Vu{9W-9`b$jEmD^}
2oZ%fniIh)Z{<fYl1fG2+O{r1sgrxdjo-aZh1{gZF}kb^Iq5~6D@wiMW{hfJACHYV6{b^_&Z*c~iMXl?B$pbra*$#XBUci^d1mUDUM$5_zu$UaDK5b**bOreQ6%3G>=YGZlc7Q!$jH>JfxVXvf1s6ZKB5uG#{<k~{#8I%@~*xj
wSVizD@SKImb@prI!b9F4JE;8TZ5!Z!<*<lGTjJoHXegk5;zQvW5gIY^@S)^4b%4an%O$8o4cNd(!$kf2m4j+V(ljhq#-P=Nl}g0<)z5xlG76Lu<^UL(RtJIc*{vgpbU8pc>TY0mo&Rq#hNojWY{n6YR~BQXHEgzi&RD(Q9qB@
>`BWsbTaC6Gw=jAQSNQGcg!8Z+!^8PZc+eEN5VO*(|h=zA*W691ybY<C`;p_9=+%fI#-V%4ususc65$T&x9h??e+fWilher8$5P>HEP%hf(Z!?DHenb4&DNEI1dke?BJ33+v2R*K9JVIJaF}g0Uk_fWI2K-UF^WpMB6xy_wB2+
0ohmpMh$kTB!cuT@tvEi`tSgcOMzqW0R1(1Xfgna=+KiclCE`hX}aw{`7D{)@u1OS5O*Y7ieRWbUQlWlMOj}Lh6My~XxB+@!Bh^DBQ8bSPtJw06dO=v*j1DAv)$z^8eELjAy(~zVL0hI9JDd?j8(FAk&myX2h%+NRL(bZoi?HJ
%#{JliD$5W7PO@5mhoD2xv5?1rEP(mjmzH{cRvL2V?1|LESu>W_8W+q9;4>B-mFl~y}YCNykYt)^pk#@Il(YpQv&F9-T~v^5DI2;i+C{~tjA7thmb;qDCFTA4ITg-<U<C1c7lm8%5~%DWd<u8SGh#LVR2cj@ss5JnKD6R3M-_C
xV#2cm3>Qkp+tqR@~Vat1&<(@qR`1IB`#1w!kC57eOWML>J%H!Z_w|V-D@kMZ;_*Nw!AUU!vY&O6K@KCWTUyK$>F=RGX$YJJvznPO^lbLS0hQL9N&E5VKb6r?xl=5ot%(s?gfEl4oGdxqzz4}hfv{$4n@@{<?_qvl+i{pl!`8(
$&FW&ge;`$l0~4DGbv%tC*&0?*z?N)AvoO1D7SqA-h!@j^ZX3;h&8NE^^k!&aTakpJ?t(C;G=pJ!0(CRlBoXKKU;kqS>HkhWrs)h*GN(vj18s(u0b5drq)7~s(~yw)bADx1`8jhn|iwU4NNDAH<+~Y3U<*#&c=TTFMAo#+e`K0
c=}Y_+>1L??4G8fc-D({%Q$wl<%7^!Aoq>+mRA|Z(SGU$jtQw)56E<ssFyLld1eDE$jD9_sttCf*#?mlV0}?)S3F`(dPn~_JbCx>=w<fu`0PCNo^*-W@X5?V_=**;vh{LVd!3eWUb;w2pSf*`bf%k^zcgl=^^bnGW1Kh!dMxVm
3lkZbMNr*f7D0bi)@K!&JISB0NC=1Et<(dZ4OQ_N%RxY!J$*`v-HpMCiK3q$Wf43ob983OuKZ(kQ5Pk*GGVSkEP!8><;d#A<~fRqg#8Eciy2>GDRuoN_QXtzp!Xp>7U0#YhOx*mVQ1LXoB&{GOfC(KBdpnpT%?(Ra|Fx`@LB~^
WA8r%TA9HRumhB49-h2-_N+1gJI*8&Q~M!{DtmhowL4phZ9gw-y!|7M8LJ$wuV%~3UixL)XIr=EF&_%31ipdlh?&G#bUo(BX2YkeU5Bt2SzgL+IExm60vC5ZG=Cd%mabq?9Rgmq1ufN&53p*zyWuYtCq<J@|AEIFFrfquB4UWq
({1=vo07Y=x5+5gpm8Ni!#@ld%?fHT{8KOXMpgd1u*#!1LJx0!GJIrF0qY|05rK1Wr|AkGb3H$3!boQF5M;5*%wAoWAX#s`gzRZhba}!Gc3Z9&ii%I$PAi=w$2d#MIcvI;vKk|PKO7D>h+gReXsWf$Klhp!V=%G&V~OQXBDu3T
-a!<%68DVrj2OU~UsSMn*Euj(PP+X0-;PgChI2T9FDG{<Z8^s@V{MW;qLLdoQkr2uN3!pPU#?y^GhL|!2Lu~hFO}9A${fp&-ly0}PP|TU;a4v$6qoHt8A9qYc9=W4zgR?LVP0*<*XVApA+?{v;hL(%Pq+tFRRN#FWYo%%Lr-?`
$0wQEk-BiR0(Wg{!Ku98kuKz1#3)V(a^df`UA9_<g`{^{v_^oer<`nZCw-3B6%*^Hw#`*Ujy}fr%4#ZCsKsq4@2G}S?IC1nE!by2xpzSzg~zCI;N$xHUh)%sXo1gbo+mny4k{4$HRX~$B-%4`gP-W4C5n5d=YWJiRc&0Up`mWQ
s;zW@ROY?W=)S7+L%&6i1~*Y2af8s*!$DDJjP@%SoLy87hqe{bD^CPVQ?nS|4pQ=`HE1`4nFP;o=}WFsSbdhVNtJm^z|5qHy5$X$Y|Y8y4Wr0stbjPPTdmo+$-D&kSyzje@gk3v%^Sj_C~*&GJ|}f%o+Q1zLt`X11C`@dE4OQ_
PaU?z)eL8m@hUktl@$46&L2MCYp$HBV9fY(#&SS>T@<(GSJD@x==-$<ADpJH=)Ry)PKg0YPb2w~P;un2`Z`(V2u8rVD$1c)c`;_)4kxCZm`#YL)k~Ue@@?Jfx3bX=Z(GYjwW}KrS|xVjQ<G-!#<Ok1N)hcp@=9@fV18{~A1^U6
U(uu*J$Q9c&Ogy_V{t@I-M^j!pf`Uyx9$5_3bKR#RnYaOE`i(T^|~m$)f0EE5>2k>&OzNPEDB;Ez~ZJC;0h+{q$^{Hkx+5*xs@dj*XWC;avnVfE6_His_S*rdmpst2?Fl0pFLo~jfp}N;r-W2&{I$}kXmbkJqN-vH2CMzbg2%n
r0|R7HNoJE1)=21-AJlAJ1jWk3}H?H|NM}3XLQS})p)jJEe}v%Fy`H@C2XM<X9Z2ZNG1jApzu=Ht#Wm8^%wxJABIBl-EM^?lrM+e7rRI#uQ5;Lh&GVT2)@ibRt^|{-)*<;<iJ(<#ur_4!vVr*&8xxfNw}KUg?dgP=By>k006N}
^%7NPYPM5#TC0qXp4llE|FtPZ-eV(2kYl5^Gohg^SUvsHc*?0a(YsO^tpEJXOfrOdl?oV1?OVHbI-y_MwtZdJos80$^q1hqh(c^3H3z`DA3YpGbA`F3#thnyF+IH01BTd+_UOknjPKB<-RXH=kFR@*Q~C-W_br)k+l}6RuZrEe
0>fXemzVkF?2dSyX{eg&TAQ?%JnT9=Y+sSQ!NvrYOtn~S=6KUluth}Jt47ZQUA@ll$c3FC1{Ot(E%BMhl_s5-xuI=YVc2UF7t7~5?|trdhpI?eV*-|#1}8wGga~eV9H%{TyMd%pR~u67wYgp1rz9|-U-TEWIO27>yub>#Pj-Hx
Wj8tI4Cl$)yZRcm0g?DzKnA1Q$5}h@%EL%tZb2Y_EZ3N=I}uphc%&O9=bIHm#7QXn<lWf`Fz)xI{KT(PCiDR&XenmAXK@CVCG`&L#N>9#s$4zYd^F;Vq}h9Kk7j!h0l#70?CnkA`yT2D)}4$<<DRW(keL`{&)Cp-)Ml2<Ksr07
J3%^AH!?Q0;#L<ay;)tY^GR?sqjt*`hDf!w+qmzEF<A(hYR(k2Dwhh~V*tDQRRkkxSdsMddG#<=RmIa?OdQ@2Y*nILCG@Hm=v17<%C%|@Q_lvj_)+c>KK+}m;t1FM5p^T8rl-rbUy;+g&@6ASOW14D&Bv5?DvwsfYBQadpQJR!
JK~g3M7`XZb9{e<32i^3e>6-!{t>*1ANTgS_LMbOuc`irhC{j0K=@^BU><1rgKc|ah7DD%a(nLWiDEXz+NQ>Q?4GW?cY~^1KiDnd=>I<5TMNp0P4z$63@_JRQ+-$wW=ii{>D%S2>=P|Y$PL79L<*IrMr0xLP$ELHs?8;=rEyVJ
<bN8f#ql1zos6Q6g)0o!&X${cwW$N{Fo?(n4@b|xdG^eC^|~0}j2yw$c7_uDqsWFpPg8y?8)H7_eKF)A|A@bNw=xsxn-PmNDX>(gDI^Bg6E*P*!0~^2_UxH|#{(y#^=`Gn3w~|wX8jEuuR-+V8B7UAwNRyxJO@|i(47Kd7v8qW
PrdmKKf;dt^s-z$<;70&iGKX!^A}Ig#U(I5Ly1J0!)aA%U>fPbE%yo0xt{oIY)k{>FUvDY@C@jdpiWKHneLA*_oOPf#U#@*OFGSp?s@&ud@bqcTGKM<o+(k_Yl{r&w9@?E`io9?;DM{oq2!}3z_tOT(Cvspkwz4^b^QOhh~MHa
e~v3vt>ci16qhmOAEdw<+&=_5_8vh`8@e<oyfT^Wa8*4Q>Mz9@>9+PT!xLun241gh)s_}779{cEuV22*dz_W4+rdR+=|}nz={|%w;FP$nuA83Ox`feGgDz^NhfR&SK^+sdMb<Ny3xR%0;R2?f_fXNn{E?xr51Rm%i*n2{%7)82
6|&Zn%++nom?$Nsnr;JLkT74jmB8t(m)R{dlDkgn;!)<~U`<s|iO@}lpm9{V?etPH6ABQ@sK_0pG<4XplIj11jp_<0R=rieM^NaCbIhAs&+ZO|&EyLw6mFWH7xmtW%$m7(LQb4BH2x&_CRqq-JNwsWS<{5@HK_{|Z_I;!@L@Lz
|6aF>D)Q?k42=Bz@!O%0qwx7JiQ$$)Yg*c8EJES&A3pRy{0{em|K0!|vH_s?8A^BI^PjAMTR<zqk0RHad3;n(X8f$#2UM)@&y6M^GhahcgNV*dJ6<QY1+;|V0ob46You-08;pFsURIT);XGg8V1^u#Bg?;+5cD)IB!V{zjr69M
H4q$qfR$Ert$pLFf{bXN<D70p;;})WqvyUH&L=65@TRyM&GPx>B&Vb0KK*O>4b4#wpdSK|P{hMI=3dD=4-3b#OxY*^$<c*e|3Y@*YK(a!QIO7iqPXy%AsPx0T>OY6Fv;wvT%@WHtFxn}T};NW?nX}?MHa`uu`&xx<mfMi=Px{w
)zK?njJDl$9Px^e+k?d?u{@>2E-lh0Po9itIV?sF&HVn|@xBfb%n)TR%S3!H1FY?{WBeg~p(SVQX!Q+fnfMPZ`z1zQGg(By|K;-2fY$k>%xB9h;Hz?u=EJPMLA<=p%=S9P$lDsPH0m4YNB-6Q*=!Se*7*9w3FDUC(Ex1l#V%y^
Q-84wF;#Fm(g*ute#IbdL^9A>7qGmzoHIyqhgshYFK&8)XT!RzXGJePI2LgUJY8w1=vA?t7Z{G4e)H_vC-^^DYL2Iue-}M8`y}vBs0{3<^>PeCJpJi3dwFznba9mZbo~0|o1f6!Mq$y{u&h8^8_$-OmaM8!nJ;^nSEF~w*>~R$
lFP{m+Pp5Wu4nLn4I29R1{A1j<aR;;p1>A7FI=iHYG(E^Uo+j#YfRPrW4?xU1Y7iar1~(xOh21~_wzfK7y?~#mx{>Uzz3-82Wl2Ti=SJ&sK#x9LfAo5E;a=%`%N+YJO2B=-mGSYj<^57m(}vIOUlblbtkVV9OmXwd7YppSV~6T
b6~u6r(tcFJf|XKSbhguT9DN*OEfkA^{4{Px%k%OH0}}!-SMW5Re<&+s00rqAOrTsem%?8ovH;6eUr&3{VMHP-Qj0cN7x)$Ihl#Xb~w2e&Lf}i0!skB%qjH}9~c#0I~=Kg(g*|hf?q;$&D(UhY8g?MvJvTuHJ<6N)#<b}P@BwI
i$7+kZ$PYMCkNjhootidy~=VsE5<s56^dSJw!dS$M>5NLaw-}Z4N&^^Y_|b9r;re4{xlVK3EVz&R!$Y9Z8Mr}5Xd%k()*0=r5A9P;Vo%{$zk*69BHfBrqbD~2g$30<MVSiQ}KJ817b>7YLwg(w9N^D3=i15mOhEwCi;x;!Z94<
%#>kG98Ig;hHFmqiH?mjBlY%05(gUb(2j;PCmaVLg5OAs_ui3_aUs-^P|q|}I7JT{DQ3i{ZCo()p5uNj1Oq593{z5>zzyc6hy?>mSkwL+YMO7|Gt3f~e)%2D|J5322gcCtl~<rAo3C6P{o|tbC0^xjYv-V59Cm%VrQQ0DF2$wB
2%GPnl)4nnYnL1nNikPKF9n@=p1RL9Ey<R8sd_FIo2lx~DBKydii=lk23d9MX?o`b!Ms1aZAU5`!KQZSin6kpP3%_Q9=tv}8MrP>PNVko%jJ4ftU-fZuBo4I&R!m!4IFPOPytnmKpT^Mebe+D<V&&EHTa!a<+KNkdS4HculqqE
L-d*FtA|kLeJ=%>gBR_B38{~8(C?)v7MF|Jo%#ERC<VQWa46a7yNjcj=3AYo^}9`7qf3E3BmR_T2PYT5#<cqF_){C|%Xm|@6;f0D91k<Q&2bx<U|JXm%aTtxy{NcV#l-tP#14-z?Rp0wEH}DxAeAw;3mEqTp{;9LN}UsL7hv})
ibXGkTc*$U>$^ZjB%E>60|E8#C7eAcX=5z{nN1w-hZo}+UE}OKGg?($+P$4;c?~Mc<p%LE(>>9FLE_^+e|9%?s~jjrN<7`m8DzUHCyb+&f8w*X(N|2FEC@}-Ouv!q0#{7?i(IbPZ#?-=(|c-Q`{|y`Z@+r>)iVnjD>(bD>O{l9
xr*$e;}@?#oD8br5>08Jde2#tGJCa$*15j@V`v6M4c{0Qh=)!M{Hv})21mntJ*Q=EEP47gd7;bS`Q=qZqHgDn_9e*I{%QYF7`=(3?mxl*cUz0A?6Yi)pBe@+DN5MT%BhJ|`oIBrVzR#*LpSttR!-~B>R?0NmN=`ItNF63gYwKh
IlTBYuo2%Z?6=p)_u9YE7--YZV3Qjcy~klh9^#+%;Ygr72@&?OtV(osj32AoU)5Y3Ebv*v(WBKTc`h*|+b3uDy$h6a5qwQ?``j0no|yHvn!roGY<&dz{wOlO69+l_JZM2ZhxC7SU~HFbpmpF!9_wFS7+!cHh7fx63GZ)AV);$!
0$h(@#aFZm(d%@j)W=+Ljdk)HT%Y@j(~5hY=<?=HDq^Ex>^x^`<?BUg*@+`-Z@T8>CF+DvXX>6|Q<o=$=f>Rvy){G!Tnr_ejnd0yeT`S8pH?|2<Sc0owIH7mimB5G)zWv0nYDgOsrkDRZtu{(Ar0^{$E=Z#^*yd)KzFet@2Urn
_=|efOD}F-vUTy*l2C`A{|VtJma|FfHo?6?C$&*JE2eeoc30$`sQl6K^H_Pa;x+P?YtR~B=Zh=TCF+t+S!O$Y?&>*`$!uld-FlDGL#jAYc5mn0CX9(Af%NP2B5Giabl0OFP3R|oLW2{jhCyBbn6FDb<c*x$tA1dTo|w1dYfiM=
%zd(8H~w?FD_<6zC+zicLw(wBV65i4(?d?G(q4tUmqX;XJ9GSGlX2>`iECWx4OoU-%ipg)I?rDN;lA!nxb!stl)NF|qi*Aqw}THHqj9;|I8LjXrm-TAIAc|IZP`8ZPJMVTzFeU139%$}WSy@@h_pMQm<-T}My7#-_oC4s-Hz&(
Viv8>wnnn0(5}WW#g=8e6dQL!<=Wa*x7?1!HefPSMH>xkfot>UA?qCTgA>xRE@ovh9i`J*UdOLnjH&o|nXh-458pBe47|Cz)+74f!L26CT0600Wc<F%AX;|%*lXe*y5kjc=Xws?>M?}+e6_4P=pM3QV5GbOpjp)00QkB-V;>l$
o-tw;*XJx|v2?$Q*7M&h_cva-##A&iJuqN&V>0RxoO?zGDvxa|y!ec;zhBAuH5?80B2_JlTXdc7UfYA5L;PwUwzlK-bYI)7U<MRBx%T>QQFBJ|Mk%m`2bVcbQ&jyHl}iogyr8R8OO2)q+`Q~=)wbg4s@IrgqjMh)v9#L>uA*G5
yxA}-539f57@Cc|L3b|g*~1aD2}uWI_6gK5PywS)P-PJsk7tE--(Zo}OPS!)^!+`yzWtEg%Y?2Xv~>H=#XlEmzkAb4*^0kY6$Q6!Ql%<@r#ID|AO?A(ar}CW0K##MN<PGKEE5(pgO7Smv)hr%vC~|fQfMTiQ3!weaJ|8eF5Z(G
{a>n#3Sj(ITWd`R6t_LQ_Ew#Lr`Pq_bchb}<YR`uF+C8vNxe*XZu{Oaq<f|2sKE5buPPNs*_I)h;C`Bt?_$!spNWeq*YB{u<vxX;i7rwVX+tc%Y>fqBT-s@ZlFXvl)M-cYg0Z$Rq3D{O!!k+z&etP?7tmXJA@YzKXIz4{4n(5M
Hw7!WZyTq_SPjB-r$H4k_!xd{LtYSvd~)~FXX0eOsp{mipqS>XVvV654CoA1TJR5IxELvb#!Yh&e)Pc3&7)5w0pb|_2>EmmHQB|dXemu%<C~s-WAkdfMKA;m11a{>=fh_yA=%rq+7)rTuPet5eh4}W+1>+L<vdHIU*soD9?SXU
bhs2=f}p^_WcAf`!r?L?I+7CS0rl^M#vxPG8E_zjhHweTzAv3c5&Z=8YD4U6L`QSJjDT{-%OCXx?D#0)A;yH{hQe{FcCXCLUh+jJv6lgMqDLOa`U_1~!OR{o6NB&)J=MLKEDH{dr3Uu@xwu!)w$1JuUKhi-e-G^<gH3YZ3{1}*
duWh*lmus66Mt7XPVHo!Y!<xmCc`vv7i%nEz5vDTvyFHC{r3E}tT}I^PFH1|^<^AWrXo)yy0G6PE;0ybAd5kU$Zbj&G+bi6hoQ5mzkwCESuhsH8H~5}9MK;_s|@TsLGrm#9?i`85NS6}U*#w?P_|VxSymD{n{`opzlWZ0cadpI
y-;u%2E<lRQPZD|Htz-TT7kO0c)9lwqPO@x>lGvEBVC}UigMHmlKacqL^9oe!nEqgf~*{d35Q!L0yV~|gwsPl+R3GjFL$G(kUL1Y`^SjzFat*(qrU(8KKK#ot#vLl)U8pCYhGlguM=q8%ezn`mTgTO$T<{XA48G-G;pq^#s}w4
DQq+i49GN5iMITv1U%io0d`C_0}-i1(Z{zXW(kh;FzRX``o}NRQ6z<GK`&dAqY{0Y<yD;q{R)(@iZ~RsHV@jvbzY&Dl;}S<S&SL3mmS(tjaDl9=@sck@~iuq1j7T@6<!q)ABu*t>Q;PBK}O_Z47(kX$4$d;@Gnt-<{b#Y4^mFl
YpJW^6``^Dda!O)hHdj34LcJpH<Glj2boQYW-91fC`+-?W%<PDGtH*_ov>jSCcRolBwp-$XAlY7VobJ&5)f;~t;1Pc8m86@t2fKFMT-k<zTusiJCM~m!|U`h*w&q~mM(_gGD4na<?3>ouP0e>gLle^DYaVL(NVNbz`C~^o#nZ)
B}sEJ|3$HA8HHPAynQj!xd&1Y5*2hAwDU&XFd-&cn2`}0Dx`nyx1OH@8XMkmHEUxcDLg0wt{xzFZ)e<wEswH8iETEJ`uxiQ2U|}92@O0Vh?34A=Xpu0&?t!-JHv0t^*K%N)o<Hmj;0oFDIs@QLd6=UjNK_4Gfbo)<w0k<ERq7+
x-;G(o6a!ZT?s>)VI!g!R84Yk_q&tM34#|dhl2M}`U=Co*+&JJuF>mj5ve-7pictrNVtn}F;v+Gh&Wu0WUBZ6AerRFe7P8f>Kzh8m@H!IVG;gc=eMGSl%~dFCmqPN{7KWSyL@yl%`UK@%$5@nL`%Af&~GX1+7awoPcQ4+loEk>
&v}RfK@7_!##E_c==a|LY5#-lGle0U9pK!ss;me6+hIJ0Rg-pdv;O@?!%Wbw>Tjs(^s%{|m$j~3a6~`mFdJZ4q^i%aJD8si>N+1^!=fR=^FEXajBfUy5v#Hv*<PbUx3eE``1&Ha*|%I4>})DTI1H$z-#WORX1c{L?pg|jk`S82
=mfH)!#vK4)%!ck&A)4LNj8(sB3Yn>Z@LA8vn~bU_k%{tX3Is4uJ)!UN2B*dAvz0KEMiN%DHRB1m%DTW%4CSwBt3jzz1k17g>Zh}?%U-y4G4X!ovM@}jhI|Y{KVQ3a9HhDvLyK*(3SjZo+I{ZLKcu@Pg=H;@82DhvD=P-1!b}%
|K3z!3?fV<ImE#cXg$yCtdQ*lmO+?wDJ`aSAZzb8rSyta!9+?2f?7ruQ%p-YyX#cT1ZDykUusRDfol-k9&fKP-y80>9b}ZnDso=U&LE)SVu{NlEVH=O5$ZZ^=O-aNuu0l9#afLBIY;Z1-28xDAoyxAGrNLt9O(`l6P52&vtWiD
>dq?!O3W!fp+f`AushT*IlP&oUH6y{JtR(!ZjVy!3}DrpyNwH!6towie0NntXFFOgSG{N993&lOe1mW|+Ls5JkfZD_kR?)7H%8ClNno~IqL%yQi3B)?b9yg;JBO$;u*c=I%#i(d0u86FHC;GGpx9JLY5(fJbFJ3sQGvc&_>SSb
rGjE91mEhZ^D_5K?>~0w0rkjBT=qo2?{af;cyc^UF78%E@<s8ftiPa3s(QJS%0Ni6dQw%fANwq%-zI@Gh};L?yb6VJQtxc0^DI%$A*4hUL+mte8UNbA-|haBR#R5!yx^fpsy*MOw?$`XZp79n*pcuOv-x<XA?f!ib(O;yjH?Ju
aR3{<knvA$4i<ruii?9p8{#025Z<<2rn1phzOD*NJJExGKJF*yXqQ-wi$Ka4>IamfcZRQX;6>^ra|`~6y;21ZL{6Qz^u)$#4Z{p_9m9Mw$z(zvObMcIsRt7GYD54z*Trl#GVZWwnI|H6t&&F==`C_A+{}~<z_7VlLhmw~l;KJg
sU#aeM;cEUdyMhSXgwgj75r7eS4~YRrjTv!5x(fa2#tf598{4LmE=ecb6S3aj{N;ki3UR(Ca36<v|1v7Efh-~FtJbs)~n-_qt^$gM_Zqe_eQa|2NyqNN3Va(sH|7BahsgTxFS6HQ@5(8%?<0y1kg(3G!fKVbh3_4zdL$qPnia7
<5@#|wyyKTOA3=h8S^9Cv=cOiW=#X3y@4(F;CK(6vGW?Ezmv<nDlmi9&?BZf4~lXpCkLkohi~6z-yNJEW$@k>>3gq!K0iA9@#qX6=xN%8vgW9%+hXi3eEHVkNb|MCNNdzQKz9TVr@<icmjrh5o6TxRURiABd*d1%w`<tIMkQWF
xg7KQ-D!4s@ej!@($|lpl-aL<EVp#=Qsm=nygJ7;gtJ*!F2ZgIA3lSes!JYYnO%E(cJ%Vt3^?I#o+1>*W}YxIc_MJA&EBFPq~^a+=XYU=dH!jys*6?CX#gM&GTiV^5B`yzUmU$XR}(r&@VARMZ%+6@-x=6cjf@v7o+%P)c$mC{
1w;OuIq0-k1jQ`;g{PFBbXLNiTTE<0cJKFrSB~9}Z96%D3ZOmW33kkz8fZc50!|T|LhVhM;scigXt8rjKTqj)rnp%lY$4*mFQ-)E;sy`!En5;R6IShc!ubqPFOzB&S}ak`>B^6=6H^k%z3Y69VMBX2g$YU)8lO8$--{}sKd%a?
q(&I>qyim_L_bc*{SOCcuaC~pv%ekvtZ6^M(Jw@Ici})#Qr35?TK0LT{`MRJbi{RO=_<R*>_Hg|)w845hi5;(y?|}SF6Bx!&b~w&XK>_6Xp^+av6E~gIlBkiU^k9rgw4~#w^E-ZYZ57kl5NBdoM_L{6r}=*r>c&sqgG)l&DkjN
s&pM&DRC=4*KU@5$H}$Xlg-hH1@6kEtX8x9&MIlvCQosE7VJIg<k_gfUPshp`tCkPh0S=($?9NjEUTgD4BgGd6fHwKMj&iQ4VsntGER90YRpI_amr8u!JnAmx=5gZO<G3Z=p^ko;&ft+-TYY>b}@6gtnsUz@0m6MK}-2nmyoEq
^jv(gpGst0RYLq=E;USRjE|*&%!IJXK)AUF?NFIs_+%}s6(UT&p=%TmFk2aCGo%FUnf_V45z4wLZ3aKB=`KvZl89Gm547vwrj5N+=hA(ff9#`bV->JZA{X^2pS;aV>C5Buw<iZb3kRs`t{TGH#NgSvbtyQ5<Mt;jakXu}aV<7H
ukN*o(YLEj34~Qy2`t!rct*!&6mlizDL{VAGBVU<8H&6t6UI7{fBh%_552ZS)5aYG00
""".replace("\n", ""))
).decode("utf-8")

UNSLOTH_GEMMA_MODEL_ALIAS = "unsloth/gemma-4-31B-it-qat-GGUF"
UNSLOTH_GEMMA_GGUF_REPO = "unsloth/gemma-4-31B-it-qat-GGUF"
UNSLOTH_GEMMA_GGUF_FILE = "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf"
UNSLOTH_GEMMA_GGUF_LABEL = "Unsloth-Gemma-4-31B-It-QAT-UD-Q4_K_XL"
UNSLOTH_GEMMA_DEFAULT_NUM_CTX = 262144


def _patch_gemma_model_defaults(base: Any) -> None:
    base.DEFAULT_MODEL = UNSLOTH_GEMMA_MODEL_ALIAS
    base.DEFAULT_HF_GGUF_REPO_ID = UNSLOTH_GEMMA_GGUF_REPO
    base.Q4_GGUF_MODEL_FILENAME = UNSLOTH_GEMMA_GGUF_FILE
    base.DEFAULT_GGUF_MODEL_LABEL = UNSLOTH_GEMMA_GGUF_LABEL
    base.DEFAULT_GGUF_MODEL_FILENAME = UNSLOTH_GEMMA_GGUF_FILE
    base.DEFAULT_NUM_CTX = UNSLOTH_GEMMA_DEFAULT_NUM_CTX

    def _resolve_gguf_model_path(
        workspace: Path,
        configured_path: str | None = None,
        preferred_filename: str = UNSLOTH_GEMMA_GGUF_FILE,
    ) -> Path | None:
        candidate_text = (configured_path or os.environ.get(getattr(base, "GGUF_MODEL_PATH_ENV_NAME", "")) or "").strip()
        if candidate_text:
            candidate_name = Path(candidate_text).name
            if candidate_name != preferred_filename:
                raise ValueError(
                    f"Configured GGUF model must be {preferred_filename}, got {candidate_name!r}."
                )
            return base.resolve_workspace_path(
                workspace,
                candidate_text,
                allow_missing=False,
                allow_external=True,
            )
        preferred = [
            workspace / "models" / preferred_filename,
            workspace / preferred_filename,
            workspace / ".cache" / "models" / "unsloth" / "gemma-4-31B-it-qat-GGUF" / preferred_filename,
        ]
        search_roots = [
            workspace / "models",
            workspace / ".cache" / "models",
            workspace,
        ]
        seen: set[Path] = set()
        for candidate in preferred:
            if candidate.exists():
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    return resolved
        for root in search_roots:
            if not root.exists():
                continue
            for candidate in sorted(root.rglob(preferred_filename)):
                if base.path_has_excluded_dir(candidate, base.EXCLUDED_DIRS):
                    continue
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    return resolved
        return None

    base.resolve_gguf_model_path = _resolve_gguf_model_path

    def _download_default_gguf_model(
        workspace: Path,
        filename: str = UNSLOTH_GEMMA_GGUF_FILE,
    ) -> Path:
        if os.environ.get("QUBITZ_OFFLINE") == "1":
            raise RuntimeError("Automatic GGUF download is disabled because QUBITZ_OFFLINE=1.")
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise RuntimeError(
                "Automatic GGUF download requires the 'huggingface_hub' package in the active runtime."
            ) from exc
        local_dir = workspace / ".cache" / "models" / "unsloth" / "gemma-4-31B-it-qat-GGUF"
        local_dir.mkdir(parents=True, exist_ok=True)
        downloaded = hf_hub_download(
            repo_id=UNSLOTH_GEMMA_GGUF_REPO,
            filename=filename,
            local_dir=local_dir,
        )
        return Path(downloaded).resolve()

    base.download_default_gguf_model = _download_default_gguf_model

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
    excluded_dirs.add(".ump")
    base.EXCLUDED_DIRS = excluded_dirs
    base.load_harness_text = _load_harness_text


class LocalOnlyApp:
    def __init__(self, base_module_name: str, wrapper_script: str, display_name: str) -> None:
        self.base = _load_embedded_base_module()
        self.wrapper_script = Path(wrapper_script).resolve()
        self.runtime_workspace = self.wrapper_script.parent.resolve()
        self.display_name = display_name
        _patch_gemma_model_defaults(self.base)
        _patch_local_only_dependencies(self.base)
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
                if self._should_bypass_embedding_retrieval(prompt):
                    configured = int(getattr(self.config, "max_steps", getattr(base, "MAX_TOOL_STEPS", 0)))
                    if configured <= 0:
                        return SIMPLE_DIRECT_QUESTION_STEP_CAP
                    return min(configured, SIMPLE_DIRECT_QUESTION_STEP_CAP)
                return self._effective_max_steps()

            def _adaptive_step_budget(self, prompt: str) -> tuple[int, list[str]]:
                original = int(getattr(self.config, "max_steps", getattr(base, "MAX_TOOL_STEPS", 0)))
                setattr(self.config, "max_steps", self._effective_prompt_max_steps(prompt))
                try:
                    return super()._adaptive_step_budget(prompt)
                finally:
                    setattr(self.config, "max_steps", original)

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
                if _resolve_existing_entrypoint_spec(base, self.workspace, cleaned) is None:
                    return False
                return any(hint in lowered for hint in FOREGROUND_EXISTING_SCRIPT_HINTS) or _prompt_has_explicit_entrypoint_command(cleaned)

            def _resolve_existing_entrypoint_for_prompt(self, prompt: str) -> dict[str, Any] | None:
                return _resolve_existing_entrypoint_spec(base, self.workspace, prompt)

            def _try_direct_existing_script_completion(
                self,
                prompt: str,
                callback: Callable[[str, str], None] | None,
            ) -> str | None:
                entrypoint = self._resolve_existing_entrypoint_for_prompt(prompt)
                if entrypoint is None:
                    return None
                expected_count = _expected_result_count(prompt)
                requested_fields = _requested_output_fields(prompt)
                requested_browser_open = _prompt_requests_browser_open(prompt)
                before_helpers = {
                    str(candidate.resolve()): candidate.stat().st_mtime
                    for candidate in _collect_browser_helper_candidates(self.workspace)
                }
                entrypoint_label = str(entrypoint.get("label", "") or "entrypoint")
                self._emit(
                    callback,
                    "status",
                    f"Wrapper direct path: running existing entrypoint {entrypoint_label}.",
                )
                try:
                    entrypoint_kind = str(entrypoint.get("kind", ""))
                    if entrypoint_kind == "file":
                        script_path = Path(str(entrypoint["path"]))
                        suffix = script_path.suffix.lower()
                        if suffix == ".py":
                            direct_python = _select_direct_workspace_python(base, self.workspace)
                            if direct_python is None:
                                self._emit(
                                    callback,
                                    "status",
                                    (
                                        "Wrapper direct path: no compatible workspace-local Python interpreter was found "
                                        "for this session; falling back to the model loop."
                                    ),
                                )
                                return None
                            interpreter_path, launch_mode = direct_python
                            self._emit(
                                callback,
                                "status",
                                (
                                    "Wrapper direct path: using "
                                    f"{base.relative_path(interpreter_path, self.workspace)} "
                                    f"through the {launch_mode} execution path."
                                ),
                            )
                            if launch_mode == "powershell":
                                command = (
                                    f"& {_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, interpreter_path))} "
                                    f"{_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, script_path))}"
                                )
                                result = _run_powershell_command(
                                    base,
                                    self.workspace,
                                    command,
                                    DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                                )
                            else:
                                relative_text = str(base.relative_path(script_path, self.workspace))
                                command = f"{_shell_quote_path(interpreter_path)} {_shell_quote_path(relative_text)}"
                                result = _run_shell_command(
                                    base,
                                    self.workspace,
                                    command,
                                    DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                                )
                        elif suffix == ".ps1":
                            command = f"& {_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, script_path))}"
                            result = _run_powershell_command(
                                base,
                                self.workspace,
                                command,
                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                            )
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
                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                            )
                        elif suffix in {".bat", ".cmd"}:
                            command = _workspace_relative_command_path(base, self.workspace, script_path)
                            result = _run_shell_command(
                                base,
                                self.workspace,
                                command,
                                DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                            )
                        else:
                            return None
                    elif entrypoint_kind in {"uv_run", "package_script", "make_target"}:
                        command = str(entrypoint.get("command", "")).strip()
                        if not command:
                            return None
                        result = _run_shell_command(
                            base,
                            self.workspace,
                            command,
                            DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                        )
                    else:
                        return None
                except Exception as exc:
                    self._emit(
                        callback,
                        "status",
                        (
                            f"Wrapper direct path: existing entrypoint {entrypoint_label} failed before completion "
                            f"({type(exc).__name__}: {exc}); falling back to the model loop."
                        ),
                    )
                    return None
                return_code = int(result.get("return_code", result.get("returncode", 0)))
                if return_code != 0:
                    self._emit(
                        callback,
                        "status",
                        f"Wrapper direct path: {entrypoint_label} exited {return_code}; falling back to the model loop.",
                    )
                    return None
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
                if not browser_helper_text:
                    with suppress(Exception):
                        if browser_helper_path is not None:
                            browser_helper_text = browser_helper_path.read_text(encoding="utf-8", errors="ignore")
                browser_urls = _extract_start_process_urls(browser_helper_text)
                rows = _extract_structured_result_rows(f"{result.get('stderr', '')}\n{result.get('stdout', '')}")
                target_count = expected_count or len(rows)
                if target_count <= 0 or len(rows) < target_count:
                    self._emit(
                        callback,
                        "status",
                        (
                            "Wrapper direct path: extracted incomplete structured results "
                            f"({len(rows)} row(s), expected {target_count}); falling back to the model loop."
                        ),
                    )
                    return None
                completed_rows: list[dict[str, Any]] = []
                for index, row in enumerate(rows[:target_count]):
                    merged = _normalize_result_row(dict(row))
                    if not str(merged.get("url", "")).strip() and index < len(browser_urls):
                        merged["url"] = browser_urls[index]
                    completed_rows.append(merged)
                requires_url = requested_browser_open or "url" in requested_fields
                if requires_url and any(not str(row.get("url", "")).strip() for row in completed_rows):
                    self._emit(
                        callback,
                        "status",
                        (
                            "Wrapper direct path: extracted incomplete structured results "
                            f"({len(completed_rows)} row(s), {len(browser_urls)} helper URL(s), expected {target_count}); "
                            "falling back to the model loop."
                        ),
                    )
                    return None
                if requested_fields and not _rows_have_required_fields(completed_rows, requested_fields):
                    self._emit(
                        callback,
                        "status",
                        (
                            "Wrapper direct path: structured rows did not satisfy the requested fields "
                            f"{requested_fields}; falling back to the model loop."
                        ),
                    )
                    return None
                browser_open_ran = False
                if requested_browser_open and (
                    self._wsl_windows_interop_available()
                    or not (callable(getattr(base, "in_wsl", None)) and base.in_wsl())
                ):
                    if browser_helper_path is not None:
                        helper_command = f"& {_powershell_single_quote(_workspace_relative_command_path(base, self.workspace, browser_helper_path))}"
                        browser_result = _run_powershell_command(
                            base,
                            self.workspace,
                            helper_command,
                            DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                        )
                    else:
                        browser_result = _run_inline_browser_open(
                            base,
                            self.workspace,
                            [str(row.get("url", "")).strip() for row in completed_rows],
                            DIRECT_SCRIPT_COMPLETION_TIMEOUT_SECONDS,
                        )
                    browser_open_ran = int(browser_result.get("return_code", browser_result.get("returncode", 0))) == 0
                helper_label = (
                    str(base.relative_path(browser_helper_path, self.workspace))
                    if browser_helper_path is not None
                    else "inline Start-Process"
                )
                self._emit(
                    callback,
                    "status",
                    (
                        "Wrapper direct path completed the existing-script task without entering the model loop. "
                        f"Rows: {len(completed_rows)}. Browser step: {helper_label}."
                    ),
                )
                return _format_direct_result_answer(
                    completed_rows,
                    browser_open_ran=browser_open_ran,
                    requested_browser_open=requested_browser_open,
                    requested_fields=requested_fields,
                )

            def _wsl_windows_interop_unavailable(self) -> bool:
                capabilities = self._runtime_capabilities()
                return bool(capabilities.get("in_wsl") and not capabilities.get("windows_interop_available"))

            def _wsl_windows_interop_available(self) -> bool:
                capabilities = self._runtime_capabilities()
                return bool(capabilities.get("in_wsl") and capabilities.get("windows_interop_available"))

            def _runtime_capabilities(self) -> dict[str, Any]:
                return _workspace_runtime_capabilities(base, self.workspace)

            def _task_class_label(self, prompt: str) -> str:
                if self._should_bypass_embedding_retrieval(prompt):
                    return "simple_direct_question"
                if self._is_foreground_existing_script_task(prompt):
                    return "foreground_existing_script_task"
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
                    f"- Runtime: {'wsl' if capabilities.get('in_wsl') else 'non_wsl'}",
                    f"- Workspace kind: {'windows_backed' if capabilities.get('workspace_is_windows_backed') else 'wsl_native'}",
                    f"- Windows executable interop: {interop_state}",
                    f"- Workspace WSL python: {'present' if capabilities.get('workspace_has_wsl_python') else 'absent'}",
                    f"- Workspace Windows python: {'present' if capabilities.get('workspace_has_windows_python') else 'absent'}",
                    f"- Preferred project python: {preferred_python}",
                    f"- Preferred project runner: {capabilities.get('preferred_python_runner', 'none') or 'none'}",
                    f"- Preferred llama runtime: {self._preferred_llama_runtime_label(capabilities)}",
                    "- These runtime facts are authoritative for this task. Do not override or re-decide them.",
                ]
                return "\n".join(lines)

            def _select_task_guidance(self, prompt: str) -> str:
                if self._should_bypass_embedding_retrieval(prompt):
                    return (
                        "- This is a simple direct question. Answer directly and do not use tools unless they are "
                        "truly necessary.\n"
                    )
                if self._is_foreground_existing_script_task(prompt):
                    capabilities = self._runtime_capabilities()
                    guidance = [
                        "- This is a foreground active-workspace task that asks you to use existing project functionality.",
                        "- Prefer the shortest existing local execution path first.",
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
                            "- Keep background-job tools, sandbox tools, and auxiliary MCP-management tools as fallbacks unless the direct path fails or the task clearly needs them.",
                            "- Do not create a temporary helper script when an existing workspace entrypoint already covers the task unless the direct entrypoint path has already failed.",
                            "- Do not modify or replace an existing workspace entrypoint just to make the task work unless the user explicitly asked for that.",
                            "- Do not claim the task is complete unless you actually have the requested final result in hand.",
                            "- If the task requires a structured list, verify the final answer includes the requested fields for every returned item.",
                        ]
                    )
                    return "\n".join(guidance)
                return ""

            def _prioritize_tools_for_prompt(self, prompt: str, tools: Sequence[Any]) -> list[Any]:
                if self._should_bypass_embedding_retrieval(prompt):
                    return []
                if not self._is_foreground_existing_script_task(prompt):
                    return list(tools)
                interop_available = self._wsl_windows_interop_available()
                interop_unavailable = self._wsl_windows_interop_unavailable()
                direct_names = {
                    "read_file",
                    "list_files",
                    "search_text",
                    "run_project_command",
                    "read_memory",
                    "search_memory",
                }
                if interop_available or not (callable(getattr(base, "in_wsl", None)) and base.in_wsl()):
                    direct_names.add("run_powershell_command")
                auxiliary_mcp_names = {
                    "start_project_mcp_server",
                    "list_project_mcp_tools",
                    "stop_project_mcp_server",
                    "read_project_mcp_server_log",
                }
                background_names = {
                    "list_background_jobs",
                    "read_background_job",
                    "cancel_background_job",
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
                    name = str(getattr(tool, "name", "") or "")
                    if interop_unavailable and (
                        name in {
                            "run_powershell_command",
                            "start_project_mcp_server",
                            "list_project_mcp_tools",
                            "stop_project_mcp_server",
                            "read_project_mcp_server_log",
                        }
                        or name.startswith("sandbox_run_powershell")
                    ):
                        continue
                    if name in mutation_names:
                        continue
                    if name in direct_names:
                        bucket = 0
                    elif name in background_names or name.startswith("sandbox_") or name == "list_sandboxes":
                        bucket = 3
                    elif name in auxiliary_mcp_names:
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
                self._ump_store = (
                    qubitz_ump.LocalUMPStore(
                        self.runtime_workspace,
                        self.workspace,
                        projection_path=self.runtime_workspace / ".memory" / base.CURRENT_MEMORY_NAME,
                        agent_name=wrapper_script.stem,
                    )
                    if qubitz_ump is not None
                    else None
                )
                self._ump_context = ""
                self._local_plugin_context = "None"
                self._task_case_guidance = ""
                self._simple_direct_mode = False
                self._runtime_fact_context = ""

            async def _run_async(self, prompt: str, callback: Callable[[str, str], None] | None = None) -> str:
                self._local_plugin_context = (
                    self.local_plugins.render_for_prompt(prompt) if self.local_only_config.plugins_enabled else "None"
                )
                self._task_case_guidance = self._select_task_guidance(prompt)
                self._runtime_fact_context = self._runtime_fact_block(prompt)
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
                self._emit(
                    callback,
                    "status",
                    f"Thinking effort preset: {_display_thinking_effort(getattr(self.config, 'thinking_effort', 'default'))}.",
                )
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
                bypass_retrieval = self._should_bypass_embedding_retrieval(prompt)
                foreground_existing_script_task = self._is_foreground_existing_script_task(prompt)
                self._ump_context = ""
                if not bypass_retrieval and not foreground_existing_script_task and self._ump_store is not None:
                    self._ump_context = self._ump_store.render_summary(
                        query=prompt,
                        limit=max(3, getattr(base, "MAX_MEMORY_CONTEXT_RESULTS", 2) * 3),
                        max_chars=getattr(base, "MAX_MEMORY_CONTEXT_CHARS", 1200),
                        project_only=True,
                    )
                    if self._ump_context:
                        self._emit(
                            callback,
                            "status",
                            "Loaded scoped local memory context from the runtime-root UMP store.",
                        )
                if foreground_existing_script_task:
                    direct_answer = self._try_direct_existing_script_completion(prompt, callback)
                    if direct_answer is not None:
                        self._ump_context = ""
                        self._local_plugin_context = "None"
                        self._task_case_guidance = ""
                        self._runtime_fact_context = ""
                        return direct_answer
                original_format_context = None
                original_should_skip_repo_retrieval = self._should_skip_repo_retrieval
                original_memory_build_context = self.memory.build_context
                original_skills = self.skills
                original_emit = self._emit
                original_cache = self._tool_definitions_cache
                original_count = self._tool_count_cache
                original_list_tools = base.MCPHost.list_tools
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
                    self._emit(
                        callback,
                        "status",
                        "Simple direct question detected; skipping repository retrieval, embedding generation, and MCP tool loading.",
                    )
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
                    self._emit(
                        callback,
                        "status",
                        "Foreground existing-entrypoint task detected; prioritizing direct workspace tools and keeping wrapper tools as fallbacks.",
                    )
                    self._tool_definitions_cache = None
                    self._tool_count_cache = 0

                async def _prompt_aware_list_tools(host_self: Any) -> list[Any]:
                    tools = await original_list_tools(host_self)
                    return self._prioritize_tools_for_prompt(prompt, tools)

                if bypass_retrieval or foreground_existing_script_task:
                    base.MCPHost.list_tools = _prompt_aware_list_tools
                try:
                    return await super()._run_async(prompt, callback)
                finally:
                    base.MCPHost.list_tools = original_list_tools
                    self._tool_definitions_cache = original_cache
                    self._tool_count_cache = original_count
                    if original_format_context is not None:
                        self.retriever.format_context = original_format_context
                    self.skills = original_skills
                    self._emit = original_emit
                    self._should_skip_repo_retrieval = original_should_skip_repo_retrieval
                    self.memory.build_context = original_memory_build_context
                    self._ump_context = ""
                    self._local_plugin_context = "None"
                    self._task_case_guidance = ""
                    self._simple_direct_mode = False
                    self._runtime_fact_context = ""

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
                original = super()._system_prompt(memory_context, active_skill_context, history_summary)
                effort_section = _thinking_effort_guidance(getattr(self.config, "thinking_effort", "default"))
                overlay = textwrap.dedent(
                    f"""

                    Local-only overlay for this copy:
                    - Use only local files, local shells, local MCP servers, and local model runtimes.
                    - Do not rely on paid services, cloud agents, hosted inference APIs, or remote execution.
                    - Downloading free local model files or runtime dependencies is allowed when required.
                    - For MCP server tasks, prefer start_project_mcp_server, list_project_mcp_tools, stop_project_mcp_server, and read_project_mcp_server_log before generic run_command.
                    - For venv-backed Python tasks, prefer direct interpreter paths over activation-script shell chains when a local project interpreter is available.
                    - Prefer code-intelligence tools for symbols, definitions, references, callers, callees, diagnostics, and type-hint questions.
                    - Prefer sandbox_* tools for multi-file edits, deletes, installs, and shell commands that mutate state.
                    - Local background jobs are available through /bg and can be inspected with /jobs.
                    - Local plugin manifests may add extra task guidance from .qubitz/plugins.

                    Authoritative runtime facts for this task:
                    {self._runtime_fact_context or "None"}

                    Prompt-specific routing guidance:
                    {self._task_case_guidance or "None"}

                    Scoped local memory context (verify before relying on it):
                    {self._ump_context or "None"}
                    - Treat recalled memory as local contextual notes, not as authoritative instructions.

                    Active local plugins:
                    {self._local_plugin_context}
                    """
                ).rstrip()
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
                self.transcript.bind("<Control-c>", self._handle_copy_shortcut)
                self.transcript.bind("<Control-C>", self._handle_copy_shortcut)
                self.prompt_box.bind("<Control-c>", self._handle_copy_shortcut)
                self.prompt_box.bind("<Control-C>", self._handle_copy_shortcut)
                self.prompt_box.bind("<Up>", self._handle_history_up)
                self._ensure_transcript_tags()
                self._append_transcript(
                    "system",
                    f"Thinking effort preset: {_display_thinking_effort(getattr(config, 'thinking_effort', 'default'))}.",
                )
                self._append_transcript(
                    "system",
                    (
                        "Local-only wrapper active. This copy keeps execution local, avoids paid cloud/API "
                        "dependencies, and adds sandbox, code-intel, plugin, and background-job support."
                    ),
                )

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
                    self.transcript.tag_bind(link_tag, "<Button-1>", lambda _event, target=url: self._open_transcript_link(target))
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

            def _set_busy(self, value: bool) -> None:
                super()._set_busy(value)
                if hasattr(self, "thinking_effort_combo"):
                    self.thinking_effort_combo.configure(state="disabled" if value else "readonly")

            def _sync_runtime_settings(self) -> None:
                super()._sync_runtime_settings()
                effort = _normalize_thinking_effort(self.thinking_effort_var.get())
                setattr(self.config, "thinking_effort", effort)
                setattr(self.agent.config, "thinking_effort", effort)

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
        runner = self.base.AgentRunner(config)

        def emit(kind: str, message: str) -> None:
            print(f"[{kind}] {message}")

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
                print(json.dumps(meta, ensure_ascii=False, indent=2))
                return
            if initial_prompt.lower() in {"/jobs", "/bg-jobs"}:
                print(json.dumps(runner.background_jobs.list_jobs(), ensure_ascii=False, indent=2))
                return
            print(runner.run_sync(initial_prompt, emit))
            return
        print(f"{self.display_name} CLI. Type 'exit' to stop.")
        while True:
            try:
                prompt = input("> ").strip()
            except EOFError:
                print()
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
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                continue
            if prompt.lower() in {"/jobs", "/bg-jobs"}:
                print(json.dumps(runner.background_jobs.list_jobs(), ensure_ascii=False, indent=2))
                continue
            answer = runner.run_sync(prompt, emit)
            print(answer)

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
    'AI_Agent_Qubitz_Embedding_Local',
    __file__,
    'AI Agent Qubitz Gemma 4 31B It Qat Embd Local-Only',
)

parse_args = _APP.parse_args
run_cli = _APP.run_cli
serve_mcp = _APP.serve_mcp
main = _APP.main


if __name__ == "__main__":
    main()
