"""CPU layout, one-variant launch dispatch, and read-only cache-color contracts."""
from __future__ import annotations

import copy
import json
import sys
import threading
import time
from pathlib import Path

import pytest
import test_followup_state_receipts as receipt_fixtures

from runtime import catalog, launcher, source
from runtime.development import launcher_text
from runtime.paths import (
    DEFAULT_VARIANT,
    PROJECT_ROOT,
    variant_path,
    variant_paths,
)
from runtime.variant_policy import user_variant_allowed

variant = receipt_fixtures.variant


def test_physical_layout_covers_exactly_all_profiles():
    expected = {row["filename"] for row in json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
                if user_variant_allowed(row["filename"])}
    actual = variant_paths()
    assert len(actual) == 40 and {path.name for path in actual} == expected
    assert not list(PROJECT_ROOT.glob("AI_Agent_Qubitz_*.py"))
    assert all(path.parent == PROJECT_ROOT / "variants" for path in actual)
    assert (PROJECT_ROOT / "AI Agent Qubitz.py").is_file()


def test_all_saved_profiles_remain_exact_after_directory_migration():
    import qubitz_variant_builder as builder

    assert builder.check_profiles(variant_paths()) == []


@pytest.mark.parametrize("change", ["guard", "other-constructor", "removed-guard"])
def test_layout_normalization_does_not_hide_runtime_root_drift(tmp_path, change):
    import qubitz_variant_builder as builder

    path = variant_path(DEFAULT_VARIANT)
    original = source.read_variant_source(path)
    if change == "guard":
        altered = original.replace('self.runtime_workspace.name == "variants"',
                                   'self.runtime_workspace.name == "elsewhere"', 1)
    elif change == "other-constructor":
        altered = original.replace("self.wrapper_script.parent.resolve()",
                                   "self.wrapper_script.parent.parent.resolve()", 1)
    else:
        altered = original.replace(builder.RUNTIME_ROOT_LAYOUT_GUARD, "", 1)
    if change != "removed-guard":
        assert builder.profile_guard_fields(altered) != builder.profile_guard_fields(original)
    target = tmp_path / path.name
    target.write_text(altered)
    template = builder.load_template()
    if change != "other-constructor":
        assert any("shared runtime-root layout drift" in issue
                   for issue in builder.check_template([target], template))


def test_documentation_layout_preserves_all_registered_variant_docs():
    docs = PROJECT_ROOT / "documentation"
    expected = {path.with_suffix(".md").name for path in variant_paths()}
    assert {path.name for path in docs.glob("AI_Agent_Qubitz_*.md")} == expected
    assert not list(PROJECT_ROOT.glob("AI_Agent_Qubitz_*.md"))
    assert all((docs / name).is_file() for name in
               ("README.md", "PROJECT.md", "num_ctx_values.md", "qubitz_variants_list.md"))
    assert "documentation/README.md" in (PROJECT_ROOT / "README.md").read_text()


@pytest.mark.parametrize("name", ["", "../AI_Agent_Qubitz_a.py", r"..\AI_Agent_Qubitz_a.py",
                                  "/AI_Agent_Qubitz_a.py", "foreign.py", "AI_Agent_Qubitz_a.txt", None])
def test_variant_paths_never_substitute_an_arbitrary_path(tmp_path, name):
    with pytest.raises(ValueError, match="basename"):
        variant_path(name, tmp_path)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["outside", "inside", "cycle"])
def test_variant_directory_aliases_are_rejected(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    target = tmp_path / "outside" if kind == "outside" else root / "other"
    if kind == "cycle":
        target = root / "variants"
    else:
        target.mkdir()
    (root / "variants").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="alias"):
        variant_paths(root)


@pytest.mark.parametrize("kind", ["outside", "inside", "dangling", "cycle"])
def test_variant_file_aliases_are_rejected(tmp_path, kind):
    directory = tmp_path / "variants"
    directory.mkdir()
    path = directory / "AI_Agent_Qubitz_a.py"
    target = tmp_path / "outside.py" if kind == "outside" else directory / "other.py"
    if kind == "cycle":
        target = path
    elif kind != "dangling":
        target.write_text("untouched = True\n")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="aliases"):
        variant_path(path.name, tmp_path)


def real_spec_launcher(root, filename=DEFAULT_VARIANT):
    spec = copy.deepcopy(source.launcher_spec(variant_path(DEFAULT_VARIANT).read_text()))
    spec["filename"] = filename
    directory = root / "variants"
    directory.mkdir(exist_ok=True)
    path = directory / filename
    path.write_text(launcher_text(spec))
    return path


def test_root_dispatch_loads_one_normal_entrypoint_and_preserves_arguments(tmp_path, monkeypatch):
    filename = "AI_Agent_Qubitz_fixture.py"
    target = real_spec_launcher(tmp_path, filename)
    real_spec_launcher(tmp_path)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    original = sys.argv
    calls = []
    monkeypatch.setattr(launcher.runpy, "run_path", lambda path, **kwargs: calls.append((path, kwargs, list(sys.argv))))
    prompt = "Explain the literal --variant token; do not change files."
    assert launcher.main(["--variant", filename, "--cli", "--prompt", prompt]) == 0
    assert calls == [(str(target), {"run_name": "__main__"},
                      [str(target), "--cli", "--prompt", prompt, "--workspace", str(tmp_path)])]
    assert sys.argv is original


def test_root_dispatch_preserves_explicit_workspace_and_restores_argv_on_error(tmp_path, monkeypatch):
    target = real_spec_launcher(tmp_path)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    original = sys.argv
    calls = []

    def run(path, **kwargs):
        calls.append(list(sys.argv))
        raise RuntimeError("fixture startup failure")

    monkeypatch.setattr(launcher.runpy, "run_path", run)
    with pytest.raises(RuntimeError, match="fixture startup failure"):
        launcher.main(["--workspace=chosen workspace", "--serve-mcp"])
    assert calls == [[str(target), "--workspace=chosen workspace", "--serve-mcp"]]
    assert sys.argv is original


def test_root_listing_and_help_do_not_import_variants(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "variants"
    directory.mkdir()
    names = ["AI_Agent_Qubitz_b.py", "AI_Agent_Qubitz_a.py"]
    for name in names:
        (directory / name).write_text("raise RuntimeError('must never run')\n")
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launcher.runpy, "run_path", lambda *_args, **_kwargs: pytest.fail("Imported a listing variant"))
    assert launcher.main(["--list-variants"]) == 0
    assert capsys.readouterr().out.splitlines() == sorted(names)
    with pytest.raises(SystemExit) as exit_info:
        launcher.main(["--help"])
    assert exit_info.value.code == 0


@pytest.mark.parametrize("kind", ["missing", "nondeclarative", "mismatched", "invalid-schema", "altered-bootstrap"])
def test_root_rejects_invalid_selection_before_dispatch(tmp_path, monkeypatch, kind):
    path = real_spec_launcher(tmp_path)
    text = path.read_text()
    if kind == "missing":
        filename = "AI_Agent_Qubitz_missing.py"
    else:
        filename = path.name
        if kind == "nondeclarative":
            text = "raise RuntimeError('must never run')\n"
        elif kind == "altered-bootstrap":
            text = text.replace("_qubitz_bootstrap_root.parent", "_qubitz_bootstrap_root.parent.parent")
        else:
            spec = source.launcher_spec(text)
            spec["filename" if kind == "mismatched" else "schema_version"] = "wrong.py" if kind == "mismatched" else 99
            text = launcher_text(spec)
        path.write_text(text)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launcher.runpy, "run_path", lambda *_args, **_kwargs: pytest.fail("Invalid variant dispatched"))
    with pytest.raises(SystemExit) as exit_info:
        launcher.main(["--variant", filename])
    assert exit_info.value.code == 2
    assert not (tmp_path / "models").exists() and not (tmp_path / ".cache").exists()


def fixture_model(root, filename="Model-00001-of-00003.gguf", wrapper=""):
    path = root / "AI_Agent_Qubitz_fixture.py"
    spec = {"schema_version": 2, "filename": path.name,
            "base": {"000": "DEFAULT_HF_GGUF_REPO_ID = 'owner/repo'\n" + f"DEFAULT_GGUF_MODEL_FILENAME = {filename!r}\n"},
            "wrapper": {"000": wrapper}}
    path.write_text(launcher_text(spec))
    return path


def weight(path, body=b"GGUF" + b"\0" * 24):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def test_cache_metadata_covers_every_real_variant_without_imports():
    profiles = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
    for row in profiles:
        if not user_variant_allowed(row["filename"]):
            continue
        metadata = catalog.model_files(variant_path(row["filename"]))
        assert metadata is not None
        assert metadata.repo == row["hf_gguf_repo_id"] and metadata.filename == row["gguf_filename"]
        for name, value in row["runtime_contract"]["constants"].items():
            if name.endswith("_GGUF_SHARDS"):
                assert metadata.shards == tuple(value)
            if name.endswith("_GGUF_PARTS"):
                assert metadata.parts == tuple(value)


@pytest.mark.parametrize("kind", ["directory-only", "missing-shard", "split-directories", "zero-shard", "invalid-header", "incomplete-name"])
def test_cache_indication_requires_the_exact_complete_file_set(tmp_path, kind):
    path = fixture_model(tmp_path)
    files = catalog.model_files(path).shards
    directory = tmp_path / ".cache/models/owner/repo/quant"
    directory.mkdir(parents=True)
    for index, name in enumerate(files):
        if kind == "directory-only" or (kind == "missing-shard" and index == 2):
            continue
        target = directory / name
        if kind == "split-directories" and index == 2:
            target = tmp_path / "models/other" / name
        if kind == "incomplete-name" and index == 2:
            target = target.with_name(name + ".incomplete")
        body = b"" if kind == "zero-shard" and index == 2 else b"GGUF" + b"\0" * 24
        if kind == "invalid-header" and index == 2:
            body = b"WRONG" + b"\0" * 24
        weight(target, body)
    assert catalog.cached_model_state(path, tmp_path) is False


def test_cache_refresh_detects_completed_and_removed_shards_without_mutating(tmp_path):
    path = fixture_model(tmp_path)
    files = catalog.model_files(path).shards
    targets = [tmp_path / "models/nested" / name for name in files]
    for target in targets:
        weight(target)
    before = [(target.read_bytes(), target.stat().st_mtime_ns) for target in targets]
    assert catalog.cached_model_state(path, tmp_path) is True
    assert before == [(target.read_bytes(), target.stat().st_mtime_ns) for target in targets]
    targets[-1].unlink()
    assert catalog.cached_model_state(path, tmp_path) is False


def test_single_file_and_multipart_cache_sets_are_supported(tmp_path):
    wrapper = "MODEL_GGUF_PARTS = ('Model.gguf.part1of2', 'Model.gguf.part2of2')\n"
    path = fixture_model(tmp_path, "Model.gguf", wrapper)
    metadata = catalog.model_files(path)
    assert len(metadata.parts) == 2
    directory = tmp_path / ".cache/models/owner/repo"
    weight(directory / metadata.parts[0])
    weight(directory / metadata.parts[1], b"tail")
    assert catalog.cached_model_state(path, tmp_path) is True
    (directory / metadata.parts[1]).unlink()
    assert catalog.cached_model_state(path, tmp_path) is False
    weight(directory / metadata.filename)
    assert catalog.cached_model_state(path, tmp_path) is True


def test_cache_does_not_read_escaped_model_files(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    path = fixture_model(root, "Model.gguf")
    outside = tmp_path / "outside"
    weight(outside / "Model.gguf")
    (root / "models").symlink_to(outside, target_is_directory=True)
    assert catalog.cached_model_state(path, root) is None


def test_invalid_metadata_is_unknown_and_never_evaluated(tmp_path, monkeypatch):
    path = fixture_model(tmp_path)
    spec = source.launcher_spec(path.read_text())
    spec["base"]["000"] = "DEFAULT_HF_GGUF_REPO_ID = __import__('os').system('must not run')\nDEFAULT_GGUF_MODEL_FILENAME = 'Model.gguf'\n"
    path.write_text(launcher_text(spec))
    monkeypatch.setattr(catalog.os, "system", lambda *_args: pytest.fail("Executed model metadata"))
    assert catalog.model_files(path) is None and catalog.cached_model_state(path, tmp_path) is None


def menu_entries(menu):
    return {str(menu.entrycget(index, "label")): index for index in range(int(menu.index("end")) + 1)
            if menu.type(index) == "radiobutton"}


def test_real_selector_colors_remain_selectable_and_refresh(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    runtime = tmp_path / "runtime"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata = catalog.model_files(Path(variant.__file__))
    folder = runtime / ".cache/models" / metadata.repo
    if metadata.subdirectories:
        folder /= metadata.subdirectories[0]
    for name in metadata.shards:
        weight(folder / name)
    original_cache_check = catalog.cached_model_states

    def fixture_cache_check(paths, runtime_root):
        assert runtime_root == PROJECT_ROOT
        return original_cache_check(paths, runtime)

    monkeypatch.setattr(catalog, "cached_model_states", fixture_cache_check)
    gui = base.QubitzGUI(base.AgentConfig(workspace=workspace, runtime_workspace=PROJECT_ROOT))
    try:
        gui.root.withdraw()
        gui._qubitz_refresh_variant_cache()
        wait_for_cache_scan(gui)
        menu = gui._qubitz_variant_menu
        entries = menu_entries(menu)
        assert len(entries) == 40
        assert {str(menu.entrycget(index, "state")) for index in entries.values()} == {"normal"}
        current = Path(variant.__file__).name
        assert str(menu.entrycget(entries[current], "foreground")) == "#ffffff"
        other = next(name for name in entries if name != current)
        assert str(menu.entrycget(entries[other], "foreground")) == "#9aa0a6"
        (folder / metadata.shards[-1]).unlink()
        gui._qubitz_refresh_variant_cache()
        wait_for_cache_scan(gui)
        assert str(menu.entrycget(entries[current], "foreground")) == "#9aa0a6"
        gui._qubitz_select_variant(current)
        assert not gui._qubitz_variant_switch_in_progress
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()


def wait_for_cache_scan(gui):
    deadline = time.monotonic() + 10
    while gui._qubitz_variant_cache_refresh_pending and time.monotonic() < deadline:
        gui.root.update()
        time.sleep(0.01)
    assert not gui._qubitz_variant_cache_refresh_pending


def test_cache_scan_does_not_block_tk_and_coalesces_repeated_requests(variant, tmp_path, monkeypatch):
    main_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    calls = []

    def delayed_scan(paths, runtime_root):
        names = tuple(path.name for path in paths)
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(5)
        return dict.fromkeys(names, True)

    monkeypatch.setattr(catalog, "cached_model_states", delayed_scan)
    base = variant._APP.base
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    try:
        gui.root.withdraw()
        gui._qubitz_refresh_variant_cache()
        assert started.wait(2)
        for _ in range(5):
            gui._qubitz_refresh_variant_cache()
        heartbeat = []
        gui.root.after_idle(lambda: heartbeat.append(threading.get_ident()))
        gui.root.update()
        assert heartbeat == [main_thread] and len(calls) == 1
        assert gui._qubitz_variant_cache_refresh_pending
        assert all(str(gui._qubitz_variant_menu.entrycget(index, "state")) == "normal"
                   for index in menu_entries(gui._qubitz_variant_menu).values())
        release.set()
        wait_for_cache_scan(gui)
        assert len(calls) == 2 and all(identifier != main_thread for identifier in calls)
        assert all(value is True for value in gui._qubitz_variant_cache_state.values())
    finally:
        release.set()
        gui.agent.reset_runtime()
        gui.root.destroy()


def test_failed_cache_scan_clears_stale_white_indicators(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    monkeypatch.setattr(catalog, "cached_model_states", lambda paths, root: dict.fromkeys((p.name for p in paths), True))
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=PROJECT_ROOT))
    try:
        gui.root.withdraw()
        gui._qubitz_refresh_variant_cache()
        wait_for_cache_scan(gui)

        def unavailable(paths, root):
            raise PermissionError("fixture scan denied")

        monkeypatch.setattr(catalog, "cached_model_states", unavailable)
        gui._qubitz_refresh_variant_cache()
        wait_for_cache_scan(gui)
        assert all(value is None for value in gui._qubitz_variant_cache_state.values())
        assert "PermissionError" in gui._qubitz_variant_cache_error
        assert all(str(gui._qubitz_variant_menu.entrycget(index, "foreground")) == "#9aa0a6"
                   for index in menu_entries(gui._qubitz_variant_menu).values())
    finally:
        gui.agent.reset_runtime()
        gui.root.destroy()


@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_real_selector_preserves_cleanup_before_new_entrypoint(variant, tmp_path, monkeypatch, cleanup_failure):
    base = variant._APP.base
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gui = base.QubitzGUI(base.AgentConfig(workspace=workspace, runtime_workspace=PROJECT_ROOT))
    trace = []

    def cleanup():
        trace.append("cleanup")
        if cleanup_failure:
            raise RuntimeError("fixture cleanup refusal")

    def execute(executable, command, environment):
        trace.append((executable, command, environment))
        raise OSError("fixture exec boundary")

    monkeypatch.setattr(gui.agent, "reset_runtime", cleanup)
    monkeypatch.setattr(variant.os, "chdir", lambda path: trace.append(("cwd", path)))
    monkeypatch.setattr(variant.os, "execve", execute)
    try:
        gui.root.withdraw()
        current = Path(variant.__file__).name
        target = next(path for path in variant_paths() if path.name != current)
        gui._qubitz_select_variant(target.name)
        gui.root.update()
        assert trace[0] == "cleanup"
        if cleanup_failure:
            assert trace == ["cleanup"]
        else:
            assert trace[1] == ("cwd", PROJECT_ROOT)
            assert trace[2][0] == sys.executable
            assert trace[2][1] == [sys.executable, str(target), "--workspace", str(workspace)]
        assert gui.variant_selector_var.get() == current
        assert not gui._qubitz_variant_switch_in_progress
    finally:
        gui.root.destroy()


def test_application_runtime_root_and_entrypoint_identity_remain_distinct(variant):
    assert variant._APP.runtime_workspace == PROJECT_ROOT
    assert variant._APP.wrapper_script.parent == PROJECT_ROOT / "variants"
    assert Path(variant.__file__) == variant._APP.wrapper_script
    assert all(name not in vars(variant) for name in ("_qubitz_bootstrap_sys", "_QubitzBootstrapPath", "_qubitz_bootstrap_root"))
