"""Public 48GB-tier placement contracts, without models or GPU execution."""
from __future__ import annotations

import ast
import base64
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

from runtime.paths import PROJECT_ROOT, variant_path
from runtime.source import launcher_spec

VARIANTS = (
    ("DeepSeek-V4-Flash-0731-IQ4", "deepseek_v4_flash"),
    ("GPT-OSS-120B_F16", "gpt_oss_120b"),
    ("Hypernova-60B-2605-Q5", "hypernova"),
    ("Kimi-Linear-48B-A3B-Instruct.i1-Q6", "kimi_linear"),
    ("Ling-3.0-Flash-124B-A5.1B-Q5", "ling3_flash"),
    ("Llama-3_3-Nemotron-Super-49B-v1_5_Q6", "llama_3_3_nemotron_super"),
    ("Ornith-1.5-122B-UD-Q5_K_XL", "ornith15_122b"),
    ("Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL", "nemotron35_super_vl"),
    ("Qwen3-Coder-Next-80B-A3B-Q4", "qwen3_next"),
    ("Qwen3.8-Flash-Next-125B-A6B-IQ4", "qwen38_flash_next"),
)


@pytest.fixture(params=VARIANTS, ids=[item[0] for item in VARIANTS])
def hooks(request, monkeypatch):
    label, prefix = request.param
    filename = f"AI_Agent_Qubitz_{label}.py"
    spec = launcher_spec(variant_path(filename).read_text())
    tree = ast.parse(spec["wrapper"]["253"])
    nodes = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id.startswith(prefix.upper() + "_")
                   for target in node.targets):
                nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and (
            node.name.startswith("_" + prefix + "_")
            or node.name == f"_install_{prefix}_multi_gpu_layer_split"
        ):
            nodes.append(node)
    namespace = {"Any": Any, "Sequence": Sequence, "Path": Path, "os": os, "re": re,
                 "shutil": shutil, "subprocess": subprocess, "LocalOnlyApp": SimpleNamespace}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)  # noqa: S102
    for key in os.environ:
        if key.startswith("QUBITZ_" + prefix.upper()):
            monkeypatch.delenv(key)
    return SimpleNamespace(filename=filename, prefix=prefix, namespace=namespace,
                           plan=namespace[f"_{prefix}_split_plan"],
                           inventory=namespace[f"_{prefix}_gpu_inventory"])


def devices(free=(24000, 18000)):
    return [{"index": index, "total_mib": 24576, "free_mib": value}
            for index, value in enumerate(free)]


def test_registered_dual_gpu_without_fixed_ram_capacity(hooks):
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    row = next(row for row in profiles if row["filename"] == hooks.filename)
    assert row["runtime_contract"]["dual_gpu"]
    assert hooks.namespace[hooks.prefix.upper() + "_GPU_INDICES"] == (0, 1)
    assert not any("RAM_CAPACITY" in key or "REQUIRED_RAM" in key for key in hooks.namespace)


@pytest.mark.parametrize("model_mib", [1024, 48000, 100000, 250000])
def test_weights_larger_than_vram_are_not_rejected(hooks, model_mib):
    plan = hooks.plan(devices(), model_mib)
    assert plan["usable_mib"] == [22464, 16464]
    assert plan["fit_target"] == "1536,1536"
    assert plan["minimum_cpu_resident_mib"] == max(0, model_mib + 512 - 38928)
    assert plan["devices"] == devices()


@pytest.mark.parametrize("size", [0, -1])
def test_invalid_weight_size_is_not_treated_as_ram_overflow(hooks, size):
    with pytest.raises(RuntimeError, match="positive"):
        hooks.plan(devices(), size)


def test_one_gpu_is_not_mistaken_for_two(hooks):
    with pytest.raises(RuntimeError, match="exactly two"):
        hooks.plan(devices()[:1], 100000)


def test_per_device_headroom_is_preserved(hooks):
    with pytest.raises(RuntimeError, match="safety margin"):
        hooks.plan(devices((1536, 24576)), 100000)


@pytest.mark.parametrize("output,code,error", [
    ("1, RTX 3090, gpu1, bus1, 24576, 18000\n0, RTX 3090, gpu0, bus0, 24576, 24000", 0, None),
    ("0, RTX 3090, gpu0, bus0, 24576, 24000", 0, "missing device"),
    ("", 1, "could not query"),
])
def test_inventory_uses_each_devices_current_free_memory(hooks, monkeypatch, tmp_path, output, code, error):
    executable = tmp_path / "nvidia-smi-fixture"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda name: str(executable))
    calls = []

    def query(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 20
        return SimpleNamespace(returncode=code, stdout=output, stderr="fixture query error" if code else "")

    monkeypatch.setattr(subprocess, "run", query)
    if error:
        with pytest.raises(RuntimeError, match=error):
            hooks.inventory()
    else:
        actual = hooks.inventory()
        assert [(item["index"], item["free_mib"]) for item in actual] == [(0, 24000), (1, 18000)]
    assert len(calls) == 1 and "memory.free" in calls[0][1]


@pytest.mark.parametrize("encoded", [False, True], ids=["native", "windows-encoded"])
def test_upstream_launch_removes_gpu_only_overrides(hooks, tmp_path, encoded):
    namespace = hooks.namespace
    required = namespace[hooks.prefix.upper() + "_REQUIRED_LLAMA_FLAGS"]
    model = tmp_path / "model-fixture.gguf"
    model.write_bytes(b"GGUFtest")
    shard_names = namespace.get(hooks.prefix.upper() + "_GGUF_SHARDS", ())
    if shard_names:
        for name in shard_names:
            (tmp_path / name).write_bytes(b"GGUFtest")
        model = tmp_path / shard_names[0]
    namespace[f"_{hooks.prefix}_gpu_inventory"] = devices
    args = ["/fixture/llama-server", "--model", str(model), "--ctx-size", "202752",
            "--device", "CUDA0", "--split-mode", "none", "--n-gpu-layers", "999",
            "--tensor-split", "1,1",
            "--fit", "on", "--fit-ctx", "16384", "--fit-target", "1536"]
    if encoded:
        script = "& " + " ".join(f"'{arg}'" for arg in args)
        args = ["powershell.exe", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]

    class Server:
        _qubitz_server_help_cache: ClassVar[dict[str, str]] = {
            "/fixture/llama-server": " ".join((*required, "--cache-type-k", "--cache-type-v", "--batch-size")),
        }

        def _launch_command(self):
            return args

    namespace[f"_install_{hooks.prefix}_multi_gpu_layer_split"](
        SimpleNamespace(base=SimpleNamespace(LlamaCppServerProcess=Server)),
    )
    server = Server()
    command = server._launch_command()
    if encoded:
        script = base64.b64decode(command[-1]).decode("utf-16-le")
        command = re.findall(r"'([^']*)'", script)
    for flag, expected in (("--device", "CUDA0,CUDA1"), ("--split-mode", "layer"),
                           ("--fit", "on"),
                           ("--fit-ctx", str(namespace[hooks.prefix.upper() + "_MIN_FIT_CONTEXT"])),
                           ("--fit-target", "1536,1536"), ("--ctx-size", "202752")):
        assert command.count(flag) == 1
        assert command[command.index(flag) + 1] == expected
    assert not {"--n-gpu-layers", "--gpu-layers", "--tensor-split"}.intersection(command)
    plan = getattr(server, f"_qubitz_{hooks.prefix}_multi_gpu_plan")
    assert plan["usable_mib"] == [22464, 16464]


def test_composed_launcher_keeps_both_gpus_and_ram_fitting(hooks, tmp_path, monkeypatch):
    import qubitz_release_gate as gate

    module = gate._import_variant(variant_path(hooks.filename), "large_tier_composed_" + hooks.prefix)
    try:
        from runtime.catalog import model_files

        metadata = model_files(variant_path(hooks.filename))
        model = tmp_path / metadata.filename
        for name in metadata.shards:
            (tmp_path / name).write_bytes(b"GGUFtest")
        base = module._APP.base
        server_cls = base.LlamaCppServerProcess
        executable = "/fixture/llama-server"
        required = hooks.namespace[hooks.prefix.upper() + "_REQUIRED_LLAMA_FLAGS"]
        monkeypatch.setattr(server_cls, "resolve_executable", lambda self: executable)
        monkeypatch.setattr(server_cls, "resolve_model_path", lambda self: model)
        monkeypatch.setattr(server_cls, "_qubitz_server_help_cache", {
            executable: " ".join((*required, "--cache-type-k", "--cache-type-v", "--batch-size")),
        }, raising=False)
        monkeypatch.setattr(module, f"_{hooks.prefix}_gpu_inventory", devices)
        config = base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT)
        command = server_cls(config)._launch_command()
        assert command[0] == executable
        assert command[command.index("--device") + 1] == "CUDA0,CUDA1"
        assert command[command.index("--split-mode") + 1] == "layer"
        assert command.count("--fit") == 1
        assert not {"--n-gpu-layers", "--gpu-layers", "--tensor-split"}.intersection(command)
        assert command[command.index("--fit") + 1] == "on"
        assert command[command.index("--fit-target") + 1] == "1536,1536"
    finally:
        gate._unload_variant(module)
