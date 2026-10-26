# AI_Agent_Qubitz_Granite-4.2-30B_Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Granite-4.2-30B_Q5.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Granite-4.2-30B_Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for IBM Granite 4.2 30B Q5_K_M.

## Model

- Served alias and GGUF repository: `ibm-granite/granite-4.2-30b-GGUF`
- GGUF file: `granite-4.2-30b-Q5_K_M.gguf`
- GGUF label: `Granite-4.2-30B-Q5_K_M`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Single-RTX-3090 24 GB variant with a configured `131072`-token context ceiling.
- Maximum output defaults to `8096` tokens, retrieval embeddings stay on CUDA, and llama.cpp uses a `q8_0` KV cache.
- Uses the shared project llama.cpp runtime and keeps the variant independently runnable.

## Verification

- The declarative profile, embedded source, shared-wrapper parity, Python syntax, targeted Ruff checks, and encrypted-harness integrity pass the project release gate.
- Live model loading and task quality require separate smoke and disposable-matrix evaluation.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `ibm-granite/granite-4.2-30b-GGUF`
- GGUF repository: `ibm-granite/granite-4.2-30b-GGUF`
- GGUF file: `granite-4.2-30b-Q5_K_M.gguf`
- GGUF label: `Granite-4.2-30B-Q5_K_M`
- Requested context ceiling: `131072` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `8096` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

