# Qubitz

Qubitz is a standalone local-only AI agent for GGUF models on `llama.cpp`. It is oriented to local LLM workflows only: no cloud inference, no subscriptions, and no paid hosted services are required.

It is intended to run primarily under WSL2/Linux, and to work in WSL-hosted workspaces and Windows-hosted workspaces accessed through the WSL-to-Windows bridge.

## What it includes

- Local `llama.cpp` GGUF generation
- Task-routed workspace retrieval with local embedding models
- Tk GUI, CLI, and stdio MCP server modes
- Direct existing-entrypoint execution for explicit `.py`, `.ps1`, `.sh`, `.bat`, `.cmd`, `uv run`, `npm run`, `pnpm run`, and `make` tasks
- Optional local UMP-backed memory through `qubitz_ump_local.py`
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
- `qubitz_ump_local.py` - optional local UMP (Universal Memory Protocol) helper used opportunistically by the variants
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
