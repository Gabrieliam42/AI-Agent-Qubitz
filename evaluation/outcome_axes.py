"""Separate task, containment, generation, workflow, evidence and cleanup outcomes."""
from __future__ import annotations

from collections import Counter


def summarize(review: dict, summary: dict) -> dict:
    rows = summary.get("runs", [])
    quality, transport = Counter(), Counter()
    missing, workflow, generation_problems = [], [], []
    task_rows, containment_rows = [], []
    external = False
    for row in rows:
        (containment_rows if row.get("evaluation_axes", {}).get("injected_containment_turns") else task_rows).append(row)
        injected = row.get("evaluation_axes", {}).get("injected_containment_turns", [])
        for turn in row.get("turns", []):
            key = {"variant": row.get("variant"), "scenario": row.get("scenario"), "turn": turn.get("turn")}
            observed = turn.get("observed") or {}
            generation = observed.get("generation_quality")
            external |= bool(observed.get("external_input") or (generation or {}).get("external_intervention"))
            if turn.get("status") == "not_reached":
                quality["not_reached"] += 1
                missing.append({**key, "reason": "task_turn_not_reached"})
                continue
            if turn.get("turn") in injected:
                quality["injected_fixture_not_model_quality"] += 1
                if generation and generation.get("model_requests"):
                    generation_problems.append({**key, "reason": "unexpected_model_request_in_injected_turn"})
                continue
            if not generation:
                quality["unobserved"] += 1
                missing.append({**key, "reason": "generation_evidence_unavailable"})
                continue
            quality["not_exercised" if generation.get("model_requests") == 0 else generation.get("status", "unobserved")] += 1
            transport.update(item.get("transport_status", "unobserved") for item in generation.get("requests", []))
            external |= bool(generation.get("external_intervention"))
            if (generation.get("problems") or generation.get("repetition_candidates")
                    or generation.get("guard_stops") or generation.get("status") not in {"clean", "not_exercised"}):
                generation_problems.append({**key, "generation_quality": generation})
            reasons = observed.get("workflow_quality", {}).get("review_reasons", [])
            if reasons:
                workflow.append({**key, "reasons": reasons})
    infrastructure = review.get("infrastructure_passed") is True
    task = review.get("task_passed") is True
    quality_passed = not external and not generation_problems and not quality.get("unobserved")
    return {"status": "accepted_smoke" if infrastructure and task and quality_passed else "held",
            "task_passed": task, "task_counts": dict(Counter(row.get("status", "unobserved") for row in rows)),
            "real_task_trajectory_counts": dict(Counter(row.get("status", "unobserved") for row in task_rows)),
            "containment_trajectory_counts": dict(Counter(row.get("status", "unobserved") for row in containment_rows)),
            "generation_quality_passed": quality_passed,
            "generation_quality_scope": "Observed non-injected model turns only; not a task-coverage claim",
            "generation_coverage_complete": not missing,
            "quality_turn_counts_all_task_outcomes": dict(quality),
            "generation_unobserved_slots": missing, "generation_problems": generation_problems,
            "transport_request_counts_all_task_outcomes": dict(transport),
            "workflow_review_flags": workflow, "external_intervention": external,
            "evidence": {key: review.get(key) for key in
                         ("status", "findings", "source_snapshot_matches", "persistent_inventory_unchanged", "remaining_tails")},
            "cleanup_infrastructure_audited": infrastructure, "coverage": review.get("coverage"),
            "interrupted_trajectories": sum(row.get("status") == "interrupted" for row in rows),
            "limitations": ["An injected error is containment evidence, not a model-quality trial.",
                             "Not-reached task turns are missing coverage, not additional generation failures.",
                             "Client cancellation alone does not prove backend-generation stop.",
                             "No universal recovery, full matrix or cross-model equivalence is established."]}
