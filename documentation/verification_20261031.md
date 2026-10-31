# Scoped GUI Verification - 2026-10-31

The `proposal_contract_frozen_live_20261031_01` campaign finished with
`accepted_smoke`, an `audited_complete` terminal review and no audit findings.

## Live Scope

- [Gemma 4 12B Q4 12 GB profile](AI_Agent_Qubitz_Gemma-4-12B-IT-Q4_12G.md).
- [Ministral 3 8B Q5 12 GB profile](AI_Agent_Qubitz_Ministral-3-8B-Instruct-2512-Q5_12G.md).
- [Qwen3.5 9B Q5 12 GB profile](AI_Agent_Qubitz_Qwen3.5_9B_Q5_12G.md).
- [AgenticQwen 30B A3B Q4](AI_Agent_Qubitz_AgenticQwen-30B-A3B.i1-Q4.md).

Each ran `create_edit_execute` (three turns) and
`structured_error_then_new_task` (two turns), once, through visible Tk normal
Send. All eight trajectories and 20 planned slots passed. Four slots deliberately
injected one malformed proposal per model: these verify containment and same-GUI
follow-up, not model quality. The remaining 16 genuine model turns had no recorded
generation-quality problems. Ordinary profiles and recovery budgets were unchanged.

## Recovered Problems

- Gemma supplied an invalid patch argument shape, then an exact-text mismatch.
  Both were rejected before dispatch. One bounded snapshot proposal produced the
  authorized patch; bounded numeric checks and the requested entrypoint passed.
- Ministral's first numeric proposal incorrectly expected `ValueError` for
  `divide(0, 5)`. One read-only correction accepted seven cases, with nine total
  evaluations across both proposals. The committed edit was not replayed.

Neither event proves an incorrect implementation, a causal regression, or
universal recovery reliability. Numeric AST checks do not execute the module or
replace a full test suite; entrypoint execution was separately recorded.

## Evidence And Provenance

The audit verified all eight worker waits, owned cleanup, native log captures,
idle evaluation endpoints and unchanged protected source/documentation snapshots.
Eight terminal native text/XML tails were preserved separately. The 219- and
333-case CPU reports and 40-public release gate were retained source-bound evidence,
not fresh reruns. Eleven pure admission checks were fresh. A preceding zero-test
WSL path-invocation failure was preserved before the corrected admission run.

Earlier held campaigns remain held, including the preceding smoke invalidated by
documentation edits during measurement. This fresh run used new outputs; it did
not resume or reclassify that evidence. Raw evidence is local under the ignored
`.cache/proposal_contract_frozen_live_20261031_01/`, not shipped in this repository.

- Terminal review SHA-256: `5ada7074846cbe5a5853245a2c827c0ad384bdf07329ec4e426211c30f8de0f5`.
- Terminal disposition SHA-256: `6ca2182b80c3cba729db28fe3e446e53afbe30336a4c88414ffe87fda357b483`.
- Independent outcome SHA-256: `82185bdea0577fa3e01a6cadd795e7435a8baf3bf014102b31d789887565e25d`.

## Limits

Only these four models and two scenarios were exercised live. No full matrix,
fresh cancellation trial, model ranking, universal reliability, full-context
memory-fit or cross-model equivalence claim follows. Initial Gemma rendering was
independently observed; other GUI coverage is retained evaluator diagnostics and
events, not independent visual-equivalence proof. The 12 GB profiles were tested
on the existing RTX 3090 host, not certified on every physical 12 GB GPU.

These documents were refreshed after terminal acceptance. The recorded results
refer to the measured frozen inputs, not a remeasurement of refreshed documents.
