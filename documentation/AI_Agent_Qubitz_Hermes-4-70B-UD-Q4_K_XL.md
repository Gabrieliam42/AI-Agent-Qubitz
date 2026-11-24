# Hermes 4 70B UD-Q4_K_XL

Select `AI_Agent_Qubitz_Hermes-4-70B-UD-Q4_K_XL.py` under **72 GB VRAM+**.
The launcher pins Unsloth's [single root-level GGUF](https://huggingface.co/unsloth/Hermes-4-70B-GGUF/blob/b5483741203956fbc0557a26429ee73bf4cb7944/Hermes-4-70B-UD-Q4_K_XL.gguf)
(42,664,578,208 bytes). It does not use the Q6 or Q8 shards.

It keeps the Hermes defaults: 131,072 context tokens, Q8_0 KV cache, and
llama.cpp v1.0.0 (b12359). GPU fitting uses compatible devices and allows RAM
overflow. A smaller GGUF does not guarantee full GPU residency or faster decode.
