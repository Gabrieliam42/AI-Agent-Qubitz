# Ministral 3 8B Instruct 2512 Q5 - 12 GB

The launcher is `variants/AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py`, copied from Devstral Small 2 24B, the existing dense Mistral-family variant. Both upstream configurations use `mistral3` with a `ministral3` text decoder. It requires project-root `runtime/` and the matching native policy bundle.

## Model And Context

- Model alias: `mistralai/Ministral-3-8B-Instruct-2512-GGUF`
- GGUF repository: `mistralai/Ministral-3-8B-Instruct-2512-GGUF`
- GGUF file: `Ministral-3-8B-Instruct-2512-Q5_K_M.gguf`
- Default weight cache: `.cache/models/mistralai/Ministral-3-8B-Instruct-2512-GGUF/Ministral-3-8B-Instruct-2512-Q5_K_M.gguf`
- GGUF label: `Ministral-3-8B-Instruct-2512-Q5_K_M`
- Embedding model: `BAAI/bge-code-v1`.
- Requested context ceiling: `262144` tokens.
- Default maximum output: `16384` tokens.

The [official configuration](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512/blob/main/config.json) declares `text_config.max_position_embeddings=262144`, and the GGUF repository metadata agrees. The copied default needs no increase or reduction on model-architecture grounds. See the [model card](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512) and [exact quantization](https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF/blob/main/Ministral-3-8B-Instruct-2512-Q5_K_M.gguf).

## 12 GB Runtime Policy

The copy declares `QUBITZ_VRAM_BUDGET_MIB=12288`, selects only `CUDA0` with no device splitting, and retains the shared 1536 MiB safety-margin and automatic context/KV fitting policy. Budgeted fitting uses a `32768` fit-context floor and permits weight offload when necessary. The budget applies on larger GPUs too; it is a fitting policy, not an OS-level allocation limit.

The requested 256K ceiling does not guarantee a 256K loaded window in 12 GB. Available memory, embeddings, KV state and temporary allocations determine the effective window. The approximately 6.06 GB weight file is not the total runtime footprint.

This copy pins official llama.cpp `b11887` under `.cache/llama.cpp-b11887/`. Its private sampling adapter uses temperature `0.05`, within Mistral's below-`0.1` recommendation, rather than an unrelated fallback profile. Manual temperature controls remain available. No MTP is enabled or other variant's settings changed.

Normal online startup prepares supported missing artifacts; offline startup requires complete local artifacts. Vision is not enabled by this text/tool-only wrapper. Static checks do not establish live compatibility, full-window fit, speed or task quality.

```text
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py
```

See [runtime packaging](../runtime/README.md).
