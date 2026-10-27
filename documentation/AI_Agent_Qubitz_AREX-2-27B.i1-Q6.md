# AI_Agent_Qubitz_AREX-2-27B.i1-Q6.py

## Launcher

`variants/AI_Agent_Qubitz_AREX-2-27B.i1-Q6.py` requires project-root `runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_AREX-2-27B.i1-Q6.py`.

## Model

- GGUF repository: `mradermacher/AREX-2-i1-GGUF`
- GGUF file: `AREX-2.i1-Q6_K.gguf`
- Requested context: `262144` tokens, subject to runtime fitting.
- Text/tool copy only. The source model is multimodal, but no vision projector, image input, or video input is wired here.
- License: Apache-2.0.

Runtime loading and performance are untested in this Qubitz copy.

Copied from the dense `AI_Agent_Qubitz_Qwen3.8-27B_Q4.py` launcher because [AREX-2](https://huggingface.co/BAAI/AREX-2) derives from that model family. The exact GGUF is 22082530720 bytes. Cache: `.cache/models/mradermacher/AREX-2-i1-GGUF/`. Menu tier: 24 GB, with context and runtime overhead subject to fitting.

The [source configuration](https://huggingface.co/BAAI/AREX-2/blob/d4e3502f92d9e889031c2d04e387ab2eb3520268/config.json) declares `text_config.max_position_embeddings: 262144`. No AREX-specific sampling recommendation was found in the inspected source card; this copy uses its base family's general settings (temperature `1.0`, top-p `0.95`, top-k `20`, neutral penalties/min-p), rather than claiming these are AREX publisher recommendations.
