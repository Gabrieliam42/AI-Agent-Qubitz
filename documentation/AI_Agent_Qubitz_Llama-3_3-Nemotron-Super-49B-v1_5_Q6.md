# AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Llama-3.3-Nemotron-Super-49B-v1.5 UD-Q6_K_XL.

## Model

- Served alias: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF repo: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF file: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL.gguf`
- GGUF label: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- 48 GB VRAM+ tier for coding, repository and MCP/tool tasks; selects all visible compatible GPUs with per-device headroom, not exactly devices `0` and `1`.
- Default native context ceiling: `131072` tokens.
- Uses the shared stable official llama.cpp `v0.2.0` Windows CUDA runtime, resolved to build `b10566` and cached under `.cache/llama.cpp-b10566/`.
- Uses llama.cpp's stable `layer` split across all backend-visible compatible GPUs, with GPU-first fitting and system-RAM overflow when needed. It does not force all model layers onto GPUs or enable experimental tensor-parallel mode or P2P.
- Reads each GPU's current free memory and reserves 1536 MiB per device with matching fit targets; the backend determines layer placement.
- Uses `--fit on --fit-ctx 131072`, preserving the existing requested context while allowing CPU-resident weights when free VRAM is insufficient. No fixed system-RAM capacity is imposed; successful loading still requires enough available memory for weights, KV cache and compute buffers.
- Validates each selected GPU, available capacity, model size, and required llama.cpp flags before launch. It fails with a direct diagnostic when per-device safety headroom is unavailable.
- All ten public large-tier variants support automatic visible GPU counts and RAM overflow. See [tier memory policy](multiGPU_tier_48gb.md).
- Uses local llama.cpp GGUF inference only; no required API, cloud account, subscription, or paid hosted inference service.

## Qubitz Behavior

- Remains independently runnable and contains its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Short direct questions bypass retrieval, embeddings, skills, and MCP/tool loading.
- Repository, coding, MCP, and multi-step tasks use local retrieval, bounded tools, transactional edit guards, and verified completion evidence.
- Supports Windows-hosted and WSL-hosted workspaces through the existing WSL-to-Windows execution paths.

## Current Source-Verified Profile

These values describe the configured launcher, not a guarantee of hardware fit or runtime throughput.
- Model alias: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF repository: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF file: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL.gguf`
- GGUF label: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL`
- Requested context ceiling: `131072` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.
