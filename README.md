# AI Agent Qubitz

Qubitz is a local WSL2 AI coding agent with a Tk GUI, CLI, MCP tools, retrieval,
and 41 public GGUF model launchers. Inference runs locally; a hosted AI API or
subscription is not required. Optional online research and configured MCP
providers can use the network when enabled.

## Current Runtime Notes

The shared GUI shows backend-counted live `tok/s` beside `Show Reasoning`.
`-- tok/s` means a rate is not yet available, including prompt processing; the
completed-turn summary distinguishes the actual aggregate decode average from
an optional token-weighted median (typical rate) of backend-timed intervals.
Missing interval coverage falls back to the average. Neither includes startup,
retrieval, tools or prompt processing, nor measures end-to-end task throughput.

New conversations use readable `[HH:MM] Role:` headers in local time and retain
the full emitted reasoning, independently of the GUI's 12,000-character live
tail. An adjacent `.events.jsonl` preserves precise timestamps and event order.
Both are private local files; historical transcripts are not converted.

File creation and behavioral verification are separate. A saved file can have
`Completion status: verified` together with `Behavior status: unverified`.
Follow-up edits prefer localized patches and use bounded recovery when no
verified progress is made. Unavailable diagnostics are not passing tests,
and rejected edits do not establish program correctness. Review the final
verification status before relying on generated code.

Steering is queued until a generation boundary, not applied immediately.
Repeated reasoning can consume a generation budget without writing a file;
reasoning and elapsed time alone do not establish task progress or completion.

The [GLM-5.3-Flash IQ4 variant](documentation/AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.md)
targets the 72 GB VRAM+ tier and retains its memory guards.
Weights and native runtimes are not shipped in Git. Online startup can prepare
the pinned compatible native backend and missing essential dependencies in
project-local caches; an unsupported or mixed CUDA/PyTorch stack fails closed.
Existing packages are not silently upgraded to make an incompatible stack fit.

## Requirements

- Windows 11 with WSL2 and a Python 3.12 project environment. Install the
  applicable dependencies from [requirements.txt](requirements.txt) into that
  environment before launch.
- A compatible GPU, with enough VRAM (at least 12 GB) and RAM, and free disk space for the selected
  model.
- The 24 GB, [48 GB VRAM+](documentation/multiGPU_tier_48gb.md), and 72 GB VRAM+ tiers automatically select all compatible GPUs visible to their backend, using one when only one is visible. Each GPU retains memory headroom; larger models may still need CPU offload and sufficient system RAM. The separate 12 GB profiles retain their budget.
- Network access for a first run with missing model weights or llama.cpp
  runtimes. The selected variant prepares its supported missing artifacts on
  demand; offline mode requires complete local artifacts. Download success does
  not guarantee hardware fit or successful inference.

Model weights, downloaded runtimes, indexes, and other caches are stored under
the project root and are not shipped in Git.

## Launch

From the project root in WSL, using the prepared environment:

```text
.venv/bin/python "AI Agent Qubitz.py"
.venv/bin/python "AI Agent Qubitz.py" --list-variants
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py
.venv/bin/python variants/AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py --help
```

The root launcher starts the normal Tk interface with one selected variant;
the default is not selected by GPU size. Use a variant's own `--help` for its
CLI and MCP-server options. An explicit `--workspace` is forwarded unchanged;
otherwise the root launcher uses the project root.

On WSL, the window opens at normal size and requests maximization once after
four seconds. Interacting with the window or minimizing it cancels the request.
Set `QUBITZ_START_MAXIMIZED=0` for a normal-sized launch. If input becomes
unresponsive, note whether it happened before or after the window maximized.

## Runtime Layout

- `variants/` contains 41 public schema-2 launchers. Private local variants
  are not included.
- `runtime/` contains the loader, shared source templates and symbol
  registries, and runtime modules. Keep the whole package for a normal launch;
  schema 1 and the former `qubitz_runtime/v2/` layout are not supported. The
  developer browser interface and maintenance/export modules are excluded.
- `HARNESS.enc` is loaded by the matching native policy bundle in `runtime/`.
  CPython 3.12 x86-64 binaries are included for Linux/WSL and Windows; keep
  `policy_loader.py`, `policy_bundle.json`, and both `_policy_runtime` binaries.
  Users do not need a key file, Cython, or a compiler. Plaintext harness and
  private build sources are not shipped. This is obfuscation, not a guarantee
  of confidentiality on a user-controlled computer.
- `documentation/` contains the [user guide](documentation/README.md),
  [variant catalog](documentation/qubitz_variants_list.md), and
  [context values](documentation/num_ctx_values.md).

The [user guide's recovery notes](documentation/README.md#proposal-validation-and-recovery)
describe bounded structured-proposal handling and its verification limits.

For a minimal Tk/CLI installation, retain the root launcher (unless launching
one selected variant directly), that public launcher, the complete runtime
package including its native policy bundle, the encrypted harness, and the required Python
environment. Developer maintenance commands and evaluation fixtures are not
needed for normal use. The optional browser frontend, evaluation results, and
locally generated caches are not shipped with this release.

Shared source is composed into each launcher's private namespace, preserving
its entrypoint identity and isolated mutable state. This is not proof that all
models have the same behavior or performance. See the
[runtime layout notes](runtime/README.md) for required components.

## Public Package Scope

This repository contains the normal-user runtime, 41 public launchers and user
guides. It excludes developer evaluation suites, test fixtures, build/export
tools, private run reports, transcripts, credentials and generated task files.
Model weights and downloaded backend/toolchain artifacts are acquired separately.
Source checks and small live workflows do not establish full-matrix acceptance,
model rankings, full-context fit or correctness of untested generated programs.
