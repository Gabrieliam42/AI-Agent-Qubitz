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

The 2026-10-26 b11887 standard-head comparison verified loading, native tool use, cancellation, cleanup, and active speculative acceptance. Its two matched capped decoding probes were about 39% slower with MTP; a shorter GUI run used different model output/tool steps and is not directly comparable. Evidence: `.cache/mtp_qwen38_20261026_01/comparison_review.json`.

Two later headless comparisons each ran two capped 512-token prompts per MTP head. Pooled decoding rates (tokens/s) were standard-Q4 8.24/8.13, shared-Q4 8.33/8.78, and shared-Q8 9.92/9.14 across the two rounds. The shared-Q8 head ranked first in both, but its lead over shared-Q4 changed substantially. Both reviews reported clean infrastructure: `.cache/mtp_head_comparison_20261026_01/comparison_review.json` and `.cache/mtp_head_comparison_repeat_20261026_02/comparison_review.json`. Fixed order, only two prompts per head, no fresh MTP-off arm, and no agent-task or quality comparison mean these data do not justify a default change or predict GUI task latency. MTP is an optional decoding-speed experiment, not a way to improve model capability.

## Qubitz Behavior

- Remains independently runnable with its own GUI, CLI, retrieval, MCP/tool, transaction, completion-verification, routing, and thermal behavior.
- Uses Qwen3.8-family route-aware sampling, including the model-card thinking profile for agentic work and non-thinking profile for simple responses.
- Uses local llama.cpp GGUF inference only; no required paid service or hosted inference account.

## Verification

- Model identity, profile, shard acquisition logic, aggregate-size planning, Python syntax, Ruff, shared-wrapper parity, and release checks are verified statically.
- The three-shard model and MTP heads loaded in the cited live comparisons at the requested `262144` context; this does not establish all agent-task outcomes or performance under other prompts.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `unsloth/Qwen3.8-Flash-Next-GGUF`
- GGUF repository: `unsloth/Qwen3.8-Flash-Next-GGUF`
- GGUF file: `Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf`
- GGUF label: `Qwen3.8-Flash-Next-125B-A6B-UD-IQ4_XS`
- Requested context ceiling: `262144` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses its own isolated llama.cpp runtime selected by the script.
- This is a three-shard dual-GPU/RAM placement variant; its shard and fit logic remain variant-specific.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

