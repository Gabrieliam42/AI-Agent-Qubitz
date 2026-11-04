"""Actual browser parsing and shared-runner completion; backend transport is mocked."""
from __future__ import annotations

import asyncio
import hashlib

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner


@pytest.mark.parametrize("online", [False, True])
def test_real_browser_policy_receipt_without_external_requests(variant, monkeypatch, online):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: online)
    source = "<html><body><p>Inventory</p><script>document.title='Inventory';</script></body></html>"
    result = variant._validate_html_artifact(source)
    assert result["browser_load"] == "passed", result
    context = result["verification_context"]
    assert context["artifact_sha256"] == hashlib.sha256(source.encode()).hexdigest()
    assert context["origin"] == "about:blank"
    assert context["network_policy"] == ("bounded_online_cdn" if online else "offline_no_external_resources")


def test_real_syntax_failure_preserves_policy_and_never_writes(variant, monkeypatch, tmp_path):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    with pytest.raises(ValueError, match="browser-load check failed") as error:
        variant._validate_html_artifact("<html><script>let = ;</script></html>")
    assert error.value.verification_context["network_policy"] == "offline_no_external_resources"
    assert list(tmp_path.iterdir()) == []


def test_real_blocked_dependency_names_exact_url(variant, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    source = '<html><script src="https://assets.example.invalid/widget/7.js"></script></html>'
    result = variant._validate_html_artifact(source)
    assert result["browser_load"] == "unavailable"
    assert result["browser_blocked_requests"] == ["https://assets.example.invalid/widget/7.js"]


def test_html_edit_never_announces_all_obligations_passed_before_partial_final(variant, runner, monkeypatch):
    path = runner.workspace / "panel.html"
    before = "<html><script>let scale = 1;</script></html>"
    after = before.replace("scale = 1", "scale = 2")
    path.write_text(before)
    receipts.install_host(variant, runner, monkeypatch)
    mro = type(runner).__mro__
    parent = mro[next(i for i, cls in enumerate(mro) if cls.__name__ == "HardenedAgentRunner") + 1]

    async def edit(self, _prompt, callback=None):
        broker = self._tool_permission_broker
        arguments = {"path": str(path), "replacements": [{"old_text": "scale = 1", "new_text": "scale = 2"}]}
        assert broker.authorize("apply_text_patch", arguments)[0]
        path.write_text(after)
        broker.record_result("apply_text_patch", arguments, receipts.result({
            "path": str(path), "new_sha256": hashlib.sha256(after.encode()).hexdigest(),
            "artifact_validation": {"browser_load": "passed", "visual_preview": "nonblank"},
        }))
        return "Updated the requested file."

    monkeypatch.setattr(parent, "_run_async", edit)
    events = []
    answer = asyncio.run(runner._run_async(
        "In panel.html change scale from 1 to 2; preserve the working animation and interactive controls.",
        lambda kind, message: events.append((kind, message)),
    ))
    assert "[Completion status: partial]" in answer
    assert runner._qubitz_completion_state["status"] == "partial"
    assert not any("passed for the requested postconditions" in message for kind, message in events if kind == "status")
    assert "incomplete" in events[-1][1] or any("incomplete" in message for kind, message in events if kind == "status")
