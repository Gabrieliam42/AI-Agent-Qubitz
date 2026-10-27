# AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for DeepSeek-V4-Flash-0731 UD-IQ4_XS.

## Model

- Served alias and GGUF repo: `unsloth/DeepSeek-V4-Flash-0731-GGUF`
- GGUF directory: `UD-IQ4_XS`
- Launch shard: `DeepSeek-V4-Flash-0731-UD-IQ4_XS-00001-of-00004.gguf`
- Required siblings: shards `00002-of-00004` through `00004-of-00004`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Requires two 24 GB CUDA GPUs and substantial system RAM for model-weight overflow.
- Requests `262144` context, below the model configuration's `1048576` architectural ceiling; automatic fitting retains a `131072` minimum target when feasible.
- Downloads and verifies all four GGUF shards and calculates placement from their aggregate size.
- Uses `CUDA0,CUDA1`, layer splitting, correctness-first `f16` KV cache, automatic fit, and a 1536 MiB safety margin per GPU. Quantized K cache remains an explicit benchmark override because affected upstream DeepSeek-V4 builds can silently produce corrupted output with it.
- Uses isolated official llama.cpp `b10982` for DeepSeek-V4 architecture support.
- `UD-IQ4_XS` is selected instead of `UD-IQ4_NL` to reduce RAM and transfer pressure on this 48 GB VRAM plus 128 GB RAM host.
- MTP/speculative decoding is not enabled initially; the target model runs directly to avoid known multi-GPU speculative-decoding instability until live validation supports enabling it.

## Qubitz Behavior

- Remains independently runnable with its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Uses local GGUF inference only and requires no paid service.

## Verification

- Model identity, profile, shard manifest, aggregate-size placement, syntax, and Ruff are checked statically.
- The model download, live dual-GPU/RAM load, effective context, and generation behavior require separate runtime verification.

## Current Source-Verified Profile

- Model alias and GGUF repository: `unsloth/DeepSeek-V4-Flash-0731-GGUF`.
- Launch shard: `DeepSeek-V4-Flash-0731-UD-IQ4_XS-00001-of-00004.gguf`; all four shards are required.
- Requested context ceiling: `262144` tokens; default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- Uses isolated llama.cpp `b10982`, dual-GPU layer fitting over `CUDA0,CUDA1`, a `131072` fit minimum, and `f16` K/V cache; profile records no MTP draft or background prewarm.
- This profile block reflects current source metadata; live download, load, effective context, and quality still require runtime verification.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

