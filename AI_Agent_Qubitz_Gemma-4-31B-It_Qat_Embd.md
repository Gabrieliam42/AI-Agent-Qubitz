# AI_Agent_Qubitz_Gemma-4-31B-It_Qat_Embd.py

Variant summary for `AI_Agent_Qubitz_Gemma-4-31B-It_Qat_Embd.py`.

## Purpose
- Standalone local-only Qubitz wrapper for the Gemma 4 31B IT QAT GGUF workflow.
- Embedding-enabled variant intended for workspace retrieval and general local agent tasks.
- Primary retrieval embedding model: `codesage/codesage-large-v2`.
- Local-LLM-oriented workflow with no required cloud APIs, subscriptions, or paid hosted inference services.

## Model and runtime
- Served model alias: `unsloth/gemma-4-31B-it-qat-GGUF`
- GGUF repo: `unsloth/gemma-4-31B-it-qat-GGUF`
- Default GGUF label: `Unsloth-Gemma-4-31B-It-QAT-UD-Q4_K_XL`
- Default GGUF filename: `gemma-4-31B-it-qat-UD-Q4_K_XL.gguf`
- Intended runtime style: WSL2/Linux execution while operating on Windows-hosted workspaces through the WSL-to-Windows bridge, and also WSL-hosted workspaces
- Supports GUI, CLI, and stdio MCP server modes

## Default generation settings
- Default `num_ctx`: `262144`
- Default `num_predict`: `16384`
- Supports `--thinking-effort default|low|medium|high|xhigh`

## Harness behavior
- If both `HARNESS.txt` and `HARNESS.enc` exist, this variant uses `HARNESS.txt`
- If `HARNESS.txt` is absent and `HARNESS.enc` exists, it falls back to `HARNESS.enc`
- `HARNESS.enc` is excluded from normal retrieval context paths so the duplicate encrypted harness is not injected
- `RULES.md` is not auto-injected like the harness; it is only available as a normal workspace file if explicitly read or retrieved
- Optionally imports `qubitz_ump_local.py` for the local UMP-backed memory layer; if that helper is missing or fails to import, the variant still runs with UMP disabled

## Retrieval and task behavior
- Uses embeddings and retrieval for project, workspace, repo, codebase, and multi-step task prompts
- Short simple general-knowledge questions bypass:
  - repository retrieval
  - embedding generation
  - MCP tool loading
- Uses a stronger simple-question fast path with a small direct-question step cap
- For use-only tasks that explicitly name an existing project entrypoint, the wrapper can:
  - resolve the entrypoint relative to the active workspace
  - run it directly
  - parse structured output
  - return the result without falling back to the slower model/tool loop
- Supported direct entrypoint types currently include:
  - existing `.py`, `.ps1`, `.sh`, `.bat`, and `.cmd` files
  - explicit `uv run <existing-file>` entrypoints
  - explicit `npm run <existing-package-script>` and `pnpm run <existing-package-script>` entrypoints
  - explicit `make <existing-target>` entrypoints

## Local behavior notes
- Keeps runtime caches, memory, and downloads rooted in the launch/runtime directory
- Prefers the active workspace Python environment for project-side execution
- Uses wrapper-provided runtime capability facts so the model does not have to infer WSL/Windows execution facts on its own
- Local `.skills` support is gated so empty/no-skill projects do not expand the local MCP surface unnecessarily
- Current Gemma comparison winner in recent local Qubitz smoke tests after the non-QAT comparison variants were removed

## Typical use
- Best fit when you want the current strongest local Gemma-based embedding-enabled Qubitz variant
