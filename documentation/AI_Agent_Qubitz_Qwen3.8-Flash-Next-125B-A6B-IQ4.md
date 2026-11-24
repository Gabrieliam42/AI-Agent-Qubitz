# AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py` or run the variant directly.

Local-inference Qubitz variant for Qwen3.8-Flash-Next 125B A6B UD-IQ4_XS.

The [Q4_K_XL profile](AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-Q4_K_XL.md)
is also available in the 72 GB VRAM+ tier.

## Model

- Served alias and GGUF repo: `unsloth/Qwen3.8-Flash-Next-GGUF`
- GGUF directory: `UD-IQ4_XS`
- Model files are pinned to the [compatible three-shard revision](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/766911a6b7369840a91dbcd95f9f997acaab6cd6/UD-IQ4_XS).
  The newer four-shard layout on `main` is not interchangeable with this profile.
- Launch shard: `Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf`
- Required siblings: shards `00002-of-00003` and `00003-of-00003`
- GGUF label: `Qwen3.8-Flash-Next-125B-A6B-UD-IQ4_XS`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Uses all backend-visible compatible GPUs (including one, two or three) and system RAM for model-weight overflow; fit and per-device memory guards still apply.
- Requests the model's native `262144`-token context; llama.cpp may fit a lower effective context when required by available memory.
- Reuses a complete local set or downloads missing shards online. Checks the
  expected sizes and GGUF headers before launching llama.cpp with shard 1.
- Uses all backend-visible compatible GPUs, `layer` split, automatic fit, `q4_0` KV cache, a 131072-token fit floor, and a 1536 MiB safety margin on each GPU. MTP's additional per-GPU reserve remains required when enabled.
- Computes placement against the aggregate size of all shards, not the small metadata-heavy first shard alone.
- Keeps as much of the model as safely fits in aggregate VRAM and leaves unavoidable overflow in system RAM.
- Uses isolated llama.cpp `b10809`, the binary release associated with v0.4.0 and required for Qwen3.8 Flash Next support.

### Optional MTP

MTP is off by default. In the GUI, enable it and select `Shared Q4` or
`Shared Q8` to choose the matching draft-head file. The selected head and its
compatible runtime must already be present locally; missing MTP components are
not downloaded automatically. Switching requires the current task to be idle
and the owned backend to be stopped cleanly.

`Shared Q4` uses `mtp-Qwen3.8-Flash-Next-shared-Q4_K_M.gguf` and `Shared Q8`
uses `mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf`.


## Qubitz Behavior

- Remains independently runnable with its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Uses Qwen3.8-family route-aware sampling, including the model-card thinking profile for agentic work and non-thinking profile for simple responses.
- Uses local llama.cpp GGUF inference only; no required paid service or hosted inference account.
- Displays live backend-counted `tok/s` beside `Show Reasoning`; unavailable
  prompt-processing counters are not reported as decoding speed. Final token
  speed distinguishes actual aggregate average from the optional token-weighted
  interval-median typical rate; both exclude startup, retrieval, prefill and tools.
