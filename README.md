# Qubitz

Qubitz is a standalone local-only AI agent published here as three main script variants:

The variants are:
- `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py` - embedding-enabled 12GB+ VRAM GPU variant with workspace retrieval using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py` - embedding-enabled 24GB+ VRAM GPU variant with workspace retrieval using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_Devstral-Small-2_Embd.py` - embedding-enabled 24GB+ VRAM GPU variant with workspace retrieval using `codesage/codesage-large-v2`

It is intended to run under WSL2 while operating on Windows-hosted workspaces through a WSL-to-Windows bridge.

It combines:
- local `llama.cpp` GGUF generation
- optional workspace retrieval with `BAAI/bge-code-v1` in the GLM and Qwen variants and `codesage/codesage-large-v2` in the Devstral variant
- harness loading from `HARNESS.txt` or `HARNESS.enc`
- Tk GUI, CLI, and stdio MCP server modes
- local tools for file work, text search, Python commands, and PowerShell

## Files
- `AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py` - main standalone GLM embedding-enabled variant
- `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py` - main standalone Qwen embedding-enabled 12G variant
- `AI_Agent_Qubitz_Devstral-Small-2_Embd.py` - main standalone Devstral embedding-enabled variant
- `HARNESS.txt` - plaintext harness, preferred when both harness files exist
- `HARNESS.enc` - encrypted harness fallback when `HARNESS.txt` is absent
- `QUBITZ_HARNESS_KEY.local.txt` - local key source for `HARNESS.enc`
- `requirements.txt` - runtime dependencies
- `requirements-ci.txt` - CI, lint, and test dependencies

## How it works
1. On startup, the selected script loads `HARNESS.txt` if it exists; otherwise it falls back to `HARNESS.enc`.
2. It runs a local `llama.cpp` OpenAI-compatible backend for generation.
3. `AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py` uses `BAAI/bge-code-v1` retrieval for project, workspace, repo, codebase, and multi-step task prompts.
4. `AI_Agent_Qubitz_Devstral-Small-2_Embd.py` uses `codesage/codesage-large-v2` retrieval for the same retrieval-enabled workflow.
5. `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py` uses `BAAI/bge-code-v1` retrieval for the same retrieval-enabled workflow in the lower-VRAM 12G variant.
6. All variants bypass retrieval, embeddings, and MCP tool loading for short simple general-knowledge questions so they answer faster.
7. For use-only tasks that explicitly name an existing script, the wrappers can resolve and run that script directly relative to the active workspace instead of always falling back to a slower model/tool loop.
8. It keeps runtime caches, memory, and downloads rooted in the launch/runtime directory even if the active workspace is changed.
9. For project-side Python work, it prefers the active workspace venv/interpreter when one exists.

## Local-only extras
- `.qubitz/local_only.toml` can add local-only config overrides.
- `.qubitz/plugins/*.toml` can add local plugin guidance.
- `/bg`, `/jobs`, and `/job <id>` support local background jobs where enabled.
- Sandbox and wrapper-local MCP orchestration are built into the scripts.
- Project `.skills` directories are supported for local skill discovery and runtime skill access.

## Interfaces
- GUI: default mode
- CLI from the project directory in PowerShell when using the WSL `.venv` environment:
  - `wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py --cli`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --cli`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Devstral-Small-2_Embd.py --cli`
- One-shot CLI from the project directory in PowerShell when using the WSL `.venv` env:
  - `wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py --cli --prompt "What does this project do?"`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --cli --prompt "What does this project do?"`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Devstral-Small-2_Embd.py --cli --prompt "What does this project do?"`
- MCP server from the project directory in PowerShell when using the WSL `.venv` environment:
  - `wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py --serve-mcp`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --serve-mcp`
  - `wsl .venv/bin/python AI_Agent_Qubitz_Devstral-Small-2_Embd.py --serve-mcp`

## Runtime requirement
- Runtime: **WSL2/Linux**
- Workspace/tool operation: **WSL2/Linux plus Windows via the WSL-to-Windows bridge when needed**
- The published setup assumes a WSL/Linux `.venv` created and run under WSL2, not a native Windows venv.
- The PowerShell CLI examples in this repository are written for launching that WSL2 environment from the Windows project directory.
- `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py` is intended for a lower-VRAM local workflow while still using embeddings and retrieval.
- `AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py`, `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py`, and `AI_Agent_Qubitz_Devstral-Small-2_Embd.py` are embedding-enabled variants.

## Setup
From the Windows project directory, create and use the WSL2/Linux `.venv` with:

```powershell
wsl python3 -m venv .venv
wsl .venv/bin/pip install -r requirements.txt
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py

or

wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py

or

wsl .venv/bin/python AI_Agent_Qubitz_Devstral-Small-2_Embd.py
```

For interactive CLI use from the Windows project directory:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_GLM_4_7_Flash_Embd.py --cli

or

wsl .venv/bin/python AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py --cli

or

wsl .venv/bin/python AI_Agent_Qubitz_Devstral-Small-2_Embd.py --cli
```

If you already have a compatible `llama.cpp` server or GGUF path, you can point the script at them with `--server-url`, `--llama-server`, and `--model-path`.

## Important options
- `--num-ctx`
- `--num-predict`
- `--max-steps`
- `--thinking-effort` with `default`, `low`, `medium`, `high`, or `xhigh`

In the GUI, the lower-right `Effort` selector maps to the same preset.
