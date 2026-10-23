"""End-to-end through the HTTP API with the offline mock provider and real workers."""

import asyncio
import csv
import io

from sqlalchemy import func, select

from aether import db as dbm
from aether.models import Trial
from aether.worker import Worker


async def models_by_name(client):
    r = await client.get("/api/v1/models")
    assert r.status_code == 200
    return {m["name"]: m for m in r.json()}


async def hotel(client):
    agents = (await client.get("/api/v1/agents")).json()
    a = next(a for a in agents if a["slug"] == "hotel-concierge")
    suites = (await client.get("/api/v1/suites", params={"agent_id": a["id"]})).json()
    return a, next(s for s in suites if s["name"] == "Starter scenarios")


async def test_meta_and_seed(client):
    meta = (await client.get("/api/v1/meta")).json()
    assert meta["app"] == "Aether" and "multi_turn" in meta["task_categories"]
    assert {p["id"] for p in meta["provider_presets"]} >= {"openai", "anthropic", "bedrock", "mock"}
    assert (await client.get("/healthz")).json() == {"status": "ok"}
    assert (await client.get("/readyz")).status_code == 200
    models = await models_by_name(client)
    assert {"mock-perfect", "mock-flaky", "mock-broken"} <= set(models)
    a, s = await hotel(client)
    assert a["kind"] == "builtin" and s["task_count"] == 6
    me = (await client.get("/api/v1/me")).json()
    assert me["workspace"]["slug"] == "default"


async def test_validation_errors(client):
    a, s = await hotel(client)
    r = await client.post("/api/v1/agents", json={"name": "Bad", "version": {"tools": [{"name": "x", "parameters": {"type": "string"}}]}})
    assert r.status_code == 422
    r = await client.post("/api/v1/agents", json={"name": "Ext", "kind": "http"})
    assert r.status_code == 422 and "endpoint.url" in r.text
    r = await client.post("/api/v1/runs", json={"agent_id": a["id"], "suite_id": s["id"], "model_ids": []})
    assert r.status_code == 422 and "model" in r.text
    r = await client.post("/api/v1/runs", json={"agent_id": "nope", "suite_id": s["id"]})
    assert r.status_code == 404
    r = await client.post("/api/v1/providers", json={"name": "x", "kind": "openai_chat"})
    assert r.status_code == 422


async def test_full_run_lifecycle(client):
    models = await models_by_name(client)
    a, s = await hotel(client)
    perfect, flaky, broken = models["mock-perfect"]["id"], models["mock-flaky"]["id"], models["mock-broken"]["id"]
    body = {
        "agent_id": a["id"],
        "suite_id": s["id"],
        "model_ids": [perfect, flaky, broken],
        "trials": 3,
        "failure_rate": 0.1,
        "judge_model_id": perfect,
        "simulator_model_id": perfect,
    }
    r = await client.post("/api/v1/runs", json=body)
    assert r.status_code == 201, r.text
    run = r.json()
    assert run["status"] == "queued"
    prog = (await client.get(f"/api/v1/runs/{run['id']}/progress")).json()
    assert prog["total"]["queued"] == 6 * 3 * 3

    await Worker(concurrency=8).drain(timeout=120)

    full = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert full["status"] == "completed", full.get("error")
    variants = full["summary"]["variants"]
    assert len(variants) == 3
    by_model = {v["model_name"]: v for v in variants.values()}
    for v in variants.values():
        m = v["metrics"]
        assert m["trials"] == 18 and m["tasks"] == 6 and m["attempts_per_task"] == 3
        assert len(m["reliability_curve"]) == 3
        assert m["reliability_curve"][0]["pass_hat_k"] >= m["reliability_curve"][2]["pass_hat_k"]
        assert "rules" in v["gate"]
    broken_m = by_model["Mock (broken)"]["metrics"]
    assert broken_m["pass_rate"] <= by_model["Mock (perfect)"]["metrics"]["pass_rate"]
    assert broken_m["tool_selection"] == 0.0
    assert full["summary"]["recommendation"]["headline"]
    assert isinstance(full["failure_clusters"], list) and full["failure_clusters"]
    assert sum(len(c["trial_ids"]) for c in full["failure_clusters"]) == sum(18 - round(v["metrics"]["pass_rate"] * 18) for v in variants.values())

    async with dbm.sessionmaker()() as db:
        attempts = (await db.execute(select(func.max(Trial.attempts)).where(Trial.run_id == run["id"]))).scalar()
        assert attempts == 1

    mx = (await client.get(f"/api/v1/runs/{run['id']}/matrix", params={"variant": next(iter(variants))})).json()
    assert len(mx["tasks"]) == 6 and all(len(t["attempts"]) == 3 for t in mx["tasks"])
    tid = mx["tasks"][1]["attempts"][0]["trial_id"]
    trial = (await client.get(f"/api/v1/trials/{tid}")).json()
    assert trial["trace"][0]["type"] == "user" and trial["task"]["ref"] == "T-002" and len(trial["attempts"]) == 3
    assert trial["checks"][0]["name"] == "completed"

    fails = (await client.get(f"/api/v1/runs/{run['id']}/trials", params={"outcome": "fail"})).json()
    assert fails
    rv = await client.post(f"/api/v1/trials/{fails[0]['id']}/review", json={"verdict": "pass", "note": "judge too strict"})
    assert rv.status_code == 200 and rv.json()["human_verdict"] == "pass"
    stats = (await client.get("/api/v1/review/stats")).json()
    assert stats["reviewed"] >= 1
    re = (await client.post(f"/api/v1/runs/{run['id']}/reanalyze")).json()
    assert re["status"] == "completed"

    g = (await client.get(f"/api/v1/runs/{run['id']}/gate")).json()
    assert g["done"] and isinstance(g["passed"], bool)

    rs = await client.post(f"/api/v1/runs/{run['id']}/rescore", params={"judge_model_id": perfect})
    assert rs.status_code == 202, rs.text
    await Worker(concurrency=2).drain(timeout=60)
    job = (await client.get(f"/api/v1/jobs/{rs.json()['job_id']}")).json()
    assert job["status"] == "done" and job["result"]["rescored"] == 54, job
    assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "completed"

    csv_text = (await client.get(f"/api/v1/runs/{run['id']}/export.csv")).text
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0][0] == "task_ref" and len(rows) == 1 + 54

    # second run on same suite -> compare + feed + agent trend
    r2 = (await client.post("/api/v1/runs", json={**body, "model_ids": [flaky]})).json()
    await Worker(concurrency=8).drain(timeout=120)
    cmp = await client.get(f"/api/v1/runs/{run['id']}/compare/{r2['id']}", params={"base_variant": f"{flaky}:{a['latest_version']['id']}"})
    assert cmp.status_code == 200, cmp.text
    c = cmp.json()
    assert c["unchanged"] + len(c["regressions"]) + len(c["improvements"]) == 6
    agents = (await client.get("/api/v1/agents")).json()
    h = next(x for x in agents if x["slug"] == "hotel-concierge")
    assert h["latest_run"]["number"] == r2["number"] and len(h["trend"]) >= 2
    feed = (await client.get("/api/v1/feed")).json()
    assert isinstance(feed, list) and feed
    bl = await client.post(f"/api/v1/agents/{a['id']}/baseline/{run['id']}")
    assert bl.status_code == 200 and bl.json()["baseline_run_id"] == run["id"]


async def test_custom_agent_versions_suite_import_and_gate(client):
    models = await models_by_name(client)
    perfect = models["mock-perfect"]["id"]
    tools = [
        {"name": "lookup_order", "description": "Find an order by id", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
        {"name": "refund_order", "description": "Refund an order", "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    ]
    r = await client.post(
        "/api/v1/agents",
        json={
            "name": "Order Desk",
            "description": "orders",
            "gate": {"name": "strict", "rules": [{"metric": "pass_rate", "op": ">=", "value": 0.99}]},
            "version": {"system_prompt": "Help with orders.", "tools": tools, "default_model_id": perfect},
        },
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    v2 = await client.post(f"/api/v1/agents/{agent['id']}/versions", json={"system_prompt": "Help with orders. Be brief.", "tools": tools, "label": "brief"})
    assert v2.status_code == 201 and v2.json()["version"] == 2
    s = (await client.post("/api/v1/suites", json={"agent_id": agent["id"], "name": "orders"})).json()
    imp = await client.post(
        f"/api/v1/suites/{s['id']}/import",
        json={"tasks": [
            {"input": "Where is order AB-1234?", "expected": {"tool_calls": [{"name": "lookup_order", "arguments": {"order_id": "AB-1234"}}]}},
            {"input": "Please refund order CD-5678", "category": "policy", "expected": {"tool_calls": [{"name": "refund_order", "arguments": {"order_id": "CD-5678"}}], "judge": False}},
            {"input": ""},
            "not a dict",
        ]},
    )
    assert imp.json() == {"imported": 2, "skipped": 2}
    full = (await client.get(f"/api/v1/suites/{s['id']}")).json()
    assert full["status"] == "ready" and [t["ref"] for t in full["tasks"]] == ["T-001", "T-002"]
    versions = [v["id"] for v in (await client.get(f"/api/v1/agents/{agent['id']}")).json()["versions"]]
    run = (await client.post("/api/v1/runs", json={"agent_id": agent["id"], "suite_id": s["id"], "version_ids": versions, "trials": 2, "judge_model_id": perfect, "simulator_model_id": perfect})).json()
    assert run["config"]["model_ids"] == [perfect], "falls back to the version's default model"
    await Worker(concurrency=4).drain(timeout=60)
    done = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "completed" and len(done["summary"]["variants"]) == 2
    for v in done["summary"]["variants"].values():
        assert v["metrics"]["argument_accuracy"] == 1.0, v["metrics"]
        assert v["gate"]["name"] == "strict"
    exported = (await client.get(f"/api/v1/suites/{s['id']}/export")).json()
    assert len(exported["tasks"]) == 2


async def test_suite_generation_job(client):
    models = await models_by_name(client)
    a, _ = await hotel(client)
    s = (await client.post("/api/v1/suites", json={"agent_id": a["id"], "name": "generated"})).json()
    r = await client.post(f"/api/v1/suites/{s['id']}/generate", json={"mix": {"everyday": 3, "tool_failure": 2, "multi_turn": 1}, "generator_model_id": models["mock-perfect"]["id"]})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert (await client.post(f"/api/v1/suites/{s['id']}/generate", json={"mix": {"everyday": 1}})).status_code == 409
    await Worker(concurrency=2).drain(timeout=60)
    job = (await client.get(f"/api/v1/jobs/{job_id}")).json()
    assert job["status"] == "done", job
    full = (await client.get(f"/api/v1/suites/{s['id']}")).json()
    assert full["status"] == "ready"
    cats = full["categories"]
    assert cats.get("everyday") == 3 and cats.get("tool_failure") == 2
    assert all(t["inject"].get("tool") for t in full["tasks"] if t["category"] == "tool_failure")
    bad = await client.post(f"/api/v1/suites/{s['id']}/generate", json={"mix": {"nonsense": 2}})
    assert bad.status_code == 422


async def test_ci_endpoint_and_api_keys(client):
    created = (await client.post("/api/v1/api-keys", json={"name": "ci"})).json()
    token = created["token"]
    assert token.startswith("ak_")
    hdr = {"Authorization": f"Bearer {token}"}
    me = (await client.get("/api/v1/me", headers=hdr)).json()
    assert me["via"] == "api_key" and me["actor"] == "api-key:ci"
    r = await client.post("/api/v1/ci/runs", headers=hdr, json={"agent": "hotel-concierge", "models": ["mock/mock-perfect"], "trials": 1})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    g = (await client.get(f"/api/v1/runs/{rid}/gate", headers=hdr)).json()
    assert g["done"] is False
    await Worker(concurrency=8).drain(timeout=60)
    g = (await client.get(f"/api/v1/runs/{rid}/gate", headers=hdr)).json()
    assert g["done"] and g["model_name"] == "Mock (perfect)"
    assert (await client.post("/api/v1/ci/runs", headers=hdr, json={"agent": "hotel-concierge", "models": ["nope"]})).status_code == 404
    await client.delete(f"/api/v1/api-keys/{created['id']}")
    assert (await client.get("/api/v1/me", headers=hdr)).status_code == 401
    audit = (await client.get("/api/v1/audit")).json()
    assert any(e["action"] == "revoke" for e in audit)


async def test_two_workers_never_double_process(client):
    models = await models_by_name(client)
    a, s = await hotel(client)
    run = (await client.post("/api/v1/runs", json={"agent_id": a["id"], "suite_id": s["id"], "model_ids": [models["mock-perfect"]["id"]], "trials": 5, "judge_model_id": models["mock-perfect"]["id"]})).json()
    w1, w2 = Worker(concurrency=6, worker_id="w1"), Worker(concurrency=6, worker_id="w2")
    await asyncio.gather(w1.drain(timeout=120), w2.drain(timeout=120))
    async with dbm.sessionmaker()() as db:
        rows = (await db.execute(select(Trial.attempts, Trial.worker_id, Trial.status).where(Trial.run_id == run["id"]))).all()
    assert len(rows) == 30 and all(r.attempts == 1 for r in rows) and all(r.status == "done" for r in rows)
    assert {r.worker_id for r in rows} <= {"w1", "w2"}
    done = (await client.get(f"/api/v1/runs/{run['id']}")).json()
    assert done["status"] == "completed"


async def test_fair_share_scheduling(client):
    models = await models_by_name(client)
    a, s = await hotel(client)
    mid = models["mock-perfect"]["id"]
    big = (await client.post("/api/v1/runs", json={"agent_id": a["id"], "suite_id": s["id"], "model_ids": [mid], "trials": 10, "judge_model_id": mid})).json()
    small = (await client.post("/api/v1/runs", json={"agent_id": a["id"], "suite_id": s["id"], "model_ids": [mid], "trials": 1, "judge_model_id": mid})).json()
    w = Worker(concurrency=6, worker_id="fair")
    claimed = await w._claim_trials(6)
    async with dbm.sessionmaker()() as db:
        runs = (await db.execute(select(Trial.run_id).where(Trial.id.in_(claimed)))).scalars().all()
    assert len(claimed) == 6 and big["id"] in runs and small["id"] in runs, "a large run must not starve a later small one"
    await asyncio.gather(*(w._do_trial(t) for t in claimed))
    await Worker(concurrency=8).drain(timeout=120)
    for rid in (big["id"], small["id"]):
        assert (await client.get(f"/api/v1/runs/{rid}")).json()["status"] == "completed"


async def test_cancel_run(client):
    models = await models_by_name(client)
    a, s = await hotel(client)
    run = (await client.post("/api/v1/runs", json={"agent_id": a["id"], "suite_id": s["id"], "model_ids": [models["mock-perfect"]["id"]], "trials": 2})).json()
    r = await client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert r.json()["status"] == "cancelled"
    prog = (await client.get(f"/api/v1/runs/{run['id']}/progress")).json()
    assert prog["total"] == {"cancelled": 12}
    await Worker(concurrency=2).drain(timeout=30)
    assert (await client.get(f"/api/v1/runs/{run['id']}")).json()["status"] == "cancelled"


async def test_provider_secrets_never_returned(client):
    r = await client.post("/api/v1/providers", json={"name": "openai-test", "kind": "openai_chat", "base_url": "https://api.openai.com/v1", "api_key": "sk-supersecret-123456", "headers": {"X-Org": "a"}})
    assert r.status_code == 201
    body = r.json()
    assert "sk-supersecret" not in r.text and body["api_key_set"] and body["api_key_hint"] == "sk-s…3456"
    assert body["header_names"] == ["X-Org"]
    dup = await client.post("/api/v1/providers", json={"name": "openai-test", "kind": "openai_chat", "base_url": "https://x"})
    assert dup.status_code == 409
    listing = (await client.get("/api/v1/providers")).text
    assert "sk-supersecret" not in listing
    ag = await client.post("/api/v1/agents", json={"name": "Remote", "kind": "openai_chat", "endpoint": {"url": "https://agent.example.com/v1", "api_key": "agent-secret-key"}})
    assert ag.status_code == 201 and "agent-secret-key" not in ag.text and ag.json()["endpoint"]["has_api_key"] is True
