"""Generic follow-up repair contracts; no model server or user workspace."""
from __future__ import annotations

import hashlib
import json
import subprocess

import pytest
import test_explicit_creation_contracts as creation
import test_file_content_recovery as recovery

runtime = recovery.runtime
variant = creation.variant
broker = creation.broker


def test_inspected_follow_up_bounds_first_edit_response(runtime, variant, broker):
    base, client, owner = recovery.wire(runtime, variant, broker)
    target = broker.workspace / "dashboard.html"
    target.write_text("<html><body><h1>Dashboard</h1></body></html>", encoding="utf-8")
    prompt = "The dashboard navigation does not respond to clicks."
    broker.begin_turn(prompt, None)
    broker.bind_follow_up_repair("Create dashboard.html", prompt, [str(target)])
    broker._initial_target_inspection_used = True
    broker.transaction_snapshot_hashes[str(target.resolve())] = hashlib.sha256(target.read_bytes()).hexdigest()
    owner._wrapper_no_progress_tool_choice = lambda _prompt, _answer: {
        "type": "function", "function": {"name": "apply_text_patch"},
    }
    client.config.num_predict = 16384
    base.set_qubitz_reasoning_mode("xhigh")
    base.set_qubitz_chat_template_kwargs({"enable_thinking": True})
    payloads = []
    client._call_with_fallback = lambda *args: (
        payloads.append(args[-1]) or recovery.raw("I should patch the existing file.", finish="length")
    )
    tools = [{"type": "function", "function": {"name": "apply_text_patch"}}]

    client.chat(**recovery.request(tools=tools))

    assert [payload["max_tokens"] for payload in payloads] == [4096, 8192]
    assert payloads[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert payloads[1]["tool_choice"] == "required"
    assert [tool["function"]["name"] for tool in payloads[1]["tools"]] == ["apply_text_patch"]
    assert "minimal patch" in str(payloads[0]["messages"])


def test_graphical_browser_probe_reports_click_handler_error(variant, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Chromium unavailable: {exc}")

    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    html = """<canvas width="128" height="128"></canvas><script>
      const canvas = document.querySelector('canvas');
      const context = canvas.getContext('2d');
      context.fillStyle = '#123456';
      context.fillRect(0, 0, 128, 128);
      context.fillStyle = '#fedcba';
      context.fillRect(0, 0, 64, 64);
      canvas.addEventListener('mousedown', () => { throw new Error('interaction broken'); });
    </script>"""
    with pytest.raises(ValueError, match="interaction broken"):
        variant._check_graphical_html_visibility(html)


def test_existing_edit_retains_interaction_error_as_diagnostic(variant, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    results = iter([
        {"status": "passed"},
        {"status": "interaction_failed", "reason": "click handler raised"},
    ])
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout=json.dumps(next(results)), stderr="",
        ),
    )
    html = "<canvas width='128' height='128'></canvas><script>void 0</script>"

    validation = variant._validate_html_artifact(html, allow_existing_interaction_errors=True)

    assert validation["browser_load"] == "passed"
    assert validation["interaction_status"] == "failed"
    assert validation["interaction_reason"] == "click handler raised"
