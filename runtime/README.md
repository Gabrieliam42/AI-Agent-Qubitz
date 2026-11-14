# Shared Qubitz Runtime

This package supports the normal Tk GUI, CLI and MCP entrypoints for the 41
public launchers in `variants/`. Keep the published package together; the small
launchers are not standalone copies of the application.

## Required Components

The loader combines each literal schema-2 variant specification with the four
source templates/registries and their registered modules. Identity and mutable
state remain isolated per variant. Keep all shipped source modules and assets,
including `glm53_loader.patch`; do not replace missing modules with another
variant's files.

Keep both `_policy_runtime` binaries, `policy_loader.py`, `policy_bundle.json`
and the matching root `HARNESS.enc`. The native policy loader supports CPython
3.12 x86-64 on Linux/WSL and Windows and verifies binary integrity. Its authoring
key, plaintext source and build tools are not required. This is obfuscation,
not confidentiality against the owner of the local inference server.

`bootstrap.py`, `native_runtime.py`, `glm53_loader.patch` and `wsl_driver.py`
are normal-user runtime dependencies. They provide missing-only dependencies,
pinned project-local GLM backend preparation and process-local WSL driver/JIT
binding. GLM native preparation needs a compatible stack and compiler toolchain;
the toolchain is acquired separately into the project cache, not installed over
the active Python stack. Offline operation requires complete local artifacts.

## Runtime Evidence

`generation_telemetry.py` supplies request-local live tok/s and completed weighted
decode timing. No rate is inferred from characters, words or stream chunks.
Prompt processing, startup and tool time are not decode throughput.

Transport failures retain bounded diagnostics without authorizing replay. Native
server lifetime receipts distinguish actual child exits from proxy exits; missing
proof stays unknown. Saved-file and behavior-verification status are separate.
Unavailable diagnostics are not passing tests. See the user guide for the known
new-file verification-loop limitation.

Local model indicators check declared file sets without loading other models.
Availability does not prove integrity, hardware fit or successful inference.
Caches, owned receipts, logs and downloaded artifacts stay under the project root.

The developer browser frontend, export/build-maintenance tools, evaluation suites,
test fixtures and private run reports are not included. See the
[user guide](../documentation/README.md) for normal launch and recovery behavior.
