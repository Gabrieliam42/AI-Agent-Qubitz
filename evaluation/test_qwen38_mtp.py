"""Optional Qwen3.8 MTP admission; no models, downloads, CUDA or live server."""
from __future__ import annotations

import ast
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from runtime.paths import variant_path
from runtime.source import launcher_spec

NAME = "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py"
PREFIX = "QUBITZ_QWEN38_FLASH_NEXT_"
HELP = "--spec-type draft-mtp --spec-draft-model --spec-draft-n-max"
BASE_HASH = "5ce89370720f8bf90890f439361282104c1aa1482d4013bb9a50923e758e71a4"
HEAD_HASH = "8087dbb39fc73f79ac069b1debe4069a44b73ada900c8bd761569d43a5a90231"


@pytest.fixture
def hooks(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(PREFIX):
            monkeypatch.delenv(key)
    spec = launcher_spec(variant_path(NAME).read_text(encoding="utf-8"))
    namespace = {"Any": Any, "Sequence": Sequence, "Path": Path, "os": os, "re": re,
                 "shutil": shutil, "subprocess": subprocess, "LocalOnlyApp": SimpleNamespace}
    tree = ast.parse(spec["wrapper"]["253"])
    tree.body = [node for node in tree.body if isinstance(node, (ast.Assign, ast.FunctionDef))]
    exec(compile(tree, NAME, "exec"), namespace)  # noqa: S102
    return SimpleNamespace(ns=namespace, runtime=namespace["_qwen38_flash_next_runtime_tag"],
                           arguments=namespace["_qwen38_flash_next_mtp_arguments"])


@pytest.fixture
def artifacts(hooks, monkeypatch, tmp_path):
    directory = tmp_path / ".cache/models/unsloth/Qwen3.8-Flash-Next-GGUF"
    head = directory / "MTP/mtp-Qwen3.8-Flash-Next-Q4_K_M.gguf"
    head.parent.mkdir(parents=True)
    head.write_bytes(b"fixture head")
    sizes = {head: 2786204800}
    models = directory / "UD-IQ4_XS"
    models.mkdir()
    names = hooks.ns["QWEN38_FLASH_NEXT_GGUF_SHARDS"]
    for name, size in zip(names, (10946624, 49835229856, 43836407744), strict=True):
        path = models / name
        path.write_bytes(b"fixture base")
        sizes[path] = size
    original = Path.stat

    def stat(path, *args, **kwargs):
        observed = original(path, *args, **kwargs)
        if path in sizes:
            values = list(observed)
            values[6] = sizes[path]
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(hashlib, "file_digest", lambda stream, _algorithm: SimpleNamespace(
        hexdigest=lambda: HEAD_HASH if Path(stream.name) == head else BASE_HASH,
    ))
    calls = []

    def version(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=0, stdout="build 11887, commit 6050b467c", stderr="")

    monkeypatch.setattr(subprocess, "run", version)
    monkeypatch.setenv(PREFIX + "MTP", "standard")
    return SimpleNamespace(root=tmp_path, head=head, base=models / names[0], sizes=sizes, calls=calls)


def args(hooks, artifacts, help_text=HELP):
    return hooks.arguments(artifacts.root, artifacts.base, "/fixture/llama-server", help_text, windows=False)


def test_default_is_unchanged_and_has_no_io(hooks, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: pytest.fail("Unexpected subprocess"))
    assert hooks.runtime() == "b10809"
    assert hooks.arguments(None, Path("unused"), "", "", windows=False) == ([], {"mode": "off"})


@pytest.mark.parametrize("mode,expected", [("standard", "b11887"), ("off", "b10809")])
def test_runtime_is_variant_local_and_explicit(hooks, monkeypatch, tmp_path, mode, expected):
    monkeypatch.setenv(PREFIX + "MTP", mode)
    base = SimpleNamespace()
    hooks.ns["_install_qwen38_flash_next_llamacpp_runtime"](SimpleNamespace(base=base))
    assert base.QUBITZ_LLAMACPP_RUNTIME_TAG == expected
    assert base.llamacpp_runtime_dir(tmp_path) == tmp_path / ".cache" / ("llama.cpp-" + expected)
    assert base.DEFAULT_LLAMACPP_RELEASE_API_URL.endswith("/tags/" + expected)


@pytest.mark.parametrize("mode,message", [("shared", "tensor-borrowing"), ("yes", "off, standard"), ("auto", "off, standard")])
def test_incompatible_modes_fail_before_io(hooks, monkeypatch, mode, message):
    monkeypatch.setenv(PREFIX + "MTP", mode)
    monkeypatch.setenv(PREFIX + "RUNTIME_TAG", "b11887")
    with pytest.raises(RuntimeError, match=message):
        hooks.arguments(None, Path("unused"), "", "", windows=False)


def test_pinned_head_and_metadata_are_required(hooks, artifacts):
    command, receipt = args(hooks, artifacts)
    assert command == ["--spec-type", "draft-mtp", "--spec-draft-model", str(artifacts.head),
                       "--spec-draft-n-max", "3"]
    assert receipt["head_sha256"] == HEAD_HASH and receipt["base_metadata_sha256"] == BASE_HASH
    assert artifacts.calls == [["/fixture/llama-server", "--version"]]


@pytest.mark.parametrize("value", ["1", "5"])
def test_bounded_draft_count(hooks, artifacts, monkeypatch, value):
    monkeypatch.setenv(PREFIX + "MTP_DRAFT_TOKENS", value)
    assert args(hooks, artifacts)[0][-1] == value


@pytest.mark.parametrize("value", ["0", "6", "abc", "-1"])
def test_bad_draft_count(hooks, artifacts, monkeypatch, value):
    monkeypatch.setenv(PREFIX + "MTP_DRAFT_TOKENS", value)
    with pytest.raises(RuntimeError, match="MTP_DRAFT_TOKENS"):
        args(hooks, artifacts)


@pytest.mark.parametrize("help_text", ["", "--spec-type draft-simple", HELP.replace("draft-mtp", "")])
def test_generic_speculation_is_not_mtp_compatibility(hooks, artifacts, help_text):
    with pytest.raises(RuntimeError, match="advertise"):
        args(hooks, artifacts, help_text)
    assert artifacts.calls == []


def test_old_or_overridden_executable_is_not_accepted(hooks, artifacts, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        returncode=0, stdout="build 10809, commit 5266f24da", stderr=""))
    with pytest.raises(RuntimeError, match="not the pinned"):
        args(hooks, artifacts)


def test_legacy_runtime_is_not_silently_upgraded(hooks, artifacts, monkeypatch):
    monkeypatch.setenv(PREFIX + "RUNTIME_TAG", "b10809")
    with pytest.raises(RuntimeError, match="requires the isolated"):
        args(hooks, artifacts)


def test_unknown_runtime_is_not_downloaded(hooks, monkeypatch):
    monkeypatch.setenv(PREFIX + "RUNTIME_TAG", "latest")
    with pytest.raises(RuntimeError, match="RUNTIME_TAG"):
        hooks.runtime()


def test_incomplete_head_is_not_used(hooks, artifacts):
    artifacts.sizes[artifacts.head] -= 1
    with pytest.raises(RuntimeError, match="head size"):
        args(hooks, artifacts)


def test_wrong_checksum_is_not_used(hooks, artifacts, monkeypatch):
    monkeypatch.setattr(hashlib, "file_digest", lambda *_a: SimpleNamespace(hexdigest=lambda: "bad"))
    with pytest.raises(RuntimeError, match="base metadata"):
        args(hooks, artifacts)


def test_incomplete_base_is_not_used(hooks, artifacts):
    artifacts.sizes[artifacts.base] -= 1
    with pytest.raises(RuntimeError, match="complete IQ4_XS"):
        args(hooks, artifacts)


@pytest.mark.parametrize("mode", ["shared", "shared-q8", "shared-q4"])
def test_shared_mode_selects_only_the_pinned_fork(hooks, monkeypatch, mode):
    monkeypatch.setenv(PREFIX + "MTP", mode)
    assert hooks.runtime() == "unsloth-mtp-6fcaa16f"


def test_missing_fork_is_not_silently_downloaded(hooks, monkeypatch, tmp_path):
    monkeypatch.setenv(PREFIX + "MTP", "shared-q8")
    base = SimpleNamespace()
    hooks.ns["_install_qwen38_flash_next_llamacpp_runtime"](SimpleNamespace(base=base))
    with pytest.raises(RuntimeError, match="no automatic fork acquisition"):
        base.llamacpp_runtime_dir(tmp_path)


@pytest.mark.parametrize("mode,size,checksum", [
    ("shared-q8", 2786568256, "5ff54097406a905cf3a724c709124ceb0e3e10235ee862298969e91c96fa96e6"),
    ("shared-q4", 1907151936, "f521868a9e143718bef513772f6e04d9642551e362cf2439636d2abdbd149dfc"),
])
def test_shared_head_uses_verified_build_and_two_drafts(hooks, artifacts, monkeypatch, mode, size, checksum):
    monkeypatch.setenv(PREFIX + "MTP", mode)
    shared = artifacts.head.with_name("mtp-Qwen3.8-Flash-Next-" + mode.replace("shared-q", "shared-Q").replace("Q8", "Q8_0").replace("Q4", "Q4_K_M") + ".gguf")
    shared.write_bytes(b"fixture shared head")
    artifacts.sizes[shared] = size
    binary = artifacts.root / "runtime/llama-server"
    binary.parent.mkdir()
    binary.write_bytes(b"fixture executable")
    (binary.parent / "qubitz-build.json").write_text(json.dumps({
        "commit": "6fcaa16f4b360649933a54d1f91ad40ed35c0e11",
        "files": {binary.name: {"bytes": len(b"fixture executable"), "sha256": "binary-hash"}},
    }))
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        returncode=0, stdout="build 10715, commit 6fcaa16", stderr=""))
    monkeypatch.setattr(hashlib, "file_digest", lambda stream, _algorithm: SimpleNamespace(
        hexdigest=lambda: checksum if Path(stream.name) == shared else "binary-hash" if Path(stream.name) == binary else BASE_HASH,
    ))
    command, receipt = hooks.arguments(artifacts.root, artifacts.base, str(binary), HELP, windows=False)
    assert command[-1] == "2" and str(shared) in command
    assert receipt["head_sha256"] == checksum and receipt["runtime_tag"] == "unsloth-mtp-6fcaa16f"


@pytest.mark.parametrize("reserve,expected", [(None, "5632,5632"), ("4096", "5632,5632"), ("5120", "6656,6656"), ("0", None)])
def test_shared_memory_reserve_cannot_be_weakened(hooks, artifacts, monkeypatch, reserve, expected):
    monkeypatch.setenv(PREFIX + "MTP", "shared-q8")
    if reserve is not None:
        monkeypatch.setenv(PREFIX + "MTP_RESERVE_MIB", reserve)
    ns = hooks.ns
    ns["_qwen38_flash_next_gpu_inventory"] = lambda: [
        {"index": index, "free_mib": 24000, "total_mib": 24576} for index in (0, 1)]
    ns["_qwen38_flash_next_mtp_arguments"] = lambda *_a, **_kw: ([], {"mode": "shared-q8"})

    class Server:
        _qubitz_server_help_cache: ClassVar[dict[str, str]] = {
            "/fixture/server": " ".join(ns["QWEN38_FLASH_NEXT_REQUIRED_LLAMA_FLAGS"]),
        }

        def _launch_command(self):
            return ["/fixture/server", "--model", str(artifacts.base)]

    ns["_install_qwen38_flash_next_multi_gpu_layer_split"](SimpleNamespace(base=SimpleNamespace(LlamaCppServerProcess=Server)))
    if expected is None:
        with pytest.raises(RuntimeError, match="at least 4096"):
            Server()._launch_command()
    else:
        command = Server()._launch_command()
        assert command[command.index("--fit-target") + 1] == expected


@pytest.mark.parametrize("encoded", [False, True])
def test_native_and_encoded_commands_keep_exact_mtp_arguments(hooks, artifacts, monkeypatch, encoded):
    ns = hooks.ns
    required = ns["QWEN38_FLASH_NEXT_REQUIRED_LLAMA_FLAGS"]
    executable = "/fixture/llama-server"
    command = [executable, "--model", str(artifacts.base), "--ctx-size", "262144"]
    if encoded:
        script = "& " + " ".join("'" + arg + "'" for arg in command)
        command = ["powershell.exe", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]

    class Server:
        _qubitz_server_help_cache: ClassVar[dict[str, str]] = {executable: " ".join(required) + " " + HELP}
        runtime_workspace = artifacts.root

        def _launch_command(self):
            return command

    ns["_qwen38_flash_next_gpu_inventory"] = lambda: [
        {"index": index, "free_mib": 24000, "total_mib": 24576} for index in (0, 1)]
    # The pure helper is separately covered with checked artifacts; use a quoted
    # Windows path here to exercise command rendering without relying on a drive mount.
    if encoded:
        ns["_qwen38_flash_next_mtp_arguments"] = lambda *_a, **_kw: (
            ["--spec-type", "draft-mtp", "--spec-draft-model", "D:\\owner's head.gguf"], {"mode": "standard"})
    ns["_install_qwen38_flash_next_multi_gpu_layer_split"](SimpleNamespace(base=SimpleNamespace(LlamaCppServerProcess=Server)))
    result = Server()._launch_command()
    if encoded:
        rendered = base64.b64decode(result[-1]).decode("utf-16-le")
        assert "'D:\\owner''s head.gguf'" in rendered
        assert rendered.count("'--spec-type'") == 1
    else:
        assert result.count("--spec-type") == result.count("--spec-draft-model") == 1
        assert result[result.index("--spec-type") + 1] == "draft-mtp"
        assert result[result.index("--ctx-size") + 1] == "262144"


@pytest.mark.parametrize("enabled,mode,tag", [(False, "off", "b10809"), (True, "shared-q4", "unsloth-mtp-6fcaa16f")])
def test_ui_selection_overrides_environment_without_mutating_it(hooks, monkeypatch, enabled, mode, tag):
    monkeypatch.setenv(PREFIX + "MTP", "standard")
    monkeypatch.setenv(PREFIX + "RUNTIME_TAG", "b11887")
    before = dict(os.environ)
    config = SimpleNamespace(mtp_enabled=enabled)
    assert hooks.ns["_qwen38_flash_next_mtp_mode"](config) == mode
    assert hooks.runtime(config) == tag
    assert dict(os.environ) == before
    if not enabled:
        assert hooks.arguments(None, Path("unused"), "", "", windows=False, config=config) == ([], {"mode": "off"})


def test_ui_mode_does_not_accept_truthy_non_boolean_values(hooks):
    with pytest.raises(ValueError, match="boolean"):
        hooks.runtime(SimpleNamespace(mtp_enabled="On"))


def test_ui_provider_selects_cached_artifacts_and_refuses_existing_server(hooks, artifacts, monkeypatch):
    monkeypatch.delenv(PREFIX + "MTP")
    detections = []

    class Client:
        reachable: ClassVar[list[str]] = []

        @classmethod
        def detect(cls, config):
            detections.append(config)
            return "fresh-owned"

        @classmethod
        def _reachable_transports(cls, _url):
            return cls.reachable

    base = SimpleNamespace(LlamaCppClient=Client, normalize_base_url=lambda value: value,
                           LLAMACPP_SERVER_URL_ENV_NAME="QUBITZ_TEST_URL")
    hooks.ns["_install_qwen38_flash_next_mtp_controls"](SimpleNamespace(base=base))
    config = SimpleNamespace(runtime_workspace=artifacts.root, workspace=artifacts.root,
                             server_url="http://fixture", llama_server_path=None)
    assert base.qubitz_mtp_status(config)["enabled"] is False
    with pytest.raises(RuntimeError, match="no download"):
        base.qubitz_configure_mtp(config, True)
    assert not hasattr(config, "mtp_enabled") and not detections
    head = artifacts.head.with_name("mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf")
    head.write_bytes(b"fixture shared")
    artifacts.sizes[head] = 1907151936
    runtime = artifacts.root / ".cache/llama.cpp-unsloth-mtp-6fcaa16f"
    runtime.mkdir()
    (runtime / "llama-server.exe").write_bytes(b"fixture binary")
    (runtime / "qubitz-build.json").write_text("{}")
    before = dict(os.environ)
    base.qubitz_configure_mtp(config, True)
    assert config.mtp_enabled is True and config.llama_server_path == str(runtime / "llama-server.exe")
    assert base.qubitz_mtp_status(config)["available"]
    assert Client.detect(config) == "fresh-owned"
    Client.reachable = ["unowned transport"]
    with pytest.raises(RuntimeError, match="existing server"):
        Client.detect(config)
    assert detections == [config] and dict(os.environ) == before
