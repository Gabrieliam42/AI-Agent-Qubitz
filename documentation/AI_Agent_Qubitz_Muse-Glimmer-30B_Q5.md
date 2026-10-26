# AI_Agent_Qubitz_Muse-Glimmer-30B_Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Muse-Glimmer-30B_Q5.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Muse-Glimmer-30B_Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Muse Glimmer 30B Q5.

## Model
- Served alias: `unsloth/Muse-Glimmer-30B-GGUF`
- GGUF repo: `unsloth/Muse-Glimmer-30B-GGUF`
- GGUF file: `Muse-Glimmer-30B-UD-Q5_K_XL.gguf`
- GGUF label: `Muse-Glimmer-30B-UD-Q5_K_XL`
- Embeddings: `BAAI/bge-code-v1`

## Runtime
- Uses shared Qubitz runtime source without importing another variant's mutable module.
- Default context ceiling: `131072` tokens, matching the model card's documented context length.
- Uses Muse's documented sampling defaults: temperature `1.0`, top-p `0.95`, and top-k `64` unless the GUI temperature override is active.
- Uses the shared llama.cpp Windows CUDA runtime and GPU-first fit policy with a 1536 MiB safety margin.
- Supports GUI, CLI, and stdio MCP server modes without importing another variant at runtime.
- The current Qubitz interface is text-oriented; Muse's optional image-input path is not wired by this variant.

## Current State
- Registered in `qubitz_variant_profiles.json` and discovered automatically by the GUI variant selector and evaluation tooling.
- Loads `HARNESS.enc` at runtime; the editable plaintext harness is not a runtime input.
- Live model download, load, and behavior tests have not yet been run for this new variant.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `unsloth/Muse-Glimmer-30B-GGUF`
- GGUF repository: `unsloth/Muse-Glimmer-30B-GGUF`
- GGUF file: `Muse-Glimmer-30B-UD-Q5_K_XL.gguf`
- GGUF label: `Muse-Glimmer-30B-UD-Q5_K_XL`
- Requested context ceiling: `131072` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- No additional profile-level exception is recorded; model-specific constants and launch logic remain in this variant's launcher.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

