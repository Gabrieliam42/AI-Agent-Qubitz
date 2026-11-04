"""Dependency proposals use existing one-action approvals; never invoke an installer."""
from __future__ import annotations

import json

import pytest

from evaluation import test_followup_state_receipts as receipts

variant = receipts.variant
runner = receipts.runner
COMMANDS = (
    "npm install three", "npm install next react react-dom", "npm install @tanstack/react-query",
    "npm install -D tailwindcss @tailwindcss/vite", "npm install @radix-ui/react-dialog",
    "npx shadcn@latest add button", "npm install @react-three/fiber @react-three/drei",
    "npm install gsap", "npm install motion", "pnpm add react", "yarn add three", "bun add three",
    "npm.cmd install three", "npm --prefix web install react", "npm ci",
    "npm exec create-next-app@latest", "pnpm dlx shadcn@latest add dialog", "npx @scope/generator",
    "npm create vite@latest", "pnpm create next-app", "yarn create vite",
    '"C:\\Tools\\npx.cmd" shadcn@latest add button',
)


@pytest.mark.parametrize("command", COMMANDS)
def test_missing_dependency_becomes_exact_approval_request(variant, runner, monkeypatch, command):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: True)
    broker = receipts.begin(runner, "Update app.js and run the project tests.")
    arguments = {"command": command}
    allowed, proposal = broker.authorize("run_command", arguments)
    assert not allowed and proposal["status"] == "approval_required", proposal
    assert not proposal["process_started"]
    assert proposal["cwd"] == str(runner.workspace.resolve())
    assert json.loads(proposal["preview"][len("run_command("):-1])["command"] == command
    pending = broker.pending[next(iter(broker.pending))]
    assert pending.arguments["command"] == command
    assert pending.arguments["cwd"] == str(runner.workspace.resolve())


def test_human_approval_authorizes_exact_command_once(variant, runner, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: True)
    broker = receipts.begin(runner, "Update app.js and run the project tests.")
    args = {"command": "npm install three"}
    _, proposal = broker.authorize("run_command", args)
    broker.begin_turn("approve " + proposal["approval_id"], None)
    assert broker.authorize("run_command", dict(args))[0]
    allowed, again = broker.authorize("run_command", dict(args))
    assert not allowed and again["status"] == "approval_required"
    changed = {**args, "command": "npm install next react"}
    assert not broker.authorize("run_command", changed)[0]


@pytest.mark.parametrize("mode", ["read_only", "plan"])
def test_proposal_does_not_override_access_mode(variant, runner, monkeypatch, mode):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: True)
    broker = receipts.begin(runner, "Build the requested interface.")
    broker.set_access_mode(mode)
    allowed, _ = broker.authorize("run_command", {"command": "npm install react"})
    assert not allowed and not broker.pending


@pytest.mark.parametrize("command", [
    "npm install three", "bun add three", "npx shadcn@latest add button",
    '"C:\\Tools\\npx.cmd" shadcn@latest add button',
])
def test_offline_does_not_offer_an_unexecutable_install(variant, runner, monkeypatch, command):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: False)
    broker = receipts.begin(runner, "Build the requested interface.")
    allowed, result = broker.authorize("run_command", {"command": command})
    assert not allowed and result["status"] == "network_offline"
    assert not broker.pending


def test_no_install_instruction_blocks_even_an_old_approval(variant, runner, monkeypatch):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: True)
    broker = receipts.begin(runner, "Build the interface. Do not install anything.")
    args = {"command": "npm install react"}
    broker.approved_once.add(broker._fingerprint("run_command", args))
    allowed, result = broker.authorize("run_command", args)
    assert not allowed and result["status"] == "scope_denied"
    assert not broker.pending


@pytest.mark.parametrize("command", ["npm install three; rm notes.txt", "npm install three && echo ready"])
def test_package_approval_does_not_bundle_other_actions(variant, runner, monkeypatch, command):
    monkeypatch.setattr(variant, "_network_mode_is_online", lambda mode=None: True)
    broker = receipts.begin(runner, "Build the interface and run project checks.")
    assert not broker.authorize("run_command", {"command": command})[0]
    assert not broker.pending


@pytest.mark.parametrize("command", ["npm install three", "pnpm add react", "npx shadcn@latest add button"])
def test_node_requests_do_not_become_python_installs(variant, runner, command):
    prompt = "Run " + command + " in this workspace."
    broker = receipts.begin(runner, prompt)
    decision = runner._decide_route(prompt)
    assert decision.selected_route != "dependency_install"
    assert broker.dependency_manifest is None
    runner._route_decision = decision
    tools = [{"name": name} for name in ("run_command", "install_python_package", "read_file_snapshot")]
    assert "run_command" in {runner._tool_name(tool) for tool in runner._filter_tools_by_intent(prompt, tools)}


@pytest.mark.parametrize("command", ["npx --no-install eslint app.js", "bunx --offline eslint app.js"])
def test_local_npx_inspection_is_not_mistaken_for_installation(variant, runner, command):
    assert not variant._tool_arguments_request_external_network("run_command", {"command": command})
    broker = receipts.begin(runner, "Run the existing project checks.")
    allowed, denial = broker.authorize("run_command", {"command": command})
    assert allowed, denial
    assert not broker.pending


@pytest.mark.parametrize("command", [
    "npx generator -- --offline", "npx --offline=false generator", "npx --no-install=false generator",
])
def test_generator_arguments_cannot_claim_the_executor_is_offline(variant, runner, command):
    assert variant._tool_arguments_request_external_network("run_command", {"command": command})
    broker = receipts.begin(runner, "Run the existing project checks.")
    allowed, proposal = broker.authorize("run_command", {"command": command})
    assert not allowed and proposal["status"] == "approval_required", proposal
