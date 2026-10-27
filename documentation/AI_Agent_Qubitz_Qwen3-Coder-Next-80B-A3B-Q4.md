# Qwen3-Coder-Next 80B A3B UD-Q4_K_S

## Launcher

`variants/AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py` requires project-root `runtime/`. Select it in the GUI, run it directly, or use:

```sh
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py
```

Uses the Qwen3-Next hybrid attention/MoE architecture with 80B total and approximately 3B active parameters, with coding-agent training and weights. Its registered launcher is independently selectable.

## Model

- Alias and repository: `unsloth/Qwen3-Coder-Next-GGUF`.
- Exact file: `Qwen3-Coder-Next-UD-Q4_K_S.gguf`, a single GGUF, not a shard set.
- Default cache: `.cache/models/unsloth/Qwen3-Coder-Next-GGUF/`.
- Embeddings: `BAAI/bge-code-v1`.
- Requested context ceiling: `262144` tokens; maximum output remains `16384`.
- Chat sampling: temperature `1.0`, top-p `0.95`, top-k `40`, min-p `0.0`, repeat penalty `1.0`, presence penalty `0.0`.
- Native non-thinking model: no synthetic thinking mode or hand-replaced chat template is enabled. llama.cpp uses the model's GGUF template and native tool parsing.

The [official configuration](https://huggingface.co/Qwen/Qwen3-Coder-Next/blob/main/config.json) declares `max_position_embeddings=262144`. The [official model card](https://huggingface.co/Qwen/Qwen3-Coder-Next) confirms this native limit and recommends the sampling values above. RoPE settings remain in the model artifact; no context extrapolation is added.

## Placement

The copied launcher retains explicit `CUDA0,CUDA1` layer placement, a `1536` MiB safety target per GPU, a `131072` automatic-fit context target, and the existing shared llama.cpp runtime channel. Fitting may lower the effective context and leave unavoidable weight overflow in system RAM. The requested ceiling is not a guarantee that the full context or all weights fit in VRAM.

This is a two-24GB-GPU/48GB aggregate VRAM variant, not a single-24GB full-GPU model. No MTP, tensor-parallel/P2P mode, new backend installation, or reduced memory reserve is introduced.

## Evidence

Publisher metadata checked on 2026-10-14 for the [requested GGUF](https://huggingface.co/unsloth/Qwen3-Coder-Next-GGUF/blob/main/Qwen3-Coder-Next-UD-Q4_K_S.gguf):

- Repository commit: `ce09c67b53bc8739eef83fe67b2f5d293c270632`.
- File size: `46079830016` bytes.
- File SHA256: `beb854e08288f8ed5f790f2eb3d5e8fa3ca36205a282e5d149c15b368292c47b`.

These are remote metadata, not proof of a local download or successful inference. The published `imatrix_unsloth.gguf_file` is a quantization input, not an inference dependency; it is not downloaded or added to the launcher. See [llama.cpp imatrix documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/imatrix/README.md).

The new launcher is not automatically added to the human-approved 27-variant follow-up matrix. CPU/startup, native tool parsing, GUI behavior, hardware fit and coding performance remain runtime validation work; existing historical results are not results for this model.
