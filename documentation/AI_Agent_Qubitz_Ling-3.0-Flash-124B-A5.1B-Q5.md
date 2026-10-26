# AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

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

## Live Observation: 2026-10-02

- The Windows CUDA 12.4 bundle was installed and its archive matched the published SHA256. The manual launch used that isolated executable, loaded the AD model, and entered generation with `131072` tokens of effective context.
- The earlier `unknown model architecture: 'bailingmoe3'` startup failure did not recur. The run uses `CUDA0,CUDA1` with layer fitting; both GPUs have substantial VRAM allocation.
- The HTML-creation task failed after about 12:36: chunked content generation produced a grammar containing `char{1,3072}`, which the server rejected with `number of repetitions exceeds sane defaults` / `Failed to initialize samplers: failed to parse grammar`. Streaming and non-streaming requests both returned HTTP 400; no completed output was verified.
- Other server warnings concern tokenizer EOS metadata, CPU tensor overrides with mmap, and unauthenticated wildcard CORS; these have not been linked to the failure. A GUI heartbeat warning caught the main thread inside the modal error dialog, not model generation.
- Native Linux fallback and the new focused bootstrap tests remain untested. GUI event text and server logs are observable; native GUI rendering and separate-terminal output are not directly captured.

## Current Source-Verified Profile

- Model alias and GGUF repository: `AtomicChat/Ling-3.0-flash-GGUF`.
- Launch shard: `Ling-3.0-flash-AD-Q5_K_M-00001-of-00002.gguf`; both shards are required. Profile label: `Ling-3.0-flash-124B-A5.1B-AD-Q5_K_M`.
- Requested context ceiling: `262144` tokens; default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- Uses isolated `AtomicBot-ai/atomic-llama-cpp-turboquant` `b10269-1.6.0`, dual-GPU layer fitting over `CUDA0,CUDA1`, a `131072` fit minimum, and `q4_0` K/V cache; profile records no MTP draft or background prewarm.
- This profile block reflects source metadata. The live observation above establishes Windows startup and effective context, not task correctness or performance baselines.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

