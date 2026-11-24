# Hermes 4.3 and Ripple

The **72 GB VRAM+** picker tier includes these single-file launchers:

- `AI_Agent_Qubitz_Hermes-4.3-36B-Q8_0.py`: [Nous Research Q8_0](https://huggingface.co/NousResearch/Hermes-4.3-36B-GGUF/blob/9ce6f623874b8e9cb7617c399b67cec820b7a594/hermes-4_3_36b-Q8_0.gguf), 38.4 GB, `seed_oss` architecture.
- `AI_Agent_Qubitz_Ripple-65B-Preview-AD-Q4_K_M.py`: [AtomicChat AD-Q4_K_M](https://huggingface.co/AtomicChat/Ripple-65B-Preview-GGUF/blob/df550a7178a87bbfb6ee2bdbd37603c553d536ee/Ripple-65B-Preview-AD-Q4_K_M.gguf), 40.4 GB, `ricursive` architecture.
- `AI_Agent_Qubitz_Ripple-65B-Preview-AD-Q5_K_M.py`: [AtomicChat AD-Q5_K_M](https://huggingface.co/AtomicChat/Ripple-65B-Preview-GGUF/blob/df550a7178a87bbfb6ee2bdbd37603c553d536ee/Ripple-65B-Preview-AD-Q5_K_M.gguf), 47.9 GB, `ricursive` architecture.

All default to 131,072 context tokens and use llama.cpp v1.0.0 (b12359).
Layer fitting uses eligible GPUs and may place overflow in RAM. The tier
is a picker label, not a guarantee of full GPU residency.
