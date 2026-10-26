"""Opt-in developer browser GUI; CLI, MCP and maintenance retain their entrypoint."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .variant_policy import user_variant_allowed


def install_browser_entrypoint(namespace: dict[str, Any]) -> None:
    app = namespace.get("_APP")
    if app is None or not user_variant_allowed(Path(namespace["__file__"]).name):
        return
    original = namespace["main"]

    def main(argv: list[str] | None = None) -> None:
        import sys

        raw = list(sys.argv[1:] if argv is None else argv)
        options = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        options.add_argument("--ui-port", type=int, default=0)
        options.add_argument("--no-browser", action="store_true")
        ui, remaining = options.parse_known_args(raw)
        if any(flag in remaining for flag in (
            "--cli", "--serve-mcp", "--generate-harness-key", "--encrypt-harness", "--help", "-h",
        )):
            return original(remaining)
        if not 0 <= ui.ui_port <= 65535:
            raise SystemExit("--ui-port must be between 0 and 65535.")
        args = app.parse_args(remaining)
        # Parsing remains owned by the selected variant, including its defaults.
        if any(getattr(args, name, False) for name in (
            "cli", "serve_mcp", "generate_harness_key", "encrypt_harness",
        )):
            return original(remaining)
        namespace["_enable_local_only_environment"]()
        app.base.configure_project_environment(app.runtime_workspace)
        config = app._build_config(args, Path(args.workspace).resolve())
        config.access_mode = os.environ.get(namespace["ACCESS_MODE_ENV_NAME"], namespace["DEFAULT_ACCESS_MODE"])
        config.network_mode = os.environ.get(namespace["NETWORK_MODE_ENV_NAME"], namespace["DEFAULT_NETWORK_MODE"])
        config.show_reasoning = True
        from .web_api import serve_browser

        serve_browser(namespace, config, port=ui.ui_port, open_browser=not ui.no_browser)

    app.main = main
    namespace["main"] = main

