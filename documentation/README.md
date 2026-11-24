# Qubitz Documentation And User Guide

## Purpose

Qubitz is a WSL2 AI coding agent built around local GGUF inference, with [model launchers](qubitz_variants_list.md) for different VRAM budgets. Shared GUI, CLI, retrieval, MCP/tool, editing, and verification code lives in `runtime/`; each launcher keeps its model-specific settings and an isolated runtime namespace.

The restored [GLM-5.3-Flash IQ4 launcher](AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.md)
targets the 72 GB VRAM+ tier using visible compatible GPUs plus system-RAM overflow and
can acquire/build its pinned isolated backend online when missing. Compatible
cached artifacts are reused, and offline or unsupported-stack failures remain
explicit. Weights are not shipped in Git.

The public Qwen3.8-Flash-Next options are [UD-IQ4_XS](AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.md)
and [UD-Q4_K_XL](AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-Q4_K_XL.md).

The project supports private local inference without requiring an API, cloud account, subscription, or paid hosted inference service. Optional online research and configured MCP providers are available when enabled and relevant to the task. Windows-hosted and WSL-hosted workspaces are supported through the existing WSL-to-Windows execution paths.

## Current Structure

- `AI Agent Qubitz.py`: normal Tk GUI launcher; loads only the selected variant.
- `variants/AI_Agent_Qubitz_*.py`: model launchers; private and local comparison launchers are not shipped.
- `runtime/`: required shared runtime source and loader; local development/export tools are not shipped.
- `documentation/`: public model/context catalog and user-facing model guides.
- `HARNESS.enc`: runtime harness artifact loaded by variants.
- `runtime/policy_bundle.json` and `_policy_runtime` binaries: version-bound native harness loader; no user key file is required.
- `.cache/`: project-local model, llama.cpp, and retrieval caches.
- `backup/`: local project backups and recovery copies; not shipped.

For a conservative user runtime bundle, retain the normal root launcher (unless launching
one selected variant directly), the selected public launcher, the complete
`runtime/` package including its `.py.in` source, native loader binaries and bundle manifest,
and `HARNESS.enc`. Use a CPython 3.12 x86-64 environment and applicable dependencies. Users do not
need a harness key, Cython, or a compiler for the policy loader. GLM native backend
preparation acquires its separate compiler toolchain when needed. Plaintext `HARNESS.txt`, the authoring key and
private native build sources are not shipped. Model weights, downloaded runtimes,
and caches are not shipped in Git. Keep the published runtime package together.

## Environment

- Primary runtime: WSL2 project environment `.venv/bin/python`.
- Default Python target: Python 3.12.
- The 24 GB, 48 GB VRAM+ and 72 GB VRAM+ profiles select all compatible GPUs visible to the backend, respecting explicit restrictions and per-device headroom. Tiers describe capacity guidance, not a fixed card count. CPU overflow still needs sufficient RAM; paging is not extra physical memory and can be much slower.
- Smaller VRAM profiles are available for compact models. A configured context limit is not a guarantee that every model or workload fits in memory.
- GPU inference uses the project-managed llama.cpp GGUF runtime and CUDA retrieval/embedding stack where supported.
- GPU selection, context limits, and runtime flags vary by model. More GPUs can increase available memory but do not guarantee higher throughput.

## Dependencies

`requirements.txt` contains the project CUDA/ML, retrieval, MCP, and document dependencies, including `openpyxl>=3.1.5`, `pypdf>=6.16.1`, and `xlrd>=2.0.2`. Developer test/lint tooling is not required for normal use.

Package installation targets the active workspace environment and follows the harness approval and compatibility rules. A package requested for a specific workspace is not implicitly treated as a request to install every entry in a repository requirements file.

## Runtime

Keep the selected launcher, complete `runtime/` package, encrypted harness,
and matching native runtime files from the same release together. Model weights
and downloaded runtime caches are stored locally and are not included in Git.

Launch from the project root with the project interpreter:

```text
.venv/bin/python "AI Agent Qubitz.py"
.venv/bin/python "AI Agent Qubitz.py" --list-variants
.venv/bin/python "AI Agent Qubitz.py" --variant AI_Agent_Qubitz_Qwen3.8-Flash-Next-125B-A6B-IQ4.py
.venv/bin/python variants/AI_Agent_Qubitz_GLM_4.7_Flash-30B-A3B-Q4.py --help
```

The root launcher's default is the existing GLM-4.7-Flash 24GB reference variant; it does not choose by GPU size. Explicit selection and the existing GUI variant menu remain available. Without `--workspace`, the root launcher uses the project root; an explicit workspace is forwarded unchanged. Variant switching stops the current owned runtime before replacing the process with the selected launcher.

On WSL, Qubitz makes one maximize request four seconds after the window appears.
Input or minimizing the window cancels the request. To disable it for one launch,
run `QUBITZ_START_MAXIMIZED=0 .venv/bin/python "AI Agent Qubitz.py"` in WSL.

The Variant picker supports the mouse wheel, scrollbar, and keyboard navigation.
Choose a row with a click or Enter; browsing alone does not switch models.
Opening the picker scans this installation's local model cache in the background.
White entries have the expected GGUF file set; gray means missing or not yet
confirmed. Opening the menu does not download weights. Selecting a variant can
download missing model files when online, after checking their metadata and
available storage. During preparation, managed files with an unexpected size or
invalid header are moved aside rather than deleted. An explicitly configured
model path is not automatically replaced. Cache indicators do not establish full
file integrity or hardware fit.

When a request identifies an exact workspace path, Qubitz can read it without a
repository-wide index. Requests that need broader context use bounded retrieval.
GPU embedding and FAISS index placement are separate decisions; a CUDA-capable
backend does not mean every retrieval step runs on every GPU. A limit or fallback
is not proof that the workspace was searched exhaustively.

`DraftPanel`, beside the token-rate controls, is off by default. Enable it to
see provisional file changes. Expand a truncated preview to see the retained
draft; very large drafts can still hit a capture limit. A draft is not a write
receipt or a test result.

The public package is the normal-user runtime, not the maintenance checkout.
Keep the shipped runtime package together with the encrypted policy bundle.

Documentation is stored in this directory. Commands and code paths in these documents are relative to the project root, not the documentation directory. See the [variant catalog](qubitz_variants_list.md) and [context values](num_ctx_values.md).

## Review Changes and Progress

When Qubitz asks whether to replace an existing named file, a standalone `yes`
confirms only that pending file. Qubitz rechecks the file before continuing and
uses a snapshot-based patch instead of searching the repository. If the file or
workspace changed, ask for the replacement again.

Saving a file and testing its behavior are different. Read both the completion
and behavior status before relying on generated code. A draft, reasoning text,
or an unavailable diagnostic does not count as a passed test. Steering takes
effect at a generation boundary, not the instant you send it.

The `tok/s` display measures model decoding, not total task speed. `-- tok/s`
means a rate is not available yet. The final summary reports the average and,
when there is enough timing data, a typical rate based on a token-weighted
median. These rates exclude model startup, prompt processing, retrieval, and tools.

## Conversations

The application stores conversation history locally. It may contain prompts,
reasoning, code, and tool output; review it before sharing. The reasoning view
is informational and does not by itself indicate that code was executed or
verified.

New text transcripts use local-time `[HH:MM] Role:` headers and retain the full
emitted reasoning even when the live view shows only a 12,000-character tail.
The adjacent `.events.jsonl` preserves precise timestamps and event order.
Existing transcripts are not rewritten.
