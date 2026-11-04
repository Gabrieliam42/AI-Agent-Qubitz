"""HTML browser diagnostics and staged repair with mocked browser/model transports."""
from __future__ import annotations

import io
import json
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import pytest

from evaluation import test_file_content_recovery as recovery

variant = recovery.variant
broker = recovery.broker
runtime = recovery.runtime


def run_probe(variant, monkeypatch, capsys, *, error_name="", blocked=False, console_location=None, stack=None,
              canvas_count=0):
    callbacks = {}
    route_callback = []

    def set_content(*_args, **_kwargs):
        if blocked:
            route_callback[0](SimpleNamespace(
                request=SimpleNamespace(url="https://example.test/missing.js"),
                abort=lambda: None,
            ))
        if error_name:
            callbacks["pageerror"](SimpleNamespace(
                name=error_name, message="Unexpected token ':'" if error_name == "SyntaxError" else "THREE is not defined",
                stack=f"{error_name}: browser error\n    at about:blank:14:7" if stack is None else stack,
            ))
        if console_location is not None:
            callbacks["console"](SimpleNamespace(
                type="error", text="Console diagnostic", location=console_location,
            ))

    page = SimpleNamespace(on=lambda name, callback: callbacks.__setitem__(name, callback),
                           set_content=set_content, wait_for_timeout=lambda *_args: None,
                           locator=lambda selector: SimpleNamespace(count=lambda: canvas_count))
    context = SimpleNamespace(new_page=lambda: page, route=lambda _pattern, callback: route_callback.append(callback),
                              close=lambda: None)
    browser = SimpleNamespace(new_context=lambda **_kwargs: context, close=lambda: None)

    class PlaywrightContext:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **_kwargs: browser))

        def __exit__(self, *_args):
            pass

    package = ModuleType("playwright")
    api = ModuleType("playwright.sync_api")
    api.sync_playwright = PlaywrightContext
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({
        "content": "<script>bad()</script>", "online": False, "cdn_hosts": [],
    })))
    exec(compile(variant._HTML_BROWSER_PROBE_SOURCE, "<mocked-html-probe>", "exec"), {})  # noqa: S102 - trusted probe, mocked browser
    return json.loads(capsys.readouterr().out)


def test_blocked_resources_cannot_hide_syntax_errors(variant, monkeypatch, capsys):
    result = run_probe(variant, monkeypatch, capsys, error_name="SyntaxError", blocked=True)
    assert result["status"] == "failed"
    assert result["errors"][0] == "Unexpected token ':'"
    assert result["blocked_requests"] == ["https://example.test/missing.js"]
    diagnostic = result["diagnostics"][0]
    assert (diagnostic["name"], diagnostic["url"], diagnostic["line"], diagnostic["column"]) == (
        "SyntaxError", "about:blank", 14, 7,
    )


def test_offline_missing_dependency_keeps_runtime_check_unavailable(variant, monkeypatch, capsys):
    result = run_probe(variant, monkeypatch, capsys, error_name="ReferenceError", blocked=True)
    assert result["status"] == "unavailable"
    assert result["errors"] == ["THREE is not defined"]
    assert result["diagnostics"][0]["name"] == "ReferenceError"
    assert result["blocked_requests"]


def test_unblocked_runtime_error_still_fails_validation(variant, monkeypatch, capsys):
    result = run_probe(variant, monkeypatch, capsys, error_name="ReferenceError")
    assert result["status"] == "failed"
    assert result["errors"] == ["THREE is not defined"]


def test_external_error_does_not_claim_its_inline_callers_location(variant, monkeypatch, capsys):
    result = run_probe(variant, monkeypatch, capsys, error_name="SyntaxError", stack=(
        "SyntaxError: invalid\n    at https://example.test/library.js:90:4\n    at about:blank:14:7"
    ))
    assert result["diagnostics"][0]["url"] == "https://example.test/library.js"
    assert result["diagnostics"][0]["line"] == 90


def test_missing_error_location_is_not_invented(variant, monkeypatch, capsys):
    result = run_probe(variant, monkeypatch, capsys, error_name="SyntaxError", stack="SyntaxError: invalid")
    diagnostic = result["diagnostics"][0]
    assert diagnostic["url"] == ""
    assert diagnostic["line"] is None and diagnostic["column"] is None


@pytest.mark.parametrize("location", [
    {"url": "about:blank", "lineNumber": 10, "columnNumber": 4},
    {"url": "about:blank", "line": 10, "column": 4},
])
def test_console_coordinates_are_preserved_as_one_based(variant, monkeypatch, capsys, location):
    result = run_probe(variant, monkeypatch, capsys, console_location=location)
    assert result["status"] == "failed"
    assert (result["diagnostics"][0]["line"], result["diagnostics"][0]["column"]) == (11, 5)


def test_browser_failure_keeps_structured_diagnostics_for_repair(variant, monkeypatch):
    diagnostic = {"kind": "pageerror", "name": "SyntaxError", "message": "Invalid token",
                  "url": "about:blank", "line": 17, "column": 9}
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    monkeypatch.setattr(variant.subprocess, "run", lambda *args, **_kwargs: subprocess.CompletedProcess(
        args, 0, json.dumps({"status": "failed", "errors": ["Invalid token"], "diagnostics": [diagnostic]}), "",
    ))
    with pytest.raises(ValueError) as failure:
        variant._validate_html_artifact("<script>invalid</script>")
    assert failure.value.html_browser_diagnostics == [diagnostic]


def test_unavailable_browser_keeps_dependency_errors_and_blocked_requests(variant, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    payload = {"status": "unavailable", "reason": "bounded network scope",
               "errors": ["THREE is not defined"], "blocked_requests": ["https://example.test/library.js"],
               "diagnostics": [{"name": "ReferenceError", "message": "THREE is not defined"}]}
    monkeypatch.setattr(variant.subprocess, "run", lambda *args, **_kwargs: subprocess.CompletedProcess(
        args, 0, json.dumps(payload), "",
    ))
    result = variant._validate_html_artifact("<script>new THREE.Scene()</script>")
    assert result["browser_load"] == "unavailable"
    assert result["browser_context_errors"] == payload["errors"]
    assert result["browser_diagnostics"] == payload["diagnostics"]
    assert result["browser_blocked_requests"] == payload["blocked_requests"]


def test_staged_html_repair_uses_location_in_middle_of_large_draft(runtime, variant, broker):
    _, client, _ = recovery.wire(runtime, variant, broker)
    broker.begin_turn("Create a detailed HTML page with an interactive scene", None)
    broken = "const speed = :;"
    fixed = "const speed = 3;"
    draft = "<html>\n" + "<!-- unchanged padding -->\n" * 800 + f"<script>{broken}</script>\n" + "x" * 9000 + "</html>"
    line = draft[:draft.index(broken)].count("\n") + 1

    def validate(content):
        if broken in content:
            failure = ValueError("Unexpected token ':'")
            failure.html_browser_diagnostics = [{
                "kind": "pageerror", "name": "SyntaxError", "message": str(failure),
                "url": "about:blank", "line": line, "column": 22,
            }]
            raise failure
        return {"browser_load": "passed"}

    runtime[2]["_validate_html_artifact"] = validate
    replies = recovery.scoped_replies(
        recovery.raw(json.dumps({"path": "scene.html", "content": draft, "complete": True})),
        recovery.raw(json.dumps({"old_text": broken, "new_text": fixed})),
    )
    seen = []
    client._call_with_fallback = lambda *args: (seen.append(args[-1]) or next(replies))

    response = client.qubitz_file_creation_chat(**recovery.request(tools=recovery.TOOLS))

    assert response["message"]["tool_calls"][0]["function"]["arguments"]["content"] == draft.replace(broken, fixed)
    assert broken in str(seen[-1]["messages"])
    assert "browser_diagnostics" in str(seen[-1]["messages"])
    assert len(str(seen[-1]["messages"])) < len(draft)
    assert not (broker.workspace / "scene.html").exists()
