"""Cache indicators avoid redundant work without weakening file containment."""
from pathlib import Path

import pytest

from evaluation.test_variant_layout import fixture_model, weight
from runtime import catalog


def test_missing_regular_file_does_not_resolve_its_full_path(tmp_path, monkeypatch):
    original = Path.resolve
    original_lstat = Path.lstat
    calls = []
    metadata_calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)

    def lstat(path):
        metadata_calls.append(path)
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert not catalog._complete(tmp_path, ("missing.gguf",), tmp_path)
    assert not calls
    assert metadata_calls == [tmp_path / "missing.gguf"]


def test_common_directories_are_resolved_once_per_scan_and_not_cached_globally(tmp_path, monkeypatch):
    paths = [tmp_path / f"variant_{index}.py" for index in range(3)]
    metadata = catalog.ModelFiles("owner/repo", "Model.gguf", ("Model.gguf",))
    monkeypatch.setattr(catalog, "model_files", lambda _: metadata)
    original = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    states = catalog.cached_model_states(paths, tmp_path)
    assert set(states.values()) == {False}
    assert calls.count(tmp_path / "models") == 0
    assert calls.count(tmp_path / ".cache/models/owner/repo") == 0
    weight(tmp_path / "models" / "Model.gguf")
    assert set(catalog.cached_model_states(paths, tmp_path).values()) == {True}


@pytest.mark.parametrize("kind,expected", [("outside", False), ("dangling", False), ("cycle", None)])
def test_model_file_links_still_require_fresh_containment(tmp_path, kind, expected):
    root = tmp_path / "project"
    root.mkdir()
    launcher = fixture_model(root, "Model.gguf")
    models = root / "models"
    models.mkdir()
    link = models / "Model.gguf"
    outside = tmp_path / "outside.gguf"
    if kind == "outside":
        weight(outside)
    target = link if kind == "cycle" else outside
    link.symlink_to(target)
    assert catalog.cached_model_state(launcher, root) is expected


def test_cached_directory_check_never_authorizes_an_escaped_file_read(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    launcher = fixture_model(root, "Model.gguf")
    inside = root / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    weight(outside / "Model.gguf")
    models = root / "models"
    models.symlink_to(inside, target_is_directory=True)
    complete = catalog._complete
    opened = Path.open
    switched = False

    def move_link(directory, *args, **kwargs):
        nonlocal switched
        if directory == models and not switched:
            switched = True
            models.unlink()
            models.symlink_to(outside, target_is_directory=True)
        return complete(directory, *args, **kwargs)

    def open_checked(path, *args, **kwargs):
        assert path.resolve().is_relative_to(root), "Attempted content read outside model root"
        return opened(path, *args, **kwargs)

    monkeypatch.setattr(catalog, "_complete", move_link)
    monkeypatch.setattr(Path, "open", open_checked)
    assert catalog.cached_model_state(launcher, root) is not True
    assert switched


@pytest.mark.parametrize("kind", ["outside", "cycle"])
def test_missing_repository_still_checks_aliased_ancestor(tmp_path, kind):
    root = tmp_path / "project"
    root.mkdir()
    launcher = fixture_model(root, "Model.gguf")
    models = root / ".cache/models"
    models.mkdir(parents=True)
    owner = models / "owner"
    outside = tmp_path / "outside"
    outside.mkdir()
    owner.symlink_to(owner if kind == "cycle" else outside, target_is_directory=True)
    assert not (owner / "repo").exists()
    assert catalog.cached_model_state(launcher, root) is None
