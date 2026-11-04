# Remaining Follow-Up Matrix: Partial Stop And Repair Proposals

## Scope And Outcome

Reviewed on 2026-11-05 (Europe/Bucharest). The cooperative stop was recorded at
2026-11-04 22:41 UTC, which is November 5 locally.

Segment `remaining_duplicate_followup_cached_20261104_01` selected 978 whole
trajectories and 3,193 planned turns across 40 cached public variants. It excluded
102 verified historical successes; those exclusions are not current-source passes.
The stopped checkpoint records 3 passed, 3 failed, 1 interrupted and 971 unrun
trajectories. Turn slots record 12 passed, 3 failed, 1 interrupted and 9 not reached;
3,168 planned slots were unrun. This is not full matrix acceptance or model ranking.

The native coordinator and supervisor both have actual-wait exit code 0. This
means the requested stop completed, not that the tests passed. All seven case
cleanup records report exit 0 and verified owned-server idle state. A fresh
post-stop native/WSL snapshot found no measurement workers, llama.cpp servers or
listeners on the monitored native ports 8000, 8001 and 8765. No reboot, forced
process kill, source repair, dependency installation or inference retry was used.

The preceding source qualification recorded 997 distinct model-free test cases
with latest status passed and a 40-public-variant model-free release gate pass.
It is separate from live trajectory acceptance. The new controller's 16 selection
tests, 14 watchdog tests and native actual-handle/duplicate-dispatch probe passed
before launch, but missed the mutable-artifact monitoring defect described below.

## Observed Failures

- Qwen3.8-Flash-Next, HTML creation: evaluator timeout on turn one. An artifact
  was retained and its journal reports file/browser-load checks, but requested
  interactions and visual behavior remained unverified. Later turns were not
  reached. This run did not reproduce the earlier `createStars` syntax rejection.
- DeepSeek-V4-Flash, HTML creation: evaluator timeout on turn one during
  constrained content generation, with no verified created artifact. Later turns
  were not reached. HTTP success alone did not establish task success.
- Ling-3.0-Flash, multi-module revision: turn one passed. Turn two's actual
  `apply_text_patch` candidate multiplied the unchanged sum by a floating-point
  percentage factor even for an omitted argument. The behavioral guard rejected
  it before dispatch; the tool receipt says `file_unchanged: true` and
  `mutation_dispatched: false`. A proposal then exhausted its output limit and
  recovery stopped after read-only results without a verified edit. The honest
  partial outcome is correct; weakening the guard is not a fix.
- Ling's HTML trajectory was interrupted by the explicit user stop. It is not
  counted as a model-quality failure.
- The local watchdog recursively treated all `.txt` files as append-only
  evidence. A legitimate rewrite shortened a fixture `state.txt`, causing
  `Evidence stream shrank` errors. Repeated observation cycles then failed on
  that same artifact. Health snapshots continued, but evidence observation and
  compact progress were degraded. Its terminal wait receipt survived; terminal
  evidence draining failed for the same reason. No complete semantic evidence
  audit or whole-run GUI-health claim is made.

The three recorded passes were DeepSeek's multi-module revision and Ling's
read-only-to-full and online-source-section-update trajectories. They do not
qualify other variants, unrun cases, scientific accuracy or universal equivalence.

## Proposed General Repairs

These are proposals, not implemented fixes or proven explanations of every timeout.

1. **Separate streams from mutable artifacts.** Discover append-only logs through
   explicit owned receipts, not a recursive extension glob. Version mutable
   artifacts by content hash and snapshot identity instead of a monotonically
   increasing byte cursor. Preserve truncation as a genuine fault for actual
   streams; never reset a stream cursor to hide it. Isolate per-stream failures
   so one artifact cannot starve all remaining evidence and progress updates.
   Persist terminal lifecycle state independently from optional evidence drains.
2. **Deliver behavioral counterexamples to recovery.** The comparison already
   records bounded before/after cases internally, but the rejected tool result
   exposes only a generic failure. Return the first safe, bounded counterexample,
   result types, source hashes and comparison scope in structured recovery
   evidence. Preserve the model's original candidate and pre-dispatch rejection;
   do not silently rewrite its algorithm or relax numeric/exception checks.
3. **Count meaningful recovery progress.** Distinguish fresh source snapshots,
   useful diagnostics, repeated identical observations and verified mutations.
   Four independent read-only results should not automatically imply the same
   nonprogress condition as repeated rejected edits. Keep finite phase budgets,
   duplicate-action protection and truthful partial completion; add tests for a
   batched snapshot followed by a materially different valid patch.
4. **Make artifact review checkpoint-driven.** Once bytes and parse/load checks
   are verified, preserve that checkpoint and target only missing behaviors.
   Supply bounded missing-postcondition evidence rather than restarting a broad
   design discussion. Test cancellation during planning, content generation,
   browser validation and post-write review. Do not raise sampling/output/time
   budgets, inject scenario-specific HTML or accept unverified interaction claims.
5. **Separate timeout from runtime error.** Both timed-out results had
   `submitted_runtime_error: null` yet carried `turn_execution_error`. Preserve
   timeout/cancellation, generation, tool execution, artifact and cleanup axes
   independently. Emit the active phase, elapsed time, last verified checkpoint
   and actual backend/tool error when present. Slow token generation or a stale
   viewport alone is not proof of a hang or a backend defect.

Wrapper changes belong in shared runtime/recovery components and their harness
contracts, with generic model-free regressions and normal parity/encryption
qualification. No independent defect in the thin `AI Agent Qubitz.py` launcher
was established, so no launcher-specific repair is proposed. This run does not
justify changing llama.cpp versions, chat templates, GPU placement, MTP, model
profiles or decoding settings. Any later live rerun requires a new source-bound
plan; stopped outputs and uncertain actions must not be replayed.

## Evidence And Limits

Local evidence remains under `.cache/remaining_duplicate_followup_cached_20261104_01/`
and `evaluation/results/remaining_duplicate_followup_cached_20261104_01_full/`:
exact selection/authorization, source freeze, actual waits, stop snapshot/decision,
case results, host tool receipts, raw HTTP streams, private case conversations,
artifacts, GPU telemetry and captured server logs. Private journals and raw
machine/process metadata are not published. Case-relative conversation paths,
not guesses from root conversation timestamps, establish attribution.

The proposals are grounded in inspected failure receipts, conversation excerpts
and relevant source mechanisms. Complete independent review of every raw request,
stream fragment, helper and graphical artifact remains outstanding. Therefore this
document is a partial evidence-based review, not an exhaustive terminal audit or
proof that these proposals cover every latent defect. Unchanged successful tests
were not rerun merely to refresh a date.
