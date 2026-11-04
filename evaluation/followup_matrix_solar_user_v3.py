"""Three-turn full-matrix solar trajectory using the corrected user prompt."""
from __future__ import annotations

from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import followup_matrix_expanded as expanded
import followup_matrix_user_solar_v2 as user_solar
from followup_matrix_cases import Check, Scenario, Turn, fixture_path

from evaluation.solar_artifact_evidence import browser_check
from evaluation.verification_outcomes import probe_issues

NAME = "solar_system_user_prompt_v3"
LIMITATION = (
    "The first solar turn uses the corrected user prompt verbatim and checks only a newly created HTML/JavaScript artifact. "
    "The later turns check an artifact edit and a tool-free limits explanation; they do not verify astronomical accuracy, "
    "visual quality, rendering or interactions. Those require independent review."
)
FIXED_ASSETS = {"three.module.js", "OrbitControls.js", "THREE-LICENSE.txt"}
WEB_SUFFIXES = (".html", ".htm", ".js", ".css", ".json", ".svg", ".png", ".jpg", ".jpeg", ".webp")


def scenario() -> Scenario:
    return Scenario(NAME, "user_prompt_graphics", {}, (
        Turn(user_solar.PROMPT, (Check("user_html_artifact"),), allow_partial=True),
        Turn(
            "In the solar-system HTML you just created, change the initial view to focus on Earth and set the initial "
            "visible animation speed to 3 (or the closest clearly labeled equivalent). Preserve the existing scene, "
            "bodies, visual content, controls, labels, animation, scientific caveats and the roughly 60-degree plane "
            "relationship. Keep the change limited to this initial focus and speed; do not edit supplied fixtures or "
            "install anything.",
            (Check("user_html_artifact_update"),),
            allow_partial=True,
        ),
        Turn(
            "Explain what you changed and which visual, interaction or scientific requirements remain unverified. "
            "Distinguish source inspection from checks actually performed. Do not use tools or edit files now.",
            (Check("no_tools"), Check("limits_report")),
        ),
    ), gui_only=True)


def catalog() -> tuple[Scenario, ...]:
    return (*expanded.catalog(), scenario())


def prepare(case: Scenario, parent: Path) -> dict[str, Path]:
    if case.name == NAME:
        return user_solar.prepare(replace(case, name=user_solar.NAME), parent)
    return expanded.prepare(case, parent)


install_capture = expanded.install_capture
def snapshot_text(root: Path, turn: Turn) -> dict:
    texts = expanded.snapshot_text(root, turn)
    if any(check.kind == "user_html_artifact_update" for check in turn.checks):
        observations = {}
        for path in sorted(root.glob("*.html")):
            try:
                observations[path.name] = browser_check(root, path, measure_only=True)
            except (OSError, ValueError, RuntimeError) as exc:
                observations[path.name] = {"status": "unavailable", "reason": str(exc)}
        texts["solar_before_renderer"] = observations
    return texts
preflight_source = expanded.preflight_source
source_unavailable = expanded.source_unavailable
only_source_blockers = expanded.only_source_blockers


class HtmlArtifact(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.has_html = False
        self.has_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.has_html |= tag == "html"
        self.has_script |= tag == "script"


def verify(
    root: Path, turn: Turn, observed: dict[str, Any], before: dict[str, str], after: dict[str, str],
    texts: dict[str, Any], receipts: dict[str, Any], *, behavior_baselines: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not any(check.kind == "user_html_artifact_update" for check in turn.checks):
        return user_solar.verify(root, turn, observed, before, after, texts, receipts,
                                 behavior_baselines=behavior_baselines)

    candidates = tuple(name for name in after if name.lower().endswith(WEB_SUFFIXES)
                       and Path(name).name not in FIXED_ASSETS)
    ordinary = replace(turn, checks=(), writable=candidates)
    issues, evidence = expanded.verify(root, ordinary, observed, before, after, texts, receipts,
                                       behavior_baselines=behavior_baselines)
    html_paths = [name for name in candidates if name.lower().endswith((".html", ".htm"))]
    changed_html = [name for name in html_paths if before.get(name) != after.get(name)]
    artifacts = []
    for name in html_paths:
        parser = HtmlArtifact()
        parser.feed(fixture_path(root, name).read_text(encoding="utf-8"))
        artifacts.append({"path": name, "sha256": after[name], "html_and_script": parser.has_html and parser.has_script,
                          "changed_this_turn": name in changed_html})
        if name in changed_html:
            baseline = texts.get("solar_before_renderer", {}).get(name, {})
            try:
                browser = browser_check(root, fixture_path(root, name), baseline=baseline.get("initial"))
            except (OSError, ValueError, RuntimeError) as exc:
                browser = {"status": "unavailable", "reason": str(exc)}
            artifacts[-1]["behavioral_evidence"] = browser
            issues.extend(probe_issues(browser, "solar_observed_behavior:" + name))
            issues.extend(probe_issues(baseline or {"status": "unavailable"},
                                       "solar_preservation_baseline:" + name, accepted="measured"))
    if not changed_html or not any(item["html_and_script"] for item in artifacts):
        issues.append("postcondition_failed:user_html_artifact_update")
    evidence.append({"check": "user_html_artifact_update", "artifacts": artifacts,
                     "full_task_success_verified": False, "limitation": LIMITATION})
    return issues, evidence
