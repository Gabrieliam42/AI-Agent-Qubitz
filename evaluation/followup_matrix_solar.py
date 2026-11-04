"""Versioned solar-system trajectory and independent numerical reference checks."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

from followup_matrix_cases import Check, Scenario, Turn, fixture_path, write_fixture

FIXTURES = Path(__file__).with_name("fixtures")
REFERENCE = FIXTURES / "solar_system_reference.json"
ASSETS = FIXTURES / "solar_assets"
NAME = "solar_system_3d_followup"
CHECKS = {"solar_browser"}
LIMITATION = (
    "The solar case captures headless software-WebGL pixels, JS errors and selected interactions; "
    "numerical checks cover the declared model, not every rendered mesh, all astronomy or GPU performance. "
    "Earth/Moon surface detail, lighting, star sprites and dust aesthetics require independent visual review."
)
PROMPT = """Create solar_system.html with HTML and JavaScript as a complete interactive 3D
Solar System scene, using the supplied solar_reference.json and local Three.js files.
Use a genuine WebGL scene, not a static image or flat diagram. Include the Sun, all
eight planets, Earth's Moon, the four giant planets' equatorial rings, an illustrative
asteroid/dust belt, and all 150 supplied real catalog stars as round radial sprites.
Start centered on the Sun at JD 2451545.0 TDB, with motion at 1 simulated day per
real second. Provide orbit/zoom controls, labels beside planets and the Moon, and
clicking a body or its label must center and then track that moving body. Provide
selectable speed including pause, a Sun reset, and detailed recognizable Earth and
Moon surfaces. Light planets visibly from the Sun with disclosed artistic ambient
fill; shadows are not required. Preserve signed spin direction and the supplied
pole orientation, keep rings in each planet's equatorial plane, and place dust near
the ecliptic. Rings contain orbiting particles, not one solid rotating disc.

Astronomical scope: use JPL Table 1 elements/rates and solve Kepler's equation in
the right-handed J2000 ecliptic frame (XY is the ecliptic, Z is its north). JDTDB is
the time unit. Derive Earth and Moon from the Earth/Moon barycenter, interpolating
the supplied geocentric Moon position/velocity samples with cubic Hermite and
splitting by the supplied mass ratio. Use the supplied PCK polynomial pole/spin
model; disclose that nutation/libration and full N-body integration are omitted.
Limit the selectable epoch and animation to the supplied 35-day interval. Show
the real catalog sample at its declared epoch, rotating its ICRS directions into
the ecliptic frame. Never claim random stars are catalog data or that all stars
in the Galaxy are represented. The Galactic plane is approximately 60 degrees
from the ecliptic, NOT the same plane. Disclose visual size/distance enhancements,
approximate ephemerides, simplified attitudes, illustrative dust/surface details
and any omitted bodies. Do not claim universal or high-precision accuracy.

Keep all dependencies offline: importmap three -> ./three.module.js and
three/addons/ -> ./, with OrbitControls.js supplied locally. No installation or
remote resources. Only create solar_system.html; leave fixtures unchanged.

For repeatable verification expose these accessible UI hooks, with visible labels:
canvas#scene, input#speed (type=number, days per second, min=0), input#epoch
(type=number, Julian date TDB), button#reset-sun, and clickable labels with
data-body="Sun", "Mercury", "Venus", "Earth", "Moon", "Mars", "Jupiter",
"Saturn", "Uranus", "Neptune". Reflect epoch, speed, focus and limitations visibly.
Expose a READ-ONLY window.solarSystemSnapshot() returning a fresh JSON-safe view
of the ACTUAL current scene state, not unrelated hardcoded test answers:
{jdTdb, daysPerSecond, focus, frame, positionsAU:{body:[x,y,z]},
displayPositions:{body:[x,y,z]}, spinAxes:{body:[x,y,z]},
spinAnglesRad:{body:number}, ringNormals:{Jupiter:[x,y,z],Saturn:[x,y,z],
Uranus:[x,y,z],Neptune:[x,y,z]}, cameraTarget:[x,y,z],
starDirections:{HIP_number:[x,y,z]}, dustPlaneNormal:[x,y,z]}.
Physical coordinates and unit normals use the declared ecliptic frame; keep
physical AU calculations separate from visual scaling. Snapshot fields must
come from state used by the renderer. Do not change evaluator files or run models.
"""


def reference() -> dict[str, Any]:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def scenario() -> Scenario:
    return Scenario(NAME, "browser_scientific_graphics", {}, (
        Turn(PROMPT, (Check("solar_browser", "solar_system.html", {"focus": "Sun", "speed": 1}),),
             ("solar_system.html",), allow_partial=True),
        Turn("In qfile change ONLY the initial focus to Earth and initial speed to 3 days per second. "
             "Preserve the science, all bodies, UI hooks, clicks, reset, pause, labels and limitations. "
             "Do not edit fixtures or install anything.",
             (Check("solar_browser", "solar_system.html", {"focus": "Earth", "speed": 3}),),
             ("solar_system.html",), allow_partial=True),
        Turn("Explain which rendering/interaction checks you actually performed and which scientific "
             "or visual requirements remain unverified. Distinguish captured runtime evidence from "
             "source inspection; explicitly state the approximation limits. Do not edit, execute "
             "or use tools now.", (Check("no_tools"), Check("answer", expected=r"(?i)approximat"),
                                       Check("answer", expected=r"(?i)unverified|not verified|not tested"))),
    ), gui_only=True)


def prepare(root: Path) -> None:
    write_fixture(root, "solar_reference.json", REFERENCE.read_text(encoding="utf-8"))
    for path in sorted(ASSETS.iterdir()):
        write_fixture(root, path.name, path.read_text(encoding="utf-8"))


def kepler(elements: list[float]) -> list[float]:
    a, e, inc, lon, peri, node = elements
    mean = math.radians((lon - peri + 180) % 360 - 180)
    anomaly = mean
    for _ in range(30):
        step = (anomaly - e * math.sin(anomaly) - mean) / (1 - e * math.cos(anomaly))
        anomaly -= step
        if abs(step) < 1e-13:
            break
    x = a * (math.cos(anomaly) - e)
    y = a * math.sqrt(1 - e * e) * math.sin(anomaly)
    w, n, i = map(math.radians, (peri - node, node, inc))
    cw, sw, cn, sn, ci, si = math.cos(w), math.sin(w), math.cos(n), math.sin(n), math.cos(i), math.sin(i)
    return [(cw * cn - sw * sn * ci) * x + (-sw * cn - cw * sn * ci) * y,
            (cw * sn + sw * cn * ci) * x + (-sw * sn + cw * cn * ci) * y,
            sw * si * x + cw * si * y]


def positions(jd: float, data: dict[str, Any]) -> dict[str, list[float]]:
    if not data["jd_start_tdb"] <= jd <= data["jd_end_tdb"]:
        raise ValueError("Epoch outside the captured lunar interval")
    t = (jd - 2451545) / 36525
    result = {name: kepler([a + b * t for a, b in zip(row["elements"], row["rates_per_century"])])
              for name, row in data["planet_elements"].items()}
    samples = data["moon_geocentric"]
    index = min(len(samples) - 2, int((jd - samples[0]["jd_tdb"]) / 0.25))
    first, last = samples[index:index + 2]
    h = last["jd_tdb"] - first["jd_tdb"]
    u = (jd - first["jd_tdb"]) / h
    lunar = [(2*u**3 - 3*u**2 + 1)*a + (u**3 - 2*u**2 + u)*h*b
             + (-2*u**3 + 3*u**2)*c + (u**3 - u**2)*h*d
             for a, b, c, d in zip(first["position_au"], first["velocity_au_day"],
                                    last["position_au"], last["velocity_au_day"])]
    bary = result.pop("EarthMoonBarycenter")
    result["Earth"] = [a - b / (1 + data["earth_moon_mass_ratio"]) for a, b in zip(bary, lunar)]
    result["Moon"] = [a + b for a, b in zip(result["Earth"], lunar)]
    return {"Sun": [0.0, 0.0, 0.0], **result}


def direction(ra: float, dec: float, obliquity: float) -> list[float]:
    ra, dec, eps = map(math.radians, (ra, dec, obliquity))
    x, y, z = math.cos(dec) * math.cos(ra), math.cos(dec) * math.sin(ra), math.sin(dec)
    return [x, math.cos(eps)*y + math.sin(eps)*z, -math.sin(eps)*y + math.cos(eps)*z]


def close_vector(actual: Any, expected: list[float], tolerance: float) -> bool:
    return (isinstance(actual, list) and len(actual) == 3
            and all(type(x) in {int, float} and math.isfinite(x) for x in actual)
            and math.dist(actual, expected) <= tolerance)


def check_state(state: dict[str, Any], data: dict[str, Any]) -> list[str]:
    """Check reported live state independently; pixels/interactions are separate evidence."""
    issues = []
    try:
        jd = state["jdTdb"]
        if type(jd) not in {int, float} or not math.isfinite(jd):
            raise ValueError("Nonfinite epoch")
        expected = positions(jd, data)
        t = (jd - 2451545) / 36525
        for name, xyz in expected.items():
            if not close_vector(state["positionsAU"].get(name), xyz, 1e-7):
                issues.append("orbital_position:" + name)
            if not close_vector(state["displayPositions"].get(name), [0, 0, 0], 1e9):
                issues.append("invalid_display_position:" + name)
            row = data["body_constants"][name]
            ra = sum(x*t**i for i, x in enumerate(row["pole_ra_polynomial_deg"]))
            dec = sum(x*t**i for i, x in enumerate(row["pole_dec_polynomial_deg"]))
            axis = direction(ra, dec, data["obliquity_j2000_deg"])
            if not close_vector(state["spinAxes"].get(name), axis, 1e-5):
                issues.append("spin_axis:" + name)
            d = jd - 2451545
            spin = math.radians(sum(x*d**i for i, x in enumerate(row["prime_meridian_deg_day"])))
            actual = state["spinAnglesRad"].get(name)
            if type(actual) not in {float, int} or not math.isfinite(actual) or abs(math.remainder(actual-spin, 2*math.pi)) > 1e-4:
                issues.append("spin_angle:" + name)
            if name in {"Jupiter", "Saturn", "Uranus", "Neptune"}:
                normal = state["ringNormals"].get(name)
                if not (close_vector(normal, axis, 1e-5) or close_vector(normal, [-x for x in axis], 1e-5)):
                    issues.append("ring_plane:" + name)
        for star in data["bright_stars"]:
            target = direction(star["ra_deg"], star["dec_deg"], data["obliquity_j2000_deg"])
            if not close_vector(state["starDirections"].get(str(star["hip"])), target, 1e-5):
                issues.append("catalog_star:" + str(star["hip"]))
        if not (close_vector(state["dustPlaneNormal"], [0, 0, 1], 1e-5)
                or close_vector(state["dustPlaneNormal"], [0, 0, -1], 1e-5)):
            issues.append("dust_plane")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        issues.append("invalid_snapshot:" + str(exc))
    return issues


def browser_check(root: Path, observed: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    path = fixture_path(root, "solar_system.html")
    if not path.is_file():
        return {"status": "failed", "issues": ["missing_html"]}
    output = root.parent.parent / "solar_browser" / f"turn_{observed['turn']:03}"
    output.mkdir(parents=True, exist_ok=False)
    command = [sys.executable, str(Path(__file__).with_name("followup_solar_browser.py")),
               "--workspace", str(root), "--output", str(output),
               "--focus", expected["focus"], "--speed", str(expected["speed"])]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=90, check=False)
        receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
        if run.returncode != 0 and receipt.get("status") == "passed":
            receipt.update(status="failed", issues=["probe_exit_mismatch"])
        receipt.update(command=command, returncode=run.returncode, stdout=run.stdout[-4000:], stderr=run.stderr[-4000:])
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        receipt = {"status": "harness_error", "issues": ["browser_probe_error"], "error": repr(exc), "command": command}
    receipt.update(output=str(output), artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                   independent_visual_review="required", limitation=LIMITATION)
    return receipt


def browser_preflight(output: Path) -> dict[str, Any]:
    path = output / "solar_browser_preflight"
    path.mkdir(exist_ok=False)
    command = [sys.executable, str(Path(__file__).with_name("followup_solar_browser.py")),
               "--preflight", "--output", str(path)]
    try:
        run = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        receipt = json.loads((path / "receipt.json").read_text(encoding="utf-8"))
        if run.returncode != 0:
            receipt["status"] = "unavailable"
        return {**receipt, "command": command, "returncode": run.returncode, "stderr": run.stderr[-2000:]}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return {"status": "unavailable", "reason": repr(exc), "command": command}
