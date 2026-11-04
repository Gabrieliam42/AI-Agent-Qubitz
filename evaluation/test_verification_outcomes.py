"""Evidence availability must never be mistaken for task acceptance or failure."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from evaluation.browser_observation import CachedResource, OfflineResources
from evaluation.verification_outcomes import probe_issues, verification_status


@pytest.mark.parametrize("status,expected", [
    ("passed", "passed"), ("failed", "failed"), ("unavailable", "blocked"),
    ("error", "harness_error"), (None, "harness_error"), ("unexpected", "harness_error"),
])
def test_shared_probe_outcome_is_fail_closed(status, expected):
    issues = probe_issues({"status": status}, "arbitrary_check:artifact")
    assert verification_status(issues) == expected
    assert verification_status(issues, interrupted=True) == "interrupted"


def test_missing_evidence_does_not_hide_a_demonstrated_failure():
    assert verification_status(["evidence_unavailable:browser", "unexpected_mutation:file"]) == "failed"
    assert verification_status(["evaluator_error:cleanup", "postcondition_failed:check"]) == "harness_error"
    assert probe_issues({"status": "measured"}, "baseline", accepted="measured") == []
    assert verification_status(["browser_infrastructure_unavailable"]) == "blocked"


class Route:
    def __init__(self, url):
        self.request = SimpleNamespace(url=url)
        self.aborted = False
        self.response = None

    def abort(self):
        self.aborted = True

    def fulfill(self, **kwargs):
        self.response = kwargs


def test_offline_resources_are_exact_hash_bound_and_never_network_fallback(tmp_path):
    path = tmp_path / "module.js"
    path.write_bytes(b"export const value = 7;")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    resources = OfflineResources(tmp_path, {"https://example.invalid/v1/module.js": CachedResource(path, digest)})
    good = Route("https://example.invalid/v1/module.js")
    resources.route(good)
    assert good.response["body"] == path.read_bytes() and not good.aborted
    for url in ["https://example.invalid/v2/module.js", "https://example.invalid/v1/module.js?changed",
                "http://127.0.0.1:18765/%2e%2e/secret", "file:///etc/passwd"]:
        request = Route(url)
        resources.route(request)
        assert request.aborted and request.response is None
    path.write_bytes(b"changed")
    corrupted = Route("https://example.invalid/v1/module.js")
    resources.route(corrupted)
    assert corrupted.aborted
    assert "SHA-256 mismatch" in resources.failures[-1]["message"]


def test_workspace_resource_preserves_bytes_and_receipt(tmp_path):
    path = tmp_path / "module.mjs"
    path.write_bytes(b"export {};\n")
    resources = OfflineResources(tmp_path)
    request = Route("http://127.0.0.1:18765/module.mjs")
    resources.route(request)
    assert request.response["content_type"] == "text/javascript"
    assert resources.served[0]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_resource_symlink_is_not_followed(tmp_path):
    target = tmp_path / "private.js"
    target.write_bytes(b"private")
    link = tmp_path / "link.js"
    link.symlink_to(target)
    resources = OfflineResources(tmp_path)
    request = Route("http://127.0.0.1:18765/link.js")
    resources.route(request)
    assert request.aborted and not resources.served
