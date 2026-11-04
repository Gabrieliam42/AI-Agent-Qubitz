"""Classify evaluator evidence independently of task assertions and interruption."""
from __future__ import annotations


def probe_issues(receipt: dict, target: str, *, accepted: str = "passed") -> list[str]:
    status = receipt.get("status")
    if status == accepted:
        return []
    if status == "failed":
        return ["postcondition_failed:" + target]
    if status == "unavailable":
        return ["evidence_unavailable:" + target]
    return ["evaluator_error:" + target]


def verification_status(issues: list[str], *, interrupted: bool = False) -> str:
    if interrupted:
        return "interrupted"
    if any(issue.startswith("evaluator_error:") for issue in issues):
        return "harness_error"
    unavailable = lambda issue: issue.startswith("evidence_unavailable:") or issue == "browser_infrastructure_unavailable"
    if issues and all(unavailable(issue) for issue in issues):
        return "blocked"
    return "failed" if issues else "passed"
