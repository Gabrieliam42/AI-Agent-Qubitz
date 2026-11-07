"""Reject unfinished raw-text HTML elements without imposing document scaffolding."""
from __future__ import annotations

import asyncio
import subprocess

import pytest
import test_explicit_creation_contracts as creation

from runtime.artifact_contracts import html_envelope_issues


@pytest.mark.parametrize("source", [
    '<script>window.ready = true;',
    '<script type="module">const answer = 42; /* unfinished',
    '<script type="importmap">{"imports":{}}',
    '<script src="local.js">',
    '<STYLE>body { color: red; }',
    '<p>Before</p><script>/* comment */',
])
def test_unclosed_raw_text_is_rejected(source):
    assert any("Unclosed" in issue for issue in html_envelope_issues(source))


@pytest.mark.parametrize("source", [
    '<script>window.ready = true;</script>',
    '<script type="module">const marker = "<script>";</script>',
    '<script type="importmap">{"imports":{}}</script>',
    '<style>body { color: red; }</style>',
    '<!-- <script> not an element -->',
    '<p>&lt;script&gt; is text',
    '<!doctype html><title>Valid omitted document end tags</title><p>Content',
    '<div>A valid fragment</div>',
])
def test_complete_blocks_and_valid_fragments_remain_accepted(source):
    assert not html_envelope_issues(source)


def test_unclosed_script_stops_before_browser_or_network(monkeypatch):
    variant = creation.variant.__wrapped__()
    monkeypatch.setattr(variant, "_check_html_dependencies", lambda *_: pytest.fail("No dependency requests permitted"))
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("No browser subprocess permitted"))
    with pytest.raises(ValueError, match="Unclosed script"):
        variant._validate_html_artifact('<script type="module">window.ready = true;')


def test_registered_write_rejects_unclosed_script_without_creating_file(tmp_path):
    variant = creation.variant.__wrapped__()
    server = variant._build_local_mcp_server(variant._APP.base, tmp_path, tmp_path, creation.REFERENCE)
    with pytest.raises(Exception, match="Unclosed script"):
        asyncio.run(server.call_tool("write_file", {"path": "example.html", "content": '<script type="module">window.ready = true;'}))
    assert not (tmp_path / "example.html").exists()
