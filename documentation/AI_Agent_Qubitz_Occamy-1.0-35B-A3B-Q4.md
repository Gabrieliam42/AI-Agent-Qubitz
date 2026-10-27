# AI_Agent_Qubitz_Occamy-1.0-35B-A3B-Q4.py

## Launcher

`variants/AI_Agent_Qubitz_Occamy-1.0-35B-A3B-Q4.py` requires project-root `runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Occamy-1.0-35B-A3B-Q4.py`.

## Model

- GGUF repository: `bartowski/occamy-ai_occamy-1.0-GGUF`
- GGUF file: `occamy-ai_occamy-1.0-Q4_K_L.gguf`
- Requested context: `262144` tokens, subject to runtime fitting; the model card separately documents a 131072-token SFT context.
- Text/tool copy only. No vision projector, image input, or video input is wired here.
- License: Apache-2.0.

Runtime loading and performance are untested in this Qubitz copy.

Copied from `AI_Agent_Qubitz_KAT-Coder-V2.5-Dev-35B-A3B-Q4.py`: both use the Qwen3.6 hybrid MoE language backbone without adding draft-MTP. The exact GGUF is 21768894784 bytes. Cache: `.cache/models/bartowski/occamy-ai_occamy-1.0-GGUF/`. Menu tier: 24 GB, with runtime fitting/offload still required when context or overhead exceeds available VRAM.

The source repository now resolves to [Accio-Lab/occamy-1.0](https://huggingface.co/Accio-Lab/occamy-1.0); the requested Bartowski GGUF repository remains unchanged. Its [configuration](https://huggingface.co/Accio-Lab/occamy-1.0/blob/72ca96031d8d11650882b5fae8dad88412a76155/config.json) declares a 262144-token ceiling. Sampling follows the source usage example: temperature `1.0`, top-p `0.95`, top-k `20`, presence penalty `1.5`, repeat penalty `1.0`, min-p `0`. Full-window quality and fit are unverified.
