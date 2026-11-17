"""Pinned official GLM runtime; independent of the retained ik runtime and patch."""
from __future__ import annotations

import json
import os
import shutil
import site
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from . import native_runtime as native

RELEASE = "v1.0.0"
COMMIT = "b1b770d90d94cad9e9ea5b993bdbc383a522fb28"
ARCHIVE_SHA256 = "ae8556670b17c25931cd39eb7c95617650eaf6091bbcff206750583ddde1acb4"
REQUIRED_FILES = {"bin/llama-server", "bin/libllama.so", "bin/libggml.so",
                  "bin/libggml-base.so", "bin/libggml-cpu.so", "bin/libggml-cuda.so", "bin/libmtmd.so"}
FLAGS = ("--fit", "--fit-target", "--fit-ctx", "--load-mode", "--device", "--split-mode",
         "--cache-type-k", "--cache-type-v", "--batch-size", "--ubatch-size",
         "--cache-ram", "--ctx-checkpoints")
_VERIFIED = {}


def model_ready(base_url: str) -> bool:
    """An HTTP listener can be available before its model is loaded."""
    import httpx

    try:
        response = httpx.get(base_url.rstrip("/") + "/health", timeout=2.0, trust_env=False)
    except httpx.TransportError:
        return False
    if response.status_code == 503:
        return False
    response.raise_for_status()
    if response.status_code != 200 or len(response.content) > 8192:
        return False
    try:
        value = response.json()
    except ValueError:
        return False
    return isinstance(value, dict) and value.get("status") == "ok"


def destination(root: Path, profile: str) -> Path:
    if profile not in native.TOOLCHAINS:
        raise ValueError("Unsupported official runtime CUDA profile")
    return root / ".cache/runtimes" / f"glm53-official-{COMMIT[:8]}-{profile}-sm86"


def artifacts(build: Path) -> dict[str, Path]:
    result = {"bin/llama-server": build / "bin/llama-server"}
    result.update({p.relative_to(build).as_posix(): p for p in (build / "bin").glob("*.so*")})
    if not REQUIRED_FILES.issubset(result):
        raise RuntimeError("Official runtime is missing required backend libraries")
    if any(not p.is_file() or not p.resolve().is_relative_to(build.resolve()) for p in result.values()):
        raise RuntimeError("Official runtime artifacts escape their build directory or are incomplete")
    return result


def installed(root: Path, profile: str) -> tuple[Path, dict] | None:
    receipt = destination(root, profile) / "installed.json"
    if not receipt.is_file() or receipt.is_symlink():
        return None
    try:
        record = json.loads(receipt.read_text())
        if (record["commit"] != COMMIT or record["archive_sha256"] != ARCHIVE_SHA256
                or record["profile"] != profile or record["validation_exit"] != 0
                or type(record["validation_exit"]) is not int):
            return None
        work = Path(record["work"])
        relative = work.resolve().relative_to((root / ".cache/official-runtime-builds").resolve())
        if len(relative.parts) != 1 or len(relative.parts[0]) != 32 or any(c not in "0123456789abcdef" for c in relative.parts[0]):
            return None
        paths = artifacts(work / "build")
        if set(paths) != set(record["fingerprints"]):
            return None
        identity = tuple((str(p), p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                         for p in [receipt, *paths.values()])
        if _VERIFIED.get(str(receipt)) != identity:
            if any(native.digest(p) != record["fingerprints"][name] for name, p in paths.items()):
                return None
            after = tuple((str(p), p.stat().st_ino, p.stat().st_size, p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                          for p in [receipt, *paths.values()])
            if after != identity:
                return None
            _VERIFIED[str(receipt)] = identity
        return work, record["fingerprints"]
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        return None


def archive(root: Path, event=None) -> Path:
    cache = root / ".cache/downloads/runtimes"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"llama-{COMMIT}.tar.gz"
    if target.exists():
        if native.digest(target) != ARCHIVE_SHA256:
            raise RuntimeError("Official source checksum mismatch; preserve the file for inspection")
        return target
    if native.offline():
        raise RuntimeError("Official runtime source is missing offline")
    partial = cache / f"{uuid4().hex}.partial"
    request = Request(f"https://codeload.github.com/ggml-org/llama.cpp/tar.gz/{COMMIT}",
                      headers={"User-Agent": "Qubitz-runtime-bootstrap"})
    with urlopen(request, timeout=30) as response, partial.open("xb") as output:
        parsed = urlparse(response.url)
        if parsed.scheme != "https" or parsed.hostname != "codeload.github.com":
            raise RuntimeError("Official source redirect left the pinned HTTPS provider")
        total = 0
        while block := response.read(1024**2):
            native.cancelled(event)
            total += len(block)
            if total > 256 * 1024**2:
                raise RuntimeError("Official source archive exceeds the download bound")
            output.write(block)
    if native.digest(partial) != ARCHIVE_SHA256:
        raise RuntimeError(f"Official source checksum mismatch; preserved: {partial}")
    partial.rename(target)
    return target


def ensure(root: Path, profile: str, *, report=None, cancel_event=None) -> dict:
    root = Path(root).resolve()
    found = installed(root, profile)
    if found:
        return {"runtime_dir": found[0], "fingerprints": found[1], "built": False}
    if sys.platform != "linux" or not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
        raise RuntimeError("Official GLM runtime preparation requires the project WSL environment")
    if native.offline():
        raise RuntimeError("Official GLM runtime is missing or unverified; preparation is disabled offline")
    notify = report or (lambda phase, text: print(f"[{phase}] {text}", flush=True))
    with native.acquisition_lock(root, cancel_event):
        found = installed(root, profile)
        if found:
            return {"runtime_dir": found[0], "fingerprints": found[1], "built": False}
        target = destination(root, profile)
        pending = target.with_suffix(".pending.json")
        if pending.exists() or target.exists():
            raise RuntimeError("Previous official preparation or invalid manifest requires reconciliation; do not retry")
        if shutil.disk_usage(root).free < 22 * 1024**3:
            raise RuntimeError("Official runtime preparation needs at least 22 GiB free")
        work = root / ".cache/official-runtime-builds" / uuid4().hex
        work.mkdir(parents=True)
        pending.parent.mkdir(parents=True, exist_ok=True)
        with pending.open("x") as stream:
            json.dump({"work": str(work), "profile": profile, "commit": COMMIT}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        notify("downloading", f"Preparing official llama.cpp {RELEASE} at {COMMIT[:8]}.")
        source_archive = archive(root, cancel_event)
        stage = work / "source"
        stage.mkdir()
        with tarfile.open(source_archive) as tar:
            members = tar.getmembers()
            if len(members) > 30000 or sum(item.size for item in members) > 2 * 1024**3:
                raise RuntimeError("Official source extraction exceeds bounds")
            tar.extractall(stage, filter="data")
        source = stage / f"llama.cpp-{COMMIT}"
        if not (source / "CMakeLists.txt").is_file() or len(list(stage.iterdir())) != 1:
            raise RuntimeError("Unexpected official source archive layout")
        toolkit = native.prepare_toolchain(root, profile, work, cancel_event)
        cuda = next((Path(p) / "nvidia/cu13" for p in site.getsitepackages()
                     if (Path(p) / "nvidia/cu13/lib/libcublas.so.13").is_file()), None)
        if cuda is None:
            raise RuntimeError("The active application CUDA/cuBLAS stack is incomplete")
        tools = {name: str(Path(sys.executable).parent / name) if (Path(sys.executable).parent / name).is_file()
                 else shutil.which(name) for name in ("cmake", "ninja", "g++-12")}
        if not all(tools.values()):
            raise RuntimeError("Official native build requires existing cmake, ninja and g++-12")
        build = work / "build"
        environment = native.build_environment(source)
        configure = [tools["cmake"], "-S", source, "-B", build, "-G", "Ninja",
            f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}", "-DCMAKE_BUILD_TYPE=Release", "-DGGML_CUDA=ON",
            "-DCMAKE_CUDA_ARCHITECTURES=86", "-DLLAMA_CURL=OFF", "-DLLAMA_BUILD_TESTS=OFF",
            "-DBUILD_SHARED_LIBS=ON", "-DGGML_CCACHE=OFF", f"-DCMAKE_CUDA_COMPILER={toolkit}/bin/nvcc",
            f"-DCUDAToolkit_ROOT={toolkit}", f"-DCMAKE_CUDA_HOST_COMPILER={tools['g++-12']}",
            f"-DCMAKE_CXX_COMPILER={tools['g++-12']}", f"-DCMAKE_CUDA_FLAGS=--threads=2 -I{cuda}/include",
            f"-DCUDA_CUDART={cuda}/lib/libcudart.so.13", f"-DCUDA_cudart_LIBRARY={cuda}/lib/libcudart.so.13",
            f"-DCUDA_cublas_LIBRARY={cuda}/lib/libcublas.so.13", f"-DCUDA_cublasLt_LIBRARY={cuda}/lib/libcublasLt.so.13",
            f"-DCMAKE_BUILD_RPATH={cuda}/lib"]
        native.run_owned(configure, root=root, directory=work / "configure", event=cancel_event, environment=environment)
        notify("building", "Building official llama.cpp for sm_86 without the ik loader patch.")
        native.run_owned([tools["cmake"], "--build", build, "--target", "llama-server", "-j", "8"],
                         root=root, directory=work / "compile", event=cancel_event, timeout=7200, environment=environment)
        for name in ("version", "help"):
            native.run_owned([build / "bin/llama-server", f"--{name}"], root=root,
                directory=work / f"validate-{name}", event=cancel_event, timeout=60, environment=environment)
        help_text = (work / "validate-help/stdout.log").read_text()
        if any(flag not in help_text for flag in FLAGS):
            raise RuntimeError("Official runtime failed the launch capability contract")
        fingerprints = {name: native.digest(path) for name, path in artifacts(build).items()}
        record = {"release": RELEASE, "commit": COMMIT, "archive_sha256": ARCHIVE_SHA256, "profile": profile,
                  "work": str(work), "fingerprints": fingerprints, "validation_exit": 0,
                  "loader_patch": None, "cuda_architectures": [86]}
        target.mkdir()
        with (target / "installed.json").open("x") as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        pending.rename(work / "completed-preparation.json")
        notify("ready", "Official GLM runtime prepared; live model qualification remains separate.")
        return {"runtime_dir": work, "fingerprints": fingerprints, "built": True}


def install_runtime(app, profile_selector):
    base = app.base

    def profile():
        return "cu130" if "cu130" in profile_selector()[0] else "cu134"

    def runtime_dir(root):
        found = installed(root, profile())
        return found[0] if found else destination(root, profile())

    def executable(root):
        return runtime_dir(root) / "build/bin/llama-server"

    def resolve(root, configured_path=None):
        if not base.in_wsl():
            raise RuntimeError("Official GLM runtime requires WSL")
        configured = configured_path or os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME, "")
        if configured and (Path(configured).expanduser() if Path(configured).is_absolute() else root / configured).resolve() != executable(root).resolve():
            raise RuntimeError("GLM official backend must use its verified isolated runtime")
        if not installed(root, profile()):
            raise RuntimeError("Official GLM runtime is missing or has a fingerprint mismatch")
        return str(executable(root).resolve())

    def prepare(root, *, report=None, cancel_event=None):
        configured = os.environ.get(base.LLAMACPP_SERVER_PATH_ENV_NAME, "")
        if configured:
            resolve(root, configured)
        result = ensure(root, profile(), report=report, cancel_event=cancel_event)
        return {"executable": Path(resolve(root)), "runtime_dir": result["runtime_dir"],
                "build_log": result["runtime_dir"] / "compile/stdout.log", "missing_before": [],
                "release_tag": RELEASE, "downloaded": result["built"], "built": result["built"]}

    base.llamacpp_runtime_dir = base.llamacpp_native_runtime_dir = runtime_dir
    base.llamacpp_native_server_executable = executable
    base.llamacpp_native_build_log_path = lambda root: runtime_dir(root) / "compile/stdout.log"
    base.llamacpp_native_server_log_path = lambda root: runtime_dir(root) / "llama-server.log"
    base.llamacpp_runtime_missing_files = lambda root: [] if installed(root, profile()) else [str(executable(root))]
    base.resolve_llama_server_executable = resolve
    base.ensure_project_local_llamacpp_runtime = base.ensure_project_local_native_llamacpp_runtime = prepare
    base._qubitz_ensure_selected_runtime = prepare
    base.QUBITZ_LLAMACPP_RUNTIME_CHANNEL = "glm53-official-isolated"
    base.QUBITZ_LLAMACPP_RUNTIME_RELEASE = base.QUBITZ_LLAMACPP_RUNTIME_TAG = RELEASE
    base.DEFAULT_LLAMACPP_RELEASE_API_URL = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"


def install_launch_fit(app, inventory, split_plan, *, margin, cache_type, cache_mib, checkpoints):
    from .gpu_devices import device_names

    cls = app.base.LlamaCppServerProcess
    original = cls._launch_command

    def launch(self):
        command = list(original(self))
        help_text = getattr(cls, "_qubitz_server_help_cache", {}).get(command[0], "")
        missing = [flag for flag in FLAGS if flag not in help_text]
        if missing:
            raise RuntimeError("Official GLM backend lacks required launch flags: " + ", ".join(missing))
        incompatible = {"--defer-experts", "--fit-margin", "--mlock", "--no-mmap"}.intersection(command)
        if incompatible:
            raise RuntimeError("Official GLM launch has incompatible flags: " + ", ".join(sorted(incompatible)))
        context = int(self.config.num_ctx)
        if not 1 <= context <= 1048576:
            raise ValueError("GLM num_ctx must remain within the native context limit")
        plan = split_plan(inventory(command[0]))
        self._qubitz_child_environment = (
            {"GGML_CUDA_NO_PINNED": "1"} if plan.get("minimum_cpu_resident_mib", 0) > 0 else {}
        )
        replaced = {*FLAGS, "--ctx-size"}
        cleaned = []
        index = 0
        while index < len(command):
            value = str(command[index])
            option = value.partition("=")[0]
            if option in replaced:
                if "=" in value:
                    index += 1
                elif index + 1 < len(command) and not str(command[index + 1]).startswith("--"):
                    index += 2
                elif option == "--fit":
                    index += 1
                else:
                    raise RuntimeError("Incomplete inherited official launch option: " + option)
            else:
                cleaned.append(command[index])
                index += 1
        command = cleaned
        # Non-mmap uploads avoid prefaulting all model shards before GPU offload.
        command.extend(["--ctx-size", str(context), "--load-mode", "none",
            "--device", device_names(plan["devices"]), "--split-mode", "layer",
            "--fit", "on", "--fit-target", ",".join(str(margin) for _ in plan["devices"]),
            "--fit-ctx", str(context), "--cache-type-k", cache_type, "--cache-type-v", cache_type,
            "--batch-size", "512", "--ubatch-size", "128", "--cache-ram", str(cache_mib),
            "--ctx-checkpoints", str(checkpoints)])
        self._qubitz_glm53_flash_multi_gpu_plan = plan
        return command

    cls._launch_command = launch
