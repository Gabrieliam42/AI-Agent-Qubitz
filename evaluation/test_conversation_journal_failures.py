"""Explicit retention failures must never look like complete conversation copies."""
from __future__ import annotations

import time
from collections import namedtuple

import pytest

from evaluation import test_followup_state_receipts as receipts
from evaluation.conftest import ROOT, private_conversation_initializer
from runtime import conversations

variant = receipts.variant


def test_reserve_blocks_recording_without_lowering_reserve(tmp_path, monkeypatch):
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(conversations._conversation_shutil, "disk_usage", lambda _path: usage(100, 100, 0))
    journal = conversations.ConversationJournal(tmp_path)
    try:
        deadline = time.monotonic() + 3
        while not journal.error and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "reserve" in journal.error
        assert not journal.record("user", "cannot retain")
        with pytest.raises(RuntimeError, match="incomplete"):
            journal.snapshot()
        assert not journal.path.exists()
    finally:
        journal.close(timeout=3)


def test_snapshot_is_an_exact_complete_barrier_not_a_display_snapshot(tmp_path):
    journal = conversations.ConversationJournal(tmp_path, reserve_bytes=0)
    try:
        assert journal.record("reasoning", "before\U0001f30d")
        first = journal.snapshot()
        assert journal.record("answer", "after\u6f22")
        second = journal.snapshot()
        assert second.startswith(first)
        assert "after\u6f22" not in first and "after\u6f22" in second
    finally:
        journal.close(timeout=3)


def test_reasoning_and_answer_chunks_preserve_text_unicode_and_code(tmp_path):
    journal = conversations.ConversationJournal(tmp_path, reserve_bytes=0)
    thought = "I will check the calculation.\n```python\nassert add(2, 3) == 5\n```\n\U0001f30d \u6f22\u5b57 e\u0301"
    try:
        pieces = [thought[:25], thought[25:-1], thought[-1:]]
        for piece in pieces:
            assert journal.record("reasoning", piece)
        assert journal.record("tool", "calculator.py: verified")
        assert journal.record("assistant_delta", "The sum ")
        assert journal.record("assistant_delta", "is 5.")
        text = journal.snapshot()
        assert text.count(thought) == 1
        assert text.count(" reasoning]") == 1
        assert text.count(" assistant_delta]") == 1
        assert "The sum is 5." in text
        assert text.index(thought) < text.index("calculator.py: verified") < text.index("The sum is 5.")
        data = journal.path.read_bytes()
        assert data.startswith(b"\xef\xbb\xbf")
        assert data.decode("utf-8-sig") == text
        assert not text.startswith("\ufeff")
    finally:
        journal.close(timeout=3)


def test_mid_reasoning_snapshot_does_not_insert_whitespace_or_restart_the_block(tmp_path):
    journal = conversations.ConversationJournal(tmp_path, reserve_bytes=0)
    try:
        assert journal.record("reasoning", "Consider e")
        first = journal.snapshot()
        assert journal.record("reasoning", "\u0301 and the next step.")
        second = journal.snapshot()
        assert second.startswith(first)
        assert "Consider e\u0301 and the next step." in second
        assert second.count(" reasoning]") == 1
        assert journal.record("status", "Tool complete")
        assert journal.record("reasoning", "Check the final result.")
        third = journal.snapshot()
        assert third.count(" reasoning]") == 2
        assert third.index("Tool complete") < third.index("Check the final result.")
    finally:
        journal.close(timeout=3)


def test_private_journal_initializer_only_redirects_the_application_root(tmp_path):
    calls = []

    def original(instance, root, **kwargs):
        calls.append((instance, root, kwargs))

    target = tmp_path / "private_archives"
    initialize = private_conversation_initializer(original, target)
    owner = object()
    initialize(owner, ROOT, reserve_bytes=0)
    explicit = tmp_path / "explicit_workspace"
    initialize(owner, explicit, pending_bytes=512)
    assert calls == [(owner, target, {"reserve_bytes": 0}), (owner, explicit, {"pending_bytes": 512})]


def test_autouse_fixture_isolates_central_and_embedded_archive_classes(variant, tmp_path):
    for archive_class in (conversations.ConversationJournal, variant.ConversationJournal):
        journal = archive_class(receipts.ROOT, reserve_bytes=0)
        try:
            assert journal.root.is_relative_to(tmp_path.resolve())
            assert journal.record("reasoning", "Model-free test data belongs only in temporary archives.")
            assert "Model-free test data" in journal.snapshot()
        finally:
            journal.close(timeout=3)


def test_overflow_is_visible_in_the_closed_text_file(tmp_path):
    journal = conversations.ConversationJournal(tmp_path, reserve_bytes=0, pending_bytes=10)
    assert not journal.record("reasoning", "overflow")
    journal.close(timeout=3)
    assert "[RECORDING ERROR]" in journal.path.read_text(encoding="utf-8")


def test_archive_root_is_runtime_data_but_nested_source_stays_protected(tmp_path, monkeypatch):
    from evaluation import smoke_complex_tasks

    monkeypatch.setattr(smoke_complex_tasks, "ROOT", tmp_path)
    archive = tmp_path / "conversations"
    archive.mkdir()
    (archive / "session.txt").write_text("private conversation")
    nested = tmp_path / "src" / "conversations"
    nested.mkdir(parents=True)
    (nested / "source.py").write_text("value = 1\n")
    manifest = smoke_complex_tasks.protected_source_manifest(tmp_path / ".cache")
    assert "conversations/session.txt" not in manifest
    assert "src/conversations/source.py" in manifest
