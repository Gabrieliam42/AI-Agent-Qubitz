"""Classify executed Python entrypoints, never strings inside -c or program arguments."""
from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath


def python_entrypoint(command: str, *, windows: bool = False) -> str | None:
    try:
        words = [word.strip('"') for word in shlex.split(command, posix=not windows)]
    except ValueError:
        return None
    if not words or not re.fullmatch(r"python(?:w|3(?:\.\d+)?)?(?:\.exe)?",
                                    words[0].replace("\\", "/").rsplit("/", 1)[-1], re.IGNORECASE):
        return None
    index = 1
    while index < len(words):
        word = words[index]
        if word in {"-c", "-m", "-"} or word.startswith(("-c", "-m")):
            return None
        if (re.fullmatch(r"-[bBdEiIOqRsuUvVxcm]+", word)
                and any(flag in word[1:] for flag in "cm")):
            return None
        if word == "--":
            index += 1
            break
        if word in {"-X", "-W", "--check-hash-based-pycs"}:
            index += 2
        elif word.startswith("-"):
            index += 1
        else:
            break
    return words[index].replace("\\", "/") if index < len(words) else None


def is_qubitz_worker(command: str, *, windows: bool = False) -> bool:
    path = python_entrypoint(command, windows=windows)
    name = PurePosixPath(path).name if path else ""
    return bool(re.fullmatch(r"AI_Agent_Qubitz_.+\.py|AI Agent Qubitz(?: Web Dev)?\.py|qubitz_disposable_eval\.py|tmp_smoke_variant\.py", name, re.IGNORECASE))
