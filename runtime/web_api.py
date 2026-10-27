"""Authenticated loopback API and static frontend, never an arbitrary tool gateway."""
from __future__ import annotations

import asyncio
import json
import secrets
import socket
import subprocess
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .catalog import cached_model_states
from .paths import PROJECT_ROOT, variant_paths
from .web_controller import AgentSession, SessionConflict


def create_api(session: AgentSession, token: str, origin: str, *, shutdown: Any = None,
               assets: Path | None = None) -> FastAPI:
    assets = (assets or PROJECT_ROOT / "frontend/dist").resolve()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            yield
        finally:
            await asyncio.to_thread(session.close)

    app = FastAPI(title="Qubitz Local Interface", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next: Any):
        if request.headers.get("host") != origin.split("://", 1)[1]:
            return JSONResponse({"detail": "Invalid local host."}, status_code=403)
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(request.headers.get("x-qubitz-token", ""), token):
                return JSONResponse({"detail": "Open the current launch URL to authenticate this tab."}, status_code=401)
            supplied_origin = request.headers.get("origin")
            if supplied_origin and supplied_origin != origin:
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
            if request.method not in {"GET", "HEAD"}:
                if supplied_origin != origin or request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                    return JSONResponse({"detail": "Same-origin JSON requests are required."}, status_code=403)
                try:
                    length = int(request.headers.get("content-length", "0"))
                except ValueError:
                    return JSONResponse({"detail": "Invalid body size."}, status_code=400)
                if not 0 <= length <= 1024 * 1024:
                    return JSONResponse({"detail": "Request body is too large."}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
            "frame-src 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(SessionConflict)
    async def conflict(_request: Request, exc: SessionConflict):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(TypeError)
    @app.exception_handler(ValueError)
    async def invalid(_request: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    async def body(request: Request) -> dict[str, Any]:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024 * 1024:
                raise HTTPException(413, "Request body is too large.")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(400, "Invalid JSON.") from exc
        if not isinstance(value, dict):
            raise HTTPException(422, "Expected a JSON object.")
        return value

    def request_id(data: dict[str, Any]) -> str:
        value = data.get("request_id")
        if not isinstance(value, str) or not 16 <= len(value) <= 128 or not all(c.isalnum() or c in "-_" for c in value):
            raise ValueError("Supply a bounded unique request_id; commands are not automatically retried.")
        return value

    @app.get("/api/state")
    def state():
        return session.snapshot()

    @app.get("/api/variants")
    def variants():
        names = [p.name for p in variant_paths()]
        groups: dict[str, list[str]] = {"12 GB VRAM": [], "24 GB VRAM": [], "48 GB VRAM+": []}
        try:
            payload = json.loads((PROJECT_ROOT / "qubitz_variant_profiles.json").read_text(encoding="utf-8"))
            profiles = {profile["filename"]: profile for profile in payload["variants"]}
            for name in names:
                contract = profiles[name]["runtime_contract"]
                if contract["dual_gpu"] is True:
                    tier = "48 GB VRAM+"
                elif contract["dual_gpu"] is False:
                    tier = "12 GB VRAM" if contract["constants"].get("QUBITZ_VRAM_BUDGET_MIB") == 12288 else "24 GB VRAM"
                else:
                    raise ValueError("Unknown variant hardware tier.")
                groups.setdefault(tier, []).append(name)
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            raise HTTPException(503, "The configured variant tier catalog is unavailable.") from exc
        return {"variants": names, "groups": [{"tier": tier, "variants": members}
                                               for tier, members in groups.items() if members]}

    @app.get("/api/model-cache")
    async def model_cache():
        try:
            states = await asyncio.to_thread(cached_model_states, variant_paths(), PROJECT_ROOT)
        except (OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(503, "The local model file check is unavailable.") from exc
        return {"states": states, "scope": "Named local file sets and headers only; integrity, VRAM fit and execution are not verified."}

    @app.get("/api/workspace-directories")
    async def directories(path: str | None = None):
        try:
            return await asyncio.to_thread(session.directory_listing, path)
        except OSError as exc:
            raise HTTPException(422, f"Cannot browse this folder: {exc}") from exc

    @app.post("/api/tasks", status_code=202)
    async def submit(request: Request):
        data = await body(request)
        if not isinstance(data.get("prompt"), str):
            raise HTTPException(422, "prompt must be text.")
        return {"task_id": session.submit(data["prompt"], request_id(data))}

    @app.post("/api/tasks/{task_id}/cancel", status_code=202)
    async def cancel(task_id: str, request: Request):
        await body(request)
        session.cancel(task_id)
        return {"status": "cancellation_requested"}

    @app.post("/api/steer", status_code=202)
    async def steer(request: Request):
        data = await body(request)
        if not isinstance(data.get("text"), str):
            raise HTTPException(422, "text must be a string.")
        session.steer(data["text"])
        return {"status": "steering_submitted"}

    @app.post("/api/approvals/{approval_id}", status_code=202)
    async def approval(approval_id: str, request: Request):
        data = await body(request)
        if not isinstance(data.get("decision"), str) or data["decision"] not in {"approve", "decline"}:
            raise ValueError("decision must be approve or decline.")
        task_id = session.decide(approval_id, data["decision"] == "approve", request_id(data))
        return {"task_id": task_id}

    @app.post("/api/config")
    async def configure(request: Request):
        data = await body(request)
        settings = data.get("settings", {})
        if not isinstance(settings, dict) or any(data.get(k) is not None and not isinstance(data[k], str) for k in ("workspace", "variant")):
            raise ValueError("Invalid configuration request.")
        await asyncio.to_thread(session.configure, settings, workspace=data.get("workspace"), variant=data.get("variant"))
        return session.snapshot()

    @app.get("/api/transcript")
    async def transcript():
        if session.journal.error:
            raise HTTPException(503, session.journal.error)
        try:
            text = await asyncio.to_thread(session.journal.snapshot)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"text": text, "filename": session.journal.path.name}

    @app.get("/api/events")
    async def events(request: Request, after: int = 0):
        if after < 0:
            raise ValueError("Invalid event cursor.")

        async def stream():
            cursor = after
            while not await request.is_disconnected():
                gap, batch = await asyncio.to_thread(session.events_after, cursor)
                if gap:
                    snapshot = session.snapshot()
                    cursor = snapshot["sequence"]
                    yield f"id: {cursor}\nevent: reset\ndata: {json.dumps(snapshot, ensure_ascii=True)}\n\n"
                elif batch:
                    for event in batch:
                        cursor = event["sequence"]
                        yield f"id: {cursor}\nevent: update\ndata: {json.dumps(event, ensure_ascii=True)}\n\n"
                else:
                    yield ": heartbeat\n\n"
                if session.phase == "closed":
                    return

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post("/api/shutdown", status_code=202)
    async def close(request: Request):
        await body(request)

        def cleanup():
            try:
                session.close()
            except Exception:  # noqa: BLE001 - Cleanup error remains visible; do not terminate uncertain ownership.
                return
            if shutdown is not None:
                shutdown()

        threading.Thread(target=cleanup, name="qubitz-web-close", daemon=False).start()
        return {"status": "closing"}

    @app.get("/")
    def index():
        path = assets / "index.html"
        if not path.is_file():
            raise HTTPException(503, "Frontend build missing. Run npm --prefix frontend run build.")
        return FileResponse(path)

    @app.get("/assets/{filename:path}")
    def asset(filename: str):
        path = (assets / "assets" / filename).resolve()
        if not path.is_relative_to(assets / "assets") or not path.is_file() or path.is_symlink():
            raise HTTPException(404, "Unknown frontend asset.")
        return FileResponse(path)

    return app


def serve_browser(namespace: dict[str, Any], config: Any, *, port: int = 0, open_browser: bool = True) -> None:
    import uvicorn

    if not (PROJECT_ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("The React interface must be built first: npm --prefix frontend run build")
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", port))
    listener.listen(128)
    assigned = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{assigned}"
    token = secrets.token_urlsafe(32)
    session = AgentSession(namespace, config)
    server: Any = None

    def shutdown():
        server.should_exit = True

    app = create_api(session, token, origin, shutdown=shutdown)
    class OwnedServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame: Any) -> None:
            threading.Thread(target=session.close, name="qubitz-signal-close", daemon=False).start()
            super().handle_exit(sig, frame)

    server = OwnedServer(uvicorn.Config(app, host="127.0.0.1", port=assigned, access_log=False,
                                      log_level="warning", timeout_graceful_shutdown=2))
    url = origin + "/#token=" + token
    print("Qubitz browser interface: " + url, flush=True)
    print("Use Close Qubitz in the interface for owned runtime cleanup. Closing a tab does not stop a task.", flush=True)

    def open_when_ready():
        import time

        while not server.started and not server.should_exit:
            time.sleep(0.05)
        if server.should_exit:
            return
        powershell = namespace.get("shutil").which("powershell.exe") if namespace.get("shutil") else None
        if namespace["_APP"].base.in_wsl() and powershell:
            command = [powershell, "-NoProfile", "-NonInteractive", "-Command", "Start-Process -FilePath '" + url + "'"]
            if Path("/init").is_file():
                command.insert(0, "/init")
            try:
                result = subprocess.run(command, capture_output=True, timeout=20, check=False)
                if result.returncode:
                    print("Browser launch failed; open the printed URL manually.", flush=True)
            except subprocess.SubprocessError:
                print("Browser launch timed out; open the printed URL manually.", flush=True)
        else:
            webbrowser.open(url)

    if open_browser:
        threading.Thread(target=open_when_ready, name="qubitz-open-browser", daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()

