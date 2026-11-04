"""Keep independently imported CPU-contract variants isolated across modules."""
from __future__ import annotations

import gc
import os
import sys
import threading
import tkinter as tk
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tk_collection_pending = False


def collect_destroyed_tk_cycles() -> None:
    global _tk_collection_pending
    if not _tk_collection_pending:
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Test-owned Tk cycles must be collected on the main thread")
    gc.collect()
    _tk_collection_pending = False


@pytest.fixture(autouse=True)
def isolated_tk_callbacks(monkeypatch):
    # Previous fixture references have now been released; don't let a worker
    # reclaim their Tk variables against an interpreter whose loop has stopped.
    collect_destroyed_tk_cycles()
    original = tk.Tk.destroy

    def destroy(root):
        global _tk_collection_pending
        # Tk.destroy removes Python callback commands but leaves Tcl timers
        # queued in this interpreter, contaminating later roots in one process.
        for identifier in root.tk.splitlist(root.tk.call("after", "info")):
            root.tk.call("after", "cancel", identifier)
        original(root)
        _tk_collection_pending = True

    monkeypatch.setattr(tk.Tk, "destroy", destroy)


def private_memory_initializer(original, destination):
    def initialize(store, workspace, *args, **kwargs):
        # Only the shared application runtime root needs redirection; explicit
        # disposable memory workspaces keep their original semantics.
        target = destination if Path(workspace).resolve() == ROOT else workspace
        original(store, target, *args, **kwargs)

    return initialize


def private_conversation_initializer(original, destination):
    def initialize(journal, root, *args, **kwargs):
        target = destination if Path(root).resolve() == ROOT else root
        if target == destination:
            destination.mkdir(parents=True, exist_ok=True)
        original(journal, target, *args, **kwargs)

    return initialize


@pytest.fixture(autouse=True)
def isolated_menu_startup(request, monkeypatch):
    if os.environ.get("QUBITZ_TEST_MENU_INPUT") != "1" or "variant" not in request.fixturenames:
        return
    base = request.getfixturevalue("variant")._APP.base
    monkeypatch.setattr(base.QubitzGUI, "_present_window", lambda _self: None)
    monkeypatch.setattr(base.QubitzGUI, "_schedule_startup_visibility_recovery", lambda _self: None)
    if hasattr(base.QubitzGUI, "_maybe_start_background_model_prewarm"):
        monkeypatch.setattr(base.QubitzGUI, "_maybe_start_background_model_prewarm", lambda _self: None)
    monkeypatch.setattr(base.AgentRunner, "_get_llm", lambda *_args: pytest.fail("Menu test attempted model loading"))


@pytest.fixture(autouse=True)
def isolated_selected_model_acquisition(request, monkeypatch):
    if ("variant" not in request.fixturenames
            or getattr(request.module, "ENABLE_SELECTED_MODEL_ACQUISITION", False)):
        return
    gui = request.getfixturevalue("variant")._APP.base.QubitzGUI
    if hasattr(gui, "_schedule_selected_model_acquisition"):
        monkeypatch.setattr(gui, "_schedule_selected_model_acquisition", lambda _self: None)


@pytest.fixture(autouse=True)
def isolated_runtime_memory(request, monkeypatch, tmp_path):
    from runtime.conversations import ConversationJournal

    if "variant" in request.fixturenames:
        request.getfixturevalue("variant")
    stores = set()
    journals = {ConversationJournal}
    for module in tuple(sys.modules.values()):
        namespace = vars(module) if module is not None else {}
        journal = namespace.get("ConversationJournal")
        if isinstance(journal, type):
            journals.add(journal)
        app = namespace.get("_APP")
        base = getattr(app, "base", None)
        store = getattr(base, "MemoryStore", None)
        if isinstance(store, type) and store not in stores:
            stores.add(store)
            monkeypatch.setattr(
                store, "__init__",
                private_memory_initializer(store.__init__, tmp_path / "runtime_memory"),
            )
    for journal in journals:
        monkeypatch.setattr(
            journal, "__init__",
            private_conversation_initializer(journal.__init__, tmp_path / "runtime_archives"),
        )


def clear_embedded_variants() -> None:
    # Variant aliases share this cache key; retaining it stacks runner wrappers.
    for name in tuple(sys.modules):
        if "__embedded__AI_Agent_Qubitz_" in name:
            sys.modules.pop(name, None)


@pytest.fixture(scope="module", autouse=True)
def isolated_embedded_variants():
    clear_embedded_variants()
    yield
    clear_embedded_variants()
