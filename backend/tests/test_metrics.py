import math

from aether.engine.metrics import evaluate_gate, pass_at_k, pass_hat_k, percentile, variant_metrics


def test_pass_hat_k_matches_definition():
    assert pass_hat_k(8, 8, 8) == 1.0
    assert pass_hat_k(8, 7, 8) == 0.0
    assert pass_hat_k(8, 4, 1) == 0.5
    assert math.isclose(pass_hat_k(4, 2, 2), 1 / 6)
    assert math.isnan(pass_hat_k(2, 2, 3))


def test_pass_at_k():
    assert pass_at_k(4, 0, 2) == 0.0
    assert pass_at_k(4, 3, 2) == 1.0
    assert math.isclose(pass_at_k(4, 1, 2), 0.5)


def test_percentile():
    assert percentile([], 0.5) is None
    assert percentile([10], 0.95) == 10
    assert percentile([1, 2, 3, 4], 0.5) == 2.5


def _t(task, ok, idx, **kw):
    return {"task_id": task, "status": "done", "passed": ok, "trial_index": idx, "category": kw.get("cat", "everyday"), "latency_ms": kw.get("lat", 100),
            "cost_usd": 0.01, "checks": kw.get("checks", []), "failure_category": None if ok else "Wrong tool arguments", "human_verdict": kw.get("hv")}


def test_variant_metrics_and_human_override():
    trials = [_t("a", True, 0), _t("a", True, 1), _t("b", True, 0), _t("b", False, 1), _t("c", False, 0), _t("c", False, 1, hv="pass")]
    m = variant_metrics(trials)
    assert m["tasks"] == 3 and m["trials"] == 6
    assert math.isclose(m["pass_rate"], 4 / 6)
    assert math.isclose(m["all_pass_rate"], 1 / 3)
    assert m["reliability_curve"][1]["k"] == 2
    assert m["failure_categories"] == {"Wrong tool arguments": 2}
    assert math.isclose(m["cost_per_trial_usd"], 0.01)


def test_infrastructure_errors_are_excluded_from_quality():
    trials = [_t("a", True, 0), _t("a", False, 1), {**_t("b", False, 0), "status": "error"}]
    m = variant_metrics(trials)
    assert m["trials"] == 2 and m["errors"] == 1 and m["pass_rate"] == 0.5 and m["tasks"] == 1


def test_gate_evaluation():
    m = {"pass_rate": 0.9, "all_pass_rate": 0.55, "errors": 0, "trials": 10, "categories": {"policy": {"pass_rate": 1.0}}}
    g = evaluate_gate(m, {"name": "prod", "rules": [{"metric": "pass_rate", "op": ">=", "value": 0.8}, {"metric": "category:policy:pass_rate", "op": ">=", "value": 0.95}]})
    assert g["passed"] is True
    g2 = evaluate_gate(m, None)
    assert g2["passed"] is False and [r["passed"] for r in g2["rules"]] == [True, False, True]
