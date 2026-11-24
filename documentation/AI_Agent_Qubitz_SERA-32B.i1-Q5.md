# AI_Agent_Qubitz_SERA-32B.i1-Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_SERA-32B.i1-Q5.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_SERA-32B.i1-Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Ai2 SERA-32B i1 Q5_K_S.

## Model

- Served alias: `mradermacher/SERA-32B-i1-GGUF`
- GGUF repo: `mradermacher/SERA-32B-i1-GGUF`
- GGUF file: `SERA-32B.i1-Q5_K_S.gguf`
- GGUF label: `SERA-32B.i1-Q5_K_S`
- Architecture: dense Qwen3, fine-tuned as a repository coding agent
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Single-RTX-3090 24 GB variant for coding, repository, and MCP/tool tasks.
- Requested context ceiling: `40960` tokens, matching the source configuration's `max_position_embeddings`; Ai2 reports its evaluations at 32K context.
- Default maximum output: `16384` tokens.
- Uses the shared official llama.cpp Windows CUDA runtime and GPU-first fitting with a 1536 MiB safety margin. The effective loaded context may be lower when weights, KV cache, and runtime buffers cannot fit safely.
- Uses local llama.cpp GGUF inference only; no required paid API, cloud service, or subscription.

## Qubitz Behavior

- Remains independently runnable and contains its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, memory, and thermal behavior.
- Short direct questions bypass retrieval, embeddings, skills, and MCP/tool loading.
- Repository and coding tasks use local retrieval, bounded tools, transactional edit guards, and verified completion evidence.
- Runtime loads `HARNESS.enc`; development-time synchronization is not a runtime dependency.

## Current Source-Verified Profile

- Model alias and GGUF repository: `mradermacher/SERA-32B-i1-GGUF`.
- GGUF file and label: `SERA-32B.i1-Q5_K_S.gguf` / `SERA-32B.i1-Q5_K_S`.
- Requested context ceiling: `40960` tokens; default maximum output: `16384` tokens.
- Uses the shared llama.cpp runtime and remains independently runnable.
- Documentation refreshed 2026-09-19 against the then-current standalone script and declarative profile.
- Smoke on 2026-09-12 passed: question `3.05 s`, task `2.06 s`, with no timeout, thermal abort, or behavior issue.
