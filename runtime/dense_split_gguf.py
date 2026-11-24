"""Pinned shard acquisition and GPU-first fitting for split GGUFs."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from .gpu_devices import device_names, discover_devices, native_path
from .model_acquisition import _qubitz_model_download_offline

MIB = 1024**2
GIB = 1024**3
DEFAULT_KV_BYTES_PER_TOKEN = 80 * 8 * 128 * 4
REQUIRED_FLAGS = ("--device", "--fit", "--fit-ctx", "--fit-target", "--split-mode")
FIT_OPTIONS = frozenset((*REQUIRED_FLAGS, "--ctx-size", "--n-gpu-layers", "--main-gpu",
                         "--tensor-split", "--cache-type-k", "--cache-type-v"))


def validate_shards(first, names, sizes, *, cache_root=None):
    first = Path(first)
    if first.name != names[0]:
        raise ValueError(f"Model path must name the first shard: {names[0]}")
    for name, size in zip(names, sizes, strict=True):
        path = first.with_name(name)
        if cache_root is not None and not path.resolve().is_relative_to(cache_root):
            raise ValueError(f"Managed model shard resolves outside the project: {name}")
        if not path.is_file() or path.stat().st_size != size:
            raise FileNotFoundError(f"GGUF shard is missing or has the wrong size: {name}")
        with path.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError(f"Invalid GGUF header: {name}")
    return first.resolve()


def fit_arguments(devices, weight_bytes, num_ctx, *, max_context, margin_mib, kv_type, host_available,
                  kv_bytes_per_token=DEFAULT_KV_BYTES_PER_TOKEN):
    if not 0 < num_ctx <= max_context:
        raise ValueError(f"Requested context must be between 1 and {max_context:,} tokens.")
    if not devices:
        raise RuntimeError("No compatible GPUs are visible to the selected llama.cpp backend.")
    usable = [int(device["free_mib"]) - margin_mib for device in devices]
    if any(mib <= 0 for mib in usable):
        raise RuntimeError(f"A selected GPU cannot retain the {margin_mib} MiB safety margin.")
    # Count full-window F16 K+V even for local-attention layers and quantized cache.
    scratch = 4 * GIB
    full_kv_upper = num_ctx * kv_bytes_per_token
    host_estimate = max(0, weight_bytes + full_kv_upper + scratch - sum(usable) * MIB)
    reserve = 8 * GIB
    if host_available < host_estimate + reserve:
        raise RuntimeError(
            f"Insufficient verified host memory for GPU overflow: available {host_available / GIB:.1f} GiB, "
            f"conservative estimate {host_estimate / GIB:.1f} GiB plus {reserve // GIB} GiB reserve. "
            "Free memory, lower the requested context, or choose a lighter quantization; paging is not counted."
        )
    return ["--ctx-size", str(num_ctx), "--device", device_names(devices), "--split-mode", "layer",
            "--n-gpu-layers", "auto", "--fit", "on", "--fit-ctx", str(num_ctx),
            "--fit-target", ",".join(str(margin_mib) for _ in devices),
            "--cache-type-k", kv_type, "--cache-type-v", kv_type]


def _windows_host_available():
    executable = shutil.which("powershell.exe") or "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
    script = (
        "$ErrorActionPreference='Stop'; "
        "$m=Get-CimInstance Win32_PerfFormattedData_PerfOS_Memory; "
        "@([UInt64]$m.AvailableBytes,[UInt64]$m.CommitLimit,[UInt64]$m.CommittedBytes) | ConvertTo-Json -Compress"
    )
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, check=True,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    free, limit, committed = json.loads(result.stdout)
    if not all(type(value) is int and value >= 0 for value in (free, limit, committed)) or not 0 <= committed <= limit:
        raise RuntimeError("Invalid Windows physical-memory or commit snapshot.")
    return min(free, limit - committed)


def host_available_bytes(executable, *, in_wsl):
    try:
        if os.name == "nt" or in_wsl:
            host = _windows_host_available()
            if os.name == "nt" or executable.lower().endswith(".exe"):
                return host
        else:
            host = 2**64
        text = Path("/proc/meminfo").read_text(encoding="ascii")
        row = re.search(r"(?m)^MemAvailable:\s+(\d+) kB$", text)
        if row is None:
            raise ValueError("MemAvailable not reported")
        return min(host, int(row[1]) * 1024)
    except (OSError, ValueError, TypeError, subprocess.SubprocessError) as exc:
        raise RuntimeError("Cannot verify available host RAM and commit for dense-model CPU overflow.") from exc


def strip_fit_options(argv):
    kept = []
    index = 0
    while index < len(argv):
        option, separator, _ = argv[index].partition("=")
        if option in FIT_OPTIONS:
            index += 1 if separator else 2
        else:
            kept.append(argv[index])
            index += 1
    return kept


def rewrite_launch_command(command, configure):
    """Keep the shared owner tag and command shape; only rewrite the backend argv."""
    command = list(command)
    if Path(command[0]).name.casefold() != "powershell.exe":
        return configure(command)
    index = next(i + 1 for i, arg in enumerate(command) if arg.lower() == "-encodedcommand")
    lines = base64.b64decode(command[index], validate=True).decode("utf-16-le").splitlines()
    invocations = [i for i, line in enumerate(lines) if line.startswith("& ")]
    if len(invocations) != 1:
        raise RuntimeError("Expected one managed llama.cpp PowerShell invocation.")
    invocation = invocations[0]

    def quote(value):
        return "'" + value.replace("'", "''") + "'"

    argv = [token.replace("''", "'") for token in re.findall(r"'((?:[^']|'')*)'", lines[invocation][2:])]
    if not argv or "& " + " ".join(map(quote, argv)) != lines[invocation]:
        raise RuntimeError("Unexpected managed llama.cpp argument quoting.")
    lines[invocation] = "& " + " ".join(map(quote, configure(argv)))
    command[index] = base64.b64encode("\n".join(lines).encode("utf-16-le")).decode("ascii")
    return command


def require_backend(executable, minimum):
    result = subprocess.run([native_path(executable), "--version"], capture_output=True, text=True,
                            timeout=20, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = result.stdout + "\n" + result.stderr
    line = re.search(r"(?im)^\s*version:\s*(.+)$", output)
    reported = line[1].strip() if line else ""
    # Older builds report only a number; v1.0+ can report a semantic version and a separate build number.
    numeric = re.match(r"b?(\d+)(?=\s|$)", reported)
    build = re.search(r"\bbuild\s+(\d+)\b", reported, re.IGNORECASE)
    semver = re.match(r"v?(\d+)\.(\d+)\.(\d+)(?=\s|$)", reported)
    build_number = int(numeric[1]) if numeric else int(build[1]) if build else 0
    compatible = (build_number >= minimum if build_number else
                  semver is not None and tuple(map(int, semver.groups())) >= (1, 0, 0))
    if not compatible:
        raise RuntimeError(f"This split-GGUF variant requires stock llama.cpp b{minimum} or newer.")


def install(app, *, subdir, revision, names, sizes, max_context, margin_mib, kv_type, tag, preflight,
            release=None, minimum_build=None, kv_bytes_per_token=DEFAULT_KV_BYTES_PER_TOKEN,
            runtime_channel="dense-split-isolated"):
    base = app.base
    repo = base.DEFAULT_HF_GGUF_REPO_ID
    original_resolve = base.resolve_gguf_model_path
    server = base.LlamaCppServerProcess
    original_launch = server._launch_command

    base.DEFAULT_LLAMACPP_RELEASE_API_URL = f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{tag}"
    base.llamacpp_runtime_dir = lambda workspace: workspace / ".cache" / f"llama.cpp-{tag}"
    base.llamacpp_native_runtime_dir = lambda workspace: workspace / ".cache" / f"llama.cpp-{tag}-linux"
    base.QUBITZ_LLAMACPP_RUNTIME_TAG = tag
    base.QUBITZ_LLAMACPP_RUNTIME_RELEASE = release or tag
    base.QUBITZ_LLAMACPP_RUNTIME_CHANNEL = runtime_channel

    def resolve(workspace, configured_path=None, preferred_filename=names[0]):
        explicit = configured_path or os.environ.get(base.GGUF_MODEL_PATH_ENV_NAME, "").strip()
        root = Path(workspace).resolve()
        if preferred_filename != names[0]:
            raise ValueError(f"Expected first shard {names[0]}")
        if explicit:
            found = original_resolve(workspace, explicit, names[0])
            if found is None:
                raise FileNotFoundError(f"Configured shard is unavailable: {names[0]}")
            return validate_shards(found, names, sizes)
        canonical = root / ".cache" / "models" / repo / subdir / names[0]
        if canonical.exists():
            try:
                return validate_shards(canonical, names, sizes, cache_root=root)
            except (FileNotFoundError, ValueError):
                pass
        local = original_resolve(workspace, None, names[0])
        if local is not None:
            try:
                return validate_shards(local, names, sizes, cache_root=root)
            except (FileNotFoundError, ValueError):
                pass
        return None

    def download(workspace, filename=names[0]):
        if filename != names[0]:
            raise ValueError(f"Download must start with {names[0]}")
        if (existing := resolve(workspace)) is not None:
            return existing
        if _qubitz_model_download_offline():
            raise RuntimeError("Model shards are missing or incomplete, and downloads are disabled offline.")
        from huggingface_hub import hf_hub_download

        receipt = preflight(workspace, repo, filename, subdir=subdir, revision=revision, repair=False)
        expected = {(Path(subdir) / name).as_posix(): size for name, size in zip(names, sizes, strict=True)}
        if {item["file"]: item["size"] for item in receipt["files"]} != expected:
            raise RuntimeError("Pinned shard metadata does not match this launcher; no download started.")
        directory = Path(workspace) / ".cache" / "models" / repo
        for name, size in zip(names, sizes, strict=True):
            path = directory / subdir / name
            if path.exists():
                if path.is_symlink() or not path.resolve().is_relative_to(Path(workspace).resolve()):
                    raise RuntimeError(f"Refusing to modify an aliased shard: {name}")
                with path.open("rb") as handle:
                    complete = path.stat().st_size == size and handle.read(4) == b"GGUF"
                if complete:
                    continue
                raise RuntimeError(f"Incorrect cached shard: {path}; move it aside before acquisition.")
            hf_hub_download(repo_id=repo, filename=(Path(subdir) / name).as_posix(), revision=revision,
                            local_dir=directory)
        return validate_shards(directory / subdir / names[0], names, sizes, cache_root=Path(workspace).resolve())

    download._qubitz_storage_preflight = True
    base.resolve_gguf_model_path = resolve
    base.download_default_gguf_model = download

    def launch(self):
        def configured(argv):
            executable = argv[0]
            model = native_path(argv[argv.index("--model") + 1])
            validate_shards(model, names, sizes)
            require_backend(executable, minimum_build if minimum_build is not None else int(tag[1:]))
            args = fit_arguments(discover_devices(executable), sum(sizes), self.config.num_ctx,
                                 max_context=max_context, margin_mib=margin_mib, kv_type=kv_type,
                                 host_available=host_available_bytes(executable, in_wsl=base.in_wsl()),
                                 kv_bytes_per_token=kv_bytes_per_token)
            return [*strip_fit_options(argv), *args]

        return rewrite_launch_command(original_launch(self), configured)

    server._launch_command = launch
