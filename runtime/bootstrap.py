"""Install only missing application dependencies into the active project environment."""

from __future__ import annotations

import importlib.metadata
import json
import re
import sys
from pathlib import Path
from uuid import uuid4

from .native_runtime import acquisition_lock, offline, run_owned, uv_executable

CORE = (
    "cryptography",
    "httpx",
    "pydantic",
    "mcp",
    "numpy",
    "huggingface-hub",
    "transformers",
    "torch",
    "faiss-gpu-cu13-cuvs",
    "cmake",
    "ninja",
)


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_lines(path: Path) -> tuple[dict[str, str], list[str]]:
    entries, indexes = {}, []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("--index-url ", "--extra-index-url ")):
            option, value = line.split(maxsplit=1)
            if not value.startswith("https://"):
                raise ValueError("Automatic dependency indexes must use HTTPS")
            indexes.extend((option, value))
            continue
        if line.startswith("-"):
            raise ValueError("Unsupported automatic dependency manifest option")
        name = re.match(r"([A-Za-z0-9][A-Za-z0-9_.-]*)", line)
        if not name:
            raise ValueError("Invalid dependency manifest entry")
        key = normalized(name[1])
        if key in entries:
            raise ValueError(f"Duplicate dependency manifest entry: {key}")
        entries[key] = line
    return entries, indexes


def ensure_application_dependencies(root: Path) -> None:
    if sys.platform != "linux":
        raise RuntimeError("The GLM native variant requires its project-local WSL environment")
    root = root.resolve()
    entries, indexes = requirement_lines(root / "requirements.txt")
    pending = list(CORE)
    missing = [name for name in (*pending, "packaging") if not distribution(name)]
    # Even read-only imports must not mutate an already complete active environment.
    if missing and (
        sys.prefix == sys.base_prefix
        or not Path(sys.prefix).resolve().is_relative_to(root)
    ):
        raise RuntimeError(
            "Automatic dependency setup requires the active project-local virtual environment"
        )
    uv = uv_executable()
    work = None

    def install(name, dependency=None):
        nonlocal work
        if (
            offline()
            or "--network-mode=offline" in sys.argv
            or any(
                sys.argv[index : index + 2] == ["--network-mode", "offline"]
                for index in range(len(sys.argv))
            )
        ):
            raise RuntimeError(
                f"Missing essential package {name}; automatic downloads are disabled offline"
            )
        if not uv:
            raise RuntimeError(
                "Install uv to enable automatic missing-dependency setup"
            )
        if sys.prefix == sys.base_prefix or not Path(
            sys.prefix
        ).resolve().is_relative_to(root):
            raise RuntimeError(
                "Refusing to install dependencies outside the active project virtual environment"
            )
        entry = entries.get(normalized(name))
        if not entry:
            raise RuntimeError(
                f"Missing essential dependency {name} has no pinned project manifest entry"
            )
        if dependency is not None:
            from packaging.requirements import Requirement

            planned = Requirement(entry)
            pins = [part.version for part in planned.specifier if part.operator == "=="]
            if pins and not dependency.specifier.contains(pins[0], prereleases=True):
                raise RuntimeError(
                    f"Missing {name} has a manifest pin incompatible with the current stack; existing packages were preserved"
                )
        if work is None:
            work = root / ".cache/dependency-preparation" / uuid4().hex
            work.mkdir(parents=True)
        print(
            f"[status] Installing missing essential package {name} into {sys.prefix}.",
            flush=True,
        )
        run_owned(
            [
                uv,
                "--system-certs",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--no-deps",
                *indexes,
                entry,
            ],
            root=root,
            directory=work / normalized(name),
            timeout=1800,
        )
        importlib.invalidate_caches()

    with acquisition_lock(root):
        if not distribution("packaging"):
            install("packaging")
        from packaging.requirements import Requirement

        checked = set()
        while pending:
            name = normalized(pending.pop(0))
            if name in checked:
                continue
            package = distribution(name)
            if package is None:
                install(name)
                package = distribution(name)
                if package is None:
                    raise RuntimeError(
                        f"Essential dependency {name} is still unavailable after installation"
                    )
            checked.add(name)
            for raw in package.requires or ():
                requirement = Requirement(raw)
                if requirement.marker and not requirement.marker.evaluate(
                    {"extra": ""}
                ):
                    continue
                child = distribution(requirement.name)
                if child is None:
                    install(requirement.name, requirement)
                elif requirement.specifier and not requirement.specifier.contains(
                    child.version, prereleases=True
                ):
                    raise RuntimeError(
                        f"Existing dependency {requirement.name}=={child.version} is incompatible with {package.metadata['Name']}; no packages were replaced"
                    )
                pending.append(requirement.name)
        if work is not None:
            (work / "complete.json").write_text(
                json.dumps({"checked": sorted(checked), "python": sys.executable})
                + "\n"
            )


def distribution(name):
    try:
        return importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
