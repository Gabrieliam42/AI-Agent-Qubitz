# AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Tiel-Coder 35B-A3B MTP UD-Q4_K_XL.

## Model

- Served alias and GGUF repository: `peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP`
- GGUF file: `Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL.gguf`
- GGUF label: `Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL`
- Architecture: Ornith-1.5/Qwen3.5-family 35B-total, approximately 3B-active mixture of experts
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Requested context ceiling: `262144` tokens, matching the source model configuration.
- Default maximum output: `16384` tokens.
- Uses the standard 24 GB Qubitz GPU-first llama.cpp fitting path; effective context may be reduced to preserve VRAM headroom.
- Requires a llama.cpp runtime that advertises `--spec-type`; the wrapper adds `--spec-type draft-mtp` so the bundled MTP head is active rather than ignored.
- Uses Tiel's Ornith-derived sampling family: temperature `1.0`, top-p `0.95`, and top-k `20` generally; temperature `0.6` for simple, MCP/tool, and coding/edit routes.
- Remains independently runnable; builder and evaluation files are development-time tooling, not runtime dependencies.

## Verification

- Identity, profile registration, shared-wrapper parity, syntax, and static checks are verified separately from live model download and inference.

- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

- Model alias and GGUF repository: `peculiar-ragdoll/Tiel-Coder-35B-A3B-GGUF-MTP`.
- GGUF file and label: `Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL.gguf` / `Tiel-Coder-35B-A3B-MTP-UD-Q4_K_XL`.
- Requested context ceiling: `262144` tokens; default maximum output: `16384` tokens.
- Uses the shared llama.cpp runtime with the variant-local `draft-mtp` configuration.
- Documentation refreshed 2026-09-19 against the then-current standalone script and declarative profile.
- Smoke on 2026-09-12 passed: question `2.05 s`, task `1.57 s`, with no timeout, thermal abort, or behavior issue.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

