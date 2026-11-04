"""Exact user-prompt experiment, separate from both existing solar scenarios."""
from __future__ import annotations

from dataclasses import replace
from html.parser import HTMLParser

import followup_matrix_expanded as expanded
from followup_matrix_cases import (
    Check,
    Scenario,
    Turn,
    fixture_path,
    source_fidelity_scenarios,
    strict_behavior_scenarios,
    write_fixture,
)
from followup_matrix_solar import ASSETS

install_capture = expanded.install_capture
snapshot_text = expanded.snapshot_text
NAME = "solar_system_user_prompt_v1"
LIMITATION = (
    "The user solar prompt is retained verbatim, including its conflicting Galactic/ecliptic-plane premise. "
    "Automatic solar acceptance checks only newly created HTML with JavaScript and preserved fixtures; "
    "3D rendering, interactions, visual detail and astronomical accuracy require independent review. "
    "No old numerical snapshot API, fixed output filename or DOM hooks are required."
)
PROMPT = 'create a html with javascript as an interactive complete and accurate model of our solar system as a 3D scene , astronomically  accurate, the default view could center the Sun , the Earth and its Moon should be highly detailed, the whole galaxy should be relatively on the same "plane" so the solar system should also be, everything should be detailed and ligthed properly although shadows are not needed, planet rings and dust disks should also be oriented and rotating correctly in relation with the plane, there should be a lot of stars everywhere where they are in reality, the stars should be round and radial , the planets should be ligthed properly to be visible, and the planets should have labels next to them, and when the user clicks on a planet the view should change to center that planet, the planets speed can be slower but visibly moving, the user should be able to select the speed'


def catalog():
    cases = source_fidelity_scenarios(strict_behavior_scenarios(list(expanded.catalog())))
    mcp = next(case for case in cases if case.name == "mcp_result_to_file_chain")
    solar = Scenario(NAME, "user_prompt_graphics", {}, (
        Turn(PROMPT, (Check("user_html_artifact"),), allow_partial=True),
    ), gui_only=True)
    return mcp, solar


def prepare(case, parent):
    roots = expanded.prepare(case, parent)
    if case.name == NAME:
        for name in ("three.module.js", "OrbitControls.js", "THREE-LICENSE.txt"):
            write_fixture(roots["a"], name, (ASSETS / name).read_text(encoding="utf-8"))
    return roots


class HtmlArtifact(HTMLParser):
    def __init__(self):
        super().__init__()
        self.html = False
        self.script = False

    def handle_starttag(self, tag, attrs):
        self.html |= tag == "html"
        self.script |= tag == "script"


def verify(root, turn, observed, before, after, texts, receipts, *, behavior_baselines=None):
    if not any(check.kind == "user_html_artifact" for check in turn.checks):
        return expanded.verify(root, turn, observed, before, after, texts, receipts,
                               behavior_baselines=behavior_baselines)
    # The prompt does not prescribe names; allow new web assets, never fixture rewrites.
    allowed = tuple(name for name in after if name not in before and name.lower().endswith(
        (".html", ".htm", ".js", ".css", ".json", ".png", ".jpg", ".jpeg", ".webp", ".svg")))
    ordinary = replace(turn, checks=(), writable=allowed)
    issues, evidence = expanded.verify(root, ordinary, observed, before, after, texts, receipts,
                                       behavior_baselines=behavior_baselines)
    artifacts = []
    for name in allowed:
        if name.lower().endswith((".html", ".htm")):
            parser = HtmlArtifact()
            parser.feed(fixture_path(root, name).read_text(encoding="utf-8"))
            artifacts.append({"path": name, "sha256": after[name],
                              "html_and_script": parser.html and parser.script})
    if not any(item["html_and_script"] for item in artifacts):
        issues.append("postcondition_failed:user_html_artifact")
    evidence.append({"check": "user_html_artifact", "artifacts": artifacts,
                     "full_task_success_verified": False, "limitation": LIMITATION})
    return issues, evidence
