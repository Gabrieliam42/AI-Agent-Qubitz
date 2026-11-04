"""Model-free launch isolation; never construct an agent, GUI or model server."""
from __future__ import annotations

import builtins
import runpy
import sys

import pytest

from runtime import browser_entrypoint, launcher, loader
from runtime.paths import DEFAULT_VARIANT, PROJECT_ROOT
from runtime.variant_policy import DEVELOPER_ONLY_VARIANTS

SOURCE = '''from types import SimpleNamespace
calls = []
def main(argv=None):
    calls.append(("original", argv))
_APP = SimpleNamespace(main=main)
if __name__ == "__main__":
    main()
'''


@pytest.mark.parametrize("name", ["__main__", "imported_variant"])
def test_normal_variant_keeps_original_main_without_web_imports(tmp_path, monkeypatch, name):
    monkeypatch.setattr(loader, "compose_variant", lambda *_args: SOURCE)
    original_import = builtins.__import__

    def guarded_import(module, *args, **kwargs):
        assert module not in {"browser_entrypoint", "runtime.browser_entrypoint", "fastapi", "web_api"}
        return original_import(module, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    namespace = {"__file__": str(tmp_path / DEFAULT_VARIANT), "__name__": name}
    loader.initialize_variant(namespace, {})
    assert namespace["main"] is namespace["_APP"].main
    assert namespace["calls"] == ([("original", None)] if name == "__main__" else [])


@pytest.mark.parametrize("flag", [True, False, "true", 1])
def test_web_hook_requires_an_explicit_boolean_and_does_not_leak(tmp_path, monkeypatch, flag):
    monkeypatch.setattr(loader, "compose_variant", lambda *_args: SOURCE)
    hooks = []

    def install(namespace):
        hooks.append(namespace["__file__"])

        def web_main():
            namespace["calls"].append(("web", None))

        namespace["main"] = namespace["_APP"].main = web_main

    monkeypatch.setattr(browser_entrypoint, "install_browser_entrypoint", install)
    namespace = {"__file__": str(tmp_path / DEFAULT_VARIANT), "__name__": "__main__",
                 loader.DEVELOPER_WEB_NAMESPACE_FLAG: flag}
    loader.initialize_variant(namespace, {})
    assert namespace["calls"] == [("web" if flag is True else "original", None)]
    assert loader.DEVELOPER_WEB_NAMESPACE_FLAG not in namespace
    following = {"__file__": str(tmp_path / DEFAULT_VARIANT), "__name__": "__main__"}
    loader.initialize_variant(following, {})
    assert following["calls"] == [("original", None)]
    assert len(hooks) == (1 if flag is True else 0)


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    directory = tmp_path / "variants"
    directory.mkdir()
    target = directory / DEFAULT_VARIANT
    target.write_text("fixture = True\n")
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launcher, "launcher_spec", lambda _text: {"filename": DEFAULT_VARIANT})
    monkeypatch.setattr(launcher, "compose_variant", lambda *_args: SOURCE)
    calls = []
    monkeypatch.setattr(launcher.runpy, "run_path", lambda path, **kwargs: calls.append((path, kwargs, list(sys.argv))))
    return tmp_path, target, calls


@pytest.mark.parametrize("web", [False, True])
def test_root_dispatch_keeps_normal_identity_and_argv_without_global_mode_changes(dispatch, web):
    root, target, calls = dispatch
    previous = sys.argv
    assert launcher.main(["--cli", "--prompt", "fixture"], developer_web=web) == 0
    options = {"run_name": "__main__"}
    if web:
        options["init_globals"] = {loader.DEVELOPER_WEB_NAMESPACE_FLAG: True}
    assert calls == [(str(target), options, [str(target), "--cli", "--prompt", "fixture", "--workspace", str(root)])]
    assert sys.argv is previous


@pytest.mark.parametrize("name", sorted(DEVELOPER_ONLY_VARIANTS))
def test_web_launcher_rejects_excluded_variants_before_reading_or_importing_them(monkeypatch, name):
    monkeypatch.setattr(launcher, "variant_path", lambda *_args: pytest.fail("Excluded variant path was inspected"))
    with pytest.raises(SystemExit) as outcome:
        launcher.main(["--variant", name], developer_web=True)
    assert outcome.value.code == 2


@pytest.mark.parametrize("filename,expected", [("AI Agent Qubitz.py", False), ("AI Agent Qubitz Web Dev.py", True)])
def test_root_scripts_choose_their_own_interface(monkeypatch, filename, expected):
    calls = []

    def main(argv=None, *, developer_web=False):
        calls.append((argv, developer_web))
        return 0

    monkeypatch.setattr(launcher, "main", main)
    with pytest.raises(SystemExit) as outcome:
        runpy.run_path(str(PROJECT_ROOT / filename), run_name="__main__")
    assert outcome.value.code == 0 and calls == [(None, expected)]


def test_developer_launch_failure_restores_argv_and_does_not_affect_next_launch(dispatch, monkeypatch):
    _root, _target, calls = dispatch
    previous = sys.argv

    def fail(*_args, **_kwargs):
        raise RuntimeError("fixture failed web launch")

    monkeypatch.setattr(launcher.runpy, "run_path", fail)
    with pytest.raises(RuntimeError, match="failed web launch"):
        launcher.main([], developer_web=True)
    assert sys.argv is previous
    monkeypatch.setattr(launcher.runpy, "run_path", lambda path, **kwargs: calls.append(kwargs))
    assert launcher.main([]) == 0
    assert calls == [{"run_name": "__main__"}]
