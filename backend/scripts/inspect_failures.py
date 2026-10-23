"""Print failed attempts of a run with the task expectation, the calls made and why they failed."""

import json
import sys

from e2e_live import c, ok

run_number = int(sys.argv[1])
model_filter = sys.argv[2] if len(sys.argv) > 2 else ""
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 12

rid = next(r["id"] for r in ok(c.get("/api/v1/runs", params={"limit": 100})) if r["number"] == run_number)
run = ok(c.get(f"/api/v1/runs/{rid}"))
variants = {k: v["model_name"] for k, v in run["summary"]["variants"].items()}
seen = set()
for t in ok(c.get(f"/api/v1/runs/{rid}/trials", params={"outcome": "fail", "limit": 500})):
    if model_filter not in variants.get(t["variant"], "") or t["task_id"] in seen:
        continue
    seen.add(t["task_id"])
    d = ok(c.get(f"/api/v1/trials/{t['id']}"))
    task = d["task"]
    calls = [f"{s['name']}({json.dumps(s.get('arguments'))[:120]})" for s in d["trace"] if s["type"] == "tool_call"]
    print(f"\n### {task['ref']} [{task['category']}] {variants[t['variant']]}  attempts {sum(a['passed'] or 0 for a in d['attempts'])}/{len(d['attempts'])}")
    print("INPUT:", task["input"][:220])
    print("EXPECTED:", json.dumps(task["expected"])[:500])
    print("CALLS:", calls)
    print("OUTPUT:", (d.get("output") or "")[:300].replace("\n", " "))
    for ch in d["checks"]:
        if not ch["passed"]:
            print(f"  FAIL {ch['name']}: {ch['detail'][:240]}")
    if d.get("error"):
        print("  ERROR:", d["error"][:200])
    if len(seen) >= limit:
        break
