# AI Agent Qubitz

Provision a WSL2 Python 3.12 `.venv` and its compatible GPU/dependency stack from
`requirements.txt` before launch. Model weights, downloaded llama.cpp runtimes, and caches
are not in this repository. Check the selected variant's guide for its hardware requirements.

From the project root in WSL, start the normal Tk GUI with `.venv/bin/python "AI Agent Qubitz.py"`.
The 39 user-facing variant launchers are in `variants/`; universal runtime code is in `qubitz_runtime/`.
The [48 GB VRAM+ tier](documentation/48gb_vram_tier.md) targets both GPUs plus available system RAM, not a 48GB total-memory limit or a fixed 128GB RAM requirement.

For a normal user runtime, retain the root launcher (or launch a selected variant directly),
the selected public launcher in `variants/`, the complete `qubitz_runtime/` package including
its `.py.in` source, `HARNESS.enc`, and the matching `QUBITZ_HARNESS_KEY.local.txt`. Use the project
Python 3.12 environment and applicable dependencies. Models, downloaded runtimes, and
caches are acquired or created separately. Developer helpers, evaluation results, plaintext
`HARNESS.txt`, and documentation are not normal runtime inputs. This release publishes the
harness key alongside the encrypted artifact; the encryption does not keep the harness secret.

The optional React browser UI and release-maintenance tools are not part of this minimal
public runtime tree. CLI (`--cli`) and MCP server (`--serve-mcp`) modes use the normal entrypoints.
Conversation transcripts remain under `conversations/` in the runtime root.

See the [project documentation](documentation/README.md),
[variant catalog](documentation/qubitz_variants_list.md), and
[context values](documentation/num_ctx_values.md).
