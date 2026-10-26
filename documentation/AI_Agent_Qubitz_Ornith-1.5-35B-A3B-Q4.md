# AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Ornith-1.5 35B-A3B Q4.

## Model
- Served alias and GGUF repository: `ornith-ai/Ornith-1.5-35B-A3B-GGUF`
- GGUF file: `Ornith-1.5-35B-Q4_K_M.gguf`
- GGUF label: `Ornith-1.5-35B-Q4_K_M`
- Architecture: Qwen3.5-family 35B-A3B mixture of experts with one MTP layer
- Embeddings: `BAAI/bge-code-v1`

## Runtime
- Default context ceiling: `262144` tokens, matching the source configuration and model card.
- Default maximum output: `16384` tokens.
- Uses the standard 24 GB Qubitz GPU-first llama.cpp fitting path; effective context may be reduced to preserve VRAM headroom.
- Uses the existing Ornith route-sampling contract; the source recommends temperature `0.6`, top-p `0.95`, and top-k `20` for general tasks and temperature `1.0` for benchmark reproduction.
- Remains independently runnable; builder and evaluation files are development-time tooling, not runtime dependencies.

## Verification
- Identity, profile registration, shared-wrapper parity, syntax, and static checks are verified separately from live model download and inference.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `ornith-ai/Ornith-1.5-35B-A3B-GGUF`
- GGUF repository: `ornith-ai/Ornith-1.5-35B-A3B-GGUF`
- GGUF file: `Ornith-1.5-35B-Q4_K_M.gguf`
- GGUF label: `Ornith-1.5-35B-Q4_K_M`
- Requested context ceiling: `262144` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

