"""Bound repeated format failures without retrying or blaming unrun trajectories."""
from __future__ import annotations


def repeated_rejection_hold(rows: list[dict]) -> dict | None:
    """Two independent consecutive failures on the same verified backend warrant review."""
    if len(rows) < 2:
        return None
    selected = rows[-2:]
    fingerprints = []
    receipts = []
    for row in selected:
        if row.get("status") != "failed" or row.get("cleanup", {}).get("exit_code") != 0 or row["cleanup"].get("exception"):
            return None
        receipt = next((turn.get("observed", {}).get("request_failure")
                        for turn in reversed(row.get("turns", [])) if turn.get("observed", {}).get("request_failure")), None)
        if not isinstance(receipt, dict) or receipt.get("schema") != "qubitz_generation_rejection_v1":
            return None
        backend = receipt.get("backend")
        if not isinstance(backend, dict) or not backend.get("build") or not backend.get("template_sha256"):
            return None
        if receipt.get("uncertain_prior_action") is not False or receipt.get("completion_accepted") is not False:
            return None
        fingerprints.append((row.get("variant"), receipt.get("category"), backend["build"], backend["template_sha256"]))
        receipts.append(receipt.get("request_id"))
    tuples = [(row.get("variant"), row.get("scenario"), row.get("repeat")) for row in selected]
    if fingerprints[0] != fingerprints[1] or tuples[0] == tuples[1] or not all(receipts) or receipts[0] == receipts[1]:
        return None
    return {"reason": "Two distinct consecutive trajectories rejected generation on the same backend/template",
            "classification": "review_required_not_proven_backend_root_cause", "variant": fingerprints[0][0],
            "backend_build": fingerprints[0][2], "template_sha256": fingerprints[0][3],
            "request_ids": receipts, "retry_permitted": False,
            "unrun_disposition": "Retain unrun tuples; do not mark them failed or dispatch further work before review"}
