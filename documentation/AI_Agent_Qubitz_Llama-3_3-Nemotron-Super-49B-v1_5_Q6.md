# AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Llama-3.3-Nemotron-Super-49B-v1.5 UD-Q6_K_XL.

## Model

- Served alias: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF repo: `unsloth/Llama-3_3-Nemotron-Super-49B-v1_5-GGUF`
- GGUF file: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL.gguf`
- GGUF label: `Llama-3_3-Nemotron-Super-49B-v1_5-UD-Q6_K_XL`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Dual-24GB GPU variant for coding, repository, and MCP/tool tasks; it requires CUDA devices `0` and `1` and uses their 48 GB aggregate VRAM.
- Default native context ceiling: `131072` tokens.
- Uses the shared stable official llama.cpp `v0.2.0` Windows CUDA runtime, resolved to build `b10566` and cached under `.cache/llama.cpp-b10566/`.
- Uses llama.cpp's stable `layer` split and explicitly selects `CUDA0,CUDA1`, with GPU-first fitting and system-RAM overflow when needed. It does not force all model layers onto GPUs or enable experimental tensor-parallel mode or P2P.
- Reads each GPU's current free memory and reserves 1536 MiB per device with matching fit targets; the backend determines layer placement.
- Uses `--fit on --fit-ctx 131072`, preserving the existing requested context while allowing CPU-resident weights when free VRAM is insufficient. No fixed system-RAM capacity is imposed; successful loading still requires enough available memory for weights, KV cache and compute buffers.
- Validates both GPUs, available capacity, model size, and required llama.cpp flags before launch. It fails with a direct diagnostic when a safe two-GPU plan is unavailable.
- The placement policy is variant-specific; all ten public large-tier variants support two GPUs and RAM overflow. See [tier memory policy](48gb_vram_tier.md).
- Uses local llama.cpp GGUF inference only; no required API, cloud account, subscription, or paid hosted inference service.

## Qubitz Behavior

- Remains independently runnable and contains its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Short direct questions bypass retrieval, embeddings, skills, and MCP/tool loading.
- Repository, coding, MCP, and multi-step tasks use local retrieval, bounded tools, transactional edit guards, and verified completion evidence.
- Supports Windows-hosted and WSL-hosted workspaces through the existing WSL-to-Windows execution paths.

## Verification

- Model identity, declarative profile, Python syntax, and shared-wrapper parity are verified statically.
- GUI smoke on 2026-09-02: simple question returned `4` in `35.10s`; direct existing-entrypoint task returned all 3 verified rows in `1.26s`; GUI remained responsive and no files were modified.
- A live two-GPU model download, load, and task benchmark remains separate runtime verification.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
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

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

