"""Stateless user-owned cwd contracts, composed into private variant namespaces."""
import re as _execution_scope_re


def _qubitz_entrypoint_cwd_contract(broker, arguments, *, segments, entrypoints, normalize_token, is_negated):
    """Resolve explicit execution directories; model arguments never create scope."""
    root = broker.workspace.resolve()
    token = r'''(?P<path>`[^`\r\n]+`|"[^"\r\n]+"|'[^'\r\n]+'|[^\s,;!?]+)'''
    patterns = (
        r"(?<![\w-])(?:cwd|working\s+directory)(?=\s|:|=)\s*(?:(?:[:=]|\b(?:to|of|is)\b)\s*)?" + token,
        r"\buse\s+" + token + r"\s+as\s+(?:the\s+)?(?:cwd|working\s+directory)\b",
        r"\b(?:from|in)\s+(?:the\s+)?(?:working\s+)?directory\s+" + token,
    )
    requested = set()
    unresolved = False
    quote_pattern = r'''(?<!\w)(?:`[^`\r\n]*`|"[^"\r\n]*"|'[^'\r\n]*')'''
    prompt = broker.current_prompt
    # Preserve offsets while hiding verbs inside quoted filenames and literals.
    literal_masked = _execution_scope_re.sub(quote_pattern, lambda m: m[0][0] + "_" * (len(m[0]) - 2) + m[0][-1], prompt)
    masked = literal_masked
    for pattern in patterns:
        for match in _execution_scope_re.finditer(pattern, prompt, _execution_scope_re.IGNORECASE):
            start, end = match.span("path")
            value = _execution_scope_re.sub(r"\w", "_", masked[start:end])
            masked = masked[:start] + value + masked[end:]
    cursor = 0
    for verb, masked_segment, act, negated in segments(masked):
        start = masked.find(masked_segment, cursor)
        if start < 0:
            return {"error": "Working-directory instruction segmentation is ambiguous.", "explicit": True}
        segment = prompt[start:start + len(masked_segment)]
        cursor = start + len(masked_segment)
        if act != "request" or negated or verb not in {
            "run", "rerun", "execute", "start", "launch", "test", "verify", "validate", "check", "lint", "use", "set",
        }:
            continue
        quotes = list(_execution_scope_re.finditer(quote_pattern, segment))
        matches = [m for pattern in patterns for m in _execution_scope_re.finditer(pattern, segment, _execution_scope_re.IGNORECASE)
                   if not any(q.start() <= m.start() < q.end() for q in quotes)
                   and not is_negated(masked_segment, m.start())
                   and not _execution_scope_re.search(r"\b(?:current|active)\s*$", masked_segment[:m.start()], _execution_scope_re.IGNORECASE)]
        signals = _execution_scope_re.finditer(
            r"\b(?:with|using|use|set|from|in)\b[^;\r\n]{0,160}\b(?:cwd|working\s+directory)\b"
            r"|(?<![\w-])cwd\s*[:=]", masked_segment, _execution_scope_re.IGNORECASE,
        )
        signal = any(not is_negated(masked_segment, m.start()) for m in signals)
        current_workspace = bool(_execution_scope_re.search(
            r"\b(?:current|active)\s+(?:workspace|working\s+directory)\b|\bworkspace\s+root\b",
            literal_masked[start:start + len(masked_segment)], _execution_scope_re.IGNORECASE,
        ))
        if not matches and not signal:
            continue
        references = entrypoints(segment)
        if references:
            try:
                selected = broker.base.resolve_workspace_path(
                    root, str(arguments.get("path") or ""), allow_missing=True, allow_external=broker.access_mode == "full",
                ).resolve()
                referenced = {broker.base.resolve_workspace_path(
                    root, normalize_token(p), allow_missing=True, allow_external=broker.access_mode == "full",
                ).resolve() for p in references}
            except (OSError, ValueError):
                return {"error": "Cannot bind the requested entrypoint working directory.", "explicit": True}
            if selected not in referenced:
                continue
        if current_workspace and (not matches or all(m["path"].strip().casefold() in {"the", "active", "current", "workspace"} for m in matches)):
            requested.add(root)
            continue
        if not matches:
            unresolved = True
            continue
        for match in matches:
            raw = match["path"].strip()
            quoted = raw.startswith(("`", '"', "'"))
            raw = raw.strip("`\"'") if quoted else raw.rstrip(".!?;") if raw not in {".", ".."} else raw
            try:
                path = broker.base.resolve_workspace_path(
                    root, raw, allow_missing=False, allow_external=broker.access_mode == "full",
                ).resolve()
            except (OSError, ValueError):
                unresolved = True
                continue
            if not raw or not path.is_dir():
                unresolved = True
            else:
                requested.add(path)
    if unresolved or len(requested) > 1:
        return {"error": "The user-selected working directory is missing, invalid or ambiguous; no default substitution is allowed.",
                "explicit": True}
    return {"cwd": next(iter(requested)) if requested else root,
            "explicit": bool(requested), "source": "explicit user instruction" if requested else "active workspace default"}
