# AI_Agent_Qubitz_Granite-4.2-30B_Q5.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Granite-4.2-30B_Q5.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Granite-4.2-30B_Q5.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for IBM Granite 4.2 30B Q5_K_M.

## Model

- Served alias and GGUF repository: `ibm-granite/granite-4.2-30b-GGUF`
- GGUF file: `granite-4.2-30b-Q5_K_M.gguf`
- GGUF label: `Granite-4.2-30B-Q5_K_M`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Single-RTX-3090 24 GB variant with a configured `131072`-token context ceiling.
- Maximum output defaults to `8096` tokens, retrieval embeddings stay on CUDA, and llama.cpp uses a `q8_0` KV cache.
- Uses the shared project llama.cpp runtime and keeps the variant independently runnable.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.
