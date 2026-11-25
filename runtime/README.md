# Shared Qubitz Runtime

## Runtime Layout

Model launchers live in project-root `variants/`. The normal picker and `--list-variants` discover user-facing launchers without importing them. Private launchers and local comparisons are not shipped. Each launcher has a literal schema-2 `_QUBITZ_VARIANT_SPEC` containing its model-specific source sections and a validated project-root import bootstrap. Universal source is stored once in package-root `base.py.in`, `wrapper.py.in`, and their `*.shared.py.in` symbol registries. Schema 1 is not supported.

For the normal Tk entrypoint, keep `__init__.py`, `launcher.py`, `loader.py`, `source.py`, `symbols.py`, `paths.py`, `catalog.py`, `managed_process.py`, `policy_loader.py`, `policy_bundle.json`, both `_policy_runtime` binaries, all four source templates/registries, and the nine registry-linked modules: `conversations.py`, `execution_scope.py`, `gui_diagnostics.py`, `gui_event_delivery.py`, `gui_window.py`, `model_acquisition.py`, `mtp_controls.py`, `prohibition_scope.py`, and `variant_policy.py`. All nine registry-linked files are read while composing every public launcher, even if a particular feature is not used. `README.md` is documentation, not runtime input. Generated `__pycache__` files are not required.

`loader.initialize_variant` composes trusted project source and executes it in the launcher's own globals. Existing module names, `__file__`, main entrypoint, ownership behavior, and class patches are preserved. Mutable classes and application state are not shared between variants. This lossless composition is deliberately not an ordinary import of one mutable application class.

Keep `request_budget.py`, `tool_schema.py`, and `memory_context.py` beside the runtime: shared transport, tool-publication, and system-prompt paths import them directly. They carry the existing remaining turn allowance, expose bounded nested argument contracts without weakening host validation, and fence historical memory within its existing context limit. They do not change model settings or persistent memory storage.

Retain `workflow.py` for creation-aware recovery and evidence-only terminal records under `.qubitz/workflow_turns/`. A verified new file is creation progress, not a reason to invent a patch or helper. Syntax parsing, optional type diagnostics and behavioral tests are separate checks; unavailable diagnostics are not passed checks. Each composed runner captures detached, invocation-bound evidence before broker teardown, then records the returned, failed or cancelled outcome without storing raw prompts or granting replay permission. Records distinguish captured, partial, unavailable and not-started evidence; snapshot or storage failures remain explicit and do not replace the original outcome or prevent broker cleanup. `gui_window.py` also wraps the settings header at restored widths without changing control values or token-rate telemetry.

Explicit create-and-test requests retain their authorized execution after creation. Creation alone does not authorize general commands. Authorized wrapper diagnostics remain bounded and tied to the exact saved revision. Existing-test failures can receive a same-scope correction only when repair is authorized. Passing results are bound to source hashes and become stale after those sources change.

`verification_discovery.py` bounds implicit post-edit test discovery to the workspace's conventional test directory or an unambiguous top-level test file. `creation_verifier.py` can recheck the exact saved revision after a bound Tk startup repair, without treating widget construction as behavior verification. Explicit tool-only selection remains restricted when a selected tool is unavailable.

The normal runtime also uses `retrieval.py` for bounded source scans, cache
identity and staged index updates; `dense_split_gguf.py` for GGUF memory fitting;
`file_draft.py` for optional provisional previews; `tk_environment.py` for
Tcl/Tk child-environment compatibility; and `startup_progress.py` for checksum
progress. Keep these modules with the shared source templates.

Also retain `generation_telemetry.py`, `bootstrap.py`, `native_runtime.py`,
`glm53_loader.patch` and `wsl_driver.py`. These are normal-user dependencies,
not evaluation tools: they provide shared live/final backend token rates,
missing-only application dependencies, pinned GLM native preparation and
process-local WSL driver/JIT binding. Native setup uses project-local cache and
isolated compiler environments; it never silently replaces the active ML stack.

`official_glm.py` is also a normal-user dependency. GLM-5.3-Flash keeps its
existing ik runtime by default. To opt into the independently cached official
llama.cpp v1.0.0 source build, set `QUBITZ_GLM53_RUNTIME=official` in the WSL
environment before launching Qubitz; unset it or use `ik` to return to the
default. Restart Qubitz before changing providers. There is no silent fallback.
Preparation verifies the pinned source checksum and every built backend library;
offline mode requires an already verified runtime and never downloads or builds.
The active Python/CUDA stack is not replaced.

The official adapter preserves configured context and memory guards, uses all
selected compatible GPUs with per-device headroom, and requires model-ready
`/health`, not merely a listening HTTP port. When CPU-resident weights are needed,
its child-only pageable-buffer policy avoids the pinned-buffer loading failure
observed under WSL. CUDA 13.4 on three RTX 3090s passed a short streaming,
reasoning and tool-response diagnostic with verified native cleanup. Probe-reported
startup was 20m26s versus an earlier ik observation of 7m12s at the same context and GPU
count; this was not a controlled speed comparison, so official remains opt-in.
CUDA 13.0, normal GUI/full-agent workflows and long-context behavior are not
live-qualified for this provider. Windows paging is not additional GPU memory
or Linux swap; CPU offload remains subject to host and guest admission guards.

Also retain `transport_failures.py`, `server_lifetime.py`, `tool_results.py`, and `request_timing.py`. Transport failures preserve typed uncertainty and bounded original diagnostics under `.cache/request_failures/`; they do not authorize replay. Owned server lifetimes retain launch-bound native handles and actual exits under `.cache/llama.cpp/lifetimes/`, separately from WSL/PowerShell proxy exits. Missing exit evidence stays unknown. Shutdown receipts record the presence of a runtime shutdown request, not proof it caused the exit.

Retain `reasoning_controls.py` and `structured_response.py` for normal request construction. Verified template AST contracts route `low`/`high`/`max` effort through template kwargs for required-thinking templates; they do not infer controls from model names. Unsupported explicit modes fail before dispatch, unknown templates retain existing behavior, and required thinking is not mislabeled as disabled. The verified history-clearing control defaults to true unless explicitly overridden; omitted effort retains the template's max default. Requests keep existing budgets and memory guards, and diagnostics need a separately approved explicit token budget.

Large model-facing tool results use valid JSON summaries within the existing 6,000-character cap; omitted fields are explicit and retained full-result hashes/output references remain evidence, not instructions. Shell success and containment identity do not prove an individual program's result or interpreter. Response timing is emitted only after an observed response; failure latency is separate.

Ordinary universal modules can be registered with `QUBITZ_SHARED_MODULE` entries. The GUI FIFO logger lives in `gui_diagnostics.py`; its exact source is composed into each private namespace. Queues, locks, counters, and shutdown state remain GUI-owned. Module aliases and escaped source paths are rejected.

Application runtime storage stays at the project root while `__file__` and process launch paths identify the real file in `variants/`. `paths.py` defaults to public discovery and rejects directory/file aliases rather than substituting another script; `all_variant_paths()` is the separate maintenance inventory. `variant_policy.py` holds the developer-only exclusions. Neither helper redirects user file tools or historical evidence paths.

`catalog.py` supplies nominal VRAM tiers and reads literal per-variant model defaults without importing them. `variant_picker.py` displays those tiers in a scrollable Tk list. Colors refresh from bounded local file-set checks on opening: white for available sets, gray for missing/unknown, with all entries selectable. No Hub query, weight download, full model hash, or model invocation is involved. The tiers and indicators do not prove model integrity or hardware fit.

`policy_loader.py` selects the exact CPython 3.12 x86-64 sibling native module for Windows or Linux/WSL, checks its manifest hash, origin and bundle ID, then decrypts `HARNESS.enc`. It does not use a plaintext harness or environment/file key as fallback. Normal users do not install Cython or compile anything. The private authoring key and native build sources remain outside the public tree. This is reversible obfuscation, not confidentiality against the owner of the local model server; plaintext is necessarily present during inference. Dependency fingerprints include both native binaries and their manifest.

The GUI reader delivers scan results through Unix Tcl file events, so completed scans do not depend on wall-clock timer deadlines. Unsupported platforms use timer polling. Workers never call Tk; GUI destruction closes the notifier and prevents a late worker from writing to a reused descriptor. Repeated requests coalesce into one subsequent scan.

`read_variant_source(Path(...))` returns effective source without importing the variant. Static checks, profile maintenance, source freezes, and artifact metadata use this source or include the package dependencies. Runtime tracebacks and `inspect` use the assembled source with the original launcher filename; physical launcher line numbers are not effective-source line numbers.

`gpu_devices.py` is a normal-user runtime dependency. Standard backends report
their own visible device IDs and free memory; the pinned GLM CUDA backend uses
driver ordinals matched to memory records by UUID. All public tiers use the
selected compatible GPUs with layer splitting and per-device
headroom, not a fixed card count. Device masks and explicit `LLAMA_ARG_DEVICE`
selection are respected. Multiple non-CUDA backend families require an explicit
selection to avoid double-counting physical GPUs. Discovery failures remain
errors, not invented single-GPU inventories. The 12 GB tier shares its aggregate
fitting allowance across devices without increasing it. Model settings, MTP
reserves, fit dialects, and host-memory guards are retained. This
does not add support for GPU vendors absent from the selected backend or imply
that a multi-GPU allocation is a unified memory pool or a guaranteed speedup.
