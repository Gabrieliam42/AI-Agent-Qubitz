# AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Granite-4.2-8B.i1-Q6.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [runtime layout](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for IBM Granite 4.2 8B imatrix Q6_K.

## Model

- Served alias and GGUF repository: `mradermacher/granite-4.2-8b-i1-GGUF`
- GGUF file: `granite-4.2-8b.i1-Q6_K.gguf`
- GGUF label: `granite-4.2-8b.i1-Q6_K`
- Architecture: dense Granite 4.2 reasoning model with tool-calling support
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Standard single-RTX-3090 24 GB variant with a configured `131072`-token native context ceiling.
- Maximum output defaults to `8096` tokens, retrieval embeddings stay on CUDA, and llama.cpp uses a `q8_0` KV cache.
- Uses the shared project llama.cpp runtime and remains independently runnable.

## Sources

- IBM model card: https://huggingface.co/ibm-granite/granite-4.2-8b
- IBM model configuration: https://huggingface.co/ibm-granite/granite-4.2-8b/blob/main/config.json
- Selected GGUF: https://huggingface.co/mradermacher/granite-4.2-8b-i1-GGUF/blob/main/granite-4.2-8b.i1-Q6_K.gguf

## Current Source-Verified Profile

- Model alias and GGUF repository: `mradermacher/granite-4.2-8b-i1-GGUF`.
- GGUF file and label: `granite-4.2-8b.i1-Q6_K.gguf` / `granite-4.2-8b.i1-Q6_K`.
- Requested context ceiling: `131072` tokens; default maximum output: `8096` tokens.
- Uses the shared llama.cpp runtime and remains independently runnable.
- Documentation refreshed 2026-09-19 against the then-current standalone script and declarative profile.
- Smoke on 2026-09-12 passed: question `1.79 s`, task `1.78 s`, with no timeout, thermal abort, or behavior issue.
