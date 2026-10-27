# AI_Agent_Qubitz_LFM2.5-24B-A2B-Q5.py

## Launcher

`variants/AI_Agent_Qubitz_LFM2.5-24B-A2B-Q5.py` requires project-root `runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_LFM2.5-24B-A2B-Q5.py`.

## Model

- GGUF repository: `LiquidAI/LFM2.5-24B-A2B-GGUF`
- GGUF file: `LFM2.5-24B-A2B-Q5_K_M.gguf`
- Requested context: `131072` tokens, subject to runtime fitting.
- Text/tool launcher; the pinned llama.cpp source contains LFM2-MoE and Pythonic tool-parser implementations, with this GGUF's live behavior still untested.
- License: LFM1.0 custom model-weight license.

Copied from `AI_Agent_Qubitz_Nemotron-3.5-Lightning-30B-A3B-IQ4.py`: its single-GPU hybrid MoE launcher is the closest existing structural base; LFM uses a different architecture. The exact GGUF is 17029602592 bytes. Its cache is `.cache/models/LiquidAI/LFM2.5-24B-A2B-GGUF/` and its menu tier is 24 GB.

The [official model card](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B) and [configuration](https://huggingface.co/LiquidAI/LFM2.5-24B-A2B/blob/1769516ac588c4c558089c94f27d96b0dc67b183/config.json) declare a 131072-token context. Sampling follows its generation configuration: temperature `0.1`, top-k `50`, repeat penalty `1.05`; top-p `1.0`, min-p `0`, and presence penalty `0` leave unprescribed filters neutral. LFM always emits reasoning using its own template.

Runtime loading and performance are untested in this Qubitz copy.
