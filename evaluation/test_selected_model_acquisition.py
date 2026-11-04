"""All-variant model-file startup, with fake local weights and no network/GPU."""
from __future__ import annotations

import ast
import json
import re
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime import catalog, read_variant_source
from runtime.source import embedded_source
from runtime.variant_policy import user_variant_allowed

variant = receipts.variant
ENABLE_SELECTED_MODEL_ACQUISITION = True
ROOT = Path(__file__).resolve().parents[1]
HEADER = b"GGUF" + b"\x00" * 28


@pytest.fixture(autouse=True)
def runtime_harness(variant, tmp_path, monkeypatch):
    (tmp_path / "HARNESS.enc").write_bytes((ROOT / "HARNESS.enc").read_bytes())
    monkeypatch.setenv("QUBITZ_HARNESS_KEY", variant._APP.base.harness_key_bytes(ROOT).decode("ascii"))


def weight(path, data=HEADER):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def online(monkeypatch):
    for name in ("QUBITZ_OFFLINE", "HF_HUB_OFFLINE", "QUBITZ_NETWORK_MODE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_base(variant, tmp_path, monkeypatch, online):
    path = tmp_path / "models/model.gguf"
    state = {"present": False, "calls": 0, "preflights": 0}

    def preflight(*_args):
        state["preflights"] += 1
        if state.get("insufficient_space"):
            raise RuntimeError("fixture insufficient_space")
        return {"status": "ready"}

    monkeypatch.setattr(variant, "_model_storage_preflight", preflight)

    def resolve(*_args):
        return path if state["present"] else None

    def download(_root, _filename):
        state["calls"] += 1
        if "entered" in state:
            state["entered"].set()
            assert state["release"].wait(5)
        state["present"] = True
        return weight(path, b"invalid" if state.get("invalid_download") else HEADER)

    base = SimpleNamespace(DEFAULT_GGUF_MODEL_FILENAME="model.gguf", GGUF_MODEL_PATH_ENV_NAME="FIXTURE_MODEL_PATH",
                           DEFAULT_HF_GGUF_REPO_ID="fixture/repo",
                           resolve_gguf_model_path=resolve, download_default_gguf_model=download,
                           QubitzGUI=type("FixtureGUI", (), {}))
    monkeypatch.delenv("FIXTURE_MODEL_PATH", raising=False)
    variant._install_selected_model_acquisition(SimpleNamespace(base=base))
    return base, state, path


def test_acquisition_reuses_a_completed_download(variant, fake_base, tmp_path):
    base, state, path = fake_base
    assert base.download_default_gguf_model(tmp_path) == path
    assert base.download_default_gguf_model(tmp_path) == path
    assert state["calls"] == state["preflights"] == 1
    variant._install_model_storage_preflight(base)
    assert base.download_default_gguf_model(tmp_path) == path
    assert state["calls"] == state["preflights"] == 1


def test_storage_preflight_blocks_transfer_before_any_bytes(variant, fake_base, tmp_path):
    base, state, _path = fake_base
    state["insufficient_space"] = True
    with pytest.raises(RuntimeError, match="insufficient_space"):
        base.download_default_gguf_model(tmp_path)
    assert state["preflights"] == 1 and state["calls"] == 0


def test_concurrent_startup_and_request_acquire_once(variant, fake_base, tmp_path):
    base, state, path = fake_base
    state.update(entered=threading.Event(), release=threading.Event())
    barrier = threading.Barrier(3)
    results = []
    errors = []

    def acquire():
        try:
            barrier.wait(5)
            results.append(base.download_default_gguf_model(tmp_path))
        except BaseException as exc:  # noqa: BLE001 - Propagate test-worker failures, including pytest.fail, to the main thread.
            errors.append(exc)

    workers = [threading.Thread(target=acquire) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(5)
    try:
        assert state["entered"].wait(2)
        assert not state["release"].wait(0.1)
        assert state["calls"] == 1 and all(worker.is_alive() for worker in workers)
    finally:
        state["release"].set()
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert errors == [] and results == [path, path] and state["calls"] == 1


def test_incomplete_download_is_reported_not_used_for_inference(variant, fake_base, tmp_path):
    base, state, _path = fake_base
    state["invalid_download"] = True
    with pytest.raises(RuntimeError, match="did not produce the complete"):
        base.download_default_gguf_model(tmp_path)
    assert state["calls"] == 1


def test_cached_file_can_be_reused_offline(variant, fake_base, tmp_path, monkeypatch):
    base, state, path = fake_base
    weight(path)
    state["present"] = True
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    assert base.download_default_gguf_model(tmp_path) == path and state["calls"] == 0


@pytest.mark.parametrize("environment,value", [("QUBITZ_OFFLINE", "1"), ("HF_HUB_OFFLINE", "1"),
                                              ("QUBITZ_NETWORK_MODE", "offline")])
def test_missing_offline_model_never_downloads(variant, fake_base, tmp_path, monkeypatch, environment, value):
    base, state, _path = fake_base
    monkeypatch.setenv(environment, value)
    with pytest.raises(RuntimeError, match="disabled offline"):
        base.download_default_gguf_model(tmp_path)
    assert state["calls"] == 0


@pytest.mark.parametrize("body", [b"", b"GGUF", b"BAD!" + b"\0" * 28])
def test_invalid_default_file_set_is_missing_but_explicit_path_is_refused(variant, fake_base, tmp_path, body):
    base, state, path = fake_base
    weight(path, body)
    state["present"] = True
    assert base.resolve_gguf_model_path(tmp_path) is None
    with pytest.raises(ValueError, match="Configured GGUF"):
        base.resolve_gguf_model_path(tmp_path, str(path))
    assert state["calls"] == 0


def test_missing_shard_is_not_a_cached_complete_model(variant, tmp_path):
    first = weight(tmp_path / "model-00001-of-00002.gguf")
    assert not variant._qubitz_model_files_present(first)
    weight(tmp_path / "model-00002-of-00002.gguf")
    assert variant._qubitz_model_files_present(first)


def test_actual_resolver_never_walks_project_or_excluded_trees(variant, tmp_path, monkeypatch):
    text = embedded_source(read_variant_source(Path(variant.__file__)))[0]
    definition = next(node for node in ast.parse(text).body
                      if isinstance(node, ast.FunctionDef) and node.name == "resolve_gguf_model_path")
    scope = dict(vars(variant._APP.base))
    exec(compile(ast.Module(body=[definition], type_ignores=[]), "<actual-resolver>", "exec"), scope)  # noqa: S102 - Trusted actual source, no injected fake resolver.
    visited = []
    real_walk = scope["os"].walk
    name = "missing-fixture.gguf"
    weight(tmp_path / ".venv/large/unrelated.bin")
    (tmp_path / "models/.venv/large").mkdir(parents=True)
    (tmp_path / ".cache/models/owner/repo/.cache/huggingface").mkdir(parents=True)

    def walk(root, **kwargs):
        assert root in (tmp_path / "models", tmp_path / ".cache/models")
        for directory, children, files in real_walk(root, **kwargs):
            visited.append(Path(directory))
            yield directory, children, files

    monkeypatch.delenv(scope["GGUF_MODEL_PATH_ENV_NAME"], raising=False)
    monkeypatch.setattr(scope["os"], "walk", walk)
    assert scope["resolve_gguf_model_path"](tmp_path, None, name) is None
    assert visited and not any(".venv" in path.parts or "huggingface" in path.parts for path in visited)
    nested = weight(tmp_path / "models/nested" / name)
    assert scope["resolve_gguf_model_path"](tmp_path, None, name) == nested
    explicit = weight(tmp_path / "chosen/non-model-directory" / name)
    assert scope["resolve_gguf_model_path"](tmp_path, str(explicit), name) == explicit


def test_effective_installed_resolver_prunes_model_roots(variant, tmp_path, monkeypatch):
    base = variant._APP.base
    name = base.DEFAULT_GGUF_MODEL_FILENAME
    (tmp_path / "models/.venv/large").mkdir(parents=True)
    (tmp_path / ".cache/models/repo/.cache/huggingface").mkdir(parents=True)
    visited = []
    real_walk = base.os.walk

    def walk(root, **kwargs):
        assert root in (tmp_path / "models", tmp_path / ".cache/models")
        for directory, children, files in real_walk(root, **kwargs):
            visited.append(Path(directory))
            yield directory, children, files

    def unbounded(*_args, **_kwargs):
        pytest.fail("Installed model resolver attempted an unpruned recursive glob")

    monkeypatch.delenv(base.GGUF_MODEL_PATH_ENV_NAME, raising=False)
    monkeypatch.setattr(base.os, "walk", walk)
    monkeypatch.setattr(Path, "rglob", unbounded)
    assert base.resolve_gguf_model_path(tmp_path, None, name) is None
    assert visited and not any(".venv" in path.parts or "huggingface" in path.parts for path in visited)
    cached = weight(tmp_path / "models/nested" / name)
    split = re.fullmatch(r"(.+)-\d{5}-of-(\d{5})\.gguf", name)
    if split:
        for number in range(1, int(split[2]) + 1):
            weight(cached.with_name(f"{split[1]}-{number:05}-of-{int(split[2]):05}.gguf"))
    assert base.resolve_gguf_model_path(tmp_path, None, name) == cached


@pytest.fixture
def gui_factory(variant, tmp_path, monkeypatch, online):
    base = variant._APP.base
    instances = []
    monkeypatch.setattr(catalog, "cached_model_states", lambda paths, _root: dict.fromkeys((p.name for p in paths), False))
    monkeypatch.setattr(base.AgentRunner, "_get_llm", lambda *_: pytest.fail("Acquisition attempted inference"))

    def build(resolve, download, *, configured=None):
        monkeypatch.setattr(base, "resolve_gguf_model_path", resolve)
        monkeypatch.setattr(base, "download_default_gguf_model", download)
        config = base.AgentConfig(workspace=tmp_path, runtime_workspace=tmp_path)
        config.model_path = configured
        gui = base.QubitzGUI(config)
        instances.append(gui)
        assert gui._qubitz_model_acquisition_state["state"] == "pending"
        gui.root.update()
        gui.root.withdraw()
        return gui

    yield build
    for gui in instances:
        worker = gui._qubitz_model_acquisition_thread
        if not gui._qubitz_acquisition_closed.is_set():
            gui.agent.reset_runtime()
            gui.root.destroy()
        if worker is not None:
            worker.join(5)
            assert not worker.is_alive()


def finish(gui):
    deadline = time.monotonic() + 5
    while gui._qubitz_model_acquisition_thread.is_alive() and time.monotonic() < deadline:
        gui.root.update()
        time.sleep(0.01)
    assert not gui._qubitz_model_acquisition_thread.is_alive()
    return gui._qubitz_model_acquisition_state


def test_gui_startup_finds_cached_files_without_loading_model(variant, gui_factory, tmp_path):
    cached = weight(tmp_path / "cached.gguf")
    gui = gui_factory(lambda *_: cached, lambda *_: pytest.fail("Cached model downloaded"))
    assert finish(gui)["state"] == "cached"
    assert gui.agent._llm is None


def test_gui_download_starts_before_prompt_without_blocking_tk(variant, gui_factory, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def download(*_args):
        entered.set()
        assert release.wait(5)
        return weight(tmp_path / "downloaded.gguf")

    gui = gui_factory(lambda *_: None, download)
    try:
        assert entered.wait(2)
        assert gui._qubitz_model_acquisition_state["state"] == "downloading"
        heartbeat = []
        gui.root.after_idle(lambda: heartbeat.append(True))
        gui.root.update()
        assert heartbeat == [True] and not gui.busy and gui.agent._llm is None
    finally:
        release.set()
    assert finish(gui)["state"] == "downloaded"


def test_gui_offline_and_error_states_are_visible(variant, gui_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("QUBITZ_OFFLINE", "1")
    gui = gui_factory(lambda *_: None, lambda *_: pytest.fail("Offline download"))
    assert finish(gui)["state"] == "offline"


def test_gui_invalid_explicit_path_does_not_download_a_substitute(variant, gui_factory, tmp_path):
    def invalid(*_args):
        raise FileNotFoundError("fixture explicit path is missing")

    gui = gui_factory(invalid, lambda *_: pytest.fail("Invalid explicit path downloaded a substitute"),
                      configured=str(tmp_path / "missing.gguf"))
    assert finish(gui)["state"] == "error"


def test_gui_download_failure_is_reported_without_inference(variant, gui_factory):
    def unavailable(*_args):
        raise OSError("fixture download unavailable")

    gui = gui_factory(lambda *_: None, unavailable)
    state = finish(gui)
    assert state["state"] == "error" and state["error"] == "fixture download unavailable"
    assert gui.agent._llm is None


def test_gui_close_discards_late_download_status(variant, gui_factory, tmp_path):
    entered, release = threading.Event(), threading.Event()

    def download(*_args):
        entered.set()
        assert release.wait(5)
        return weight(tmp_path / "closed.gguf")

    gui = gui_factory(lambda *_: None, download)
    try:
        assert entered.wait(2)
        gui.agent.reset_runtime()
        gui.root.destroy()
        before = gui._qubitz_model_acquisition_state.copy()
    finally:
        release.set()
    gui._qubitz_model_acquisition_thread.join(5)
    assert not gui._qubitz_model_acquisition_thread.is_alive()
    assert gui._qubitz_acquisition_closed.is_set() and gui._qubitz_model_acquisition_state == before


def test_gui_destroyed_before_idle_starts_no_acquisition(variant, tmp_path, monkeypatch, online):
    base = variant._APP.base
    monkeypatch.setattr(base, "resolve_gguf_model_path", lambda *_: pytest.fail("Destroyed GUI started acquisition"))
    monkeypatch.setattr(catalog, "cached_model_states", lambda paths, _root: dict.fromkeys((p.name for p in paths), False))
    gui = base.QubitzGUI(base.AgentConfig(workspace=tmp_path, runtime_workspace=tmp_path))
    gui.agent.reset_runtime()
    gui.root.destroy()
    assert gui._qubitz_acquisition_closed.is_set() and gui._qubitz_model_acquisition_thread is None


def test_physical_profiles_and_registered_module_cover_all_variants(variant):
    rows = [row for row in json.loads((ROOT / "qubitz_variant_profiles.json").read_text())["variants"]
            if user_variant_allowed(row["filename"])]
    assert len(rows) == 40
    source = read_variant_source(Path(variant.__file__))
    assert source.count((ROOT / "runtime/model_acquisition.py").read_text()) == 1
