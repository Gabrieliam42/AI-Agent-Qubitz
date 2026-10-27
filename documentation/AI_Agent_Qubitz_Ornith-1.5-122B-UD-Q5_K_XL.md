# AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated.

## Model And Lineage

- Clone base: `AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py`, matching the published `Qwen3_5MoeForConditionalGeneration` architecture family and Ornith reasoning/sampling behavior.
- Placement/shard logic donor: Qwen3.8-Flash-Next's existing three-shard, 48-layer dual-GPU/RAM adapter; model names, configuration constants, environment variables and diagnostics are renamed for Ornith 122B.
- Model alias and repository: `unsloth/Ornith-1.5-122B-GGUF`.
- Quantization directory: `UD-Q5_K_XL`.
- Launch file: `Ornith-1.5-122B-UD-Q5_K_XL-00001-of-00003.gguf`; both remaining siblings are required.
- Embedding model: `BAAI/bge-code-v1`. Text and tool use only; vision projector and speculative/MTP execution are not enabled.

## Memory And Context

- Tier: 48 GB VRAM+; two RTX 3090 24GB GPUs plus available system RAM, not a 48GB model-size limit or fixed RAM requirement.
- All three published files total 94,611,939,505 bytes (about 88.1 GiB). Shard one is mostly metadata; placement uses the total of all siblings.
- Uses `CUDA0,CUDA1`, layer splitting, `--fit on`, `q4_0` KV cache, a 131072-token fit floor and 1536 MiB VRAM reserve on each device. Excess weights remain CPU-addressable; memory fitting is not a guarantee of successful loading.
- `num_ctx=262144` matches `text_config.max_position_embeddings` and the model card's native context. This is a requested ceiling; Qubitz uses the context actually reported by llama.cpp for request budgeting after fitting.
- The card describes approximately 1M-token YaRN extension for vLLM/SGLang. No equivalent quality, memory-fit or llama.cpp validation is established here, so no extension is enabled by default.
- Uses the already established llama.cpp `b10809` runtime directory; no dependency upgrades or runtime rebuild are part of adding the variant.

## Sampling And Limits

Existing Ornith route-aware sampling is retained: general reasoning uses temperature 1.0, top-p 0.95, top-k 20, min-p 0, presence penalty 1.5 and repetition penalty 1.0; precise coding/tool routes use temperature 0.6 and presence penalty 0. Default maximum output remains 16384 tokens.

Optional developer tuning uses the isolated `QUBITZ_ORNITH15_122B_` prefix for `THREADS`, `THREADS_BATCH`, `CACHE_REUSE`, `N_CPU_MOE`, `KV_CACHE_TYPE`, `BATCH_SIZE` and `UBATCH_SIZE`. The existing backend validates bounds and available flags before applying them.

Model-free checks do not establish actual two-GPU loading, usable full context, visual capabilities, speed or model quality. No weights are downloaded by these checks. Frozen historical campaign records are not updated or resumed.

## Sources

- [Official configuration](https://huggingface.co/ornith-ai/Ornith-1.5-122B/blob/main/config.json)
- [Official model card](https://huggingface.co/ornith-ai/Ornith-1.5-122B)
- [Selected Unsloth GGUF files](https://huggingface.co/unsloth/Ornith-1.5-122B-GGUF/tree/main/UD-Q5_K_XL)

Resolved revisions, exact shard metadata, clone provenance and model-free verification receipts are saved under `.cache/ornith122_variant_20261021/`.
