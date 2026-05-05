from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import traceback
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from mcp import ClientSession, StdioServerParameters, types as mcp_types
from mcp.client.stdio import stdio_client
from mcp.server.fastmcp import FastMCP


DEFAULT_MODEL = "glm-4.7-flash:latest"
DEFAULT_EMBED_MODEL = "BAAI/bge-code-v1"
CURRENT_MEMORY_NAME = "MEMORY.md"
ARCHIVE_MEMORY_PREFIX = "MEMORY_"
HISTORY_TURNS = 6
MAX_TOOL_STEPS = 16
MAX_TOOL_RESULT_CHARS = 6000
MAX_DIRECT_READ_FILES = 4
MAX_DIRECT_READ_CHARS = 12000
DEFAULT_NUM_PREDICT = 4096
MAX_HISTORY_SUMMARY_CHARS = 6000
OLLAMA_GPU_ENV_KEYS = (
    "OLLAMA_FLASH_ATTENTION",
    "OLLAMA_KV_CACHE_TYPE",
    "OLLAMA_NUM_PARALLEL",
    "OLLAMA_MAX_LOADED_MODELS",
)
QUERY_INSTRUCTION = (
    "Given a repository task or question, retrieve code and project files that help solve it."
)
READ_INTENT_PATTERN = re.compile(
    r"\b(read|open|show|view|inspect|examine|review|check|display|print|cat|look\s+at)\b",
    re.IGNORECASE,
)
FILE_TOKEN_PATTERN = re.compile(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'|(?<!\w)([A-Za-z0-9_.\-\\/]+(?:\.[A-Za-z0-9_]+))(?!\w)")
TEXT_SUFFIXES = {
    ".bat",
    ".c",
    ".cfg",
    ".cpp",
    ".css",
    ".csv",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
EXCLUDED_DIRS = {
    ".cache",
    ".git",
    ".memory",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "models",
    "node_modules",
}
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_COMMANDS = {
    ".venv/bin/python",
    "git",
    "ls",
    "nvidia-smi",
    "ollama",
    "pwd",
    "py",
    "pytest",
    "python",
    "python3",
    "rg",
    "ruff",
    "sed",
    "tail",
    "uv",
}
UI_BG = "#141416"
UI_PANEL = "#1b1c1f"
UI_PANEL_ALT = "#23252a"
UI_TEXT = "#ffffff"
UI_TEXT_MUTED = "#d7d7d9"
UI_BORDER = "#2d3036"
UI_SELECT = "#3c4048"


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def token_set(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:-]+", text.lower()))


def shorten(text: str, limit: int = 1200) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def format_bytes(value: int | float | None) -> str:
    if value is None:
        return "unknown"
    size = float(value)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} TiB"


def gib_to_bytes(value: float) -> int:
    return int(max(0.0, value) * 1024**3)


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def is_sensitive_path_name(path: Path) -> bool:
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    return path.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}


def extract_file_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for match in FILE_TOKEN_PATTERN.finditer(text):
        candidate = next((group for group in match.groups() if group), "")
        candidate = candidate.strip().strip("()[]{}<>.,:;")
        if not candidate:
            continue
        if candidate.startswith(("http://", "https://")):
            continue
        tokens.append(candidate)
    return tokens


def describe_tool_action(name: str, arguments: dict[str, Any]) -> str:
    if name == "read_file":
        path = arguments.get("path", "?")
        start_line = arguments.get("start_line", 1)
        end_line = arguments.get("end_line", 200)
        return f"Reading {path} lines {start_line}-{end_line}"
    if name == "write_file":
        return f"Writing {arguments.get('path', '?')}"
    if name == "replace_text":
        return f"Modifying {arguments.get('path', '?')}"
    if name == "delete_path":
        return f"Deleting {arguments.get('path', '?')}"
    if name == "make_directory":
        return f"Creating directory {arguments.get('path', '?')}"
    if name == "move_path":
        return f"Moving {arguments.get('source', '?')} -> {arguments.get('destination', '?')}"
    if name == "search_text":
        return f"Searching text for {arguments.get('query', '')!r}"
    if name == "list_files":
        return f"Listing files under {arguments.get('path', '.')}"
    if name == "install_python_package":
        if arguments.get("requirements_file"):
            return f"Installing Python dependencies from {arguments['requirements_file']}"
        return f"Installing Python packages {arguments.get('packages', [])}"
    if name == "run_project_command":
        return f"Running command {arguments.get('command', [])}"
    if name == "read_memory":
        return "Reading persistent memory"
    if name == "search_memory":
        return f"Searching memory for {arguments.get('query', '')!r}"
    return f"Calling tool {name}"


def summarize_tool_result(name: str, payload: dict[str, Any]) -> str:
    if payload.get("is_error"):
        return f"{name} failed: {shorten(payload.get('content_text', 'tool error'), 240)}"
    structured = payload.get("structured_content") or {}
    if name == "read_file":
        return (
            f"Loaded {structured.get('path', '?')} "
            f"lines {structured.get('start_line', '?')}-{structured.get('end_line', '?')}"
        )
    if name == "write_file":
        return f"Wrote {structured.get('path', '?')}"
    if name == "replace_text":
        return f"Updated {structured.get('path', '?')} with {structured.get('replacements', 0)} replacements"
    if name == "delete_path":
        return f"Deleted {structured.get('deleted', '?')}"
    if name == "make_directory":
        return f"Created {structured.get('path', '?')}"
    if name == "move_path":
        return f"Moved to {structured.get('destination', '?')}"
    if name == "search_text":
        matches = structured.get("matches") or []
        return f"Search found {len(matches)} matches"
    if name == "list_files":
        entries = structured.get("entries") or []
        return f"Listed {len(entries)} entries"
    if name == "install_python_package":
        return f"Install command exited with code {structured.get('return_code', '?')}"
    if name == "run_project_command":
        return f"Command exited with code {structured.get('return_code', '?')}"
    return shorten(payload.get("content_text", f"{name} finished"), 240)


def in_wsl() -> bool:
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lowered = version_text.lower()
    return "microsoft" in lowered or "wsl" in lowered


def configure_project_environment(workspace: Path) -> None:
    cache_root = workspace / ".cache"
    hf_home = cache_root / "huggingface"
    os.environ.setdefault("HF_HOME", str(hf_home))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub"))
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    if os.environ.get("QUBITZ_OFFLINE") == "1":
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    cache_root.mkdir(parents=True, exist_ok=True)
    hf_home.mkdir(parents=True, exist_ok=True)


def ensure_display_environment() -> None:
    if not in_wsl():
        return
    if os.environ.get("DISPLAY"):
        return
    os.environ["DISPLAY"] = ":0"


def configure_tk_environment() -> None:
    if getattr(sys, "frozen", False):
        base_path = Path(getattr(sys, "_MEIPASS"))
    else:
        base_path = Path(sys.prefix or getattr(sys, "base_prefix", sys.executable))
        if not (base_path / "tcl").exists() and getattr(sys, "base_prefix", None):
            base_path = Path(sys.base_prefix)
    os.environ["TCL_LIBRARY"] = str(base_path / "tcl" / "tcl8.6")
    os.environ["TK_LIBRARY"] = str(base_path / "tcl" / "tk8.6")


def import_tk_modules():
    ensure_display_environment()
    configure_tk_environment()
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk

    return tk, ttk, scrolledtext, messagebox


def relative_path(path: Path, workspace: Path) -> str:
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def resolve_workspace_path(
    workspace: Path,
    candidate: str,
    *,
    allow_missing: bool = True,
) -> Path:
    raw = Path(candidate)
    resolved = (workspace / raw).resolve() if not raw.is_absolute() else raw.resolve()
    if not resolved.is_relative_to(workspace.resolve()):
        raise ValueError(f"Path escapes workspace: {candidate}")
    if not allow_missing and not resolved.exists():
        raise FileNotFoundError(candidate)
    return resolved


def is_probably_text_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return True
    try:
        sample = path.read_bytes()[:2048]
    except OSError:
        return False
    return b"\x00" not in sample


def serialize_mcp_result(result: mcp_types.CallToolResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "is_error": bool(result.isError),
        "structured_content": result.structuredContent,
        "content": [],
    }
    for item in result.content:
        if hasattr(item, "text"):
            payload["content"].append(item.text)
        else:
            payload["content"].append(item.model_dump())
    payload["content_text"] = shorten(
        "\n".join(part for part in payload["content"] if isinstance(part, str)),
        MAX_TOOL_RESULT_CHARS,
    )
    return payload


def build_ollama_tools(tools: Sequence[mcp_types.Tool]) -> list[dict[str, Any]]:
    ollama_tools: list[dict[str, Any]] = []
    for tool in tools:
        parameters = tool.inputSchema or {"type": "object", "properties": {}}
        ollama_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": parameters,
                },
            }
        )
    return ollama_tools


def clean_arguments(arguments: Any) -> dict[str, Any]:
    if arguments is None:
        return {}
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"raw": arguments}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    return {"value": arguments}


def default_embed_device() -> str:
    return os.environ.get("QUBITZ_EMBED_DEVICE", "auto").lower()


def default_local_files_only() -> bool:
    return os.environ.get("QUBITZ_ALLOW_EMBED_ONLINE", "").strip() != "1"


@dataclass
class AgentConfig:
    workspace: Path
    model_name: str = DEFAULT_MODEL
    embed_model_name: str = DEFAULT_EMBED_MODEL
    max_steps: int = MAX_TOOL_STEPS
    ollama_keep_alive: str = "30m"
    ollama_num_ctx: int = 16384
    ollama_num_predict: int = DEFAULT_NUM_PREDICT
    ollama_temperature: float = 0.0
    local_files_only: bool = field(default_factory=default_local_files_only)
    embed_device: str = field(default_factory=default_embed_device)
    max_repo_chunks: int = 4
    embed_min_free_vram_gib: float = field(default_factory=lambda: env_float("QUBITZ_EMBED_MIN_FREE_VRAM_GIB", 4.0))
    retrieval_gpu_reserve_gib: float = field(default_factory=lambda: env_float("QUBITZ_RETRIEVAL_GPU_RESERVE_GIB", 1.0))


def strip_yaml_scalar(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        return cleaned[1:-1]
    return cleaned


def parse_skill_frontmatter(frontmatter: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    current_map_key: str | None = None
    for raw_line in frontmatter.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[:1].isspace():
            if current_map_key is None or not isinstance(parsed.get(current_map_key), dict):
                continue
            nested = raw_line.strip()
            key, separator, value = nested.partition(":")
            if not separator:
                raise ValueError(f"Invalid nested frontmatter line: {raw_line}")
            parsed[current_map_key][key.strip()] = strip_yaml_scalar(value)
            continue
        key, separator, value = raw_line.partition(":")
        if not separator:
            raise ValueError(f"Invalid frontmatter line: {raw_line}")
        normalized_key = key.strip()
        cleaned_value = value.strip()
        if normalized_key == "metadata":
            parsed[normalized_key] = {}
            current_map_key = normalized_key
            if cleaned_value:
                raise ValueError("metadata must be a mapping block, not an inline scalar.")
            continue
        parsed[normalized_key] = strip_yaml_scalar(cleaned_value)
        current_map_key = None
    return parsed


@dataclass
class SkillDefinition:
    name: str
    description: str
    root: Path
    skill_file: Path
    body: str
    license: str = ""
    compatibility: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    allowed_tools: str = ""

    def to_summary(self, workspace: Path) -> dict[str, Any]:
        resource_paths: list[str] = []
        for folder in ("scripts", "references", "assets"):
            base = self.root / folder
            if not base.exists():
                continue
            for candidate in sorted(base.rglob("*")):
                if candidate.is_file():
                    resource_paths.append(relative_path(candidate, workspace))
        return {
            "name": self.name,
            "description": self.description,
            "license": self.license or None,
            "compatibility": self.compatibility or None,
            "allowed_tools": self.allowed_tools or None,
            "metadata": self.metadata,
            "root": relative_path(self.root, workspace),
            "skill_file": relative_path(self.skill_file, workspace),
            "resources": resource_paths,
        }


class SkillRegistry:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.skills_root = workspace / ".skills"
        self.skills: dict[str, SkillDefinition] = {}
        self.warnings: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.skills = {}
        self.warnings = []
        if not self.skills_root.exists():
            return
        for skill_dir in sorted(self.skills_root.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            try:
                skill = self._load_skill(skill_dir, skill_file)
            except Exception as exc:
                self.warnings.append(f"{relative_path(skill_file, self.workspace)}: {type(exc).__name__}: {exc}")
                continue
            self.skills[skill.name] = skill

    def _load_skill(self, skill_dir: Path, skill_file: Path) -> SkillDefinition:
        text = skill_file.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---\n") and text != "---":
            raise ValueError("SKILL.md must start with YAML frontmatter delimited by ---")
        lines = text.splitlines()
        closing_index: int | None = None
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                closing_index = index
                break
        if closing_index is None:
            raise ValueError("SKILL.md frontmatter is missing a closing --- delimiter")
        frontmatter = "\n".join(lines[1:closing_index])
        body = "\n".join(lines[closing_index + 1 :]).strip()
        parsed = parse_skill_frontmatter(frontmatter)
        name = str(parsed.get("name", "")).strip()
        description = str(parsed.get("description", "")).strip()
        license_value = str(parsed.get("license", "")).strip()
        compatibility = str(parsed.get("compatibility", "")).strip()
        allowed_tools = str(parsed.get("allowed-tools", "")).strip()
        metadata = parsed.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a mapping")
        if not name or len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):
            raise ValueError("name must match the Agent Skills lowercase-hyphen naming rules")
        if name != skill_dir.name:
            raise ValueError("name must match the parent skill directory name")
        if not description or len(description) > 1024:
            raise ValueError("description must be 1-1024 characters")
        if compatibility and len(compatibility) > 500:
            raise ValueError("compatibility must be 500 characters or fewer")
        normalized_metadata = {str(key): str(value) for key, value in metadata.items()}
        return SkillDefinition(
            name=name,
            description=description,
            root=skill_dir,
            skill_file=skill_file,
            body=body,
            license=license_value,
            compatibility=compatibility,
            metadata=normalized_metadata,
            allowed_tools=allowed_tools,
        )

    def count(self) -> int:
        return len(self.skills)

    def list_summaries(self) -> list[dict[str, Any]]:
        return [skill.to_summary(self.workspace) for skill in self.skills.values()]

    def get(self, skill_name: str) -> SkillDefinition:
        normalized = skill_name.strip().lower()
        if normalized in self.skills:
            return self.skills[normalized]
        raise KeyError(skill_name)

    def _resolve_skill_resource(self, skill_name: str, resource_path: str) -> tuple[SkillDefinition, Path]:
        skill = self.get(skill_name)
        target = (skill.root / Path(resource_path)).resolve()
        if not target.is_relative_to(skill.root.resolve()):
            raise ValueError("Skill resource path escapes the skill root")
        if not target.exists():
            raise FileNotFoundError(resource_path)
        return skill, target

    def read_skill_resource(self, skill_name: str, resource_path: str) -> dict[str, Any]:
        skill, target = self._resolve_skill_resource(skill_name, resource_path)
        if target.is_dir():
            entries = [relative_path(candidate, self.workspace) for candidate in sorted(target.iterdir())]
            return {
                "skill": skill.name,
                "path": relative_path(target, self.workspace),
                "is_dir": True,
                "entries": entries,
            }
        if not is_probably_text_file(target):
            return {
                "skill": skill.name,
                "path": relative_path(target, self.workspace),
                "is_dir": False,
                "binary": True,
                "size": target.stat().st_size,
            }
        text = target.read_text(encoding="utf-8", errors="ignore")
        return {
            "skill": skill.name,
            "path": relative_path(target, self.workspace),
            "is_dir": False,
            "binary": False,
            "content": shorten(text, 12000),
        }

    def select_for_prompt(self, prompt: str, max_results: int = 3) -> list[SkillDefinition]:
        prompt_lower = prompt.lower()
        prompt_tokens = token_set(prompt)
        ranked: list[tuple[int, str, SkillDefinition]] = []
        for skill in self.skills.values():
            score = 0
            aliases = {
                skill.name,
                skill.name.replace("-", " "),
                skill.name.replace("-", "_"),
            }
            if any(alias and alias in prompt_lower for alias in aliases):
                score += 50
            skill_tokens = token_set(f"{skill.name.replace('-', ' ')} {skill.description}")
            overlap = prompt_tokens & skill_tokens
            score += len(overlap) * 3
            if skill.compatibility:
                score += len(prompt_tokens & token_set(skill.compatibility))
            if score <= 0:
                continue
            ranked.append((score, skill.name, skill))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [skill for _, _, skill in ranked[:max_results]]

    def render_active_context(self, active_skills: Sequence[SkillDefinition]) -> str:
        if not active_skills:
            return "None"
        sections: list[str] = []
        for skill in active_skills:
            sections.append(
                textwrap.dedent(
                    f"""
                    Skill: {skill.name}
                    Description: {skill.description}
                    Compatibility: {skill.compatibility or "None"}
                    Allowed tools: {skill.allowed_tools or "None"}
                    Root: {relative_path(skill.root, self.workspace)}

                    Instructions:
                    {skill.body or "No body content provided."}
                    """
                ).strip()
            )
        return "\n\n".join(sections)

class MemoryStore:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = workspace / ".memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.current_path = self.memory_dir / CURRENT_MEMORY_NAME
        self.archive_path = self.memory_dir / f"{ARCHIVE_MEMORY_PREFIX}{self.session_id}.md"
        self.notes: list[dict[str, str]] = []
        self.turns: list[dict[str, str]] = []
        self.flush()

    def add_turn(self, role: str, content: str) -> None:
        self.turns.append(
            {
                "timestamp": now_stamp(),
                "role": role,
                "content": shorten(content.strip(), 4000),
            }
        )
        self.turns = self.turns[-20:]
        self.flush()

    def add_note(self, note: str, category: str = "note") -> None:
        cleaned = note.strip()
        if not cleaned:
            return
        self.notes.append({"timestamp": now_stamp(), "category": category, "text": shorten(cleaned, 1000)})
        self.notes = self.notes[-40:]
        self.flush()

    def render(self) -> str:
        lines = [
            "# Qubitz Memory",
            "",
            f"Session ID: {self.session_id}",
            f"Updated: {now_stamp()}",
            f"Workspace: {self.workspace.as_posix()}",
            "",
            "## Notes",
        ]
        if self.notes:
            for note in self.notes[-20:]:
                lines.append(f"- [{note['timestamp']}] ({note['category']}) {note['text']}")
        else:
            lines.append("- No notes recorded yet.")
        lines.extend(["", "## Recent Turns"])
        if self.turns:
            for turn in self.turns[-12:]:
                lines.append(f"### {turn['role'].title()} [{turn['timestamp']}]")
                lines.append(turn["content"])
                lines.append("")
        else:
            lines.append("No conversation turns recorded yet.")
        return "\n".join(lines).rstrip() + "\n"

    def flush(self) -> None:
        rendered = self.render()
        self.current_path.write_text(rendered, encoding="utf-8")
        self.archive_path.write_text(rendered, encoding="utf-8")

    def search(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        query_tokens = token_set(query)
        results: list[dict[str, str]] = []
        memory_files = sorted(
            self.memory_dir.glob(f"{ARCHIVE_MEMORY_PREFIX}*.md"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if self.current_path.exists():
            memory_files.insert(0, self.current_path)
        seen: set[Path] = set()
        for path in memory_files:
            if path in seen or not path.exists():
                continue
            seen.add(path)
            text = path.read_text(encoding="utf-8", errors="ignore")
            lowered = text.lower()
            if query_tokens:
                score = sum(lowered.count(token) for token in query_tokens)
            else:
                score = 1
            if score <= 0:
                continue
            results.append(
                {
                    "path": relative_path(path, self.workspace),
                    "score": str(score),
                    "snippet": shorten(text, 1200),
                }
            )
        return results[:limit]

    def build_context(self, query: str) -> str:
        current = self.current_path.read_text(encoding="utf-8", errors="ignore") if self.current_path.exists() else ""
        archived = self.search(query, limit=3)
        sections = ["Current session memory:", shorten(current, 5000)]
        if archived:
            sections.append("Relevant archived memory:")
            for item in archived:
                sections.append(f"- {item['path']} (score {item['score']}): {item['snippet']}")
        return "\n\n".join(section for section in sections if section.strip())


@dataclass
class RepoChunk:
    chunk_id: str
    path: str
    start_line: int
    end_line: int
    text: str


class BGECodeEmbedder:
    def __init__(
        self,
        workspace: Path,
        model_name: str,
        *,
        device: str = "auto",
        local_files_only: bool = False,
        min_free_vram_gib: float = 4.0,
        reserve_vram_gib: float = 1.0,
        progress_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.workspace = workspace
        self.model_name = model_name
        self.requested_device = device.lower()
        self.min_free_vram_bytes = gib_to_bytes(min_free_vram_gib)
        self.reserve_vram_bytes = gib_to_bytes(reserve_vram_gib)
        self.device = "cpu"
        self.local_files_only = local_files_only
        self.progress_callback = progress_callback
        self._loaded = False
        self._released_from_gpu = False
        self._torch: Any = None
        self._torch_f: Any = None
        self._tokenizer: Any = None
        self._model: Any = None
        self.uses_flash_attention = False
        self.uses_xformers = False
        self.load_warning: str | None = None

    def _report(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    @staticmethod
    def _cuda_memory_snapshot(torch: Any) -> tuple[int | None, int | None]:
        if not torch.cuda.is_available():
            return None, None
        try:
            return torch.cuda.mem_get_info()
        except Exception:
            return None, None

    def _resolve_device(self, torch: Any) -> str:
        if self.requested_device == "auto":
            if not torch.cuda.is_available():
                return "cpu"
            free_bytes, _ = self._cuda_memory_snapshot(torch)
            if free_bytes is not None and free_bytes < self.min_free_vram_bytes:
                self.load_warning = (
                    f"Free CUDA VRAM {format_bytes(free_bytes)} is below the embedder threshold "
                    f"{format_bytes(self.min_free_vram_bytes)}; using CPU fallback."
                )
                self._report(self.load_warning)
                return "cpu"
            return "cuda"
        return self.requested_device

    @staticmethod
    def _has_flash_attention() -> bool:
        try:
            import flash_attn  # noqa: F401
        except Exception:
            return False
        return True

    @staticmethod
    def _has_xformers() -> bool:
        try:
            import xformers  # noqa: F401
        except Exception:
            return False
        return True

    def _load_model(self, auto_model_cls: Any, load_kwargs: dict[str, Any]) -> Any:
        return auto_model_cls.from_pretrained(self.model_name, **load_kwargs)

    def _enable_optional_attention_kernels(self) -> None:
        if self.device != "cuda":
            return
        if self.uses_flash_attention:
            return
        if self._has_xformers() and hasattr(self._model, "enable_xformers_memory_efficient_attention"):
            try:
                self._model.enable_xformers_memory_efficient_attention()
                self.uses_xformers = True
                self._report("xFormers memory-efficient attention enabled for the embedder.")
            except Exception as exc:
                self._report(f"xFormers was detected but not enabled: {type(exc).__name__}: {exc}")

    def _cuda_batch_size(self, text_count: int) -> int:
        if self.device != "cuda":
            return 2
        torch = self._torch
        free_bytes, _ = self._cuda_memory_snapshot(torch)
        if free_bytes is None:
            return 1
        free_bytes = max(0, free_bytes - self.reserve_vram_bytes)
        if text_count <= 1:
            return 1
        if free_bytes >= 12 * 1024**3:
            return 4
        if free_bytes >= 7 * 1024**3:
            return 2
        return 1

    def _report_cuda_device(self) -> None:
        try:
            props = self._torch.cuda.get_device_properties(0)
            self._report(
                f"Embedder active on CUDA device 0: {props.name} with {format_bytes(props.total_memory)} total VRAM."
            )
        except Exception:
            self._report("Embedder active on CUDA device 0.")

    def _activate_cuda_model(self) -> None:
        assert self._torch is not None
        assert self._model is not None
        self._model.to("cuda")
        self.device = "cuda"
        self._released_from_gpu = False
        self.load_warning = None
        self._torch.backends.cuda.matmul.allow_tf32 = True
        self._torch.backends.cudnn.allow_tf32 = True
        self._enable_optional_attention_kernels()
        self._report_cuda_device()

    def load(self) -> None:
        if self._loaded and not self._released_from_gpu:
            return
        if self._loaded and self._released_from_gpu:
            assert self._torch is not None
            target_device = self._resolve_device(self._torch)
            if target_device != "cuda":
                self._report("Keeping the embedder on CPU for this retrieval to preserve GPU headroom.")
                self.device = "cpu"
                return
            try:
                self._report("Restoring the embedder to CUDA for retrieval.")
                self._activate_cuda_model()
                return
            except Exception as exc:
                self.load_warning = f"CUDA embedder restore failed, keeping CPU copy: {type(exc).__name__}: {exc}"
                self._report(self.load_warning)
                self.device = "cpu"
                return
        configure_project_environment(self.workspace)
        import torch
        import torch.nn.functional as torch_f
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self._torch_f = torch_f
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_name,
                trust_remote_code=True,
                local_files_only=self.local_files_only,
            )
        except Exception as exc:
            if not self.local_files_only:
                raise
            raise RuntimeError(
                "Offline embedder load failed because required local Hugging Face files are missing. "
                "Prefetch the embedder into .cache/huggingface or set QUBITZ_ALLOW_EMBED_ONLINE=1."
            ) from exc
        base_load_kwargs: dict[str, Any] = {
            "trust_remote_code": True,
            "local_files_only": self.local_files_only,
            "low_cpu_mem_usage": True,
        }
        target_device = self._resolve_device(torch)
        cache_mode = "local cache only" if self.local_files_only else "network allowed"
        self._report(f"Loading embedder {self.model_name} on {target_device} ({cache_mode}).")
        load_kwargs = dict(base_load_kwargs)
        if target_device == "cuda":
            load_kwargs["dtype"] = torch.float16
            if self._has_flash_attention():
                load_kwargs["attn_implementation"] = "flash_attention_2"
                self._report("FlashAttention2 is available for the embedder.")
        try:
            self._model = self._load_model(AutoModel, load_kwargs)
            self.device = target_device
        except Exception as exc:
            if target_device != "cuda":
                raise
            self.load_warning = f"CUDA embedder load failed, falling back to CPU: {type(exc).__name__}: {exc}"
            self._report(self.load_warning)
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            self._model = self._load_model(AutoModel, base_load_kwargs)
            self.device = "cpu"
            load_kwargs = dict(base_load_kwargs)
        self._model.eval()
        if self.device == "cuda":
            self._activate_cuda_model()
        else:
            self._report("Embedder active on CPU.")
        self.uses_flash_attention = load_kwargs.get("attn_implementation") == "flash_attention_2"
        self._loaded = True

    def release_gpu(self) -> bool:
        if not self._loaded or self._model is None or self.device != "cuda":
            return False
        self._report(
            f"Releasing embedder GPU memory before generation and preserving {format_bytes(self.reserve_vram_bytes)} of headroom."
        )
        try:
            self._model.to("cpu")
        except Exception:
            self._model = None
            self._loaded = False
        self.device = "cpu"
        self._released_from_gpu = True
        torch = self._torch
        if torch is not None and torch.cuda.is_available():
            gc.collect()
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            try:
                torch.cuda.ipc_collect()
            except Exception:
                pass
            free_bytes, total_bytes = self._cuda_memory_snapshot(torch)
            if free_bytes is not None and total_bytes is not None:
                self._report(
                    f"Embedder GPU memory released. CUDA free VRAM now {format_bytes(free_bytes)} of {format_bytes(total_bytes)}."
                )
            else:
                self._report("Embedder GPU memory released.")
        return True

    def _last_token_pool(self, last_hidden_states: Any, attention_mask: Any) -> Any:
        torch = self._torch
        left_padding = attention_mask[:, -1].sum() == attention_mask.shape[0]
        if bool(left_padding):
            return last_hidden_states[:, -1]
        sequence_lengths = attention_mask.sum(dim=1) - 1
        batch_size = last_hidden_states.shape[0]
        return last_hidden_states[torch.arange(batch_size, device=last_hidden_states.device), sequence_lengths]

    def _encode(self, texts: Sequence[str], *, prompt: str | None = None) -> Any:
        self.load()
        import numpy as np

        torch = self._torch
        encoded_batches: list[Any] = []
        batch_size = self._cuda_batch_size(len(texts))
        kind = "query" if prompt else "document"
        self._report(f"Encoding {len(texts)} {kind} chunk(s) on {self.device} with batch size {batch_size}.")
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            if prompt:
                batch = [f"{prompt}{text}" for text in batch]
            tokenize_kwargs: dict[str, Any] = {
                "max_length": 2048,
                "padding": True,
                "return_tensors": "pt",
                "truncation": True,
            }
            if self.device == "cuda":
                tokenize_kwargs["pad_to_multiple_of"] = 8
            batch_dict = self._tokenizer(batch, **tokenize_kwargs)
            batch_dict = {
                key: value.to(self.device) if hasattr(value, "to") else value
                for key, value in batch_dict.items()
            }
            with torch.inference_mode():
                outputs = self._model(**batch_dict)
                embeddings = self._last_token_pool(outputs.last_hidden_state, batch_dict["attention_mask"])
                embeddings = self._torch_f.normalize(embeddings, p=2, dim=1)
            encoded_batches.append(embeddings.detach().cpu().float().numpy())
        return np.vstack(encoded_batches) if encoded_batches else np.empty((0, 0), dtype="float32")

    def encode_queries(self, texts: Sequence[str]) -> Any:
        prompt = f"<instruct>{QUERY_INSTRUCTION}\n<query>"
        return self._encode(texts, prompt=prompt)

    def encode_documents(self, texts: Sequence[str]) -> Any:
        return self._encode(texts)


class RepoRetriever:
    def __init__(self, config: AgentConfig, progress_callback: Callable[[str], None] | None = None) -> None:
        self.config = config
        self.workspace = config.workspace
        self.progress_callback = progress_callback
        self.cache_dir = self.workspace / ".cache" / "retrieval"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.cache_dir / "manifest.json"
        self.vectors_path = self.cache_dir / "vectors.npy"
        self.embedder = BGECodeEmbedder(
            self.workspace,
            config.embed_model_name,
            device=config.embed_device,
            local_files_only=config.local_files_only,
            min_free_vram_gib=config.embed_min_free_vram_gib,
            reserve_vram_gib=config.retrieval_gpu_reserve_gib,
            progress_callback=self._report,
        )
        self._chunks: list[RepoChunk] = []
        self._vectors: Any = None
        self._backend = "lexical"
        self._last_error: str | None = None
        self._faiss: Any = None
        self._faiss_gpu_resources: Any = None
        self._faiss_index: Any = None

    def _report(self, message: str) -> None:
        if self.progress_callback is not None:
            self.progress_callback(message)

    def _iter_files(self) -> Iterable[Path]:
        for path in self.workspace.rglob("*"):
            if not path.is_file():
                continue
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if path.name.endswith(".bak"):
                continue
            if not is_probably_text_file(path):
                continue
            yield path

    def _chunk_file(self, path: Path) -> list[RepoChunk]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        lines = text.splitlines()
        if not lines:
            return []
        chunks: list[RepoChunk] = []
        chunk_size = 80
        overlap = 20
        start = 0
        rel = relative_path(path, self.workspace)
        while start < len(lines):
            end = min(len(lines), start + chunk_size)
            chunk_lines = lines[start:end]
            chunk_text = "\n".join(chunk_lines).strip()
            if chunk_text:
                chunk_id = f"{rel}:{start + 1}-{end}"
                chunks.append(
                    RepoChunk(
                        chunk_id=chunk_id,
                        path=rel,
                        start_line=start + 1,
                        end_line=end,
                        text=chunk_text,
                    )
                )
            if end >= len(lines):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def _file_state(self) -> dict[str, dict[str, int]]:
        state: dict[str, dict[str, int]] = {}
        for path in self._iter_files():
            stat = path.stat()
            state[relative_path(path, self.workspace)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
        return state

    def _build_chunks(self) -> list[RepoChunk]:
        chunks: list[RepoChunk] = []
        files = sorted(self._iter_files())
        self._report(f"Scanning {len(files)} workspace file(s) for retrieval chunks.")
        for path in files:
            chunks.extend(self._chunk_file(path))
        self._report(f"Built {len(chunks)} retrieval chunk(s).")
        return chunks

    def _load_cached(self, state: dict[str, dict[str, int]]) -> bool:
        if not self.manifest_path.exists():
            return False
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        if manifest.get("file_state") != state:
            return False
        chunk_dicts = manifest.get("chunks") or []
        self._chunks = [RepoChunk(**chunk_dict) for chunk_dict in chunk_dicts]
        if self.vectors_path.exists():
            import numpy as np

            self._vectors = np.load(self.vectors_path, allow_pickle=False)
            self._backend = manifest.get("backend", "embedding")
            self._report(
                f"Loaded cached retrieval vectors for {len(self._chunks)} chunk(s) from {relative_path(self.vectors_path, self.workspace)}."
            )
        else:
            self._vectors = None
            self._backend = "lexical"
        return True

    def _save_cached(self, state: dict[str, dict[str, int]]) -> None:
        manifest = {
            "file_state": state,
            "chunks": [asdict(chunk) for chunk in self._chunks],
            "backend": self._backend,
        }
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
        if self._vectors is not None:
            import numpy as np

            np.save(self.vectors_path, self._vectors, allow_pickle=False)
        elif self.vectors_path.exists():
            self.vectors_path.unlink()

    def _reset_faiss_index(self) -> None:
        self._faiss_index = None
        self._faiss_gpu_resources = None

    def _ensure_faiss_index(self) -> bool:
        if self._vectors is None:
            return False
        if self._faiss_index is not None:
            return True
        try:
            import faiss
        except Exception as exc:  # pragma: no cover - optional dependency path
            self._last_error = f"{type(exc).__name__}: {exc}"
            return False
        try:
            cpu_index = faiss.IndexFlatIP(int(self._vectors.shape[1]))
            cpu_index.add(self._vectors)
            backend = "faiss-cpu"
            gpu_count = faiss.get_num_gpus()
            if gpu_count > 0 and hasattr(faiss, "StandardGpuResources") and hasattr(faiss, "index_cpu_to_gpu"):
                try:
                    self._faiss_gpu_resources = faiss.StandardGpuResources()
                    self._faiss_index = faiss.index_cpu_to_gpu(self._faiss_gpu_resources, 0, cpu_index)
                    backend = "faiss-gpu"
                except Exception as exc:
                    self._last_error = f"{type(exc).__name__}: {exc}"
                    self._faiss_gpu_resources = None
                    self._faiss_index = cpu_index
                    backend = "faiss-cpu"
                    self._report(f"FAISS GPU index allocation failed, falling back to CPU FAISS: {self._last_error}")
            else:
                self._faiss_index = cpu_index
            self._faiss = faiss
            self._backend = backend
            self._report(f"Retrieval index ready with backend {backend} over {len(self._chunks)} chunk(s).")
            return True
        except Exception as exc:  # pragma: no cover - runtime fallback path
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._reset_faiss_index()
            return False

    def ensure_index(self) -> None:
        state = self._file_state()
        if self._load_cached(state):
            self._reset_faiss_index()
            self._ensure_faiss_index()
            return
        self._report("Retrieval cache is stale or missing. Rebuilding repository index.")
        self._chunks = self._build_chunks()
        self._vectors = None
        self._backend = "lexical"
        self._last_error = None
        self._reset_faiss_index()
        if self._chunks:
            try:
                self._report("Generating repository embeddings.")
                self._vectors = self.embedder.encode_documents([chunk.text for chunk in self._chunks])
                self._backend = "embedding-numpy"
                if self.embedder.load_warning:
                    self._last_error = self.embedder.load_warning
                self._ensure_faiss_index()
            except Exception as exc:  # pragma: no cover - runtime fallback path
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._vectors = None
                self._backend = "lexical"
        self._save_cached(state)

    def _lexical_scores(self, query: str) -> list[tuple[float, RepoChunk]]:
        query_tokens = token_set(query)
        scored: list[tuple[float, RepoChunk]] = []
        for chunk in self._chunks:
            lowered = chunk.text.lower()
            score = float(sum(lowered.count(token) for token in query_tokens))
            if any(token in chunk.path.lower() for token in query_tokens):
                score += 2.0
            if score > 0:
                scored.append((score, chunk))
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored

    def query(self, query: str, limit: int | None = None) -> dict[str, Any]:
        import numpy as np

        self.ensure_index()
        top_k = limit or self.config.max_repo_chunks
        results: list[dict[str, Any]] = []
        if not self._chunks:
            return {"backend": "empty", "results": [], "error": self._last_error}
        if self._vectors is not None:
            try:
                self._report(f"Retrieving repository context for query: {shorten(query, 100)}")
                query_vector = self.embedder.encode_queries([query])[0]
                if self._ensure_faiss_index():
                    scores_array, indices_array = self._faiss_index.search(
                        np.ascontiguousarray(query_vector.reshape(1, -1), dtype="float32"),
                        top_k,
                    )
                    top_indices = [int(index) for index in indices_array[0] if int(index) >= 0]
                    score_map = {
                        int(index): float(score)
                        for index, score in zip(indices_array[0], scores_array[0], strict=False)
                        if int(index) >= 0
                    }
                else:
                    scores = self._vectors @ query_vector
                    top_indices = [int(index) for index in np.argsort(scores)[::-1][:top_k]]
                    score_map = {int(index): float(scores[int(index)]) for index in top_indices}
                for index in top_indices:
                    chunk = self._chunks[int(index)]
                    results.append(
                        {
                            "path": chunk.path,
                            "start_line": chunk.start_line,
                            "end_line": chunk.end_line,
                            "score": score_map[int(index)],
                            "text": shorten(chunk.text, 1400),
                        }
                    )
            except Exception as exc:  # pragma: no cover - runtime fallback path
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._reset_faiss_index()
                if self._vectors is not None:
                    scores = self._vectors @ query_vector
                    top_indices = [int(index) for index in np.argsort(scores)[::-1][:top_k]]
                    self._backend = "embedding-numpy"
                    for index in top_indices:
                        chunk = self._chunks[index]
                        results.append(
                            {
                                "path": chunk.path,
                                "start_line": chunk.start_line,
                                "end_line": chunk.end_line,
                                "score": float(scores[index]),
                                "text": shorten(chunk.text, 1400),
                            }
                        )
        if not results:
            for score, chunk in self._lexical_scores(query)[:top_k]:
                results.append(
                    {
                        "path": chunk.path,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "score": score,
                        "text": shorten(chunk.text, 1400),
                    }
                )
        if results:
            preview = ", ".join(f"{item['path']}:{item['start_line']}-{item['end_line']}" for item in results[:3])
            self._report(f"Repository context ready from {self._backend}: {preview}")
        return {"backend": self._backend, "results": results, "error": self._last_error}

    def format_context(self, query: str) -> str:
        query_result = self.query(query)
        lines = [f"Repository retrieval backend: {query_result['backend']}"]
        if query_result.get("error"):
            lines.append(f"Retrieval warning: {query_result['error']}")
        if not query_result["results"]:
            lines.append("No repository context was retrieved.")
            return "\n".join(lines)
        for item in query_result["results"]:
            lines.append(
                f"- {item['path']}:{item['start_line']}-{item['end_line']} score={item['score']:.3f}\n"
                f"  {item['text']}"
            )
        return "\n".join(lines)

    def release_gpu_resources(self) -> None:
        released: list[str] = []
        if self._faiss_index is not None and self._backend == "faiss-gpu":
            released.append("FAISS GPU index")
            self._backend = "embedding-numpy" if self._vectors is not None else self._backend
        self._reset_faiss_index()
        if self.embedder.release_gpu():
            released.append("embedder")
        if released:
            self._report(f"Released retrieval GPU resources before generation: {', '.join(released)}.")


class DirectOllamaTransport:
    def __init__(self, base_url: str, label: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.label = label

    def get_json(self, path: str) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=20.0) as client:
            response = client.get(f"{self.base_url}{path}")
            response.raise_for_status()
            return response.json()

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        import httpx

        with httpx.Client(timeout=600.0) as client:
            response = client.post(f"{self.base_url}{path}", json=payload)
            response.raise_for_status()
            return response.json()


class WindowsBridgeOllamaTransport:
    def __init__(self, base_url: str = "http://127.0.0.1:11434") -> None:
        self.base_url = base_url.rstrip("/")
        self.label = "windows-bridge"

    @staticmethod
    def _encoded(script: str) -> str:
        return base64.b64encode(script.encode("utf-16le")).decode("ascii")

    def _run_script(self, script: str) -> str:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-EncodedCommand", self._encoded(script)],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "Unknown PowerShell error"
            raise RuntimeError(message)
        return result.stdout.strip()

    def get_json(self, path: str) -> dict[str, Any]:
        script = textwrap.dedent(
            f"""
            $ProgressPreference = 'SilentlyContinue'
            $response = Invoke-RestMethod -Uri '{self.base_url}{path}' -Method Get
            $response | ConvertTo-Json -Depth 100 -Compress
            """
        )
        output = self._run_script(script)
        return json.loads(output) if output else {}

    def post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=True)
        script = textwrap.dedent(
            f"""
            $ProgressPreference = 'SilentlyContinue'
            $body = @'
            {body}
            '@
            $response = Invoke-RestMethod -Uri '{self.base_url}{path}' -Method Post -ContentType 'application/json' -Body $body
            $response | ConvertTo-Json -Depth 100 -Compress
            """
        )
        output = self._run_script(script)
        return json.loads(output) if output else {}

    def windows_env(self, names: Sequence[str]) -> dict[str, dict[str, Any]]:
        quoted = ", ".join(f"'{name}'" for name in names)
        script = textwrap.dedent(
            f"""
            $ProgressPreference = 'SilentlyContinue'
            $names = @({quoted})
            $result = @{{}}
            foreach ($name in $names) {{
              $user = [Environment]::GetEnvironmentVariable($name, 'User')
              $machine = [Environment]::GetEnvironmentVariable($name, 'Machine')
              $effective = if ($user) {{ $user }} elseif ($machine) {{ $machine }} else {{ $null }}
              $scope = if ($user) {{ 'User' }} elseif ($machine) {{ 'Machine' }} else {{ 'Unset' }}
              $result[$name] = @{{
                effective = $effective
                scope = $scope
                user = $user
                machine = $machine
              }}
            }}
            $result | ConvertTo-Json -Depth 8 -Compress
            """
        )
        output = self._run_script(script)
        return json.loads(output) if output else {}

    def ollama_ps_text(self) -> str:
        script = textwrap.dedent(
            """
            $ProgressPreference = 'SilentlyContinue'
            $command = Get-Command ollama -ErrorAction SilentlyContinue
            if (-not $command) {
              return
            }
            (& $command.Source ps 2>$null | Out-String).Trim()
            """
        )
        return self._run_script(script)


class OllamaClient:
    def __init__(self, transports: Sequence[DirectOllamaTransport | WindowsBridgeOllamaTransport]) -> None:
        if not transports:
            raise ValueError("At least one Ollama transport is required.")
        self.transports = list(transports)
        self.transport = self.transports[0]

    @classmethod
    def detect(cls) -> "OllamaClient":
        transports: list[DirectOllamaTransport | WindowsBridgeOllamaTransport] = []
        if in_wsl() and shutil.which("powershell.exe"):
            bridge = WindowsBridgeOllamaTransport()
            try:
                bridge.get_json("/api/tags")
                transports.append(bridge)
            except Exception:
                pass
        candidates: list[tuple[str, str]] = []
        env_url = os.environ.get("QUBITZ_OLLAMA_URL") or os.environ.get("OLLAMA_HOST")
        if env_url:
            if "://" not in env_url:
                env_url = f"http://{env_url}"
            candidates.append((env_url, "configured"))
        candidates.extend(
            [
                ("http://127.0.0.1:11434", "localhost-127"),
                ("http://localhost:11434", "localhost-name"),
            ]
        )
        for base_url, label in candidates:
            transport = DirectOllamaTransport(base_url, label)
            try:
                transport.get_json("/api/tags")
                transports.append(transport)
            except Exception:
                continue
        if not transports:
            raise RuntimeError("No reachable Ollama server was found.")
        return cls(transports)

    @property
    def label(self) -> str:
        return self.transport.label

    def _call_with_fallback(self, method_name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        errors: list[str] = []
        for index, transport in enumerate(list(self.transports)):
            try:
                result = getattr(transport, method_name)(*args, **kwargs)
            except Exception as exc:
                errors.append(f"{transport.label}: {type(exc).__name__}: {exc}")
                continue
            if index != 0:
                self.transports.insert(0, self.transports.pop(index))
            self.transport = self.transports[0]
            return result
        raise RuntimeError(" ; ".join(errors) or "All Ollama transports failed.")

    def ensure_model(self, model_name: str) -> None:
        payload = {"model": model_name}
        self._call_with_fallback("post_json", "/api/show", payload)

    def runtime_status(self, model_name: str) -> dict[str, Any] | None:
        try:
            payload = self._call_with_fallback("get_json", "/api/ps")
        except Exception:
            return None
        for model in payload.get("models", []):
            if model.get("name") == model_name or model.get("model") == model_name:
                return model
        return None

    @staticmethod
    def _parse_ps_row(ps_text: str, model_name: str) -> dict[str, str] | None:
        lines = [line.strip() for line in ps_text.splitlines() if line.strip()]
        if len(lines) < 2:
            return None
        headers = re.split(r"\s{2,}", lines[0])
        wanted = {model_name.lower(), model_name.split(":", 1)[0].lower()}
        for raw_line in lines[1:]:
            columns = re.split(r"\s{2,}", raw_line)
            if len(columns) != len(headers):
                continue
            row = {header: value for header, value in zip(headers, columns, strict=False)}
            name = row.get("NAME", "").lower()
            if name not in wanted and not any(name.startswith(candidate) or candidate.startswith(name) for candidate in wanted):
                continue
            return row
        return None

    @staticmethod
    def _detect_windows_diagnostics_bridge() -> WindowsBridgeOllamaTransport | None:
        if not in_wsl() or not shutil.which("powershell.exe"):
            return None
        bridge = WindowsBridgeOllamaTransport()
        try:
            bridge.get_json("/api/tags")
        except Exception:
            return None
        return bridge

    def startup_diagnostics(self, model_name: str) -> dict[str, Any]:
        runtime = self.runtime_status(model_name)
        diagnostics: dict[str, Any] = {"transport": self.label, "runtime": runtime}
        bridge = self.transport if isinstance(self.transport, WindowsBridgeOllamaTransport) else self._detect_windows_diagnostics_bridge()
        if bridge is not None:
            try:
                diagnostics["configured_env"] = bridge.windows_env(OLLAMA_GPU_ENV_KEYS)
            except Exception as exc:
                diagnostics["configured_env_error"] = f"{type(exc).__name__}: {exc}"
            try:
                ps_row = self._parse_ps_row(bridge.ollama_ps_text(), model_name)
                if ps_row is not None:
                    diagnostics["processor"] = ps_row.get("PROCESSOR")
                    diagnostics["ps_context"] = ps_row.get("CONTEXT")
                    diagnostics["ps_until"] = ps_row.get("UNTIL")
            except Exception as exc:
                diagnostics["processor_error"] = f"{type(exc).__name__}: {exc}"
            diagnostics["env_note"] = (
                "Windows Ollama inherits user and system environment variables when the app starts; "
                "restart the Ollama app after changing them."
            )
            return diagnostics

        diagnostics["configured_env"] = {
            key: {
                "effective": os.environ.get(key),
                "scope": "process" if os.environ.get(key) is not None else "Unset",
            }
            for key in OLLAMA_GPU_ENV_KEYS
        }
        diagnostics["env_note"] = (
            "This is a local process environment snapshot. If Ollama is running as a separate service, "
            "its active server environment may differ."
        )
        return diagnostics

    def chat(
        self,
        *,
        model_name: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        keep_alive: str,
        num_ctx: int,
        num_predict: int,
        temperature: float,
    ) -> dict[str, Any]:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "tools": tools,
            "think": False,
            "keep_alive": keep_alive,
            "options": {
                "num_ctx": num_ctx,
                "num_predict": num_predict,
                "temperature": temperature,
            },
        }
        return self._call_with_fallback("post_json", "/api/chat", payload)


class MCPHost:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None

    def _server_parameters(self) -> StdioServerParameters:
        env = os.environ.copy()
        env["QUBITZ_MCP_WORKSPACE"] = str(self.workspace.resolve())
        if getattr(sys, "frozen", False):
            command = sys.executable
            args = ["--serve-mcp", "--workspace", "."]
        else:
            command = sys.executable
            args = [str(Path(__file__).resolve()), "--serve-mcp", "--workspace", "."]
        return StdioServerParameters(command=command, args=args, cwd=self.workspace, env=env)

    async def __aenter__(self) -> "MCPHost":
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(self._server_parameters()))
        self.session = await self._stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self._stack.aclose()

    async def list_tools(self) -> list[mcp_types.Tool]:
        assert self.session is not None
        result = await self.session.list_tools()
        return list(result.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> mcp_types.CallToolResult:
        assert self.session is not None
        return await self.session.call_tool(name, arguments)


class AgentRunner:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.workspace = config.workspace
        self.memory = MemoryStore(self.workspace)
        self.skills = SkillRegistry(self.workspace)
        self.retriever = RepoRetriever(config, progress_callback=self._report_retrieval)
        self.history: list[dict[str, str]] = []
        self.history_summary = ""
        self.harness_text = (self.workspace / "HARNESS.txt").read_text(encoding="utf-8", errors="ignore")
        rules_path = self.workspace / "RULES.md"
        self.rules_text = rules_path.read_text(encoding="utf-8", errors="ignore") if rules_path.exists() else ""
        self._active_callback: Callable[[str, str], None] | None = None

    def _emit(self, callback: Callable[[str, str], None] | None, kind: str, message: str) -> None:
        if callback is not None:
            callback(kind, message)

    def _report_retrieval(self, message: str) -> None:
        self._emit(self._active_callback, "status", message)

    def _system_prompt(self, memory_context: str, active_skill_context: str = "", history_summary: str = "") -> str:
        history_section = history_summary or "None"
        skill_section = active_skill_context or "None"
        return textwrap.dedent(
            f"""
            You are AI Agent Qubitz, a local standalone coding agent running inside the repository workspace.

            Governing harness:
            {self.harness_text}

            Project rules:
            {self.rules_text or "None"}

            Operating rules for this runtime:
            - Use tools directly whenever file inspection, file edits, file deletion, package installation, or command execution is needed.
            - Prefer relative workspace paths when possible.
            - Use the available tools to read current files before changing them.
            - If the harness says a requested file was already read directly, treat that as completed work and do not answer with future intent such as "I will read it".
            - Keep final answers concise and factual.
            - Do not expose hidden chain-of-thought.
            - The current memory context is below.

            Memory context:
            {memory_context}

            Active local skills:
            {skill_section}

            Condensed earlier conversation summary:
            {history_section}
            """
        ).strip()

    def _compact_history(self) -> None:
        max_recent_messages = HISTORY_TURNS * 2
        if len(self.history) <= max_recent_messages:
            return
        overflow = self.history[:-max_recent_messages]
        self.history = self.history[-max_recent_messages:]
        lines = [self.history_summary.strip()] if self.history_summary.strip() else []
        for item in overflow:
            label = "User" if item.get("role") == "user" else "Assistant"
            lines.append(f"- {label}: {shorten(item.get('content', ''), 240)}")
        self.history_summary = shorten("\n".join(line for line in lines if line), MAX_HISTORY_SUMMARY_CHARS)

    def _estimate_active_context(
        self,
        messages: Sequence[dict[str, Any]],
        tool_definitions: Sequence[dict[str, Any]],
    ) -> int:
        payload = json.dumps({"messages": list(messages), "tools": list(tool_definitions)}, ensure_ascii=False)
        return estimate_tokens(payload)

    def _user_message(
        self,
        prompt: str,
        repo_context: str,
        direct_file_context: str = "",
        active_skills: Sequence[SkillDefinition] | None = None,
    ) -> str:
        skill_list = ", ".join(skill.name for skill in active_skills or []) or "None"
        return textwrap.dedent(
            f"""
            User request:
            {prompt}

            Direct file reads already completed for this request:
            {direct_file_context or "None"}

            Retrieved repository context:
            {repo_context}

            Activated skills:
            {skill_list}

            Use tools when they are helpful. Finish with a direct answer when the task is complete.
            """
        ).strip()

    def _resolve_direct_read_paths(self, prompt: str) -> list[Path]:
        if not READ_INTENT_PATTERN.search(prompt):
            return []
        exact_map: dict[str, Path] = {}
        basename_map: dict[str, list[Path]] = {}
        for candidate in self.workspace.rglob("*"):
            if not candidate.is_file():
                continue
            if any(part in {".git", ".venv", "__pycache__", "node_modules"} for part in candidate.parts):
                continue
            rel = relative_path(candidate, self.workspace)
            exact_map[rel.lower()] = candidate
            basename_map.setdefault(candidate.name.lower(), []).append(candidate)
        resolved: list[Path] = []
        seen: set[Path] = set()
        for token in extract_file_tokens(prompt):
            normalized = token.replace("\\", "/").lstrip("./").strip()
            if not normalized:
                continue
            path = exact_map.get(normalized.lower())
            if path is None:
                basename_matches = basename_map.get(Path(normalized).name.lower(), [])
                if len(basename_matches) == 1:
                    path = basename_matches[0]
            if path is None or path in seen or is_sensitive_path_name(path):
                continue
            seen.add(path)
            resolved.append(path)
            if len(resolved) >= MAX_DIRECT_READ_FILES:
                break
        return resolved

    def _prepare_direct_file_context(
        self,
        prompt: str,
        callback: Callable[[str, str], None] | None = None,
    ) -> str:
        paths = self._resolve_direct_read_paths(prompt)
        if not paths:
            return ""
        self._emit(callback, "status", f"Direct file read requested for {len(paths)} file(s).")
        sections: list[str] = []
        for path in paths:
            rel = relative_path(path, self.workspace)
            self._emit(callback, "tool", f"Reading {rel} directly.")
            text = path.read_text(encoding="utf-8", errors="ignore")
            truncated = ""
            if len(text) > MAX_DIRECT_READ_CHARS:
                text = text[:MAX_DIRECT_READ_CHARS]
                truncated = "\n[truncated by harness]"
            sections.append(f"File: {rel}\n{text}{truncated}")
        return "\n\n".join(sections)

    async def _run_async(
        self,
        prompt: str,
        callback: Callable[[str, str], None] | None = None,
    ) -> str:
        self._active_callback = callback
        try:
            self.memory.add_turn("user", prompt)
            if self.skills.warnings:
                for warning in self.skills.warnings:
                    self._emit(callback, "status", f"Skill warning: {warning}")
            self._emit(callback, "status", f"Loaded {self.skills.count()} local skill(s) from .skills.")
            active_skills = self.skills.select_for_prompt(prompt)
            active_skill_context = self.skills.render_active_context(active_skills)
            if active_skills:
                names = ", ".join(skill.name for skill in active_skills)
                self._emit(callback, "status", f"Activated local skill(s): {names}")
            self._emit(
                callback,
                "status",
                (
                    "Retrieval GPU policy: use CUDA when free VRAM stays above "
                    f"{self.config.embed_min_free_vram_gib:.1f} GiB and preserve "
                    f"{self.config.retrieval_gpu_reserve_gib:.1f} GiB of headroom before generation."
                ),
            )
            direct_file_context = self._prepare_direct_file_context(prompt, callback)
            if direct_file_context:
                self._emit(
                    callback,
                    "status",
                    "Direct file contents were loaded. Semantic retrieval is skipped for this request.",
                )
                repo_context = "Direct file read path used."
            else:
                self._emit(
                    callback,
                    "status",
                    "Preparing repository context before loading the generation model to keep more VRAM free for embeddings.",
                )
                repo_context = self.retriever.format_context(prompt)
            self.retriever.release_gpu_resources()
            memory_context = self.memory.build_context(prompt)
            self._emit(callback, "status", "Repository and memory context prepared.")

            self._emit(callback, "status", "Detecting Ollama and validating the target model.")
            ollama = OllamaClient.detect()
            ollama.ensure_model(self.config.model_name)
            self._emit(callback, "status", f"Ollama transport: {ollama.label}")
            diagnostics = ollama.startup_diagnostics(self.config.model_name)
            configured_env = diagnostics.get("configured_env") or {}
            if configured_env:
                env_summary = ", ".join(
                    (
                        f"{key.removeprefix('OLLAMA_').lower()}="
                        f"{(details.get('effective') if isinstance(details, dict) else None) or 'unset'}"
                        f" [{(details.get('scope') if isinstance(details, dict) else 'unknown')}]"
                    )
                    for key, details in configured_env.items()
                )
                self._emit(callback, "status", f"Ollama GPU env snapshot: {env_summary}")
            if diagnostics.get("configured_env_error"):
                self._emit(callback, "status", f"Ollama env detection warning: {diagnostics['configured_env_error']}")
            if diagnostics.get("env_note"):
                self._emit(callback, "status", diagnostics["env_note"])
            if diagnostics.get("processor"):
                self._emit(callback, "status", f"Ollama processor state for {self.config.model_name}: {diagnostics['processor']}")
            if diagnostics.get("processor_error"):
                self._emit(callback, "status", f"Ollama processor detection warning: {diagnostics['processor_error']}")
            runtime = diagnostics.get("runtime")
            if runtime is not None:
                details = runtime.get("details") or {}
                self._emit(
                    callback,
                    "status",
                    (
                        f"Ollama model ready: {runtime.get('model', self.config.model_name)} "
                        f"family={details.get('family', 'unknown')} "
                        f"quant={details.get('quantization_level', 'unknown')} "
                        f"vram={format_bytes(runtime.get('size_vram'))} "
                        f"context={runtime.get('context_length', 'unknown')}"
                    ),
                )
            self._emit(callback, "status", "Starting model loop.")

            async with MCPHost(self.workspace) as mcp_host:
                tools = await mcp_host.list_tools()
                self._emit(callback, "status", f"Loaded {len(tools)} MCP tool(s) for the model.")
                tool_definitions = build_ollama_tools(tools)
                messages: list[dict[str, Any]] = [
                    {"role": "system", "content": self._system_prompt(memory_context, active_skill_context, self.history_summary)}
                ]
                messages.extend(self.history[-(HISTORY_TURNS * 2) :])
                messages.append(
                    {
                        "role": "user",
                        "content": self._user_message(prompt, repo_context, direct_file_context, active_skills),
                    }
                )

                repeated_calls: dict[str, int] = {}
                for step in range(1, self.config.max_steps + 1):
                    active_context_tokens = self._estimate_active_context(messages, tool_definitions)
                    self._emit(
                        callback,
                        "status",
                        (
                            f"Model step {step}/{self.config.max_steps}: waiting for the next answer or tool call. "
                            f"Estimated active context ~{active_context_tokens} tokens, "
                            f"num_ctx={self.config.ollama_num_ctx}, num_predict={self.config.ollama_num_predict}."
                        ),
                    )
                    response = ollama.chat(
                        model_name=self.config.model_name,
                        messages=messages,
                        tools=tool_definitions,
                        keep_alive=self.config.ollama_keep_alive,
                        num_ctx=self.config.ollama_num_ctx,
                        num_predict=self.config.ollama_num_predict,
                        temperature=self.config.ollama_temperature,
                    )
                    message = response.get("message") or {}
                    tool_calls = message.get("tool_calls") or []
                    content = (message.get("content") or "").strip()
                    if tool_calls:
                        assistant_message = {
                            "role": "assistant",
                            "content": content,
                            "tool_calls": tool_calls,
                        }
                        messages.append(assistant_message)
                        for tool_call in tool_calls:
                            function = tool_call.get("function") or {}
                            name = function.get("name", "").strip()
                            arguments = clean_arguments(function.get("arguments"))
                            call_key = json.dumps({"name": name, "arguments": arguments}, sort_keys=True)
                            repeated_calls[call_key] = repeated_calls.get(call_key, 0) + 1
                            if repeated_calls[call_key] > 2:
                                tool_payload = {
                                    "is_error": True,
                                    "content_text": f"Refusing repeated tool call for {name} after 2 repeats.",
                                }
                            else:
                                self._emit(callback, "tool", describe_tool_action(name, arguments))
                                tool_result = await mcp_host.call_tool(name, arguments)
                                tool_payload = serialize_mcp_result(tool_result)
                                self._emit(callback, "tool", summarize_tool_result(name, tool_payload))
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_name": name,
                                    "content": shorten(
                                        json.dumps(tool_payload, ensure_ascii=True, indent=2),
                                        MAX_TOOL_RESULT_CHARS,
                                    ),
                                }
                            )
                        continue
                    if content:
                        self.memory.add_turn("assistant", content)
                        self.history.extend(
                            [
                                {"role": "user", "content": prompt},
                                {"role": "assistant", "content": content},
                            ]
                        )
                        self._compact_history()
                        return content
                    messages.append({"role": "assistant", "content": ""})
                    messages.append(
                        {
                            "role": "user",
                            "content": "You returned neither tool calls nor a final answer. Either call a tool or answer directly.",
                        }
                    )
            final_error = "Stopped after reaching the maximum tool steps."
            self.memory.add_turn("assistant", final_error)
            return final_error
        finally:
            self._active_callback = None

    def run_sync(self, prompt: str, callback: Callable[[str, str], None] | None = None) -> str:
        return asyncio.run(self._run_async(prompt, callback))


def build_mcp_server(workspace: Path) -> FastMCP:
    workspace = workspace.resolve()
    memory_dir = workspace / ".memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    current_memory_path = memory_dir / CURRENT_MEMORY_NAME
    skills = SkillRegistry(workspace)
    server = FastMCP("Qubitz Local Tools", json_response=True, instructions="Local offline filesystem and workspace tools.")

    def _resolve(candidate: str, *, allow_missing: bool = True) -> Path:
        return resolve_workspace_path(workspace, candidate, allow_missing=allow_missing)

    def _read_text(candidate: Path) -> str:
        return candidate.read_text(encoding="utf-8", errors="ignore")

    @server.resource("workspace://summary")
    def workspace_summary() -> str:
        return json.dumps(
            {
                "workspace": workspace.as_posix(),
                "memory_file": relative_path(current_memory_path, workspace),
                "excluded_dirs": sorted(EXCLUDED_DIRS),
                "skill_count": skills.count(),
                "skills_root": relative_path(skills.skills_root, workspace),
                "skill_warnings": skills.warnings,
            },
            ensure_ascii=True,
            indent=2,
        )

    @server.resource("memory://current")
    def memory_resource() -> str:
        if not current_memory_path.exists():
            return ""
        return current_memory_path.read_text(encoding="utf-8", errors="ignore")

    @server.resource("skills://index")
    def skills_index() -> str:
        return json.dumps(
            {
                "skills_root": relative_path(skills.skills_root, workspace),
                "count": skills.count(),
                "warnings": skills.warnings,
                "skills": skills.list_summaries(),
            },
            ensure_ascii=True,
            indent=2,
        )

    @server.tool(description="List files or directories inside the workspace.")
    def list_files(path: str = ".", recursive: bool = False, max_entries: int = 200) -> dict[str, Any]:
        root = _resolve(path, allow_missing=False)
        iterator = root.rglob("*") if recursive else root.iterdir()
        entries: list[dict[str, Any]] = []
        for candidate in sorted(iterator):
            if any(part in EXCLUDED_DIRS for part in candidate.parts if candidate != root):
                continue
            entries.append(
                {
                    "path": relative_path(candidate, workspace),
                    "is_dir": candidate.is_dir(),
                    "size": candidate.stat().st_size if candidate.is_file() else None,
                }
            )
            if len(entries) >= max_entries:
                break
        return {"root": relative_path(root, workspace), "entries": entries}

    @server.tool(description="List local Agent Skills discovered under .skills.")
    def list_skills() -> dict[str, Any]:
        return {
            "skills_root": relative_path(skills.skills_root, workspace),
            "count": skills.count(),
            "warnings": skills.warnings,
            "skills": skills.list_summaries(),
        }

    @server.tool(description="Read a local skill's metadata and SKILL.md body.")
    def read_skill(skill_name: str) -> dict[str, Any]:
        skill = skills.get(skill_name)
        summary = skill.to_summary(workspace)
        summary["body"] = skill.body
        return summary

    @server.tool(description="Read a file or directory inside a local skill root such as references/, scripts/, or assets/.")
    def read_skill_resource(skill_name: str, resource_path: str) -> dict[str, Any]:
        return skills.read_skill_resource(skill_name, resource_path)

    @server.tool(description="Read a text file from the workspace.")
    def read_file(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False)
        if not target.is_file():
            raise ValueError(f"Not a file: {path}")
        text = _read_text(target)
        lines = text.splitlines()
        start_index = max(start_line - 1, 0)
        end_index = min(end_line, len(lines))
        excerpt = "\n".join(lines[start_index:end_index])
        return {
            "path": relative_path(target, workspace),
            "start_line": start_index + 1,
            "end_line": end_index,
            "content": excerpt,
        }

    @server.tool(description="Write or overwrite a file inside the workspace.")
    def write_file(path: str, content: str, make_parents: bool = True) -> dict[str, Any]:
        target = _resolve(path)
        if make_parents:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": relative_path(target, workspace), "bytes_written": target.stat().st_size}

    @server.tool(description="Replace exact text inside a file.")
    def replace_text(path: str, old_text: str, new_text: str, count: int = 0) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False)
        original = _read_text(target)
        replacements = original.count(old_text) if count == 0 else min(original.count(old_text), count)
        updated = original.replace(old_text, new_text, count) if count > 0 else original.replace(old_text, new_text)
        if updated == original:
            return {"path": relative_path(target, workspace), "replacements": 0}
        target.write_text(updated, encoding="utf-8")
        return {"path": relative_path(target, workspace), "replacements": replacements}

    @server.tool(description="Delete a file or directory inside the workspace.")
    def delete_path(path: str, recursive: bool = False) -> dict[str, Any]:
        target = _resolve(path, allow_missing=False)
        if target.is_dir():
            if recursive:
                shutil.rmtree(target)
            else:
                target.rmdir()
        else:
            target.unlink()
        return {"deleted": relative_path(target, workspace), "recursive": recursive}

    @server.tool(description="Create a directory inside the workspace.")
    def make_directory(path: str) -> dict[str, Any]:
        target = _resolve(path)
        target.mkdir(parents=True, exist_ok=True)
        return {"path": relative_path(target, workspace), "created": True}

    @server.tool(description="Move or rename a path inside the workspace.")
    def move_path(source: str, destination: str, overwrite: bool = False) -> dict[str, Any]:
        source_path = _resolve(source, allow_missing=False)
        destination_path = _resolve(destination)
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
            "source": relative_path(source_path, workspace),
            "destination": relative_path(destination_path, workspace),
        }

    @server.tool(description="Search text content inside workspace files.")
    def search_text(
        query: str,
        path: str = ".",
        file_glob: str = "*",
        max_results: int = 50,
        case_sensitive: bool = False,
    ) -> dict[str, Any]:
        root = _resolve(path, allow_missing=False)
        hits: list[dict[str, Any]] = []
        needle = query if case_sensitive else query.lower()
        for candidate in sorted(root.rglob("*")):
            if not candidate.is_file():
                continue
            if any(part in EXCLUDED_DIRS for part in candidate.parts):
                continue
            if not candidate.match(file_glob) and not Path(relative_path(candidate, workspace)).match(file_glob):
                continue
            if not is_probably_text_file(candidate):
                continue
            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
            for index, line in enumerate(lines, start=1):
                haystack = line if case_sensitive else line.lower()
                if needle in haystack:
                    hits.append(
                        {
                            "path": relative_path(candidate, workspace),
                            "line": index,
                            "content": line.strip(),
                        }
                    )
                    if len(hits) >= max_results:
                        return {"query": query, "matches": hits}
        return {"query": query, "matches": hits}

    @server.tool(description="Install Python packages into the workspace .venv using uv when available.")
    def install_python_package(
        packages: list[str] | None = None,
        requirements_file: str | None = None,
        upgrade: bool = False,
    ) -> dict[str, Any]:
        venv_python = workspace / ".venv" / "bin" / "python"
        if not venv_python.exists():
            raise FileNotFoundError("Workspace .venv/bin/python was not found.")
        if not packages and not requirements_file:
            raise ValueError("Provide packages and/or requirements_file.")
        if shutil.which("uv"):
            command = ["uv", "pip", "install", "--python", str(venv_python)]
        else:
            command = [str(venv_python), "-m", "pip", "install"]
        if upgrade:
            command.append("--upgrade")
        if requirements_file:
            requirements_path = _resolve(requirements_file, allow_missing=False)
            command.extend(["-r", str(requirements_path)])
        if packages:
            command.extend(packages)
        result = subprocess.run(
            command,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
        )
        return {
            "command": command,
            "return_code": result.returncode,
            "stdout": shorten(result.stdout, 4000),
            "stderr": shorten(result.stderr, 4000),
        }

    @server.tool(description="Run a bounded project command without using a shell.")
    def run_project_command(command: list[str], cwd: str = ".", timeout_seconds: int = 300) -> dict[str, Any]:
        if not command:
            raise ValueError("Command cannot be empty.")
        executable = command[0]
        if executable not in ALLOWED_COMMANDS and executable != relative_path(workspace / ".venv" / "bin" / "python", workspace):
            raise ValueError(f"Command is not allowed: {executable}")
        target_cwd = _resolve(cwd, allow_missing=False)
        result = subprocess.run(
            command,
            cwd=target_cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "command": command,
            "cwd": relative_path(target_cwd, workspace),
            "return_code": result.returncode,
            "stdout": shorten(result.stdout, 4000),
            "stderr": shorten(result.stderr, 4000),
        }

    @server.tool(description="Read the current persistent memory file.")
    def read_memory() -> dict[str, Any]:
        text = current_memory_path.read_text(encoding="utf-8", errors="ignore") if current_memory_path.exists() else ""
        return {"path": relative_path(current_memory_path, workspace), "content": shorten(text, 6000)}

    @server.tool(description="Search across memory markdown files.")
    def search_memory(query: str, max_results: int = 5) -> dict[str, Any]:
        query_tokens = token_set(query)
        matches: list[dict[str, Any]] = []
        for candidate in sorted(memory_dir.glob("MEMORY*.md"), key=lambda item: item.stat().st_mtime, reverse=True):
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            lowered = text.lower()
            score = sum(lowered.count(token) for token in query_tokens)
            if score <= 0:
                continue
            matches.append(
                {
                    "path": relative_path(candidate, workspace),
                    "score": score,
                    "snippet": shorten(text, 1200),
                }
            )
            if len(matches) >= max_results:
                break
        return {"query": query, "matches": matches}

    return server


class QubitzGUI:
    def __init__(self, config: AgentConfig) -> None:
        tk, ttk, scrolledtext, messagebox = import_tk_modules()
        self.tk = tk
        self.ttk = ttk
        self.scrolledtext = scrolledtext
        self.messagebox = messagebox
        self.config = config
        self.agent = AgentRunner(config)
        self.root = tk.Tk()
        self.root.title("AI Agent Qubitz")
        self.root.geometry("800x800")
        self.root.minsize(800, 800)
        self.root.configure(bg=UI_BG, bd=0, highlightthickness=0)
        self._apply_theme()
        self.status_var = tk.StringVar(master=self.root, value="Ready")
        self.num_ctx_var = tk.StringVar(master=self.root, value=str(self.config.ollama_num_ctx))
        self.num_predict_var = tk.StringVar(master=self.root, value=str(self.config.ollama_num_predict))
        self.event_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self._build_layout()
        self.root.after_idle(self._maximize_window)
        self._append_transcript("system", f"Workspace: {config.workspace.as_posix()}")
        self._append_transcript("system", f"Model: {config.model_name}")
        self._append_transcript("system", f"Embedding Model: {config.embed_model_name}")
        self._append_transcript(
            "system",
            f"Runtime defaults: num_ctx={config.ollama_num_ctx}, num_predict={config.ollama_num_predict}.",
        )
        self._append_transcript(
            "system",
            "The agent can use local tools for reading, editing, deleting, installing, and running bounded commands.",
        )
        self._append_transcript("system", f"Local skills discovered: {self.agent.skills.count()}")
        for warning in self.agent.skills.warnings:
            self._append_transcript("status", f"Skill warning: {warning}")
        self.root.after(100, self._poll_events)

    def _apply_theme(self) -> None:
        self.root.configure(bg=UI_BG)
        self.root.option_add("*Background", UI_BG)
        self.root.option_add("*Foreground", UI_TEXT)
        self.root.option_add("*selectBackground", UI_SELECT)
        self.root.option_add("*selectForeground", UI_TEXT)
        style = self.ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=UI_BG, foreground=UI_TEXT)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabel", background=UI_BG, foreground=UI_TEXT)
        style.configure(
            "TEntry",
            fieldbackground=UI_PANEL,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_PANEL,
            darkcolor=UI_PANEL,
            insertcolor=UI_TEXT,
            padding=(6, 6),
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", UI_PANEL), ("readonly", UI_PANEL)],
            foreground=[("disabled", UI_TEXT_MUTED)],
        )
        style.configure(
            "TButton",
            background=UI_PANEL_ALT,
            foreground=UI_TEXT,
            bordercolor=UI_BORDER,
            lightcolor=UI_PANEL_ALT,
            darkcolor=UI_PANEL_ALT,
            focuscolor=UI_PANEL_ALT,
            padding=(10, 8),
        )
        style.map(
            "TButton",
            background=[("active", UI_SELECT), ("pressed", UI_SELECT), ("disabled", UI_PANEL)],
            foreground=[("disabled", UI_TEXT_MUTED)],
        )

    def _maximize_window(self) -> None:
        try:
            self.root.state("zoomed")
            return
        except self.tk.TclError:
            pass
        try:
            self.root.wm_attributes("-zoomed", True)
            return
        except self.tk.TclError:
            pass
        self.root.update_idletasks()
        width = max(800, self.root.winfo_screenwidth())
        height = max(800, self.root.winfo_screenheight())
        self.root.geometry(f"{width}x{height}+0+0")

    def _build_layout(self) -> None:
        frame = self.ttk.Frame(self.root, padding=10)
        frame.pack(fill="both", expand=True)

        header = self.ttk.Frame(frame)
        header.pack(fill="x", pady=(0, 10))
        title = self.ttk.Label(header, text="AI Agent Qubitz", font=("TkDefaultFont", 14, "bold"))
        title.pack(side="left")
        self.ttk.Label(header, textvariable=self.status_var).pack(side="right")

        controls = self.ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 10))
        self.ttk.Label(controls, text="Context").pack(side="left")
        self.num_ctx_entry = self.ttk.Entry(controls, textvariable=self.num_ctx_var, width=10)
        self.num_ctx_entry.pack(side="left", padx=(6, 16))
        self.ttk.Label(controls, text="Max Output").pack(side="left")
        self.num_predict_entry = self.ttk.Entry(controls, textvariable=self.num_predict_var, width=10)
        self.num_predict_entry.pack(side="left", padx=(6, 0))

        self.transcript = self.scrolledtext.ScrolledText(frame, wrap="word", state="disabled", height=30)
        self.transcript.configure(
            background=UI_PANEL,
            foreground=UI_TEXT,
            insertbackground=UI_TEXT,
            selectbackground=UI_SELECT,
            selectforeground=UI_TEXT,
            highlightbackground=UI_BORDER,
            highlightcolor=UI_BORDER,
            highlightthickness=1,
            borderwidth=0,
            relief="flat",
        )
        self.transcript.vbar.configure(
            background=UI_PANEL_ALT,
            troughcolor=UI_BG,
            activebackground=UI_SELECT,
            highlightbackground=UI_BG,
        )
        self.transcript.pack(fill="both", expand=True)

        composer = self.ttk.Frame(frame)
        composer.pack(fill="both", pady=(10, 0))
        self.prompt_box = self.tk.Text(composer, wrap="word", height=8)
        self.prompt_box.configure(
            background=UI_PANEL,
            foreground=UI_TEXT,
            insertbackground=UI_TEXT,
            selectbackground=UI_SELECT,
            selectforeground=UI_TEXT,
            highlightbackground=UI_BORDER,
            highlightcolor=UI_BORDER,
            highlightthickness=1,
            borderwidth=0,
            relief="flat",
        )
        self.prompt_box.pack(fill="both", expand=True, side="left")
        self.prompt_box.bind("<Control-Return>", self._handle_send_shortcut)

        buttons = self.ttk.Frame(composer)
        buttons.pack(fill="y", side="left", padx=(10, 0))
        self.send_button = self.ttk.Button(buttons, text="Send", command=self.send_prompt)
        self.send_button.pack(fill="x")
        self.clear_button = self.ttk.Button(buttons, text="Clear", command=self.clear_input)
        self.clear_button.pack(fill="x", pady=(8, 0))

    def _append_transcript(self, role: str, message: str) -> None:
        self.transcript.configure(state="normal")
        self.transcript.insert("end", f"[{role}] {message.strip()}\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    def _set_busy(self, value: bool) -> None:
        self.busy = value
        state = "disabled" if value else "normal"
        self.send_button.configure(state=state)
        self.prompt_box.configure(state=state)
        self.num_ctx_entry.configure(state=state)
        self.num_predict_entry.configure(state=state)
        self.status_var.set("Working" if value else "Ready")

    def _sync_runtime_settings(self) -> None:
        try:
            num_ctx = int(self.num_ctx_var.get().strip())
            num_predict = int(self.num_predict_var.get().strip())
        except ValueError as exc:
            raise ValueError("Context and Max Output must be integers.") from exc
        if num_ctx <= 0 or num_predict <= 0:
            raise ValueError("Context and Max Output must be positive integers.")
        self.config.ollama_num_ctx = num_ctx
        self.config.ollama_num_predict = num_predict

    def clear_input(self) -> None:
        if self.busy:
            return
        self.prompt_box.delete("1.0", "end")

    def _handle_send_shortcut(self, _event: Any) -> str:
        self.send_prompt()
        return "break"

    def send_prompt(self) -> None:
        if self.busy:
            return
        try:
            self._sync_runtime_settings()
        except ValueError as exc:
            self.messagebox.showerror("AI Agent Qubitz", str(exc))
            return
        prompt = self.prompt_box.get("1.0", "end").strip()
        if not prompt:
            return
        self.prompt_box.delete("1.0", "end")
        self._append_transcript("user", prompt)
        self._set_busy(True)
        worker = threading.Thread(target=self._worker_run, args=(prompt,), daemon=True)
        worker.start()

    def _worker_emit(self, kind: str, message: str) -> None:
        self.event_queue.put((kind, message))

    def _worker_run(self, prompt: str) -> None:
        try:
            answer = self.agent.run_sync(prompt, self._worker_emit)
        except Exception as exc:  # pragma: no cover - runtime GUI path
            details = "".join(traceback.format_exception(exc))
            self.event_queue.put(("error", details))
        else:
            self.event_queue.put(("answer", answer))
        finally:
            self.event_queue.put(("done", ""))

    def _poll_events(self) -> None:
        while True:
            try:
                kind, message = self.event_queue.get_nowait()
            except queue.Empty:
                break
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
        self.root.after(100, self._poll_events)

    def run(self) -> None:
        self.root.mainloop()


def run_cli(config: AgentConfig, initial_prompt: str | None = None) -> None:
    runner = AgentRunner(config)

    def emit(kind: str, message: str) -> None:
        print(f"[{kind}] {message}")

    if initial_prompt:
        print(runner.run_sync(initial_prompt, emit))
        return
    print("AI Agent Qubitz CLI. Type 'exit' to stop.")
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
        answer = runner.run_sync(prompt, emit)
        print(answer)


def serve_mcp(workspace: Path) -> None:
    server = build_mcp_server(workspace)
    server.run(transport="stdio")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Agent Qubitz")
    parser.add_argument("--workspace", default=".", help="Workspace root. Defaults to the current working directory.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama chat model name.")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL, help="Embedding model name.")
    parser.add_argument("--num-ctx", type=int, default=16384, help="Ollama context window to request for each chat call.")
    parser.add_argument(
        "--num-predict",
        type=int,
        default=DEFAULT_NUM_PREDICT,
        help="Maximum output tokens to request from Ollama for each chat call.",
    )
    parser.add_argument("--cli", action="store_true", help="Run the terminal interface instead of the Tk GUI.")
    parser.add_argument("--prompt", help="Run a single CLI prompt and exit.")
    parser.add_argument("--serve-mcp", action="store_true", help="Run the local MCP server over stdio.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    workspace = Path(args.workspace).resolve()
    configure_project_environment(workspace)
    ensure_display_environment()
    config = AgentConfig(
        workspace=workspace,
        model_name=args.model,
        embed_model_name=args.embed_model,
        ollama_num_ctx=args.num_ctx,
        ollama_num_predict=args.num_predict,
    )
    if args.serve_mcp:
        serve_mcp(workspace)
        return
    if args.cli or (not os.environ.get("DISPLAY") and sys.platform.startswith("linux")):
        run_cli(config, initial_prompt=args.prompt)
        return
    gui = QubitzGUI(config)
    gui.run()


if __name__ == "__main__":
    main()
