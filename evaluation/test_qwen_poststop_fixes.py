"""Approved post-stop regressions; public variants only, with model loading forbidden."""
from __future__ import annotations

import asyncio
import hashlib
import queue
import threading
from pathlib import Path

import pytest

from evaluation import test_followup_state_receipts as receipts
from runtime.conversations import ConversationJournal, RecordingQueue
from runtime.prohibition_scope import class_targets, inspection_command

variant = receipts.variant
runner = receipts.runner


@pytest.fixture
def server(variant, tmp_path):
    return variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, Path(variant.__file__))


def tool(server, name, **arguments):
    return asyncio.run(server.call_tool(name, arguments))[1]


def test_named_file_search_literal_case_and_cap(server, tmp_path):
    (tmp_path / "source.js").write_text("const A = '[Hi]';\n// [hi]\n// [Hi]\n", encoding="utf-8")
    found = tool(server, "search_text", query="[hi]", path="source.js", max_results=2)
    assert [item["line"] for item in found["matches"]] == [1, 2]
    assert all(item["path"] == "source.js" for item in found["matches"])
    assert len(tool(server, "search_text", query="[Hi]", path="source.js", case_sensitive=True)["matches"]) == 2
    assert not tool(server, "search_text", query="[Hi]", path="source.js", file_glob="*.py")["matches"]
    assert len(tool(server, "search_text", query="[hi]", path=".")["matches"]) == 3


def test_conversation_archive_not_in_auto_retrieval_or_search(variant, server, tmp_path):
    for name in ("conversations", "Conversations"):
        directory = tmp_path / name
        directory.mkdir(exist_ok=True)
        (directory / "private.txt").write_text("private archive marker")
        assert variant._APP.base.is_excluded_dir_name(name)
    (tmp_path / "ordinary.txt").write_text("private archive marker")
    found = tool(server, "search_text", query="private archive marker", path=".")
    assert [item["path"] for item in found["matches"]] == ["ordinary.txt"]
    files = list(variant._APP.base.iter_workspace_files(tmp_path, variant._APP.base.EXCLUDED_DIRS))
    assert not any("conversations" in str(p).casefold() for p in files)


def test_target_prohibitions_are_not_blanket_bans(variant, runner):
    prompt = "Create output.html then verify it. Do not change evaluator files or run models."
    semantic = variant._analyze_semantic_intent(prompt)
    assert not {"execute", "edit"} & set(semantic.prohibited_actions)
    assert ("execute", "@class:models") in semantic.scoped_prohibitions
    assert ("edit", "@class:evaluator_files") in semantic.scoped_prohibitions
    broker = receipts.begin(runner, prompt)
    assert broker.authorize("run_command", {"command": "rg light output.html"})[0]
    assert not broker.authorize("run_command", {"command": "python model.py"})[0]
    assert not broker.authorize("write_file", {"path": "evaluation/evaluator.py", "content": "x"})[0]


@pytest.mark.parametrize("negative", ["Do not execute anything.", "Do not run commands.", "Do not use tools."])
def test_blanket_prohibitions_remain_enforced(variant, runner, negative):
    prompt = "Create output.html and verify it. " + negative
    broker = receipts.begin(runner, prompt)
    assert not broker.authorize("run_command", {"command": "rg light output.html"})[0]


@pytest.mark.parametrize("count", [1, 2])
def test_real_write_files_creation_receipt_is_credited(server, runner, tmp_path, count):
    names = [f"created{index}.json" for index in range(count)]
    prompt = "Create " + " and ".join(names) + " with JSON data."
    broker = receipts.begin(runner, prompt)
    args = {"files": [{"path": name, "content": '{"value":7}\n'} for name in names]}
    assert broker.authorize("write_files", args)[0]
    data = tool(server, "write_files", **args)
    broker.record_result("write_files", args, receipts.result(data))
    item = broker.evidence[-1]
    assert item["success"]
    assert {"created_outputs", "changed_files"} <= set(item["categories"])
    assert set(item["paths"]) == {str((tmp_path / name).resolve()) for name in names}
    assert "created_outputs" not in broker.completion_report(prompt)[1]


@pytest.mark.parametrize("fault", ["hash", "path", "created", "duplicate"])
def test_bad_creation_receipts_stay_uncredited(runner, tmp_path, fault):
    target = tmp_path / "created.json"
    target.write_text('{"value":7}\n')
    broker = receipts.begin(runner, "Create created.json with JSON data.")
    item = {"path": "created.json", "created": True, "new_sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    args = {"files": [{"path": "created.json", "content": target.read_text()}]}
    if fault == "hash":
        item["new_sha256"] = "0" * 64
    elif fault == "path":
        item["path"] = "different.json"
    elif fault == "created":
        item["created"] = False
    else:
        args["files"].append(dict(args["files"][0]))
        assert not broker.authorize("write_files", args)[0]
        return
    broker.record_result("write_files", args, receipts.result({"files": [item]}))
    assert not broker.evidence[-1]["success"]


def test_journal_order_unicode_large_text_and_complete_barrier(tmp_path):
    journal = ConversationJournal(tmp_path, reserve_bytes=0)
    text = "Earth \U0001f30d e\u0301 \u6f22\u5b57\n" * 20000
    try:
        assert journal.record("user", "prompt")
        assert journal.record("reasoning", text)
        wrapped = RecordingQueue(queue.Queue(), journal)
        wrapped.put(("tool", "tool display"))
        wrapped.put(("answer", "complete"))
        snapshot = journal.snapshot()
        assert text in snapshot
        assert snapshot.index("prompt") < snapshot.index(text) < snapshot.index("tool display") < snapshot.index("complete\n")
        assert journal.error == ""
    finally:
        journal.close(timeout=5)
    assert not journal._thread.is_alive()
    assert "complete" in journal.path.read_text(encoding="utf-8")


def test_bounded_queue_overflow_cannot_claim_full_retention(tmp_path):
    journal = ConversationJournal(tmp_path, pending_bytes=16, reserve_bytes=0)
    try:
        assert not journal.record("reasoning", "large text")
        assert "queue exhausted" in journal.error
        with pytest.raises(RuntimeError, match="incomplete"):
            journal.snapshot()
    finally:
        journal.close(timeout=5)


def test_concurrent_journal_producers_have_unique_ordered_sequences(tmp_path):
    journal = ConversationJournal(tmp_path, reserve_bytes=0)
    try:
        workers = [threading.Thread(target=lambda n=n: [journal.record("event", f"{n}/{i}") for i in range(20)])
                   for n in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        snapshot = journal.snapshot()
        import re

        assert [int(n) for n in re.findall(r"\n\[(\d{8}) ", snapshot)] == list(range(1, 81))
    finally:
        journal.close(timeout=5)


def test_model_execution_class_does_not_accept_arbitrary_shell():
    assert class_targets(" models", {"execute"}) == ("@class:models",)
    assert not class_targets(" anything", {"execute"})
    assert inspection_command("grep -n light source.js")
    for command in ("rg --pre python source.js", "rg --pre=python source.js", "cat x; python model.py",
                    "python -c 'import torch'", "sh -c 'cat x'", "rg $(python model.py)"):
        assert not inspection_command(command)


def test_solar_visual_suite_is_separate_from_original():
    from followup_matrix_solar import PROMPT as original
    from followup_matrix_solar_visual import PROMPT, valid_state

    from evaluation.disposable_followup_matrix import selected_scenarios

    assert len(selected_scenarios(None, "expanded-v5")) == 27
    original_case = selected_scenarios(["solar_system_3d_followup"], "expanded-v5")[0]
    assert original_case.turns[0].prompt == original
    case = selected_scenarios(None, "solar-visual-v1")[0]
    assert case.name == "solar_system_visual_v1"
    assert len(PROMPT) < len(original) / 2
    assert case.turns[0].checks[0].kind == "solar_visual_browser"
    assert not valid_state({})
