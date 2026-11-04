"""Exact resource admission and explicit browser-verification scope; no acquisition."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit


@dataclass(frozen=True)
class CachedResource:
    path: Path
    sha256: str
    version: str = ""


def resource_bytes(resource: CachedResource) -> bytes:
    if any(path.is_symlink() for path in (resource.path, *resource.path.parents)):
        raise ValueError("Aliased browser resource")
    raw = resource.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != resource.sha256:
        raise ValueError("Cached browser resource SHA-256 mismatch")
    return raw


def cached_browser_resources(root: Path | None = None) -> dict[str, CachedResource]:
    root = (root or Path(__file__).resolve().parents[1]).resolve()
    catalog = root / "runtime/browser_resources.json"
    if any(path.is_symlink() for path in (catalog, *catalog.parents)):
        raise ValueError("Aliased browser resource catalog")
    data = json.loads(catalog.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("resources"), list):
        raise ValueError("Unsupported browser resource catalog")
    result = {}
    for entry in data["resources"]:
        relative = entry["path"]
        if (not isinstance(relative, str) or "\\" in relative or ":" in relative
                or any(part in {"", ".", ".."} for part in relative.split("/"))):
            raise ValueError("Invalid browser resource path")
        path = root / relative
        if not path.resolve().is_relative_to(root) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
            raise ValueError("Browser resource escapes its catalog or lacks a pinned hash")
        resource = CachedResource(path, entry["sha256"], entry.get("version", ""))
        for url in entry["urls"]:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.fragment or parsed.username or url in result:
                raise ValueError("Invalid or duplicate admitted resource URL")
            result[url] = resource
    return result


def resource_capabilities() -> dict:
    available, unavailable = [], []
    for url, resource in cached_browser_resources().items():
        try:
            resource_bytes(resource)
        except (OSError, ValueError) as exc:
            unavailable.append({"url": url, "reason": str(exc)})
        else:
            available.append({"url": url, "sha256": resource.sha256, "version": resource.version})
    return {"available": available, "unavailable": unavailable, "acquisition_allowed": False,
            "scope": "Available exact cached bytes, not proof an artifact uses them or renders correctly."}


def verification_context(content: str | bytes, *, origin: str, network_policy: str,
                         engine: str = "chromium", admitted: dict[str, CachedResource] | None = None,
                         online_hosts: tuple[str, ...] | list[str] = ()) -> dict:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    return {"schema_version": 1, "artifact_sha256": hashlib.sha256(raw).hexdigest(),
            "origin": origin, "network_policy": network_policy, "browser_engine": engine,
            "admitted_resources": [{"url": url, "sha256": item.sha256, "version": item.version}
                                   for url, item in sorted((admitted or {}).items())],
            "online_hosts": sorted(online_hosts),
            "scope": "This resource policy only; load is not behavior, offline portability or scientific verification."}


class OfflineResources:
    """Serve only owned workspace files or explicitly hash-bound cached URLs."""

    def __init__(self, root: Path, cached: dict[str, CachedResource] | None = None,
                 *, origin: str = "http://127.0.0.1:18765"):
        self.root = root.resolve()
        self.origin = urlsplit(origin)
        self.cached = dict(cached or {})
        self.blocked: list[str] = []
        self.served: list[dict] = []
        self.failures: list[dict] = []

    def route(self, route):
        url = route.request.url
        try:
            parsed = urlsplit(url)
            resource = self.cached.get(url)
            if resource is not None:
                path = resource.path
                raw = resource_bytes(resource)
            elif parsed.scheme == self.origin.scheme and parsed.netloc == self.origin.netloc:
                path = self.root / unquote(parsed.path).lstrip("/")
                if not path.resolve().is_relative_to(self.root):
                    raise ValueError("Workspace resource escapes its root")
                if any(parent.is_symlink() for parent in (path, *path.parents)):
                    raise ValueError("Aliased browser resource")
                raw = path.read_bytes()
            else:
                self.blocked.append(url)
                route.abort()
                return
            digest = hashlib.sha256(raw).hexdigest()
            mime = "text/javascript" if path.suffix.lower() in {".js", ".mjs"} else mimetypes.guess_type(path)[0]
            route.fulfill(body=raw, content_type=mime, headers={"Access-Control-Allow-Origin": "*"})
            self.served.append({"url": url, "path": str(path), "bytes": len(raw), "sha256": digest})
        except (OSError, ValueError) as exc:
            self.failures.append({"url": url, "exception_type": type(exc).__name__, "message": str(exc)})
            self.blocked.append(url)
            route.abort()
