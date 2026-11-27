"""Bounded static discovery and evidence checks for declared project workflows."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from .retrieval import FOCUSED_SKIP_DIRECTORIES, SKIP_DIRECTORIES, read_text_prefix

_NAME = r"[A-Za-z0-9][A-Za-z0-9_:-]{0,63}"
_CONFIRM = re.compile(
    rf"\s*(?:please\s+)?run\s+workflow\s+([`\"']?)((?:{_NAME}/){{0,2}}{_NAME})\1[.!]?\s*",
    re.IGNORECASE,
)
_SUFFIXES = {"node": {".js", ".mjs", ".cjs"}, "python": {".py"}, "python3": {".py"}, "bash": {".sh"}}
_EXCLUDED = FOCUSED_SKIP_DIRECTORIES | SKIP_DIRECTORIES | {"data", "node_modules", "fixtures", "test-fixtures", "tests", "examples"}
_ROOT_MANIFESTS = (".qubitz/workflows.json", "package.json")
_WORD_GROUPS = {
    "discover": {"discover", "find", "scan", "search", "look"},
    "create": {"create", "generate", "produce", "export", "build", "make"},
    "check": {"check", "verify", "validate", "inspect"},
}
_SKIP_TERMS = {"this", "that", "using", "use", "project", "workspace", "repo", "run", "workflow", "with", "the", "for", "please", "and", "from"}


@dataclass(frozen=True)
class Workflow:
    name: str
    project: str
    declaration: str
    declaration_sha256: str
    argv: tuple[str, ...]
    description: str
    requires: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    network: bool | None = None

    @property
    def key(self) -> str:
        return self.name if self.project == "." else f"{self.project}/{self.name}"


def explicit_workflow_name(prompt: str) -> str | None:
    return match[2] if (match := _CONFIRM.fullmatch(prompt)) else None


def workspace_path(root: Path, raw: str) -> Path | None:
    if (not raw or len(raw) > 240 or "\\" in raw or ":" in raw or PureWindowsPath(raw).drive or PurePosixPath(raw).is_absolute()
            or ".." in PurePosixPath(raw).parts or re.search(r"[\x00-\x1f\x7f]", raw)):
        return None
    path = root / raw
    try:
        return path if path.resolve() == path and path.is_relative_to(root) else None
    except (OSError, RuntimeError):
        return None


def _revision(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _text(root: Path, name: str, limit: int, *, prefix: bool = False) -> str | None:
    path = workspace_path(root, name)
    try:
        if path is None or not path.is_file():
            return None
        before = path.stat()
        text, truncated = read_text_prefix(path, limit, errors="strict")
        after = path.stat()
        if (truncated and not prefix) or "\x00" in text or workspace_path(root, name) is None:
            return None
        if _revision(before) != _revision(after):
            return None
        return text
    except (OSError, UnicodeError):
        return None


def _tokens(value: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]{2,}", value.casefold())) - _SKIP_TERMS
    for alias, words in _WORD_GROUPS.items():
        if tokens & words:
            tokens.difference_update(words)
            tokens.add(alias)
    return {token.removesuffix("s") if len(token) > 3 else token for token in tokens}


def _argv(value) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = shlex.split(value)
        except ValueError:
            return ()
    if (not isinstance(value, list) or not 2 <= len(value) <= 18
            or not all(isinstance(arg, str) and 0 < len(arg) <= 240 and not re.search(r"[;&|<>`$\x00-\x1f\x7f]", arg)
                       for arg in value)
            or value[0] not in _SUFFIXES or value[1].startswith("-")
            or Path(value[1]).suffix.casefold() not in _SUFFIXES[value[0]]):
        return ()
    target = PurePosixPath(value[1])
    if target.suffix.casefold() == ".py" and (
        target.name.startswith("test_") or target.name.endswith("_test.py")
        or target.parent.name.casefold() in {"test", "tests"}
    ):
        return ()  # The owned runner would substitute pytest for this declared invocation.
    return tuple(value)


def _relative_list(value) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 12 or not all(isinstance(item, str) for item in value):
        raise ValueError("Workflow file declarations must be a short list of relative paths")
    return tuple(value)


def _discover_project(project: Path, relative: str, checkpoint) -> list[Workflow]:
    readme = _text(project, "README.md", 48 * 1024, prefix=True) or ""
    descriptions = dict(re.findall(r"(?m)^\|\s*`(" + _NAME + r")`\s*\|\s*([^|\r\n]{1,300})", readme))
    found: dict[str, Workflow] = {}
    for declaration in _ROOT_MANIFESTS:
        checkpoint()
        raw = _text(project, declaration, 64 * 1024)
        if raw is None:
            continue
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        explicit = declaration != "package.json"
        table = payload.get("workflows" if explicit else "scripts", {})
        if not isinstance(table, dict):
            continue
        digest = hashlib.sha256(raw.encode()).hexdigest()
        for name, item in list(table.items())[:128]:
            checkpoint()
            if name in found or not re.fullmatch(_NAME, name):
                continue
            if explicit and not isinstance(item, dict):
                continue
            records = item if explicit else {}
            try:
                inputs = _relative_list(records.get("requires", []))
                outputs = _relative_list(records.get("outputs", []))
            except ValueError:
                continue
            command = _argv(records.get("argv") if explicit else item)
            if not explicit and ("pre" + name in table or "post" + name in table):
                command = ()  # Do not bypass package lifecycle behavior.
            description = str(records.get("description") or descriptions.get(name, ""))[:300]
            network = records.get("network")
            found[name] = Workflow(name, relative, declaration, digest, command, description, inputs, outputs,
                                   network if isinstance(network, bool) else None)
    # A documented literal interpreter invocation can describe a workflow without a package script.
    for match in re.finditer(r"(?m)^\s*`?((?:node|python3?|bash)\s+[./A-Za-z0-9_][^`\r\n]{0,180})`?\s*$", readme):
        if len(found) >= 128:
            break
        command = _argv(match[1])
        if not command:
            continue
        name = Path(command[1]).stem
        if re.fullmatch(_NAME, name) and not any(item.argv == command for item in found.values()):
            found.setdefault(name, Workflow(name, relative, "README.md", hashlib.sha256(readme.encode()).hexdigest(),
                                            command, descriptions.get(name, "")))
    return list(found.values())


def discover(root: Path, prompt: str, checkpoint) -> list[Workflow]:
    """Rank declared workflow candidates without running project code."""
    wanted = explicit_workflow_name(prompt)
    query = _tokens(prompt[:8192])
    found = _discover_project(root, ".", checkpoint)
    if wanted is not None and "/" in wanted:
        project_name, _ = wanted.rsplit("/", 1)
        project = workspace_path(root, project_name)
        if project is not None and not any(part.casefold() in _EXCLUDED for part in Path(project_name).parts):
            found.extend(_discover_project(project, project_name, checkpoint))
    elif wanted is None:
        # Root plus one level of nested project roots; never recursively enumerate the tree.
        nested_projects = 0
        for index, directory in enumerate(root.iterdir()):
            checkpoint()
            if index >= 96 or nested_projects >= 8:
                break
            name = directory.name
            if (name.startswith(".") or name.casefold() in _EXCLUDED or workspace_path(root, name) is None
                    or not directory.is_dir()):
                continue
            if any((directory / manifest).is_file() for manifest in _ROOT_MANIFESTS):
                nested_projects += 1
                found.extend(_discover_project(directory, name, checkpoint))
    if wanted is not None:
        return [item for item in found if item.key == wanted]
    scored = []
    for item in found:
        if not item.argv:
            continue
        name_terms = _tokens(item.name)
        hits = len(query & name_terms)
        description_hits = len(query & _tokens(item.description))
        if hits or description_hits >= 2:
            scored.append((4 * hits + 2 * description_hits - 2 * len(name_terms - query), item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].key))
    if not scored:
        return []
    return [item for score, item in scored if score >= scored[0][0] - 1][:4]


def preflight(root: Path, workflow: Workflow) -> tuple[Path | None, tuple[str, ...]]:
    project = workspace_path(root, workflow.project)
    if project is None or not workflow.argv:
        return None, ("No supported direct, single-script argument vector is declared.",)
    declaration = _text(project, workflow.declaration, 48 * 1024 if workflow.declaration == "README.md" else 64 * 1024,
                        prefix=workflow.declaration == "README.md")
    if declaration is None or hashlib.sha256(declaration.encode()).hexdigest() != workflow.declaration_sha256:
        return None, ("The workflow declaration changed or is no longer readable.",)
    target = workspace_path(project, workflow.argv[1])
    if target is None or not target.is_file():
        return None, (f"The declared entrypoint is missing or outside the project: {workflow.argv[1]}.",)
    required = list(workflow.requires)
    if workflow.declaration != ".qubitz/workflows.json":
        for folder in ("modes", "workflows", "docs"):
            doc = _text(project, f"{folder}/{workflow.name}.md", 16 * 1024, prefix=True) or ""
            section = re.search(r"(?im)^#{2,4}\s+(?:Configuration|Prerequisites|Required inputs)\s*$([\s\S]*?)(?=^#{1,4}\s|\Z)", doc)
            if section:
                required.extend(re.findall(r"(?im)^\s*(?:[-*]\s+)?(?:read|requires?)\s+`([^`\r\n]{1,160})`", section[1])[:8])
    issues = []
    for name in dict.fromkeys(required):
        path = workspace_path(project, name)
        if path is None or not path.exists():
            issues.append(f"Required project input not verified: {name}. Check the configured location or project setup.")
    for name in workflow.outputs:
        if workspace_path(project, name) is None:
            issues.append(f"Declared output is outside the allowed project scope: {name}.")
    return target, tuple(issues)


def output_hashes(root: Path, names: tuple[str, ...]) -> dict[str, str]:
    hashes = {}
    for name in names:
        path = workspace_path(root, name)
        if path is None:
            continue
        try:
            before = path.stat()
            if not stat.S_ISREG(before.st_mode) or before.st_size > 1024 * 1024:
                continue
            with path.open("rb") as stream:
                if _revision(before) != _revision(os.fstat(stream.fileno())):
                    continue
                data = stream.read(1024 * 1024 + 1)
                if _revision(before) != _revision(os.fstat(stream.fileno())):
                    continue
            if (len(data) == before.st_size and workspace_path(root, name) is not None
                    and _revision(before) == _revision(path.stat())):
                hashes[name] = hashlib.sha256(data).hexdigest()
        except OSError:
            continue
    return hashes


def render_result(workflow: Workflow, result, before: dict[str, str], after: dict[str, str]) -> str:
    value = getattr(result, "structuredContent", None)
    value = value if isinstance(value, dict) else {}
    receipt = value.get("execution_receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    started = receipt.get("started") or {}
    waited = receipt.get("exit") or {}
    code = value.get("returncode")
    if not (type(code) is int and receipt.get("actual_wait") is True and receipt.get("supervisor_actual_wait") is True
            and isinstance(started, dict) and isinstance(waited, dict) and started.get("identity")
            and started.get("token") and started.get("token") == waited.get("token")
            and started["identity"] == waited.get("identity") and waited.get("actual_wait") is True
            and waited.get("tree_verified") is True and waited.get("returncode") == code):
        reason = value.get("reason") or value.get("status") or "No matching owned execution receipt was returned."
        return (f"Project workflow `{workflow.key}` has no verified execution receipt: {str(reason)[:500]}\n"
                "No automatic retry was made.\n\n[Completion status: partial]")
    lines = [f"Project workflow `{workflow.key}` ran once and exited with code {code}."]
    changed = [name for name in workflow.outputs if name in after and before.get(name) != after[name]]
    if changed:
        lines.append("Verified new or changed declared files: " + ", ".join(f"`{name}`" for name in changed) + ".")
    if workflow.outputs and len(changed) != len(workflow.outputs):
        lines.append("Not all declared outputs were verified as new or changed.")
    for key in ("stdout", "stderr"):
        if output := str(value.get(key) or "").strip():
            lines.append(f"Captured {key} excerpt (data):\n" + "\n".join(f"    {line}" for line in output[:2000].splitlines()))
    lines.append("[Completion status: partial] Execution evidence does not establish that the requested outcome was achieved.")
    return "\n\n".join(lines)
