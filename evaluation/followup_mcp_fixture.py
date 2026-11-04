"""Real local stdio MCP fixture; never connects to an external provider."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

MANAGED_SERVER = '''import json
import signal
import time
from pathlib import Path
running = True
def stop(*_args):
    global running
    running = False
signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
Path('server_state.json').write_text(json.dumps({'state': 'ready'}))
print('READY', flush=True)
try:
    while running:
        time.sleep(0.05)
finally:
    Path('server_state.json').write_text(json.dumps({'state': 'stopped'}))
'''


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.session_id = uuid.uuid4().hex

    def read(self, name: str, default: Any) -> Any:
        path = self.root / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def write(self, name: str, payload: Any) -> None:
        path = self.root / name
        temporary = path.with_suffix(path.suffix + ".pending")
        temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    def audit(self, tool: str, **data: Any) -> dict[str, Any]:
        row = {"tool": tool, "pid": os.getpid(), "session_id": self.session_id, **data}
        with (self.root / "mcp_audit.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, sort_keys=True) + "\n")
        return data

    def create(self, label: str, value: int, tool: str = "create_record") -> dict[str, Any]:
        records = self.read("records.json", {})
        record_id = uuid.uuid4().hex
        records[record_id] = {"label": label, "value": value}
        self.write("records.json", records)
        self.audit(tool, record_id=record_id, label=label, value=value)
        return {"record_id": record_id, "label": label, "value": value}

    def get(self, record_id: str) -> dict[str, Any]:
        records = self.read("records.json", {})
        if record_id not in records:
            self.audit("get_record", record_id=record_id, error="unknown_id")
            raise ValueError("Unknown record_id; use the ID returned by create_record")
        row = {"record_id": record_id, **records[record_id]}
        self.audit("get_record", **row)
        return row

    def update(self, record_id: str, value: int) -> dict[str, Any]:
        records = self.read("records.json", {})
        if record_id not in records:
            self.audit("update_record", record_id=record_id, error="unknown_id")
            raise ValueError("Unknown record_id; do not invent IDs")
        records[record_id]["value"] = value
        self.write("records.json", records)
        row = {"record_id": record_id, **records[record_id]}
        self.audit("update_record", **row)
        return row

    def flaky(self, value: str) -> dict[str, str]:
        attempts = int(self.read("transient.json", {"attempt": 0})["attempt"]) + 1
        self.write("transient.json", {"attempt": attempts})
        self.audit("flaky_read", attempt=attempts, value=value)
        if attempts == 1:
            raise ValueError("Injected transient read failure; no mutation was performed")
        return {"value": value}


def main() -> None:
    from mcp.server.fastmcp import FastMCP

    store = Store(Path(__file__).resolve().parent)
    server = FastMCP("qubitz-followup-fixture")

    @server.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def echo(value: str, label: str) -> dict[str, Any]:
        """Return the exact requested value and label without changing them."""
        return store.audit("echo", value=value, label=label)

    @server.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def typed_echo(value: int, label: str) -> dict[str, Any]:
        """Return an integer value and an exact string label; only value accepts integer conversion."""
        return store.audit("typed_echo", value=value, label=label)

    @server.tool()
    def create_record(label: str, value: int) -> dict[str, Any]:
        """Create exactly one record and return its generated record_id."""
        return store.create(label, value)

    @server.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def get_record(record_id: str) -> dict[str, Any]:
        """Get a record using the exact previously returned record_id."""
        return store.get(record_id)

    @server.tool()
    def update_record(record_id: str, value: int) -> dict[str, Any]:
        """Change the existing record's value while preserving its label."""
        return store.update(record_id, value)

    @server.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def list_records() -> dict[str, Any]:
        """Read current records without creating, updating or replaying a mutation."""
        records = store.read("records.json", {})
        store.audit("list_records", count=len(records))
        return {"records": records}

    @server.tool(annotations={"readOnlyHint": True, "idempotentHint": True})
    def flaky_read(value: str) -> dict[str, str]:
        """First call fails deliberately; subsequent calls return the exact value."""
        return store.flaky(value)

    @server.tool()
    def commit_then_error(label: str, value: int) -> dict[str, Any]:
        """Commit once, then raise: the caller must inspect state, not blindly replay."""
        store.create(label, value, "commit_then_error")
        raise ValueError("Injected error after commit; outcome requires read-back, do not replay")

    @server.tool()
    async def wait_for_release() -> dict[str, bool]:
        """Wait for the test driver's release, creating a safe steering boundary."""
        store.write("waiting.json", {"waiting": True})
        store.audit("wait_for_release")
        release = store.root / ".cache" / "followup_controls" / "release.flag"
        for _ in range(300):
            if release.exists():
                return {"released": True}
            await asyncio.sleep(0.1)
        raise TimeoutError("Test driver did not release the bounded fixture wait")

    server.run(transport="stdio")


if __name__ == "__main__":
    main()
