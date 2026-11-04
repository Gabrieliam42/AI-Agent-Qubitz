"""Unsupported page origins must not become false artifact failures or passes."""
import json
import subprocess

import pytest
import test_html_artifact_validation as html

loaded_variant = html.loaded_variant
LOCAL = ('<script type="importmap">{"imports":{"three":"./three.module.js"}}</script>'
         '<script type="module">import {Scene} from "three"; new Scene();</script>')


def test_real_relative_import_map_needs_a_workspace_origin(loaded_variant, monkeypatch):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    result = loaded_variant._validate_html_artifact(LOCAL)
    assert result["browser_load"] == "unavailable" and result["browser_rendered"] is False
    assert result["browser_check_reason"] == "relative_dependencies_require_workspace_origin"


@pytest.mark.parametrize("source,error", [
    (LOCAL, 'Failed to resolve module specifier "three". Relative references must start with either "/", "./", or "../".'),
    ('<script type="module">import "./helper.js";</script>', 'Failed to resolve module specifier "./helper.js". The base URL is about:blank because import() is called from a CORS-cross-origin script.'),
    ('<script>fetch("./data.json")</script>', 'Failed to execute \'fetch\' on \'Window\': Failed to parse URL from ./data.json'),
])
def test_relative_context_is_unverified_not_success(loaded_variant, monkeypatch, source, error):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({"status": "failed", "errors": [error]}), ""))
    result = loaded_variant._validate_html_artifact(source)
    assert result["browser_load"] == "unavailable" and result["browser_rendered"] is False


@pytest.mark.parametrize("source,errors", [
    (LOCAL, ['Failed to resolve module specifier "three".', 'ReferenceError: missingVariable is not defined']),
    ('<script type="module">import {Scene} from "three";</script>', ['Failed to resolve module specifier "three".']),
    ('<script>const arbitrary="./local.js";</script>', ['Failed to resolve module specifier "three".']),
])
def test_actual_errors_and_missing_bare_imports_still_fail(loaded_variant, monkeypatch, source, errors):
    monkeypatch.setattr(loaded_variant, "_network_mode_is_online", lambda: False)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps({"status": "failed", "errors": errors}), ""))
    with pytest.raises(ValueError, match="HTML browser-load check failed"):
        loaded_variant._validate_html_artifact(source)
