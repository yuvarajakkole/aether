"""Group failed trials into a handful of named root causes."""

from __future__ import annotations

import random
from collections import defaultdict
from typing import Any

from aether.engine.llm_json import JSONReplyError, chat_json
from aether.providers.base import LLMClient, ProviderError

CLUSTER_SYSTEM = """You analyse failed test attempts of an AI agent and group them by root cause.
Each failure has an id, a rule-based category, failed check details and the judge's reasoning.
Produce at most 6 clusters. Name each cluster with a specific, plain-language cause in under 8 words
(e.g. "Changed booking terms without asking", not "Policy issue"). Every id must appear in exactly one cluster.
Return JSON only: {"clusters": [{"name": str, "description": str (one sentence), "trial_ids": [str]}]}"""


def fallback_clusters(failures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for f in failures:
        groups[f.get("category") or "Other"].append(f["id"])
    return [{"name": k, "description": f"{len(v)} failed attempts.", "trial_ids": v} for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1]))]


async def cluster_failures(client: LLMClient | None, failures: list[dict[str, Any]], max_items: int = 250) -> tuple[list[dict[str, Any]], float]:
    if not failures:
        return [], 0.0
    if client is None:
        return fallback_clusters(failures), 0.0
    sample = failures if len(failures) <= max_items else random.Random(0).sample(failures, max_items)
    try:
        data, responses = await chat_json(client, CLUSTER_SYSTEM, {"failures": sample}, purpose="cluster", max_tokens=4000, temperature=0)
    except (JSONReplyError, ProviderError):
        return fallback_clusters(failures), 0.0
    cost = sum(r.cost_usd for r in responses)
    known = {f["id"] for f in failures}
    clusters = []
    assigned: set[str] = set()
    for c in data.get("clusters") or []:
        ids = [i for i in c.get("trial_ids") or [] if i in known and i not in assigned]
        if not ids or not c.get("name"):
            continue
        assigned.update(ids)
        clusters.append({"name": str(c["name"])[:120], "description": str(c.get("description", ""))[:400], "trial_ids": ids})
    # Failures outside the sample (or dropped by the model) go to the cluster of their rule category.
    leftovers = [f for f in failures if f["id"] not in assigned]
    if leftovers:
        by_cat = {c["name"]: c for c in clusters}
        for f in leftovers:
            cat = f.get("category") or "Other"
            target = next((c for c in clusters if cat.lower() in c["name"].lower()), None) or by_cat.get(cat)
            if target is None:
                target = {"name": cat, "description": "Failed attempts with this rule-based category.", "trial_ids": []}
                clusters.append(target)
                by_cat[cat] = target
            target["trial_ids"].append(f["id"])
    clusters.sort(key=lambda c: -len(c["trial_ids"]))
    return clusters, cost
