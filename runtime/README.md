# Shared Qubitz Runtime

This package supports the normal Tk GUI, CLI and MCP entrypoints for the 40
public launchers in `variants/`. Keep the published runtime package together;
the small launchers do not contain a standalone copy of the implementation.

## Required Files

The loader composes each launcher's literal schema-2 specification with
`base.py.in`, `wrapper.py.in`, the shared symbol registries and their registered
Python modules. It preserves the launcher's identity and private mutable state.
Missing source modules must not be replaced with another variant's files.

Retain both native `_policy_runtime` binaries, `policy_loader.py`,
`policy_bundle.json` and the matching project-root `HARNESS.enc`. The policy
loader supports CPython 3.12 x86-64 on Linux/WSL and Windows, verifies binary
integrity, and does not use a plaintext or key-file fallback. No compiler or
authoring key is required by users. This is obfuscation, not confidentiality
against the owner of the local inference server.

The request-budget, memory-context and tool-schema modules are normal runtime
dependencies. Transport failures retain bounded diagnostics without authorizing
replay. Native server lifetime receipts distinguish actual child exits from
proxy exits; missing evidence remains unknown. Tool-result summaries stay valid
JSON within their cap, and response timings require observed responses.

Model availability indicators read declared local GGUF file sets without
importing or downloading another model. Availability is not proof of model
integrity, hardware fit or successful inference. Runtime state and caches remain
under the project root, not inside `variants/`.

The developer browser interface, export tools, evaluation suites and maintenance
scripts are not included in this user runtime tree. See the
[user guide](../documentation/README.md) for launch and recovery behavior.
