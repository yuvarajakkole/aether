"""Phase 2 live checks: retry errored attempts, re-score with updated evaluators, sandbox-aware generation + real APIs."""

from __future__ import annotations

import sys
import time

from e2e_live import M, agents, c, generate, ok, report, start, wait  # noqa: E402


def job_wait(job_id: str) -> dict:
    while True:
        j = ok(c.get(f"/api/v1/jobs/{job_id}"))
        if j["status"] in ("done", "failed"):
            return j
        time.sleep(3)


def run_by(number: int) -> dict:
    rid = next(r["id"] for r in ok(c.get("/api/v1/runs", params={"limit": 100})) if r["number"] == number)
    return ok(c.get(f"/api/v1/runs/{rid}"))


def main() -> None:
    # 1. Retry the attempt that hit the provider 429 in run 1.
    r1 = run_by(1)
    res = ok(c.post(f"/api/v1/runs/{r1['id']}/retry-errors"))
    print(f"[retry] run 1: requeued {res['requeued']} -> {res['status']}")
    r1 = wait([r1])[0]
    print(f"   run 1 now: errors={sum(v['metrics']['errors'] for v in r1['summary']['variants'].values())}, status={r1['status']}")

    # 2. Re-score run 4 (order tracker) with the tolerant date/money matching; agents are not re-run.
    r4 = run_by(4)
    before = {v["model_name"]: v["metrics"]["pass_rate"] for v in r4["summary"]["variants"].values()}
    j = job_wait(ok(c.post(f"/api/v1/runs/{r4['id']}/rescore"))["job_id"])
    r4 = ok(c.get(f"/api/v1/runs/{r4['id']}"))
    after = {v["model_name"]: v["metrics"]["pass_rate"] for v in r4["summary"]["variants"].values()}
    print(f"[rescore] run 4: {j['status']} {j['result']} pass_rate {before} -> {after}")

    # 3. Billing agent: sandbox-aware generation, then real APIs.
    sandbox = (
        "customers: dana@example.com -> C-100 (Dana Lee, Team plan); sam@example.com -> C-200 (Sam Ortiz, Pro plan). "
        "invoices C-100: INV-1001 49.00 USD paid 2026-09, INV-1002 249.00 USD paid 2026-10, INV-1003 49.00 USD open 2026-11. "
        "invoices C-200: INV-2001 99.00 USD paid 2026-10. Any other email returns 404. The refunds API rejects amounts over 100.00 with 403."
    )
    billing = agents["billing-support"]
    s = ok(c.post("/api/v1/suites", json={"agent_id": billing["id"], "name": "Sandbox-aware v2"}))
    jid = ok(c.post(f"/api/v1/suites/{s['id']}/generate", json={"mix": {"everyday": 3, "edge_case": 2, "policy": 3, "adversarial": 2}, "sandbox_data": sandbox}))["job_id"]
    j = job_wait(jid)
    gen_billing = ok(c.get(f"/api/v1/suites/{s['id']}"))
    print(f"[generate] billing sandbox-aware: {j['status']} {gen_billing['task_count']} tasks {gen_billing['categories']}")
    if not gen_billing["task_count"]:
        sys.exit(1)

    # 4. Hotel agent: regenerate with the stricter, fairer generator guidance.
    gen_hotel = generate("hotel-concierge", "Generated v2", {"everyday": 4, "edge_case": 3, "policy": 3, "adversarial": 2, "tool_failure": 2, "multi_turn": 2})

    rb = start("billing-support", gen_billing, model_ids=M("gpt-4o-mini", "claude-sonnet-4-6"), trials=2, tool_mode="real", name="Billing v2: real APIs, sandbox-aware tasks")
    rh = start("hotel-concierge", gen_hotel, model_ids=M("gpt-4o-mini", "gpt-5.2"), trials=3, failure_rate=0.1, name="Generated v2: mini vs 5.2")
    for r in sorted(wait([rb, rh]), key=lambda x: x["number"]):
        report(r)


if __name__ == "__main__":
    main()
