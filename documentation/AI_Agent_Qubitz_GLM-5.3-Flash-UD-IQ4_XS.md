# GLM-5.3-Flash UD-IQ4_XS

Launcher: `variants/AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.py`.
Keep the project-root `runtime/` package with the launcher.

## Model And Hardware

- Model: `unsloth/GLM-5.3-Flash-GGUF`, revision `a38483c8cd5df544f53d70fb281afe97369d5ab6`.
- Five `UD-IQ4_XS` shards total 156,822,111,200 bytes (about 146.1 GiB).
- Default context: 202752; architectural ceiling: 1048576; output limit: 16384.
- Tier: 72 GB VRAM+, selecting all visible compatible CUDA devices with 1536 MiB
  reserve per GPU and CPU overflow. Separate GPUs do not form unified VRAM.
- F16 K/V cache; batch 512, microbatch 128, prompt-cache budget 1024 MiB and four checkpoints.
- Temperature 1.0, top-p 0.95, top-k 0, min-p 0, repeat penalty 1, presence penalty 0.
- Retrieval defaults to exact flat cuVS search. ANN is optional, not universally better.

Available system RAM, WSL limits, Windows commit and runtime buffers matter in
addition to aggregate VRAM. Paging can be much slower than resident memory.
The context ceiling and tier are not a full-window fit or performance guarantee.

## Native Backend

The default is the pinned compatible ik backend. Online startup prepares missing
supported model/backend artifacts in project-local caches; installed artifacts
are checked before reuse. Native preparation may need substantial disk space
and build time. Offline use requires complete cached artifacts. Incompatible or
unverifiable CUDA/PyTorch profiles fail explicitly rather than altering the stack.

Set `QUBITZ_GLM53_RUNTIME=official` before launch to select the isolated official
llama.cpp v1.0.0 provider, pinned to
`b1b770d90d94cad9e9ea5b993bdbc383a522fb28`. Use `ik` for the default. A provider
change requires a new Qubitz session; caches are separate. A scoped backend
protocol test passed, not a long GUI task or proof of improved speed. This
option does not change other variants' required backends.

The ik path requires deferred expert loading and preserves mmap-backed CPU
weights. Physical/commit and WSL guards retain 4 GiB admission and 2 GiB runtime
reserves. Unknown or low memory latches the session instead of silently
restarting or replaying generation. Do not bypass guards to force a load.

## Progress And Known Limits

Initial loading, prefill and CPU-offloaded decoding can be slow. Live `tok/s`
comes from backend counters; `-- tok/s` means no valid current rate. Completed
summaries distinguish actual aggregate average from an optional typical
token-weighted interval median; neither is total-task throughput.

The GUI's 12,000-character reasoning tail does not truncate the full emitted
reasoning in private conversation records. New transcripts have local
`[HH:MM] Role:` headers and precise event sidecars; historical files are unchanged.

Long repetitive drafting can consume a generation budget without writing a file.
Steering is queued until a generation boundary, not applied immediately.
Creation and behavior verification are separate: inspect actual tool receipts,
saved artifacts and final status rather than treating reasoning as completion.
Short successful workflows do not establish long-task stability or correctness.
Cache-advice, tokenizer EOT/EOM and disabled recurrent context-shift warnings
have been observed; a warning alone establishes neither failure nor health.

See the [user guide](README.md) and [multi-GPU notes](multiGPU_tier_48gb.md).
