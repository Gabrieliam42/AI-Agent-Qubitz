"""Browser subprocess checks must not depend on the caller's working directory."""
from __future__ import annotations

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant


@pytest.mark.parametrize("invalid", [False, True])
def test_actual_browser_validation_from_an_unrelated_workspace(variant, monkeypatch, tmp_path, invalid):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda: False)
    monkeypatch.chdir(tmp_path)
    script = "let = ;" if invalid else "document.title='Stock ledger';"
    source = f"<html><body><script>{script}</script></body></html>"
    if invalid:
        with pytest.raises(ValueError, match="browser-load check failed") as error:
            variant._validate_html_artifact(source)
        assert error.value.verification_context["network_policy"] == "offline_no_external_resources"
    else:
        result = variant._validate_html_artifact(source)
        assert result["browser_load"] == "passed", result
        assert result["verification_context"]["origin"] == "about:blank"
    assert not list(tmp_path.iterdir())
