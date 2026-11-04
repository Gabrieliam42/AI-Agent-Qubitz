"""Model-free native policy-loader contracts; real bundles are tested explicitly.

Until the matching binaries are ready, run with ``-k 'not real_bundle'``.
The real-bundle test is deliberately unskipped and never reads a key or plaintext
harness file.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from runtime import policy_loader

ROOT = Path(__file__).resolve().parents[1]
FILENAMES = {
    "linux-x86_64-cp312": "_policy_runtime.cpython-312-x86_64-linux-gnu.so",
    "windows-amd64-cp312": "_policy_runtime.cp312-win_amd64.pyd",
}


@pytest.fixture(autouse=True)
def clear_native_cache():
    policy_loader._load_native.cache_clear()
    yield
    policy_loader._load_native.cache_clear()


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    directory = tmp_path / "runtime"
    directory.mkdir()
    artifact_bytes = b"Fixture native artifact, imported only through injection."
    digest = hashlib.sha256(artifact_bytes).hexdigest()
    manifest = {
        "schema_version": 1,
        "bundle_id": "fixture-bundle-v1",
        "algorithm": "fernet",
        "artifacts": {
            tag: {"filename": filename, "sha256": digest}
            for tag, filename in FILENAMES.items()
        },
    }
    for filename in FILENAMES.values():
        (directory / filename).write_bytes(artifact_bytes)
    manifest_path = directory / "policy_bundle.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    harness = tmp_path / "HARNESS.enc"
    harness.write_bytes(b"fixture encrypted token")
    binary = directory / FILENAMES["linux-x86_64-cp312"]
    native = SimpleNamespace(
        __file__=str(binary), bundle_id=lambda: "fixture-bundle-v1",
        decrypt=lambda _ciphertext: b"fixture policy\n",
    )
    imports = []

    def import_native(path):
        imports.append(path)
        return native

    monkeypatch.setattr(policy_loader, "_BUNDLE_DIRECTORY", directory)
    monkeypatch.setattr(policy_loader, "_platform_artifact", lambda: (
        "linux-x86_64-cp312", FILENAMES["linux-x86_64-cp312"],
    ))
    monkeypatch.setattr(policy_loader, "_import_native", import_native)
    return SimpleNamespace(
        directory=directory, manifest=manifest, manifest_path=manifest_path,
        binary=binary, harness=harness, native=native, imports=imports,
    )


def write_manifest(bundle):
    bundle.manifest_path.write_text(json.dumps(bundle.manifest), encoding="utf-8")


def test_decrypts_exact_ciphertext_and_decodes_utf8(bundle):
    received = []

    def decrypt(ciphertext):
        received.append(ciphertext)
        return "fixture \u03bb \U0001f642\n".encode("utf-8")

    bundle.native.decrypt = decrypt
    assert policy_loader.load_harness(bundle.harness) == "fixture \u03bb \U0001f642\n"
    assert received == [b"fixture encrypted token"]
    assert bundle.imports == [bundle.binary]


@pytest.mark.parametrize("tag", FILENAMES)
def test_selects_only_exact_sibling_for_platform(bundle, monkeypatch, tag):
    binary = bundle.directory / FILENAMES[tag]
    monkeypatch.setattr(policy_loader, "_platform_artifact", lambda: (tag, FILENAMES[tag]))
    bundle.native.__file__ = str(binary)
    assert policy_loader.load_harness(bundle.harness) == "fixture policy\n"
    assert bundle.imports == [binary]


def test_missing_manifest_fails_before_import(bundle):
    bundle.manifest_path.unlink()
    with pytest.raises(RuntimeError, match="manifest is missing"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("raw", [b"{", b"\xff", b"[]", b"null", b'{"schema_version":1,"schema_version":1}'])
def test_malformed_manifest_fails_before_import(bundle, raw):
    bundle.manifest_path.write_bytes(raw)
    with pytest.raises(RuntimeError, match="manifest"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", 2), ("schema_version", "1"),
    ("bundle_id", ""), ("bundle_id", "  "), ("bundle_id", None),
    ("algorithm", "plaintext"), ("algorithm", "Fernet"),
    ("artifacts", []), ("artifacts", {}), ("unexpected", "field"),
])
def test_unsupported_metadata_fails_before_import(bundle, field, value):
    bundle.manifest[field] = value
    write_manifest(bundle)
    with pytest.raises(RuntimeError, match="manifest"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("tag", FILENAMES)
@pytest.mark.parametrize("filename", [
    "../_policy_runtime.so", "..\\_policy_runtime.pyd", "/tmp/_policy_runtime.so",
    "C:\\fixture\\_policy_runtime.pyd", "\\\\fixture\\share\\_policy_runtime.pyd",
    "subdir/_policy_runtime.so", "_policy_runtime.py", "_policy_runtime.abi3.so", None,
])
def test_rejects_artifact_path_traversal_and_nonexact_names(bundle, tag, filename):
    bundle.manifest["artifacts"][tag]["filename"] = filename
    write_manifest(bundle)
    with pytest.raises(RuntimeError, match="artifact metadata"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("artifact", [None, {}, {"filename": "_policy_runtime.so"}, {
    "filename": FILENAMES["linux-x86_64-cp312"], "sha256": "0" * 64, "extra": True,
}])
def test_rejects_malformed_artifact_objects(bundle, artifact):
    bundle.manifest["artifacts"]["linux-x86_64-cp312"] = artifact
    write_manifest(bundle)
    with pytest.raises(RuntimeError, match="artifact metadata"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("digest", [None, 12, "", "f" * 63, "g" * 64, "f" * 65, "f" * 64 + "\n"])
def test_rejects_invalid_sha256_metadata(bundle, digest):
    bundle.manifest["artifacts"]["linux-x86_64-cp312"]["sha256"] = digest
    write_manifest(bundle)
    with pytest.raises(RuntimeError, match="artifact metadata"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


def test_accepts_uppercase_hex_digest(bundle):
    artifact = bundle.manifest["artifacts"]["linux-x86_64-cp312"]
    artifact["sha256"] = artifact["sha256"].upper()
    write_manifest(bundle)
    assert policy_loader.load_harness(bundle.harness) == "fixture policy\n"


def test_missing_binary_fails_before_import(bundle):
    bundle.binary.unlink()
    with pytest.raises(RuntimeError, match="binary is missing"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


def test_corrupted_binary_is_never_imported(bundle):
    bundle.binary.write_bytes(b"corrupt fixture artifact")
    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("target", ["binary", "manifest_path"])
def test_resolved_artifact_escape_fails_before_import(bundle, monkeypatch, target):
    original = Path.resolve
    selected = getattr(bundle, target)

    def resolve(path, *args, **kwargs):
        if path == selected:
            return bundle.directory.parent / path.name
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(RuntimeError, match="sibling regular file"):
        policy_loader.load_harness(bundle.harness)
    assert not bundle.imports


@pytest.mark.parametrize("origin", [None, 123, "_policy_runtime.so", "other"])
def test_rejects_wrong_import_origin(bundle, origin):
    bundle.native.__file__ = str(bundle.harness) if origin == "other" else origin
    with pytest.raises(RuntimeError, match="origin"):
        policy_loader.load_harness(bundle.harness)
    assert policy_loader._load_native.cache_info().currsize == 0


@pytest.mark.parametrize("identifier", [None, b"fixture-bundle-v1", "other-bundle"])
def test_rejects_wrong_bundle_id(bundle, identifier):
    bundle.native.bundle_id = lambda: identifier
    with pytest.raises(RuntimeError, match="bundle ID"):
        policy_loader.load_harness(bundle.harness)
    assert policy_loader._load_native.cache_info().currsize == 0


@pytest.mark.parametrize("attribute", ["bundle_id", "decrypt"])
def test_rejects_missing_native_interface(bundle, attribute):
    setattr(bundle.native, attribute, None)
    with pytest.raises(RuntimeError, match="callable bundle_id and decrypt"):
        policy_loader.load_harness(bundle.harness)


def test_import_failure_is_sanitized_without_fallback(bundle, monkeypatch):
    def fail(_path):
        raise ImportError("fixture sensitive detail")

    monkeypatch.setattr(policy_loader, "_import_native", fail)
    with pytest.raises(RuntimeError, match="could not be imported") as caught:
        policy_loader.load_harness(bundle.harness)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_identification_failure_is_sanitized(bundle):
    def fail():
        raise ValueError("fixture sensitive detail")

    bundle.native.bundle_id = fail
    with pytest.raises(RuntimeError, match="identification failed") as caught:
        policy_loader.load_harness(bundle.harness)
    assert "sensitive" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_invalid_token_does_not_fall_back_to_env_file_key_or_plaintext(bundle, monkeypatch, capsys, caplog):
    # Replace the mapping, without looking up any actual developer key value.
    monkeypatch.setattr(os, "environ", {"QUBITZ_HARNESS_KEY": "fixture-only-not-a-real-key"})
    (bundle.harness.parent / "QUBITZ_HARNESS_KEY.local.txt").write_text("fixture-only", encoding="utf-8")
    (bundle.harness.parent / "HARNESS.txt").write_text("fixture plaintext fallback", encoding="utf-8")
    original_bytes = Path.read_bytes
    original_text = Path.read_text

    def guard_bytes(path, *args, **kwargs):
        if path.suffix == ".txt":
            pytest.fail("Loader attempted a key or plaintext fallback")
        return original_bytes(path, *args, **kwargs)

    def guard_text(path, *args, **kwargs):
        if path.suffix == ".txt":
            pytest.fail("Loader attempted a key or plaintext fallback")
        return original_text(path, *args, **kwargs)

    def invalid_token(_ciphertext):
        raise ValueError("fixture decrypted secret must not be exposed")

    monkeypatch.setattr(Path, "read_bytes", guard_bytes)
    monkeypatch.setattr(Path, "read_text", guard_text)
    bundle.native.decrypt = invalid_token
    with pytest.raises(RuntimeError, match="invalid token") as caught:
        policy_loader.load_harness(bundle.harness)
    assert "fixture decrypted secret" not in str(caught.value)
    assert caught.value.__suppress_context__
    assert capsys.readouterr() == ("", "")
    assert not caplog.records


def test_missing_bundle_does_not_fall_back_to_plaintext(bundle, monkeypatch):
    monkeypatch.setattr(os, "environ", {"QUBITZ_HARNESS_KEY": "fixture-only-not-a-real-key"})
    (bundle.harness.parent / "HARNESS.txt").write_text("fixture plaintext", encoding="utf-8")
    bundle.binary.unlink()
    with pytest.raises(RuntimeError, match="binary is missing"):
        policy_loader.load_harness(bundle.harness)


@pytest.mark.parametrize("value", ["fixture policy", bytearray(b"fixture policy"), None])
def test_native_decrypt_must_return_bytes(bundle, value):
    bundle.native.decrypt = lambda _ciphertext: value
    with pytest.raises(RuntimeError, match="must return bytes"):
        policy_loader.load_harness(bundle.harness)


def test_rejects_invalid_utf8_without_exposing_plaintext(bundle):
    bundle.native.decrypt = lambda _ciphertext: b"fixture private policy\xff"
    with pytest.raises(RuntimeError, match="not valid UTF-8") as caught:
        policy_loader.load_harness(bundle.harness)
    assert "private policy" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_missing_encrypted_harness_is_a_clear_error(bundle):
    with pytest.raises(RuntimeError, match="Encrypted harness cannot be read"):
        policy_loader.load_harness(bundle.harness.parent / "missing.enc")


def test_caches_verified_callable_without_rehashing_or_caching_plaintext(bundle, monkeypatch):
    digests = []
    original_digest = hashlib.file_digest

    def digest(stream, algorithm):
        digests.append(algorithm)
        return original_digest(stream, algorithm)

    monkeypatch.setattr(hashlib, "file_digest", digest)
    bundle.native.decrypt = lambda ciphertext: b"decoded " + ciphertext
    assert policy_loader.load_harness(bundle.harness) == "decoded fixture encrypted token"
    second = bundle.harness.parent / "second.enc"
    second.write_bytes(b"second fixture token")
    bundle.native.decrypt = lambda _ciphertext: pytest.fail("Cached callable was replaced")
    assert policy_loader.load_harness(second) == "decoded second fixture token"
    assert digests == ["sha256"]
    assert bundle.imports == [bundle.binary]
    assert policy_loader._load_native.cache_info().hits == 1


def test_concurrent_first_load_imports_only_once(bundle):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(policy_loader.load_harness, [bundle.harness] * 12))
    assert results == ["fixture policy\n"] * 12
    assert bundle.imports == [bundle.binary]


@pytest.mark.parametrize(("overrides", "message"), [
    ({"version": (3, 11)}, "requires CPython 3.12"),
    ({"version": (3, 13)}, "requires CPython 3.12"),
    ({"implementation": "PyPy"}, "requires CPython 3.12"),
    ({"machine": "aarch64"}, "requires CPython 3.12"),
    ({"system": "darwin"}, "requires CPython 3.12"),
    ({"maxsize": 2**31 - 1}, "requires CPython 3.12"),
    ({"suffix": ".abi3.so"}, "extension ABI"),
    ({"system": "win32"}, "extension ABI"),
])
def test_unsupported_platform_fails_before_bundle_access(tmp_path, monkeypatch, overrides, message):
    settings = {
        "version": (3, 12), "implementation": "CPython", "machine": "x86_64",
        "system": "linux", "maxsize": 2**63 - 1, "suffix": ".cpython-312-x86_64-linux-gnu.so",
    }
    settings.update(overrides)
    monkeypatch.setattr(policy_loader, "sys", SimpleNamespace(
        version_info=settings["version"], platform=settings["system"], maxsize=settings["maxsize"],
    ))
    monkeypatch.setattr(policy_loader, "platform", SimpleNamespace(
        python_implementation=lambda: settings["implementation"], machine=lambda: settings["machine"],
    ))
    monkeypatch.setattr(policy_loader, "sysconfig", SimpleNamespace(get_config_var=lambda _name: settings["suffix"]))
    monkeypatch.setattr(policy_loader, "_BUNDLE_DIRECTORY", tmp_path)
    with pytest.raises(RuntimeError, match=message):
        policy_loader.load_harness(tmp_path / "HARNESS.enc")


@pytest.mark.parametrize(("system", "machine", "tag", "suffix"), [
    ("linux", "x86_64", "linux-x86_64-cp312", ".cpython-312-x86_64-linux-gnu.so"),
    ("win32", "AMD64", "windows-amd64-cp312", ".cp312-win_amd64.pyd"),
])
def test_supported_platform_uses_exact_cpython_filename(monkeypatch, system, machine, tag, suffix):
    monkeypatch.setattr(policy_loader, "sys", SimpleNamespace(
        version_info=(3, 12), platform=system, maxsize=2**63 - 1,
    ))
    monkeypatch.setattr(policy_loader, "platform", SimpleNamespace(
        python_implementation=lambda: "CPython", machine=lambda: machine,
    ))
    monkeypatch.setattr(policy_loader, "sysconfig", SimpleNamespace(get_config_var=lambda _name: suffix))
    assert policy_loader._platform_artifact() == (tag, FILENAMES[tag])


def test_native_import_uses_explicit_extension_spec(bundle, monkeypatch):
    calls = []
    executed = []
    real_import = _REAL_IMPORT_NATIVE

    def make_spec(name, path, *, loader):
        calls.append((name, path, loader))
        return SimpleNamespace(loader=SimpleNamespace(exec_module=executed.append))

    monkeypatch.delitem(sys.modules, policy_loader._NATIVE_MODULE, raising=False)
    monkeypatch.setattr(policy_loader.importlib.util, "spec_from_file_location", make_spec)
    monkeypatch.setattr(policy_loader.importlib.util, "module_from_spec", lambda _spec: bundle.native)
    monkeypatch.setitem(sys.modules, policy_loader._NATIVE_MODULE, bundle.native)
    monkeypatch.delitem(sys.modules, policy_loader._NATIVE_MODULE)
    assert real_import(bundle.binary) is bundle.native
    assert executed == [bundle.native]
    name, path, loader = calls[0]
    assert name == "runtime._policy_runtime" and path == bundle.binary
    assert isinstance(loader, policy_loader.importlib.machinery.ExtensionFileLoader)
    assert loader.path == str(bundle.binary)


def test_native_import_refuses_an_independently_preloaded_module(bundle, monkeypatch):
    foreign = SimpleNamespace(__file__=str(bundle.harness))
    monkeypatch.setitem(sys.modules, policy_loader._NATIVE_MODULE, foreign)
    with pytest.raises(ImportError, match="outside the verified loader"):
        _REAL_IMPORT_NATIVE(bundle.binary)
    assert sys.modules[policy_loader._NATIVE_MODULE] is foreign


def test_native_import_reuses_only_the_exact_extension_origin(bundle, monkeypatch):
    native = ModuleType(policy_loader._NATIVE_MODULE)
    native.__file__ = str(bundle.binary)
    native.__loader__ = policy_loader.importlib.machinery.ExtensionFileLoader(
        policy_loader._NATIVE_MODULE, str(bundle.binary),
    )
    monkeypatch.setitem(sys.modules, policy_loader._NATIVE_MODULE, native)
    assert _REAL_IMPORT_NATIVE(bundle.binary) is native


def test_native_exec_failure_removes_only_its_own_module(bundle, monkeypatch):
    def fail(_module):
        raise ImportError("fixture native initialization failure")

    monkeypatch.delitem(sys.modules, policy_loader._NATIVE_MODULE, raising=False)
    monkeypatch.setattr(policy_loader.importlib.util, "spec_from_file_location", lambda *_args, **_kwargs: (
        SimpleNamespace(loader=SimpleNamespace(exec_module=fail))
    ))
    monkeypatch.setattr(policy_loader.importlib.util, "module_from_spec", lambda _spec: bundle.native)
    with pytest.raises(ImportError, match="fixture native initialization"):
        _REAL_IMPORT_NATIVE(bundle.binary)
    assert policy_loader._NATIVE_MODULE not in sys.modules


def test_validation_failure_removes_only_its_own_module(bundle, monkeypatch):
    def import_native(_path):
        sys.modules[policy_loader._NATIVE_MODULE] = bundle.native
        return bundle.native

    monkeypatch.delitem(sys.modules, policy_loader._NATIVE_MODULE, raising=False)
    monkeypatch.setattr(policy_loader, "_import_native", import_native)
    bundle.native.bundle_id = lambda: "wrong-bundle"
    with pytest.raises(RuntimeError, match="bundle ID"):
        policy_loader.load_harness(bundle.harness)
    assert policy_loader._NATIVE_MODULE not in sys.modules


_REAL_IMPORT_NATIVE = policy_loader._import_native


def test_real_bundle_decrypts_project_encrypted_harness():
    """Run only after main confirms the real platform bundle is ready; no skip."""
    if not policy_loader.load_harness(ROOT / "HARNESS.enc").strip():
        pytest.fail("Real native bundle returned an empty policy.", pytrace=False)
    policy_loader._load_native.cache_clear()
    assert policy_loader.load_harness(ROOT / "HARNESS.enc").strip()


def test_dependency_fingerprints_include_both_native_binaries_and_manifest():
    from runtime.source import runtime_dependency_paths

    names = {path.relative_to(ROOT).as_posix() for path in runtime_dependency_paths(ROOT)}
    assert {"runtime/policy_loader.py", "runtime/policy_bundle.json",
            *("runtime/" + name for name in FILENAMES.values())} <= names
    assert not any(name.endswith((".pyx", ".c", ".pdb")) for name in names)


def test_assembled_export_copies_policy_runtime_but_not_private_key(tmp_path, monkeypatch):
    from runtime import development

    root = tmp_path / "project"
    root.mkdir()
    package = root / "runtime"
    package.mkdir()
    for name in ("policy_loader.py", "policy_bundle.json", *FILENAMES.values()):
        (package / name).write_bytes(b"fixture artifact")
    (package / "_policy_runtime.pyx").write_text("private build source", encoding="utf-8")
    (root / "HARNESS.enc").write_bytes(b"fixture encrypted harness")
    (root / "QUBITZ_HARNESS_KEY.local.txt").write_text("private fixture key", encoding="utf-8")
    variant = root / "AI_Agent_Qubitz_fixture.py"
    variant.write_text("fixture = True\n", encoding="utf-8")
    output = root / "export"
    monkeypatch.setattr(development, "ROOT", root)
    monkeypatch.setattr(development, "RUNTIME_DIRECTORY", package)
    monkeypatch.setattr(development, "variant_paths", lambda _root: [variant])
    monkeypatch.setattr(development, "read_variant_source", lambda path: path.read_text())
    monkeypatch.setattr(sys, "argv", ["export", "--export", str(output)])
    assert development.main() == 0
    assert (output / "HARNESS.enc").read_bytes() == b"fixture encrypted harness"
    assert all((output / "runtime" / name).is_file() for name in FILENAMES.values())
    assert (output / "runtime/policy_bundle.json").is_file()
    assert not (output / "QUBITZ_HARNESS_KEY.local.txt").exists()
    assert not (output / "runtime/_policy_runtime.pyx").exists()
