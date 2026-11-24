# GLM-5.3-Flash UD-IQ4_XS

Launcher: `variants/AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.py`.
This launcher requires project-root `runtime/` alongside the `variants/` directory.

Uses the current shared Qubitz runtime and its variant-specific native backend.

## Current Startup And Verification

The default backend remains the pinned compatible ik runtime. Before launch,
`QUBITZ_GLM53_RUNTIME=official` opts into the separately cached, pinned official
llama.cpp v1.0.0 provider; `ik` selects the default. Changing provider requires a
new Qubitz session. Scoped backend protocol checks passed, but do not establish
better speed, long-task stability or interchangeability with other variants.

New conversation records use local `[HH:MM] Role:` headers and retain full
emitted reasoning independently of the 12,000-character live tail. Final token
rates distinguish typical interval-median decoding from actual aggregate average.
Repeated drafting without a tool call or file write remains a known limitation;
steering is queued until a generation boundary, not applied immediately.

Online startup prepares missing essential dependencies and a pinned compatible
native backend automatically. Complete installed artifacts are reused after
manifest/hash validation. Downloads/builds stay under the project root; unknown
or mixed stack profiles fail instead of changing the active environment. Offline
startup requires complete local artifacts. First-time native preparation can
require substantial disk space and several minutes.

The shared GUI shows live backend-counted `tok/s` next to `Show Reasoning`.
A controlled file-write/readback workflow completed on November 14 at 2.79
weighted decode tok/s and 23:10 total elapsed time, with three requests and
verified prompt-cache reuse. This is narrow workflow evidence, not a benchmark,
long-context qualification or proof of improvement over a different task.
Slow initial prefill/decoding and native cache-advice, tokenizer EOT/EOM and
disabled recurrent-context-shift warnings remain.

## Configuration

- Model: `unsloth/GLM-5.3-Flash-GGUF`, pinned revision `a38483c8cd5df544f53d70fb281afe97369d5ab6`.
- Five `UD-IQ4_XS` shards, totaling 156,822,111,200 bytes (about 146.1 GiB).
- Default context: 202752; architectural ceiling: 1048576; output limit: 16384.
- Tier: `72 GB VRAM+`; automatic visible CUDA GPU selection (one or more), layer splitting,
  CPU overflow, 1536 MiB fit reserve per GPU. VRAM remains separate allocations,
  not one unified buffer; this label does not imply the model fits entirely in VRAM.
- F16 K/V caches; batch 512; microbatch 128; prompt-cache budget 1024 MiB; four checkpoints.
- Sampling: temperature 1.0, top-p 0.95, top-k 0, min-p 0.0, repeat penalty 1.0, presence penalty 0.0.
- Retrieval remains `auto` exact flat cuVS search. ANN is opt-in, not an automatic
  improvement; choose it only after measuring retrieval latency and recall.
- Isolated WSL runtimes are selected from the exact stack profiles below; the
  executable and three shared libraries each have profile-specific SHA-256 pins.
- Startup allowance: 7200 seconds; response read timeout: 1800 seconds.
- CPU weights remain mmap-backed via the child-only `GGML_CUDA_NO_PINNED_WEIGHTS=1`.
- `--defer-experts` avoids eager whole-shard residency before GPU uploads; CPU
  expert pages are faulted in on demand. The pinned backend must advertise this
  capability. Flags that disable mmap or force eager/pinned residency are rejected.
- Windows physical/commit and WSL memory counters are required: 4 GiB admission
  floor, 2 GiB runtime reserve. An unknown/low-memory stop latches the session;
  it does not silently relaunch or replay generation.

## Dependencies And Limits

The Python launcher does not contain native backend binaries. Its normal online
preparation now downloads/builds the approved pinned runtime when necessary;
offline, incompatible or unverifiable installations fail explicitly, with no
fallback to a different model or an unrelated backend profile.
The backend must support both the GGUF architecture name `glm5-next` and its
metadata namespace, not merely rename it to `glm5next`.

Historical IQ3 tests completed short arithmetic and calculator generation. One
48-token calculator response decoded at about 2.47 tokens/second. Longer runs
hit Windows commit pressure; the last normal October 20 launch stopped during
loading at 1.92 GiB free physical RAM. These are historical results, not current
performance or stability qualification. The context ceiling is not a fit guarantee.

EXL3, expert-cache/native-loader experiments, DFlash2, TriAttention and older
IQ4-only flags are not enabled by this restoration. The historical experimental
loader repairs were not integrated into the normal variant and are not assumed
to fix its peak-memory problem.

Recovery evidence and source-line references are under
`.cache/restore_glm53_20261111_01/`; historical run outputs remain immutable.

Historical IQ3 restoration verification on 2026-11-12: scoped Ruff, 22 GLM acquisition tests,
normal selected-variant CLI help, the real Windows/WSL memory-counter query and
the 41-public-variant model-free release gate passed. Earlier broader partial
test runs and their corrected failures remain recorded; this is not a claim of
a clean full-repository test run or live model acceptance. No weights or native
backend were downloaded, installed or started during restoration.

The later IQ4 quantization, filename and three-GPU configuration are a new
baseline; those historical IQ3 gate and inference results do not qualify it.
On 2026-11-12 the five local IQ4 shard files matched the declared sizes and had
GGUF headers; this is not full integrity or load verification. The required
isolated backend executable was absent. The 877 focused model-free regressions
passed, including lifecycle, acquisition fixtures, three-GPU launch contracts,
retrieval status and four-tier Tk/browser catalogs. Live compatibility,
memory fit and performance remain unverified.

## Native Runtime Profiles

Normal automatic preparation uses the pinned source and loader patch in
`runtime/native_runtime.py` and `runtime/glm53_loader.patch`. Installation
manifests live under `.cache/runtimes/glm53-d741de50-8a5456ed952c-<profile>-sm86/`;
owned build outputs live under `.cache/runtime-builds/`. CUDA 13.0 and 13.4
toolchains are isolated from the active environment. Required backend files,
source/patch hashes, validation and build identity are retained in the manifest.
Process-local WSL driver/PTX-JIT binding runs before CUDA imports; it does not
replace system driver libraries or require a WSL restart.

The following explicitly restored candidate paths are historical preparation
evidence, not the normal automatic install destinations:

Both builds use the retained source snapshot for
[`d741de5074cd424dd3ba7cfc4d9b7649f1eb0463`](https://github.com/ikawrakow/ik_llama.cpp/commit/d741de5074cd424dd3ba7cfc4d9b7649f1eb0463),
targeting Ampere `sm_86`. This replaces reliance on the misleading old `401a09d2`
directory label. Selection reads installed package metadata without importing Torch:

- Torch `2.15.0+cu134`, CUDA runtime `13.4.49`, cuBLAS `13.7.0.27`:
  `.cache/ik_llama.cpp-glm53-d741de50-bounded-cu134-20261113-02/build/bin/llama-server`.
- Torch `2.9.1+cu130`, CUDA runtime `13.0.48`, cuBLAS `13.0.0.19`:
  `.cache/ik_llama.cpp-glm53-d741de50-bounded-cu130-20261113-02/build/bin/llama-server`.

These isolated candidates add the bounded-upload loader patch described below.
The original restored builds remain unchanged. Each candidate recompiles one
loader object using its original CMake recipe and relinks against hash-bound
unchanged objects/libraries; the launcher pins all four resulting runtime files.

Unknown or mixed combinations fail preflight. Changing stacks after a build has
been selected requires restarting Qubitz; it does not silently switch a running
session. The CUDA 13.0 compiler and libraries are isolated under
`.cache/toolchains/cuda130/.venv`; they do not downgrade the active `.venv`.
Neither runtime directory nor its dependencies should be moved or removed.

On 2026-11-13 both builds passed native dependency resolution, required CLI flag
checks, both GLM metadata-namespace/tensor-map fixtures and three-RTX-3090
enumeration. The active `.venv` was restored from all 120 entries in
`requirements.txt`; dependency checks, native imports and tiny tensor operations
on all three GPUs passed. The older full Python stack was not installed over
the current one, and full model loading/inference was not tested. These results
do not establish model quality, throughput or long-context memory fit.

Build and verification receipts are under
`.cache/glm53_backend_restore_20261113_01/`; earlier failures and the old
unsupported backend are retained, not rewritten as successful runs. Both native
builds emitted the same upstream GCC `-Wstringop-overread` warning at
`model.splits = { 1.0f }`; no native source change or warning suppression was applied.

## Startup Memory Policy

The first IQ4_XS live attempt on 2026-11-13 recognized the architecture and all
five shards but stopped before generation: Windows commit headroom fell to
1,961,607,168 bytes, below the unchanged 2 GiB runtime reserve. The owned server
was reaped with return code -15, port 8001 became idle and host memory recovered.
Evidence is preserved under `.cache/glm53_live_observation_20261113_01/`.

The original backend's eager `MAP_POPULATE` mapping path was the leading
startup-peak mechanism. Requiring `--defer-experts` reduced initial residency in
64 MiB fixtures, but the first approved live test still stopped at the commit
reserve before generation: uploaded source pages accumulated during loading.

The selected candidates additionally release full, page-aligned source pages
after synchronous GPU upload, using `MADV_DONTNEED` plus `POSIX_FADV_DONTNEED`.
CPU weights and boundary pages remain available, and weights are not modified.
This is an advisory cache policy, not a guaranteed RAM cap. Eight original/candidate
128 MiB upload cases across both CUDA builds verified numerical copies on all
three GPUs, CPU boundary retention and successful source-page refaults. Eight
metadata/mapping cases, 170 focused regressions and all 41 public source contracts
also passed. Source, exact build commands and test receipts are retained under
`.cache/glm53_bounded_loading_20261113_01/`.

The hardware review found no remaining one-GPU allocation cap: GPU indices
0,1,2, dynamic per-device fitting, the 72 GB VRAM+ tier and CPU overflow already
describe the three-card system. Context 202752, F16 KV, batch 512, microbatch 128,
1536 MiB reserve per GPU, the 1024 MiB prompt-cache budget and four checkpoints
were retained; increasing them is not a remedy for pre-offload host-memory peaks.
Runtime contract `dual_gpu` is the existing multi-device capability schema field,
not a limit of two GPUs. No model quantization, sampling or timeout was changed.

The second approved live test reached readiness in 740.2 seconds and returned
`READY` from one short diagnostic request, but Windows commit headroom fell to
1,541,545,984 bytes and triggered the unchanged memory guard during that request.
About 63 GiB of physical RAM was still free. Native actual wait exit was 0 after
cleanup, with the process absent and port 8001 idle. The original helper's
response-only `passed` label is preserved; the independent disposition is HELD,
not stable inference acceptance. The helper now checks memory-stop and cleanup
axes separately; seven model-free reporting regressions passed.

The live logs retain three `MADV_DONTNEED` / `Cannot allocate memory` warnings
after tensor loading, EOT/EOM tokenizer warnings and disabled recurrent context
shift. Their cause/impact is not established by the correct diagnostic response.
Neither full GUI/tool workflows nor long-context stability or throughput is qualified.

With explicit approval, Windows `C:\pagefile.sys` was configured to 81920 MiB
initial / 262144 MiB maximum, replacing automatic sizing. Configuration readback
and elevated process exit 0 were verified; the active pagefile remained 8192 MiB
and commit limit 145,803,485,184 bytes. A user-controlled Windows restart is needed
to apply the initial capacity. No restart, shutdown or further inference was
dispatched. After restart, verify actual capacity before another separately
approved controlled test. The larger pagefile provides commit backing, not more
physical RAM or a guarantee of acceptable speed. Backup/configuration receipts
are under `.cache/glm53_bounded_loading_20261113_01/pagefile-adjustment/`.

After the user restarted Windows on 2026-11-13, the active pagefile was verified
at 80 GiB and the commit limit at 223,112,896,512 bytes (about 207.8 GiB).
One newly approved diagnostic reached readiness in 432.1 seconds, retained at
least 81.9 GiB sampled commit headroom and triggered no memory guard. The overall
test nevertheless failed: all 32 completion tokens were reasoning, final content
was empty and `finish_reason` was `length`, despite `enable_thinking: false`.
The diagnostic's no-thinking assumption was subsequently disproved by reading
and rendering the pinned GGUF template; no retry or automatic token-limit
increase was performed. Native actual wait exit was 0,
with process absence and port 8001 idle verified. All 49 telemetry samples had
successful GPU queries, no observer errors and maximum GPU temperature 36 C.
The same native warnings remain; full-agent stability is still not qualified.
Evidence: `.cache/glm53_bounded_loading_20261113_01/live-verification-post-restart/`.

## Template Reasoning Controls

The pinned template (SHA-256
`a4fddbbf0b432101a296c17094f8bc5a2b0d30713b5b5cd92f86be78511aa724`)
does not consume `enable_thinking`. Its generation prompt always ends in
`<think>`, and omitted or unsupported effort renders as Max. The supported
controls are `chat_template_kwargs.reasoning_effort` (`low`, `high`, `max`) and
`clear_thinking` (boolean), as described by the
[official model card](https://huggingface.co/zai-org/GLM-5.3-Flash#note).
`clear_thinking=true` removes earlier reasoning from chat history, not new reasoning.

Shared request construction now recognizes this literal template contract rather
than the model name. Supported explicit effort is routed through template kwargs;
unsupported modes, conflicting controls and a requested thinking disable fail
before dispatch. Normal chat, streaming, constrained file generation and JSON
requests retain required thinking and their existing token budgets. Omitted
effort still uses Max; history clearing defaults to true unless explicitly set.
Other templates retain their previous request behavior.

The local native diagnostic selects Low only for its separately approved request,
requires an explicit diagnostic token budget before server startup, and records
the observed template hash and capability policy. No new live request was
authorized or performed by this repair; the old 32-token response remains failed.
Model-free repair evidence is under `.cache/template_reasoning_repairs_20261113_01/`.
This change does not address the separate native precision/cache-advice warnings,
enable MTP, acquire projector/imatrix files, or qualify full-agent stability.

Repair verification: scoped Python and composed GLM/Qwen Ruff passed; 758 focused
model-free regressions and 89 boundary rechecks passed with no skipped cases.
All 41 public syntax, runtime, shared-template and profile contracts passed after
refreshing only the two changed shared-function snapshots, their enclosing section
and corresponding hashes. Earlier validation failures and interrupted checks are
retained separately. The variant/profile settings are unchanged.

One subsequent explicitly approved native smoke used Low effort and a 256-token
diagnostic cap, returning `READY` with `finish_reason=stop` in three completion
tokens. Startup took 452.6 seconds; total test and cleanup took 509.5 seconds.
No memory guard triggered. The 47 telemetry samples retained at least 88.0 GiB
of Windows commit headroom and 74.7 GiB free physical RAM, with maximum GPU
temperature 33.4 C and no observation/GPU-query failures. The native launch/exit
identities matched, native actual wait exit was 0, and fresh process/port checks
confirmed cleanup. Production defaults were unchanged and no request was retried.

The server-reported template hash
`d332fd5766d800fb2bb288c8befe075d69eeccbdc73181ec3a6254ea7b1798e7`
matches the same cached template after trailing-whitespace trimming. The native
cache-advice/tokenizer/context-shift warnings remain recorded; this bounded
success does not qualify full-agent/GUI/tool workflows, long-context stability,
numerical fidelity or comparative performance. Evidence:
`.cache/template_reasoning_repairs_20261113_01/live-verification-low256/`.
