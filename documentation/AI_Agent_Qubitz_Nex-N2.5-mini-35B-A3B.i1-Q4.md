# AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Nex-N2.5-mini 35B-A3B i1 Q4.

## Model
- Served alias and GGUF repository: `mradermacher/Nex-N2.5-mini-i1-GGUF`
- GGUF file and label: `Nex-N2.5-mini.i1-Q4_K_M.gguf`
- Architecture: Qwen3.5-derived 35B-A3B mixture of experts with one MTP layer
- Embeddings: `BAAI/bge-code-v1`

## Runtime
- Default context ceiling: `262144` tokens, matching the source model configuration.
- Default maximum output: `16384` tokens.
- Uses the standard 24 GB Qubitz GPU-first llama.cpp fitting path; effective context may be reduced to preserve VRAM headroom.
- Uses the model-card sampling defaults: temperature `0.7`, top-p `0.95`, and top-k `40` unless the GUI temperature override is set.
- The variant remains independently runnable; builder and evaluation files are development-time tooling rather than runtime dependencies.

## Verification
- Model identity, profile registration, shared-wrapper parity, syntax, and static checks are validated separately from live inference.
- Live model download and inference are not performed automatically by documentation or static validation.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `mradermacher/Nex-N2.5-mini-i1-GGUF`
- GGUF repository: `mradermacher/Nex-N2.5-mini-i1-GGUF`
- GGUF file: `Nex-N2.5-mini.i1-Q4_K_M.gguf`
- GGUF label: `Nex-N2.5-mini.i1-Q4_K_M`
- Requested context ceiling: `262144` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

