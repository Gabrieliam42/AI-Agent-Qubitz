# AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py` and requires project-root `runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for Qwen3.8-Flash-Next 125B A6B UD-IQ4_XS.

## Model

- Served alias and GGUF repo: `unsloth/Qwen3.8-Flash-Next-GGUF`
- GGUF directory: `UD-IQ4_XS`
- Launch shard: `Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf`
- Required siblings: shards `00002-of-00003` and `00003-of-00003`
- GGUF label: `Qwen3.8-Flash-Next-125B-A6B-UD-IQ4_XS`
- Embeddings: `BAAI/bge-code-v1`

## Runtime

- Requires two 24 GB CUDA GPUs and system RAM for the model-weight overflow.
- Requests the model's native `262144`-token context; llama.cpp may fit a lower effective context when required by available memory.
- Downloads and verifies all three GGUF shards, then launches llama.cpp with shard 1 so its standard split-file loader discovers the siblings.
- Uses `CUDA0,CUDA1`, `layer` split, automatic fit, `q4_0` KV cache, a 131072-token fit floor, and a 1536 MiB safety margin on each GPU.
- Computes placement against the aggregate size of all shards, not the small metadata-heavy first shard alone.
- Keeps as much of the model as safely fits in aggregate VRAM and leaves unavoidable overflow in system RAM.
- Uses isolated llama.cpp `b10809`, the binary release associated with v0.4.0 and required for Qwen3.8 Flash Next support.

### Performance Experiments

Production defaults stay unchanged unless one of these Qwen3.8-specific environment variables is set:

- `QUBITZ_QWEN38_FLASH_NEXT_THREADS`: generation CPU threads, from 1 through the detected logical CPU count.
- `QUBITZ_QWEN38_FLASH_NEXT_THREADS_BATCH`: prompt-processing CPU threads, with the same bounds.
- `QUBITZ_QWEN38_FLASH_NEXT_CACHE_REUSE`: prompt-cache reuse threshold from 0 through 262144 tokens.
- `QUBITZ_QWEN38_FLASH_NEXT_N_CPU_MOE`: number of initial MoE layers whose expert weights remain on CPU, from 0 through 48.
- `QUBITZ_QWEN38_FLASH_NEXT_KV_CACHE_TYPE`: `q4_0` or `q8_0`; the default remains `q4_0`.
- `QUBITZ_QWEN38_FLASH_NEXT_BATCH_SIZE`: logical batch size from 128 through 4096.
- `QUBITZ_QWEN38_FLASH_NEXT_UBATCH_SIZE`: physical microbatch size from 32 through 2048 and no greater than the effective batch size.

Invalid values or options unsupported by the selected llama.cpp runtime stop before model launch. The effective launch command is recorded in `.cache/llama.cpp/llama-server.log`, and the calculated GPU/CPU placement plan retains the selected tuning values for diagnostics.

### Optional MTP Experiments

The default remains b10809 with MTP off. These controls affect only this variant:

The normal Tk GUI also shows an MTP Off/On control beside Temperature. Off retains b10809;
On selects the cached shared-Q4 head and pinned `6fcaa16f` fork. Switching is available
only after the current task is idle and owned backend cleanup is verified; the next task
starts a fresh backend. A missing head or runtime fails closed without downloading it.
The GUI switch does not select the standard-Q4 or shared-Q8 experimental heads.

- `QUBITZ_QWEN38_FLASH_NEXT_MTP=off|standard|shared-q8|shared-q4`: `standard` selects the pinned standard Q4_K_M head and b11887; shared modes select the pinned Unsloth fork `6fcaa16f` and the corresponding local shared head. `shared` aliases `shared-q8`.
- `QUBITZ_QWEN38_FLASH_NEXT_RUNTIME_TAG=b10809|b11887|unsloth-mtp-6fcaa16f` explicitly selects an isolated runtime, including for an MTP-off control arm.
- `QUBITZ_QWEN38_FLASH_NEXT_MTP_DRAFT_TOKENS=1..5`: defaults to 3 for the standard head and 2 for shared heads.
- `QUBITZ_QWEN38_FLASH_NEXT_MTP_RESERVE_MIB=0..8192` adds fit headroom per GPU. Shared heads require at least 4096 MiB because this fork cannot account for them in its automatic extra-model fit. Use the same reserve for both arms of a controlled comparison.

Heads must already exist under `.cache/models/unsloth/Qwen3.8-Flash-Next-GGUF/MTP/`. Launch verifies pinned head bytes, base metadata identity, complete base shard sizes, and runtime version. Shared modes additionally verify the locally built runtime against `qubitz-build.json`; missing files never trigger an automatic fork/head download. The main model quantization, sampling, context ceiling, and harness remain unchanged.

## Qubitz Behavior

- Remains independently runnable with its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Uses Qwen3.8-family route-aware sampling, including the model-card thinking profile for agentic work and non-thinking profile for simple responses.
- Uses local llama.cpp GGUF inference only; no required paid service or hosted inference account.
- Displays live backend-counted `tok/s` beside `Show Reasoning`; unavailable
  prompt-processing counters are not reported as decoding speed. Final token
  speed excludes startup, retrieval, prefill and tool execution.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.
