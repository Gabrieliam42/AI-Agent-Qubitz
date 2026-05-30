# Qubitz

Qubitz is a standalone local-only AI agent published here as two main script variants: `AI_Agent_Qubitz_Embedding.py` and `AI_Agent_Qubitz_All_Local_11G.py`. It is intended to run under WSL2 while operating on Windows-hosted workspaces through a WSL-to-Windows bridge.

The variants are:
- `AI_Agent_Qubitz_Embedding.py` - embedding-enabled 24GB+ VRAM GPU variant with workspace retrieval using `BAAI/bge-code-v1`
- `AI_Agent_Qubitz_All_Local_11G.py` - all-local 11GB+ VRAM GPU variant that works the same way but without embeddings or retrieval

It combines:
- local `llama.cpp` GGUF generation
- optional workspace retrieval with `BAAI/bge-code-v1` in the embedding-enabled variant
- encrypted harness loading from `HARNESS.enc`
- Tk GUI, CLI, and stdio MCP server modes
- local tools for file work, text search, Python commands, and PowerShell

## Files
- `AI_Agent_Qubitz_Embedding.py` - main standalone script with embeddings and workspace retrieval, intended for 24GB+ VRAM GPUs
- `AI_Agent_Qubitz_All_Local_11G.py` - main standalone script variant without embeddings, intended for 11GB+ VRAM GPUs
- `HARNESS.enc` - harness
- `QUBITZ_HARNESS_KEY.local.txt` - local indicator for the harness
- `requirements.txt` - runtime dependencies
- `requirements-ci.txt` - CI, lint, and test dependencies

## How it works
1. On startup, the selected script loads `HARNESS.enc` as the AI Agent Harness.
2. It runs a local `llama.cpp` OpenAI-compatible backend for generation.
3. `AI_Agent_Qubitz_Embedding.py` uses `BAAI/bge-code-v1` retrieval for project, workspace, repo, codebase, and multi-step task prompts.
4. `AI_Agent_Qubitz_All_Local_11G.py` runs without embeddings or retrieval while keeping the same local agent workflow.
5. The embedding-enabled variant bypasses retrieval for simple general-knowledge questions so they answer faster.
6. It keeps runtime caches, memory, and downloads rooted in the launch/runtime directory even if the active workspace is changed.
7. For project-side Python work, it prefers the active workspace venv/interpreter when one exists.

## Local-only extras
- `.qubitz/local_only.toml` can add local-only config overrides.
- `.qubitz/plugins/*.toml` can add local plugin guidance.
- `/bg`, `/jobs`, and `/job <id>` support local background jobs.
- Sandbox and wrapper-local MCP orchestration are built into the scripts.

## Interfaces
- GUI: default mode
- CLI from the project directory in PowerShell when using the WSL `.venv` environment:
  - `wsl .venv/bin/python AI_Agent_Qubitz_Embedding.py --cli`
  - `wsl .venv/bin/python AI_Agent_Qubitz_All_Local_11G.py --cli`
- One-shot CLI from the project directory in PowerShell when using the WSL `.venv` env:
  - `wsl .venv/bin/python AI_Agent_Qubitz_Embedding.py --cli --prompt "What does this project do?"`
  - `wsl .venv/bin/python AI_Agent_Qubitz_All_Local_11G.py --cli --prompt "What does this project do?"`
- MCP server:
  - `python AI_Agent_Qubitz_Embedding.py --serve-mcp`
  - `python AI_Agent_Qubitz_All_Local_11G.py --serve-mcp`

## Runtime requirement
- Runtime: **WSL2/Linux**
- Workspace/tool operation: **WSL2/Linux plus Windows via the WSL-to-Windows bridge when needed**
- The published setup assumes a WSL/Linux `.venv` created and run under WSL2, not a native Windows venv.
- The PowerShell CLI examples in this repository are written for launching that WSL2 environment from the Windows project directory.
- `AI_Agent_Qubitz_All_Local_11G.py` is intended for 11GB+ VRAM GPUs and runs without embeddings or retrieval.
- `AI_Agent_Qubitz_Embedding.py` is intended for 24GB+ VRAM GPUs and runs without embeddings or retrieval.

## Setup
From the Windows project directory, create and use the WSL2/Linux `.venv` with:

```powershell
wsl python3 -m venv .venv
wsl .venv/bin/pip install -r requirements.txt
wsl .venv/bin/python AI_Agent_Qubitz_Embedding.py

or

wsl .venv/bin/python AI_Agent_Qubitz_All_Local_11G.py
```

For interactive CLI use from the Windows project directory:

```powershell
wsl .venv/bin/python AI_Agent_Qubitz_Embedding.py --cli

or

wsl .venv/bin/python AI_Agent_Qubitz_All_Local_11G.py --cli
```

If you already have a compatible `llama.cpp` server or GGUF path, you can point the script at them with `--server-url`, `--llama-server`, and `--model-path`.

## Important options
- `--num-ctx`
- `--num-predict`
- `--max-steps`
- `--thinking-effort` with `default`, `low`, `medium`, `high`, or `xhigh`

In the GUI, the lower-right `Effort` selector maps to the same preset.
