# AI Agent Qubitz

Qubitz is a local WSL2 AI coding agent with a Tk GUI, CLI, MCP tools, retrieval,
and 39 public GGUF model launchers. Inference runs locally; a hosted AI API or
subscription is not required. Optional online research and configured MCP
providers can use the network when enabled.

## Requirements

- Windows 11 with WSL2 and a Python 3.12 project environment. Install the
  applicable dependencies from [requirements.txt](requirements.txt) into that
  environment before launch.
- A compatible GPU, with enough VRAM (at least 12 GB) and RAM, and free disk space for the selected
  model.
- Some larger variants target one or two 24 GB GPUs; see the [48 GB VRAM+ tier](documentation/multiGPU_tier_48gb.md) and each variant guide.
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

## Runtime Layout

- `variants/` contains 39 public schema-2 launchers. The three developer-only
  local variants are not included in this release.
- `runtime/` contains the loader, shared source templates and symbol
  registries, and runtime modules. Keep the whole package for a normal launch;
  schema 1 and the former `qubitz_runtime/v2/` layout are not supported.
- `HARNESS.enc` is loaded by the matching native policy bundle in `runtime/`.
  CPython 3.12 x86-64 binaries are included for Linux/WSL and Windows; keep
  `policy_loader.py`, `policy_bundle.json`, and both `_policy_runtime` binaries.
  Users do not need a key file, Cython, or a compiler. Plaintext harness and
  private build sources are not shipped. This is obfuscation, not a guarantee
  of confidentiality on a user-controlled computer.
- `documentation/` contains the [user guide](documentation/README.md),
  [variant catalog](documentation/qubitz_variants_list.md), and
  [context values](documentation/num_ctx_values.md).

For a minimal Tk/CLI installation, retain the root launcher (unless launching
one selected variant directly), that public launcher, the complete runtime
package including its native policy bundle, the encrypted harness, and the required Python
environment. Developer maintenance commands and evaluation fixtures are not
needed for normal use. The optional browser frontend, evaluation results, and
locally generated caches are not shipped with this release.

Shared source is composed into each launcher's private namespace, preserving
its entrypoint identity and isolated mutable state. This is not proof that all
models have the same behavior or performance. See the
[runtime layout notes](runtime/README.md) for maintenance details.
