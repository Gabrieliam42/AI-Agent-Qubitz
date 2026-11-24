"""Single-turn consent for replacing one existing named workspace file."""
from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path


def overwrite_reply(prompt: str) -> bool | None:
    """Only standalone replies may consume the pending operation as consent."""
    reply = " ".join(prompt.casefold().strip().rstrip(".!").split())
    if re.fullmatch(
        r"(?:yes(?:,? (?:please(?: overwrite it)?|overwrite it|go ahead))?|"
        r"overwrite it|go ahead|confirm(?:ed)?|approve(?:d)?|proceed)", reply,
    ):
        return True
    if re.fullmatch(r"(?:no(?:,? (?:thanks|do not overwrite it))?|cancel|do not overwrite it|don't overwrite it)", reply):
        return False
    return None


def looks_like_overwrite_reply(prompt: str) -> bool:
    return bool(re.match(
        r"\s*(?:yes|no|approved?|confirm(?:ed)?|go ahead|proceed|overwrite it)\b", prompt, re.IGNORECASE,
    ))


def _revision(workspace: Path, target: Path) -> tuple[int, ...] | None:
    try:
        if target == workspace or not target.is_relative_to(workspace) or target.resolve(strict=True) != target:
            return None
        info = target.lstat()
    except (OSError, ValueError, RuntimeError):
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


@dataclass(frozen=True)
class PendingOverwrite:
    workspace: Path
    target: Path
    original_prompt: str
    revision: tuple[int, ...]

    @classmethod
    def capture(cls, workspace: Path, target: Path, prompt: str) -> PendingOverwrite | None:
        root = workspace.resolve()
        revision = _revision(root, target)
        return cls(root, target, prompt, revision) if revision is not None else None

    def matches(self, workspace: Path) -> bool:
        return workspace.resolve() == self.workspace and _revision(self.workspace, self.target) == self.revision

    @property
    def patch_tool(self) -> str:
        return {
            ".docx": "apply_docx_text_patch",
            ".ipynb": "apply_notebook_text_patch",
            ".xlsx": "apply_spreadsheet_cell_patch",
            ".xlsm": "apply_spreadsheet_cell_patch",
        }.get(self.target.suffix.casefold(), "apply_text_patch")
