"""Run-level metrics per variant (model x prompt version)."""

from __future__ import annotations

from collections import Counter, defaultdict
from math import comb
from typing import Any


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(all k i.i.d. attempts succeed) from c successes in n attempts (tau-bench)."""
    if k > n or n == 0:
        return float("nan")
    return comb(c, k) / comb(n, k)


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(at least one of k attempts succeeds) (Chen et al. 2021)."""
    if k > n or n == 0:
        return float("nan")
    if n - c < k:
        return 1.0
    return 1.0 - comb(n - c, k) / comb(n, k)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = (len(s) - 1) * p
    lo, hi = int(idx), min(int(idx) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def effective_pass(t: dict[str, Any]) -> bool:
    hv = t.get("human_verdict")
    if hv in ("pass", "fail"):
        return hv == "pass"
    return bool(t.get("passed"))


def _check_rate(trials: list[dict[str, Any]], name: str) -> float | None:
    vals = [c["passed"] for t in trials for c in (t.get("checks") or []) if c["name"] == name]
    return sum(vals) / len(vals) if vals else None


def variant_metrics(trials: list[dict[str, Any]]) -> dict[str, Any]:
    # Infrastructure errors (provider outages, network) say nothing about the agent: they are reported
    # separately and can be retried. Failures of the agent itself (including its own endpoint) are status 'done'.
    errored = [t for t in trials if t["status"] == "error"]
    done = [t for t in trials if t["status"] == "done"]
    by_task: dict[str, list[bool]] = defaultdict(list)
    cat_of: dict[str, str] = {}
    for t in done:
        by_task[t["task_id"]].append(effective_pass(t))
        cat_of[t["task_id"]] = t.get("category") or "uncategorised"
    n_trials = max((len(v) for v in by_task.values()), default=0)
    total = len(done)
    passed = sum(effective_pass(t) for t in done)

    curve = []
    for k in range(1, n_trials + 1):
        vals = [pass_hat_k(len(v), sum(v), k) for v in by_task.values() if len(v) >= k]
        at = [pass_at_k(len(v), sum(v), k) for v in by_task.values() if len(v) >= k]
        curve.append({"k": k, "pass_hat_k": sum(vals) / len(vals) if vals else None, "pass_at_k": sum(at) / len(at) if at else None})
    all_k = sum(1 for v in by_task.values() if v and all(v)) / len(by_task) if by_task else None

    categories: dict[str, dict[str, Any]] = {}
    for cat in sorted(set(cat_of.values())):
        tids = [tid for tid, c in cat_of.items() if c == cat]
        att = [p for tid in tids for p in by_task[tid]]
        categories[cat] = {
            "tasks": len(tids),
            "pass_rate": sum(att) / len(att) if att else None,
            "all_pass_rate": sum(1 for tid in tids if all(by_task[tid])) / len(tids) if tids else None,
        }

    latencies = [t["latency_ms"] for t in done if t.get("latency_ms") is not None]
    costs = [t.get("cost_usd") or 0.0 for t in done]
    failures = Counter(t.get("failure_category") for t in done if not effective_pass(t) and t.get("failure_category"))
    injected = [t for t in done if any(c["name"] == "recovery" for c in (t.get("checks") or []))]
    return {
        "trials": total,
        "tasks": len(by_task),
        "attempts_per_task": n_trials,
        "pass_rate": passed / total if total else None,
        "all_pass_rate": all_k,
        "reliability_curve": curve,
        "tool_selection": _check_rate(done, "tool_selection"),
        "argument_accuracy": _check_rate(done, "arguments"),
        "schema_validity": _check_rate(done, "schema"),
        "recovery_rate": _check_rate(done, "recovery"),
        "trials_with_tool_errors": len(injected),
        "policy_pass_rate": _check_rate(done, "judge"),
        "latency_p50_ms": percentile(latencies, 0.5),
        "latency_p95_ms": percentile(latencies, 0.95),
        "input_tokens": sum(t.get("input_tokens") or 0 for t in done),
        "output_tokens": sum(t.get("output_tokens") or 0 for t in done),
        "cost_usd": sum(costs),
        "cost_per_trial_usd": sum(costs) / total if total else None,
        "eval_cost_usd": sum(t.get("eval_cost_usd") or 0.0 for t in done),
        "errors": len(errored),
        "agent_crashes": sum(1 for t in done if t.get("error")),
        "needs_review": sum(1 for t in done if t.get("needs_review") and not t.get("human_verdict")),
        "categories": categories,
        "failure_categories": dict(failures.most_common()),
        "task_pass_counts": {tid: sum(v) for tid, v in by_task.items()},
    }


DEFAULT_GATE: dict[str, Any] = {
    "name": "default",
    "rules": [
        {"metric": "pass_rate", "op": ">=", "value": 0.8, "label": "Attempts passed"},
        {"metric": "all_pass_rate", "op": ">=", "value": 0.6, "label": "Tasks passed on every attempt"},
        {"metric": "errors_rate", "op": "<=", "value": 0.02, "label": "Crashed or errored attempts"},
    ],
}

OPS = {">=": lambda a, b: a >= b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b, "<": lambda a, b: a < b, "==": lambda a, b: a == b}


def gate_value(metrics: dict[str, Any], metric: str) -> float | None:
    if metric == "errors_rate":
        attempted = (metrics.get("trials") or 0) + (metrics.get("errors") or 0)
        return ((metrics.get("errors") or 0) + (metrics.get("agent_crashes") or 0)) / attempted if attempted else None
    if metric.startswith("category:"):
        _, cat, field = (metric.split(":") + ["pass_rate"])[:3]
        return (metrics.get("categories", {}).get(cat) or {}).get(field)
    v = metrics.get(metric)
    return v if isinstance(v, (int, float)) else None


def evaluate_gate(metrics: dict[str, Any], gate: dict[str, Any] | None) -> dict[str, Any]:
    gate = gate if gate and gate.get("rules") else DEFAULT_GATE
    rules = []
    for r in gate["rules"]:
        v = gate_value(metrics, r["metric"])
        ok = v is not None and OPS[r.get("op", ">=")](v, r["value"])
        rules.append({**r, "actual": v, "passed": ok})
    return {"name": gate.get("name", "custom"), "passed": all(r["passed"] for r in rules), "rules": rules}
