# AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

- Script: `AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py`
- Model repository: `AtomicChat/Ling-3.0-flash-GGUF`
- Model files: `AD-Q5_K_M/Ling-3.0-flash-AD-Q5_K_M-00001-of-00002.gguf` and `00002-of-00002.gguf`
- Architecture: 124B total, 5.1B active hybrid-linear MoE with 42 layers.
- Requested context: `262144`; automatic-fit minimum: `131072` when feasible.
- Runtime: isolated `AtomicBot-ai/atomic-llama-cpp-turboquant` `b10269-1.6.0`, required by the selected AD quant and carrying the Ling SwiGLU-clamp correction.
- Placement: llama.cpp fits layers across `CUDA0` and `CUDA1`, reserves 1536 MiB on each GPU, and leaves unavoidable weight overflow in system RAM.
- Acquisition: downloads both native GGUF shards automatically when online; no MTP helper is configured.

## Runtime Bootstrap

- Online startup downloads the pinned Windows x64 CUDA 12 bundle when required runtime files are missing, then reuses the isolated cache. Its binaries and CUDA dependencies are bundled in one archive, not separate assets.
- If Windows execution is unavailable under WSL, the native fallback downloads the same Atomic release's Linux x64 CUDA 12.4 bundle into `.cache/atomic-llama.cpp-turboquant-b10269-1.6.0/linux-cuda-12.4`; it does not fall back to stock llama.cpp.
- The native installer validates archive size and the release SHA256 when supplied. `QUBITZ_OFFLINE=1` prevents missing-runtime downloads.
- Using the TurboQuant fork does not enable TurboQuant KV compression: this variant retains `q4_0` K/V cache and CUDA FlashAttention.

## Behavioral Compatibility Evidence

The November 5 partial remaining matrix recorded passed read-only-to-full and
online-source-update trajectories. The multi-module follow-up failed after a
type-changing candidate was correctly rejected before dispatch; HTML creation
was interrupted by the user stop. These observations do not erase earlier
runtime failures or establish general model compatibility. See the
partial review (maintainer record, not shipped).

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.
