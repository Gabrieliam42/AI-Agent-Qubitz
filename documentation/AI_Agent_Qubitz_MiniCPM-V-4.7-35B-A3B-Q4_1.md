# AI_Agent_Qubitz_MiniCPM-V-4.7-35B-A3B-Q4_1.py

## Launcher

`variants/AI_Agent_Qubitz_MiniCPM-V-4.7-35B-A3B-Q4_1.py` requires project-root `qubitz_runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_MiniCPM-V-4.7-35B-A3B-Q4_1.py`.

## Model

- GGUF repository: `openbmb/MiniCPM-V-4.7-35B-A3B-gguf`
- GGUF file: `MiniCPM-V-4_7-35B-A3B-Q4_1.gguf`
- Requested context: `262144` tokens, subject to runtime fitting.
- Text/tool copy only. The source model is multimodal, but no vision projector, image input, or video input is wired here.
- License: Apache-2.0.

Runtime loading and performance are untested in this Qubitz copy.

Copied from `AI_Agent_Qubitz_KAT-Coder-V2.5-Dev-35B-A3B-Q4.py`: its Qwen3.6 hybrid MoE language backbone is the closest non-MTP base. The exact GGUF is 21847643040 bytes. Cache: `.cache/models/openbmb/MiniCPM-V-4.7-35B-A3B-gguf/`. Menu tier: 24 GB. MTP, LES and DSpark are not enabled by this text/tool copy.

The [official configuration](https://huggingface.co/openbmb/MiniCPM-V-4.7-35B-A3B/blob/f0e255279f110702b24e4cb67eddb137ee00ec30/config.json) declares a 262144-token language context. Sampling follows its shipped [generation configuration](https://huggingface.co/openbmb/MiniCPM-V-4.7-35B-A3B/blob/f0e255279f110702b24e4cb67eddb137ee00ec30/generation_config.json): temperature `1.0`, top-p `0.95`, top-k `20`, neutral penalties/min-p. The [model card](https://huggingface.co/openbmb/MiniCPM-V-4.7-35B-A3B) separately recommends `0.7/0.95/100` with repeat penalty `1.03` for instruct mode; this copy keeps native template mode selection instead of imposing that mode. Template/tool compatibility requires a later guarded runtime check.
