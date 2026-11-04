"""Observational coverage dimensions, independent of run selection and verdicts."""
from __future__ import annotations

import copy
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from runtime.variant_policy import require_user_variants_allowed


def runtime_parser_observation(runner: Any) -> dict[str, Any]:
    """Read already captured /props facts; never instantiate or probe a client."""
    client = getattr(runner, "_llm", None)
    facts = getattr(client, "_qubitz_reasoning_capabilities", None)
    if not isinstance(facts, dict):
        facts = {}
    formats = {key: facts.get(key) if isinstance(facts.get(key), str)
               and facts[key] not in {"", "unknown"} else None
               for key in ("chat_format", "reasoning_format")}
    return {"source": "existing_runtime_props" if facts else "unobserved",
            "client_type": type(client).__name__ if client is not None else None,
            **formats,
            "capabilities": copy.deepcopy(facts)}


def coverage_plan(root: Path, names: Iterable[str]) -> dict[str, Any]:
    names = list(names)
    require_user_variants_allowed(names)
    path = root / "qubitz_variant_profiles.json"
    profiles = json.loads(path.read_text(encoding="utf-8"))["variants"] if path.is_file() else []
    declared = {row["filename"]: row for row in profiles}
    if len(declared) != len(profiles):
        raise ValueError("Duplicate coverage profile identities")
    entries = {}
    for name in names:
        profile = declared.get(name)
        contract = profile.get("runtime_contract", {}) if profile else {}
        constants = contract.get("constants", {})
        budget = constants.get("QUBITZ_VRAM_BUDGET_MIB")
        tier = 12 if budget == 12288 else 48 if contract.get("dual_gpu") is True else 24 if profile else None
        tags = {key: value for key, value in constants.items() if key.endswith("LLAMA_TAG")}
        runtime = "isolated[" + ",".join(sorted(set(tags.values()))) + "]" if tags else "isolated[unobserved]"
        backend_group = ("llama.cpp/" + (runtime if contract.get("isolated_runtime") else "shared")
                         + ("/mtp" if contract.get("mtp_draft") else "/no_mtp")) if profile else "unobserved"
        entries[name] = {
            "source": "declared_variant_profile" if profile else "unobserved",
            "vram_profile_tier_gb": tier,
            "backend_group": backend_group,
            "runtime_tags": tags,
            "chat_format": None,
            "reasoning_format": None,
        }
    return {
        "schema_version": 1,
        "scope": "Coverage labels only; nominal VRAM targets are not measured fit or performance guarantees.",
        "execution_order": names,
        "variants": entries,
        "configured_groups": {
            key: {str(value): [name for name in names if entries[name][key] == value]
                  for value in dict.fromkeys(entries[name][key] for name in names)}
            for key in ("vram_profile_tier_gb", "backend_group")
        },
        "parser_scope": "Group observed runtime formats separately; never infer a parser from a model or sampling family.",
    }


def grouped_results(plan: dict[str, Any], rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        name = row.get("variant_filename") or row.get("variant")
        configured = plan.get("variants", {}).get(name, {})
        observations = []
        for turn in row.get("turns", []):
            observed = turn.get("observed") or {}
            runtime = observed.get("runtime") or {}
            observations.append(runtime.get("parser_observation") or {})
        if not observations:
            observations = [row.get("parser_observation") or {}]
        formats = list(dict.fromkeys(tuple(item.get(key) if isinstance(item.get(key), str)
                                          and item[key] not in {"", "unknown"} else None
                                          for key in ("chat_format", "reasoning_format"))
                                     for item in observations))
        # A trajectory that exposes multiple formats belongs to each observed
        # group. It remains one raw trajectory; these groups are not additive.
        for chat, reasoning in formats:
            key = (configured.get("vram_profile_tier_gb"), configured.get("backend_group", "unobserved"), chat, reasoning)
            group = groups.setdefault(key, {"vram_profile_tier_gb": key[0], "backend_group": key[1],
                                           "chat_format": chat, "reasoning_format": reasoning,
                                           "variants": [], "trajectory_count": 0, "status_counts": Counter()})
            if name not in group["variants"]:
                group["variants"].append(name)
            group["trajectory_count"] += 1
            group["status_counts"][row.get("status", "unobserved")] += 1
    return [{**group, "status_counts": dict(group["status_counts"])} for group in groups.values()]
