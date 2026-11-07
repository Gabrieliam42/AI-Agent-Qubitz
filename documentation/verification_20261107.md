# November 7 Repairs And Verification

## Current Status

The disposable matrix and owned llama.cpp servers are stopped. No live
acceptance of the latest repaired baseline has run. Earlier partial matrices,
success exclusions and model-free checks are not current-source live passes or
a completed 40-model matrix. A new source-bound acceptance plan and explicit
user resume are required before inference.

The November 6 campaign reviewed on November 7 recorded 21 attempts: 15 passed,
5 failed and 1 harness error, with 944 selected trajectories unrun. Later
acceptance segments used different frozen sources and are not combined into a
pass rate. The latest acceptance recorded a four-turn GPT-OSS pass with
generation problems and a Nemotron harness error (passed / failed / not reached).
The dispatched Nemotron request suffered a receive failure during prefill;
no second-turn patch or execution followed. The original server was already
absent before cleanup, but its native exit reason was not retained. A GPU fault,
backend crash, external termination or parser cause has not been established.

Disposable journals are retained case-locally under
`private_transcripts/conversations/`, bound by evidence manifests. The reviewed
IDs were absent from root `conversations/`; root chats were not attributed by
timestamp or published. Raw journals and campaign artifacts remain local.

## Implemented General Repairs

- Tool schemas expose nested authoritative fields; control notices and historical
  memory are separated from untrusted data. Existing turn deadlines reach direct
  HTTP and Windows helpers without increasing allowances.
- Any current-turn execution attempt blocks forced execution recovery unless
  proven rejected before dispatch. Uncertain requests or accepted mutations are
  never replayed to repair missing execution proof.
- Typed transport receipts retain request identity, observed HTTP/helper evidence,
  readable CLIXML diagnostics and explicit uncertainty. Connection resets and
  arbitrary HTTP errors are not silently recast as safe format rejections.
- Owned native server handles retain launch identity and actual wait/exit receipts,
  separately from proxy exits. Missing receipts stay unknown. Shutdown markers
  record a request, not proof that it caused the exit.
- Transport failure, model correctness and cleanup remain separate axes. Unknown
  effects, infrastructure state or cleanup hold continuation; safe continuation
  never means retrying a failed request or only its remaining turns.
- Large tool results remain valid JSON within the existing 6,000-character cap;
  scope/wait evidence, hashes and explicit omissions replace cut JSON previews.
  Shell success and containment identity do not prove child-program correctness.
- Monotonic timings distinguish actual responses from errors. Canonical
  launch/wait-bound lifecycle state prevents stale copied running labels from
  claiming an exited campaign is active; user pause remains effective.

No task-specific answer, artifact template or weakened validator was introduced.
Model/backend/device/context/sampling/token/retry settings were unchanged. The
latest repair needed no harness or `AI Agent Qubitz.py` change, package install,
requirements change, download, Windows/WSL restart or inference retry.

## Verification And Limits

The final batches passed 61 new and 301 focused model-free tests. Three
Windows-only parametrized pytest cases were skipped under WSL. A native stdlib
driver exercised two corresponding fixture functions using the composed bridge,
not both native bridge parametrizations: exact child arguments, stdout/stderr,
actual exit 7, receive reset, HTTP rejection and malformed replies without replay.

Scoped Ruff and the 40-public-variant model-free release gate passed. All original
public profiles and the original common template reproduced exactly before
code-only metadata refresh; settings and excluded maintenance entries were
unchanged. Qualification binds 335 frozen inputs and preserves historical result,
journal, cursor and private-storage hashes. Fresh native/WSL captures found no
matrix worker or llama.cpp server; monitored ports 8000, 8001 and 8765 were idle.

Earlier failed checks are retained: native pytest could not start without
`pluggy`, so no package was installed and a stdlib driver was used. Native output
forwarding and an extra PowerShell task-result value were corrected. One old
exception assertion was updated to verify the typed failure while retaining its
single-request and partial-diagnostic checks. Metadata helper allowlist and WSL
path-separator failures were corrected before final profile qualification.

Earlier repair baselines separately passed 817 and 914 model-free checks and
their public release gates; these are not additional unique tests to sum with
362. Local qualification is retained at
`.cache/transport_lifecycle_repairs_20261107_01/qualification.json` (SHA-256
`bed6e498734929ecae60c8e5f295bbd414d870ab7298bfcef42712fd0c13eb78`).
This evidence does not establish live speed, model-quality improvement, complete
GUI behavior, universal correctness or the cause of the server disappearance.
