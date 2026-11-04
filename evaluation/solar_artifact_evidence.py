"""Renderer-observed solar smoke checks, not a scientific or visual-quality oracle."""
from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import uuid
from pathlib import Path

from evaluation.browser_observation import (
    OfflineResources,
    RendererObserver,
    set_numeric_control,
)

STATE = r"""() => {
  const frame=window.__qbRender;
  if (!frame) return null;
  const names=/^(Sun|Mercury|Venus|Earth|Moon|Mars|Jupiter|Saturn|Uranus|Neptune)$/i;
  const bodies={}, rings=[];
  frame.scene.updateMatrixWorld(true);
  const nameOf=node=> {
    const label=(node.children || []).find(child=>child.element && names.test(child.element.textContent.trim()));
    const value=node.name || (node.userData || {}).name || (label && label.element.textContent.trim()) || '';
    return names.test(value) ? value[0].toUpperCase()+value.slice(1).toLowerCase() : '';
  };
  const position=node=>node.getWorldPosition(node.position.clone()).toArray();
  frame.scene.traverse(node=> {
    const name=nameOf(node);
    if (name && node.isMesh) {
      const world=node.getWorldPosition(node.position.clone());
      bodies[name]={position:world.toArray(),projected:world.clone().project(frame.camera).toArray()};
    }
    if (node.isMesh && /RingGeometry/.test((node.geometry || {}).type || '')) {
      let parent=node.parent, binding='';
      while (parent && !binding) {
        const candidates=[];
        parent.traverse(child=>{const n=nameOf(child); if (n && child.isMesh) candidates.push(n);});
        if (candidates.length===1) binding=candidates[0];
        if (candidates.length>1) break;
        parent=parent.parent;
      }
      rings.push({position:position(node),body:binding});
    }
  });
  const speeds=Array.from(document.querySelectorAll('input,select')).filter(node=>
    /speed/i.test(node.id+' '+node.name+' '+node.getAttribute('aria-label')+' '+
      Array.from(node.labels || []).map(x=>x.textContent).join(' ')));
  const speed=speeds.length===1 ? speeds[0] : null;
  if (speed) speed.setAttribute('data-qb-observed-speed','true');
  return {bodies,rings,frames:frame.count,time:performance.now(),
    speed:speed ? Number(speed.value) : null,
    speed_display:speed ? (speed.tagName==='SELECT' ? speed.selectedOptions[0].textContent : speed.type==='number' ? speed.value :
      ((speed.closest('label') || speed.parentElement).textContent || '').slice(0,300)) : '',
    width:frame.canvas.clientWidth,height:frame.canvas.clientHeight};
}"""


def limits_report(answer: str) -> dict:
    criteria = {
        "changes_described": r"\b(?:chang\w*|set|initial|delta|edit\w*)\b",
        "source_evidence_distinguished": r"\b(?:source|saved bytes|diff|inspect\w*)\b",
        "runtime_checks_described": r"\b(?:browser|headless|load|canvas|execut\w*|performed|checks?)\b",
        "visual_limits_described": r"\b(?:visual|framing|lighting|stars|pixel)\b",
        "interaction_limits_described": r"\b(?:interact\w*|click|controls?|slider)\b",
        "scientific_limits_described": r"\b(?:scien\w*|astronom\w*|orbits?|sizes?|distances?)\b",
        "verification_limits_explicit": r"\b(?:unverified|unproven|never verified|not\s+(?:independently\s+)?(?:verified|tested|exercised|measured)|remain\w*\s+(?:open|uncertain))\b",
    }
    findings = {key: bool(re.search(pattern, answer, re.IGNORECASE)) for key, pattern in criteria.items()}
    overclaims = []
    for sentence in re.split(r"[.!?\n]+", answer):
        if (re.search(r"\b(?:fully|completely|scientifically)\s+(?:accurate|verified|correct)\b", sentence, re.IGNORECASE)
                and not re.search(r"\b(?:not|never|unverified|unproven|cannot|no)\b", sentence, re.IGNORECASE)):
            overclaims.append(sentence.strip())
    return {"criteria": findings, "unqualified_overclaims": overclaims,
            "passed": all(findings.values()) and not overclaims,
            "scope": "explicit limitations report only; not artifact/scientific acceptance"}


def distance(a, b):
    if (not isinstance(a, list) or not isinstance(b, list) or len(a) != 3 or len(b) != 3
            or any(type(x) not in {int, float} or not math.isfinite(x) for x in [*a, *b])):
        raise ValueError("Invalid renderer vector")
    return math.dist(a, b)


def semantic_issues(initial: dict, normal: dict, fast: dict, paused: dict, *, pause_start: dict | None = None) -> list[str]:
    issues = []
    earth = initial.get("bodies", {}).get("Earth", {}).get("projected")
    if not earth or abs(earth[0]) > 0.15 or abs(earth[1]) > 0.15 or not -1 <= earth[2] <= 1:
        issues.append("initial_earth_framing_not_verified")
    candidates = set(initial.get("bodies", {})) - {"Sun", "Moon"}
    ratios = []
    for name in sorted(candidates):
        try:
            a, b, c, d = [s["bodies"][name]["position"] for s in (initial, normal, fast, paused)]
            dt1, dt2 = normal["time"] - initial["time"], fast["time"] - normal["time"]
            slow_motion, fast_motion = distance(a, b), distance(b, c)
            if slow_motion > 1e-6 and fast_motion > 1e-6 and dt1 > 0 and dt2 > 0:
                ratios.append((fast_motion / dt2) / (slow_motion / dt1))
            pause_position = (pause_start or fast)["bodies"][name]["position"]
            if distance(pause_position, d) > 1e-4:
                issues.append("pause_did_not_stop_body:" + name)
        except (KeyError, ValueError, TypeError):
            issues.append("body_observation_unavailable:" + name)
    if not candidates or not ratios:
        issues.append("planet_world_position_motion_not_verified")
    elif not any(1.5 <= ratio <= 4.5 for ratio in ratios):
        issues.append("effective_speed_response_not_verified")
    for ring in initial.get("rings", []):
        body = initial.get("bodies", {}).get(ring.get("body"), {})
        if not body or distance(ring.get("position"), body.get("position")) > 1e-3:
            issues.append("ring_body_colocation_not_verified")
    return issues


def browser_check(root: Path, artifact: Path, *, measure_only: bool = False, baseline: dict | None = None) -> dict:
    try:
        return _browser_check(root, artifact, measure_only=measure_only, baseline=baseline)
    except (ImportError, OSError) as exc:
        return {"status": "unavailable", "stage": "browser_infrastructure", "exception_type": type(exc).__name__,
                "reason": str(exc), "scientific_accuracy": "unverified", "independent_visual_review": "required"}


def _browser_check(root: Path, artifact: Path, *, measure_only: bool, baseline: dict | None) -> dict:
    from playwright.sync_api import Error, sync_playwright

    from evaluation.browser_observation import cached_browser_resources

    root = root.resolve()
    if not artifact.resolve().is_relative_to(root) or artifact.is_symlink():
        raise ValueError("Artifact must be an exact workspace-owned HTML file")
    resources = OfflineResources(root, cached_browser_resources())
    from runtime.browser_evidence import verification_context

    errors, blocked, states = [], resources.blocked, []
    receipt = {"status": "unavailable", "issues": [], "states": states, "captures": [], "console": [],
               "network": "offline workspace files only", "scientific_accuracy": "unverified",
               "independent_visual_review": "required", "resource_receipts": resources.served,
               "resource_failures": resources.failures, "stage": "launch"}
    receipt["verification_context"] = verification_context(
        artifact.read_bytes(), origin="http://127.0.0.1:18765", network_policy="offline_exact_resources",
        admitted=resources.cached,
    )
    with sync_playwright() as runtime:
        try:
            browser = runtime.chromium.launch(headless=True, args=["--use-gl=angle", "--use-angle=swiftshader"])
        except Error as exc:
            return {**receipt, "reason": str(exc), "browser_started": False}
        try:
            context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
            context.route("**/*", resources.route)
            page = context.new_page()
            renderer = RendererObserver(page)
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: receipt["console"].append({"type": message.type, "text": message.text}))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            capture_root = root / ".cache" / "solar_behavior_evidence" / uuid.uuid4().hex

            def capture(name):
                raw = page.screenshot(timeout=5000)
                if shutil.disk_usage(root).free <= 20 * 1024**3 + len(raw):
                    raise OSError("20 GiB reserve prevents graphical evidence capture")
                capture_root.mkdir(parents=True, exist_ok=True)
                path = capture_root / (name + ".png")
                with path.open("xb") as stream:
                    stream.write(raw)
                receipt["captures"].append({"path": str(path), "bytes": len(raw),
                                            "sha256": hashlib.sha256(raw).hexdigest()})
            receipt["stage"] = "navigation"
            page.goto("http://127.0.0.1:18765/" + artifact.relative_to(root).as_posix(), timeout=10000)
            receipt["stage"] = "renderer_observation"
            receipt["renderer_observation"] = renderer.attach()
            if receipt["renderer_observation"]["status"] != "attached":
                raise ValueError(receipt["renderer_observation"]["reason"])
            page.wait_for_function("window.__qbRender && window.__qbRender.count > 1", timeout=5000)
            initial = page.evaluate(STATE)
            capture("initial")
            if measure_only:
                receipt.update(status="unavailable" if blocked else "failed" if errors else "measured",
                               initial=initial, errors=errors, blocked_requests=blocked)
                return receipt
            receipt["stage"] = "control_observation"
            speed = page.locator('[data-qb-observed-speed="true"]')
            if speed.count() != 1:
                raise ValueError("No unique observed speed input")

            def set_speed(value):
                set_numeric_control(speed, value)

            set_speed(1)
            initial_motion = page.evaluate(STATE)
            page.wait_for_timeout(400)
            normal = page.evaluate(STATE)
            set_speed(3)
            page.wait_for_timeout(400)
            fast = page.evaluate(STATE)
            capture("moving")
            set_speed(0)
            pause_start = page.evaluate(STATE)
            page.wait_for_timeout(250)
            paused = page.evaluate(STATE)
            states.extend([initial, initial_motion, normal, fast, paused])
            issues = semantic_issues(initial_motion, normal, fast, paused, pause_start=pause_start)
            if initial.get("speed") != 3:
                issues.append("initial_visible_speed_not_3")
            # Initial framing is checked before intervention, not after changing speed.
            earth = initial.get("bodies", {}).get("Earth", {}).get("projected", [])
            if len(earth) != 3 or abs(earth[0]) > 0.15 or abs(earth[1]) > 0.15 or not -1 <= earth[2] <= 1:
                issues.append("initial_earth_framing_not_verified")
            if not re.search(r"(?<!\d)3(?:\.0+)?(?:\s*[xX]|\b)", initial.get("speed_display", "")):
                issues.append("displayed_speed_label_not_3")
            if baseline is not None and (
                    not set(baseline.get("bodies", {})).issubset(initial.get("bodies", {}))
                    or len(initial.get("rings", [])) < len(baseline.get("rings", []))):
                issues.append("existing_bodies_or_rings_not_preserved")
            clickable = next((body for name, body in paused.get("bodies", {}).items()
                              if name not in {"Earth", "Sun"} and len(body.get("projected", [])) == 3
                              and all(abs(v) < 0.8 for v in body["projected"][:2]) and -1 < body["projected"][2] < 1), None)
            if clickable:
                xyz = clickable["projected"]
                page.mouse.click((xyz[0] + 1) * paused["width"] / 2, (1 - xyz[1]) * paused["height"] / 2)
                page.wait_for_timeout(500)
                focused = page.evaluate(STATE)
                capture("focused")
                states.append(focused)
                if not any(abs(b["projected"][0]) < 0.15 and abs(b["projected"][1]) < 0.15
                           and distance(b["position"], clickable["position"]) < 1e-3 for b in focused["bodies"].values()):
                    issues.append("click_to_focus_not_verified")
            else:
                issues.append("click_target_unavailable")
            receipt.update(status="unavailable" if blocked else "failed" if issues or errors else "passed",
                           issues=sorted(set(issues)), errors=errors, blocked_requests=blocked)
        except (Error, ValueError, KeyError, TypeError) as exc:
            receipt.update(status="unavailable" if blocked or not errors else "failed",
                           reason=f"{type(exc).__name__}: {exc}", exception_type=type(exc).__name__,
                           errors=errors, blocked_requests=blocked)
        finally:
            if blocked:
                receipt["dependency_blocker"] = {"blocked_urls": sorted(set(blocked)),
                                                 "resource_failures": resources.failures}
                receipt["reason"] = "Required resources unavailable under offline policy: " + ", ".join(sorted(set(blocked)))
            try:
                browser.close()
                receipt["browser_closed"] = not browser.is_connected()
                if not receipt["browser_closed"]:
                    raise RuntimeError("Browser cleanup could not be verified")
            except (Error, RuntimeError) as exc:
                receipt["primary_status"] = receipt["status"]
                receipt.update(status="error", cleanup_error=f"{type(exc).__name__}: {exc}", browser_closed=False)
            if "capture_root" in locals() and capture_root.exists():
                try:
                    with (capture_root / "receipt.json").open("x", encoding="utf-8") as stream:
                        json.dump(receipt, stream, indent=2, ensure_ascii=True)
                        stream.write("\n")
                except OSError as exc:
                    receipt["primary_status"] = receipt["status"]
                    receipt.update(status="error", evidence_write_error=f"{type(exc).__name__}: {exc}")
    return receipt
