"""Model-free contracts for the Gemma and Ministral 12 GB copies."""
from __future__ import annotations

import ast
import base64
import json
import re
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

import qubitz_release_gate as gate
from runtime.catalog import model_files
from runtime.paths import PROJECT_ROOT, variant_path
from runtime.source import launcher_spec
from runtime.symbols import symbol_sources

VARIANTS = (
    ("AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.py", "gemma4_12b",
     "unsloth/gemma-4-12b-it-GGUF", "gemma-4-12b-it-Q4_K_M.gguf"),
    ("AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py", "ministral3_8b",
     "mistralai/Ministral-3-8B-Instruct-2512-GGUF", "Ministral-3-8B-Instruct-2512-Q5_K_M.gguf"),
)


@pytest.mark.parametrize("name,prefix,repo,filename", VARIANTS)
def test_exact_model_and_12gb_profile(name, prefix, repo, filename):
    path = variant_path(name)
    files = model_files(path)
    assert files.repo == repo and files.filename == filename
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    row = next(row for row in profiles if row["filename"] == name)
    assert row["num_ctx"] == 262144
    assert row["runtime_contract"]["constants"]["QUBITZ_VRAM_BUDGET_MIB"] == 12288
    assert row["runtime_contract"]["dual_gpu"] is False
    spec = launcher_spec(path.read_text())
    assert "CUDA0,CUDA1" not in spec["wrapper"]["253"]
    assert f"_install_{prefix}_single_gpu(_APP)" in spec["wrapper"]["253"]


@pytest.mark.parametrize("name,prefix,repo,filename", VARIANTS)
@pytest.mark.parametrize("encoded", [False, True], ids=["native", "windows"])
def test_single_device_preserves_upstream_fit_and_context(name, prefix, repo, filename, encoded):
    del repo, filename
    spec = launcher_spec(variant_path(name).read_text())
    tree = ast.parse(spec["wrapper"]["253"])
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == f"_install_{prefix}_single_gpu")
    namespace = {"Any": Any, "LocalOnlyApp": SimpleNamespace, "re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)  # noqa: S102
    argv = ["/fixture/llama-server", "--model", "/fixture/model.gguf", "--ctx-size", "65536",
            "--fit", "on", "--fit-target", "13824", "--cache-type-k", "q8_0",
            "--device", "CUDA0,CUDA1", "--split-mode", "layer", "--tensor-split", "1,1",
            "--main-gpu", "1"]
    script = "# QUBITZ_OWNER=fixture\n& " + " ".join(f"'{value}'" for value in argv)
    command = (["powershell.exe", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode()]
               if encoded else argv)

    class Server:
        _qubitz_server_help_cache: ClassVar[dict[str, str]] = {
            "fixture": "--device --split-mode --main-gpu --fit --fit-target",
        }

        def _launch_command(self):
            return list(command)

    namespace[f"_install_{prefix}_single_gpu"](
        SimpleNamespace(base=SimpleNamespace(LlamaCppServerProcess=Server)),
    )
    result = Server()._launch_command()
    if encoded:
        decoded = base64.b64decode(result[-1]).decode("utf-16-le")
        assert decoded.startswith("# QUBITZ_OWNER=fixture\n")
        result = re.findall(r"'([^']*)'", decoded)
    for option, value in (("--device", "CUDA0"), ("--split-mode", "none"), ("--main-gpu", "0"),
                          ("--ctx-size", "65536"), ("--fit-target", "13824"), ("--cache-type-k", "q8_0")):
        assert result.count(option) == 1
        assert result[result.index(option) + 1] == value
    assert "--tensor-split" not in result
    Server._qubitz_server_help_cache = {}
    with pytest.raises(RuntimeError, match="required single-GPU 12 GB fitting controls"):
        Server()._launch_command()


def test_ministral_instruct_uses_its_own_low_temperature_profile():
    name = VARIANTS[1][0]
    spec = launcher_spec(variant_path(name).read_text())
    tree = ast.parse(spec["wrapper"]["253"])
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                and node.name == "_ministral3_instruct_sampling_profile")
    namespace = {"_MINISTRAL3_ORIGINAL_SAMPLING_PROFILE": lambda *args: (
        "fallback_coding_edit", 0.6, 0.95, 20, 0.0, 1.0, 0.0,
    )}
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, "exec"), namespace)  # noqa: S102
    profile = namespace["_ministral3_instruct_sampling_profile"]("edit", "tool_loop", VARIANTS[1][2])
    assert profile[1:] == (0.05, 1.0, 0, 0.0, 1.0, 0.0)


@pytest.mark.parametrize("name,prefix,repo,filename", VARIANTS)
def test_release_sampling_registration_matches_every_route(name, prefix, repo, filename):
    del filename
    family = "gemma_4" if prefix == "gemma4_12b" else "ministral3_instruct_fallback"
    assert gate.VARIANT_SAMPLING_FAMILIES.get(name) == family
    fragments = symbol_sources("wrapper", PROJECT_ROOT / "runtime")
    shared = ast.parse("\n".join(fragment for fragment in fragments.values() if fragment.startswith((
        "ROUTE_SAMPLING_PROFILES:", "def _sampling_profile_for_route(",
    ))))
    nodes = [node for node in shared.body if (
        isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "ROUTE_SAMPLING_PROFILES"
    ) or (isinstance(node, ast.FunctionDef) and node.name == "_sampling_profile_for_route")]
    assert len(nodes) == 2
    namespace = {"re": re}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), name, "exec"), namespace)  # noqa: S102
    actual = namespace["_sampling_profile_for_route"]
    if prefix == "ministral3_8b":
        spec = launcher_spec(variant_path(name).read_text())
        adapter = next(node for node in ast.parse(spec["wrapper"]["253"]).body
                       if isinstance(node, ast.FunctionDef)
                       and node.name == "_ministral3_instruct_sampling_profile")
        namespace["_MINISTRAL3_ORIGINAL_SAMPLING_PROFILE"] = actual
        exec(compile(ast.Module(body=[adapter], type_ignores=[]), name, "exec"), namespace)  # noqa: S102
        actual = namespace["_ministral3_instruct_sampling_profile"]
    expected = gate._sampling_profiles_for_family(family)
    for route, prompt, selected_route in gate.SAMPLING_ROUTE_CASES:
        assert actual(prompt, selected_route, repo) == (f"{family}_{route}", *expected[route])
