"""Owned, offline Chromium verifier; does not import Qubitz or invoke models."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from followup_matrix_solar import check_state, close_vector, reference

ORIGIN = "http://127.0.0.1:18765"
FILES = ("solar_system.html", "solar_reference.json", "three.module.js", "OrbitControls.js", "THREE-LICENSE.txt")
INSTRUMENT = """(() => {
  window.__solarDraws = 0; window.__solarInvalid = [];
  for (const T of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!T) continue;
    for (const name of ['drawArrays','drawElements','drawArraysInstanced','drawElementsInstanced',
                        'uniformMatrix4fv','uniformMatrix3fv']) {
      const original = T.prototype[name]; if (!original) continue;
      T.prototype[name] = function(...args) {
        if (name.startsWith('draw')) window.__solarDraws++;
        if (window.__solarInvalid.length < 8 && args.some(x => typeof x === 'number'
            ? !Number.isFinite(x) : ArrayBuffer.isView(x) && Array.from(x).some(y => !Number.isFinite(y))))
          window.__solarInvalid.push(name);
        return original.apply(this, args);
      };
    }
  }
})();"""


def snapshot(page: Any) -> dict[str, Any]:
    value = page.evaluate("() => window.solarSystemSnapshot()")
    if not isinstance(value, dict):
        raise TypeError("Snapshot must be an object")
    return value


def number_input(page: Any, name: str, value: float) -> None:
    locator = page.locator("#" + name)
    locator.fill(str(value))
    locator.press("Enter")
    locator.blur()
    page.wait_for_timeout(100)


def canvas_pixels(page: Any, path: Path) -> dict[str, Any]:
    from PIL import Image

    canvas = page.locator("canvas#scene")
    if not canvas.is_visible():
        raise ValueError("Scene canvas not visible")
    box = canvas.bounding_box()
    if box is None or min(box["width"], box["height"]) < 128:
        raise ValueError("Scene canvas too small")
    raw = canvas.screenshot(timeout=5000)
    path.write_bytes(raw)
    picture = Image.open(io.BytesIO(raw)).convert("RGB")
    colors = picture.resize((128, 128)).getcolors(128*128)
    # Necessary smoke evidence only; this is not a semantic or lighting oracle.
    distinct = len(colors) if colors else 128*128
    return {"sha256": hashlib.sha256(raw).hexdigest(), "path": str(path),
            "width": picture.width, "height": picture.height, "distinct_sampled_colors": distinct}


def probe(root: Path, output: Path, focus: str, speed: float) -> dict[str, Any]:
    from playwright.sync_api import Error, sync_playwright

    data = reference()
    issues: list[str] = []
    errors: list[str] = []
    blocked: list[str] = []
    states: list[dict[str, Any]] = []
    captures = []
    files = {name: (root / name).read_bytes() for name in FILES}
    if json.loads(files["solar_reference.json"]) != data:
        return {"status": "failed", "issues": ["reference_fixture_changed"]}
    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader"])
        except Error as exc:
            return {"status": "unavailable", "issues": [], "reason": str(exc)[:1000]}
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
            context.add_init_script(INSTRUMENT)

            def route(request: Any) -> None:
                url = urlsplit(request.request.url)
                name = url.path.removeprefix("/")
                if url.scheme == "http" and url.netloc == "127.0.0.1:18765" and name in files:
                    mime = "text/html" if name.endswith(".html") else "application/json" if name.endswith(".json") else "text/javascript"
                    request.fulfill(status=200, body=files[name], content_type=mime)
                else:
                    blocked.append(request.request.url)
                    request.abort()

            context.route("**/*", route)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(ORIGIN + "/solar_system.html", wait_until="domcontentloaded", timeout=10000)
            page.wait_for_function("typeof window.solarSystemSnapshot === 'function' && window.__solarDraws > 0", timeout=10000)
            initial = snapshot(page)
            states.append({"stage": "initial", "state": initial})
            if initial.get("focus") != focus or initial.get("daysPerSecond") != speed:
                issues.append("initial_focus_or_speed")
            captures.append(canvas_pixels(page, output / "initial.png"))
            number_input(page, "speed", 0)
            paused = snapshot(page)
            page.wait_for_timeout(250)
            if paused.get("daysPerSecond") != 0 or abs(snapshot(page)["jdTdb"] - paused["jdTdb"]) > 1e-8:
                issues.append("pause_control")
            for jd in [2451545.0, 2451552.125, 2451573.75]:
                number_input(page, "epoch", jd)
                state = snapshot(page)
                states.append({"stage": "epoch", "state": state})
                if not math.isclose(state.get("jdTdb", -1), jd, rel_tol=0, abs_tol=1e-8):
                    issues.append("epoch_control")
                issues.extend(check_state(state, data))
            for name in data["body_constants"]:
                label = page.locator(f'[data-body="{name}"]')
                if label.count() != 1 or not label.is_visible() or name not in label.inner_text():
                    issues.append("body_label:" + name)
                    continue
                label.click(timeout=3000)
                page.wait_for_timeout(200)
                state = snapshot(page)
                states.append({"stage": "click:" + name, "state": state})
                target = state.get("displayPositions", {}).get(name)
                if state.get("focus") != name or not isinstance(target, list) or not close_vector(state.get("cameraTarget"), target, 1e-3):
                    issues.append("focus_click:" + name)
                if name in {"Moon", "Saturn", "Uranus"}:
                    captures.append(canvas_pixels(page, output / (name.lower() + "_focused.png")))
            page.locator('[data-body="Earth"]').click()
            number_input(page, "epoch", 2451545.0)
            captures.append(canvas_pixels(page, output / "earth_paused.png"))
            for value in [1, 3]:
                number_input(page, "epoch", 2451545.0)
                number_input(page, "speed", value)
                before = snapshot(page)
                start = page.evaluate("performance.now()")
                page.wait_for_timeout(600)
                after = snapshot(page)
                elapsed = (page.evaluate("performance.now()") - start) / 1000
                states.append({"stage": "speed:" + str(value), "state": after, "browser_elapsed_seconds": elapsed})
                if after.get("daysPerSecond") != value or not math.isclose(after["jdTdb"] - before["jdTdb"], elapsed*value, rel_tol=0.20, abs_tol=0.10):
                    issues.append("speed_clock:" + str(value))
                if not close_vector(after.get("cameraTarget"), after["displayPositions"]["Earth"], 1e-3):
                    issues.append("moving_focus_tracking")
                if after.get("frame", 0) <= before.get("frame", 0):
                    issues.append("animation_frames")
                issues.extend(check_state(after, data))
            captures.append(canvas_pixels(page, output / "earth_moving.png"))
            if captures[-1]["sha256"] == captures[-2]["sha256"]:
                issues.append("no_visible_animation_change")
            page.locator("#reset-sun").click()
            page.wait_for_timeout(200)
            if snapshot(page).get("focus") != "Sun":
                issues.append("reset_sun")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(200)
            captures.append(canvas_pixels(page, output / "mobile.png"))
            if not page.locator("#speed").is_visible():
                issues.append("mobile_speed_hidden")
            if page.evaluate("document.documentElement.scrollWidth > innerWidth + 2"):
                issues.append("mobile_horizontal_overflow")
            draw = page.evaluate("({calls:window.__solarDraws, invalid:window.__solarInvalid})")
            if draw["invalid"]:
                issues.append("nonfinite_draw_values")
            if any(item["distinct_sampled_colors"] <= 1 for item in captures):
                issues.append("blank_canvas")
            if errors:
                issues.append("javascript_or_console_errors")
            if blocked:
                issues.append("undeclared_or_remote_dependencies")
            context.close()
        finally:
            browser.close()
    return {"status": "failed" if issues else "passed", "issues": sorted(set(issues)),
            "javascript_errors": errors[:20], "blocked_requests": blocked[:20], "snapshots": states,
            "captures": captures, "webgl": draw, "renderer": "Chromium software ANGLE/SwiftShader",
            "independent_visual_review": "required", "network": "intercepted declared local fixture files only"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--focus", choices=("Sun", "Earth"))
    parser.add_argument("--speed", type=float)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    start = time.monotonic()
    try:
        if args.preflight:
            from PIL import Image
            from playwright.sync_api import sync_playwright

            with sync_playwright() as runtime:
                browser = runtime.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader"])
                try:
                    page = browser.new_page()
                    usable = page.evaluate("() => !!document.createElement('canvas').getContext('webgl2')")
                    Image.new("RGB", (1, 1))
                finally:
                    browser.close()
            result = {"status": "available" if usable else "unavailable", "issues": [], "webgl2": usable}
        else:
            if args.workspace is None or args.focus is None or args.speed is None:
                parser.error("Artifact probes require --workspace, --focus and --speed")
            result = probe(args.workspace, args.output, args.focus, args.speed)
    except ImportError as exc:
        result = {"status": "unavailable", "issues": [], "reason": repr(exc)}
    except Exception as exc:  # noqa: BLE001 - publish failed probe evidence, never claim a pass
        result = {"status": "unavailable" if args.preflight else "failed", "issues": ["browser_probe_exception"], "reason": repr(exc)}
    result["duration_monotonic_seconds"] = time.monotonic() - start
    (args.output / "receipt.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "issues": result["issues"]}))
    return 0 if result["status"] in {"passed", "available", "unavailable"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
