"""Resource policies and completion obligations are independent of the generated task."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from runtime.browser_evidence import (
    CachedResource,
    OfflineResources,
    cached_browser_resources,
    resource_bytes,
    resource_capabilities,
    verification_context,
)
from runtime.completion_state import completion_state


def test_same_bytes_different_resource_policy_is_not_same_verification():
    content = "<html><script>document.title='Inventory'</script></html>"
    online = verification_context(content, origin="about:blank", network_policy="bounded_online_cdn",
                                  online_hosts=["assets.example.org"])
    offline = verification_context(content, origin="http://127.0.0.1:18765", network_policy="offline_exact_resources")
    assert online["artifact_sha256"] == offline["artifact_sha256"]
    assert online != offline
    assert "not behavior" in online["scope"]


@pytest.mark.parametrize("tampered", [False, True])
def test_exact_cached_resource_never_substitutes_other_bytes(tmp_path, tampered):
    script = tmp_path / "widget.js"
    script.write_bytes(b"window.widget=1")
    resource = CachedResource(script, hashlib.sha256(script.read_bytes()).hexdigest(), "1.2")
    if tampered:
        script.write_bytes(b"window.widget=2")
        with pytest.raises(ValueError, match="SHA-256 mismatch"):
            resource_bytes(resource)
    else:
        assert resource_bytes(resource) == b"window.widget=1"


def test_unknown_version_blocked_without_fetch_or_alias(tmp_path):
    path = tmp_path / "widget.js"
    path.write_bytes(b"original")
    resources = OfflineResources(tmp_path, {
        "https://assets.example.org/widget/1.2.js": CachedResource(path, hashlib.sha256(b"original").hexdigest()),
    })
    route = Mock(request=SimpleNamespace(url="https://assets.example.org/widget/1.3.js"))
    resources.route(route)
    route.abort.assert_called_once()
    route.fulfill.assert_not_called()
    assert resources.blocked == [route.request.url]


@pytest.mark.parametrize("path", ["../escape.js", "/escape.js", "a/../escape.js", "a\\escape.js"])
def test_catalog_refuses_noncanonical_paths(tmp_path, path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime/browser_resources.json").write_text(json.dumps({"schema_version": 1, "resources": [
        {"path": path, "sha256": "0" * 64, "urls": ["https://assets.example.org/1.js"]},
    ]}))
    with pytest.raises(ValueError):
        cached_browser_resources(tmp_path)


def test_available_catalog_is_hash_verified_and_not_execution_proof():
    capabilities = resource_capabilities()
    assert capabilities["available"] and not capabilities["acquisition_allowed"]
    assert all(len(item["sha256"]) == 64 for item in capabilities["available"])
    assert "not proof" in capabilities["scope"]


@pytest.mark.parametrize("missing,extra,status", [
    ([], [], "verified"), (["report_file"], [], "partial"), ([], ["interaction behavior"], "partial"),
])
def test_completion_state_retains_all_independent_obligations(missing, extra, status):
    state = completion_state(["changed_files", "report_file"], missing, unverified=extra)
    assert state.status == status == state.receipt()["status"]
    assert ("passed for the requested" in state.message()) is (status == "verified")
    assert set(state.verified).isdisjoint(state.unverified)
    assert set(state.required) == set(state.verified) | set(state.unverified)


def test_intentionally_failing_test_execution_is_not_test_success():
    state = completion_state(["test_command_executed", "tests"], ["tests"])
    assert state.verified == ("test command executed",)
    assert state.unverified == ("tests",)


def test_no_action_postconditions_do_not_fabricate_verification():
    assert completion_state([], []).status == "not_applicable"
