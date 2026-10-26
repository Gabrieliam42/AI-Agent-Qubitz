# AI_Agent_Qubitz_GPT-OSS-120B_F16.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_GPT-OSS-120B_F16.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_GPT-OSS-120B_F16.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Qubitz variant for `unsloth/gpt-oss-120b-GGUF`.

- Model file: `gpt-oss-120b-F16.gguf` (65.4 GB); the original GPT-OSS expert weights are native MXFP4, so this GGUF is not a higher-fidelity all-F16 expert checkpoint.
- Requested context: `131072` tokens, matching the [OpenAI configuration](https://huggingface.co/openai/gpt-oss-120b/blob/main/config.json). The effective loaded context can be lower if llama.cpp fitting requires it.
- Placement: llama.cpp `layer` split over `CUDA0,CUDA1`, with a 1536 MiB fit target on each RTX 3090; weights that cannot fit on the GPUs remain in system RAM. Requires both GPUs.
- Uses the shared llama.cpp runtime, no speculative draft model, and no Xorton-specific transactional response cap.
- Static checks do not establish download completion, actual dual-GPU placement, response quality, or tokens per second. Those require a live run after the model is downloaded.

## Qubitz Behavior

- This variant retains isolated GUI, CLI, retrieval, MCP/tool, transaction, and completion-verification behavior through the shared runtime.
- It loads the encrypted harness and uses local llama.cpp GGUF inference; no paid API or hosted inference service is required.

## Current Source-Verified Profile

- Model alias and GGUF repository: `unsloth/gpt-oss-120b-GGUF`.
- GGUF file and label: `gpt-oss-120b-F16.gguf` / `gpt-oss-120b-F16`.
- Requested context ceiling: `131072` tokens; default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- Requires CUDA devices `0` and `1`; uses llama.cpp layer splitting and fitting with a 1536 MiB reserve per GPU and `32768` fit-context setting. Model-weight overflow remains in system RAM. It uses the shared runtime, with no MTP draft and no background prewarm.
- The F16 GGUF is not a higher-fidelity all-F16 expert checkpoint: GPT-OSS expert weights are natively MXFP4.
- This profile block reflects current source metadata; live download, dual-GPU placement, effective context, and generation quality require runtime verification.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

