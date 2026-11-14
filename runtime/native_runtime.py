"""Pinned, project-local native runtime acquisition; never starts a model server."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import site
import subprocess
import sys
import tarfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

COMMIT = "d741de5074cd424dd3ba7cfc4d9b7649f1eb0463"
ARCHIVE_SHA256 = "7ea2463e3d1bc9051446cf1a2dc23248f3f09ebb2eeb5054d5ef3427256043b9"
LOADER_BEFORE = "3c16bbd9bcad46f6326f4a04929066d037f5a949f11c1fd24008060df1b735f1"
LOADER_AFTER = "8a5456ed952cb670eb29c5635f4617cdb7c28266485008664674b242b6320322"
FILES = (
    "bin/llama-server",
    "src/libllama.so",
    "ggml/src/libggml.so",
    "examples/mtmd/libmtmd.so",
)
TOOLCHAINS = {
    "cu130": (
        "nvidia-cuda-runtime==13.0.48",
        "nvidia-cuda-crt==13.0.88",
        "nvidia-nvvm==13.0.88",
        "nvidia-cuda-nvcc==13.0.88",
        "nvidia-cuda-cccl==13.0.85",
    ),
    "cu134": (
        "nvidia-cuda-runtime==13.4.92",
        "nvidia-cuda-crt==13.4.92",
        "nvidia-nvvm==13.4.92",
        "nvidia-cuda-nvcc==13.4.92",
        "nvidia-cuda-cccl==13.3.4.3.1",
    ),
}
_INSTALL_CACHE: dict[str, tuple] = {}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def uv_executable() -> str | None:
    for candidate in (Path(sys.executable).parent / "uv", shutil.which("uv"), Path.home() / ".local/bin/uv"):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def offline() -> bool:
    return os.environ.get("QUBITZ_NETWORK_MODE", "").strip().lower() in {
        "off", "offline", "disabled", "local", "local_only",
    } or any(
        os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}
        for name in ("QUBITZ_OFFLINE", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    )


def cancelled(event: threading.Event | None) -> None:
    if event is not None and event.is_set():
        raise InterruptedError("Selected runtime preparation cancelled")


def runtime_destination(root: Path, profile: str) -> Path:
    if profile not in TOOLCHAINS:
        raise ValueError("Unsupported native runtime profile")
    path = (
        root
        / ".cache"
        / "runtimes"
        / f"glm53-{COMMIT[:8]}-{LOADER_AFTER[:12]}-{profile}-sm86"
    )
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Native runtime cache escapes the project root")
    return path


def installed(root: Path, profile: str) -> tuple[Path, dict] | None:
    target = runtime_destination(root, profile)
    receipt = target / "installed.json"
    if not receipt.is_file() or receipt.is_symlink():
        return None
    try:
        record = json.loads(receipt.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        if (
            record.get("commit") != COMMIT
            or record.get("archive_sha256") != ARCHIVE_SHA256
            or record.get("loader_sha256") != LOADER_AFTER
            or record.get("profile") != profile
            or type(record.get("validation_exit")) is not int
            or record["validation_exit"] != 0
            or set(record.get("fingerprints", {})) != set(FILES)
        ):
            return None
        hashes = record["fingerprints"]
        if not all(
            isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value)
            for value in hashes.values()
        ):
            return None
        build = Path(record["build_directory"])
        relative = build.resolve().relative_to(
            (root / ".cache/runtime-builds").resolve()
        )
        if (
            len(relative.parts) != 2
            or relative.parts[1] != "build"
            or not re.fullmatch("[a-f0-9]{32}", relative.parts[0])
        ):
            return None
        paths = [receipt, *(build / name for name in FILES)]

        def identity():
            return tuple(
                (
                    str(path.resolve()),
                    value.st_dev,
                    value.st_ino,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )
                for path in paths
                for value in [path.stat()]
            )

        current = identity()
        cached = _INSTALL_CACHE.get(str(target))
        if cached is not None and cached == current:
            return build.parent, hashes
        if (
            all(
                (build / name).is_file()
                and (build / name).resolve().is_relative_to(build.resolve())
                and digest(build / name) == hashes[name]
                for name in FILES
            )
            and identity() == current
        ):
            _INSTALL_CACHE[str(target)] = current
            return build.parent, hashes
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return None


@contextmanager
def acquisition_lock(root: Path, event=None):
    import fcntl

    lock = root / ".cache" / "runtime-acquisition.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as stream:
        while True:
            cancelled(event)
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def run_owned(
    argv, *, root: Path, directory: Path, event=None, timeout=1800, environment=None,
    expected_exit_codes=(0,),
):
    from .managed_process import launch_command_session

    directory.mkdir(parents=True, exist_ok=False)
    with (
        (directory / "stdout.log").open("wb") as out,
        (directory / "stderr.log").open("wb") as err,
    ):
        command = launch_command_session(
            list(map(str, argv)),
            cwd=root,
            directory=directory / "process",
            stdout=out,
            stderr=err,
            environment=environment or {},
            cancel_event=event,
        )
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = command.wait(timeout=0.25)
                break
            except subprocess.TimeoutExpired:
                if event is not None and event.is_set() or time.monotonic() >= deadline:
                    outcome = command.stop(timeout=60)
                    if not outcome.get("stopped"):
                        raise RuntimeError(
                            f"Preparation cleanup unverified; do not retry: {directory}"
                        )
                    raise InterruptedError(
                        f"Preparation cancelled or timed out: {directory}"
                    )
    if result not in expected_exit_codes:
        raise RuntimeError(
            f"Runtime preparation command exited {result}; logs: {directory}"
        )
    return result


def download_archive(root: Path, event=None, report=None) -> Path:
    cache = root / ".cache" / "downloads" / "runtimes"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"ik_llama-{COMMIT}.tar.gz"
    if target.is_file() and digest(target) == ARCHIVE_SHA256:
        return target
    # Reuse a retained, independently bound download without touching old evidence.
    retained = (
        root / ".cache/glm53_backend_restore_20261112_01" / f"upstream-{COMMIT}.tar.gz"
    )
    if retained.is_file() and digest(retained) == ARCHIVE_SHA256:
        candidate = cache / f"{uuid4().hex}.tar.gz"
        shutil.copyfile(retained, candidate)
    else:
        if offline():
            raise RuntimeError(
                "Pinned runtime source is missing; downloads are disabled offline"
            )
        candidate = cache / f"{uuid4().hex}.partial"
        url = f"https://github.com/ikawrakow/ik_llama.cpp/archive/{COMMIT}.tar.gz"
        with urlopen(
            Request(url, headers={"User-Agent": "Qubitz-runtime-bootstrap"}), timeout=30
        ) as response:
            if urlparse(response.url).scheme != "https" or urlparse(
                response.url
            ).hostname not in {"github.com", "codeload.github.com"}:
                raise RuntimeError(
                    "Runtime source redirected outside the pinned HTTPS provider"
                )
            total = 0
            with candidate.open("xb") as output:
                while block := response.read(1024 * 1024):
                    cancelled(event)
                    total += len(block)
                    if total > 256 * 1024**2:
                        raise RuntimeError(
                            "Runtime source archive exceeds its bounded download size"
                        )
                    output.write(block)
                    if report and total % (8 * 1024**2) == 0:
                        report(
                            "downloading",
                            f"Downloaded {total // 1024**2} MiB of pinned runtime source.",
                        )
    if digest(candidate) != ARCHIVE_SHA256:
        raise RuntimeError(
            f"Runtime source checksum mismatch; retained for inspection: {candidate}"
        )
    if target.exists():
        target.rename(target.with_name(target.name + ".invalid-" + uuid4().hex))
    candidate.rename(target)
    return target


def extract_and_patch(archive: Path, stage: Path) -> Path:
    if digest(archive) != ARCHIVE_SHA256:
        raise ValueError("Unverified runtime source archive")
    with tarfile.open(archive) as stream:
        members = stream.getmembers()
        if len(members) > 20000 or sum(row.size for row in members) > 2 * 1024**3:
            raise ValueError("Runtime source extraction exceeds bounds")
        stream.extractall(stage, filter="data")
    sources = [path for path in stage.iterdir() if path.is_dir()]
    if len(sources) != 1:
        raise ValueError("Pinned runtime source must have one root")
    source = sources[0]
    loader = source / "src/llama-model-loader.cpp"
    if digest(loader) != LOADER_BEFORE:
        raise ValueError(
            "Pinned loader source does not match the qualified patch input"
        )
    original = loader.read_text(encoding="utf-8").splitlines(keepends=True)
    patch = (
        Path(__file__)
        .with_name("glm53_loader.patch")
        .read_text(encoding="utf-8")
        .splitlines(keepends=True)
    )
    result, cursor, index = [], 0, 0
    while index < len(patch):
        header = re.match(r"@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@", patch[index])
        if not header:
            index += 1
            continue
        start = int(header[1]) - 1
        if start < cursor:
            raise ValueError("Overlapping native patch hunks")
        result.extend(original[cursor:start])
        cursor, index = start, index + 1
        while index < len(patch) and not patch[index].startswith("@@"):
            line = patch[index]
            if line[0] in {" ", "-"}:
                if cursor >= len(original) or original[cursor] != line[1:]:
                    raise ValueError("Native patch context mismatch")
                cursor += 1
            if line[0] in {" ", "+"}:
                result.append(line[1:])
            index += 1
    result.extend(original[cursor:])
    encoded = "".join(result).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != LOADER_AFTER:
        raise ValueError("Native patch output does not match the qualified source")
    loader.write_bytes(encoded)
    return source


def prepare_toolchain(root: Path, profile: str, work: Path, event=None) -> Path:
    uv = uv_executable()
    if not uv:
        raise RuntimeError(
            "Install uv before automatic runtime preparation; pip fallback is disabled"
        )
    venv = root / ".cache/toolchains" / profile / ".venv"
    python = venv / "bin/python"
    if not python.is_file():
        run_owned(
            [uv, "venv", "--python", sys.executable, str(venv)],
            root=root,
            directory=work / "toolchain-venv",
            event=event,
        )
    for index, requirement in enumerate(TOOLCHAINS[profile]):
        run_owned(
            [
                uv,
                "--system-certs",
                "pip",
                "install",
                "--python",
                python,
                "--no-deps",
                "--only-binary",
                ":all:",
                requirement,
            ],
            root=root,
            directory=work / f"toolchain-package-{index}",
            event=event,
        )
    toolkit = next(iter((venv / "lib").glob("python*/site-packages/nvidia/cu13")), None)
    if toolkit is None or not (toolkit / "bin/nvcc").is_file():
        raise RuntimeError("The isolated CUDA compiler package is incomplete")
    return toolkit


@contextmanager
def preparation_attempt(root: Path, profile: str):
    """Never redispatch while any preceding managed command lacks a verified exit."""
    pending = runtime_destination(root, profile).with_suffix(".pending.json")
    pending.parent.mkdir(parents=True, exist_ok=True)
    if pending.exists():
        previous = json.loads(pending.read_text(encoding="utf-8"))
        work = Path(previous["work"])
        relative = work.resolve().relative_to(
            (root / ".cache/runtime-builds").resolve()
        )
        if len(relative.parts) != 1 or not re.fullmatch(
            "[a-f0-9]{32}", relative.parts[0]
        ):
            raise RuntimeError(
                "Previous preparation ownership is invalid; refusing a retry"
            )
        for launch in work.glob("*/process/process_launch.json"):
            try:
                started = json.loads(launch.read_text())
                ended = json.loads(launch.with_name("process_exit.json").read_text())
                waited = json.loads(
                    launch.with_name("supervisor_wait.json").read_text()
                )
            except (OSError, ValueError) as error:
                raise RuntimeError(
                    f"Previous preparation outcome is unverified; do not retry: {work}"
                ) from error
            if (
                ended.get("token") != started.get("token")
                or ended.get("actual_wait") is not True
                or ended.get("tree_verified") is not True
                or ended.get("error")
                or waited.get("token") != started.get("token")
                or waited.get("actual_wait") is not True
            ):
                raise RuntimeError(
                    f"Previous preparation cleanup is unverified; do not retry: {work}"
                )
        pending.rename(work / f"reconciled-preparation-{uuid4().hex}.json")
    work = root / ".cache/runtime-builds" / uuid4().hex
    work.mkdir(parents=True)
    with pending.open("x", encoding="utf-8") as stream:
        json.dump({"work": str(work), "profile": profile, "status": "started"}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    # Exceptions skip publication and retain the intent for explicit reconciliation.
    yield work
    pending.rename(work / "completed-preparation.json")


def ensure_glm_runtime(
    root: Path, profile: str, *, report=None, cancel_event=None
) -> dict:
    root = Path(root).resolve()
    found = installed(root, profile)
    if found:
        return {"runtime_dir": found[0], "fingerprints": found[1], "built": False}
    if (
        sys.platform != "linux"
        or not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    ):
        raise RuntimeError("Automatic GLM native runtime preparation requires WSL")
    if offline():
        raise RuntimeError(
            "Compatible GLM runtime is missing; automatic preparation is disabled offline"
        )
    with acquisition_lock(root, cancel_event):
        found = installed(root, profile)
        if found:
            return {"runtime_dir": found[0], "fingerprints": found[1], "built": False}
        if shutil.disk_usage(root).free < 22 * 1024**3:
            raise RuntimeError(
                "Native runtime preparation requires at least 22 GiB free disk space"
            )
        with preparation_attempt(root, profile) as work:
            return _build_glm_runtime(
                root, profile, work, report=report, cancel_event=cancel_event
            )


def _build_glm_runtime(
    root: Path, profile: str, work: Path, *, report=None, cancel_event=None
) -> dict:
    notify = report or (lambda state, text: print(f"[{state}] {text}", flush=True))
    notify("downloading", "Preparing the pinned GLM-compatible native runtime.")
    archive = download_archive(root, cancel_event, notify)
    stage = work / "source"
    stage.mkdir()
    source = extract_and_patch(archive, stage)
    notify(
        "building",
        "Preparing the isolated CUDA build toolchain; the application stack is unchanged.",
    )
    toolkit = prepare_toolchain(root, profile, work, cancel_event)
    def tool(name):
        local = Path(sys.executable).parent / name
        return str(local) if local.is_file() else shutil.which(name)

    cmake, ninja, compiler = tool("cmake"), tool("ninja"), tool("g++-12")
    if not cmake or not ninja or not compiler:
        raise RuntimeError(
            "Native build needs cmake, ninja and g++-12; missing OS compiler needs separate setup"
        )
    cuda = next(
        (
            Path(path) / "nvidia/cu13"
            for path in site.getsitepackages()
            if (Path(path) / "nvidia/cu13/lib/libcublas.so.13").is_file()
        ),
        None,
    )
    if cuda is None:
        raise RuntimeError("The selected application CUDA/cuBLAS stack is incomplete")
    build = work / "build"
    configure = [
        cmake,
        "-S",
        source,
        "-B",
        build,
        "-G",
        "Ninja",
        f"-DCMAKE_MAKE_PROGRAM={ninja}",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DGGML_CUDA=ON",
        "-DCMAKE_CUDA_ARCHITECTURES=86",
        "-DLLAMA_CURL=OFF",
        "-DLLAMA_BUILD_TESTS=OFF",
        "-DBUILD_SHARED_LIBS=ON",
        "-DGGML_CCACHE=OFF",
        f"-DCMAKE_CUDA_COMPILER={toolkit}/bin/nvcc",
        f"-DCUDAToolkit_ROOT={toolkit}",
        f"-DCMAKE_CUDA_HOST_COMPILER={compiler}",
        f"-DCMAKE_CXX_COMPILER={compiler}",
        f"-DCMAKE_CUDA_FLAGS=--threads=2 -I{cuda}/include",
        f"-DCUDA_CUDART={cuda}/lib/libcudart.so.13",
        f"-DCUDA_cudart_LIBRARY={cuda}/lib/libcudart.so.13",
        f"-DCUDA_cublas_LIBRARY={cuda}/lib/libcublas.so.13",
        f"-DCUDA_cublasLt_LIBRARY={cuda}/lib/libcublasLt.so.13",
        f"-DCMAKE_BUILD_RPATH={cuda}/lib",
    ]
    environment = build_environment(source)
    notify(
        "building",
        "Building the pinned GLM runtime for Ampere sm_86; no model is being loaded.",
    )
    run_owned(
        configure,
        root=root,
        directory=work / "configure",
        event=cancel_event,
        environment=environment,
    )
    run_owned(
        [cmake, "--build", build, "--target", "llama-server", "-j", "8"],
        root=root,
        directory=work / "compile",
        event=cancel_event,
        timeout=7200,
        environment=environment,
    )
    return _validate_and_publish_runtime(root, profile, work, report=notify, cancel_event=cancel_event)


def build_environment(source: Path) -> dict[str, str]:
    environment = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1",
                   "GIT_CEILING_DIRECTORIES": str(source.parent.resolve())}
    for name in ("LD_LIBRARY_PATH", "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR"):
        environment.pop(name, None)
    return environment


def _validate_and_publish_runtime(root: Path, profile: str, work: Path, *, report=None, cancel_event=None):
    notify = report or (lambda state, text: print(f"[{state}] {text}", flush=True))
    build = work / "build"
    environment = {**os.environ, "CUDA_VISIBLE_DEVICES": "-1"}
    environment.pop("LD_LIBRARY_PATH", None)
    notify(
        "validating", "Validating the native executable and its required runtime files."
    )
    version_exit = run_owned(
        [build / "bin/llama-server", "--version"],
        root=root,
        directory=work / "validate-version",
        event=cancel_event,
        environment=environment,
        timeout=60,
    )
    help_exit = run_owned(
        [build / "bin/llama-server", "--help"],
        root=root,
        directory=work / "validate-help",
        event=cancel_event,
        environment=environment,
        timeout=60,
        expected_exit_codes=(1,),
    )
    help_text = (work / "validate-help/stdout.log").read_text(encoding="utf-8")
    if (not help_text.startswith(f"usage: {build}/bin/llama-server [options]")
            or "--defer-experts" not in help_text
            or (work / "validate-help/stderr.log").read_text().strip()):
        raise RuntimeError("Pinned runtime help output failed its capability contract")
    fingerprints = {name: digest(build / name) for name in FILES}
    record = {
        "commit": COMMIT,
        "archive_sha256": ARCHIVE_SHA256,
        "loader_sha256": LOADER_AFTER,
        "profile": profile,
        "fingerprints": fingerprints,
        "validation_exit": version_exit,
        "help_exit": help_exit,
        "build_directory": str(build),
        "build_logs": str(work),
    }
    destination = runtime_destination(root, profile)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.rename(
            destination.with_name(destination.name + ".invalid-" + uuid4().hex)
        )
    # Keep build/RUNPATH locations intact; publish only its verified manifest.
    destination.mkdir()
    (destination / "installed.json").write_text(
        json.dumps(record, sort_keys=True) + "\n", encoding="utf-8"
    )
    notify("ready", "The compatible GLM runtime is ready; model validation is next.")
    return {"runtime_dir": work, "fingerprints": fingerprints, "built": True}
