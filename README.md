# Qubitz

Qubitz is a standalone local-only AI agent published here as a single main script: `AI_Agent_Qubitz_Embedding.py`.

It combines:
- local `llama.cpp` GGUF generation
- workspace retrieval with `BAAI/bge-code-v1`
- encrypted harness loading from `HARNESS.enc`
- Tk GUI, CLI, and stdio MCP server modes
- local tools for file work, text search, Python commands, and PowerShell

## Files
- `AI_Agent_Qubitz_Embedding.py` - main standalone script
- `HARNESS.enc` - harness
- `QUBITZ_HARNESS_KEY.local.txt` - local indicator for the harness
- `requirements.txt` - runtime dependencies
- `requirements-ci.txt` - CI, lint, and test dependencies

## How it works
1. On startup, the script loads `HARNESS.enc` as the AI Agent Harness.
2. It runs a local `llama.cpp` OpenAI-compatible backend for generation.
3. It uses `BAAI/bge-code-v1` retrieval for project, workspace, repo, codebase, and multi-step task prompts.
4. It bypasses retrieval for simple general-knowledge questions so they answer faster.
5. It keeps runtime caches, memory, and downloads rooted in the launch/runtime directory even if the active workspace is changed.
6. For project-side Python work, it prefers the active workspace venv/interpreter when one exists.

## Local-only extras
- `.qubitz/local_only.toml` can add local-only config overrides.
- `.qubitz/plugins/*.toml` can add local plugin guidance.
- `/bg`, `/jobs`, and `/job <id>` support local background jobs.
- Sandbox and wrapper-local MCP orchestration are built into the script.

## Interfaces
- GUI: default mode
- CLI: `python AI_Agent_Qubitz_Embedding.py --cli`
- One-shot CLI: `python AI_Agent_Qubitz_Embedding.py --cli --prompt "What does this project do?"`
- MCP server: `python AI_Agent_Qubitz_Embedding.py --serve-mcp`

## Setup
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python AI_Agent_Qubitz_Embedding.py
```

If you already have a compatible `llama.cpp` server or GGUF path, you can point the script at them with `--server-url`, `--llama-server`, and `--model-path`.

## Important options
- `--num-ctx`
- `--num-predict`
- `--max-steps`
- `--thinking-effort` with `default`, `low`, `medium`, `high`, or `xhigh`

In the GUI, the lower-right `Effort` selector maps to the same preset.
