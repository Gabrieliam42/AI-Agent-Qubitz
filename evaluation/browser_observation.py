"""Offline, source-preserving browser observation utilities for generated artifacts."""
from __future__ import annotations

import json

from runtime.browser_evidence import (
    CachedResource,
    OfflineResources,
    cached_browser_resources,
)

__all__ = ["CachedResource", "OfflineResources", "RendererObserver", "cached_browser_resources", "set_numeric_control"]


RENDER_HOOK = r"""() => {
  window.__qbRenderers = [];
  window.__qbObserveRenderer = renderer => {
    if (window.__qbRenderers.includes(renderer)) return;
    window.__qbRenderers.push(renderer);
    const draw = renderer.render;
    renderer.render = function(scene, camera, ...rest) {
      window.__qbRender = {scene, camera, canvas:this.domElement,
        count:((window.__qbRender || {}).count || 0)+1};
      return draw.call(this, scene, camera, ...rest);
    };
  };
}"""


class RendererObserver:
    """Discover live THREE renderers through CDP, including lexical ES modules.

    Only modules already parsed in this page are queried. Importing the same URL
    reuses its module instance; no source rewrite or benchmark API is required.
    """

    def __init__(self, page):
        self.page = page
        self.session = page.context.new_cdp_session(page)
        self.modules: set[str] = set()
        self.session.on("Debugger.scriptParsed", self._parsed)
        self.session.send("Debugger.enable")

    def _parsed(self, event):
        # Inline modules have their HTML document's URL; importing that URL would
        # create a new, invalid module request rather than inspect an existing one.
        if (event.get("isModule") and event.get("startLine", 0) == 0 and event.get("startColumn", 0) == 0
                and event.get("url", "").startswith(("http://", "https://"))):
            self.modules.add(event["url"])

    def attach(self) -> dict:
        self.page.evaluate(RENDER_HOOK)
        expressions = ["window.THREE && window.THREE.WebGLRenderer && window.THREE.WebGLRenderer.prototype"]
        expressions += [f"import({json.dumps(url)}).then(m=>m.WebGLRenderer && m.WebGLRenderer.prototype)"
                        for url in sorted(self.modules)]
        try:
            for expression in expressions:
                value = self.session.send("Runtime.evaluate", {"expression": expression, "awaitPromise": True,
                                           "objectGroup": "qubitz_observation", "timeout": 5000})
                if value.get("exceptionDetails"):
                    raise ValueError("Loaded renderer module could not be inspected")
                prototype = value.get("result", {}).get("objectId")
                if prototype is None:
                    continue
                objects = self.session.send("Runtime.queryObjects", {"prototypeObjectId": prototype,
                                            "objectGroup": "qubitz_observation"})["objects"]["objectId"]
                self.session.send("Runtime.callFunctionOn", {"objectId": objects, "functionDeclaration":
                    "function(){for(const renderer of this)if(renderer.isWebGLRenderer===true && "
                    "typeof renderer.render==='function' && renderer.domElement instanceof HTMLCanvasElement)"
                    "window.__qbObserveRenderer(renderer);}",
                    "returnByValue": True})
            count = self.page.evaluate("window.__qbRenderers.length")
            return {"status": "attached" if count == 1 else "unavailable", "renderer_count": count,
                    "reason": "" if count == 1 else "No unique supported renderer instance",
                    "loaded_module_urls": sorted(self.modules)}
        finally:
            self.session.send("Runtime.releaseObjectGroup", {"objectGroup": "qubitz_observation"})


def set_numeric_control(locator, value: float) -> None:
    """Exercise standard input/change events without inventing select options."""
    locator.evaluate(r"""(node, value) => {
      if (node.tagName === 'SELECT' && !Array.from(node.options).some(option=>Number(option.value)===value))
        throw new Error('Requested numeric option is unavailable');
      node.value=String(value);
      if (Number(node.value)!==value) throw new Error('Control rejected the requested numeric value');
      node.dispatchEvent(new Event('input',{bubbles:true}));
      node.dispatchEvent(new Event('change',{bubbles:true}));
    }""", value)
