# AI_Agent_Qubitz_GPT-OSS-20B_F16.py

## Shared Runtime

The normal launcher is `variants/AI_Agent_Qubitz_GPT-OSS-20B_F16.py` and requires project-root `qubitz_runtime/`. Use `AI Agent Qubitz.py --variant AI_Agent_Qubitz_GPT-OSS-20B_F16.py` or run the variant directly. Universal source is shared; specific source and mutable runtime state remain isolated. See [packaging and standalone export](../qubitz_runtime/README.md). Model settings are unchanged.

Local-inference Qubitz variant for GPT-OSS 20B F16.

## Model
- Served alias: `unsloth/gpt-oss-20b-GGUF`
- GGUF repo: `unsloth/gpt-oss-20b-GGUF`
- GGUF file: `gpt-oss-20b-F16.gguf`
- GGUF label: `GPT-OSS-20B-F16`
- Embeddings: `BAAI/bge-code-v1`

## Runtime
- 24GB-class embedding-enabled comparison variant for coding and MCP/tool tasks.
- Default context: `131072` tokens.
- Uses background model prewarm after the GUI opens to reduce perceived startup delay without changing task semantics.
- Uses the stable official llama.cpp `v0.2.0` Windows CUDA 12.4 runtime, resolved to build `b10566` and cached under `.cache/llama.cpp-b10566/`; if missing, it downloads that build and reuses it. Set `QUBITZ_LLAMA_CPP_RUNTIME_CHANNEL=previous` to roll back to `b10549`, or `legacy` for `b9193`.
- Sizes the llama.cpp server GPU-first: model weights are placed in VRAM, then the KV cache is fitted from the remaining VRAM behind a 1536 MiB safety margin. The configured context is a ceiling the runtime clamps down to whatever fits.
- When weights nearly fill usable VRAM the KV cache drops from `q8_0` to `q4_0` and every layer is pinned to the GPU, so the runtime shrinks context rather than offloading weights to the CPU.
- That fitting is a threshold rather than a smooth curve, so the window a variant actually gets can be far below its configured default: on one 24 GB card a 20.61 GiB model fitted 90368 tokens while a 21.11 GiB model fitted 16384. A turn that grows past the fitted window is rejected by llama-server with an HTTP `400`, and the status line reports the configured context rather than the fitted one, so check the server's own `n_ctx` when a long turn fails unexpectedly.
- Supports GUI, CLI, and stdio MCP server modes.
- Uses local llama.cpp GGUF inference only; no required API, cloud, subscription, or paid hosted inference service.

## Shared Qubitz Behavior
- Loads `HARNESS.enc` only. `HARNESS.txt` is an editable source that is never read at runtime, so it must be re-encrypted after any edit.
- Short direct questions bypass retrieval, embeddings, skills, and MCP/tool loading.
- Binary and structured files are snapshot-hashed instead of being decoded as text; `read_file_snapshot` returns a SHA-256-bound structured notice for formats such as `.docx`, `.xlsx`, `.pptx`, and `.pdf`.
- For `.docx`, that snapshot also carries the document's readable text as numbered `[Paragraph N]` lines, extracted from `word/document.xml` with the standard library, so the text can be inspected before editing and without installing anything. The SHA-256 still hashes the real file bytes.
- `apply_docx_text_patch` replaces text run by run and preserves formatting: it fingerprints each paragraph's properties and each run's properties, matches the unchanged prefix and suffix around a replacement, and covers body paragraphs, tables, headers, and footers.
- The raw text patch tools refuse binary and structured targets with a message naming the format-aware tool instead of failing on a decode error, and they preserve the original encoding and byte order mark; an ambiguous non-UTF-8 encoding is reported rather than guessed.
- Jupyter notebooks are edited through `apply_notebook_text_patch`, which requires a current-turn `read_file_snapshot` SHA-256 and splices each replacement into the original JSON bytes rather than re-serializing, so the notebook's existing indentation, key order, and escaping style survive unchanged. It matches only within a single cell's source and never reads or writes outputs, cell metadata, or execution counts. The raw text patch tools refuse `.ipynb`, while `read_file` and `search_text` still read notebooks as text.
- Spreadsheets are edited through `apply_spreadsheet_cell_patch`, which sets only the cells named, writes numbers, booleans, and formulas as themselves, keeps macros in `.xlsm`, and abandons the edit if saving would drop any part of the workbook container such as a chart, drawing, or image. `read_file_snapshot` previews `.xlsx`, `.xlsm`, and `.xls` as sheet-qualified rows; legacy `.xls` is read-only.
- `read_file_snapshot` extracts `.pdf` page text for reading only through `pypdf`; no PDF editor is provided, and the wrapper reports that limitation rather than rewriting the file.
- Exact-text edits to `.json` and `.toml` are rejected when they would break a file that parsed cleanly beforehand. A file that was already non-strict, such as a `tsconfig.json` carrying comments, is exempt so correct edits are never refused.
- TypeScript and JavaScript symbols are indexed by declaration rather than by loose token matching, so classes, interfaces, enums, types, functions, arrow-function consts, and class members are reported with their kind and containing scope.
- Existing `.docx` files can be edited through `apply_docx_text_patch`, which uses `python-docx` from the active workspace's project-local Python environment, verifies a staged document by reopening it, and atomically commits it; a missing capability or any failure leaves the original unchanged and does not install packages automatically.
- `expected_sha256` is normalized before comparison, so stray hyphens, whitespace, or uppercase in a model-emitted hash no longer cause a silent transaction rejection. A hash that still does not resolve to 64 hexadecimal characters is refused as `invalid_expected_sha256` naming the exact problem.
- Replacements that would change nothing are rejected as `no_change`, repeated invalid calls are stopped deterministically, and a document that cannot be written is reported as `commit_denied` with the original left intact.
- Invalid tool calls are fingerprinted by normalized semantic arguments, so harmless formatting differences do not reset the single schema-guided correction allowance.
- Edit targets are the files the user named; files read only as incidental context are not treated as edit targets. A single named `.docx` is a supported one-document transaction, and the wrapper explicitly corrects any claim of a mixed-format or multi-file limitation.
- Multi-file exact replacements can be proposed across separate calls: the wrapper stages them without mutation until every explicitly named target is covered, then validates and commits one atomic batch. Once structured evidence verifies all required postconditions, it ends further tool use and finalizes from that evidence.
- Tool-step budgets use progressive effort profiles: `low` starts at 8 and can extend to 24, `medium` 16 to 48, `high` 24 to 96, and `xhigh` 48 to 128. Extensions occur only after verified new progress; simple-question and direct-entrypoint limits remain specialized.
- A transient `500`, `502`, `503`, or `504` from the local llama.cpp server, or a dropped connection such as a reset, read error, or premature disconnect, is retried once before the error surfaces, so a momentary fault does not abort the run. Every other failure, including a `400`, is still reported on its first occurrence.
- Before each model call the request is measured against the context window llama-server actually loaded, read from the server's own `/props` rather than the configured default. If the request would exceed that window, the oldest tool-result contents are shortened until it fits. Messages are never removed and roles, tool-call identifiers, and prompt text are never altered, so tool calls stay paired with their results; a request that still does not fit is sent unchanged rather than hiding a genuine overflow.
- `inspect_python_environment` reports an environment's interpreter, kind, and installed distributions, and `install_python_package` accepts an `environment="..."` target such as `.venv` or `.venv312`. Relative environment values resolve from the active workspace root, so switching workspaces immediately changes the base; the runtime workspace's own environment is never silently targeted.
- Environments are classified by executing their exact interpreter, so a relocated or copied environment whose `pyvenv.cfg` records a stale creation path is still treated as valid. Already-present unversioned packages are skipped rather than reinstalled, and installation is only reported complete once the target environment's metadata verifies it.
- An imperative that follows a leading prepositional phrase is recognized as a request, so `in the venv .venv312 install uv` is routed to the side-effecting tool loop and requires verified dependency evidence, exactly as the verb-first wording would be.
- A runtime network mode of Online or Offline is treated as an authoritative capability fact. Offline permits only local data and loopback services and forbids any claim of live external verification.
- When an edit depends on a source URL named by the user, that exact source must be retrieved in the current turn before any file is mutated; otherwise the target is left unchanged and the blocker is reported instead of substituting memory, retrieval, or search snippets. A URL that is merely literal text to insert is exempt.
- Repository, coding, MCP, and multi-step tasks use local retrieval, bounded tools, transactional edit guards, completion verification, and wrapper-owned routing.
- MCP server references may recover from a uniquely matching workspace basename; ambiguous or unsafe matches are rejected, and repeated equivalent launch failures switch to deterministic wrapper-owned MCP lifecycle recovery.
- Wrapper-initiated transaction recovery or policy stops are reported separately from genuine user cancellation, internal recovery prompts are removed from persistent history, and completion status cites only verified current-turn evidence.
- Supports direct existing entrypoints such as `.py`, `.ps1`, `.sh`, `.bat`, `.cmd`, `uv run`, package scripts, and make targets.
- Final GUI and CLI outputs include wrapper-measured elapsed time in `(MM:SS)` format.

## Current Project State

- This document describes the variant profile with the same filename in `qubitz_variant_profiles.json`; that profile is the source of truth for this variant's model artifact and scalar runtime settings.
- Runtime loads `HARNESS.enc` only. `HARNESS.txt` is the editable source and must be re-encrypted after changes.
- The variant remains independently runnable; `qubitz_variant_builder.py` is development-time synchronization tooling, not a runtime dependency.
- Headless evaluations capture process output, logs, exit status, structured events, and telemetry; GUI rendering, browser state, and browser devtools are not covered unless separately observed.

## Verification
- Disposable matrix: 50/50 case runs across 5 full runs (10 cases each), most recently 2026-08-19.
- Live smoke: clean on 2026-08-19 with no behavior issues, timeouts, or thermal aborts.
- Median full-matrix wall time: 430 s.
- Historical static source audit 2026-09-19: shared hardening was synchronized through the builder; intentional model-specific runtime, placement, MTP, sampling, and recovery branches were standalone.
## Current Source-Verified Profile

This section is generated from the current variant profile and script synchronization state; older evaluation notes above are historical evidence and may predate later source changes.
- Model alias: `unsloth/gpt-oss-20b-GGUF`
- GGUF repository: `unsloth/gpt-oss-20b-GGUF`
- GGUF file: `gpt-oss-20b-F16.gguf`
- GGUF label: `GPT-OSS-20B-F16`
- Requested context ceiling: `131072` tokens; the effective loaded context may be lower when llama.cpp fitting must preserve memory headroom.
- Default maximum output: `16384` tokens.
- Embedding model: `BAAI/bge-code-v1`.
- This variant uses the shared project llama.cpp runtime unless its script-level model compatibility logic selects another runtime.
- The GUI performs model prewarm after startup as a variant-specific latency optimization.
- All variants now include the common exact named-file operation route for explicit create, edit, copy, rename/move, and delete requests, with operation-specific verification; the model and hardware-specific sections remain in each launcher.
- Historical documentation refresh 2026-09-19: the then-standalone wrapper included follow-up-aware routing, narrow fixed-response fast paths, explicit online/offline research gating, loaded-context request budgeting, adaptive reasoning/tool budgets, GUI elapsed-time and generation-speed telemetry, and verified completion evidence.

## Latest Targeted Smoke

- 2026-09-12 targeted smoke completed the question and direct-entrypoint task without timeout, thermal abort, model-load failure, stderr, or workspace-integrity mutation.
- Question duration was `0:10.01`; direct-entrypoint task duration was `0:09.93`.
- The test driver recorded one recoverable GUI heartbeat stall; this is a diagnostic warning, not an agent-task failure.

## Behavioral Compatibility Evidence

The shared wrapper distinguishes API/signature compatibility from behavioral compatibility. Claims about prior numeric types, precision, overflow, exception behavior, or omitted-argument defaults must be limited to behavior verified by same-input comparisons or tests; otherwise state what remains unverified. Passing a narrow fixture or run does not establish untested edge cases.

