"""Actual offline software-WebGL checks on test-owned scenes; no model or network."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from evaluation.solar_artifact_evidence import browser_check

SOURCE = """<!doctype html><html><body style="margin:0">
<label>Speed <span id="value">3x</span><input id="speed" type="range" min="0" max="10" value="3"></label>
<script type="module">
import * as module from './three.module.js';
window.THREE={...module};
const scene=new THREE.Scene(), camera=new THREE.PerspectiveCamera(60,1280/900,0.1,1000);
const renderer=new THREE.WebGLRenderer();renderer.setSize(1280,900);document.body.appendChild(renderer.domElement);
const bodies=[];let speed=3,focus=null,angle=0;
for(const [name,distance] of [['Earth',20],['Mars',35],['Saturn',50]]) {
 const holder=new THREE.Group(),body=new THREE.Mesh(new THREE.SphereGeometry(3,24,24),new THREE.MeshBasicMaterial({color:0x44aaff}));
 body.userData.name=name;scene.add(holder);holder.add(body);bodies.push({name,distance,holder,body});
 if(name==='Earth')focus=body;
 if(name==='Saturn') { const ring=new THREE.Mesh(new THREE.RingGeometry(4,6,32),new THREE.MeshBasicMaterial({side:THREE.DoubleSide}));
 ring.rotation.x=Math.PI/2; (BROKEN ? holder : body).add(ring); }
}
document.querySelector('#speed').oninput=e=>{speed=Number(e.target.value);document.querySelector('#value').textContent=speed+'x';};
renderer.domElement.onclick=e=>{
 const ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2(e.clientX/1280*2-1,-e.clientY/900*2+1),camera);
 const hits=ray.intersectObjects(bodies.map(x=>x.body));if(hits.length)focus=hits[0].object;
};
let last=performance.now();
function frame(now) {
 const delta=Math.min((now-last)/1000,0.1);last=now;angle+=delta*speed*0.2;
 bodies.forEach((x,i)=>{const a=angle+i*0.7;
   x.body.position.set(Math.cos(BROKEN ? i*0.7 : a)*x.distance,Math.sin(BROKEN ? i*0.7 : a)*x.distance,0);
   if(BROKEN)x.body.rotation.y=angle;
 });
 scene.updateMatrixWorld(true);const p=focus.getWorldPosition(new THREE.Vector3());
 camera.position.copy(p).add(new THREE.Vector3(0,0,120));camera.lookAt(p);
 renderer.render(scene,camera);requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script></body></html>"""


@pytest.mark.parametrize("mode", ["global", "esm", "select", "cached_cdn"])
@pytest.mark.parametrize("broken", [False, True])
def test_renderer_probe_distinguishes_motion_and_ring_binding(tmp_path, broken, mode):
    assets = Path(__file__).parent / "fixtures/solar_assets/three.module.js"
    shutil.copyfile(assets, tmp_path / assets.name)
    artifact = tmp_path / "scene.html"
    source = SOURCE.replace("BROKEN", "true" if broken else "false")
    if mode != "global":
        source = source.replace("import * as module from './three.module.js';\nwindow.THREE={...module};",
                                "import * as THREE from './three.module.js';")
    if mode == "select":
        source = source.replace('<input id="speed" type="range" min="0" max="10" value="3">',
                                '<select id="speed"><option value="0">0x</option><option value="1">1x</option>'
                                '<option value="3" selected>3x</option></select>')
    if mode == "cached_cdn":
        source = source.replace("'./three.module.js'", "'https://unpkg.com/three@0.160.0/build/three.module.js'")
    artifact.write_text(source)
    result = browser_check(tmp_path, artifact)
    receipt_path = Path(result["captures"][0]["path"]).parent / "receipt.json"
    assert json.loads(receipt_path.read_text())["browser_closed"] is True
    assert result.get("browser_closed") is True, result
    assert artifact.read_text() == source
    if broken:
        assert result["status"] == "failed", result
        assert "planet_world_position_motion_not_verified" in result["issues"]
        assert "ring_body_colocation_not_verified" in result["issues"]
    else:
        assert result["status"] == "passed", result


@pytest.mark.parametrize("fault,issue", [
    ("focus", "initial_earth_framing_not_verified"),
    ("initial_speed", "initial_visible_speed_not_3"),
    ("speed_response", "effective_speed_response_not_verified"),
    ("pause", "pause_did_not_stop_body:"),
    ("click", "click_to_focus_not_verified"),
])
def test_real_behavior_failures_still_fail(tmp_path, fault, issue):
    assets = Path(__file__).parent / "fixtures/solar_assets/three.module.js"
    shutil.copyfile(assets, tmp_path / assets.name)
    source = SOURCE.replace("BROKEN", "false")
    if fault == "focus":
        source = source.replace("if(name==='Earth')focus=body;", "if(name==='Saturn')focus=body;")
    elif fault == "initial_speed":
        source = source.replace('value="3"', 'value="1"').replace('id="value">3x', 'id="value">1x')
    elif fault == "speed_response":
        source = source.replace("angle+=delta*speed*0.2", "angle+=delta*(speed===0 ? 0 : 1)*0.2")
    elif fault == "pause":
        source = source.replace("angle+=delta*speed*0.2", "angle+=delta*(speed || 1)*0.2")
    else:
        source = source.replace("if(hits.length)focus=hits[0].object;", "if(hits.length)void hits[0];")
    artifact = tmp_path / "scene.html"
    artifact.write_text(source)
    result = browser_check(tmp_path, artifact)
    assert result["status"] == "failed", result
    assert any(value.startswith(issue) for value in result["issues"]), result


@pytest.mark.parametrize("source,status", [
    ('<html><script type="module">import "https://invalid.example/missing.js";</script></html>', "unavailable"),
    ('<html><script>throw new Error("fixture syntax/runtime failure")</script></html>', "failed"),
    ('<html><body>No supported renderer</body></html>', "unavailable"),
])
def test_missing_observation_is_not_a_task_failure(tmp_path, source, status):
    artifact = tmp_path / "artifact.html"
    artifact.write_text(source)
    result = browser_check(tmp_path, artifact)
    assert result["status"] == status, result
    assert result["browser_closed"] is True
    assert result["stage"] == "renderer_observation"


def test_cleanup_error_does_not_erase_primary_evidence(tmp_path, monkeypatch):
    from playwright.sync_api import Browser, Error

    close = Browser.close

    def close_then_error(browser, *args, **kwargs):
        close(browser, *args, **kwargs)
        raise Error("injected cleanup acknowledgement error")

    monkeypatch.setattr(Browser, "close", close_then_error)
    artifact = tmp_path / "artifact.html"
    artifact.write_text('<html><script>throw new Error("primary failure")</script></html>')
    result = browser_check(tmp_path, artifact)
    assert result["status"] == "error"
    assert result["primary_status"] == "failed"
    assert any("primary failure" in error for error in result["errors"])
    assert "injected cleanup acknowledgement error" in result["cleanup_error"]
