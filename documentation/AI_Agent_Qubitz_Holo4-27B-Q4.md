# AI_Agent_Qubitz_Holo4-27B-Q4.py

## Launcher

`variants/AI_Agent_Qubitz_Holo4-27B-Q4.py` requires project-root `runtime/`. Select it in the GUI or use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Holo4-27B-Q4.py`.

## Model

- GGUF repository: `Hcompany/Holo4-27B-GGUF`
- GGUF file: `q4_k_m.gguf`
- Requested context: `262144` tokens, subject to runtime fitting.
- Text/tool copy only. The source model is multimodal, but no vision projector, image input, or video input is wired here.
- License: CC-BY-NC-4.0 model weights.

Runtime loading and performance are untested in this Qubitz copy.

Copied from the dense `AI_Agent_Qubitz_Qwen3.8-27B_Q4.py` launcher because Holo4 derives from Qwen3.8-27B. The exact GGUF is 16875182592 bytes. Its repo-scoped cache is `.cache/models/Hcompany/Holo4-27B-GGUF/`; the generic filename `q4_k_m.gguf` is preserved exactly. Menu tier: 24 GB.

The [source configuration](https://huggingface.co/Hcompany/Holo4-27B/blob/4de03cfeee1d930b616bfd4817570293f29ad8b9/config.json) declares `text_config.max_position_embeddings: 262144`. Its generation configuration provides temperature `1.0`, top-p `0.95`, top-k `20`; the remaining penalties and min-p are neutral. The [model card](https://huggingface.co/Hcompany/Holo4-27B) identifies the noncommercial weight license; this copy does not expose its visual computer-use capabilities.
