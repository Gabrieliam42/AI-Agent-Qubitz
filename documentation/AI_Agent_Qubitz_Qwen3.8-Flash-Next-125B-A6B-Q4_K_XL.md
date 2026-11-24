# AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-Q4_K_XL.py

Qubitz launcher for Qwen3.8-Flash-Next 125B A6B in UD-Q4_K_XL. This variant
is included in the normal variant selector and targets the 72 GB VRAM+ tier
with system-RAM overflow; actual fit depends on context, available memory and
backend placement.

## Model files

- Repository: `unsloth/Qwen3.8-Flash-Next-GGUF`
- Quantization directory: `UD-Q4_K_XL`
- Pinned model revision: [f5d175de2cf7f720d45d6064019a81fcf056e986](https://huggingface.co/unsloth/Qwen3.8-Flash-Next-GGUF/tree/f5d175de2cf7f720d45d6064019a81fcf056e986/UD-Q4_K_XL)
- Required shards: `Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf` through `00004-of-00004.gguf`
- Requested context ceiling: 262144 tokens; the loaded context can be lower to respect memory limits.
- The launcher checks the complete shard set and metadata, and can acquire missing managed model files when online. Model weights are not included in Git.

## Runtime and MTP

The launcher selects compatible GPUs visible to its backend and keeps per-device
memory headroom. CPU offload may be needed; paging is not a substitute for
physical memory or a performance guarantee. The variant uses an isolated
Qwen3.8-compatible llama.cpp runtime.

MTP is off by default. When enabled in the GUI, choose `Shared Q4` or `Shared
Q8`. The corresponding shared head and compatible runtime must already exist
in the local cache; this variant does not download missing MTP components.
Changing the selection takes effect only after the current task and owned
backend have stopped cleanly.

See the [IQ4_XS guide](AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.md)
for the other public Qwen3.8-Flash-Next quantization.
