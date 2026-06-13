# AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py

Variant summary for `AI_Agent_Qubitz_Qwen3_5_9B_Q8_12G.py`.

## Purpose
- Standalone local-only Qubitz wrapper for the Qwen 3.5 9B Q8 GGUF workflow.
- Embedding-enabled lower-VRAM variant intended for local workspace retrieval and agent tasks.
- Primary retrieval embedding model: `BAAI/bge-code-v1`.
- Local-LLM-oriented workflow with no required cloud APIs, subscriptions, or paid hosted inference services.

## Model and runtime
- Served model alias: `unsloth/Qwen3.5-9B-GGUF`
- GGUF repo: `unsloth/Qwen3.5-9B-GGUF`
- Default GGUF label: `Qwen3.5-9B-Q8`
- Default GGUF filename: `Qwen3.5-9B-Q8_0.gguf`
- Intended runtime style: WSL2/Linux execution while operating on Windows-hosted workspaces through the WSL-to-Windows bridge, and also WSL-hosted workspaces
- Supports GUI, CLI, and stdio MCP server modes

## Default generation settings
- Default `num_ctx`: GPU-dependent
  - `RTX 3090`: `262144`
  - other detected GPUs: `101376`
- Max supported `num_ctx` in this wrapper: GPU-dependent
  - `RTX 3090`: `262144`
  - other detected GPUs: `101376`
- Default `num_predict`: `8096`
- Max supported `num_predict` in this wrapper: `8096`
- KV cache type target: `q8_0`
- Supports `--thinking-effort default|low|medium|high|xhigh`

## Harness behavior
- If both `HARNESS.txt` and `HARNESS.enc` exist, this variant uses `HARNESS.txt`
- If `HARNESS.txt` is absent and `HARNESS.enc` exists, it falls back to `HARNESS.enc`
- `HARNESS.enc` is excluded from normal retrieval context paths so the duplicate encrypted harness is not injected
- `RULES.md` is not auto-injected like the harness; it is only available as a normal workspace file if explicitly read or retrieved
- Optionally imports `qubitz_ump_local.py` for the local UMP-backed memory layer; if that helper is missing or fails to import, the variant still runs with UMP disabled

## Retrieval and task behavior
- Uses embeddings and retrieval for retrieval-enabled project and workspace prompts
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
- Lower-VRAM-oriented local workflow compared with the GLM, Devstral, and Gemma 24 GB variants

## Typical use
- Best fit when you want a smaller local embedding-enabled variant with the same general Qubitz workflow
