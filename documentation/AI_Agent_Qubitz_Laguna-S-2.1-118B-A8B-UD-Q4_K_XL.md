# Laguna S 2.1 118B-A8B UD-Q4_K_XL

Select `AI_Agent_Qubitz_Laguna-S-2.1-118B-A8B-UD-Q4_K_XL.py` under **72 GB VRAM+**.
Laguna S is a 118B-parameter MoE with 8B active per token and the same architecture
as Laguna XS. This launcher uses [three pinned Unsloth shards](https://huggingface.co/unsloth/Laguna-S-2.1-GGUF/tree/750f92f90cf54159c4d7a610cb7b3e74498e75c6/UD-Q4_K_XL)
totaling 73.4 GB.

The launcher starts at 16,384 tokens, matching [Unsloth's llama.cpp example](https://huggingface.co/unsloth/Laguna-S-2.1-GGUF).
The [model configuration](https://huggingface.co/poolside/Laguna-S-2.1/blob/main/config.json)
allows 1,048,576 tokens; that is a model limit, not a promise of local memory fit.
The managed runtime is official llama.cpp v1.0.0 (b12359), using the GGUF's Jinja
template without a speculative draft. All selected GPUs are eligible. Fit reserves
1.5 GiB per GPU and uses RAM for overflow when the host-memory check permits.
