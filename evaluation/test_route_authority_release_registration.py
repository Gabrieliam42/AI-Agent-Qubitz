"""The shared route authority is release-checked, not variant configuration."""
from __future__ import annotations

import json

import pytest

import qubitz_variant_builder as builder
from runtime.paths import variant_path

METHOD = "_install_agent_tool_hardening.HardenedAgentRunner._selected_route_name"


@pytest.fixture(scope="module")
def reference_source():
    path = variant_path(builder.DEFAULT_REFERENCE)
    return path, builder.read_variant_source(path)


def test_shared_route_authority_matches_declared_profile(reference_source):
    path, source = reference_source
    saved = json.loads(builder.PROFILES_PATH.read_text())["variants"]
    expected = next(row for row in saved if row["filename"] == path.name)
    assert METHOD in builder.COMMON_FUNCTIONS
    assert builder.profile_from_text(path, source) == expected


def test_shared_route_authority_body_drift_is_rejected(reference_source, monkeypatch):
    path, source = reference_source
    span = builder.node_spans(source)[METHOD]
    changed = span.text.replace('selected = getattr(decision, "selected_route", None)',
                                'selected = "tool_loop"', 1)
    assert changed != span.text
    altered = source[:span.start] + changed + source[span.end:]
    monkeypatch.setattr(builder, "read_variant_source", lambda _path: altered)
    assert f"{path.name}: drift in {METHOD}" in builder.check_template([path], builder.load_template())
