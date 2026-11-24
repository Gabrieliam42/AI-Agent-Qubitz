# AI Agent Qubitz

Qubitz helps you work on code with an AI model running on your own computer.
Open a workspace, ask it to explain or change code, and review what it changed
and which checks actually ran. Use the desktop app or the command line, and
choose a [GGUF model](documentation/qubitz_variants_list.md) that fits your hardware.

Qubitz runs inference locally through llama.cpp on Windows with WSL2. You don't
need a hosted AI account, API key, or subscription. Model downloads, optional
web research, and enabled MCP integrations can use the network.

See the [user guide](documentation/README.md) for setup, tool use, conversation
logs, and how to interpret verification and token-speed results.

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

## Models and Files

Use the [variant catalog](documentation/qubitz_variants_list.md) to choose a
model and see its memory requirements. For long prompts, check the
[configured context limits](documentation/num_ctx_values.md); a configured
limit does not guarantee the model will fit at that size on your hardware.

Keep the launcher, `variants/`, `runtime/`, and `HARNESS.enc` from the same
release together. You do not need a harness key or developer tools. This
repository ships the app and user guides, not model weights, downloaded
backends, evaluation files, or conversation history.
