"""Typed, read-only interpretations of captured process and completion evidence."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WindowsProcessIdentity:
    pid: int
    creation_ticks: str
    executable: str
    command_line: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WindowsProcessIdentity:
        if not isinstance(raw, Mapping):
            raise ValueError("Process identity must be an object")  # noqa: TRY004 - one schema rejection contract
        pid, ticks = raw.get("pid"), raw.get("creation_ticks")
        executable, command = raw.get("executable"), raw.get("command_line")
        if (type(pid) is not int or pid <= 0 or not isinstance(ticks, str)
                or not ticks.isascii() or not ticks.isdigit() or int(ticks) <= 0
                or not isinstance(executable, str) or not executable.strip()
                or not isinstance(command, str) or not command.strip()):
            raise ValueError("Process identity requires exact PID, CIM ticks, executable and arguments")
        return cls(pid, ticks, executable, command)

    def matches(self, other: WindowsProcessIdentity) -> bool:
        return (self.pid == other.pid and self.creation_ticks == other.creation_ticks
                and self.executable == other.executable
                and self.command_line.rstrip() == other.command_line.rstrip())


@dataclass(frozen=True)
class ChainCompletionReceipt:
    turn_numbers: tuple[int, ...]
    turn_statuses: tuple[str, ...]
    finished: str | None
    exit_code: int | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> ChainCompletionReceipt:
        turns = raw.get("turns", [])
        if not isinstance(turns, list) or any(
            not isinstance(turn, dict) or type(turn.get("turn")) is not int
            or not isinstance(turn.get("status"), str) for turn in turns
        ):
            raise ValueError("Completion receipt requires explicit turn numbers and statuses")
        finished, exit_code = raw.get("finished"), raw.get("exit_code")
        return cls(tuple(turn["turn"] for turn in turns), tuple(turn["status"] for turn in turns),
                   finished if isinstance(finished, str) and finished.strip() else None,
                   exit_code if type(exit_code) is int else None)

    def pass_issues(self, planned_turns: int, *, finalized: bool) -> tuple[str, ...]:
        issues = []
        if finalized and self.exit_code != 0:
            issues.append("passed_receipt_with_nonzero_exit")
        if (self.turn_numbers != tuple(range(1, planned_turns + 1))
                or any(status != "passed" for status in self.turn_statuses)):
            issues.append("incomplete_chain_cannot_pass")
        if finalized and self.finished is None:
            issues.append("unfinished_child_cannot_pass")
        return tuple(issues)


def chain_pass_issues(raw: Mapping[str, Any], planned_turns: int, *, finalized: bool) -> tuple[str, ...]:
    try:
        return ChainCompletionReceipt.from_mapping(raw).pass_issues(planned_turns, finalized=finalized)
    except ValueError:
        return ("incomplete_chain_cannot_pass",)
