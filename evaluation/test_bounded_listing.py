"""Registered public-variant MCP listings bound work before traversal or sorting."""
from __future__ import annotations

import asyncio
import errno
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "listing_workspace"
    root.mkdir()
    return root


@pytest.fixture
def listing(variant, workspace):
    server = variant._build_local_mcp_server(
        variant._APP.base, workspace, workspace, Path(variant.__file__),
    )

    def call(**arguments):
        return asyncio.run(server.call_tool("list_files", arguments))[1]

    return call


class Entries:
    def __init__(self, values):
        self.values = iter(values)
        self.reads = 0
        self.closed = False

    def __next__(self):
        self.reads += 1
        return next(self.values)

    def close(self):
        self.closed = True


def file_entry(path):
    return SimpleNamespace(path=str(path), is_dir=lambda: False, is_file=lambda: True,
                           stat=lambda: SimpleNamespace(st_size=7))


@pytest.mark.parametrize("recursive", [False, True])
def test_exclusions_are_pruned_before_descent(listing, workspace, variant, monkeypatch, recursive):
    root = workspace
    (root / ".cache" / "buried").mkdir(parents=True)
    (root / ".cache" / "buried" / "hidden.py").write_text("hidden")
    (root / "src").mkdir()
    (root / "src" / "visible.py").write_text("visible")
    opened = []
    original = variant.os.scandir

    def scan(path):
        opened.append(Path(path))
        assert ".cache" not in Path(path).relative_to(root).parts
        return original(path)

    monkeypatch.setattr(variant.os, "scandir", scan)
    result = listing(recursive=recursive)
    names = {entry["path"] for entry in result["entries"]}
    assert "src" in names
    assert ("src/visible.py" in names) is recursive
    assert not any(".cache" in name for name in names)
    assert result["complete"] and result["error_count"] == 0
    assert len(opened) == (2 if recursive else 1)


@pytest.mark.parametrize("recursive", [False, True])
def test_result_cap_does_not_materialize_or_sort_the_unread_tail(listing, workspace, variant, monkeypatch, recursive):
    root = workspace

    def values():
        for name in ("z.txt", "y.txt", "x.txt"):
            yield file_entry(root / name)
        pytest.fail("Listing consumed entries beyond its result cap")

    iterator = Entries(values())
    monkeypatch.setattr(variant.os, "scandir", lambda _path: iterator)
    result = listing(recursive=recursive, max_entries=3)
    assert [entry["path"] for entry in result["entries"]] == ["x.txt", "y.txt", "z.txt"]
    assert iterator.reads == result["scanned_entries"] == 3
    assert iterator.closed
    assert result["limit_reached"] == "max_entries" and not result["complete"]


def test_excluded_only_stream_has_an_independent_scan_budget(listing, workspace, variant, monkeypatch):
    root = workspace

    def values():
        for _ in range(1024):
            yield SimpleNamespace(path=str(root / ".cache"))
        pytest.fail("Excluded names bypassed the scan budget")

    iterator = Entries(values())
    monkeypatch.setattr(variant.os, "scandir", lambda _path: iterator)
    result = listing(recursive=True, max_entries=1)
    assert result["entries"] == [] and not result["complete"]
    assert result["scan_limit"] == result["scanned_entries"] == iterator.reads == 1024
    assert result["limit_reached"] == "scanned_entries" and iterator.closed


def test_nested_access_error_is_reported_without_losing_other_entries(listing, workspace, variant, monkeypatch):
    root = workspace
    denied = root / "denied"
    denied.mkdir()
    (root / "ok.txt").write_text("ok")
    original = variant.os.scandir

    def scan(path):
        if Path(path) == denied:
            raise PermissionError(errno.EACCES, "fixture denied", str(path))
        return original(path)

    monkeypatch.setattr(variant.os, "scandir", scan)
    result = listing(recursive=True)
    assert {entry["path"] for entry in result["entries"]} == {"denied", "ok.txt"}
    assert result["error_count"] == 1 and result["errors"][0]["path"] == "denied"
    assert "fixture denied" in result["errors"][0]["error"]
    assert not result["complete"] and result["limit_reached"] is None


def test_iterator_error_reports_partial_data_and_closes(listing, workspace, variant, monkeypatch):
    def values():
        yield file_entry(workspace / "visible.txt")
        raise PermissionError(errno.EACCES, "fixture iterator denied")

    iterator = Entries(values())
    monkeypatch.setattr(variant.os, "scandir", lambda _path: iterator)
    result = listing(recursive=True)
    assert [entry["path"] for entry in result["entries"]] == ["visible.txt"]
    assert not result["complete"] and result["error_count"] == 1
    assert "fixture iterator denied" in result["errors"][0]["error"]
    assert iterator.closed


def test_error_details_are_bounded_without_hiding_error_count(listing, workspace, variant, monkeypatch):
    def denied():
        raise PermissionError(errno.EACCES, "fixture metadata denied")

    items = [SimpleNamespace(path=str(workspace / f"denied_{number}"), is_dir=denied)
             for number in range(40)]
    iterator = Entries(items)
    monkeypatch.setattr(variant.os, "scandir", lambda _path: iterator)
    result = listing(recursive=True)
    assert result["error_count"] == 40 and len(result["errors"]) == 20
    assert result["entries"] == [] and not result["complete"] and iterator.closed


def test_directory_links_are_listed_but_never_followed(listing, workspace, variant, monkeypatch):
    root = workspace
    outside = root.parent / (root.name + "-link-target")
    outside.mkdir()
    (outside / "hidden.txt").write_text("outside")
    (root / "external").symlink_to(outside, target_is_directory=True)
    (root / "loop").symlink_to(root, target_is_directory=True)
    original = variant.os.scandir
    opened = []

    def scan(path):
        opened.append(Path(path))
        return original(path)

    monkeypatch.setattr(variant.os, "scandir", scan)
    result = listing(recursive=True)
    assert {entry["path"] for entry in result["entries"]} == {outside.as_posix(), "."}
    assert all(entry["is_dir"] for entry in result["entries"])
    assert opened == [root] and result["complete"]


def test_junction_guard_does_not_descend(listing, workspace, monkeypatch):
    root = workspace
    junction = root / "junction"
    junction.mkdir()
    (junction / "hidden.txt").write_text("hidden")
    monkeypatch.setattr(Path, "is_junction", lambda path: path == junction)
    result = listing(recursive=True)
    assert [entry["path"] for entry in result["entries"]] == ["junction"]
    assert result["complete"]


def test_requested_root_access_error_is_not_a_successful_empty_listing(listing, variant, monkeypatch):
    def denied(_path):
        raise PermissionError(errno.EACCES, "fixture root denied")

    monkeypatch.setattr(variant.os, "scandir", denied)
    with pytest.raises(ToolError, match="fixture root denied"):
        listing(recursive=True)


def test_unexpected_failure_still_closes_directory_handles(listing, workspace, variant, monkeypatch):
    def broken_stat():
        raise RuntimeError("fixture metadata failure")

    item = file_entry(workspace / "broken.txt")
    item.stat = broken_stat
    iterator = Entries([item])
    monkeypatch.setattr(variant.os, "scandir", lambda _path: iterator)
    with pytest.raises(ToolError, match="fixture metadata failure"):
        listing(recursive=True)
    assert iterator.closed


@pytest.mark.parametrize("maximum", [0, -1, 501])
def test_invalid_result_caps_fail_before_scan(listing, variant, monkeypatch, maximum):
    monkeypatch.setattr(variant.os, "scandir", lambda _path: pytest.fail("Invalid cap started traversal"))
    with pytest.raises(ToolError, match="max_entries must be between 1 and 500"):
        listing(max_entries=maximum)
