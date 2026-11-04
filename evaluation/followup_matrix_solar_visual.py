"""Optional visual-first suite; original expanded-v5 astronomy checks remain unchanged."""
from __future__ import annotations

import json
import math
from dataclasses import replace
from urllib.parse import urlsplit

import followup_matrix_expanded as expanded
from followup_matrix_cases import Check, Scenario, Turn, prepare_scenario, write_fixture
from followup_matrix_expanded import verify as expanded_verify
from followup_matrix_solar import ASSETS

install_capture = expanded.install_capture
snapshot_text = expanded.snapshot_text

NAME = "solar_system_visual_v1"
LIMITATION = ("Visual-first solar checks cover offline WebGL, labeled bodies, motion, focus and speed controls; "
              "they do not certify ephemerides, astronomical completeness or detailed mesh orientation. "
              "Use the unchanged expanded-v5 scientific scenario for numerical accuracy checks.")
PROMPT = """Create solar_visual.html: an offline interactive 3D Solar System using the supplied
Three.js and OrbitControls.js. Start centered on the Sun. Include eight labeled planets,
Earth's Moon, detailed recognizable Earth/Moon surfaces, visible Sun lighting without
shadows, equator-aligned giant-planet rings, an ecliptic dust belt and round radial stars.
Clicking a body or label should center and track it. Animate visibly and provide pause,
selectable speed and a Sun reset. Disclose illustrative stars, visual scaling and orbital
approximations; the Galactic plane must not be described as identical to the ecliptic.
This is a visual model, not a precision ephemeris. No numerical astronomy API is required.
Use canvas#scene, input#speed (number, min=0, default=1), button#reset-sun, and clickable
labels data-body=Sun/Mercury/Venus/Earth/Moon/Mars/Jupiter/Saturn/Uranus/Neptune.
Expose read-only window.solarVisualSnapshot() returning actual renderer state:
{focus, speed, positions:{body:[x,y,z]}, cameraTarget:[x,y,z]}. Use importmap three ->
./three.module.js and three/addons/ -> ./; no remote resources or installation.
Only create solar_visual.html. Leave supplied files unchanged; do not run models.
"""


def catalog():
    return (Scenario(NAME, "browser_visual_graphics", {}, (
        Turn(PROMPT, (Check("solar_visual_browser", "solar_visual.html"),),
             ("solar_visual.html",), allow_partial=True),
        Turn("Explain the astronomical approximations and unverified visual details. "
             "Do not edit, execute or use tools now.",
             (Check("no_tools"), Check("answer", expected=r"(?i)approximat"),
              Check("answer", expected=r"(?i)unverified|not verified|not tested"))),
    ), gui_only=True),)


def prepare(case, parent):
    roots = prepare_scenario(case, parent)
    for name in ("three.module.js", "OrbitControls.js", "THREE-LICENSE.txt"):
        write_fixture(roots["a"], name, (ASSETS / name).read_text(encoding="utf-8"))
    return roots


def valid_state(state):
    bodies = {"Sun", "Mercury", "Venus", "Earth", "Moon", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"}
    positions = state.get("positions", {})
    vectors = [state.get("cameraTarget"), *(positions.get(body) for body in bodies)]
    return (state.get("focus") in bodies and isinstance(state.get("speed"), (int, float))
            and math.isfinite(state["speed"]) and state["speed"] >= 0
            and all(isinstance(v, list) and len(v) == 3 and all(
                isinstance(n, (int, float)) and math.isfinite(n) for n in v) for v in vectors))


def browser_check(root):
    from followup_solar_browser import INSTRUMENT, canvas_pixels, number_input
    from playwright.sync_api import Error, sync_playwright

    files = {name: (root / name).read_bytes() for name in
             ("solar_visual.html", "three.module.js", "OrbitControls.js", "THREE-LICENSE.txt")}
    output = root / ".cache" / "solar_visual_evidence"
    output.mkdir(parents=True, exist_ok=True)
    errors, blocked, issues, captures = [], [], [], []
    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader"])
        except Error as exc:
            return {"status": "unavailable", "reason": str(exc), "issues": []}
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
            context.add_init_script(INSTRUMENT)

            def route(request):
                url = urlsplit(request.request.url)
                name = url.path.removeprefix("/")
                if url.scheme == "http" and url.netloc == "127.0.0.1:18765" and name in files:
                    request.fulfill(status=200, body=files[name], content_type=(
                        "text/html" if name.endswith(".html") else "text/javascript"))
                else:
                    blocked.append(request.request.url)
                    request.abort()

            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.goto("http://127.0.0.1:18765/solar_visual.html", timeout=10000)
            page.wait_for_function("typeof window.solarVisualSnapshot === 'function' && window.__solarDraws > 0", timeout=10000)
            state = lambda: page.evaluate("() => window.solarVisualSnapshot()")
            initial = state()
            if not valid_state(initial) or initial.get("focus") != "Sun" or initial.get("speed") != 1:
                issues.append("initial_state")
            for body in initial.get("positions", {}):
                if not page.locator(f'[data-body="{body}"]').is_visible():
                    issues.append("missing_visible_label:" + body)
            captures.append(canvas_pixels(page, output / "initial.png"))
            number_input(page, "speed", 0)
            paused = state()
            page.wait_for_timeout(300)
            if paused.get("speed") != 0 or state().get("positions") != paused.get("positions"):
                issues.append("pause")
            page.locator('[data-body="Earth"]').click()
            page.wait_for_timeout(200)
            focused = state()
            if focused.get("focus") != "Earth" or not valid_state(focused) or any(
                abs(a-b) > 1e-5 for a, b in zip(focused["cameraTarget"], focused["positions"]["Earth"])
            ):
                issues.append("earth_focus")
            number_input(page, "speed", 3)
            moving = state()
            page.wait_for_timeout(600)
            advanced = state()
            if advanced.get("speed") != 3 or advanced.get("positions") == moving.get("positions"):
                issues.append("motion_or_speed")
            if valid_state(advanced) and any(abs(a-b) > 1e-5 for a, b in zip(
                advanced["cameraTarget"], advanced["positions"]["Earth"]
            )):
                issues.append("moving_focus_not_tracked")
            page.locator("#reset-sun").click()
            page.wait_for_timeout(200)
            if state().get("focus") != "Sun":
                issues.append("reset")
            if page.evaluate("window.__solarInvalid.length"):
                issues.append("nonfinite_webgl")
        except (Error, OSError, ValueError, TypeError, KeyError) as exc:
            issues.append(f"browser_check:{type(exc).__name__}:{exc}")
        finally:
            browser.close()
    receipt = {"status": "passed" if not issues and not errors and not blocked else "failed",
               "issues": issues, "errors": errors, "blocked_network": blocked, "captures": captures,
               "limitation": LIMITATION}
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def verify(root, turn, observed, before, after, texts, receipts, *, behavior_baselines=None):
    ordinary = replace(turn, checks=tuple(c for c in turn.checks if c.kind != "solar_visual_browser"))
    issues, evidence = expanded_verify(root, ordinary, observed, before, after, texts, receipts,
                                       behavior_baselines=behavior_baselines)
    for check in turn.checks:
        if check.kind == "solar_visual_browser":
            receipt = browser_check(root)
            evidence.append({"check": check.kind, "path": check.path, "browser_receipt": receipt})
            if receipt["status"] != "passed":
                issues.append("browser_infrastructure_unavailable" if receipt["status"] == "unavailable"
                              else "postcondition_failed:solar_visual_browser:solar_visual.html")
    return issues, evidence
