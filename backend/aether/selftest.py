"""End-to-end self-test of a running Aether installation, using only the offline mock models.

  aether selftest --url http://localhost:8080 [--api-key ak_...] [--live]

Every feature is exercised over the public HTTP API; everything created is deleted afterwards.
--live additionally runs a connection test against every enabled non-mock model.
"""

from __future__ import annotations

import csv
import io
import time
import traceback
from collections.abc import Callable
from typing import Any

import httpx

TOOLS = [
    {"name": "lookup_order", "description": "Find an order by its id", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    {"name": "refund_order", "description": "Refund an order", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["order_id"]}},
]
TASKS = [
    {"category": "everyday", "input": "Where is order AB-1234?", "expected": {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "AB-1234"}}], "assertions": ["Gives the order status."]}},
    {"category": "policy", "input": "Refund order CD-5678 please", "expected": {"tool_calls": [{"name": "refund_order", "arguments": {"order_id": "CD-5678"}}]}},
    {"category": "tool_failure", "input": "Status of order EF-9012?", "inject": {"tool": "lookup_order", "kind": "timeout", "on_calls": [0]}, "expected": {"tool_calls": [{"name": "lookup_order"}]}},
    {"category": "multi_turn", "input": "I have a question about an order", "scenario": "You want the status of order GH-3456. Give the id when asked.", "max_turns": 3, "expected": {"assertions": ["Answers about the order."]}},
]


class SelfTest:
    def __init__(self, url: str, api_key: str | None, live: bool, out: Callable[[str], None], ui: bool = True):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.c = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=120)
        self.live = live
        self.ui = ui
        self.out = out
        self.results: list[tuple[str, bool, str]] = []
        self.cleanup: list[str] = []
        self.tag = f"selftest-{int(time.time())}"

    # ---------- helpers ----------
    def req(self, method: str, path: str, expect: int | tuple[int, ...] = (200, 201, 202, 204), **kw) -> Any:
        r = self.c.request(method, path, **kw)
        codes = (expect,) if isinstance(expect, int) else expect
        if r.status_code not in codes:
            raise AssertionError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        if not r.content:
            return None
        return r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text

    def check(self, name: str, fn: Callable[[], str | None]) -> Any:
        try:
            detail = fn() or ""
            self.results.append((name, True, detail))
            self.out(f"  PASS  {name}{(' - ' + detail) if detail else ''}")
            return True
        except Exception as exc:  # noqa: BLE001
            msg = f"{type(exc).__name__}: {exc}"
            self.results.append((name, False, msg))
            self.out(f"  FAIL  {name} - {msg}")
            if not isinstance(exc, AssertionError):
                self.out("        " + traceback.format_exc().strip().splitlines()[-1])
            return False

    def wait_run(self, run_id: str, timeout: float = 600) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            p = self.req("GET", f"/api/v1/runs/{run_id}/progress")
            if p["status"] in ("completed", "failed", "cancelled"):
                return self.req("GET", f"/api/v1/runs/{run_id}")
            time.sleep(2)
        raise AssertionError(f"run {run_id} did not finish within {timeout}s; are workers running?")

    def wait_job(self, job_id: str, timeout: float = 300) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            j = self.req("GET", f"/api/v1/jobs/{job_id}")
            if j["status"] in ("done", "failed"):
                return j
            time.sleep(2)
        raise AssertionError(f"job {job_id} did not finish within {timeout}s; are workers running?")

    # ---------- the test ----------
    def run(self) -> bool:
        s: dict[str, Any] = {}
        self.out(f"Aether self-test against {self.c.base_url}")

        def platform():
            assert self.req("GET", "/healthz")["status"] == "ok"
            assert self.req("GET", "/readyz")["status"] == "ready"
            meta = self.req("GET", "/api/v1/meta")
            s["meta"] = meta
            me = self.req("GET", "/api/v1/me")
            return f"version {meta['version']}, auth {meta['auth_mode']}, signed in as {me['actor']}"

        def web_ui():
            ui = self.c.get("/")
            assert ui.status_code == 200 and '<div id="root">' in ui.text, "web UI is not being served (AETHER_STATIC_DIR)"
            assets = [a for a in ui.text.split('"') if a.startswith("/assets/")]
            assert assets, "UI index has no assets"
            for a in assets:
                assert self.c.get(a).status_code == 200, f"asset {a} missing"
            deep = self.c.get("/runs/some-id")
            assert deep.status_code == 200 and '<div id="root">' in deep.text, "deep links do not fall back to the UI"
            return f"index, {len(assets)} assets, deep links"

        def models():
            ms = {m["name"]: m for m in self.req("GET", "/api/v1/models")}
            for n in ("mock-perfect", "mock-broken"):
                assert n in ms, f"{n} missing; set AETHER_SEED_SAMPLES=true"
            s["perfect"], s["broken"] = ms["mock-perfect"]["id"], ms["mock-broken"]["id"]
            t = self.req("POST", f"/api/v1/models/{s['perfect']}/test")
            assert t["ok"] and t["tool_call"]["ok"], t
            real = [m for m in ms.values() if m["provider_kind"] != "mock" and m["enabled"]]
            s["real_models"] = real
            return f"{len(ms)} models ({len(real)} real)"

        def providers():
            p = self.req("POST", "/api/v1/providers", json={"name": f"{self.tag}-openai", "kind": "openai_chat", "base_url": "https://api.openai.com/v1", "api_key": "sk-selftest-secret-0000", "headers": {"X-Org": "x"}})
            listing = self.c.get("/api/v1/providers").text
            assert "sk-selftest-secret" not in listing and p["api_key_set"], "provider secret leaked or not stored"
            m = self.req("POST", "/api/v1/models", json={"provider_id": p["id"], "name": "gpt-test", "input_cost_per_mtok": 1, "output_cost_per_mtok": 2})
            self.req("PATCH", f"/api/v1/models/{m['id']}", json={"enabled": False})
            self.req("POST", "/api/v1/providers", expect=409, json={"name": f"{self.tag}-openai", "kind": "openai_chat", "base_url": "https://x"})
            self.req("DELETE", f"/api/v1/providers/{p['id']}")
            return "create, mask secret, add/disable model, duplicate rejected, delete"

        def agent():
            a = self.req("POST", "/api/v1/agents", json={"name": f"Selftest Agent {self.tag}", "description": "self-test", "gate": {"name": "selftest", "rules": [{"metric": "pass_rate", "op": ">=", "value": 0.5}]},
                                                         "version": {"system_prompt": "You help with orders. Always look up the order first.", "tools": TOOLS, "default_model_id": s["perfect"]}})
            self.cleanup.append(a["id"])
            v2 = self.req("POST", f"/api/v1/agents/{a['id']}/versions", json={"label": "v2", "system_prompt": "You help with orders. Be brief.", "tools": TOOLS})
            s["agent"], s["versions"] = a, [a["versions"][0]["id"], v2["id"]]
            self.req("POST", "/api/v1/agents", expect=422, json={"name": "bad", "version": {"tools": [{"name": "x", "parameters": {"type": "string"}}]}})
            return f"agent {a['slug']} with 2 prompt versions; invalid tool schema rejected"

        def suites():
            su = self.req("POST", "/api/v1/suites", json={"agent_id": s["agent"]["id"], "name": "selftest import"})
            imp = self.req("POST", f"/api/v1/suites/{su['id']}/import", json={"tasks": [*TASKS, {"input": ""}, "junk"]})
            assert imp == {"imported": 4, "skipped": 2}, imp
            exported = self.req("GET", f"/api/v1/suites/{su['id']}/export")
            assert len(exported["tasks"]) == 4
            s["suite"] = su
            g = self.req("POST", "/api/v1/suites", json={"agent_id": s["agent"]["id"], "name": "selftest generated"})
            job = self.req("POST", f"/api/v1/suites/{g['id']}/generate", json={"mix": {"everyday": 2, "edge_case": 1, "tool_failure": 1}, "generator_model_id": s["perfect"]})
            j = self.wait_job(job["job_id"])
            assert j["status"] == "done", j
            gen = self.req("GET", f"/api/v1/suites/{g['id']}")
            assert gen["task_count"] >= 3 and gen["status"] == "ready", gen
            return f"imported 4 (2 invalid skipped), exported, generated {gen['task_count']} tasks"

        def run_eval():
            r = self.req("POST", "/api/v1/runs", json={"agent_id": s["agent"]["id"], "suite_id": s["suite"]["id"], "model_ids": [s["perfect"], s["broken"]], "version_ids": s["versions"], "trials": 2,
                                                      "failure_rate": 0.2, "judge_model_id": s["perfect"], "simulator_model_id": s["perfect"], "name": "selftest run"})
            assert r["status"] == "queued"
            done = self.wait_run(r["id"])
            assert done["status"] == "completed", done.get("error")
            vs = done["summary"]["variants"]
            assert len(vs) == 4, f"expected 4 variants, got {len(vs)}"
            for v in vs.values():
                m = v["metrics"]
                assert m["trials"] == 8 and m["tasks"] == 4 and len(m["reliability_curve"]) == 2 and "rules" in v["gate"], m
            perfect = max(v["metrics"]["pass_rate"] for v in vs.values() if v["model_id"] == s["perfect"])
            broken = max(v["metrics"]["pass_rate"] for v in vs.values() if v["model_id"] == s["broken"])
            assert perfect > broken, f"perfect {perfect} should beat broken {broken}"
            assert done["summary"]["recommendation"]["headline"]
            s["run"] = done
            return f"4 tasks x 4 variants x 2 attempts, perfect {perfect:.0%} vs broken {broken:.0%}; {done['summary']['recommendation']['headline']}"

        def inspect_run():
            r = s["run"]
            key = next(iter(r["summary"]["variants"]))
            mx = self.req("GET", f"/api/v1/runs/{r['id']}/matrix", params={"variant": key})
            assert len(mx["tasks"]) == 4 and all(len(t["attempts"]) == 2 for t in mx["tasks"])
            tid = mx["tasks"][0]["attempts"][0]["trial_id"]
            t = self.req("GET", f"/api/v1/trials/{tid}")
            assert t["trace"] and t["checks"] and len(t["attempts"]) == 2 and t["task"]["ref"].startswith("T-")
            injected = self.req("GET", f"/api/v1/runs/{r['id']}/trials", params={"limit": 500})
            assert any(x["status"] == "done" for x in injected)
            assert isinstance(r["failure_clusters"], list)
            g = self.req("GET", f"/api/v1/runs/{r['id']}/gate")
            assert g["done"] and isinstance(g["passed"], bool)
            rows = list(csv.reader(io.StringIO(self.c.get(f"/api/v1/runs/{r['id']}/export.csv").text)))
            assert rows[0][0] == "task_ref" and len(rows) == 1 + 32, f"CSV rows {len(rows)}"
            s["trial"] = tid
            return f"task field, replay, gate ({'pass' if g['passed'] else 'fail'}), {len(r['failure_clusters'])} failure clusters, CSV {len(rows) - 1} rows"

        def review_compare_rescore():
            r = s["run"]
            self.req("POST", f"/api/v1/trials/{s['trial']}/review", json={"verdict": "fail", "note": "selftest"})
            stats = self.req("GET", "/api/v1/review/stats")
            assert stats["reviewed"] >= 1
            self.req("GET", "/api/v1/review")
            assert self.req("POST", f"/api/v1/runs/{r['id']}/reanalyze")["status"] == "completed"
            c = self.req("GET", f"/api/v1/runs/{r['id']}/compare/{r['id']}")
            assert c["base"]["variant"] != c["head"]["variant"] and c["unchanged"] + len(c["regressions"]) + len(c["improvements"]) == 4
            job = self.req("POST", f"/api/v1/runs/{r['id']}/rescore", params={"judge_model_id": s["perfect"]})
            j = self.wait_job(job["job_id"])
            assert j["status"] == "done" and j["result"]["rescored"] == 32, j
            assert self.req("GET", f"/api/v1/runs/{r['id']}")["status"] == "completed"
            self.req("POST", f"/api/v1/agents/{s['agent']['id']}/baseline/{r['id']}")
            return "human verdict, judge agreement, re-analyse, variant compare, re-score 32 attempts, baseline"

        def ci_gate():
            k = self.req("POST", "/api/v1/api-keys", json={"name": self.tag})
            hdr = {"Authorization": f"Bearer {k['token']}"}
            r = self.c.post("/api/v1/ci/runs", headers=hdr, json={"agent": s["agent"]["slug"], "suite": "selftest import", "models": ["mock/mock-perfect"], "trials": 1})
            assert r.status_code == 201, r.text
            done = self.wait_run(r.json()["id"])
            g = self.c.get(f"/api/v1/runs/{done['id']}/gate", headers=hdr).json()
            assert g["done"] and g["model_name"], g
            self.req("DELETE", f"/api/v1/api-keys/{k['id']}")
            assert self.c.get("/api/v1/me", headers=hdr).status_code == 401, "revoked key still works"
            return f"CI run by name with an API key, gate {'passed' if g['passed'] else 'failed'}, key revoked"

        def external_agents():
            agents = {a["slug"]: a for a in self.req("GET", "/api/v1/agents")}
            missing = [x for x in ("order-tracker", "kitchen-helper") if x not in agents]
            assert not missing, f"sample external agents missing: {missing} (set AETHER_SAMPLE_AGENTS_URL)"
            out = []
            for slug, task in (("order-tracker", {"input": "Where is my parcel TRK-1001?", "expected": {"judge": False}}), ("kitchen-helper", {"input": "How many ml is 1 cup?", "expected": {"judge": False}})):
                su = self.req("POST", "/api/v1/suites", json={"agent_id": agents[slug]["id"], "name": f"{self.tag} {slug}", "tasks": [task]})
                r = self.req("POST", "/api/v1/runs", json={"agent_id": agents[slug]["id"], "suite_id": su["id"], "trials": 1, "judge_model_id": s["perfect"], "simulator_model_id": s["perfect"]})
                done = self.wait_run(r["id"])
                trials = self.req("GET", f"/api/v1/runs/{done['id']}/trials")
                assert done["status"] == "completed" and trials[0]["status"] == "done" and not trials[0]["error"], (
                    f"{slug}: {trials[0].get('error')}. The sample agents run in the 'samples' container; check `docker compose logs samples`."
                )
                t = self.req("GET", f"/api/v1/trials/{trials[0]['id']}")
                assert any(x["type"] == "tool_call" for x in t["trace"]), f"{slug} made no tool calls"
                self.req("DELETE", f"/api/v1/suites/{su['id']}")
                out.append(slug)
            return "protocol agent and OpenAI-compatible agent both answered with tool calls"

        def real_tools():
            agents = {a["slug"]: a for a in self.req("GET", "/api/v1/agents")}
            billing = agents.get("billing-support")
            assert billing, "billing-support sample agent missing"
            su = self.req("POST", "/api/v1/suites", json={"agent_id": billing["id"], "name": f"{self.tag} real", "tasks": [{"input": "Look up customer Dana", "expected": {"judge": False}}]})
            r = self.req("POST", "/api/v1/runs", json={"agent_id": billing["id"], "suite_id": su["id"], "model_ids": [s["perfect"]], "trials": 1, "tool_mode": "real", "judge_model_id": s["perfect"], "simulator_model_id": s["perfect"]})
            done = self.wait_run(r["id"])
            tid = self.req("GET", f"/api/v1/runs/{done['id']}/trials")[0]["id"]
            t = self.req("GET", f"/api/v1/trials/{tid}")
            real = [x for x in t["trace"] if x["type"] == "tool_result" and x.get("simulated") is False]
            self.req("DELETE", f"/api/v1/suites/{su['id']}")
            assert real, "no real HTTP tool call was made; is the samples service reachable from the workers?"
            return f"real API called ({real[0]['name']}, HTTP {real[0].get('http_status')})"

        def observability():
            audit = self.req("GET", "/api/v1/audit", params={"limit": 200})
            assert any(e["entity_type"] == "run" for e in audit)
            self.req("GET", "/api/v1/feed")
            metrics = self.c.get("/metrics").text
            assert "aether_http_requests_total" in metrics
            return f"{len(audit)} audit events, feed, Prometheus metrics"

        def live_models():
            res = []
            for m in s.get("real_models", []):
                t = self.req("POST", f"/api/v1/models/{m['id']}/test")
                res.append(f"{m['display_name']}: {'ok' if t.get('ok') else 'FAILED ' + (t.get('error') or '')[:120]}")
                if not t.get("ok"):
                    raise AssertionError("; ".join(res))
            return "; ".join(res) or "no real models configured"

        steps = [("Platform health, metadata and web UI", platform), *([("Web UI served", web_ui)] if self.ui else []), ("Model catalog and mock connection", models), ("Provider management and secret masking", providers),
                 ("Agent with tools and prompt versions", agent), ("Test suites: import, export, generation", suites), ("Evaluation run: models x prompts x attempts", run_eval),
                 ("Run inspection: task field, replay, gate, clusters, CSV", inspect_run), ("Review, compare, re-score, baseline", review_compare_rescore),
                 ("CI gate with API keys", ci_gate), ("External agents (protocol + OpenAI-compatible)", external_agents), ("Real-API tool mode", real_tools),
                 ("Audit log, activity feed, metrics", observability)]
        if self.live:
            steps.append(("Live connection to every real model", live_models))
        for name, fn in steps:
            ok = self.check(name, fn)
            if not ok and name in ("Platform health, metadata and web UI", "Model catalog and mock connection", "Agent with tools and prompt versions"):
                self.out("  Stopping: later checks depend on this one.")
                break
            if not ok and name in ("Test suites: import, export, generation", "Evaluation run: models x prompts x attempts"):
                break
        for aid in self.cleanup:
            try:
                self.req("DELETE", f"/api/v1/agents/{aid}")
            except Exception as exc:  # noqa: BLE001
                self.out(f"  (cleanup of agent {aid} failed: {exc})")
        passed = sum(1 for _, ok, _ in self.results if ok)
        self.out(f"\n{passed} of {len(self.results)} checks passed." + ("" if passed == len(self.results) else " See FAIL lines above."))
        return passed == len(self.results)
