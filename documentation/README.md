# Qubitz Documentation And User Guide

## Current Runtime Notes

The shared GUI displays backend-counted live `tok/s` beside `Show Reasoning`.
Prompt processing or missing/stale counters display `-- tok/s`; completion uses
backend decode measurements, not elapsed wall time or text length.
Hiding reasoning does not hide this counter or disable model thinking.

Creation-only requests can still encounter an unnecessary edit/verification
recovery loop. A verified saved file is not a behaviorally tested program;
`available=false` diagnostics are unavailable, not successful. Read both the
completion and behavior status. Repetitive reasoning can exhaust a generation
budget without a tool call or file write. Steering waits for a generation
boundary; a queued message is not proof that the current call has received it.

## Purpose

Qubitz is a WSL2 AI coding agent built around local GGUF inference, with 41 user-facing Python launchers for different models and VRAM budgets. Universal GUI, CLI, retrieval, MCP/tool, transaction, and completion-verification code lives in `runtime/`; each launcher retains its variant-specific source and an isolated runtime namespace.

The restored [GLM-5.3-Flash IQ4 launcher](AI_Agent_Qubitz_GLM-5.3-Flash-UD-IQ4_XS.md)
targets the 72 GB VRAM+ tier using visible compatible GPUs plus system-RAM overflow and
can acquire/build its pinned isolated backend online when missing. Compatible
cached artifacts are reused, and offline or unsupported-stack failures remain
explicit. Weights are not shipped in Git.
Historical small-test passes did not establish stable long-context operation.

The project supports private local inference without requiring an API, cloud account, subscription, or paid hosted inference service. Optional online research and configured MCP providers are available when enabled and relevant to the task. Windows-hosted and WSL-hosted workspaces are supported through the existing WSL-to-Windows execution paths.

## Current Structure

- `AI Agent Qubitz.py`: normal Tk GUI launcher; loads only the selected variant.
- `variants/AI_Agent_Qubitz_*.py`: 41 public launchers; private local launchers are not shipped.
- `runtime/`: required shared runtime source and loader; local development/export tools are not shipped.
- `documentation/`: public model/context catalog and 41 user-facing model guides.
- `HARNESS.enc`: runtime harness artifact loaded by variants.
- `runtime/policy_bundle.json` and `_policy_runtime` binaries: version-bound native harness loader; no user key file is required.
- `.cache/`: project-local model, llama.cpp, retrieval, and evaluation caches.
- `backup/`: local project backups and recovery copies; not shipped.

For a conservative user runtime bundle, retain the normal root launcher (unless launching
one selected variant directly), the selected public launcher, the complete
`runtime/` package including its `.py.in` source, native loader binaries and bundle manifest,
and `HARNESS.enc`. Use a CPython 3.12 x86-64 environment and applicable dependencies. Users do not
need a harness key, Cython, or a compiler for the policy loader. GLM native backend
preparation acquires its separate compiler toolchain when needed. Plaintext `HARNESS.txt`, the authoring key and
private native build sources are not shipped. This packaging discourages casual inspection;
it does not prevent extraction of the harness on a user-controlled machine.
Model weights, downloaded runtimes, and caches are not shipped in Git.
Keep the published runtime package together;
source/package checks are not full live-workflow acceptance.

## Dependencies

`requirements.txt` contains the project CUDA/ML, retrieval, MCP, and document dependencies, including `openpyxl>=3.1.5`, `pypdf>=6.16.1`, and `xlrd>=2.0.2`. Developer test/lint tooling is not required for normal use.

Package installation targets the active workspace environment and follows the harness approval and compatibility rules. A package requested for a specific workspace is not implicitly treated as a request to install every entry in a repository requirements file.

## Runtime And Harness

Variants load only the encrypted harness through the version-bound native policy loader. Keep the compiled Linux/WSL `.so`, Windows `.pyd`, bundle manifest, and `HARNESS.enc` from the same release. Missing binaries, unsupported Python/architecture, integrity mismatch and invalid ciphertext fail explicitly; there is no runtime plaintext or environment-key fallback. Private authoring and bundle rebuilds remain developer-only, followed by exact plaintext parity and release checks. Binary obfuscation does not protect a prompt from the owner of the local inference server.

Automatic retrieval and embedding indexes exclude the encrypted harness, private harness source, `CLAUDE.md`, `PROJECT.md`, and `RULES.md` case-insensitively. Explicit file tools remain available when the user directly requests one of these files. `README.md` remains retrievable as untrusted factual project context, not instruction authority.

Normal launchers require the complete project-root `runtime/` package. Their import bootstrap works from `variants/` without relying on the current working directory. Models, caches, memory, harness, plugins, and the project interpreter remain rooted at the project, not `variants/`. Builder JSON templates and profile tooling remain maintenance-only dependencies. The loader assembles trusted source in each launcher's own namespace, preserving its entrypoint identity and private mutable classes/state. It does not execute model-provided source as part of this composition.

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

The Variant menu scans this installation's local model cache in the background.
White entries have the expected GGUF file set; gray means missing or not yet
confirmed. Opening the menu does not download weights. Selecting a variant can
download missing model files when online, after checking their metadata and
available storage. During preparation, managed files with an unexpected size or
invalid header are moved aside rather than deleted. An explicitly configured
model path is not automatically replaced. Cache indicators do not establish full
file integrity or hardware fit.

The public package is the normal-user runtime, not the maintenance checkout.
Keep the shipped runtime and encrypted policy bundle together. See the
[runtime component guide](../runtime/README.md).

Documentation is stored in this directory. Commands and code paths in these documents are relative to the project root, not the documentation directory. See the [variant catalog](qubitz_variants_list.md) and [context values](num_ctx_values.md).

Shared wrapper instructions require behavioral compatibility claims to be bounded by verified same-input comparisons or tests. API/signature compatibility does not establish preservation of numeric types, precision, overflow, exception behavior, or omitted-argument defaults.

## Proposal Validation And Recovery

Structured edit proposals are checked against the exact authorized file snapshots before dispatch. Each target path has its own original line bounds; malformed, out-of-range, overlapping, or otherwise invalid proposals are rejected before mutation. Rejection is recorded as proposal evidence and does not by itself mean that a mutation was attempted.

Where the existing shared recovery budget permits, one read-only proposal correction may be requested. It is bounded to the existing authorization and budget; it does not unescape model text, expand scope, or replay an uncertain mutation. Structured-response failures retain typed evidence, while backend HTTP or payload errors remain backend faults rather than being reported as ordinary model partial results.

After an edit, optional numeric checks use a restricted interpreter over supported scalar Python syntax. A proposal may be checked against task-derived expectations, with evaluated, matched, and accepted cases recorded separately. This does not execute the Python module, run its full test suite, or establish behavior outside the checked functions and inputs. If a proposal is unusable or disagrees with the bounded checks, the edit remains distinct from its verification status; report it as unverified/partial rather than treating a successful write as proof of correctness. See [runtime recovery code](../runtime/behavioral_checks.py) and [structured response handling](../runtime/structured_response.py).

## Transport Failures And Execution Evidence

Transport errors retain typed request/helper evidence and bounded original
diagnostics rather than being labeled model-format rejections. A reset or other
uncertain dispatched request is not automatically replayed or sent through a
different transport. Unknown action outcomes or unverified cleanup hold campaign
continuation. Any attempted execution blocks forced execution recovery unless it
was proven rejected before dispatch; missing proof does not authorize a rerun.

Owned server receipts distinguish actual native-child waits/exits from
PowerShell/WSL proxy exits. Missing native exit evidence stays unknown, and a
shutdown-request marker does not establish why the server exited. Large tool
results use parseable summaries within the existing 6,000-character cap, with
omissions explicit. Shell success does not prove a child program's success or
interpreter. Response latency requires an observed response; time-to-error is
separate. Canonical launch/wait-bound progress overrides stale running labels;
a user pause still requires an explicit resume.

## Live Token Rate

Live rates use request-local backend token counts over a short time window.
The actual average is total measured completion tokens divided by total backend
decode seconds across model calls. The optional typical rate is an approximate
token-weighted median of non-overlapping five-second decode intervals; a final
interval needs at least one second and intervals must cover at least 80 percent
of measured tokens. Otherwise the summary uses the actual average. Peak rates
are not arbitrarily boosted. Startup, prefill, tool work and idle time are not
included. Rates from different prompts,
contexts or output lengths are not controlled performance comparisons. Missing
backend counters remain unavailable; Qubitz does not estimate tokens from
characters, reasoning words or streamed chunks.

## Reasoning Display

The GUI's `Show Reasoning` checkbox displays provisional `reasoning_content`.
Its live tail is capped at 12,000 characters, not the full conversation record.
Hiding it does not disable model thinking or change inference settings. Chat
follows new output until the user scrolls up; `Jump to latest` resumes following.
Reasoning is not verified execution evidence or an accepted final answer.

## Private Conversation Records

New GUI sessions save readable transcripts under `conversations/` with local
`[HH:MM] System:`, `Status:`, `User:`, `Reasoning:` and other role headers.
The text follows displayed messages and retains full emitted reasoning, even
when the live tail is capped. Internal producer duplicates and transient answer
previews remain in the adjacent `.events.jsonl`, which preserves capture order,
exact UTC timestamps, original roles and emitted text. Historical files are not
rewritten. These records can contain prompts, code and tool results; they are
private local artifacts and are not shipped to GitHub.
