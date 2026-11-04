"""User solar prompt with only the explicitly requested plane-alignment correction."""
from __future__ import annotations

from dataclasses import replace

import followup_matrix_user_solar as original

NAME = "solar_system_user_prompt_v2"
OLD_PLANE_CLAUSE = 'the whole galaxy should be relatively on the same "plane" so the solar system should also be'
NEW_PLANE_CLAUSE = "the Solar System and Galactic planes are inclined by roughly 60 degrees, not exactly aligned"
if original.PROMPT.count(OLD_PLANE_CLAUSE) != 1:
    raise ValueError("The original user prompt no longer has one unique plane clause")
PROMPT = original.PROMPT.replace(OLD_PLANE_CLAUSE, NEW_PLANE_CLAUSE, 1)
LIMITATION = (
    "The user solar prompt retains its original wording except for the user-requested roughly-60-degree "
    "Solar System/Galactic plane correction. Automatic acceptance checks only new HTML with JavaScript "
    "and preserved fixtures; rendering, interactions, visual detail and astronomy require independent review. "
    "No numerical snapshot API, fixed output filename or DOM hooks are required."
)
install_capture = original.install_capture
snapshot_text = original.snapshot_text
verify = original.verify


def catalog():
    return tuple(
        replace(case, name=NAME, turns=(replace(case.turns[0], prompt=PROMPT),))
        if case.name == original.NAME else case
        for case in original.catalog()
    )


def prepare(case, parent):
    return original.prepare(replace(case, name=original.NAME) if case.name == NAME else case, parent)
