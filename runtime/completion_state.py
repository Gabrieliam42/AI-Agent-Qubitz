"""One authoritative, evidence-derived completion state for display and capture."""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CompletionState:
    required: tuple[str, ...]
    verified: tuple[str, ...]
    unverified: tuple[str, ...]

    @property
    def status(self) -> str:
        return "partial" if self.unverified else "verified" if self.required else "not_applicable"

    def receipt(self) -> dict:
        return {**asdict(self), "status": self.status,
                "scope": "Only current-turn recorded obligations; no universal correctness claim."}

    def message(self) -> str:
        if self.unverified:
            return "Completion verification is incomplete: " + ", ".join(self.unverified) + "."
        if self.required:
            return "Completion verification passed for the requested postconditions."
        return "Response completed; no action postconditions were requested."


def completion_state(required, missing, *, unverified=()) -> CompletionState:
    def labels(values):
        return {str(value).replace("_", " ") for value in values}
    pending = labels(missing) | labels(unverified)
    obligations = labels(required) | pending
    return CompletionState(tuple(sorted(obligations)), tuple(sorted(obligations - pending)), tuple(sorted(pending)))
