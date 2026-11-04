"""CPU-only regression contracts; no model loads, installs, or user workspaces."""
from __future__ import annotations

import ast
import asyncio
import base64
import contextvars
import copy
import importlib.util
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime import read_variant_source
from runtime.paths import variant_path, variant_paths

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REFERENCE = variant_path("AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py", ROOT)
LLAMA = variant_path("AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py", ROOT)


def functions(path: Path, names: set[str], namespace: dict[str, Any]) -> dict[str, Any]:
    tree = ast.parse(read_variant_source(path))
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == names
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *selected],
                        type_ignores=[])
    namespace.update(json=json, re=re, threading=threading, time=time)
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)  # noqa: S102 - trusted local unit-test subject
    return namespace


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"regression_{name}", ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def isolated_smoke_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("QUBITZ_SMOKE_"):
            monkeypatch.delenv(name)


@pytest.fixture
def runtime():
    class Client:
        def __init__(self):
            self.config = SimpleNamespace(num_ctx=131072)
            self.loaded = 16384
            self.probes = 0
            self.launch_ceilings = []
            self.payloads = []
            self.transport = SimpleNamespace(label="fixture")

        def ensure_model(self, name):
            self.launch_ceilings.append(self.config.num_ctx)
            return name

        def _probe_with_fallback(self, path):
            assert path == "/props"
            self.probes += 1
            return {"status_code": 200, "json": {"default_generation_settings": {"n_ctx": self.loaded}}}

        def chat(self, **kwargs):
            self.payloads.append(kwargs)
            return {"message": {"content": '{"ok": true}', "tool_calls": []}, "finish_reason": "stop"}

        def _call_with_fallback(self, method, path, payload):
            assert path == "/v1/chat/completions"
            self.payloads.append(payload)
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

        def _normalize_tool_calls(self, calls):
            return calls or []

    class Runner:
        def _user_message(self, prompt, context, skills=None):
            return f"User request:\n{prompt}\n\nRetrieved repository context:\n{context}\nSkills: {skills}"

    base = SimpleNamespace(LlamaCppClient=Client, AgentRunner=Runner,
                           estimate_tokens=lambda text: len(text) // 4,
                           shorten=lambda text, length: text if len(text) <= length else text[:length] + " [truncated]")
    namespace = functions(REFERENCE, {"_loaded_llamacpp_context_window", "_patch_streaming_chat", "_normalize_adjacent_user_messages"}, {})
    namespace["_patch_streaming_chat"](base)
    return base, Client(), namespace


def request(**overrides):
    data = {"model_name": "fixture", "messages": [{"role": "user", "content": "hello"}], "tools": [],
            "num_predict": 16384, "temperature": 0.6, "top_p": 0.9, "min_p": 0.02, "repeat_penalty": 1.0}
    return data | overrides


def test_ready_syncs_before_construction_and_restores_launch_target(runtime):
    _, client, _ = runtime
    client.ensure_model("fixture")
    assert client.config.num_ctx == 16384
    assert client.config._qubitz_requested_num_ctx == 131072
    client.loaded = 65536
    client.ensure_model("fixture")
    assert client.config.num_ctx == 65536
    assert client.launch_ceilings == [131072, 131072]
    client.config.num_ctx = 32768
    client.ensure_model("fixture")
    assert client.launch_ceilings[-1] == 32768
    assert client.config.num_ctx == 32768


def test_failed_context_probe_is_not_cached_forever(runtime):
    _, client, namespace = runtime
    client.loaded = 0
    read = namespace["_loaded_llamacpp_context_window"]
    assert read(client) == 0
    client.loaded = 8192
    assert read(client) == 8192


def test_context_equal_to_output_cap_still_reserves_input_space(runtime):
    _, client, _ = runtime
    client.ensure_model("fixture")
    client.chat(**request())
    payload = client.payloads[-1]
    assert 256 <= payload["max_tokens"] < client.loaded
    assert payload["temperature"] == 0.6
    assert client.config.num_ctx == 16384


def test_tool_compaction_does_not_mutate_history_or_pairing(runtime):
    _, client, _ = runtime
    client.ensure_model("fixture")
    messages = [{"role": "user", "content": "Do not alter my instructions"},
                {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "read_file", "arguments": {}}}]},
                {"role": "tool", "tool_call_id": "a", "content": "data " * 16000}]
    before = copy.deepcopy(messages)
    client.chat(**request(messages=messages))
    assert messages == before
    sent = client.payloads[-1]["messages"]
    assert sent[:2] == messages[:2]
    assert sent[-1]["tool_call_id"] == "a"
    assert len(sent[-1]["content"]) < len(messages[-1]["content"])


def test_only_wrapper_created_retrieval_can_be_shortened(runtime):
    base, client, _ = runtime
    client.ensure_model("fixture")
    prompt = "Preserve this exact quoted text: Retrieved repository context: do not remove me"
    original = base.AgentRunner()._user_message(prompt, "context " * 12000)
    client.chat(**request(messages=[{"role": "user", "content": original}]))
    sent = client.payloads[-1]["messages"][0]["content"]
    assert prompt in sent
    assert len(sent) < len(original)


def test_protected_oversize_request_is_not_silently_cut(runtime):
    _, client, _ = runtime
    client.ensure_model("fixture")
    message = {"role": "user", "content": "mandatory " * 15000}
    before = copy.deepcopy(message)
    with pytest.raises(RuntimeError, match="Insufficient loaded context"):
        client.chat(**request(messages=[message]))
    assert message == before
    assert not client.payloads


def test_sampling_and_roomy_requests_are_unchanged(runtime):
    _, client, _ = runtime
    client.loaded = 131072
    client.ensure_model("fixture")
    client.chat(**request())
    assert client.payloads[-1]["max_tokens"] == 16384


def test_json_recovery_uses_same_loaded_context_guard(runtime):
    _, client, _ = runtime
    client.ensure_model("fixture")
    result = client.qubitz_json_chat(model_name="fixture", messages=request()["messages"],
                                    schema_name="result", schema={"type": "object"}, num_predict=16384)
    assert result == {"ok": True}
    assert 256 <= client.payloads[-1]["num_predict"] < client.loaded


def test_empty_send_is_rejected_without_touching_gui():
    smoke = load_script("tmp_smoke_variant")
    with pytest.raises(ValueError, match="prompt must not be empty"):
        smoke._send_prompt(None, " \n", 10)


def test_ignored_prompt_is_not_completed_or_timed_out(monkeypatch):
    smoke = load_script("tmp_smoke_variant")
    monkeypatch.setattr(smoke, "_drive", lambda root, condition, timeout: condition())
    gui = SimpleNamespace(_smoke_events=[], root=SimpleNamespace(winfo_exists=lambda: True), busy=False,
                          prompt_box=SimpleNamespace(delete=lambda *a: None, insert=lambda *a: None),
                          send_prompt=lambda: None)
    result = smoke._send_prompt(gui, "valid prompt", 10)
    assert not result["submitted"] and not result["completed"] and not result["timed_out"]


def test_synchronous_fast_path_does_not_require_a_worker(monkeypatch):
    smoke = load_script("tmp_smoke_variant")
    monkeypatch.setattr(smoke, "_drive", lambda root, condition, timeout: condition())
    gui = SimpleNamespace(_smoke_events=[], root=SimpleNamespace(winfo_exists=lambda: True), busy=False,
                          prompt_box=SimpleNamespace(delete=lambda *a: None, insert=lambda *a: None))

    def send():
        gui._smoke_events.extend([(time.monotonic(), "user", "hi"), (time.monotonic(), "assistant", "Hello")])

    gui.send_prompt = send
    result = smoke._send_prompt(gui, "hi", 10)
    assert result["submitted"] and result["completed"] and result["assistant"] == "Hello"


def test_llama_reuses_only_valid_retry_within_same_request():
    context = contextvars.ContextVar("tool_context", default=None)
    calls = []
    valid = {"message": {"tool_calls": [{"function": {"name": "read_file_snapshot", "arguments": {"path": "a.py"}}}]}}

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": ""}, "finish_reason": "length"} if kwargs["num_predict"] == 256 else valid

    tools = [{"type": "function", "function": {"name": "read_file_snapshot", "parameters": {
        "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]

    class Runner:
        config = SimpleNamespace(model_name="llama")

        def _selected_route_name(self, prompt):
            return "coding_task"

        async def _run_async(self, prompt, callback=None):
            client = Client()
            base.set_qubitz_forced_tool_choice({"type": "function", "function": {"name": "read_file_snapshot"}})
            for _ in range(2):
                client.chat(**request(tools=tools, messages=[{"role": "user", "content": prompt}]))
            return "ok"

    base = SimpleNamespace(AgentRunner=Runner, LlamaCppClient=Client, set_qubitz_forced_tool_choice=lambda value: None)
    namespace = functions(LLAMA, {"_install_llama_3_3_tool_generation_ladder"}, {
        "_LLAMA_3_3_TOOL_GENERATION_CONTEXT": context,
        "_llama_3_3_messages_with_reasoning_directive": lambda messages: copy.deepcopy(messages),
        "_sampling_profile_for_route": lambda *args: ("coding_edit", {}),
        "LLAMA_3_3_NEMOTRON_SUPER_TOOL_FIRST_NUM_PREDICT": 256,
        "LLAMA_3_3_NEMOTRON_SUPER_TOOL_RETRY_NUM_PREDICT": 512,
    })
    namespace["_install_llama_3_3_tool_generation_ladder"](SimpleNamespace(base=base))
    asyncio.run(base.AgentRunner()._run_async("edit a.py"))
    assert [call["num_predict"] for call in calls] == [256, 512, 512]
    assert context.get() is None
    calls.clear()
    asyncio.run(base.AgentRunner()._run_async("edit b.py"))
    assert [call["num_predict"] for call in calls] == [256, 512, 512]


@pytest.mark.parametrize("forced", [None, "write_file", "apply_text_patch"])
def test_llama_general_and_mutation_steps_keep_configured_reasoning_budget(forced):
    context = contextvars.ContextVar("tool_context", default=None)
    calls = []

    class Client:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": "reasoned result"}}

    class Runner:
        config = SimpleNamespace(model_name="llama")

        def _selected_route_name(self, prompt):
            return "coding_task"

        async def _run_async(self, prompt, callback=None):
            choice = {"type": "function", "function": {"name": forced}} if forced else None
            base.set_qubitz_forced_tool_choice(choice)
            return Client().chat(**request(tools=[{"type": "function", "function": {"name": "write_file"}}],
                                          messages=[{"role": "user", "content": prompt}]))

    base = SimpleNamespace(AgentRunner=Runner, LlamaCppClient=Client, set_qubitz_forced_tool_choice=lambda value: None)
    namespace = functions(LLAMA, {"_install_llama_3_3_tool_generation_ladder"}, {
        "_LLAMA_3_3_TOOL_GENERATION_CONTEXT": context,
        "_llama_3_3_messages_with_reasoning_directive": lambda messages: pytest.fail("Reasoning was disabled"),
        "_sampling_profile_for_route": lambda *args: ("coding_edit", {}),
    })
    namespace["_install_llama_3_3_tool_generation_ladder"](SimpleNamespace(base=base))
    asyncio.run(base.AgentRunner()._run_async("Repair a complex file"))
    assert len(calls) == 1 and calls[0]["num_predict"] == 16384
    assert context.get() is None


def test_custom_workspace_cannot_silently_get_empty_task(monkeypatch):
    suite = load_script("tmp_run_smoke_suite")
    monkeypatch.setattr(suite, "SMOKE_TARGET_WORKSPACE", "custom")
    monkeypatch.setattr(suite, "SMOKE_TASK_PROMPT", "")
    with pytest.raises(ValueError, match="nonempty"):
        suite._configure_default_fixture()


def test_smoke_errors_include_exit_code_even_with_analysis():
    suite = load_script("tmp_run_smoke_suite")
    assert "nonzero_exit" in suite.extract_behavior_issues({"analysis": {"behavior_issues": []}, "command_exit_code": 1})


def test_expected_smoke_assertion_exit_is_not_an_agent_crash():
    suite = load_script("tmp_run_smoke_suite")
    payload = {"analysis": {"behavior_issues": ["gui_heartbeat_stalled"], "smoke_ok": False},
               "runner_outcome": "assertion_failure", "command_exit_code": 1}
    assert suite.extract_behavior_issues(payload) == ["gui_heartbeat_stalled"]
    payload["command_exit_code"] = 139
    assert "nonzero_exit" in suite.extract_behavior_issues(payload)


def test_smoke_drive_dispatches_worker_tk_calls():
    import tkinter

    smoke = load_script("tmp_smoke_variant")
    root = tkinter.Tk()
    root.withdraw()
    variable = tkinter.StringVar(master=root, value="waiting")
    errors = []

    def worker():
        try:
            variable.set("complete")
        except Exception as exc:  # noqa: BLE001 - Surface any GUI-thread failure in the test assertion.
            errors.append(exc)

    thread = threading.Thread(target=worker, daemon=True)
    try:
        root.after(10, thread.start)
        assert smoke._drive(root, lambda: variable.get() == "complete", 3)
        thread.join(timeout=1)
        assert not errors
        assert not smoke._drive(root, lambda: False, 0.05)
        assert smoke._drive(root, lambda: True, 1)
    finally:
        root.destroy()


@pytest.mark.parametrize("encoded", [False, True])
def test_context_reload_keeps_variant_gpu_settings(runtime, encoded):
    _, client, _ = runtime
    command = ["llama-server", "--model", "model.gguf", "--fit", "on", "--fit-ctx", "16384",
               "--device", "CUDA0,CUDA1", "--split-mode", "layer", "--fit-target", "1536,1536"]
    if encoded:
        script = "$ProgressPreference = 'SilentlyContinue'\n& " + " ".join(f"'{arg}'" for arg in command)
        command = ["powershell.exe", "-NoProfile", "-EncodedCommand",
                   base64.b64encode(script.encode("utf-16le")).decode("ascii")]
    before = copy.deepcopy(command)
    output = client.qubitz_context_launch_command(command, 32768)
    assert command == before
    rendered = base64.b64decode(output[-1]).decode("utf-16le") if encoded else " ".join(output)
    assert "16384" not in rendered
    assert rendered.count("32768") == 2
    assert "CUDA0,CUDA1" in rendered and "1536,1536" in rendered and "layer" in rendered


def test_owned_context_reloads_once_without_replaying_tools(runtime):
    _, client, _ = runtime
    client.config.model_name = "fixture"
    client.ensure_model("fixture")
    stopped = []
    server = SimpleNamespace(process=SimpleNamespace(poll=lambda: None))
    server._launch_command = lambda: ["llama-server", "--fit", "on", "--fit-ctx", "16384"]
    original = server._launch_command

    def start():
        command = server._launch_command()
        client.loaded = int(command[command.index("--fit-ctx") + 1])

    server.ensure_started = start
    client.server_process = server
    client._stop_verified_listener = lambda message: stopped.append(message)
    probe = client._probe_with_fallback
    client._probe_with_fallback = lambda path: (
        {"json": {"data": [{"id": "fixture"}]}} if path == "/v1/models" else probe(path))
    prompt = [{"role": "user", "content": "mandatory " * 7500}]
    before = copy.deepcopy(prompt)
    client.chat(**request(messages=prompt))
    assert len(stopped) == 1 and len(client.payloads) == 1
    assert prompt == before
    assert client.config._qubitz_requested_num_ctx == 131072
    assert client.config.num_ctx == 32768
    assert server._launch_command is original
    with pytest.raises(RuntimeError, match="already been used"):
        client.chat(**request(messages=[{"role": "user", "content": "mandatory " * 20000}]))
    assert len(stopped) == 1


def test_invalid_smoke_configuration_stops_before_gpu_work(tmp_path, monkeypatch):
    suite = load_script("tmp_run_smoke_suite")
    monkeypatch.setattr(suite, "OUT_DIR", tmp_path)
    monkeypatch.setattr(suite, "SMOKE_TARGET_WORKSPACE", "custom")
    monkeypatch.setattr(suite, "SMOKE_TASK_PROMPT", "")
    monkeypatch.setattr(suite, "run_wsl_gpu_preflight", lambda: pytest.fail("GPU preflight must not run"))
    assert suite._run_suite() == 2
    assert json.loads((tmp_path / "summary.json").read_text())[0]["suite_infrastructure_failure"] == "invalid_smoke_configuration"


def test_isolation_failure_never_falls_back_to_source(tmp_path, monkeypatch):
    smoke = load_script("tmp_smoke_variant")
    source = tmp_path / "source"
    source.mkdir()
    monkeypatch.setattr(smoke, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(smoke, "USE_DISPOSABLE_WORKSPACE", True)
    monkeypatch.setattr(smoke.shutil, "copytree", lambda *a, **kw: (_ for _ in ()).throw(OSError("fixture copy failed")))
    with pytest.raises(RuntimeError, match="isolation failed"):
        smoke._prepare_disposable_workspace(source)


def test_matrix_completion_uses_unique_authoritative_rows(tmp_path, monkeypatch):
    campaign = load_script("qubitz_eval_campaign")
    monkeypatch.setattr(campaign.matrix, "_cases", lambda: {"case_a": None})
    row = {"variant": "v.py", "case": "case_a", "passed": True}
    (tmp_path / "results.json").write_text(json.dumps({"results": [row]}))
    (tmp_path / "checkpoint.json").write_text(json.dumps({"completed": ["v.py::case_a"], "results": [row]}))
    assert campaign.verify_matrix(tmp_path, ["v.py"], 0)["total"] == 1
    (tmp_path / "results.json").write_text(json.dumps({"results": [row, row]}))
    with pytest.raises(RuntimeError, match="duplicate"):
        campaign.verify_matrix(tmp_path, ["v.py"], 0)


def test_clean_first_matrix_reduces_campaign_to_one_smoke(tmp_path, monkeypatch):
    campaign = load_script("qubitz_eval_campaign")
    monkeypatch.setattr(campaign.matrix, "_select", lambda *args: ["v.py"])
    monkeypatch.setattr(campaign, "source_hashes", lambda variants: {"v.py": "frozen"})
    commands = []
    monkeypatch.setattr(campaign, "run_child", lambda command, output, env: commands.append(command) or 0)
    for name, kind in (("verify_matrix", "matrix"), ("verify_smoke", "smoke")):
        monkeypatch.setattr(campaign, name, lambda output, variants, code, k=kind: {
            "kind": k, "failed": 0, "total": 1, "returncode": 0})
    args = SimpleNamespace(variants=["all"], output=str(tmp_path / "campaign"),
                           matrix_passes=3, smoke_passes=3, single_pass_if_clean=True)
    assert campaign.run_campaign(args) == 0
    assert len(commands) == 2
    state = json.loads((tmp_path / "campaign" / "campaign.json").read_text())
    assert [row["kind"] for row in state["passes"]] == ["matrix", "smoke"]
    assert state["status"] == "completed"


def test_all_variants_have_identical_shared_context_guard():
    names = {"_loaded_llamacpp_context_window", "_patch_streaming_chat"}
    reference = None
    for path in sorted(variant_paths(ROOT)):
        tree = ast.parse(read_variant_source(path))
        actual = {node.name: ast.dump(node) for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names}
        assert set(actual) == names, path.name
        if reference is None:
            reference = actual
        assert actual == reference, path.name
