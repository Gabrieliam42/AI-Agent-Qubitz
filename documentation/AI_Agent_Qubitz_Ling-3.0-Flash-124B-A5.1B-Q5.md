# AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

- Script: `AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py`
- Model repository: `AtomicChat/Ling-3.0-flash-GGUF`
- Model files: `AD-Q5_K_M/Ling-3.0-flash-AD-Q5_K_M-00001-of-00002.gguf` and `00002-of-00002.gguf`
- Architecture: 124B total, 5.1B active hybrid-linear MoE with 42 layers.
- Requested context: `262144`; automatic-fit minimum: `131072` when feasible.
- Runtime: isolated `AtomicBot-ai/atomic-llama-cpp-turboquant` `b10269-1.6.0`, required by the selected AD quant and carrying the Ling SwiGLU-clamp correction.
- Placement: llama.cpp fits layers across all visible compatible GPUs, reserves 1536 MiB on each GPU and leaves unavoidable weight overflow in system RAM.
- Acquisition: downloads both native GGUF shards automatically when online; no MTP helper is configured.

## Runtime Bootstrap

- Online startup downloads the pinned Windows x64 CUDA 12 bundle when required runtime files are missing, then reuses the isolated cache. Its binaries and CUDA dependencies are bundled in one archive, not separate assets.
- If Windows execution is unavailable under WSL, the native fallback downloads the same Atomic release's Linux x64 CUDA 12.4 bundle into `.cache/atomic-llama.cpp-turboquant-b10269-1.6.0/linux-cuda-12.4`; it does not fall back to stock llama.cpp.
- The native installer validates archive size and the release SHA256 when supplied. `QUBITZ_OFFLINE=1` prevents missing-runtime downloads.
- Using the TurboQuant fork does not enable TurboQuant KV compression: this variant retains `q4_0` K/V cache and CUDA FlashAttention.

## Current Source-Verified Profile

- Model alias and GGUF repository: `AtomicChat/Ling-3.0-flash-GGUF`.
- Launch shard: `Ling-3.0-flash-AD-Q5_K_M-00001-of-00002.gguf`; both shards are required. Profile label: `Ling-3.0-flash-124B-A5.1B-AD-Q5_K_M`.
- Requested context ceiling: `262144` tokens; default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- Uses isolated `AtomicBot-ai/atomic-llama-cpp-turboquant` `b10269-1.6.0`, automatic multi-GPU layer fitting, a `131072` fit minimum, and `q4_0` K/V cache; profile records no MTP draft or background prewarm. The earlier two-GPU observation above remains historical evidence, not a fixed device-count requirement.
- This profile block reflects source metadata. The live observation above establishes Windows startup and effective context, not task correctness or performance baselines.
