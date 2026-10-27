# Qubitz Documentation And User Guide

## Purpose

Qubitz is a WSL2 AI coding agent built around local GGUF inference, with 39 user-facing Python launchers for different models and VRAM budgets. Universal GUI, CLI, retrieval, MCP/tool, transaction, and completion-verification code lives in `runtime/`; each launcher retains its variant-specific source and an isolated runtime namespace.

The project supports private local inference without requiring an API, cloud account, subscription, or paid hosted inference service. Optional online research and configured MCP providers are available when enabled and relevant to the task. Windows-hosted and WSL-hosted workspaces are supported through the existing WSL-to-Windows execution paths.

## Current Structure

- `AI Agent Qubitz.py`: normal Tk GUI launcher; loads only the selected variant.
- `variants/AI_Agent_Qubitz_*.py`: 39 user-facing release launchers; three developer-only local launchers are excluded from this release.
- `runtime/`: required shared runtime source, loader, and development/export tools.
- `documentation/`: public model/context catalog and 39 user-facing model guides.
- `HARNESS.enc`: runtime harness artifact loaded by variants.
- `runtime/policy_bundle.json` and `_policy_runtime` binaries: version-bound native harness loader; no user key file is required.
- `.cache/`: project-local model, llama.cpp, retrieval, and evaluation caches.
- `backup/`: local project backups and recovery copies; not shipped.

For a conservative user runtime bundle, retain the normal root launcher (unless launching
one selected variant directly), the selected public launcher, the complete
`runtime/` package including its `.py.in` source, native loader binaries and bundle manifest,
and `HARNESS.enc`. Use a CPython 3.12 x86-64 environment and applicable dependencies. Users do not
need a harness key, Cython, or a native compiler. Plaintext `HARNESS.txt`, the authoring key and
private native build sources are not shipped. This packaging discourages casual inspection;
it does not prevent extraction of the harness on a user-controlled machine. Model weights, downloaded runtimes, caches,
evaluation artifacts, maintainer-only builder tooling, and the optional browser frontend are not shipped
in this public Tk/CLI/MCP runtime tree. This is a conservative file set, not a tested pruned-package export.

## Environment

- Primary runtime: WSL2 project environment `.venv/bin/python`.
- Default Python target: Python 3.12.
- Target hardware: one RTX 3090 24 GB for standard variants; DeepSeek-V4-Flash, GPT-OSS-120B, Hypernova, Kimi-Linear, Ling-3.0-flash, Llama-3.3-Nemotron-Super, Nemotron-3.5-Super-VL, Ornith-1.5-122B, Qwen3-Coder-Next, and Qwen3.8-Flash-Next use two RTX 3090 GPUs with 48 GB aggregate VRAM. The target host also has an Intel i9-14900KF and 128 GB RAM.
- GPU inference uses the project-managed llama.cpp GGUF runtime and CUDA retrieval/embedding stack where supported.
- North Mini Code, DeepSeek-V4-Flash, Ling-3.0-flash, Nemotron-3.5-Super-VL, Ornith-1.5-122B, and Qwen3.8-Flash-Next use isolated compatible llama.cpp runtime channels; other variants use the shared project runtime channel unless their profile requires otherwise.
- Ling's AD GGUF requires the isolated Atomic TurboQuant `b10269-1.6.0` runtime. Online startup downloads a missing Windows CUDA 12 bundle or, when Windows execution is unavailable under WSL, the same fork's native Linux CUDA 12.4 bundle; cached runtimes are reused. It does not substitute stock llama.cpp or automatically enable TurboQuant KV compression.
- Known Ling limitation: the current runtime rejected a chunked file-content grammar with HTTP 400 (`number of repetitions exceeds sane defaults`). Successful model loading does not yet establish file-creation compatibility.
- Hypernova, Kimi-Linear, Llama-3.3-Nemotron-Super, and Qwen3-Coder-Next use explicit llama.cpp layer placement across `CUDA0` and `CUDA1` with a 1536 MiB safety margin on each GPU. DeepSeek-V4-Flash, GPT-OSS-120B, Ling-3.0-flash, Nemotron-3.5-Super-VL, Ornith-1.5-122B, and Qwen3.8-Flash-Next use automatic dual-GPU layer fitting with the same per-GPU margin and retain unavoidable model-weight overflow in system RAM; other variants retain their existing single-GPU launch behavior.

## Dependencies

`requirements.txt` contains the project CUDA/ML, retrieval, MCP, and document dependencies, including `openpyxl>=3.1.5`, `pypdf>=6.16.1`, and `xlrd>=2.0.2`. `requirements-ci.txt` contains CI/test-only dependencies.

Package installation targets the active workspace environment and follows the harness approval and compatibility rules. A package requested for a specific workspace is not implicitly treated as a request to install every entry in a repository requirements file.

## Runtime And Harness

Variants load only the encrypted harness through the version-bound native policy loader. Keep the compiled Linux/WSL `.so`, Windows `.pyd`, bundle manifest, and `HARNESS.enc` from the same release. Missing binaries, unsupported Python/architecture, integrity mismatch and invalid ciphertext fail explicitly; there is no runtime plaintext or environment-key fallback. Private authoring and bundle rebuilds remain developer-only, followed by exact plaintext parity and release checks. Binary obfuscation does not protect a prompt from the owner of the local inference server.

Automatic retrieval and embedding indexes exclude the encrypted harness, private harness source, `CLAUDE.md`, `PROJECT.md`, and `RULES.md` case-insensitively. Explicit file tools remain available when the user directly requests one of these files. `README.md` remains retrievable as untrusted factual project context, not instruction authority.

Normal launchers require the complete project-root `runtime/` package. Their import bootstrap works from `variants/` without relying on the current working directory. Models, caches, memory, harness, plugins, and the project interpreter remain rooted at the project, not `variants/`. Builder JSON templates and profile tooling remain maintenance-only dependencies. The loader assembles trusted source in each launcher's own namespace, preserving its entrypoint identity and private mutable classes/state. It does not execute model-provided source as part of this composition.

Universal code is maintained once in the shared runtime and composed into each launcher's private namespace from validated template symbols and registered source modules. This keeps GUI queues, locks, counters, and mutable model state local to each variant while avoiding a runtime dependency on the builder. Exports contain the composed implementation; normal launchers use the project runtime package. Some composed paths still import `runtime` modules, so do not assume an export is package-free.

Launch from the project root with the project interpreter:

```text
.venv/bin/python "AI Agent Qubitz.py"
.venv/bin/python "AI Agent Qubitz.py" --list-variants
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py
.venv/bin/python variants/AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py --help
```

The root launcher's default is the existing GLM-4.7-Flash 24GB reference variant; it does not choose by GPU size. Explicit selection and the existing GUI variant menu remain available. Without `--workspace`, the root launcher uses the project root; an explicit workspace is forwarded unchanged. Variant switching stops the current owned runtime before replacing the process with the selected launcher.

Variant menu entries are white when the exact default GGUF file/shard set is locally available and gray when missing or unknown; both remain selectable. Opening the menu starts read-only local checks in one background reader, keeping the GUI responsive; entries remain gray until verified and update on the Tk event loop. Repeated requests are coalesced. No weights are downloaded and other variants are not imported. The check rejects empty/misnamed files and invalid GGUF prefixes but is not a full weight-integrity, launch-success, hardware-fit, or performance guarantee. Variant-specific warmup and download policies are unchanged when selecting a model.

For composed export scripts, use `python -m runtime.development --export .cache/standalone_export` with the project interpreter and a new output directory. The export includes `HARNESS.enc` and the required `runtime/` package, including the native policy bundle, but no plaintext harness, key file or private build source. Keep that package alongside the assembled scripts. See [shared-runtime maintenance](../runtime/README.md).

Documentation is stored in this directory. Commands and code paths in these documents are relative to the project root, not the documentation directory. See the [variant catalog](qubitz_variants_list.md) and [context values](num_ctx_values.md).

Shared wrapper instructions require behavioral compatibility claims to be bounded by verified same-input comparisons or tests. API/signature compatibility does not establish preservation of numeric types, precision, overflow, exception behavior, or omitted-argument defaults.

The GUI's `Show Reasoning` checkbox is checked by default and streams separate runtime `reasoning_content` inline as provisional text. Chat follows all new output, including reasoning, until the user scrolls up; scrolling to the bottom or pressing `Jump to latest` resumes following. After a task finishes, reasoning collapses after the `Reading Speed` delay; manual scrolling keeps it visible until the next task. Click the summary to expand it in the original chat order. The GUI retains at most 100,000 displayed reasoning characters per turn and the latest 10 collapsed turns; older reasoning text is cleared. Unchecking stops new display without changing model thinking or inference settings. Reasoning is excluded from answer previews, model history, and persistent GUI logs. Missing reasoning text does not prove thinking is disabled; rendering adds GUI work, not a model call, and the text is not verified evidence.

## Verification

The maintainer's full local release gate validates profile/common-wrapper parity for the 39 public variants, Python syntax, targeted Ruff correctness rules, encrypted-harness integrity, and ignore protection. The gate and plaintext harness are not part of this public runtime tree. The three developer-only variants are excluded from automated release validation. Historical gate results no longer establish current validity after source or profile changes.

The initial 2026-10-14 modular-runtime validation passed 893 of 894 expanded CPU regressions; its cache-refresh timeout and isolated reproductions remain preserved in `.cache/modular_evidence_*.log` and `.xml`. Subsequent controlled testing showed that a backward Tcl clock step delayed timer-based delivery after the scan had finished. Unix/WSL now uses a pipe-backed Tcl file event for completed cache results, with GUI-thread updates and safe notifier closure. Platforms without file events retain timer polling. All 909 expanded CPU/Tk regressions then passed, including the original unchanged 10-second test and the controlled clock-step, shutdown, and fallback checks. The source of host clock changes and attribution of historical GUI delays remain unknown. See `.cache/cache_delivery_expanded_20261014.log` and `.xml` for the new results.

Coverage reports can group planned variants and completed results by configured backend path and nominal VRAM profile tier, then by parser formats observed from an existing runtime-properties response. These groups describe coverage; they do not reorder or select runs, and a trajectory with multiple observed formats can appear in more than one group. Unknown runtime facts remain unknown. Grouping and CPU tests do not establish model quality, speed, hardware fit, or GUI behavior beyond the specific checks performed.

Recent targeted evaluation evidence recorded in the variant documentation includes successful matrix and smoke reruns for the previously failing/slow subset. Those records are historical after source or profile changes. Headless evaluation captures stdout, stderr, logs, exit status, structured events, and telemetry; it does not by itself observe GUI rendering, browser state, or browser developer-console output.

## Maintenance

The full local maintenance checkout contains the builder, release gate, plaintext harness, and evaluation fixtures; this public runtime tree omits them. Edit universal behavior in the shared runtime only with the corresponding maintenance checks available. Pause measurement before any source change: shared runtime files are included in source freezes and artifact metadata. New live results are current evidence only after the evaluator exits and its structured results are inspected; source equality and CPU checks do not prove universal inference quality or speed.
