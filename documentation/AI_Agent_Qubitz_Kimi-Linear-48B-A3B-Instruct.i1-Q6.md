# AI_Agent_Qubitz_Kimi-Linear-48B-A3B-Instruct.i1-Q6.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Kimi-Linear-48B-A3B-Instruct.i1-Q6.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Kimi-Linear-48B-A3B-Instruct.i1-Q6.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Moonshot AI Kimi-Linear 48B A3B Instruct i1 Q6_K.

## Model

- Served alias: `mradermacher/Kimi-Linear-48B-A3B-Instruct-i1-GGUF`
- GGUF repo: `mradermacher/Kimi-Linear-48B-A3B-Instruct-i1-GGUF`
- GGUF file: `Kimi-Linear-48B-A3B-Instruct.i1-Q6_K.gguf`
- GGUF label: `Kimi-Linear-48B-A3B-Instruct.i1-Q6_K`
- Architecture: 48B-total, approximately 3B-active MoE with hybrid Kimi Delta Attention and global attention
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- 48 GB VRAM+ tier selecting all visible compatible GPUs, not requiring exactly two RTX 3090 cards.
- Requested native context ceiling: `1048576` tokens, matching the source configuration and model card. This is a ceiling, not a promise that the full window fits on this host.
- Default maximum output: `16384` tokens.
- Uses llama.cpp's stable layer split and `--fit`, reserves 1536 MiB on each GPU, and requires a feasible minimum loaded context of `131072` tokens. llama.cpp may reduce the requested 1M window and may retain unavoidable model or runtime overflow in system RAM.
- Uses matching `f16` K/V caches because Kimi-Linear rejects block-32 `q4_0` caching for its 72-wide V head and rejects mixed K/V cache types.
- Uses the shared official llama.cpp Windows CUDA runtime, whose current source supports the Kimi Linear architecture.
- Uses local llama.cpp GGUF inference only; no required paid API, cloud service, or subscription.

## Qubitz Behavior

- Remains independently runnable and contains its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, memory, and thermal behavior.
- Short direct questions bypass retrieval, embeddings, skills, and MCP/tool loading.
- Repository and coding tasks use local retrieval, bounded tools, transactional edit guards, and verified completion evidence.
- Runtime loads `HARNESS.enc`; development-time synchronization is not a runtime dependency.

## Current Source-Verified Profile

- Model alias and GGUF repository: `mradermacher/Kimi-Linear-48B-A3B-Instruct-i1-GGUF`.
- GGUF file and label: `Kimi-Linear-48B-A3B-Instruct.i1-Q6_K.gguf` / `Kimi-Linear-48B-A3B-Instruct.i1-Q6_K`.
- Requested context ceiling: `1048576` tokens; default maximum output: `16384` tokens.
- Uses a multi-GPU layer split, a `131072` fit minimum and matching `f16` K/V caches.
- Documentation refreshed 2026-09-19 against the then-current standalone script and declarative profile.
- Smoke on 2026-09-12 loaded successfully and completed the task in `9.55 s`, but failed overall because the simple answer did not contain the expected answer and two GUI heartbeat stalls occurred and recovered.
