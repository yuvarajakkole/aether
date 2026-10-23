"""Apply the current task-fairness rules to existing suites, then re-score their runs (agents are not re-run)."""

from __future__ import annotations

import sys
import time

from e2e_live import c, ok

from aether.engine.generator import normalise_task


def main(suite_names: list[str]) -> None:
    agents = {a["id"]: a for a in ok(c.get("/api/v1/agents"))}
    for s in ok(c.get("/api/v1/suites")):
        if s["name"] not in suite_names:
            continue
        agent = ok(c.get(f"/api/v1/agents/{s['agent_id']}"))
        tools = agent["versions"][-1]["tools"]
        full = ok(c.get(f"/api/v1/suites/{s['id']}"))
        changed = 0
        for t in full["tasks"]:
            n = normalise_task(t, t["category"], tools)
            if n and n["expected"] != t["expected"]:
                ok(c.patch(f"/api/v1/suites/{s['id']}/tasks/{t['id']}", json={"expected": n["expected"]}))
                changed += 1
        print(f"[refair] {agents[s['agent_id']]['name']} / {s['name']}: {changed} of {len(full['tasks'])} tasks updated")
        for r in ok(c.get("/api/v1/runs", params={"agent_id": s["agent_id"]})):
            if r["suite_id"] != s["id"] or r["status"] != "completed":
                continue
            before = ok(c.get(f"/api/v1/runs/{r['id']}"))["summary"]["variants"]
            job = ok(c.post(f"/api/v1/runs/{r['id']}/rescore"))["job_id"]
            while (j := ok(c.get(f"/api/v1/jobs/{job}")))["status"] not in ("done", "failed"):
                time.sleep(4)
            after = ok(c.get(f"/api/v1/runs/{r['id']}"))
            print(f"  run {r['number']}: {j['status']} {j.get('result') or j.get('error')}")
            for k, v in after["summary"]["variants"].items():
                b, a = before[k]["metrics"], v["metrics"]
                print(f"    {v['model_name']}: attempts passed {b['pass_rate']:.0%} -> {a['pass_rate']:.0%}, all passed {b['all_pass_rate']:.0%} -> {a['all_pass_rate']:.0%}")
            print(f"    {after['summary']['recommendation']['headline']}")
            for cl in after["failure_clusters"][:4]:
                print(f"    * {cl['name']} ({len(cl['trial_ids'])})")


if __name__ == "__main__":
    main(sys.argv[1:] or ["Sandbox-aware v2", "Generated v2"])
