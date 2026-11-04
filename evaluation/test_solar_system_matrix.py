"""CPU reference/oracle tests and real software-WebGL regression probes; no LLMs."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import asdict, replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_followup_matrix as matrix
import followup_matrix_expanded as expanded
import followup_matrix_solar as solar
from followup_matrix_cases import workspace_manifest, write_fixture


def state(jd=2451545.0):
    data = solar.reference()
    result = {"jdTdb": jd, "positionsAU": solar.positions(jd, data), "displayPositions": solar.positions(jd, data),
              "spinAxes": {}, "spinAnglesRad": {}, "ringNormals": {}, "starDirections": {}, "dustPlaneNormal": [0, 0, 1]}
    t, d = (jd-2451545)/36525, jd-2451545
    for name, row in data["body_constants"].items():
        ra = sum(x*t**i for i, x in enumerate(row["pole_ra_polynomial_deg"]))
        dec = sum(x*t**i for i, x in enumerate(row["pole_dec_polynomial_deg"]))
        result["spinAxes"][name] = solar.direction(ra, dec, data["obliquity_j2000_deg"])
        result["spinAnglesRad"][name] = math.radians(sum(x*d**i for i, x in enumerate(row["prime_meridian_deg_day"])))
    for name in ("Jupiter", "Saturn", "Uranus", "Neptune"):
        result["ringNormals"][name] = result["spinAxes"][name].copy()
    for star in data["bright_stars"]:
        result["starDirections"][str(star["hip"])] = solar.direction(star["ra_deg"], star["dec_deg"], data["obliquity_j2000_deg"])
    return result


def test_new_version_extends_v4_without_rewriting_old_contracts():
    old = matrix.selected_scenarios(None, "expanded-v4")
    new = matrix.selected_scenarios(None, "expanded-v5")
    assert new[:-1] == old and len(new) == 27
    assert new[-1].name == solar.NAME and len(new[-1].turns) == 3
    assert len(matrix.selected_scenarios(None, "expanded-v3")) == 26
    assert solar.NAME not in {case.name for case in expanded.catalog()}
    assert all(case.gui_only for case in new)
    assert len(matrix.ALL_VARIANTS)*len(new) == 1080
    assert len(matrix.ALL_VARIANTS)*sum(len(case.turns) for case in new) == 3520
    assert "solar_reference.json" not in new[-1].turns[0].writable


def test_dry_plan_does_not_start_models_or_browser(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["matrix", "--suite", "expanded-v5", "--gui", "--scenarios", solar.NAME])
    monkeypatch.setattr(matrix, "coordinate", lambda *a: pytest.fail("Started live campaign"))
    monkeypatch.setattr(solar, "browser_check", lambda *a: pytest.fail("Started browser"))
    assert matrix.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["execution_started"] is False and result["trajectory_count"] == 40 and result["turn_count"] == 120
    assert solar.LIMITATION in result["limitations"]


def test_reference_is_frozen_in_source_contract():
    frozen = matrix.freeze_sources()
    assert frozen["evaluation/fixtures/solar_system_reference.json"] == hashlib.sha256(solar.REFERENCE.read_bytes()).hexdigest()
    assert "evaluation/fixtures/solar_assets/three.module.js" in frozen
    data = solar.reference()
    assert len(data["planet_elements"]) == 8 and len(data["body_constants"]) == 10
    assert len(data["bright_stars"]) == 150 and len(data["moon_geocentric"]) == 141
    assert len({star["hip"] for star in data["bright_stars"]}) == 150
    assert data["jd_end_tdb"] - data["jd_start_tdb"] == 35
    assert data["body_constants"]["Venus"]["prime_meridian_deg_day"][1] < 0
    assert data["body_constants"]["Uranus"]["prime_meridian_deg_day"][1] < 0
    assert 81.29 < data["earth_moon_mass_ratio"] < 81.31


@pytest.mark.parametrize("jd", [2451545, 2451545.125, 2451552.125, 2451573.75, 2451580])
def test_state_and_lunar_endpoints(jd):
    data = solar.reference()
    assert solar.check_state(state(jd), data) == []
    xyz = solar.positions(jd, data)
    moon = [a-b for a, b in zip(xyz["Moon"], xyz["Earth"])]
    assert 0.0023 < math.dist(moon, [0, 0, 0]) < 0.0028
    if jd in {2451545, 2451580}:
        expected = data["moon_geocentric"][0 if jd == 2451545 else -1]["position_au"]
        assert math.dist(moon, expected) < 1e-15


def test_kepler_reference_axes_are_not_circular_or_coplanar_assumptions():
    assert solar.kepler([1, 0, 0, 0, 0, 0]) == [1, 0, 0]
    assert solar.close_vector(solar.kepler([1, 0, 90, 90, 0, 0]), [0, 0, 1], 1e-15)
    assert solar.close_vector(solar.kepler([1, .2, 0, 0, 0, 0]), [.8, 0, 0], 1e-15)
    assert solar.close_vector(solar.kepler([1, .2, 0, 180, 0, 0]), [-1.2, 0, 0], 1e-15)
    assert solar.close_vector(solar.direction(0, 90, 23.43928), [0, .397776978, .917482139], 1e-8)


@pytest.mark.parametrize("field,key,expected", [
    ("positionsAU", "Mercury", "orbital_position:Mercury"),
    ("positionsAU", "Earth", "orbital_position:Earth"),
    ("positionsAU", "Moon", "orbital_position:Moon"),
    ("spinAxes", "Uranus", "spin_axis:Uranus"),
    ("ringNormals", "Saturn", "ring_plane:Saturn"),
    ("starDirections", "32349", "catalog_star:32349"),
])
def test_scientific_oracle_rejects_plausible_but_wrong_vectors(field, key, expected):
    current = state()
    current[field][key] = [0, 0, 0]
    assert expected in solar.check_state(current, solar.reference())


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), True, "2451545", 2451581, 2451544])
def test_bad_epoch_never_passes(bad):
    current = state()
    current["jdTdb"] = bad
    assert solar.check_state(current, solar.reference())


def test_negative_spin_and_earth_barycenter_must_not_be_substituted():
    data = solar.reference()
    current = state(2451546.125)
    current["spinAnglesRad"]["Venus"] *= -1
    current["positionsAU"]["Earth"] = solar.kepler(data["planet_elements"]["EarthMoonBarycenter"]["elements"])
    issues = solar.check_state(current, data)
    assert "spin_angle:Venus" in issues and "orbital_position:Earth" in issues


def test_protected_fixture_mutation_fails_even_with_passed_browser(tmp_path, monkeypatch):
    roots = expanded.prepare(solar.scenario(), tmp_path / "fixtures")
    root = roots["a"]
    before = workspace_manifest(root)
    write_fixture(root, "solar_reference.json", "{}")
    monkeypatch.setattr(solar, "browser_check", lambda *a: {"status": "passed"})
    turn = solar.scenario().turns[0]
    issues, _ = expanded.verify(root, turn, {"turn": 1}, before, workspace_manifest(root), {}, {})
    assert "unexpected_mutation:solar_reference.json" in issues


@pytest.mark.parametrize("status,expected", [("passed", []), ("failed", ["postcondition_failed:solar_browser:solar_system.html"]),
                                            ("unavailable", ["browser_infrastructure_unavailable"])])
def test_browser_availability_is_not_an_agent_failure(tmp_path, monkeypatch, status, expected):
    turn = replace(solar.scenario().turns[0], writable=())
    monkeypatch.setattr(solar, "browser_check", lambda *a: {"status": status})
    issues, evidence = expanded.verify(tmp_path, turn, {"turn": 1}, {}, {}, {}, {})
    assert issues == expected and evidence[-1]["browser_receipt"]["status"] == status


@pytest.fixture(scope="module")
def chromium():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader"])
        browser.close()


def test_real_browser_accepts_rendered_interactions_and_preserves_artifact(tmp_path, chromium):
    roots = expanded.prepare(solar.scenario(), tmp_path / "fixtures")
    root = roots["a"]
    content = (solar.FIXTURES / "solar_probe_test.html").read_text()
    write_fixture(root, "solar_system.html", content)
    before = workspace_manifest(root)
    receipt = solar.browser_check(root, {"turn": 1}, {"focus": "Sun", "speed": 1})
    assert receipt["status"] == "passed", receipt
    assert receipt["independent_visual_review"] == "required"
    assert len(receipt["captures"]) == 7 and receipt["webgl"]["calls"] > 0
    assert {Path(item["path"]).name for item in receipt["captures"]} >= {
        "moon_focused.png", "saturn_focused.png", "uranus_focused.png",
    }
    assert workspace_manifest(root) == before
    altered = content.replace("let jd=2451545, speed=1, focus='Sun'", "let jd=2451545, speed=3, focus='Earth'")
    altered = altered.replace('id="speed" type="number" min="0" value="1"', 'id="speed" type="number" min="0" value="3"')
    write_fixture(root, "solar_system.html", altered)
    second = solar.browser_check(root, {"turn": 2}, {"focus": "Earth", "speed": 3})
    assert second["status"] == "passed", second


@pytest.mark.parametrize("source,issue", [
    ("throw new Error('intentional broken fixture')", "browser_probe_exception"),
    ("document.getElementById('reset-sun').onclick=()=>{};", "reset_sun"),
    ("document.getElementById('speed').oninput=()=>{};", "pause_control"),
    ("window.solarSystemSnapshot=()=>({jdTdb:NaN});", "browser_probe_exception"),
])
def test_real_browser_rejects_broken_javascript_or_controls(tmp_path, chromium, source, issue):
    root = expanded.prepare(solar.scenario(), tmp_path / "fixtures")["a"]
    content = (solar.FIXTURES / "solar_probe_test.html").read_text().replace("update();requestAnimationFrame(animate);",
                                                                                      source + "update();requestAnimationFrame(animate);")
    write_fixture(root, "solar_system.html", content)
    receipt = solar.browser_check(root, {"turn": 1}, {"focus": "Sun", "speed": 1})
    assert receipt["status"] == "failed" and issue in receipt["issues"], receipt


def test_fixture_test_app_is_not_supplied_to_the_agent(tmp_path):
    roots = expanded.prepare(solar.scenario(), tmp_path / "fixtures")
    assert not (roots["a"] / "solar_probe_test.html").exists()
    assert not (roots["a"] / "solar_system.html").exists()
    assert (roots["a"] / "solar_reference.json").is_file()
    assert json.loads(json.dumps(asdict(solar.scenario())))["turns"][2]["writable"] == []


def test_v5_counts_include_blocked_and_interrupted_turns():
    summary = {"contract": {"suite": "expanded-v5"}, "runs": [
        {"status": "blocked", "turns": [{"status": "blocked"}, {"status": "not_reached"}]},
        {"status": "interrupted", "turns": [{"status": "interrupted"}]},
    ]}
    matrix.update_counts(summary)
    assert summary["turn_counts"]["blocked"] == summary["turn_counts"]["interrupted"] == 1


def test_browser_preflight_real_software_webgl(tmp_path, chromium):
    receipt = solar.browser_preflight(tmp_path)
    assert receipt["status"] == "available" and receipt["webgl2"] is True, receipt


def test_clean_generation_does_not_clear_pending_visual_review():
    summary = {"contract": {"suite": "expanded-v5"}, "runs": [{"status": "passed", "turns": [
        {"status": "passed", "observed": {"generation_quality": {}},
         "verification": [{"browser_receipt": {"status": "passed", "independent_visual_review": "required"}}]}
    ]}]}
    matrix.update_counts(summary)
    assert summary["quality_counts"]["passed_clean"] == 0
    assert summary["quality_counts"]["passed_review_required"] == 1
