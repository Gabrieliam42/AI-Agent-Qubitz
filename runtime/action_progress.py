"""Bounded per-turn progress accounting; never authorizes or executes an action."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


class ActionProgress:
    """Compare exact observed states, independently of model narration or retry layers."""

    def __init__(self, prompt: str, workspace: Path) -> None:
        self.workspace = workspace.resolve()
        self.hashes: dict[str, set[str]] = {}
        self.executions: Counter[str] = Counter()
        self.recoveries: set[str] = set()
        unquoted = re.sub(r"```.*?```|`[^`]*`|\"[^\"]*\"|'[^']*'", "", prompt, flags=re.DOTALL)
        self.explicit_revisit = False
        for clause in re.split(r"[;\n]+|[.!?](?=\s)|\bthen\b", unquoted, flags=re.IGNORECASE):
            if re.search(r"\b(?:do\s+not|don't|never|without|explain|says?|quoted?)\b", clause, re.IGNORECASE):
                continue
            if re.search(r"^\s*(?:please\s+)?(?:undo|revert|restore|repeat)\b", clause, re.IGNORECASE):
                self.explicit_revisit = True

    def key(self, path: str) -> str:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        return str(candidate.resolve())

    def observe_hash(self, path: str, digest: str) -> None:
        key = self.key(path)
        if key not in self.hashes and len(self.hashes) >= 64:
            return
        history = self.hashes.setdefault(key, set())
        if len(history) < 32:
            history.add(digest)

    def patch_cycle(self, path: str, before: str, after: str) -> bool:
        return bool(not self.explicit_revisit and before != after
                    and after in self.hashes.get(self.key(path), set()))

    def reserve_execution(self, name: str, arguments: dict, state: tuple | None) -> bool:
        if self.explicit_revisit or state is None:
            return True
        fingerprint = hashlib.sha256(json.dumps(
            [name, arguments, state], sort_keys=True, ensure_ascii=True, default=str,
        ).encode()).hexdigest()
        if self.executions[fingerprint] >= 2:
            return False
        if len(self.executions) >= 128 and fingerprint not in self.executions:
            # An untracked state is not proof of a repeated execution.
            return True
        self.executions[fingerprint] += 1
        return True

    def reserve_recovery(self, phase: str) -> bool:
        if phase in self.recoveries or len(self.recoveries) >= 2:
            return False
        self.recoveries.add(phase)
        return True

    def evidence(self) -> dict:
        return {"recovery_phases_used": sorted(self.recoveries),
                "recovery_phases_remaining": max(0, 2 - len(self.recoveries)),
                "observed_target_count": len(self.hashes)}
