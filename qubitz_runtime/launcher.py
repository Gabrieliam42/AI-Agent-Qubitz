"""Launch one selected variant through its existing normal GUI/CLI entrypoint."""
from __future__ import annotations

import argparse
import runpy
import sys
from collections.abc import Sequence

from .loader import DEVELOPER_WEB_NAMESPACE_FLAG
from .paths import DEFAULT_VARIANT, PROJECT_ROOT, variant_path, variant_paths
from .source import compose_variant, launcher_spec
from .variant_policy import require_user_variants_allowed


def main(argv: Sequence[str] | None = None, *, developer_web: bool = False) -> int:
    parser = argparse.ArgumentParser(
        description=("Open the experimental developer browser interface using a public variant."
                     if developer_web else "Open the original Qubitz Tkinter GUI using one variant from variants/."),
        epilog="Other options are forwarded unchanged to the selected variant; use its --help directly for those options.",
        allow_abbrev=False,
    )
    parser.add_argument("--variant", default=DEFAULT_VARIANT, metavar="BASENAME",
                        help="Exact variant filename; default: %(default)s")
    parser.add_argument("--list-variants", action="store_true", help="List available variant filenames without loading them.")
    args, forwarded = parser.parse_known_args(argv)
    try:
        if args.list_variants:
            paths = variant_paths(PROJECT_ROOT)
            if not paths:
                parser.error("No variants were found in variants/.")
            print("\n".join(path.name for path in paths))
            return 0
        if developer_web:
            require_user_variants_allowed([args.variant])
        target = variant_path(args.variant, PROJECT_ROOT)
        if not target.is_file():
            parser.error(f"Variant is unavailable: {args.variant}")
        spec = launcher_spec(target.read_text(encoding="utf-8"))
        if spec is None or spec.get("filename") != target.name:
            parser.error(f"Variant does not contain its matching declarative specification: {target.name}")
        compose_variant(spec, target.name)
    except (OSError, ValueError, SyntaxError, TypeError) as exc:
        parser.error(str(exc))
    if not any(value == "--workspace" or value.startswith("--workspace=") for value in forwarded):
        forwarded.extend(("--workspace", str(PROJECT_ROOT)))
    original_argv = sys.argv
    try:
        # run_path preserves the actual variant __file__/main identity and keeps
        # normal ownership, startup, and close handling inside the selected app.
        sys.argv = [str(target), *forwarded]
        if developer_web:
            runpy.run_path(str(target), run_name="__main__",
                           init_globals={DEVELOPER_WEB_NAMESPACE_FLAG: True})
        else:
            runpy.run_path(str(target), run_name="__main__")
    finally:
        sys.argv = original_argv
    return 0
