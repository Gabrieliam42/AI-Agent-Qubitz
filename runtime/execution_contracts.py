"""Conservative ordering of already-resolved explicit execution requests."""
from __future__ import annotations

import re


def ordered_entrypoints(prompt: str, paths: tuple[str, ...]) -> tuple[str, ...] | None:
    if len(paths) < 2 or not re.search(r"\b(?:then|before|after)\b", prompt, re.IGNORECASE):
        return ()
    spans = [re.search(re.escape(path), prompt, re.IGNORECASE) for path in paths]
    if any(span is None for span in spans):
        return None
    if len(paths) == 2:
        between = prompt[spans[0].end():spans[1].start()]
        prefix = prompt[:spans[0].start()]
        if re.search(r"\bafter\b", between, re.IGNORECASE) or re.search(r"\bbefore\s+(?:running|executing|we\s+run)\s*$", prefix, re.IGNORECASE):
            return tuple(reversed(paths))
        return paths
    between = prompt[spans[0].end():spans[-1].start()]
    return None if re.search(r"\b(?:before|after)\b", between, re.IGNORECASE) else paths
