"""Load an immutable native policy bundle without key or plaintext fallbacks.

The manifest hash detects artifact mismatches, not malicious bundle replacement.
Restart the process after replacing a bundle; only the native callable is cached,
never the decrypted policy.
"""
from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import platform
import re
import sys
import sysconfig
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import RLock
from types import ModuleType

__all__ = ["load_harness"]

_BUNDLE_DIRECTORY = Path(__file__).resolve().parent
_NATIVE_MODULE = "runtime._policy_runtime"
_ARTIFACT_FILENAMES = {
    "linux-x86_64-cp312": "_policy_runtime.cpython-312-x86_64-linux-gnu.so",
    "windows-amd64-cp312": "_policy_runtime.cp312-win_amd64.pyd",
}
_NATIVE_LOCK = RLock()


@dataclass(frozen=True)
class _NativeLoader:
    bundle_id: str
    decrypt: Callable[[bytes], bytes]


def _platform_artifact() -> tuple[str, str]:
    if (sys.version_info[:2] != (3, 12)
            or platform.python_implementation() != "CPython"
            or platform.machine().lower() not in {"amd64", "x86_64"}
            or sys.maxsize != 2**63 - 1
            or sys.platform not in {"linux", "win32"}):
        raise RuntimeError(
            "Policy loader requires CPython 3.12 on 64-bit x86_64 Linux or Windows."
        )
    tag = "linux-x86_64-cp312" if sys.platform == "linux" else "windows-amd64-cp312"
    filename = _ARTIFACT_FILENAMES[tag]
    if filename != "_policy_runtime" + str(sysconfig.get_config_var("EXT_SUFFIX")):
        raise RuntimeError("Unsupported CPython extension ABI for the policy loader.")
    return tag, filename


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate manifest field")
        result[key] = value
    return result


def _read_manifest(directory: Path) -> dict:
    path = directory / "policy_bundle.json"
    try:
        if path.resolve(strict=True) != path:
            raise RuntimeError("Policy bundle manifest must be a sibling regular file.")
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except FileNotFoundError:
        raise RuntimeError("Policy bundle manifest is missing; install the matching native bundle.") from None
    except (OSError, ValueError):
        raise RuntimeError("Policy bundle manifest is unreadable or malformed.") from None
    if (not isinstance(document, dict)
            or set(document) != {"schema_version", "bundle_id", "algorithm", "artifacts"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or not isinstance(document["bundle_id"], str)
            or not document["bundle_id"].strip()
            or document["algorithm"] != "fernet"
            or not isinstance(document["artifacts"], dict)
            or set(document["artifacts"]) != set(_ARTIFACT_FILENAMES)):
        raise RuntimeError("Policy bundle manifest has an unsupported schema or metadata.")
    for tag, filename in _ARTIFACT_FILENAMES.items():
        artifact = document["artifacts"][tag]
        if (not isinstance(artifact, dict)
                or set(artifact) != {"filename", "sha256"}
                or artifact["filename"] != filename
                or not isinstance(artifact["sha256"], str)
                or re.fullmatch(r"[0-9a-fA-F]{64}", artifact["sha256"]) is None):
            raise RuntimeError("Policy bundle artifact metadata must name the exact platform extension and SHA-256.")
    return document


def _import_native(path: Path) -> ModuleType:
    # Never allow sys.path search or a foreign preloaded module to select the
    # native binary. Cache resets may reuse only the exact verified extension.
    if _NATIVE_MODULE in sys.modules:
        existing = sys.modules[_NATIVE_MODULE]
        if (isinstance(existing, ModuleType) and getattr(existing, "__file__", None) == str(path)
                and isinstance(getattr(existing, "__loader__", None), importlib.machinery.ExtensionFileLoader)):
            return existing
        raise ImportError("Native policy module was already loaded outside the verified loader")
    loader = importlib.machinery.ExtensionFileLoader(_NATIVE_MODULE, str(path))
    spec = importlib.util.spec_from_file_location(_NATIVE_MODULE, path, loader=loader)
    if spec is None or spec.loader is None:
        raise ImportError("Cannot construct the native policy module specification")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_NATIVE_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if sys.modules.get(_NATIVE_MODULE) is module:
            del sys.modules[_NATIVE_MODULE]
        raise
    return module


def _validate_native(module: ModuleType, path: Path, bundle_id: str) -> _NativeLoader:
    origin = getattr(module, "__file__", None)
    try:
        correct_origin = (isinstance(origin, str) and Path(origin) == path
                          and Path(origin).resolve(strict=True) == path)
    except (OSError, ValueError, RuntimeError):
        correct_origin = False
    if not correct_origin:
        raise RuntimeError("Native policy module origin does not match the verified sibling binary.")
    identify = getattr(module, "bundle_id", None)
    decrypt = getattr(module, "decrypt", None)
    if not callable(identify) or not callable(decrypt):
        # Bundle ABI failures share the public RuntimeError contract.
        raise RuntimeError("Native policy module must provide callable bundle_id and decrypt functions.")  # noqa: TRY004
    try:
        actual_id = identify()
    except Exception:  # noqa: BLE001 - suppress potentially sensitive native exception details
        raise RuntimeError("Native policy bundle identification failed.") from None
    if not isinstance(actual_id, str) or actual_id != bundle_id:
        raise RuntimeError("Native policy bundle ID does not match policy_bundle.json.")
    return _NativeLoader(bundle_id, decrypt)


@lru_cache(maxsize=1)
def _load_native() -> _NativeLoader:
    tag, filename = _platform_artifact()
    directory = _BUNDLE_DIRECTORY.resolve()
    manifest = _read_manifest(directory)
    path = directory / filename
    try:
        if path.resolve(strict=True) != path or not path.is_file():
            raise RuntimeError("Native policy binary must be an exact sibling regular file.")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
    except FileNotFoundError:
        raise RuntimeError("Native policy binary is missing; install the matching policy bundle.") from None
    except OSError:
        raise RuntimeError("Native policy binary cannot be read.") from None
    if digest != manifest["artifacts"][tag]["sha256"].lower():
        raise RuntimeError("Native policy binary SHA-256 mismatch; reinstall the matching policy bundle.")
    try:
        module = _import_native(path)
    except Exception:  # noqa: BLE001 - native initialization errors must not expose bundle internals
        raise RuntimeError("Native policy binary could not be imported; verify its CPython 3.12 compatibility.") from None
    try:
        return _validate_native(module, path, manifest["bundle_id"])
    except BaseException:
        if sys.modules.get(_NATIVE_MODULE) is module:
            del sys.modules[_NATIVE_MODULE]
        raise


def load_harness(path: Path) -> str:
    """Decrypt an encrypted harness with the verified sibling native bundle.

    Raises RuntimeError for unsupported hosts, invalid bundles, unreadable tokens,
    decryption failures, or non-UTF-8 output. No key lookup or fallback is used.
    """
    # lru_cache alone permits concurrent first misses; serialize native loading.
    with _NATIVE_LOCK:
        native = _load_native()
    try:
        ciphertext = path.read_bytes()
    except OSError:
        raise RuntimeError("Encrypted harness cannot be read.") from None
    try:
        plaintext = native.decrypt(ciphertext)
    except Exception:  # noqa: BLE001 - native decrypt errors must not expose key or policy details
        raise RuntimeError("Harness decryption failed; check for an invalid token or mismatched policy bundle.") from None
    if not isinstance(plaintext, bytes):
        raise RuntimeError("Native policy decrypt must return bytes.")  # noqa: TRY004 - public bundle ABI contract
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("Decrypted harness is not valid UTF-8.") from None
