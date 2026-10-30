# Qubitz Variant `num_ctx` Values

1. `AI_Agent_Qubitz_AgenticQwen-30B-A3B.i1-Q4.py`: `202752`
2. `AI_Agent_Qubitz_CalibForge-35B-A3B.i1-Q4.py`: `262144`
3. `AI_Agent_Qubitz_DeepSeek-V4-Flash-0731-IQ4.py`: `262144`
4. `AI_Agent_Qubitz_Devstral-Small-2-24B-Q4.py`: `262144`
5. `AI_Agent_Qubitz_Gemma-4-31B-IT_QAT-Q4.py`: `262144`
6. `AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py`: `202752`
7. `AI_Agent_Qubitz_GPT-OSS-20B_F16.py`: `131072`
8. `AI_Agent_Qubitz_GPT-OSS-120B_F16.py`: `131072`
9. `AI_Agent_Qubitz_Granite-4.2-30B_Q5.py`: `131072`
10. `AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py`: `131072`
11. `AI_Agent_Qubitz_Hypernova-60B-2605-Q5.py`: `131072`
12. `AI_Agent_Qubitz_KAT-Coder-V2.5-Dev-35B-A3B-Q4.py`: `262144`
13. `AI_Agent_Qubitz_Kimi-Linear-48B-A3B-Instruct.i1-Q6.py`: `1048576`
14. `AI_Agent_Qubitz_Laguna-XS-2.1-33B-A3B_Q4.py`: `262144`
15. `AI_Agent_Qubitz_Llama-3_3-Nemotron-Super-49B-v1_5_Q6.py`: `131072`
16. `AI_Agent_Qubitz_Macaron-V1-Tall-35B-A3B.i1-Q4.py`: `262144`
17. `AI_Agent_Qubitz_Muse-Glimmer-30B_Q5.py`: `131072`
18. `AI_Agent_Qubitz_Nemotron-3.5-Lightning-30B-A3B-IQ4.py`: `262144`
19. `AI_Agent_Qubitz_Nemotron-Cascade-2-30B-A3B-IQ4.py`: `262144`
20. `AI_Agent_Qubitz_Nex-N2.5-mini-35B-A3B.i1-Q4.py`: `262144`
21. `AI_Agent_Qubitz_North-Mini-Code-1.0-30B-A3B-Q4.py`: `262144`
22. `AI_Agent_Qubitz_Ornith-1.5-35B-A3B-Q4.py`: `262144`
23. `AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.py`: `101376`
24. `AI_Agent_Qubitz_Qwen3.6-35B-A3B_MTP_Q4.py`: `262144`
25. `AI_Agent_Qubitz_Qwen3.8-27B_Q4.py`: `262144`
26. `AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py`: `262144`
27. `AI_Agent_Qubitz_SERA-32B.i1-Q5.py`: `40960`
28. `AI_Agent_Qubitz_Tiel-Coder-35B-A3B-MTP-Q4.py`: `262144`
29. `AI_Agent_Qubitz_Ling-3.0-Flash-124B-A5.1B-Q5.py`: `262144`
30. `AI_Agent_Qubitz_Qwen3-Coder-Next-80B-A3B-Q4.py`: `262144`
31. `AI_Agent_Qubitz_LFM2.5-24B-A2B-Q5.py`: `131072`
32. `AI_Agent_Qubitz_Mellum2.1-12B-A2.5B-Thinking-Q6.py`: `131072`
33. `AI_Agent_Qubitz_Holo4-27B-Q4.py`: `262144`
34. `AI_Agent_Qubitz_Occamy-1.0-35B-A3B-Q4.py`: `262144`
35. `AI_Agent_Qubitz_AREX-2-27B.i1-Q6.py`: `262144`
36. `AI_Agent_Qubitz_MiniCPM-V-4.7-35B-A3B-Q4_1.py`: `262144`
37. `AI_Agent_Qubitz_Ornith-1.5-122B-UD-Q5_K_XL.py`: `262144`
38. `AI_Agent_Qubitz_Nemotron-3.5-Super-VL-120B-A12B-UD-Q6_K_XL.py`: `262144`
39. `AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.py`: `262144`
40. `AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.py`: `262144`

These configured runtime defaults were verified against `qubitz_variant_profiles.json`. The Qwen3.5 9B 12GB variant caps context at `101376` on every GPU.

This list contains the 40 current user-facing variants; developer-only guides and profiles are separate from the public catalog and automated validation.

These are requested context ceilings, not guaranteed effective windows: llama.cpp may fit a lower loaded context when model weights, KV cache, GPU headroom, or dual-GPU placement require it.

Gemma 4 12B Unified and Ministral 3 8B Instruct 2512 both retain `262144`: Google's `text_config.max_position_embeddings` and Mistral's corresponding configuration declare that value, and the GGUF repository metadata agrees. Their 12 GB single-GPU fitting policy may load a smaller effective window; it does not increase either model's declared ceiling or guarantee 256K fits in 12 GB. See their model guides for source links and memory limitations.

Hypernova 60B 2605 uses `131072` because its authoritative model configuration declares `max_position_embeddings: 131072`. Its YaRN configuration scales an original `4096` positions by a factor of `32`, producing `131072`; the model card also evaluates Terminal-Bench with `max-model-len 131072`. The dual-GPU Qubitz implementation provides enough aggregate VRAM for the Q5 weights and KV cache, but additional VRAM does not change the model's declared context limit. Setting it to `262144` would be unsupported context extrapolation, not restoration of a documented 256K trained context.

Llama-3.3-Nemotron-Super-49B-v1.5 uses `131072` because NVIDIA's authoritative model configuration declares `max_position_embeddings: 131072`, with Llama 3 RoPE scaling from an original `8192` positions by a factor of `16`.


Qwen3-Coder-Next also keeps `262144`: its [official configuration](https://huggingface.co/Qwen/Qwen3-Coder-Next/blob/main/config.json) and [model card](https://huggingface.co/Qwen/Qwen3-Coder-Next) declare that native limit. The inherited two-GPU fitting may lower the effective loaded window; adding a coding-specialized model does not authorize context extrapolation.

Kimi-Linear 48B A3B Instruct uses `1048576` because Moonshot AI's configuration and model card declare a native 1M-token context. Its hybrid linear-attention architecture reduces KV-cache growth, but the wrapper still treats 1M as a requested ceiling and allows llama.cpp to fit a lower effective window while preserving dual-GPU headroom and a `131072` minimum fit target.

Qwen3.8-Flash-Next 125B A6B uses `262144` because the official model card and configuration declare a native context length of 262,144 tokens. Its three-shard wrapper requests that ceiling while allowing llama.cpp to fit a lower effective context if the two-GPU and system-RAM placement cannot safely sustain the full window.

Nex-N2.5-mini uses `262144` because its official configuration declares `max_position_embeddings: 262144`; the configured value remains a ceiling that llama.cpp can reduce to fit available VRAM.

KAT-Coder-V2.5-Dev, Macaron-V1-Tall, Ornith-1.5, and CalibForge use `262144` because each source configuration declares `max_position_embeddings: 262144`. KAT declares no MTP layer; Macaron and Ornith bundle one MTP layer. CalibForge's source configuration declares one, but the selected GGUF contains only the trunk model. These remain requested ceilings subject to runtime fitting.

SERA-32B uses `40960` because the source configuration declares `max_position_embeddings: 40960`. Ai2 describes and evaluates the release at 32K context, so `40960` is the model artifact's declared ceiling rather than a claim that its published agent evaluation used the full window.

Granite 4.2 8B uses `131072` because IBM's authoritative configuration declares `max_position_embeddings: 131072`, and its model card identifies 128K as the native context. The documented 512K mode is a long-context extension, so Qubitz keeps the native ceiling by default.

Tiel-Coder 35B A3B MTP uses `262144` because its Ornith-1.5 base configuration declares `max_position_embeddings: 262144`. The selected GGUF bundles the trained one-layer MTP head; the wrapper activates it with `--spec-type draft-mtp`. The configured context remains a requested ceiling subject to 24 GB runtime fitting.
DeepSeek-V4-Flash-0731 declares `max_position_embeddings: 1048576`. Qubitz requests `262144` rather than the full 1M ceiling because its GGUF weights already require both 24 GB GPUs plus substantial system-RAM overflow; its wrapper retains a `131072` automatic-fit target when feasible.

Ling-3.0-flash uses `262144` because the official model card documents a context-training schedule ending at 256K and the official configuration declares `max_position_embeddings: 262144`. The wrapper keeps `131072` as its automatic-fit minimum when feasible.

Ornith-1.5-122B uses `262144` from its official `text_config.max_position_embeddings` and native-context model card. Automatic fitting may lower the loaded context, with a `131072` fit floor when feasible. The approximately 1M-token YaRN extension described for other serving backends is not enabled by default in this GGUF variant.

Nemotron-3.5-Super-VL-120B-A12B declares `llm_config.max_position_embeddings: 1048576` in NVIDIA's [official configuration](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16/blob/main/config.json). Qubitz keeps `262144` as the default request, rather than preallocating the full native ceiling alongside approximately 112.7 GiB of Q6 weights. `--num-ctx` can request a larger window up to that documented ceiling, subject to actual memory fitting; the fit floor remains `131072` when feasible. This is a hardware policy, not a claim that the model is limited to 256K or that 1M has been load-tested.

