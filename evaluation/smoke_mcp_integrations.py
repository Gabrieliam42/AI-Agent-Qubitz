"""Live optional-MCP adapter smoke tests; no generation model or package installs.

Run with the same OS/interpreter as the configured MCP executables. Each case
uses a fresh worker and retained fixture directory, never a user's workspace.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import importlib.util
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qubitz_live_eval_policy import live_variant_allowed, require_live_variants_allowed
from runtime.paths import variant_path, variant_paths

PROVIDERS = (
    "serena", "context7", "playwright", "dbhub", "github", "firecrawl",
    "brave", "sequential_thinking",
)
LIMITATIONS = [
    "Adapter integration only: no model-driven tool selection or reasoning-quality evaluation.",
    "Qubitz GUI rendering is not observed; Playwright uses visible Chromium unless --headless.",
    "Browser console/network output is collected only for the Playwright case.",
    "HTTP server process lifecycle is remote and cannot be verified locally.",
    "Public research checks verify returned content/provenance, not every factual claim.",
    "Raw stdout/stderr and server logs are private diagnostic artifacts and may contain sensitive data.",
]


class Blocked(RuntimeError):
    """An explicitly missing prerequisite, not a passing test."""


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decoded_result(result):
    """Decode the provider result, not the wrapper's echo of input arguments."""
    payload = result.structuredContent or {}
    nested = payload.get("result", {})
    if nested.get("structuredContent") is not None:
        data = nested["structuredContent"]
    else:
        text = "\n".join(item.get("text", "") for item in nested.get("content", [])
                         if item.get("type") == "text")
        try:
            data = json.loads(text)
        except ValueError:
            data = text
    if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], str):
        try:
            return json.loads(data["result"])
        except ValueError:
            return data["result"]
    return data


def rendered(data):
    return data if isinstance(data, str) else json.dumps(data, ensure_ascii=True)


def urls(data):
    found = set()
    for value in re.findall(r'https?://[^\s<>"\\]+', rendered(data)):
        value = value.rstrip(".,;:)]}")
        parsed = urlsplit(value)
        if parsed.hostname and not parsed.username and not parsed.password:
            found.add(value)
    return sorted(found)


def result_error(data):
    return isinstance(data, dict) and (data.get("success") is False or bool(data.get("error")))


@contextmanager
def local_page(nonce, directory):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            if self.path not in {"/", "/clicked"}:
                self.send_error(404)
                return
            body = (
                '<!doctype html><title>Qubitz integration fixture</title>'
                f'<h1>{nonce}</h1><a href="/clicked">Verify fixture</a>'
                + (f'<p>clicked-{nonce}</p>' if self.path == "/clicked" else "")
                + f'<script>console.log("console-{nonce}")</script>'
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            with (directory / "http.log").open("a", encoding="utf-8") as stream:
                stream.write((fmt % args) + "\n")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        write_json(directory / "http_requests.json", requests)


class Probe:
    def __init__(self, module, host, provider, directory):
        self.module, self.host = module, host
        self.provider, self.directory = provider, directory
        self.started = time.monotonic()
        self.names = set()
        self.secrets = []
        for entry in host.capability_entries.values():
            variables = list(entry.get("env", {}).values())
            variables.append(entry.get("bearer_token_env", ""))
            self.secrets.extend(os.environ[name] for name in variables
                                if name in os.environ and len(os.environ[name]) >= 8)

    def log(self, event, data):
        text = json.dumps({"elapsed_seconds": time.monotonic() - self.started,
                           "event": event, "data": data}, default=str, ensure_ascii=True)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(text + "\n")

    async def invoke(self, name, arguments):
        self.log("request", {"tool": name, "arguments": arguments})
        result = await self.host.call_tool(name, arguments)
        self.log("response", result.model_dump(mode="json"))
        return result

    async def discover(self):
        require(not self.host.capability_sessions, "MCP session was started before discovery")
        catalog = await self.invoke("discover_mcp_capability", {})
        require(not catalog.isError, "Metadata discovery failed")
        require(not self.host.capability_sessions, "Metadata-only discovery launched a server")
        result = await self.invoke("discover_mcp_capability", {"capability": self.provider})
        require(not result.isError, f"Provider initialization failed: {result.structuredContent}")
        self.names = {tool["name"] for tool in result.structuredContent["tools"]}
        require(bool(self.names), "No permitted live tools advertised")

    async def call(self, tool, arguments, *, expect_error=False):
        require(tool in self.names, f"Required tool {tool!r} missing from live discovery")
        schema = await self.invoke("discover_mcp_capability", {
            "capability": self.provider, "tool_name": tool,
        })
        require(not schema.isError, "Live schema retrieval failed")
        import jsonschema
        jsonschema.validate(arguments, schema.structuredContent["tool"]["inputSchema"])
        result = await self.invoke("call_project_mcp_tool", {
            "server_reference": f"capability:{self.provider}", "tool_name": tool,
            "arguments": arguments,
        })
        data = decoded_result(result)
        failed = bool(result.isError) or result_error(data)
        require(failed if expect_error else not failed,
                f"Unexpected result for {tool}: {result.structuredContent}")
        return data


def configure_fixture(host, args, workspace, directory):
    """Only in-memory test overlays; never write the operator registry."""
    entry = copy.deepcopy(host.capability_entries.get(args.provider, {}))
    if args.provider == "dbhub" and args.disposable_dbhub:
        if not entry.get("command") or entry.get("transport", "stdio") != "stdio":
            raise Blocked("No installed stdio DBHub launcher is configured")
        argv = list(entry.get("args", []))
        # Discard connection arguments: preserve just a configured Node JS entrypoint.
        launcher = argv[:1] if argv and argv[0].endswith((".js", ".mjs", ".cjs")) else []
        if not launcher and any(a.endswith((".js", ".mjs", ".cjs")) for a in argv):
            raise Blocked("DBHub launcher shape is not supported safely")
        database = workspace / "probe.sqlite"
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE probe (value TEXT NOT NULL)")
            connection.execute("INSERT INTO probe VALUES (?)", (args.nonce,))
        config = workspace / "dbhub.toml"
        config.write_text(
            '[[sources]]\nid = "probe"\ndsn = '
            + json.dumps("sqlite://" + str(database))
            + '\n[[tools]]\nname = "execute_sql"\nsource = "probe"\nreadonly = true\nmax_rows = 10\n',
            encoding="utf-8",
        )
        entry.update(enabled=True, cost_policy="self_hosted", env={},
                     runtime_env={}, workspaces=[str(workspace)],
                     read_only_credentials_confirmed=True,
                     args=[*launcher, "--transport", "stdio", "--config", str(config)])
    elif args.provider == "dbhub":
        raise Blocked("DBHub requires --disposable-dbhub; real configured databases are never tested")
    if entry.get("enabled") is not True:
        raise Blocked("Provider is disabled or unconfigured")
    if entry.get("cost_policy") not in {"free", "self_hosted"}:
        raise Blocked("No explicit free/self-hosted entitlement")
    for source in [*entry.get("env", {}).values(), entry.get("bearer_token_env")]:
        if source and not os.environ.get(source):
            raise Blocked(f"Missing environment variable: {source}")
    if args.provider not in {"serena", "sequential_thinking"} and not args.online:
        raise Blocked("Network-capable provider requires explicit --online")
    scopes = entry.get("workspaces", [])
    if scopes and str(workspace) not in scopes:
        raise Blocked("Operator workspace binding excludes the disposable fixture")
    if args.provider == "playwright":
        argv = list(entry.get("args", []))
        # The approved test profile must not reuse or attach to a user's browser.
        for flag in ("--cdp-endpoint", "--endpoint", "--extension", "--user-data-dir"):
            if any(item == flag or item.startswith(flag + "=") for item in argv):
                raise Blocked("Playwright config attaches to a non-disposable browser")
        filtered = []
        skip = False
        for item in argv:
            if skip:
                skip = False
            elif item == "--output-dir":
                skip = True
            elif item == "--headless" or item.startswith("--output-dir="):
                continue
            else:
                filtered.append(item)
        if not args.headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise Blocked("No display available for visible Chromium")
        entry["args"] = [*filtered, "--output-dir", str(directory / "browser")]
        if args.headless:
            entry["args"].append("--headless")
    host.capability_entries = {args.provider: entry}


async def exercise(probe, args, workspace, directory):
    nonce = args.nonce
    if args.provider == "serena":
        target = workspace / "sample.py"
        before = sha256(target)
        data = await probe.call("get_symbols_overview", {"relative_path": "sample.py"})
        require(nonce in rendered(data), "Serena did not locate the fixture symbol")
        denied = await probe.invoke("call_project_mcp_tool", {
            "server_reference": "capability:serena", "tool_name": "replace_symbol_body",
            "arguments": {"relative_path": "sample.py", "name_path": nonce, "body": "pass"},
        })
        require(denied.isError and denied.structuredContent.get("effect") == "UNKNOWN",
                "Serena mutation was not rejected by the wrapper")
        require(sha256(target) == before, "Serena unexpectedly modified the fixture")
    elif args.provider == "context7":
        data = await probe.call("resolve-library-id", {
            "libraryName": "Python", "query": "Python pathlib Path.read_text documentation",
        })
        require("/python/cpython" in rendered(data), "Python library ID not resolved")
        docs = await probe.call("query-docs", {
            "libraryId": "/python/cpython", "query": "pathlib Path.read_text encoding parameter",
        })
        require("read_text" in rendered(docs) and bool(urls(docs)),
                "Documentation lacks requested API or source URLs")
    elif args.provider == "playwright":
        with local_page(nonce, directory) as (url, requests):
            await probe.call("browser_navigate", {"url": url})
            snapshot = rendered(await probe.call("browser_snapshot", {}))
            require(nonce in snapshot, "Browser snapshot lacks fixture nonce")
            match = re.search(r'link "Verify fixture"[^\n]*\[ref=([^\]]+)\]', snapshot)
            require(match is not None, "Clickable link reference absent from snapshot")
            await probe.call("browser_click", {"element": "Verify fixture link", "ref": match[1]})
            snapshot = rendered(await probe.call("browser_snapshot", {}))
            require(f"clicked-{nonce}" in snapshot and "/clicked" in requests,
                    "Click did not reach the fixture's verified state")
            console = await probe.call("browser_console_messages", {})
            require(f"console-{nonce}" in rendered(console), "Console evidence missing")
            await probe.call("browser_network_requests", {})
            await probe.call("browser_close", {})
            tabs = rendered(await probe.call("browser_tabs", {"action": "list"}))
            require("No open tabs" in tabs, "Browser tabs remain after explicit close")
    elif args.provider == "dbhub":
        data = await probe.call("execute_sql", {"sql": "SELECT value FROM probe"})
        require(isinstance(data, dict) and data.get("success") is True, "DB query unsuccessful")
        statements = data.get("data", {}).get("statements", [])
        require(any(s.get("rows") == [{"value": nonce}] for s in statements), "Wrong DB rows")
        await probe.call("execute_sql", {"sql": "DELETE FROM probe"}, expect_error=True)
        with sqlite3.connect(f"file:{workspace / 'probe.sqlite'}?mode=ro", uri=True) as connection:
            require(connection.execute("SELECT value FROM probe").fetchall() == [(nonce,)],
                    "Read-only DB guard failed: fixture data changed")
    elif args.provider == "github":
        data = await probe.call("get_file_contents", {
            "owner": "octocat", "repo": "Hello-World", "path": "README", "ref": "refs/heads/master",
        })
        text = rendered(data)
        require("Hello World" in text or "SGVsbG8gV29ybGQ" in text,
                "GitHub returned no recognizable public README content")
        require(not probe.host.capability_entries["github"].get("allow_remote_mutations"),
                "GitHub remote mutations must be disabled for this suite")
    elif args.provider == "firecrawl":
        data = await probe.call("firecrawl_scrape", {"url": "https://example.com", "formats": ["markdown"]})
        require("Example Domain" in rendered(data), "Expected public page content missing")
        sources = urls(data)
        require(any(urlsplit(url).hostname == "example.com" for url in sources),
                "Scrape has no matching source provenance")
    elif args.provider == "brave":
        data = await probe.call("brave_web_search", {"query": "site:docs.python.org pathlib read_text", "count": 3})
        require(any(urlsplit(url).hostname == "docs.python.org" for url in urls(data)),
                "Search returned no official documentation source")
    elif args.provider == "sequential_thinking":
        data = await probe.call("sequentialthinking", {
            "thought": "Integration fixture: one plus one equals two.",
            "thoughtNumber": 1, "totalThoughts": 1, "nextThoughtNeeded": False,
        })
        require(isinstance(data, dict) and data.get("thoughtNumber") == 1
                and data.get("totalThoughts") == 1 and data.get("nextThoughtNeeded") is False
                and data.get("thoughtHistoryLength") == 1, "Wrong structured thinking response")


async def worker(args, directory):
    require_live_variants_allowed([args.variant])
    workspace = directory / "workspace"
    workspace.mkdir()
    (workspace / "sample.py").write_text(f"def {args.nonce}(left, right):\n    return left + right\n", encoding="utf-8")
    os.chdir(workspace)
    spec = importlib.util.spec_from_file_location("qubitz_mcp_smoke_variant", variant_path(args.variant, ROOT))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    os.environ[module.NETWORK_MODE_ENV_NAME] = "online" if args.online else "offline"
    host = module._APP.base.MCPHost(workspace)
    broker = module._ToolPermissionBroker(module._APP.base, workspace, ROOT)
    broker.set_access_mode(module.ACCESS_MODE_FULL if args.provider == "playwright" else module.ACCESS_MODE_READ_ONLY)
    prompt = f"Use {args.provider} to verify the disposable integration fixture."
    if args.provider == "playwright":
        prompt += " Open the local page in Chromium, click the fixture link, verify it, and close the browser."
    broker.begin_turn(prompt, lambda kind, text: print(f"[{kind}] {text}", flush=True))
    token = module._TOOL_CALL_CONTEXT.set(broker)
    probe = Probe(module, host, args.provider, directory)
    started = time.monotonic()
    result = {"variant": args.variant, "provider": args.provider, "status": "failed",
              "prompt": prompt, "limitations": LIMITATIONS}
    try:
        if host.capability_config_error:
            raise Blocked(host.capability_config_error)
        configure_fixture(host, args, workspace, directory)
        if args.provider == "github":
            host.capability_entries["github"]["allow_remote_mutations"] = False
        async with asyncio.timeout(args.timeout - 20):
            await probe.discover()
            await exercise(probe, args, workspace, directory)
        result["status"] = "passed"
    except Blocked as exc:
        result.update(status="blocked", reason=str(exc))
    except Exception as exc:  # A test failure must retain its evidence and permit cleanup.
        result.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        try:
            async with asyncio.timeout(15):
                await host.__aexit__(None, None, None)
            sessions = host.capability_sessions
            result["session_cleanup"] = {
                name: {"owner_task_done": session.task.done(), "stderr_log": str(session.stderr_path)}
                for name, session in sessions.items()
            }
            require(all(session.task.done() for session in sessions.values()), "Session owner did not exit")
        except Exception as exc:
            result.update(status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
        module._TOOL_CALL_CONTEXT.reset(token)
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        probe.log("case_result", result)
        write_json(directory / "result.json", result)
    return 0 if result["status"] == "passed" else 2 if result["status"] == "blocked" else 1


def process_group_members(group):
    import psutil
    members = []
    for process in psutil.process_iter(["pid", "create_time", "name"]):
        try:
            if os.getpgid(process.pid) == group:
                members.append(process.info)
        except (ProcessLookupError, PermissionError, psutil.NoSuchProcess):
            continue
    return members


def stop_group(group):
    # Each worker starts a new POSIX session. Never signal a shared/user process group.
    if group == os.getpgrp():
        raise RuntimeError("Refusing to terminate coordinator process group")
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not process_group_members(group):
            return
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            return
        time.sleep(1)


def run_case(args, variant, provider, directory):
    directory.mkdir(parents=True)
    nonce = "probe_" + uuid.uuid4().hex
    command = [sys.executable, "-u", str(Path(__file__).resolve()), "--worker",
               "--variant", variant, "--provider", provider, "--output", str(directory),
               "--nonce", nonce, "--timeout", str(args.timeout)]
    for flag, enabled in (("--online", args.online), ("--headless", args.headless),
                          ("--disposable-dbhub", args.disposable_dbhub)):
        if enabled:
            command.append(flag)
    started = time.monotonic()
    with (directory / "stdout.log").open("w", encoding="utf-8") as out, \
            (directory / "stderr.log").open("w", encoding="utf-8") as err:
        process = subprocess.Popen(command, cwd=ROOT, stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            with (directory / "processes.jsonl").open("w", encoding="utf-8") as trace:
                while process.poll() is None:
                    trace.write(json.dumps({"elapsed_seconds": time.monotonic() - started,
                                            "processes": process_group_members(process.pid)}) + "\n")
                    trace.flush()
                    if time.monotonic() - started >= args.timeout:
                        timed_out = True
                        break
                    time.sleep(1)
        finally:
            remaining = process_group_members(process.pid)
            if remaining:
                stop_group(process.pid)
            process.wait(timeout=10)
    receipt = directory / "result.json"
    result = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {
        "variant": variant, "provider": provider, "status": "failed", "reason": "Worker produced no result",
    }
    result.update(return_code=process.returncode, wall_seconds=round(time.monotonic() - started, 3),
                  timeout=timed_out, residual_processes=remaining,
                  process_cleanup_verified=not process_group_members(process.pid))
    if timed_out or remaining or not result["process_cleanup_verified"]:
        result["status"] = "failed"
    if process.returncode not in {0, 2}:
        result["status"] = "failed"
    write_json(receipt, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variants", nargs="+", default=["AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py"])
    parser.add_argument("--all-variants", action="store_true")
    parser.add_argument("--providers", nargs="+", choices=PROVIDERS, default=list(PROVIDERS))
    parser.add_argument("--online", action="store_true", help="Authorize configured free/self-hosted research calls")
    parser.add_argument("--disposable-dbhub", action="store_true", help="Use configured DBHub executable with fixture-only SQLite config")
    parser.add_argument("--headless", action="store_true", help="Opt out of visible Chromium")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--variant", help=argparse.SUPPRESS)
    parser.add_argument("--provider", choices=PROVIDERS, help=argparse.SUPPRESS)
    parser.add_argument("--nonce", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Use the project WSL/Linux interpreter; the configured MCP servers are Linux executables")
    if args.timeout < 60:
        parser.error("--timeout must be at least 60 seconds, including cleanup reserve")
    if args.worker:
        require_live_variants_allowed([args.variant])
        return asyncio.run(worker(args, args.output.resolve()))
    variants = [p.name for p in variant_paths(ROOT) if live_variant_allowed(p.name)] if args.all_variants else args.variants
    require_live_variants_allowed(variants)
    for name in variants:
        if Path(name).name != name or not name.startswith("AI_Agent_Qubitz_") or not variant_path(name, ROOT).is_file():
            parser.error(f"Not a current variants-directory launcher: {name}")
    output = (args.output or ROOT / "evaluation" / "results" / ("mcp_smoke_" + uuid.uuid4().hex)).resolve()
    if not output.is_relative_to(ROOT / "evaluation" / "results"):
        parser.error("Output must stay under evaluation/results")
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    lock = ROOT / "evaluation" / ".mcp_integration_smoke.lock"
    import fcntl
    with lock.open("a+", encoding="utf-8") as lock_stream:
        try:
            fcntl.flock(lock_stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another MCP integration suite is active")
        from runtime import runtime_dependency_paths

        paths = ([variant_path(name, ROOT) for name in variants] + runtime_dependency_paths(ROOT)
                 + [Path(__file__).resolve(), ROOT / ".qubitz/mcp_capabilities.json"])
        hashes = {str(path): sha256(path) if path.exists() else None for path in paths}
        report = {"schema_version": 1, "status": "running", "source_hashes": hashes,
                  "limitations": LIMITATIONS, "cases": []}
        write_json(output / "results.json", report)
        try:
            for name in dict.fromkeys(variants):
                for provider in dict.fromkeys(args.providers):
                    require(all((sha256(Path(p)) if Path(p).exists() else None) == h for p, h in hashes.items()),
                            "Frozen source/configuration changed; stop the campaign")
                    result = run_case(args, name, provider, output / Path(name).stem / provider)
                    report["cases"].append(result)
                    write_json(output / "results.json", report)
                    if result["status"] != "passed":
                        print(f"{name}: {provider}: {result['status']}", flush=True)
                    require(result["process_cleanup_verified"], "Owned process cleanup failed; stopping suite")
            report["status"] = "complete"
        except BaseException:
            report["status"] = "interrupted"
            raise
        finally:
            report["counts"] = {status: sum(row["status"] == status for row in report["cases"])
                                for status in ("passed", "blocked", "failed")}
            write_json(output / "results.json", report)
    return 1 if report["counts"]["failed"] else 2 if report["counts"]["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
