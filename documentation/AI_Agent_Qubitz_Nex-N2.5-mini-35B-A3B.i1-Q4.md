# AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

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

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.
