# Shared Qubitz Runtime

This package supports the normal Tk GUI, CLI and MCP entrypoints for the 41
public launchers. Keep it alongside `variants/`; launchers are not standalone.

## Required Components

The loader composes each schema-2 variant with shared templates, registries and
runtime modules into an isolated namespace. Keep every published runtime file,
including `.py.in` sources and `glm53_loader.patch`.

Keep both `_policy_runtime` binaries, `policy_loader.py`, `policy_bundle.json`
and the matching root `HARNESS.enc`. The native policy loader requires CPython
3.12 x86-64 on Linux/WSL or Windows and verifies binary integrity. Users do not
need a harness key, plaintext source or policy compiler. Obfuscation does not
provide confidentiality against the owner of a local inference server.

`bootstrap.py`, `native_runtime.py`, `official_glm.py`, `gpu_devices.py` and
provide missing-only preparation, pinned compatible backends, visible-device
selection and process-local driver binding. GLM defaults to ik; the official
provider is opt-in. Offline use requires complete compatible cached artifacts.
Preparation does not silently replace the active Python/CUDA stack.

## Measurements And Evidence

`generation_telemetry.py` supplies backend-counted live tok/s, an actual aggregate
decode average and an optional token-weighted interval-median typical rate.
Missing interval coverage falls back to the average. No rate is inferred from
characters, words or chunks, or boosted by weighting peaks. Startup, prompt
processing and tools are not decode throughput.

`conversations.py` saves private readable `[HH:MM] Role:` transcripts and exact
event sidecars. Full emitted reasoning is retained independently of the GUI's
12,000-character live tail. These records are not part of the public package.

`workflow.py`, execution/completion modules and receipts distinguish a saved
file from verified behavior. Unavailable diagnostics are not successful checks.
Repetitive reasoning and unnecessary verification recovery remain known limits;
steering waits for a generation boundary. Reasoning is not executed action proof.

Transport errors do not authorize uncertain request replay. Owned native waits
are distinct from proxy exits; missing lifecycle evidence stays unknown. Device
availability checks do not prove hardware fit or successful model inference.

The developer browser interface, build/export tools, tests, evaluation suites,
private reports and generated tasks are excluded. See the
[user guide](../documentation/README.md) for launch and recovery behavior.
