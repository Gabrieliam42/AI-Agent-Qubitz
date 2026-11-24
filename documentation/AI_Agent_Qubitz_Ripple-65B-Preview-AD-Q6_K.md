# Ripple 65B Preview AD-Q6_K

Select `AI_Agent_Qubitz_Ripple-65B-Preview-AD-Q6_K.py` under **72 GB VRAM+**.
The launcher uses the two AD-Q6_K shards (about 57.7 GB total) at the root of the
[pinned AtomicChat GGUF revision](https://huggingface.co/AtomicChat/Ripple-65B-Preview-GGUF/tree/df550a7178a87bbfb6ee2bdbd37603c553d536ee).
It launches shard `00001-of-00002`.

Ripple is dense but uses the `ricursive` architecture, not Llama or Qwen.
[AtomicChat's card](https://huggingface.co/AtomicChat/Ripple-65B-Preview-GGUF/blob/main/README.md)
requires llama.cpp b12167 or later and `--jinja`. The launcher pins official
v1.0.0 (b12359), which includes `ricursive` support, and requests 131,072
tokens by default. The backend fits layers across eligible GPUs. Some CPU overflow
is possible after the KV cache, compute buffers and per-GPU margin are included.
