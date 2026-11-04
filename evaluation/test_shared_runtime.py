"""CPU source-composition and namespace isolation contracts for shared runtime."""
from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import sys
import tkinter as tk
from pathlib import Path

import pytest

from runtime import development, loader, source
from runtime.development import launcher_text, split_sections
from runtime.paths import variant_path


def test_cpu_root_teardown_does_not_leak_tcl_callbacks():
    root = tk.Tk()
    root.withdraw()
    token = root.after(60000, lambda: None)
    assert token in root.tk.splitlist(root.tk.call("after", "info"))
    root.destroy()
    assert not root.tk.splitlist(root.tk.call("after", "info"))


def templates():
    return {"base": "# <QUBITZ_VARIANT_SECTION:base:000>\n",
            "wrapper": "from __future__ import annotations\n"
                       "# <QUBITZ_VARIANT_SECTION:wrapper:000>\n"
                       "_EMBEDDED_BASE_SOURCE = __QUBITZ_EMBEDDED_SOURCE_SLOT__\n"
                       "def identify():\n    return __file__, MODEL\n"}


def specification(name="example.py", model="one"):
    return {"schema_version": 2, "filename": name, "base": {"000": "value = 3\n"},
            "wrapper": {"000": f"MODEL = {model!r}\n"}}


def test_empty_and_inserted_sections_have_identical_topology():
    reference = ["one\n", "two\n"]
    other = ["one\n", "specific\n", "two\n"]
    first, a = split_sections(reference, [(0, 1), (1, 2)], "base")
    second, b = split_sections(other, [(0, 1), (2, 3)], "base")
    assert first == second
    assert a == {"000": "", "001": "", "002": ""}
    assert b == {"000": "", "001": "specific\n", "002": ""}
    assert source.compose_layer("base", a, template=first, symbols={}) == "".join(reference)
    assert source.compose_layer("base", b, template=second, symbols={}) == "".join(other)


def test_launcher_source_is_literal_and_effective_source_is_exact(tmp_path, monkeypatch):
    spec = specification()
    for layer, text in templates().items():
        (tmp_path / f"{layer}.py.in").write_text(text)
        (tmp_path / f"{layer}.shared.py.in").write_text("")
    monkeypatch.setattr(source, "RUNTIME_DIRECTORY", tmp_path)
    path = tmp_path / "example.py"
    path.write_text(launcher_text(spec))
    assert source.launcher_spec(path.read_text()) == spec
    effective = source.read_variant_source(path)
    assert "MODEL = 'one'" in effective and "value = 3\\n" in effective
    ast.parse(effective)
    assert "_initialize_variant" not in effective


@pytest.mark.parametrize("corruption", ["filename", "version", "legacy", "missing", "extra", "invalid_section"])
def test_invalid_spec_is_rejected_without_execution(corruption):
    spec = specification()
    if corruption == "filename":
        spec["filename"] = "foreign.py"
    elif corruption == "version":
        spec["schema_version"] = True
    elif corruption == "legacy":
        spec["schema_version"] = 1
    elif corruption == "missing":
        spec["wrapper"] = {}
    elif corruption == "extra":
        spec["unexpected"] = "value"
    else:
        spec["wrapper"]["000"] = 5
    with pytest.raises(ValueError):
        source.compose_variant(spec, "example.py", templates=templates(), symbols={"base": {}, "wrapper": {}})


def test_each_instance_keeps_its_namespace_and_launcher_identity(tmp_path, monkeypatch):
    for layer, text in templates().items():
        (tmp_path / f"{layer}.py.in").write_text(text)
        (tmp_path / f"{layer}.shared.py.in").write_text("")
    monkeypatch.setattr(source, "RUNTIME_DIRECTORY", tmp_path)
    first = {"__file__": str(tmp_path / "first.py"), "__name__": "first"}
    second = {"__file__": str(tmp_path / "second.py"), "__name__": "second"}
    loader.initialize_variant(first, specification("first.py", "one"))
    loader.initialize_variant(second, specification("second.py", "two"))
    first["MODEL"] = "changed"
    assert first["identify"]() == (str(tmp_path / "first.py"), "changed")
    assert second["identify"]() == (str(tmp_path / "second.py"), "two")
    assert first["identify"].__globals__ is first
    assert second["identify"].__globals__ is second
    assert inspect.getsource(first["identify"]).strip() == "def identify():\n    return __file__, MODEL"


def test_ordinary_source_and_marker_in_a_string_remain_ordinary(tmp_path):
    path = tmp_path / "ordinary.py"
    text = "message = '_QUBITZ_VARIANT_SPEC'\n"
    path.write_text(text)
    assert source.read_variant_source(path) == text


def test_embedded_source_span_handles_unicode_before_literal():
    text = "label = 'é'; _EMBEDDED_BASE_SOURCE = 'value = 3\\n'\n"
    base, (start, end) = source.embedded_source(text)
    assert base == "value = 3\n"
    assert ast.literal_eval(text[start:end]) == base


def test_dependency_snapshot_includes_templates_but_not_bytecode(tmp_path):
    directory = tmp_path / "runtime"
    directory.mkdir()
    (directory / "source.py").write_text("value = 1\n")
    (directory / "base.py.in").write_text("value = 2\n")
    (directory / "__pycache__").mkdir()
    (directory / "__pycache__/source.pyc").write_bytes(b"bytecode")
    assert {path.name for path in source.runtime_dependency_paths(tmp_path)} == {"source.py", "base.py.in"}


def test_extra_launcher_execution_cannot_bypass_static_validation(tmp_path, monkeypatch):
    path = tmp_path / "example.py"
    path.write_text(launcher_text(specification()) + "raise RuntimeError('unexpected execution')\n")
    with pytest.raises(ValueError, match="Unexpected executable code"):
        source.read_variant_source(path)


def test_variant_edit_and_standalone_expansion_are_lossless(tmp_path, monkeypatch):
    for layer, text in templates().items():
        (tmp_path / f"{layer}.py.in").write_text(text)
        (tmp_path / f"{layer}.shared.py.in").write_text("")
    monkeypatch.setattr(source, "RUNTIME_DIRECTORY", tmp_path)
    monkeypatch.setattr(development, "ROOT", tmp_path)
    path = tmp_path / "example.py"
    path.write_text(launcher_text(specification()))
    before = source.read_variant_source(path)
    after = before.replace("MODEL = 'one'", "MODEL = 'two'")
    assert development.replace_variant_source(path, after)
    assert source.read_variant_source(path) == after
    assert len(list((tmp_path / "backup").glob("*.bak"))) == 1
    assert not development.replace_variant_source(path, after)
    with pytest.raises(ValueError, match="shared source interval changed"):
        development.replace_variant_source(path, after.replace("return __file__, MODEL", "return MODEL"))


def test_development_backup_uses_project_root_and_lowest_unused_number(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    target = project / "scripts/example.py"
    target.write_bytes(b"value = 1\n")
    (project / "backup").mkdir()
    original = project / "backup/scripts__example.py.001.bak"
    original.write_bytes(b"earlier source\n")
    dangling = project / "backup/scripts__example.py.002.bak"
    dangling.symlink_to(project / "missing.py")
    monkeypatch.setattr(development, "ROOT", project)
    monkeypatch.chdir(tmp_path)
    saved = development.backup(Path("scripts/example.py"), "Keep the project-local source unchanged.")
    assert saved == project / "backup/scripts__example.py.003.bak"
    assert saved.read_bytes() == target.read_bytes() == b"value = 1\n"
    assert original.read_bytes() == b"earlier source\n"
    assert dangling.is_symlink()
    assert (project / "changelog.txt").read_text() == (
        "backup/scripts__example.py.003.bak | scripts/example.py | Keep the project-local source unchanged.\n"
    )
    assert not (tmp_path / "backup").exists()


def test_development_edit_resolves_relative_path_from_project_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    for layer, text in templates().items():
        (project / f"{layer}.py.in").write_text(text)
        (project / f"{layer}.shared.py.in").write_text("")
    target = project / "example.py"
    target.write_text(launcher_text(specification()))
    monkeypatch.setattr(source, "RUNTIME_DIRECTORY", project)
    monkeypatch.setattr(development, "ROOT", project)
    monkeypatch.chdir(tmp_path)
    before = source.read_variant_source(target)
    candidate = before.replace("MODEL = 'one'", "MODEL = 'two'")
    assert development.replace_variant_source(Path("example.py"), candidate)
    assert source.read_variant_source(target) == candidate
    assert (project / "backup/example.py.001.bak").read_text() == launcher_text(specification())


@pytest.mark.parametrize("operation", ["backup", "edit"])
@pytest.mark.parametrize("path_kind", ["absolute", "parent", "symlink"])
def test_development_rejects_escaped_sources_without_side_effects(tmp_path, monkeypatch, operation, path_kind):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"untouched outside source\n")
    before = outside.stat()
    monkeypatch.setattr(development, "ROOT", project)
    if path_kind == "symlink":
        path = project / "escaped.py"
        path.symlink_to(outside)
    else:
        path = outside if path_kind == "absolute" else project / "../outside.py"
    with pytest.raises(ValueError, match="inside the Qubitz project root"):
        if operation == "backup":
            development.backup(path, "Do not change an external source.")
        else:
            development.replace_variant_source(path, "value = 2\n")
    assert outside.read_bytes() == b"untouched outside source\n"
    assert outside.stat().st_mtime_ns == before.st_mtime_ns
    assert not (project / "backup").exists()
    assert not (project / "changelog.txt").exists()


@pytest.mark.parametrize("destination", ["backup", "changelog.txt"])
def test_development_rejects_escaped_backup_destinations_before_writing(tmp_path, monkeypatch, destination):
    project = tmp_path / "project"
    project.mkdir()
    target = project / "example.py"
    target.write_bytes(b"unchanged source\n")
    outside = tmp_path / "outside"
    if destination == "backup":
        outside.mkdir()
    else:
        outside.write_bytes(b"unchanged external history\n")
    (project / destination).symlink_to(outside, target_is_directory=destination == "backup")
    monkeypatch.setattr(development, "ROOT", project)
    with pytest.raises(ValueError, match="inside the Qubitz project root"):
        development.backup(target, "Do not write external backup data.")
    assert target.read_bytes() == b"unchanged source\n"
    if destination == "backup":
        assert not list(outside.iterdir())
        assert not (project / "changelog.txt").exists()
    else:
        assert outside.read_bytes() == b"unchanged external history\n"
        assert not (project / "backup").exists()


def test_shared_template_changes_are_in_matrix_freeze(tmp_path, monkeypatch):
    from evaluation import disposable_followup_matrix as matrix

    directory = tmp_path / "runtime"
    directory.mkdir()
    target = directory / "wrapper.py.in"
    target.write_text("value = 1\n")
    monkeypatch.setattr(matrix, "ROOT", tmp_path)
    before = matrix.freeze_sources()
    target.write_text("value = 2\n")
    after = matrix.freeze_sources()
    assert before.keys() == after.keys()
    assert before["runtime/wrapper.py.in"] != after["runtime/wrapper.py.in"]


def test_every_registered_variant_has_complete_effective_source():
    from runtime.variant_policy import user_variant_allowed

    root = Path(__file__).resolve().parents[1]
    profiles = [row for row in json.loads((root / "qubitz_variant_profiles.json").read_text())["variants"]
                if user_variant_allowed(row["filename"])]
    assert len(profiles) == 40
    for profile in profiles:
        path = variant_path(profile["filename"], root)
        assert source.launcher_spec(path.read_text())["filename"] == path.name
        current = source.read_variant_source(path)
        compile(current, str(path), "exec")
        base, _span = source.embedded_source(current)
        compile(base, str(path), "exec")
        assert source.BASE_MARKER not in current


def test_real_variant_modules_do_not_share_classes_or_context(tmp_path):
    root = Path(__file__).resolve().parents[1]
    modules = []
    try:
        for index, filename in enumerate(("AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py",
                                         "AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py")):
            spec = importlib.util.spec_from_file_location(f"shared_isolation_{index}", variant_path(filename, root))
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            modules.append(module)
        first, second = modules
        assert first._APP is not second._APP
        assert first._APP.base is not second._APP.base
        assert first._APP.base.AgentRunner is not second._APP.base.AgentRunner
        assert first._GPU_THERMAL_GATE_CONTEXT is not second._GPU_THERMAL_GATE_CONTEXT
        assert first._APP.base.DEFAULT_NUM_CTX == 101376
        assert second._APP.base.DEFAULT_NUM_CTX == 262144
        assert Path(first.__file__).name == "AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py"
        assert "_QUBITZ_VARIANT_SPEC" not in vars(first)
        assert first.__doc__ is None
    finally:
        for module in modules:
            sys.modules.pop(module.__name__, None)
            sys.modules.pop(module._APP.base.__name__, None)


def test_real_layout_accepts_only_variant_configuration_edits(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    original = variant_path("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py", root)
    target = tmp_path / original.name
    target.write_text(original.read_text())
    monkeypatch.setattr(development, "ROOT", tmp_path)
    before = source.read_variant_source(target)
    base, (start, end) = source.embedded_source(before)
    updated = base.replace("DEFAULT_NUM_PREDICT = 16384", "DEFAULT_NUM_PREDICT = 12000")
    assert updated != base
    candidate = before[:start] + repr(updated) + before[end:]
    assert development.replace_variant_source(target, candidate)
    assert source.read_variant_source(target) == candidate
    assert source.launcher_spec(target.read_text())["schema_version"] == 2
