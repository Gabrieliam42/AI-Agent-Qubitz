# GLM-5.3-Flash UD-IQ4_XS

Launcher: `variants/AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.py`.
Keep project-root `runtime/` beside `variants/`; launch through the normal root
selector or run this variant directly with the project Python environment.

## Model And Hardware

- Repository: `unsloth/GLM-5.3-Flash-GGUF`, revision
  `a38483c8cd5df544f53d70fb281afe97369d5ab6`.
- Five UD-IQ4_XS shards total 156,822,111,200 bytes, about 146.1 GiB.
- Tier: 72 GB VRAM+, three RTX 3090 GPUs with CUDA0,CUDA1,CUDA2 layer splitting
  and system-RAM overflow. Separate GPUs are not a single unified allocation.
- Requested context 202752; architectural ceiling 1048576; output limit 16384.
  Context size is a ceiling, not a full-window fit guarantee.
- F16 K/V cache, batch 512, microbatch 128, 1536 MiB reserve per GPU,
  1024 MiB prompt-cache budget and four checkpoints.
- Temperature 1.0, top-p 0.95, top-k 0, min-p 0, repeat penalty 1,
  presence penalty 0. Retrieval defaults to exact flat cuVS search; ANN is opt-in.

## Automatic Native Preparation

Online startup prepares missing essential dependencies and a pinned compatible
ik_llama.cpp backend. Source commit
`d741de5074cd424dd3ba7cfc4d9b7649f1eb0463` and the bounded loader patch are
verified before building for Ampere sm_86. The backend must support both the
`glm5-next` GGUF architecture and metadata namespace; it is not stock llama.cpp.

Supported profiles are Torch 2.15.0+cu134 with CUDA runtime 13.4.49/cuBLAS
13.7.0.27, or Torch 2.9.1+cu130 with CUDA runtime 13.0.48/cuBLAS 13.0.0.19.
Unknown or mixed stacks fail explicitly. Compiler toolchains are isolated from
the active environment; existing packages are not silently upgraded or downgraded.
Install uv in the project environment for missing-artifact preparation.

Verified manifests live under `.cache/runtimes/`; owned build outputs live under
`.cache/runtime-builds/`. Valid complete installations are reused. First-time
downloads/builds require network access, disk space and time. Offline mode
requires complete cached artifacts. Partial or mismatched installations are not
treated as ready, and no unrelated backend/model is substituted.

## Memory And Reasoning

CPU weights stay mmap-backed; deferred experts and advisory upload-page release
reduce eager residency, not total model size. Windows physical/commit and WSL
memory counters retain a 4 GiB admission floor and 2 GiB runtime reserve.
Unknown or low-memory state stops/latches the session rather than replaying it.
Adequate physical RAM does not by itself establish Windows commit headroom.

The pinned template always starts thinking. Supported controls are
`reasoning_effort` low/high/max and boolean `clear_thinking`; unsupported
thinking-disable requests fail before dispatch. Omitted effort retains Max;
history clearing removes earlier reasoning, not current generation. Hiding
`Show Reasoning` changes display only. Live tok/s uses backend counters.

## Limits

Small write/readback workflows have completed with cache reuse, but slow initial
prefill and decoding remain. Native cache-advice, tokenizer EOT/EOM and disabled
recurrent-context-shift warnings remain unresolved; they are not silently ignored
as proof of correctness. Long-context stability, numerical fidelity and general
task quality are not established by those small workflows. Keep memory guards
enabled and inspect both completion and behavior-verification status.

Model weights, toolchains and backend binaries are not shipped in this Git tree.
Projectors, imatrix files, MTP and speculative/experimental loaders are not enabled
by this text/tool configuration.
