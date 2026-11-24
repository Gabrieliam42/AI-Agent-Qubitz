# Laguna S 2.1 118B-A8B Q8_0

Select `AI_Agent_Qubitz_Laguna-S-2.1-118B-A8B-Q8_0.py` under **72 GB VRAM+**.
It uses [four pinned Unsloth shards](https://huggingface.co/unsloth/Laguna-S-2.1-GGUF/tree/750f92f90cf54159c4d7a610cb7b3e74498e75c6/Q8_0)
totaling 125.0 GB.

It uses the same managed llama.cpp v1.0.0 runtime, 16,384-token default, and GPU
fit as [UD-Q4_K_XL](AI_Agent_Qubitz_Laguna-S-2.1-118B-A8B-UD-Q4_K_XL.md).
Expect substantial RAM offload on three 24 GiB RTX 3090s. There is no paging
fallback in the host-memory budget. Start with UD-Q4_K_XL if speed matters;
actual placement, speed, and output quality require a live comparison.
