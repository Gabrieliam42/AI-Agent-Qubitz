"""Shared drain methods retain exact parity without changing variant profiles."""
from __future__ import annotations

import json

import pytest

import qubitz_variant_builder as builder
from runtime.paths import variant_path

METHODS = (
    "_install_agent_tool_hardening.HardenedGUI._drain_gui_events",
    "_install_agent_tool_hardening.HardenedGUI._consume_gui_events",
)


@pytest.fixture(scope="module")
def reference_source():
    path = variant_path(builder.DEFAULT_REFERENCE)
    return path, builder.read_variant_source(path)


def test_shared_drains_preserve_the_existing_declared_profile(reference_source):
    path, original = reference_source
    profiles = json.loads(builder.PROFILES_PATH.read_text())["variants"]
    expected = next(row for row in profiles if row["filename"] == path.name)
    assert set(METHODS) <= set(builder.COMMON_FUNCTIONS)
    assert builder.profile_from_text(path, original) == expected


@pytest.mark.parametrize("method", METHODS)
def test_shared_drain_body_drift_is_still_rejected(reference_source, monkeypatch, method):
    path, original = reference_source
    span = builder.node_spans(original)[method]
    # Keep the method registered, but alter its body without executing the source.
    header, newline, body = span.text.partition("\n")
    altered_method = header + newline + "            pass\n" + body
    altered = original[:span.start] + altered_method + original[span.end:]
    monkeypatch.setattr(builder, "read_variant_source", lambda _path: altered)
    assert f"{path.name}: drift in {method}" in builder.check_template([path], builder.load_template())
