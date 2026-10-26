"""One owned agent session, independent of a GUI or HTTP transport."""
from __future__ import annotations

import dataclasses
import importlib.util
import json
import math
import os
import re
import secrets
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .conversations import ConversationJournal
from .mtp_controls import configured_mtp, mtp_control_status
from .paths import PROJECT_ROOT, variant_path, variant_paths
from .variant_policy import user_variant_allowed


class SessionConflict(RuntimeError):
    """An action cannot be safely applied to the current session state."""


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reasoning_status(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        return {}
    if not isinstance(value, dict):
        return {}
    status: dict[str, Any] = {}
    for key in ("received", "forwarded"):
        if type(value.get(key)) is int and 0 <= value[key] <= 2**53 - 1:
            status[key] = value[key]
    if type(value.get("display_enabled")) is bool:
        status["display_enabled"] = value["display_enabled"]
    for key in ("reasoning_format", "chat_format"):
        if isinstance(value.get(key), str):
            status[key] = value[key][:40]
    return status


def load_public_variant(filename: str) -> dict[str, Any]:
    if not user_variant_allowed(filename):
        raise ValueError("Select a public Qubitz variant.")
    path = variant_path(filename)
    if path not in variant_paths():
        raise ValueError("Unknown public variant.")
    name = "qubitz_browser_" + secrets.token_hex(12)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("The selected variant cannot be loaded.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return vars(module)


class AgentSession:
    """Serial task ownership; reconnects only observe, never restart a request."""

    MAX_EVENTS = 2048
    MAX_EVENT_BYTES = 8 * 1024**2
    MAX_TASKS = 128

    def __init__(self, namespace: dict[str, Any], config: Any) -> None:
        self.namespace = namespace
        self.config = config
        self.runner: Any = None
        self.id = secrets.token_hex(16)
        self.condition = threading.Condition(threading.RLock())
        self._lifecycle_lock = threading.Lock()
        self.events: deque[dict[str, Any]] = deque()
        self.event_bytes = 0
        self.sequence = 0
        self.tasks: list[dict[str, Any]] = []
        self.queue: deque[str] = deque()
        self.messages: list[dict[str, Any]] = []
        self.active: str | None = None
        self.phase = "ready"
        self.error = ""
        self.receipts: dict[str, str] = {}
        self.tools: list[dict[str, Any]] = []
        self.skill_info: dict[str, Any] = {"count": None, "names": [], "warnings": []}
        self._worker: threading.Thread | None = None
        self._closed = False
        self._cleanup_failure = ""
        self._journal_error_reported = ""
        self.cleanup_receipt: dict[str, Any] = {}
        self._temperature_default = config.temperature
        self.journal = ConversationJournal(Path(config.runtime_workspace or PROJECT_ROOT))
        self.publish("status", "Ready. Model inference starts only when you submit a task.")

    def _broker(self) -> Any:
        return getattr(self.runner, "_tool_permission_broker", None)

    def _approvals(self) -> list[dict[str, Any]]:
        broker = self._broker()
        if broker is None:
            return []
        return [{"id": p.approval_id, "preview": p.preview, "tool": p.tool_name,
                 "prompt": p.request_prompt} for p in broker.pending.copy().values()]

    def publish(self, kind: str, text: str, task_id: str | None = None) -> None:
        with self.condition:
            self.sequence += 1
            text = str(text)
            event = {"sequence": self.sequence, "kind": kind, "text": text,
                     "task_id": task_id, "at": stamp()}
            if kind == "state":
                event["phase"] = self.phase
                event["error"] = self.error
                event["journal_error"] = self.journal.error
            elif kind == "error":
                event["journal_error"] = self.journal.error
            elif kind == "reasoning_status" and task_id:
                status = _reasoning_status(text)
                task = next((task for task in self.tasks if task["id"] == task_id), None)
                if status and task is not None:
                    task["reasoning_status"] = status
                    event["reasoning_status"] = status
            size = len(json.dumps(event, ensure_ascii=True).encode())
            # A snapshot and the journal remain authoritative when the event ring rolls.
            if size > self.MAX_EVENT_BYTES:
                event["text"] = text[:100000]
                event["truncated"] = True
                size = len(json.dumps(event, ensure_ascii=True).encode())
            event["_bytes"] = size
            self.events.append(event)
            self.event_bytes += size
            while len(self.events) > self.MAX_EVENTS or self.event_bytes > self.MAX_EVENT_BYTES:
                self.event_bytes -= self.events.popleft()["_bytes"]
            if kind not in {"state", "heartbeat", "reasoning_delta"}:
                self.journal.record(kind, text)
            if kind in {"user", "assistant", "error", "steer"}:
                self.messages.append({"id": self.sequence, "role": kind, "text": text[:1000000],
                                      "truncated": len(text) > 1000000, "task_id": task_id, "at": event["at"]})
                self.messages = self.messages[-128:]
            if kind in {"assistant_delta", "reasoning_delta", "reasoning"} and task_id:
                role = "reasoning" if kind != "assistant_delta" else "stream"
                message = next((m for m in reversed(self.messages)
                                if m["task_id"] == task_id and m["role"] == role), None)
                if message is None:
                    message = {"id": self.sequence, "role": role, "text": "", "task_id": task_id, "at": event["at"]}
                    self.messages.append(message)
                message["text"] = (message["text"] + text)[-100000:]
            while len(self.messages) > 128 or sum(len(m["text"]) for m in self.messages) > 8 * 1024**2:
                self.messages.pop(0)
            self.condition.notify_all()

    def snapshot(self) -> dict[str, Any]:
        with self.condition:
            self._check_journal_error()
            mtp = mtp_control_status(self.namespace["_APP"].base, self.config)
            return json.loads(json.dumps({
                "session_id": self.id, "sequence": self.sequence, "phase": self.phase,
                "error": self.error or self.journal.error, "active_task": self.active,
                "workspace": str(self.config.workspace),
                "variant": Path(self.namespace["__file__"]).name,
                "model": self.config.model_name, "model_filename": self.config.selected_model_filename,
                "model_loaded": getattr(self.runner, "_llm", None) is not None,
                "settings": {**{name: getattr(self.config, name, None) for name in (
                    "num_ctx", "num_predict", "temperature", "thinking_effort", "access_mode", "network_mode", "show_reasoning")},
                    "mtp_enabled": mtp["enabled"]},
                "mtp": mtp,
                "tasks": [{**{key: value for key, value in task.items() if key not in {"answer", "prompt"}},
                           "prompt": task["prompt"][:2000]} for task in self.tasks],
                "messages": self.messages, "approvals": self._approvals(),
                "tools": self.tools, "journal": self.journal.path.name,
                "events": [{k: v for k, v in e.items() if k != "_bytes"}
                           for e in list(self.events)[-80:] if e["kind"] not in {"assistant_delta", "reasoning_delta"}],
                "efforts": list(self.namespace.get("THINKING_EFFORT_OPTIONS", ())),
                "access_modes": self.namespace["ACCESS_MODE_LABELS"],
                "owned_cleanup": self.cleanup_receipt,
                "journal_error": self.journal.error,
                "runtime": {"embedding_model": getattr(self.config, "embed_model_name", None),
                            "backend_url": getattr(self.config, "server_url", None) or
                            getattr(self.namespace["_APP"].base, "DEFAULT_LLAMACPP_BASE_URL", None),
                            "skills": self.skill_info},
            }, default=str))

    def directory_listing(self, candidate: str | None = None) -> dict[str, Any]:
        """Browse immediate folders only; selecting one does not change the workspace."""
        if candidate is not None and (not candidate.strip() or len(candidate) > 4096):
            raise ValueError("Enter a bounded folder path.")
        with self.condition:
            base, current = self.namespace["_APP"].base, self.config.workspace
        directory = base.resolve_workspace_path(current, candidate or str(current),
                                                allow_missing=False, allow_external=True).resolve()
        if not directory.is_dir():
            raise ValueError("Select an existing folder, not a file.")
        entries = []
        truncated = False
        inaccessible = False
        with os.scandir(directory) as children:
            for index, child in enumerate(children):
                if index >= 4096:
                    truncated = True
                    break
                try:
                    if child.is_dir(follow_symlinks=False):
                        entries.append({"name": child.name, "path": str(directory / child.name)})
                except OSError:
                    inaccessible = True
                if len(entries) > 200:
                    truncated = True
                    break
        entries.sort(key=lambda entry: entry["name"].casefold())
        return {"path": str(directory), "parent": str(directory.parent) if directory.parent != directory else None,
                "directories": entries[:200], "truncated": truncated, "inaccessible": inaccessible}

    def events_after(self, after: int, wait: float = 10) -> tuple[bool, list[dict[str, Any]]]:
        with self.condition:
            if after == self.sequence and not self._closed:
                self.condition.wait(min(wait, 1))
            self._check_journal_error()
            gap = after > self.sequence or bool(self.events and after < self.events[0]["sequence"] - 1)
            return gap, [{k: v for k, v in e.items() if k != "_bytes"}
                         for e in self.events if e["sequence"] > after]

    def _check_journal_error(self) -> None:
        if self.journal.error and self.journal.error != self._journal_error_reported:
            self._journal_error_reported = self.journal.error
            self.error = self.journal.error
            self.publish("error", self.journal.error)

    def submit(self, prompt: str, request_id: str, *, approval: bool = False) -> str:
        if not prompt.strip() or len(prompt) > 200000:
            raise ValueError("A prompt must contain between 1 and 200000 characters.")
        with self.condition:
            if request_id in self.receipts:
                previous = next((t for t in self.tasks if t["id"] == self.receipts[request_id]), None)
                if previous is None or previous["prompt"] != prompt or previous["approval"] != approval:
                    raise SessionConflict("A request_id cannot be reused for a different action.")
                return self.receipts[request_id]
            if self.phase in {"closing", "closed", "transitioning", "cleanup_error"}:
                raise SessionConflict("The session cannot accept tasks in its current state.")
            if len(self.receipts) >= 1024 or len(self.tasks) >= self.MAX_TASKS:
                raise SessionConflict("This session reached its bounded task limit. Close and reopen Qubitz.")
            control = self._control_kind(prompt) if not approval else None
            if len(self.queue) >= 32 and control is None:
                raise SessionConflict("The prompt queue is full.")
            if self._approvals() and not approval and control is None:
                raise SessionConflict("Approve or decline the pending action before submitting another task.")
            task_id = secrets.token_hex(12)
            self.tasks.append({"id": task_id, "prompt": prompt, "state": "queued", "created_at": stamp(),
                               "approval": approval, "request_id": request_id, "answer": "", "elapsed": 0.0})
            self.receipts[request_id] = task_id
            if control is not None:
                self._complete_control(self.tasks[-1], control)
                return task_id
            (self.queue.appendleft if approval else self.queue.append)(task_id)
            self.publish("user", prompt, task_id)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="qubitz-web-runner", daemon=False)
                self._worker.start()
            self.publish("state", "Task queued.", task_id)
            return task_id

    @staticmethod
    def _control_kind(prompt: str) -> str | None:
        if re.fullmatch(r"\s*(?:stop|cancel|abort|interrupt)(?:\s+(?:this|the|my)?\s*(?:current\s+)?(?:task|request|run|operation))?[.!]?\s*", prompt, re.IGNORECASE):
            return "stop"
        if re.fullmatch(r"\s*(?:status|task status|what are you doing|are you running)[?!.]?\s*", prompt, re.IGNORECASE):
            return "status"
        if re.match(r"^/(?:bg|jobs|bg-jobs|job|cancel-job)(?:\s|$)", prompt.strip(), re.IGNORECASE):
            return "background_unavailable"
        return None

    def _complete_control(self, task: dict[str, Any], kind: str) -> None:
        task.update(control=kind, state="running", started_at=stamp())
        self.publish("user", task["prompt"], task["id"])
        try:
            if kind == "stop":
                queued = len(self.queue)
                for identifier in list(self.queue):
                    self.cancel(identifier)
                if self.active:
                    self.cancel(self.active)
                    answer = f"Cancellation requested at the active task's safe boundary; cleared {queued} queued task(s)."
                else:
                    answer = f"No foreground Qubitz task is running. Cleared {queued} queued task(s)."
            elif kind == "status":
                answer = f"Foreground task: {'running' if self.active else 'idle'}. Queued tasks: {len(self.queue)}."
            else:
                answer = "Background-job commands are not enabled in the browser interface. No job was started or changed."
            task.update(state="completed", answer=answer, ended_at=stamp())
            self.publish("assistant", answer, task["id"])
            self.publish("state", "Local control handled without model dispatch.", task["id"])
        except Exception as exc:
            task.update(state="error", ended_at=stamp())
            self.publish("error", f"Local control failed: {exc}", task["id"])
            raise

    def _refresh_skill_info(self) -> None:
        skills = getattr(self.runner, "skills", None)
        if skills is None:
            return
        try:
            summaries = skills.list_summaries()
            self.skill_info = {"count": skills.count(),
                               "names": [str(item.get("name") or item.get("skill_name") or "")[:200]
                                         for item in summaries[:50] if isinstance(item, dict)],
                               "warnings": [str(item)[:1000] for item in getattr(skills, "warnings", ())[:12]]}
        except Exception as exc:  # noqa: BLE001 - Optional UI diagnostics cannot break the task.
            self.skill_info = {"count": None, "names": [], "warnings": [f"Skill status unavailable: {exc}"]}

    def _work(self) -> None:
        while True:
            with self.condition:
                if not self.queue or self.phase in {"closing", "closed", "cleanup_error"}:
                    self._worker = None
                    return
                # A pending proposal is an interaction boundary, not permission to execute.
                next_id = self.queue[0]
                task = next(t for t in self.tasks if t["id"] == next_id)
                if self._approvals() and not task["approval"]:
                    self.phase = "awaiting_approval"
                    self._worker = None
                    return
                self.queue.popleft()
                self.active = task["id"]
                self.phase = "running"
                task.update(state="running", started_at=stamp())
                self.publish("state", "Task started.", self.active)
            start = time.monotonic()
            try:
                if self.runner is None:
                    self.runner = self.namespace["_APP"].base.AgentRunner(self.config)
                    self._refresh_skill_info()
                broker = self._broker()
                if broker is not None:
                    broker.conversation_journal = self.journal
                def deliver(kind: str, text: str, task: dict[str, Any] = task) -> None:
                    self.publish(kind, text, task["id"])
                    if task["state"] == "cancelling" and not self.runner._cancel_event.is_set():
                        self.runner.request_cancel()

                if task["state"] == "cancelling":
                    answer = "Cancelled before model dispatch.\n\n[Completion status: cancelled]"
                else:
                    answer = self.runner.run_sync(task["prompt"], deliver)
                with self.condition:
                    task["answer"] = answer
                    task["approval_ids"] = [item["id"] for item in self._approvals()]
                    task["state"] = (("cancelled" if "[Completion status: cancelled]" in answer else
                                      "completed_after_cancel") if task["state"] == "cancelling" else
                                     "awaiting_approval" if self._approvals() else "completed")
                    self.publish("assistant", answer, task["id"])
                    if broker is not None:
                        self.tools = [{key: item.get(key) for key in (
                            "tool", "success", "status", "paths", "error", "return_code", "execution_verified")}
                                      for item in list(broker.evidence)[-100:]]
            except Exception as exc:  # noqa: BLE001 - Keep runtime failures visible and do not retry actions.
                with self.condition:
                    task["state"] = "error"
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.publish("error", self.error, task["id"])
            finally:
                with self.condition:
                    task.update(elapsed=round(time.monotonic() - start, 3), ended_at=stamp())
                    self.active = None
                    if self.phase not in {"closing", "cleanup_error"}:
                        self.phase = "awaiting_approval" if self._approvals() else "ready"
                    self.publish("state", "Task ended; inspect its outcome and receipts.", task["id"])

    def cancel(self, task_id: str) -> None:
        with self.condition:
            task = next((t for t in self.tasks if t["id"] == task_id), None)
            if task is None:
                raise ValueError("Unknown task.")
            if task_id in self.queue:
                self.queue.remove(task_id)
                task["state"] = "cancelled"
            elif self.active == task_id:
                task["state"] = "cancelling"
                self.phase = "cancelling"
                if self.runner is not None:
                    self.runner.request_cancel()
            self.publish("state", "Cancellation requested; active work stops at its safe boundary.", task_id)

    def steer(self, text: str) -> None:
        with self.condition:
            if not self.active or self.phase != "running" or self.runner is None:
                raise SessionConflict("There is no active task accepting steering.")
            if not text.strip() or len(text) > 200000:
                raise ValueError("Enter a bounded steering message.")
            if not self.runner.submit_side_note(text):
                raise SessionConflict("The current step is no longer accepting steering.")
            self.publish("steer", text, self.active)

    def decide(self, approval_id: str, approve: bool, request_id: str) -> str | None:
        with self.condition:
            if request_id in self.receipts:
                previous = next((t for t in self.tasks if t["id"] == self.receipts[request_id]), None)
                if (approve and (previous is None or previous["prompt"] != "approve " + approval_id
                                 or not previous["approval"])) or (
                    not approve and self.receipts[request_id] != "declined:" + approval_id
                ):
                    raise SessionConflict("A request_id cannot be reused for a different decision.")
                return self.receipts[request_id]
            if self.active:
                raise SessionConflict("Wait for the active task before deciding an approval.")
            broker = self._broker()
            candidates = [(key, item) for key, item in (broker.pending.copy().items() if broker else ())
                          if item.approval_id == approval_id]
            if len(candidates) != 1:
                raise SessionConflict("This approval is unavailable or has expired.")
            key, _ = candidates[0]
            if approve:
                task_id = self.submit("approve " + approval_id, request_id, approval=True)
                self._record_approval_decision(approval_id, "approved")
                return task_id
            broker.pending.pop(key, None)
            broker.approved_once.discard(key)
            self.receipts[request_id] = "declined:" + approval_id
            self._record_approval_decision(approval_id, "declined")
            self.phase = "ready"
            self.publish("status", "Action declined: " + approval_id + ". Nothing was executed.")
            self.publish("state", "Approval decision recorded.")
            if self.queue and (self._worker is None or not self._worker.is_alive()):
                self._worker = threading.Thread(target=self._work, name="qubitz-web-runner", daemon=False)
                self._worker.start()
            return None

    def _record_approval_decision(self, approval_id: str, decision: str) -> None:
        for task in self.tasks:
            if task["state"] != "awaiting_approval" or approval_id not in task.get("approval_ids", ()):
                continue
            decisions = task.setdefault("approval_decisions", {})
            decisions[approval_id] = decision
            if all(identifier in decisions for identifier in task["approval_ids"]):
                task["state"] = "approval_decided"

    def configure(self, settings: dict[str, Any], *, workspace: str | None = None,
                  variant: str | None = None) -> None:
        if not self._lifecycle_lock.acquire(blocking=False):
            raise SessionConflict("An owned lifecycle transition is already in progress.")
        try:
            self._configure(settings, workspace=workspace, variant=variant)
        finally:
            self._lifecycle_lock.release()

    def _configure(self, settings: dict[str, Any], *, workspace: str | None,
                   variant: str | None) -> None:
        with self.condition:
            if self.active or self.queue or self.phase in {"closing", "closed", "transitioning", "cleanup_error"}:
                raise SessionConflict("Finish or cancel tasks before changing the session.")
            if self._approvals():
                raise SessionConflict("Decline pending approvals before changing their context.")
            values = dict(settings)
            allowed = {"num_ctx", "num_predict", "temperature", "thinking_effort", "access_mode", "network_mode", "show_reasoning", "mtp_enabled"}
            if set(values) - allowed:
                raise ValueError("Unknown setting.")
            for name in ("thinking_effort", "access_mode", "network_mode"):
                if name in values and not isinstance(values[name], str):
                    raise TypeError(f"{name} must be a string.")
            for name in ("num_ctx", "num_predict"):
                if name in values and (type(values[name]) is not int or not 1 <= values[name] <= 2000000):
                    raise ValueError(f"{name} must be a positive bounded integer.")
            if "temperature" in values and (isinstance(values["temperature"], bool)
                    or not isinstance(values["temperature"], (float, int))
                    or not math.isfinite(values["temperature"]) or not 0 <= values["temperature"] <= 10):
                raise ValueError("Temperature must be finite and between 0 and 10.")
            if "thinking_effort" in values and values["thinking_effort"] not in self.namespace["THINKING_EFFORT_OPTIONS"]:
                raise ValueError("Unknown reasoning effort.")
            if "access_mode" in values and values["access_mode"] not in self.namespace["ACCESS_MODE_LABELS"]:
                raise ValueError("Unknown access mode.")
            if "network_mode" in values and values["network_mode"] not in {"online", "offline"}:
                raise ValueError("Unknown network mode.")
            if "show_reasoning" in values and type(values["show_reasoning"]) is not bool:
                raise ValueError("show_reasoning must be boolean.")
            if "mtp_enabled" in values and type(values["mtp_enabled"]) is not bool:
                raise ValueError("MTP selection must be boolean.")
            self.phase = "transitioning"
            self.publish("state", "Applying session configuration.")
        cleanup_started = False
        try:
            namespace = load_public_variant(variant) if variant and variant != Path(self.namespace["__file__"]).name else self.namespace
            base = namespace["_APP"].base
            destination = base.resolve_workspace_path(self.config.workspace, workspace, allow_missing=False,
                                                       allow_external=True).resolve() if workspace else self.config.workspace
            if not Path(destination).is_dir():
                raise ValueError("Select an existing workspace directory.")
            if namespace is self.namespace:
                config = dataclasses.replace(self.config, workspace=destination)
                for name in ("thinking_effort", "access_mode", "network_mode", "show_reasoning"):
                    setattr(config, name, getattr(self.config, name))
                if hasattr(self.config, "mtp_enabled"):
                    config.mtp_enabled = self.config.mtp_enabled
                    config.llama_server_path = self.config.llama_server_path
            else:
                args = namespace["_APP"].parse_args(["--workspace", str(destination)])
                config = namespace["_APP"]._build_config(args, Path(destination))
                for name in ("access_mode", "network_mode", "show_reasoning"):
                    setattr(config, name, getattr(self.config, name))
            for name, value in values.items():
                if name != "mtp_enabled":
                    setattr(config, name, value)
            if "mtp_enabled" in values:
                config = configured_mtp(base, config, values["mtp_enabled"])
            if "temperature" in values:
                if abs(values["temperature"] - self._temperature_default) > 1e-12:
                    config._qubitz_manual_temperature_override = values["temperature"]
                elif hasattr(config, "_qubitz_manual_temperature_override"):
                    del config._qubitz_manual_temperature_override
            elif hasattr(self.config, "_qubitz_manual_temperature_override") and namespace is self.namespace:
                config._qubitz_manual_temperature_override = self.config._qubitz_manual_temperature_override
            if self.runner is not None:
                cleanup_started = True
                self._reset_owned_runner()
            with self.condition:
                self.runner = None
                self.namespace, self.config = namespace, config
                if variant:
                    self._temperature_default = namespace["_APP"].base.AgentConfig.__dataclass_fields__["temperature"].default
                os.environ[namespace["NETWORK_MODE_ENV_NAME"]] = config.network_mode
                os.environ[namespace["ACCESS_MODE_ENV_NAME"]] = config.access_mode
                self.phase = "ready"
                self.error = ""
                self.tools.clear()
                self.skill_info = {"count": None, "names": [], "warnings": []}
                self.publish("status", f"Session ready in {destination}. Agent context was reset; transcript retained.")
                self.publish("state", "Configuration applied.")
        except Exception as exc:
            with self.condition:
                self.phase = "cleanup_error" if cleanup_started else "ready"
                self.error = f"{type(exc).__name__}: {exc}"
                self.publish("error", self.error)
            raise

    def close(self) -> None:
        with self._lifecycle_lock:
            self._close()

    def _close(self) -> None:
        with self.condition:
            if self._closed:
                return
            if self._cleanup_failure:
                raise SessionConflict(self._cleanup_failure + "; uncertain cleanup is not automatically retried.")
            self.phase = "closing"
            self.config._qubitz_closing = True
            for task in self.tasks:
                if task["id"] in self.queue:
                    task["state"] = "cancelled"
            self.queue.clear()
            for task in self.tasks:
                if task["id"] == self.active:
                    task["state"] = "cancelling"
            if self.runner is not None:
                self.runner.config._qubitz_closing = True
                self.runner.request_cancel()
            self.publish("state", "Closing; waiting for owned work and cleanup.")
            worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join()
        try:
            if self.runner is not None:
                self._reset_owned_runner()
        except Exception as exc:
            with self.condition:
                self.phase = "cleanup_error"
                self.error = f"Owned cleanup failed: {exc}"
                self.publish("error", self.error)
            raise
        finally:
            self.journal.close(timeout=15)
        with self.condition:
            # Recording loss is not evidence that owned process cleanup failed.
            self._check_journal_error()
            self.phase = "closed"
            self._closed = True
            self.publish("state", "Session closed; owned cleanup completed.")
            self.condition.notify_all()

    def _reset_owned_runner(self) -> None:
        """Retain the manager's actual cleanup outcome before reset drops its reference."""
        manager = getattr(getattr(self.runner, "_llm", None), "server_process", None)
        required = getattr(manager, "_qubitz_owned_launch_epoch", None) is not None
        try:
            self.runner.reset_runtime()
            outcome = getattr(manager, "_qubitz_cleanup_outcome", None)
            if outcome is not None:
                self.cleanup_receipt = dict(outcome)
            if (required and not outcome) or (outcome and outcome.get("status") == "failed"):
                raise RuntimeError("The owned model server did not provide a confirmed cleanup receipt.")
        except Exception as exc:
            self._cleanup_failure = f"{type(exc).__name__}: {exc}"
            raise

