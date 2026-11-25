# Hermes 4.3 and Ripple

The **72 GB VRAM+** picker tier includes these launchers:

- `AI_Agent_Qubitz_Hermes-4.3-36B-Q8_0.py`: [Nous Research Q8_0](https://huggingface.co/NousResearch/Hermes-4.3-36B-GGUF/blob/9ce6f623874b8e9cb7617c399b67cec820b7a594/hermes-4_3_36b-Q8_0.gguf), 38.4 GB, `seed_oss` architecture.
- [Ripple 65B Preview AD-Q6_K](AI_Agent_Qubitz_Ripple-65B-Preview-AD-Q6_K.md): the two pinned shards total about 57.7 GB and use the `ricursive` architecture.

All default to 131,072 context tokens and use llama.cpp v1.0.0 (b12359).
Layer fitting uses eligible GPUs and may place overflow in RAM. The tier
is a picker label, not a guarantee of full GPU residency.
