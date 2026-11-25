"""Backend-visible GPU discovery; no model loading, installs or device-mask changes."""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
from typing import Any


def child_environment(overrides: dict[str, str] | None = None, *, windows_interop: bool = False) -> dict[str, str] | None:
    """WSL does not forward arbitrary variables to Windows without WSLENV."""
    names = ("CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES",
             "GGML_VK_VISIBLE_DEVICES", "ONEAPI_DEVICE_SELECTOR", "SYCL_DEVICE_FILTER", "LLAMA_ARG_DEVICE")
    environment = {**os.environ, **(overrides or {})}
    selected = {name for name in names if name in environment}
    if windows_interop and selected:
        entries = [entry for entry in environment.get("WSLENV", "").split(":")
                   if entry and entry.split("/", 1)[0] not in selected]
        environment["WSLENV"] = ":".join([*entries, *(name + "/w" for name in sorted(selected))])
        return environment
    return environment if overrides else None


def native_path(executable: str) -> str:
    match = re.fullmatch(r"([A-Za-z]):[\\/](.*)", executable)
    if os.name == "posix" and match:
        return f"/mnt/{match[1].lower()}/" + match[2].replace("\\", "/")
    return executable


def parse_devices(output: str) -> list[dict[str, Any]]:
    devices = []
    seen = set()
    for line in output.splitlines():
        match = re.fullmatch(
            r"\s*([A-Za-z][\w-]*\d+): (.+) \((\d+) MiB, (\d+) MiB free\)\s*", line,
        )
        if not match:
            if re.match(r"\s*(?:CUDA|Vulkan|ROCm|HIP|SYCL|MUSA|Metal)\d+:", line):
                raise RuntimeError("Backend GPU inventory is unavailable or unrecognized: " + line.strip())
            continue
        identity, name, total, free = match.groups()
        if identity in seen or int(total) <= 0 or not 0 <= int(free) <= int(total):
            raise RuntimeError("Invalid or duplicate backend GPU memory record: " + identity)
        seen.add(identity)
        devices.append({"id": identity, "index": len(devices), "name": name,
                        "total_mib": int(total), "free_mib": int(free)})
    if not devices and "Available devices:" not in output:
        raise RuntimeError("Backend GPU inventory is unavailable or unrecognized.")
    return devices


def _legacy_cuda_devices() -> list[dict[str, Any]]:
    """The pinned ik backend lacks --list-devices; match driver ordinals by UUID.

    cuInit/device queries do not retain a CUDA context or allocate model buffers.
    Never equate nvidia-smi physical indices with masked CUDA ordinals.
    """
    driver = ctypes.CDLL("nvcuda.dll" if os.name == "nt" else "libcuda.so.1")

    def checked(name: str, *args: Any) -> None:
        code = getattr(driver, name)(*args)
        if code:
            raise RuntimeError(f"CUDA inventory {name} failed with code {code}.")

    checked("cuInit", 0)
    count = ctypes.c_int()
    checked("cuDeviceGetCount", ctypes.byref(count))
    if not count.value:
        return []
    query = subprocess.run(
        ["nvidia-smi", "--query-gpu=uuid,memory.free", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20, check=False,
    )
    if query.returncode:
        raise RuntimeError("Cannot verify free CUDA memory: " + query.stderr[:1024])
    memory = {}
    for line in query.stdout.splitlines():
        uuid, free = line.rsplit(",", 1)
        memory[uuid.strip().lower()] = int(free)
    devices = []
    for index in range(count.value):
        device = ctypes.c_int()
        checked("cuDeviceGet", ctypes.byref(device), index)
        uuid = (ctypes.c_ubyte * 16)()
        checked("cuDeviceGetUuid_v2" if hasattr(driver, "cuDeviceGetUuid_v2") else "cuDeviceGetUuid",
                ctypes.byref(uuid), device)
        raw = bytes(uuid).hex()
        identity = "GPU-" + "-".join((raw[:8], raw[8:12], raw[12:16], raw[16:20], raw[20:]))
        if identity.lower() not in memory:
            raise RuntimeError("Cannot bind CUDA device to its free-memory record: " + identity)
        name = ctypes.create_string_buffer(256)
        total = ctypes.c_size_t()
        checked("cuDeviceGetName", name, len(name), device)
        checked("cuDeviceTotalMem_v2", ctypes.byref(total), device)
        total_mib = total.value // 1024**2
        devices.append({"id": f"CUDA{index}", "index": index, "uuid": identity,
                        "name": name.value.decode("utf-8", errors="replace"),
                        "total_mib": total_mib, "free_mib": min(total_mib, memory[identity.lower()])})
    return devices


def discover_devices(executable: str, *, legacy_cuda: bool = False) -> list[dict[str, Any]]:
    try:
        if legacy_cuda:
            devices = _legacy_cuda_devices()
        else:
            options = {}
            if os.name == "posix" and executable.lower().endswith(".exe"):
                environment = child_environment(windows_interop=True)
                if environment is not None:
                    options["env"] = environment
            query = subprocess.run([native_path(executable), "--list-devices"], capture_output=True,
                                   text=True, timeout=30, check=False, **options)
            if query.returncode:
                raise RuntimeError("Backend GPU inventory failed: " + (query.stderr or query.stdout)[:2048])
            devices = parse_devices(query.stdout + "\n" + query.stderr)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise RuntimeError(f"Cannot verify backend-visible GPUs: {exc}") from exc
    requested = os.environ.get("LLAMA_ARG_DEVICE", "").strip()
    if requested == "none":
        return []
    if requested:
        names = requested.split(",")
        lookup = {device["id"]: device for device in devices}
        if len(names) != len(set(names)) or any(name not in lookup for name in names):
            raise RuntimeError("LLAMA_ARG_DEVICE does not match available backend devices.")
        return [lookup[name] for name in names]
    # Do not count CUDA/Vulkan aliases of one physical GPU as additional VRAM.
    families = {re.sub(r"\d+$", "", item["id"]) for item in devices}
    if len(families) > 1:
        if "CUDA" in families:
            devices = [item for item in devices if item["id"].startswith("CUDA")]
        else:
            raise RuntimeError("Multiple GPU backends detected; select one with LLAMA_ARG_DEVICE.")
    return devices


def device_names(devices: list[dict[str, Any]]) -> str:
    # The index fallback supports legacy saved planning records, not discovery.
    return ",".join(item.get("id", f"CUDA{item['index']}") for item in devices)


def usable_memory(devices: list[dict[str, Any]], margin: int = 1536) -> int:
    if any(int(item["free_mib"]) <= margin for item in devices):
        raise RuntimeError("A selected GPU cannot retain its per-device safety margin.")
    return sum(int(item["free_mib"]) - margin for item in devices)


def budgeted_fit_targets(devices: list[dict[str, Any]], budget_mib: int, margin_mib: int) -> list[int]:
    """Split one fit budget without multiplying it by the device count."""
    usable_memory(devices, margin_mib)
    totals = [int(item["total_mib"]) for item in devices]
    capacities = [total - margin_mib for total in totals]
    capacity = sum(capacities)
    allowance = min(capacity, max(0, budget_mib - margin_mib))
    # Base the targets on device totals, so increased free memory after the
    # inventory probe cannot increase the aggregate fitting budget.
    targets = []
    cumulative = allocated = 0
    for total, weight in zip(totals, capacities, strict=True):
        cumulative += weight
        allocation = allowance * cumulative // capacity
        targets.append(total - (allocation - allocated))
        allocated = allocation
    return targets
