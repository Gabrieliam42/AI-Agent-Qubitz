# Shared Qubitz Runtime

## Runtime Layout

The 40 user-facing launchers live in project-root `variants/`. Three developer-only local launchers are excluded from the public release. Normal menus and `--list-variants` expose only the public variants without importing them. Each launcher has a literal schema-2 `_QUBITZ_VARIANT_SPEC` containing its model-specific source sections and a validated project-root import bootstrap. Universal source is stored once in package-root `base.py.in`, `wrapper.py.in`, and their `*.shared.py.in` symbol registries. Schema 1 is not supported.

For the normal Tk entrypoint, keep `__init__.py`, `launcher.py`, `loader.py`, `source.py`, `symbols.py`, `paths.py`, `catalog.py`, `managed_process.py`, `policy_loader.py`, `policy_bundle.json`, both `_policy_runtime` binaries, all four source templates/registries, and the eight registry-linked modules: `conversations.py`, `execution_scope.py`, `gui_diagnostics.py`, `gui_event_delivery.py`, `model_acquisition.py`, `mtp_controls.py`, `prohibition_scope.py`, and `variant_policy.py`. All eight registry-linked files are read while composing every public launcher, even if a particular feature is not used. The separate `browser_entrypoint.py`, `web_api.py`, and `web_controller.py` are for the opt-in developer browser entrypoint. `development.py` is maintenance tooling for controlled variant edits and assembled export; it is not needed by a normal user launch. `README.md` is documentation, not runtime input. Generated `__pycache__` files are not required.

`loader.initialize_variant` composes trusted project source and executes it in the launcher's own globals. Existing module names, `__file__`, main entrypoint, ownership behavior, and class patches are preserved. Mutable classes and application state are not shared between variants. This lossless composition is deliberately not an ordinary import of one mutable application class.

Ordinary universal modules can be registered with `QUBITZ_SHARED_MODULE` entries. The GUI FIFO logger lives in `gui_diagnostics.py`; its exact source is composed into each private namespace and standalone export. Queues, locks, counters, and shutdown state remain GUI-owned. Module aliases and escaped source paths are rejected.

Application runtime storage stays at the project root while `__file__` and process launch paths identify the real file in `variants/`. `paths.py` defaults to public discovery and rejects directory/file aliases rather than substituting another script; `all_variant_paths()` is the separate maintenance inventory. `variant_policy.py` holds the developer-only exclusions and is composed into standalone selectors too. Neither helper redirects user file tools or historical evidence paths.

`catalog.py` reads literal per-variant defaults without importing them. Menu colors refresh bounded local file-set checks on opening: white for available sets, gray for missing/unknown, with all entries selectable. No Hub query, weight download, full model hash, or model invocation is involved. These indicators are not proof of model integrity or hardware compatibility.

`policy_loader.py` selects the exact CPython 3.12 x86-64 sibling native module for Windows or Linux/WSL, checks its manifest hash, origin and bundle ID, then decrypts `HARNESS.enc`. It does not use a plaintext harness or environment/file key as fallback. Normal users do not install Cython or compile anything. The private authoring key and native build sources remain outside the public tree. This is reversible obfuscation, not confidentiality against the owner of the local model server; plaintext is necessarily present during inference. Dependency fingerprints include both native binaries and their manifest.

The GUI reader delivers scan results through Unix Tcl file events, so completed scans do not depend on wall-clock timer deadlines. Unsupported platforms use timer polling. Workers never call Tk; GUI destruction closes the notifier and prevents a late worker from writing to a reused descriptor. Repeated requests coalesce into one subsequent scan.

`read_variant_source(Path(...))` returns effective source without importing the variant. Static checks, profile maintenance, source freezes, and artifact metadata use this source or include the package dependencies. Runtime tracebacks and `inspect` use the assembled source with the original launcher filename; physical launcher line numbers are not effective-source line numbers.

## Maintenance

Pause live measurement before changing runtime source. Edit universal behavior in the applicable shared template or symbol registry, not a variant's universal-reference markers. Use a sequential project-root backup and changelog entry before editing Python source, including source registries. In the full local maintenance checkout, universal changes affect all 43 launchers; automated regressions and the release gate exercise only the 40 public variants. The three developer-only launchers, builder, and release gate are not shipped in this public runtime tree.

In the full local maintenance checkout, keep all 43 model settings in `qubitz_variant_profiles.json` authoritative. Builder synchronization, profile application, and profile capture retain the full maintenance inventory; `--check` and the release gate use the public selection. After a deliberate shared edit, recapture the maintenance template from the effective public reference source with `--capture-template`, then run `--check` and the release gate. The profile JSON, builder, and its template are not shipped in this minimal public runtime tree.

Development edits and backups resolve relative paths from the project root and reject sources, backup directories, or changelog targets that resolve outside it.

## Standalone Export

From the project root, using the project interpreter:

```text
python -m runtime.development --export .cache/standalone_export
```

Choose a new project-local output directory. The export contains the 40 fully assembled public scripts, `HARNESS.enc`, and the required `runtime/` package including its native policy bundle. It omits developer-only scripts, plaintext harness, key file, private build sources and debug symbols. Keep the package beside the assembled scripts and use the project dependencies. The export is not package-free.

The initial shared-source extraction was source-lossless against the preserved originals. The later directory migration deliberately changes runtime-root discovery and selector behavior; preserved original source and launch receipts remain historical evidence. Neither source checks nor CPU tests guarantee inference speed, quality, or untested deployment compatibility.
