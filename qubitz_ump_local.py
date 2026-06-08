from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _in_wsl() -> bool:
    try:
        version_text = Path("/proc/version").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    lowered = version_text.lower()
    return "microsoft" in lowered or "wsl" in lowered


def _workspace_kind(workspace: Path) -> str:
    normalized = workspace.resolve().as_posix()
    if _in_wsl():
        if re.match(r"^/mnt/[a-z]/", normalized, flags=re.IGNORECASE):
            return "windows_hosted_via_wsl"
        return "wsl_native"
    if re.match(r"^[A-Za-z]:[/\\\\]", str(workspace.resolve())):
        return "windows_native"
    return "local_native"


def _windows_from_wsl_path(path_text: str) -> str:
    match = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path_text)
    if not match:
        return ""
    drive = match.group(1).upper()
    remainder = match.group(2).replace("/", "\\")
    return f"{drive}:\\{remainder}" if remainder else f"{drive}:\\"


def _workspace_paths(workspace: Path) -> dict[str, str]:
    resolved = workspace.resolve()
    posix_path = resolved.as_posix()
    windows_path = ""
    if re.match(r"^[A-Za-z]:[/\\\\]", str(resolved)):
        windows_path = str(resolved).replace("/", "\\")
    elif posix_path.startswith("/mnt/"):
        windows_path = _windows_from_wsl_path(posix_path)
    return {
        "resolved_path": str(resolved),
        "posix_path": posix_path,
        "windows_path": windows_path,
        "workspace_kind": _workspace_kind(workspace),
    }


def _run_git(args: Sequence[str], workspace: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _project_key(workspace: Path) -> str:
    remote = _run_git(["config", "--get", "remote.origin.url"], workspace)
    if remote:
        normalized = remote.strip().rstrip("/")
        normalized = normalized.removesuffix(".git")
        if ":" in normalized and "://" not in normalized:
            normalized = normalized.split(":", 1)[1]
        else:
            normalized = normalized.rsplit("/", 2)[-2:]
            normalized = "/".join(normalized)
        return f"git:{normalized}"
    git_root = _run_git(["rev-parse", "--show-toplevel"], workspace)
    if git_root:
        return f"gitroot:{_stable_hash(Path(git_root).resolve().as_posix())}"
    return f"path:{_stable_hash(workspace.resolve().as_posix())}"


def _query_tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9_./:-]+", text.lower()))


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


class LocalUMPStore:
    def __init__(
        self,
        runtime_workspace: Path,
        workspace: Path,
        *,
        projection_path: Path | None = None,
        owner: str = "local",
        agent_name: str = "qubitz",
    ) -> None:
        self.runtime_workspace = runtime_workspace.resolve()
        self.workspace = workspace.resolve()
        self.owner = owner
        self.agent_name = agent_name
        self.store_dir = self.runtime_workspace / ".ump"
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = self.store_dir / "memory.ump.json"
        self.projection_path = projection_path or (self.runtime_workspace / ".memory" / "MEMORY.md")
        self.projection_path.parent.mkdir(parents=True, exist_ok=True)
        self.project_key = _project_key(self.workspace)
        self.workspace_meta = _workspace_paths(self.workspace)
        self._lock = threading.RLock()
        payload = self._load_payload()
        self._records: list[dict[str, Any]] = payload.get("records", [])
        self._bootstrap_legacy_projection()
        self.refresh_projection()

    def _load_payload(self) -> dict[str, Any]:
        if not self.store_path.exists():
            return {"version": "qubitz-ump-v1", "records": []}
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return {"version": "qubitz-ump-v1", "records": []}
        if not isinstance(payload, dict):
            return {"version": "qubitz-ump-v1", "records": []}
        records = payload.get("records")
        if not isinstance(records, list):
            payload["records"] = []
        return payload

    def _save(self) -> None:
        payload = {"version": "qubitz-ump-v1", "records": self._records}
        target_tmp = self.store_path.with_suffix(".tmp")
        target_tmp.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        target_tmp.replace(self.store_path)

    def _bootstrap_legacy_projection(self) -> None:
        if self._records or not self.projection_path.exists():
            return
        legacy_text = self.projection_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not legacy_text:
            return
        self.remember(
            kind="semantic",
            content=legacy_text,
            title="Legacy MEMORY.md import",
            tags=["legacy", "import"],
            source="legacy_memory_md",
            refresh_projection=False,
        )

    def _base_scope(self, *, project_only: bool) -> dict[str, str]:
        return {
            "owner": self.owner,
            "project": self.project_key if project_only else "global",
            "agent": self.agent_name,
            "visibility": "private",
        }

    def _record_matches_scope(self, record: dict[str, Any], *, project_only: bool, include_global_identity: bool) -> bool:
        scope = record.get("scope") or {}
        project = str(scope.get("project") or "")
        if project == self.project_key:
            return True
        if not project_only and project == "global":
            return True
        if include_global_identity and project == "global" and str(record.get("kind") or "") == "identity":
            return True
        return False

    def _active_records(self) -> list[dict[str, Any]]:
        return [record for record in self._records if str(record.get("state") or "active") == "active"]

    def count(self) -> int:
        return len(self._active_records())

    def summary_metadata(self) -> dict[str, Any]:
        return {
            "store_path": self.store_path.as_posix(),
            "projection_path": self.projection_path.as_posix(),
            "project_key": self.project_key,
            "record_count": self.count(),
            **self.workspace_meta,
        }

    def remember(
        self,
        *,
        kind: str,
        content: str,
        title: str = "",
        tags: Sequence[str] | None = None,
        project_only: bool = True,
        source: str = "qubitz",
        refresh_projection: bool = True,
    ) -> dict[str, Any]:
        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Memory content cannot be empty.")
        now = _utc_now()
        record = {
            "id": f"mem_{_stable_hash(f'{now}:{kind}:{title}:{normalized_content}', 20)}",
            "kind": kind.strip() or "semantic",
            "title": title.strip(),
            "content": normalized_content,
            "tags": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
            "scope": self._base_scope(project_only=project_only),
            "created_at": now,
            "updated_at": now,
            "valid_from": now,
            "valid_to": None,
            "state": "active",
            "source": source,
            "provenance": self.workspace_meta.copy(),
        }
        with self._lock:
            self._records.append(record)
            self._save()
            if refresh_projection:
                self.refresh_projection()
        return record

    def revise(
        self,
        record_id: str,
        *,
        content: str | None = None,
        title: str | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            for record in self._records:
                if record.get("id") != record_id:
                    continue
                if content is not None and content.strip():
                    record["content"] = content.strip()
                if title is not None:
                    record["title"] = title.strip()
                if tags is not None:
                    record["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
                record["updated_at"] = _utc_now()
                self._save()
                self.refresh_projection()
                return record
        raise KeyError(f"Unknown memory id: {record_id}")

    def forget(self, record_id: str, *, reason: str = "") -> dict[str, Any]:
        with self._lock:
            for record in self._records:
                if record.get("id") != record_id:
                    continue
                now = _utc_now()
                record["state"] = "forgotten"
                record["valid_to"] = now
                record["updated_at"] = now
                if reason.strip():
                    record["forget_reason"] = reason.strip()
                self._save()
                self.refresh_projection()
                return record
        raise KeyError(f"Unknown memory id: {record_id}")

    def search(
        self,
        query: str = "",
        *,
        kinds: Sequence[str] | None = None,
        limit: int = 8,
        project_only: bool = True,
        include_global_identity: bool = True,
    ) -> list[dict[str, Any]]:
        requested_kinds = {str(item).strip().lower() for item in (kinds or []) if str(item).strip()}
        query_norm = query.strip().lower()
        query_terms = _query_tokens(query_norm)
        ranked: list[tuple[tuple[int, int, str], dict[str, Any]]] = []
        for record in self._active_records():
            if requested_kinds and str(record.get("kind") or "").lower() not in requested_kinds:
                continue
            if not self._record_matches_scope(
                record,
                project_only=project_only,
                include_global_identity=include_global_identity,
            ):
                continue
            haystack = " ".join(
                [
                    str(record.get("title") or ""),
                    str(record.get("content") or ""),
                    " ".join(str(tag) for tag in record.get("tags") or []),
                ]
            ).lower()
            tokens = _query_tokens(haystack)
            overlap = len(query_terms & tokens)
            direct = 1 if query_norm and query_norm in haystack else 0
            if query_terms and overlap == 0 and direct == 0:
                continue
            updated = str(record.get("updated_at") or "")
            ranked.append(((-direct, -overlap, updated), record))
        ranked.sort(key=lambda item: item[0])
        if not query_terms and not query_norm:
            ranked = sorted(
                ((("0", "0", str(record.get("updated_at") or "")), record) for record in self._active_records()
                 if self._record_matches_scope(
                     record,
                     project_only=project_only,
                     include_global_identity=include_global_identity,
                 )
                 and (not requested_kinds or str(record.get("kind") or "").lower() in requested_kinds)),
                key=lambda item: item[1].get("updated_at") or "",
                reverse=True,
            )
        return [dict(item[1]) for item in ranked[: max(1, limit)]]

    def render_summary(
        self,
        query: str = "",
        *,
        kinds: Sequence[str] | None = None,
        limit: int = 6,
        max_chars: int = 1200,
        project_only: bool = True,
    ) -> str:
        results = self.search(
            query=query,
            kinds=kinds,
            limit=limit,
            project_only=project_only,
            include_global_identity=True,
        )
        if not results:
            return ""
        lines: list[str] = []
        remaining = max_chars
        for record in results:
            label = f"[{record.get('kind', 'semantic')}]"
            title = str(record.get("title") or "").strip()
            content = str(record.get("content") or "").strip().replace("\n", " ")
            text = f"- {label} {title}: {content}" if title else f"- {label} {content}"
            line = _shorten(text, 220)
            if len(line) + 1 > remaining:
                break
            lines.append(line)
            remaining -= len(line) + 1
        return "\n".join(lines).strip()

    def refresh_projection(self, *, max_items: int = 20, max_chars: int = 6000) -> str:
        summary = self.summary_metadata()
        lines = [
            "# Qubitz Memory",
            f"Updated: {_utc_now()}",
            f"Workspace: {self.workspace.as_posix()}",
            f"Project: {self.project_key}",
            "",
            "## Notes",
        ]
        for record in self.search(limit=max_items, project_only=True, include_global_identity=True):
            label = f"[{record.get('kind', 'semantic')}]"
            title = str(record.get("title") or "").strip()
            content = str(record.get("content") or "").strip().replace("\n", " ")
            item = f"- {label} {title}: {content}" if title else f"- {label} {content}"
            lines.append(_shorten(item, 280))
        lines.extend(
            [
                "",
                "## Runtime",
                f"- UMP store: {summary['store_path']}",
                f"- Record count: {summary['record_count']}",
                f"- Workspace kind: {summary['workspace_kind']}",
            ]
        )
        text = _shorten("\n".join(lines).strip(), max_chars)
        self.projection_path.write_text(text, encoding="utf-8")
        return text
