"""Instantiate shared source without sharing patched classes or mutable globals."""
from __future__ import annotations

import ast
import linecache
from pathlib import Path
from typing import Any

from .source import compose_variant

DEVELOPER_WEB_NAMESPACE_FLAG = "_QUBITZ_DEVELOPER_WEB_UI"


def initialize_variant(namespace: dict[str, Any], spec: dict[str, Any]) -> None:
    developer_web = namespace.pop(DEVELOPER_WEB_NAMESPACE_FLAG, False) is True
    path = Path(namespace["__file__"]).resolve()
    source = compose_variant(spec, path.name)
    tree = ast.parse(source, filename=str(path))
    run_main = namespace.get("__name__") == "__main__"
    if run_main:
        expected = ast.parse('if __name__ == "__main__":\n    main()\n').body[0]
        if not tree.body or ast.dump(tree.body[-1]) != ast.dump(expected):
            raise ValueError("The variant's normal main guard is not recognized; dispatch was not changed.")
        # Defer only the final dispatch, not initialization or variant patches.
        tree.body.pop()
    code = compile(tree, str(path), "exec", dont_inherit=True)
    namespace.pop("_initialize_variant", None)
    namespace.pop("_QUBITZ_VARIANT_SPEC", None)
    for name in ("_qubitz_bootstrap_sys", "_QubitzBootstrapPath", "_qubitz_bootstrap_root"):
        namespace.pop(name, None)
    namespace["__doc__"] = None
    # The real launcher remains the ownership/entrypoint identity. Its effective
    # source must also remain available to traceback and inspect consumers.
    linecache.cache[str(path)] = (len(source.encode("utf-8")), None, source.splitlines(keepends=True), str(path))
    exec(code, namespace, namespace)  # noqa: S102 - trusted project source, not user/model text
    if developer_web:
        from .browser_entrypoint import install_browser_entrypoint

        install_browser_entrypoint(namespace)
    if run_main:
        namespace["main"]()
