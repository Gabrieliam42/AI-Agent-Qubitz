"""Keep a WSL process and its children on one Windows-provided CUDA driver family."""
from __future__ import annotations

import ctypes
import os
import re
import sys
from pathlib import Path

_HANDLES: list = []
_PREPARED: Path | None = None


def mapped_libraries(text: str, stem: str) -> set[Path]:
    result = set()
    for line in text.splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6:
            path = Path(fields[5].removesuffix(" (deleted)"))
            if re.fullmatch(re.escape(stem) + r"\.so(?:\.[0-9]+)*", path.name):
                result.add(path.resolve())
    return result


def driver_directory(text: str, root: Path = Path("/usr/lib/wsl/drivers")) -> Path:
    paths = mapped_libraries(text, "libcuda")
    candidates = {path.parent for path in paths if path.is_relative_to(root.resolve())}
    if len(candidates) != 1:
        raise RuntimeError("The active Windows-provided WSL CUDA driver could not be identified uniquely")
    return candidates.pop()


def bind_wsl_driver() -> Path | None:
    global _PREPARED
    if _PREPARED is not None:
        return _PREPARED
    stub = Path("/usr/lib/wsl/lib/libcuda.so.1")
    if (sys.platform != "linux" or not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
            or not stub.is_file()):
        return None
    driver = ctypes.CDLL(str(stub))
    driver.cuInit.argtypes = [ctypes.c_uint]
    driver.cuInit.restype = ctypes.c_int
    result = driver.cuInit(0)
    if result == 100:  # CUDA_ERROR_NO_DEVICE permits CPU-only startup.
        return None
    if result:
        raise RuntimeError(f"WSL CUDA driver initialization failed with code {result}; no libraries were replaced")
    maps = Path("/proc/self/maps")
    text = maps.read_text()
    directory = driver_directory(text)
    compiler = (directory / "libnvidia-ptxjitcompiler.so.1").resolve()
    if not compiler.is_relative_to(directory) or not compiler.is_file():
        raise RuntimeError("The active WSL CUDA driver has no matching PTX JIT companion")
    loaded = mapped_libraries(text, "libnvidia-ptxjitcompiler")
    if loaded and loaded != {compiler}:
        raise RuntimeError("A conflicting PTX JIT library is already loaded; restart Qubitz before using CUDA")
    _HANDLES.extend((driver, ctypes.CDLL(str(compiler), mode=ctypes.RTLD_GLOBAL)))
    previous = os.environ.get("LD_LIBRARY_PATH", "")
    paths = [str(directory), *(item for item in previous.split(":") if item and item != str(directory))]
    os.environ["LD_LIBRARY_PATH"] = ":".join(paths)
    _PREPARED = directory
    return directory
