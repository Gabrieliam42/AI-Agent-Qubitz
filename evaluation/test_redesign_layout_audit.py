"""All-launcher layout invariants without model startup or root-memory writes."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.paths import PROJECT_ROOT, variant_paths
from runtime.source import launcher_spec
from runtime.variant_policy import user_variant_allowed

PATHS = variant_paths()


@pytest.fixture(params=PATHS, ids=lambda path: path.stem)
def isolated_launcher(request, tmp_path, monkeypatch):
    path = request.param
    monkeypatch.chdir(tmp_path)
    name = "redesign_audit_" + re.sub(r"\W", "_", path.stem)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    embedded_before = set(sys.modules)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        yield module, path
    finally:
        sys.modules.pop(name, None)
        for key in set(sys.modules) - embedded_before:
            if "__embedded__AI_Agent_Qubitz_" in key:
                sys.modules.pop(key, None)


def test_every_normal_launcher_from_foreign_cwd_keeps_project_and_task_roots_separate(isolated_launcher, tmp_path):
    module, path = isolated_launcher
    app = module._APP
    assert app.runtime_workspace == PROJECT_ROOT
    assert app.wrapper_script == path
    assert Path(module.__file__) == path
    assert Path(app.base.__file__) == path
    config = app._build_config(app.parse_args(["--workspace", str(tmp_path), "--cli"]), tmp_path)
    assert config.workspace == tmp_path
    assert config.runtime_workspace == PROJECT_ROOT
    host = app._build_mcp_host_class()(tmp_path)
    params = host._server_parameters()
    assert params.command == sys.executable
    assert params.args == [str(path), "--serve-mcp", "--workspace", "."]
    assert Path(params.cwd) == tmp_path
    assert params.env["QUBITZ_RUNTIME_ROOT"] == str(PROJECT_ROOT)
    assert params.env["QUBITZ_LAUNCH_SCRIPT"] == str(path)


@pytest.mark.parametrize("native_exists", [False, True])
def test_transport_runtime_probe_does_not_use_foreign_cwd(isolated_launcher, tmp_path, monkeypatch, native_exists):
    module, _ = isolated_launcher
    app = module._APP
    probes = []
    native = tmp_path / "native-placeholder"
    if native_exists:
        native.touch()

    class Direct:
        def __init__(self, *_args):
            pass

        def probe_json(self, _path):
            return {"status_code": 200}

    class Bridge:
        def __init__(self, *_args):
            pass

        def probe_json(self, _path):
            return {"status_code": 200, "json": {"data": []}}

    def executable(root):
        probes.append(root)
        return native

    monkeypatch.setattr(app.base, "DirectHTTPTransport", Direct)
    monkeypatch.setattr(app.base, "WindowsBridgeHTTPTransport", Bridge)
    monkeypatch.setattr(app.base, "llamacpp_native_server_executable", executable)
    monkeypatch.setattr(app.base, "in_wsl", lambda: True)
    monkeypatch.setattr(module, "_wsl_windows_executable_interop_available", lambda _base: True)
    monkeypatch.setattr(module, "_llamacpp_listener_port_is_bound", lambda *_args: True)
    transports = app.base.LlamaCppClient._reachable_transports("http://127.0.0.1:8001")
    assert probes == [app.runtime_workspace]
    assert isinstance(transports[0], Direct)
    assert len(transports) == (1 if native_exists else 2)


@pytest.mark.parametrize("path", PATHS, ids=lambda path: path.stem)
def test_every_spec_and_guide_bind_the_actual_new_layout(path):
    spec = launcher_spec(path.read_text(encoding="utf-8"))
    assert spec is not None and spec["schema_version"] == 2 and spec["filename"] == path.name
    assert path.parent == PROJECT_ROOT / "variants"
    guide = PROJECT_ROOT / "documentation" / path.with_suffix(".md").name
    text = guide.read_text(encoding="utf-8")
    assert f"`variants/{path.name}`" in text
    assert "requires project-root `runtime/`" in text
    assert not (PROJECT_ROOT / path.name).exists()


def test_registered_profiles_and_launchers_are_exactly_equal():
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())
    assert len(PATHS) == 40
    assert {path.name for path in PATHS} == {row["filename"] for row in profiles["variants"]
                                           if user_variant_allowed(row["filename"])}


@pytest.mark.parametrize("binding", ["Path.cwd()", "_APP.wrapper_script.parent", "_APP.runtime_workspace.parent"])
def test_transport_layout_guard_rejects_stale_or_altered_root(binding, tmp_path):
    import qubitz_variant_builder as builder
    from runtime import read_variant_source

    original = read_variant_source(PATHS[0])
    altered = original.replace(builder.RUNTIME_ROOT_TRANSPORT_PROBE,
                               f"base.llamacpp_native_server_executable({binding})", 1)
    assert altered != original
    target = tmp_path / PATHS[0].name
    target.write_text(altered)
    assert any("shared runtime-root transport drift" in issue
               for issue in builder.check_template([target], builder.load_template()))
    if binding != "Path.cwd()":
        assert builder.profile_guard_fields(altered) != builder.profile_guard_fields(original)


def test_documentation_local_links_resolve_from_their_actual_file_location():
    documents = [*(PROJECT_ROOT / "documentation").glob("*.md"),
                 PROJECT_ROOT / "README.md", PROJECT_ROOT / "PROJECT.md",
                 PROJECT_ROOT / "MCP_CAPABILITIES.md", PROJECT_ROOT / "runtime/README.md"]
    failures = []
    for document in documents:
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            relative = target.split("#", 1)[0].strip("<>")
            if relative and not (document.parent / relative).exists():
                failures.append(f"{document.relative_to(PROJECT_ROOT)}: {target}")
    assert not failures, failures


def test_legacy_question_batch_uses_canonical_paths_without_unowned_cleanup():
    text = (PROJECT_ROOT / "run_variant_question.sh").read_text(encoding="utf-8")
    assert "from runtime.paths import variant_paths" in text
    assert "live_variant_allowed" in text
    assert 'for f in "${LAUNCHERS[@]}"' in text
    assert '--workspace "$ROOT"' in text
    assert "pkill" not in text and "killall" not in text
    assert "/mnt/d/AI_Research" not in text
    result = subprocess.run(["bash", "-n", str(PROJECT_ROOT / "run_variant_question.sh")],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
