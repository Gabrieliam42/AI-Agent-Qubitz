# AI_Agent_Qubitz_CalibForge-35B-A3B.i1-Q4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_CalibForge-35B-A3B.i1-Q4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_CalibForge-35B-A3B.i1-Q4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for CalibForge 35B-A3B i1 Q4.

## Model
- Served alias and GGUF repository: `mradermacher/CalibForge-35B-A3B-i1-GGUF`
- GGUF file and label: `CalibForge-35B-A3B.i1-Q4_K_M.gguf`
- Architecture: Qwen3.5-family 35B-A3B mixture of experts; the selected GGUF contains the trunk model without bundled MTP tensors
- Embeddings: `BAAI/bge-code-v1`

## Runtime
- Default context ceiling: `262144` tokens, matching the source configuration.
- Default maximum output: `16384` tokens.
- Uses the standard 24 GB Qubitz GPU-first llama.cpp fitting path; effective context may be reduced to preserve VRAM headroom.
- Uses the established Qwen3.6 35B route-sampling contract because the source does not publish a distinct generation configuration.
- Remains independently runnable; builder and evaluation files are development-time tooling, not runtime dependencies.

## Current Source-Verified Profile

These values describe the configured launcher, not a guarantee of hardware fit or runtime throughput.
- Model alias: `mradermacher/CalibForge-35B-A3B-i1-GGUF`
- GGUF repository: `mradermacher/CalibForge-35B-A3B-i1-GGUF`
- GGUF file: `CalibForge-35B-A3B.i1-Q4_K_M.gguf`
- GGUF label: `CalibForge-35B-A3B.i1-Q4_K_M`
- Requested context ceiling: `262144` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.
