"""HTML validation contracts; browser subprocesses and network are mocked."""
from __future__ import annotations

import io
import json
import subprocess
import urllib.error
import urllib.request

import pytest
import test_explicit_creation_contracts as creation


@pytest.fixture(scope="module")
def loaded_variant():
    return creation.variant.__wrapped__()


def test_missing_public_script_blocks_creation_before_publish(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: True)

    class MissingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not found", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: MissingOpener())
    html = '<script src="https://cdnjs.cloudflare.com/ajax/libs/example/1/missing.js"></script>'
    with pytest.raises(ValueError, match="HTML dependency is missing.*no file was changed"):
        loaded_variant._check_html_dependencies(html)


def test_offline_and_unknown_hosts_are_not_fetched(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    html = '<script src="https://cdnjs.cloudflare.com/example.js"></script>'
    assert loaded_variant._check_html_dependencies(html)["unchecked"] == 1

    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: True)
    monkeypatch.setattr(
        urllib.request, "build_opener",
        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )
    other = '<script src="https://example.org/app.js"></script>'
    assert loaded_variant._check_html_dependencies(other)["unchecked"] == 1


def test_importmap_dependencies_are_discovered(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    html = '<script type="importmap">{"imports":{"lib":"https://cdn.jsdelivr.net/npm/lib/x.js"}}</script>'
    assert loaded_variant._check_html_dependencies(html)["unchecked"] == 1


def test_importmap_prefixes_are_not_fetched_as_files(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: True)
    fetched = []

    class RecordingOpener:
        def open(self, request, timeout):
            fetched.append(request.full_url)
            if request.full_url.endswith("/examples/jsm"):
                raise urllib.error.HTTPError(request.full_url, 404, "Not found", {}, None)
            return io.BytesIO(b"")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: RecordingOpener())
    html = (
        '<script type="importmap">{"imports":{'
        '"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",'
        '"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"},'
        '"scopes":{"https://example.test/":{'
        '"helpers/":"https://cdn.jsdelivr.net/npm/lib/helpers/",'
        '"util":"https://cdn.jsdelivr.net/npm/lib/util.js"}}}</script>'
    )

    assert loaded_variant._check_html_dependencies(html)["checked"] == 2
    assert fetched == [
        "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
        "https://cdn.jsdelivr.net/npm/lib/util.js",
    ]


def test_missing_importmap_module_still_blocks_creation(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: True)

    class MissingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(request.full_url, 404, "Not found", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: MissingOpener())
    html = '<script type="importmap">{"imports":{"lib":"https://cdn.jsdelivr.net/npm/lib/missing.js"}}</script>'
    with pytest.raises(ValueError, match="HTML dependency is missing.*missing.js"):
        loaded_variant._check_html_dependencies(html)


def test_browser_script_failure_rejects_html_before_commit(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0,
            stdout=json.dumps({"status": "failed", "errors": ["Failed to resolve module specifier 'three'"]}),
            stderr="",
        ),
    )
    with pytest.raises(ValueError, match="Failed to resolve module specifier.*no file was changed"):
        loaded_variant._validate_html_artifact('<script type="module">import {Scene} from "three"</script>')


def test_browser_unavailable_never_claims_rendered(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0,
            stdout=json.dumps({"status": "unavailable", "reason": "browser missing"}),
            stderr="",
        ),
    )
    result = loaded_variant._validate_html_artifact("<script>window.ready = true</script>")
    assert result["browser_load"] == "unavailable"
    assert result["browser_rendered"] is False


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        (
            "Create an interactive 3D HTML scene with JavaScript.",
            ("requested visual behavior", "requested interactions"),
        ),
        ("Create an animated HTML canvas.", ("requested visual behavior",)),
        ("Create a static HTML file named index.html.", ()),
    ],
)
def test_browser_load_does_not_verify_requested_html_behavior(loaded_variant, prompt, expected):
    assert loaded_variant._unverified_html_behavior_postconditions(prompt) == expected


def test_nonfinite_webgl_draw_call_blocks_graphical_artifact(loaded_variant, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    try:
        with playwright.sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            browser.close()
    except playwright.Error as exc:
        pytest.skip(f"Chromium unavailable: {exc}")

    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    html = """<canvas width="128" height="128"></canvas><script>
      const gl = document.querySelector('canvas').getContext('webgl');
      gl.clearColor(1, 0, 0, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.uniformMatrix4fv(null, false, new Float32Array([NaN, 0, 0, 0]));
    </script>"""
    with pytest.raises(ValueError, match="non-finite numeric values"):
        loaded_variant._check_graphical_html_visibility(html)
