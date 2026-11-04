"""Model-free controller, transport boundary and browser-entrypoint contracts."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from runtime import browser_entrypoint, web_controller
from runtime.web_api import create_api
from runtime.web_controller import AgentSession, SessionConflict


@dataclass
class Config:
    workspace: Path
    runtime_workspace: Path
    model_name: str = "fixture-no-model"
    selected_model_filename: str = "not-loaded.gguf"
    num_ctx: int = 4096
    num_predict: int = 1024
    temperature: float = 0.7
    thinking_effort: str = "high"
    access_mode: str = "read_only"
    network_mode: str = "offline"
    show_reasoning: bool = True


class Journal:
    error = ""

    def __init__(self, root):
        self.path = root / "fixture.txt"
        self.records = []

    def record(self, role, text):
        self.records.append((role, text))
        return True

    def snapshot(self):
        return str(self.records)

    def close(self, timeout=0):
        pass


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr(web_controller, "ConversationJournal", Journal)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Runner:
        def __init__(self, config):
            self.config = config
            self._cancel_event = threading.Event()
            self._tool_permission_broker = SimpleNamespace(pending={}, approved_once=set(), evidence=[])

        def run_sync(self, prompt, callback):
            calls.append(prompt)
            entered.set()
            assert release.wait(3), "fixture worker was not released"
            callback("assistant_delta", "stream")
            return "[Completion status: cancelled]" if self._cancel_event.is_set() else "Fixture result"

        def request_cancel(self):
            self._cancel_event.set()

        def reset_runtime(self):
            pass

        def submit_side_note(self, text):
            calls.append("steer:" + text)
            return True

    def resolve(workspace, candidate, **_kwargs):
        path = Path(candidate)
        return path if path.is_absolute() else workspace / path

    namespace = {"__file__": "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py",
                 "_APP": SimpleNamespace(base=SimpleNamespace(AgentRunner=Runner, AgentConfig=Config,
                                                               resolve_workspace_path=resolve)),
                 "THINKING_EFFORT_OPTIONS": ("high",),
                 "ACCESS_MODE_LABELS": {"read_only": "Read-only", "plan": "Plan Mode", "full": "Full Access"},
                 "NETWORK_MODE_ENV_NAME": "QUBITZ_NETWORK_MODE", "ACCESS_MODE_ENV_NAME": "QUBITZ_ACCESS_MODE"}
    value = AgentSession(namespace, Config(tmp_path, tmp_path))
    yield value, calls, entered, release
    release.set()
    value.close()


def finish(value):
    deadline = time.monotonic() + 4
    while value.active or value.queue:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_model_is_lazy_and_duplicate_commands_are_not_replayed(session):
    value, calls, entered, release = session
    assert value.runner is None
    first = value.submit("fixture", "a" * 32)
    assert entered.wait(2)
    assert value.submit("fixture", "a" * 32) == first
    with pytest.raises(SessionConflict):
        value.submit("different", "a" * 32)
    release.set()
    finish(value)
    assert calls == ["fixture"]
    assert value.tasks[0]["state"] == "completed"


def test_cancellation_and_steering_do_not_dispatch_queued_work(session):
    value, calls, entered, release = session
    active = value.submit("first", "a" * 32)
    assert entered.wait(2)
    queued = value.submit("second", "b" * 32)
    value.steer("follow the scope")
    value.cancel(queued)
    value.cancel(active)
    release.set()
    finish(value)
    assert calls == ["first", "steer:follow the scope"]
    assert {task["state"] for task in value.tasks} == {"cancelled"}


def test_event_gap_is_observation_not_dispatch(session):
    value, calls, _, _ = session
    value.MAX_EVENTS = 2
    for number in range(4):
        value.publish("status", str(number))
    gap, events = value.events_after(0, wait=0)
    assert gap and len(events) == 2
    assert value.snapshot()["sequence"] == events[-1]["sequence"]
    assert not calls and value.runner is None
    assert value.events_after(value.sequence + 99, wait=0)[0]


def test_config_uses_actual_broker_modes_and_restores_temperature_profile(session, monkeypatch):
    value, _, _, _ = session
    monkeypatch.setenv("QUBITZ_NETWORK_MODE", "offline")
    monkeypatch.setenv("QUBITZ_ACCESS_MODE", "read_only")
    value.configure({"access_mode": "full", "temperature": 0.8})
    assert value.config.access_mode == "full"
    assert value.config._qubitz_manual_temperature_override == 0.8
    value.configure({"temperature": 0.7})
    assert not hasattr(value.config, "_qubitz_manual_temperature_override")
    with pytest.raises(ValueError, match="access mode"):
        value.configure({"access_mode": "Full Access"})


def test_approval_pauses_existing_queue_and_decision_is_bound_once(session):
    value, calls, entered, release = session
    first = value.submit("first", "a" * 32)
    assert entered.wait(2)
    second = value.submit("second", "b" * 32)
    pending = SimpleNamespace(approval_id="abc1234567", preview="exact command", tool_name="run_command", request_prompt="first")
    value.runner._tool_permission_broker.pending["fingerprint"] = pending
    release.set()
    deadline = time.monotonic() + 3
    while value.active or value.phase != "awaiting_approval":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert calls == ["first"]
    assert list(value.queue) == [second]
    assert value.tasks[0]["id"] == first
    value.decide(pending.approval_id, False, "c" * 32)
    assert value.tasks[0]["state"] == "approval_decided"
    assert value.tasks[0]["approval_decisions"] == {pending.approval_id: "declined"}
    assert value.decide(pending.approval_id, False, "c" * 32) == "declined:" + pending.approval_id
    with pytest.raises(SessionConflict):
        value.decide(pending.approval_id, True, "c" * 32)
    finish(value)
    assert calls == ["first", "second"]


@pytest.mark.parametrize("settings", [{"temperature": float("nan")}, {"num_ctx": True},
                                     {"num_predict": -1}, {"network_mode": []}, {"show_reasoning": "yes"}])
def test_invalid_settings_do_not_reset_context(session, settings):
    value, calls, _, _ = session
    config = value.config
    with pytest.raises((ValueError, TypeError)):
        value.configure(settings)
    assert value.config is config and value.phase == "ready" and not calls


def test_cleanup_receipt_failure_is_held_without_retry(session):
    value, _, _, _ = session
    resets = []
    manager = SimpleNamespace(_qubitz_owned_launch_epoch=1, _qubitz_cleanup_outcome={"status": "failed"})
    value.runner = SimpleNamespace(config=value.config, _llm=SimpleNamespace(server_process=manager),
                                   request_cancel=lambda: None, reset_runtime=lambda: resets.append(True))
    with pytest.raises(RuntimeError, match="confirmed cleanup"):
        value.close()
    assert value.phase == "cleanup_error" and len(resets) == 1
    with pytest.raises(SessionConflict, match="not automatically retried"):
        value.close()
    assert len(resets) == 1
    # The fixture's teardown must not replay the failed cleanup either.
    value._closed = True


def test_confirmed_cleanup_and_terminal_event_are_preserved(session):
    value, _, _, _ = session
    manager = SimpleNamespace(_qubitz_owned_launch_epoch=1, _qubitz_cleanup_outcome={"status": "stopped"})
    value.runner = SimpleNamespace(config=value.config, _llm=SimpleNamespace(server_process=manager),
                                   request_cancel=lambda: None, reset_runtime=lambda: None)
    value.close()
    assert value.snapshot()["owned_cleanup"] == {"status": "stopped"}
    assert value.events[-1]["phase"] == "closed"


def test_recording_failure_is_visible_once_and_not_mislabeled_as_owned_cleanup(session):
    value, _, _, _ = session
    value.journal.error = "Conversation retention incomplete: fixture disk failure"
    _, events = value.events_after(value.sequence, wait=0)
    assert events[-1]["kind"] == "error" and "fixture disk failure" in events[-1]["text"]
    assert value.snapshot()["journal_error"] == value.journal.error
    assert not value.events_after(value.sequence, wait=0)[1]
    value.close()
    assert value.phase == "closed" and value.events[-1]["journal_error"] == value.journal.error
    assert not value._cleanup_failure


def test_incomplete_journal_cannot_be_copied_as_complete(session, tmp_path):
    value, _, _, _ = session
    value.journal.error = "Conversation retention incomplete: fixture overflow"
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/transcript", headers={"x-qubitz-token": "secret"})
        assert response.status_code == 503 and response.json()["detail"] == value.journal.error


def test_approval_acceptance_precedes_preexisting_queue(session):
    value, calls, entered, release = session
    value.submit("first", "a" * 32)
    assert entered.wait(2)
    value.submit("second", "b" * 32)
    broker = value.runner._tool_permission_broker
    pending = SimpleNamespace(approval_id="exact123", preview="fixture only", tool_name="fixture", request_prompt="first")
    broker.pending["exact"] = pending
    original = value.runner.run_sync

    def consume_approval(prompt, callback):
        if prompt == "approve exact123":
            broker.pending.clear()
        return original(prompt, callback)

    value.runner.run_sync = consume_approval
    release.set()
    deadline = time.monotonic() + 3
    while value.active or value.phase != "awaiting_approval":
        assert time.monotonic() < deadline
        time.sleep(0.01)
    approved = value.decide("exact123", True, "c" * 32)
    assert value.tasks[0]["state"] == "approval_decided"
    assert value.tasks[0]["approval_decisions"] == {"exact123": "approved"}
    assert value.decide("exact123", True, "c" * 32) == approved
    finish(value)
    assert calls == ["first", "approve exact123", "second"]


def test_invalid_workspace_keeps_original_context(session):
    value, _, _, _ = session
    original = value.config
    with pytest.raises(ValueError, match="existing workspace"):
        value.configure({}, workspace="missing-directory")
    assert value.config is original and value.phase == "ready"


def test_api_requires_authentication_same_origin_and_json(session, tmp_path):
    value, calls, entered, release = session
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.get("/api/state").status_code == 401
        headers = {"x-qubitz-token": "secret", "origin": "http://127.0.0.1:8765"}
        assert client.get("/api/state", headers=headers).status_code == 200
        assert client.get("/api/state", headers={**headers, "origin": "https://untrusted.example"}).status_code == 403
        assert client.get("/api/state", headers={**headers, "host": "untrusted.example"}).status_code == 403
        assert client.post("/api/tasks", headers=headers, content="prompt").status_code == 403
        assert client.post("/api/tasks", headers=headers, json={"prompt": "fixture", "request_id": "x" * 32}).status_code == 202
        assert entered.wait(2)
        release.set()
    assert calls == ["fixture"]


def test_api_rejects_malformed_and_oversized_requests_without_dispatch(session, tmp_path):
    value, calls, _, _ = session
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    headers = {"x-qubitz-token": "secret", "origin": "http://127.0.0.1:8765", "content-type": "application/json"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.post("/api/tasks", headers=headers, content="{").status_code == 400
        assert client.post("/api/tasks", headers=headers, json=[]).status_code == 422
        assert client.post("/api/tasks", headers=headers, json={"prompt": 3}).status_code == 422
        assert client.post("/api/tasks", headers=headers, content=" " * (1024**2 + 1)).status_code == 413
        assert client.get("/api/state", headers={**headers, "sec-fetch-site": "cross-site"}).status_code == 403
        assert client.get("/assets/../../HARNESS.txt").status_code == 404
        assert client.get("/", headers=headers).status_code == 503
    assert not calls


def test_variant_picker_groups_public_profiles_without_loading_models(session, tmp_path):
    value, calls, _, _ = session
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/variants", headers={"x-qubitz-token": "secret"})
        assert response.status_code == 200
        catalog = response.json()
    groups = {group["tier"]: group["variants"] for group in catalog["groups"]}
    assert {tier: len(names) for tier, names in groups.items()} == {
        "12 GB VRAM": 3, "24 GB VRAM": 27, "48 GB VRAM+": 10,
    }
    names = [name for members in groups.values() for name in members]
    assert len(names) == len(set(names)) == len(catalog["variants"]) == 40
    assert set(names) == set(catalog["variants"])
    assert "AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py" in groups["12 GB VRAM"]
    assert "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py" in groups["24 GB VRAM"]
    assert "AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py" in groups["48 GB VRAM+"]
    assert "AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py" in groups["48 GB VRAM+"]
    assert "AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py" in groups["48 GB VRAM+"]
    assert not any("WX" in name for name in names)
    assert value.runner is None and not calls


def test_missing_variant_tiers_are_reported_not_guessed(session, tmp_path, monkeypatch):
    from runtime import web_api

    value, calls, _, _ = session
    monkeypatch.setattr(web_api, "PROJECT_ROOT", tmp_path)
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/variants", headers={"x-qubitz-token": "secret"})
        assert response.status_code == 503
        assert response.json()["detail"] == "The configured variant tier catalog is unavailable."
    assert value.runner is None and not calls


@pytest.mark.parametrize("prompt,kind", [("status", "status"), ("what are you doing?", "status"),
                                        ("/bg do work", "background_unavailable"),
                                        ("/jobs", "background_unavailable"), ("stop", "stop")])
def test_local_controls_never_initialize_or_dispatch_a_model(session, prompt, kind):
    value, calls, _, _ = session
    identifier = value.submit(prompt, "x" * 32)
    assert value.submit(prompt, "x" * 32) == identifier
    assert len(value.tasks) == 1
    assert value.tasks[0]["control"] == kind and value.tasks[0]["state"] == "completed"
    assert value.runner is None and value._worker is None and not calls


def test_typed_stop_clears_queue_and_cancels_only_foreground_once(session):
    value, calls, entered, release = session
    value.submit("first", "a" * 32)
    assert entered.wait(2)
    value.submit("second", "b" * 32)
    identifier = value.submit("cancel the current task!", "c" * 32)
    assert value.phase == "cancelling" and not value.queue
    assert value.submit("cancel the current task!", "c" * 32) == identifier
    release.set()
    finish(value)
    assert calls == ["first"]
    assert [task["state"] for task in value.tasks] == ["cancelled", "cancelled", "completed"]
    assert "cleared 1 queued" in value.tasks[-1]["answer"]


@pytest.mark.parametrize("prompt", ["stopwatch implementation", "status of the solar project",
                                   "cancel the incorrect paragraph and rewrite it", "/background is a path"])
def test_control_detection_does_not_capture_normal_tasks(prompt):
    assert AgentSession._control_kind(prompt) is None


def test_local_status_is_allowed_at_a_pending_approval_boundary(session):
    value, calls, _, _ = session
    proposal = SimpleNamespace(approval_id="pending", preview="fixture", tool_name="fixture", request_prompt="fixture")
    value.runner = SimpleNamespace(_tool_permission_broker=SimpleNamespace(pending={"key": proposal}),
                                   config=value.config, request_cancel=lambda: None, reset_runtime=lambda: None)
    value.phase = "awaiting_approval"
    value.submit("status", "x" * 32)
    assert value.phase == "awaiting_approval" and value._approvals()[0]["id"] == "pending" and not calls


def test_folder_browsing_does_not_switch_workspace_or_construct_runner(session, tmp_path):
    value, calls, _, _ = session
    (tmp_path / "Beta").mkdir()
    (tmp_path / "alpha").mkdir()
    (tmp_path / "file.txt").write_text("fixture")
    result = value.directory_listing()
    assert [directory["name"] for directory in result["directories"]] == ["alpha", "Beta"]
    assert result["path"] == str(tmp_path) and not result["truncated"]
    assert value.directory_listing("alpha")["parent"] == str(tmp_path)
    assert value.config.workspace == tmp_path and value.runner is None and not calls
    for path in ("", " " * 4, "x" * 4097, "file.txt"):
        with pytest.raises(ValueError):
            value.directory_listing(path)


def test_folder_browsing_caps_wide_listings_and_skips_symlink_folders(session, tmp_path):
    value, _, _, _ = session
    for number in range(205):
        (tmp_path / f"folder_{number:03}").mkdir()
    result = value.directory_listing()
    assert len(result["directories"]) == 200 and result["truncated"]
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    target = isolated / "real"
    target.mkdir()
    try:
        (isolated / "link").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Filesystem does not support fixture symlinks.")
    assert [item["name"] for item in value.directory_listing(str(isolated))["directories"]] == ["real"]


def test_authenticated_folder_and_mocked_public_model_cache_endpoints(session, tmp_path, monkeypatch):
    from runtime import web_api

    value, calls, _, _ = session
    checked = []

    def cache(paths, root):
        checked.extend(paths)
        assert root == web_api.PROJECT_ROOT
        return {path.name: index % 2 == 0 for index, path in enumerate(paths)}

    monkeypatch.setattr(web_api, "cached_model_states", cache)
    app = create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path)
    headers = {"x-qubitz-token": "secret"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        for route in ("/api/model-cache", "/api/workspace-directories"):
            assert client.get(route).status_code == 401
        assert not checked
        assert client.get("/api/workspace-directories", headers=headers).json()["path"] == str(tmp_path)
        assert client.get("/api/workspace-directories?path=nonexistent", headers=headers).status_code == 422
        response = client.get("/api/model-cache", headers=headers)
        assert response.status_code == 200 and len(response.json()["states"]) == 40
        assert "integrity" in response.json()["scope"]
    assert len(checked) == 40 and not any("WX" in path.name for path in checked)
    assert not calls and value.runner is None


def test_local_model_file_check_errors_are_visible(session, tmp_path, monkeypatch):
    from runtime import web_api

    value, _, _, _ = session

    def unavailable(*_args):
        raise OSError("fixture access denied")

    monkeypatch.setattr(web_api, "cached_model_states", unavailable)
    with TestClient(create_api(value, "secret", "http://127.0.0.1:8765", assets=tmp_path),
                    base_url="http://127.0.0.1:8765") as client:
        response = client.get("/api/model-cache", headers={"x-qubitz-token": "secret"})
        assert response.status_code == 503 and "unavailable" in response.json()["detail"]


def test_runtime_details_are_observed_without_initializing_models(session):
    value, calls, entered, release = session
    value.config.embed_model_name = "fixture-embedding"
    value.config.server_url = "http://127.0.0.1:9999"
    assert value.snapshot()["runtime"] == {"embedding_model": "fixture-embedding",
                                          "backend_url": "http://127.0.0.1:9999",
                                          "skills": {"count": None, "names": [], "warnings": []}}
    assert value.runner is None and not calls
    value.submit("fixture", "x" * 32)
    assert entered.wait(2)
    value.runner.skills = SimpleNamespace(count=lambda: 2, list_summaries=lambda: [{"name": "one"}, {"name": "two"}],
                                          warnings=["fixture warning"])
    value._refresh_skill_info()
    assert value.snapshot()["runtime"]["skills"] == {"count": 2, "names": ["one", "two"], "warnings": ["fixture warning"]}
    release.set()
    finish(value)


def test_reasoning_diagnostics_survive_snapshot_and_remain_task_scoped(session):
    value, calls, entered, release = session
    identifier = value.submit("fixture", "x" * 32)
    assert entered.wait(2)
    status = {"received": 0, "forwarded": 0, "display_enabled": True,
              "reasoning_format": "unknown", "chat_format": "unknown"}
    value.publish("reasoning_status", json.dumps(status), identifier)
    assert value.events[-1]["reasoning_status"] == status
    value.publish("reasoning_status", "not json", identifier)
    assert "reasoning_status" not in value.events[-1]
    assert value.snapshot()["tasks"][0]["reasoning_status"] == status
    value.publish("reasoning_delta", "Runtime-emitted provisional text.", identifier)
    value.publish("assistant_delta", "Answer", identifier)
    release.set()
    finish(value)
    snapshot = value.snapshot()
    assert snapshot["tasks"][0]["reasoning_status"] == status
    assert [(message["role"], message["text"]) for message in snapshot["messages"]
            if message["role"] == "reasoning"] == [("reasoning", "Runtime-emitted provisional text.")]
    assert calls == ["fixture"]


@pytest.mark.parametrize("payload", ["not json", "[]", "null",
                                     '{"received": -1, "forwarded": true, "display_enabled": "yes"}'])
def test_malformed_reasoning_diagnostics_do_not_invent_counters(payload):
    assert web_controller._reasoning_status(payload) == {}


@pytest.mark.parametrize("args", [["--cli"], ["--serve-mcp"], ["--encrypt-harness"], ["--help"]])
def test_non_gui_entrypoints_are_preserved(args):
    calls = []
    namespace = {"__file__": "AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py",
                 "_APP": SimpleNamespace(), "main": lambda argv: calls.append(argv)}
    browser_entrypoint.install_browser_entrypoint(namespace)
    namespace["main"](args)
    assert calls == [args]

