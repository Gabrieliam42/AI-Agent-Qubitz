# Gemma 4 12B IT Q4 - 12 GB

The launcher is `variants/AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.py`, copied from the existing Gemma 4 31B dense variant and adapted to the 12 GB tier. It requires project-root `runtime/` and the matching native policy bundle.

## Model And Context

- Model alias: `unsloth/gemma-4-12b-it-GGUF`
- GGUF repository: `unsloth/gemma-4-12b-it-GGUF`
- GGUF file: `gemma-4-12b-it-Q4_K_M.gguf`
- Default weight cache: `.cache/models/unsloth/gemma-4-12b-it-GGUF/gemma-4-12b-it-Q4_K_M.gguf`
- GGUF label: `gemma-4-12b-it-Q4_K_M`
- Embedding model: `BAAI/bge-code-v1`.
- Requested context ceiling: `262144` tokens.
- Default maximum output: `16384` tokens.

Google's [configuration](https://huggingface.co/google/gemma-4-12B-it/blob/main/config.json) declares a dense decoder (`enable_moe_block=false`) and `max_position_embeddings=262144`. The GGUF repository metadata reports `gemma4` and the same context ceiling. The copied default therefore needs no change. See the [official card](https://huggingface.co/google/gemma-4-12B-it) and [exact quantization](https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/blob/main/gemma-4-12b-it-Q4_K_M.gguf).

## 12 GB Runtime Policy

The copy declares `QUBITZ_VRAM_BUDGET_MIB=12288`, selects only `CUDA0` with no device splitting, and retains the shared 1536 MiB safety-margin and automatic context/KV fitting policy. That policy uses a `32768` fit-context floor for budgeted variants and can offload weights when necessary rather than exceed its requested memory class. The budget applies even on a larger GPU; it is a fitting policy, not an OS-level allocation limit.

The 256K value is the model's requested ceiling, not a promise of a 256K loaded window in 12 GB. Runtime fitting may load less, and actual free VRAM, embeddings and temporary buffers still matter. The approximately 7.12 GB weight file alone does not establish total memory use or successful loading.

This copy pins official llama.cpp `b11887` under `.cache/llama.cpp-b11887/` without upgrading another variant's runtime. It retains Gemma's existing temperature `1.0`, top-p `0.95`, top-k `64` profile. MTP is not enabled. Supported missing artifacts are prepared during normal online startup; offline startup requires complete local artifacts.

The wrapper exposes text and tools only; the upstream image/audio interfaces are not enabled. Metadata and static checks do not prove inference compatibility, speed or task quality.

```text
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.py
```

See [runtime packaging](../runtime/README.md).
