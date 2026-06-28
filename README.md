# Qubitz

Are you exhausted of testing small local AI agents that ignore instructions, misuse tools, or wander off task?

Qubitz is a local-first AI agent with harness that aims to make 7B–35B MCP/tool-capable LLMs more predictable and useful. It keeps routing, workspace handling, retrieval, and tool orchestration under wrapper control, so smaller models are not left to decide everything on their own.

***
Qubitz is a standalone local-only AI agent for GGUF models on `llama.cpp`. It is oriented to local LLM workflows only: no cloud inference, no subscriptions, and no paid hosted services are required.

Qubitz is unusual compared to most AI agents because it is strongly local-first and wrapper-driven and harness-driven.

Most agents out there are one of these:

- Cloud/API agents: faster setup, stronger frontier models, but dependent on APIs, subscriptions, cloud state, and vendor limits.
- IDE agents: good UX and repo integration, but usually tied to a hosted model or editor ecosystem.
- Local chat wrappers: private/local, but often weak as real agents because tool routing, workspace handling, and recovery paths are thin.
- Research agent frameworks: flexible, but often overcomplicated, brittle, and not optimized for one real workstation.

Qubitz is closer to a local style agent with a strong harness. Its stronger points are:

- Fully local orientation: no API, no cloud, no subscription dependency.
- Multiple model variants: lets you compare behavior across 8B-35B-class local models.
- Wrapper-owned routing: simple questions, direct existing scripts, read-only workspace tasks, and tool/MCP paths are not left entirely to the model.
- Good WSL2/Windows awareness: this is a real advantage because many agents handle mixed Windows/WSL workspaces badly.
- Strong direct-entrypoint path: this is better than many agents that overthink and rewrite instead of running what already exists.
- Harness plus wrapper separation: useful because small models need both persistent policy and runtime facts.
- Local retrieval/embeddings: gives project context without cloud retrieval.

A realistical view: For its purposes Qubitz is better than most local hobby agents and many generic framework agents for practical local repository work. It is not better than frontier cloud coding agents on raw intelligence, but it is much better if your priorities are privacy, no paid services, local control, WSL/Windows operation, and predictable wrapper-owned behavior.

The most valuable design choice is that Qubitz does not let small models decide everything. The wrapper owns routing, execution facts, and fast paths; the model handles language/reasoning where needed. That is the right architecture for 8B-35B local agents.

It is intended to run primarily under WSL2/Linux, and to work in WSL-hosted workspaces and Windows-hosted workspaces accessed through the WSL-to-Windows bridge.

## What it includes

- Local `llama.cpp` GGUF generation
- Task-routed workspace retrieval with local embedding models
- Tk GUI, CLI, and stdio MCP server modes
- Direct existing-entrypoint execution for explicit `.py`, `.ps1`, `.sh`, `.bat`, `.cmd`, `uv run`, `npm run`, `pnpm run`, and `make` tasks
- Local background jobs, local plugin guidance, and wrapper-local sandbox/tool orchestration

## Variant scripts

- `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py` - 12GB VRAM GPU embedding-enabled Qwen 3.5 9B Q8 variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_Granite-4_1-8B_Q8_12G.py` - 12GB VRAM GPU embedding-enabled Granite 4.1 8B Q8 variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py` - 24 GB class embedding-enabled GLM 4.7 Flash variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_Devstral-Small-2_Embd.py` - 24 GB class embedding-enabled Devstral Small 2 variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_Gemma-4-31B-It_Qat_Embd.py` - 24 GB class embedding-enabled Gemma 4 31B IT QAT variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_GPT-OSS-20B_F16_Embd.py` - 24 GB class embedding-enabled GPT-OSS 20B F16 variant using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_Qwen3_6-35B-A3B_MTP_UD_Q4_K_M_Embd.py` - 24 GB class embedding-enabled Qwen 3.6 35B A3B MTP variant using `BAAI/bge-code-v1`

## Runtime behavior

- Short simple questions use a fast path that skips broader retrieval, embedding generation, and local skill/MCP expansion.
- Repository-specific, code-specific, and multi-step tasks use retrieval when needed.
- If a prompt explicitly names an existing project entrypoint, the wrapper can run it directly and return the result without forcing a slower model/tool loop.
- Wrapper-provided runtime facts steer WSL/Windows execution behavior so small local models do not need to infer interop rules from scratch.

## Harness behavior

- It uses `HARNESS.enc` that exists in the workspace root
- `HARNESS.enc` is excluded from normal retrieval context paths so the encrypted duplicate is not injected.
- `QUBITZ_HARNESS_KEY.local.txt` is also used for the harness loading.

## Main files

- Variant scripts: the eight `AI_Agent_Qubitz_*.py` files above
- `HARNESS.enc` - AI Agent Harness
- `QUBITZ_HARNESS_KEY.local.txt` - local harness-key helper file
- `requirements.txt` - runtime dependencies
- `requirements-ci.txt` - CI, lint, and test dependencies

## Setup

From the Windows project directory, create and use a WSL2/Linux virtual environment:

```powershell
wsl python3 -m venv .venv
wsl .venv/bin/pip install -r requirements.txt
```

Launch a variant:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py
```

CLI examples:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --cli
wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --cli --prompt "What does this project do?"
```

MCP server example:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py --serve-mcp
```

If you already have a compatible `llama.cpp` server or GGUF path, point a variant at it with `--server-url`, `--llama-server`, and `--model-path`.

## Important options

- `--num-ctx`
- `--num-predict`
- `--max-steps`
- `--thinking-effort` with `default`(xhigh), `low`, `medium`, `high`, or `xhigh`

In the GUI, the lower-right `Effort` selector maps to the same preset.
