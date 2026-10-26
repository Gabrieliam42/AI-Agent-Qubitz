# AI_Agent_Qubitz_Mellum2.1-12B-A2.5B-Thinking-Q6.py

## Launcher

`variants/AI_Agent_Qubitz_Mellum2.1-12B-A2.5B-Thinking-Q6.py` requires project-root `qubitz_runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Mellum2.1-12B-A2.5B-Thinking-Q6.py`.

## Model

- GGUF repository: `JetBrains/Mellum2.1-12B-A2.5B-Thinking-GGUF`
- GGUF file: `Mellum2.1-12B-A2.5B-Thinking-Q6_K.gguf`
- Requested context: `131072` tokens, subject to runtime fitting.
- Text/tool copy; no separate vision or projector handling is added.
- License: Apache-2.0.

Runtime loading and performance are untested in this Qubitz copy.

Copied from `AI_Agent_Qubitz_AgenticQwen-30B-A3B.i1-Q4.py`: the standard attention MoE launcher avoids adding MTP or an unrelated runtime policy. Mellum has a distinct MoE architecture with sliding attention. The exact GGUF is 10879834400 bytes. Its cache is `.cache/models/JetBrains/Mellum2.1-12B-A2.5B-Thinking-GGUF/` and its menu tier is 24 GB; weight size alone does not establish 12 GB fit with context and headroom.

The [official configuration](https://huggingface.co/JetBrains/Mellum2.1-12B-A2.5B-Thinking/blob/92ddae9fc7665e9f801d141d2e5a6b2caf2460c4/config.json) declares `131072`. Sampling uses the [GGUF quickstart](https://huggingface.co/JetBrains/Mellum2.1-12B-A2.5B-Thinking-GGUF): temperature `0.6`, top-p `0.95`, top-k `20`, neutral penalties and min-p `0`. The source card separately reports agentic benchmarks at temperature `1.0`; this launcher follows the usage example. The pinned backend source lists Mellum support, which does not establish loading of this artifact.
