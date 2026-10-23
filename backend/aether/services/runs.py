"""Run lifecycle: create -> trials queued -> workers execute -> finalize (metrics, gates, clusters)."""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from aether.engine.clusters import cluster_failures
from aether.engine.evaluate import evaluate
from aether.engine.metrics import effective_pass, evaluate_gate, variant_metrics
from aether.engine.runner import TrialSpec, run_trial
from aether.models import Agent, AgentVersion, LLMModel, Run, Suite, Task, Trial, new_id
from aether.providers.base import ModelConfig, ProviderError
from aether.providers.registry import client_for, client_from_config
from aether.services.agents import runtime_endpoint
from aether.services.resolve import Invalid, NotFound, get_model, role_client

log = logging.getLogger(__name__)

TERMINAL = ("completed", "failed", "cancelled")
MAX_TRIALS_PER_RUN = 50_000


def utcnow() -> datetime:
    return datetime.now(UTC)


def variant_key(model_id: str | None, version_id: str | None) -> str:
    return f"{model_id or 'external'}:{version_id or 'none'}"


async def create_run(db: AsyncSession, workspace_id: str, body: dict[str, Any], actor: str) -> Run:
    agent = (
        await db.execute(select(Agent).options(joinedload(Agent.versions)).where(Agent.id == body["agent_id"], Agent.workspace_id == workspace_id))
    ).unique().scalar_one_or_none()
    if agent is None:
        raise NotFound("agent not found")
    suite = (await db.execute(select(Suite).where(Suite.id == body["suite_id"], Suite.workspace_id == workspace_id))).scalar_one_or_none()
    if suite is None or suite.agent_id != agent.id:
        raise NotFound("suite not found for this agent")
    tq = select(Task).where(Task.suite_id == suite.id, Task.enabled.is_(True))
    if body.get("task_ids"):
        tq = tq.where(Task.id.in_(body["task_ids"]))
    if body.get("categories"):
        tq = tq.where(Task.category.in_(body["categories"]))
    tasks = (await db.execute(tq.order_by(Task.seq))).scalars().all()
    if not tasks:
        raise Invalid("the suite has no enabled tasks matching this run")

    versions = agent.versions
    version_ids = body.get("version_ids") or ([versions[-1].id] if versions else [])
    known_versions = {v.id for v in versions}
    if any(v not in known_versions for v in version_ids):
        raise Invalid("unknown agent version")
    if agent.kind == "http":
        model_ids: list[str | None] = [None]
        version_ids = version_ids[:1] or [None]  # type: ignore[list-item]
    elif agent.kind == "openai_chat":
        model_ids = [None]
    else:
        model_ids = list(body.get("model_ids") or [])
        if not model_ids:
            defaults = [v.default_model_id for v in versions if v.id in version_ids and v.default_model_id]
            model_ids = defaults[:1]
        if not model_ids:
            raise Invalid("choose at least one model")
        for mid in model_ids:
            m = await get_model(db, workspace_id, mid)
            if not m.enabled:
                raise Invalid(f"model {m.name} is disabled")
    trials_per_task = int(body.get("trials", 3))
    if not 1 <= trials_per_task <= 20:
        raise Invalid("trials must be between 1 and 20")
    total = len(tasks) * len(model_ids) * max(1, len(version_ids)) * trials_per_task
    if total > MAX_TRIALS_PER_RUN:
        raise Invalid(f"run would create {total} trials; the limit is {MAX_TRIALS_PER_RUN}")
    for role in ("judge", "simulator"):
        mid = body.get(f"{role}_model_id")
        if mid:
            await get_model(db, workspace_id, mid)

    number = ((await db.execute(select(func.max(Run.number)).where(Run.workspace_id == workspace_id))).scalar() or 0) + 1
    config = {
        "model_ids": model_ids,
        "version_ids": version_ids,
        "trials": trials_per_task,
        "tool_mode": body.get("tool_mode", "simulated"),
        "failure_rate": float(body.get("failure_rate", 0.0)),
        "judge_model_id": body.get("judge_model_id"),
        "simulator_model_id": body.get("simulator_model_id"),
        "auto_reply": bool(body.get("auto_reply", True)),
        "seed": body.get("seed") or new_id()[:8],
        "task_count": len(tasks),
    }
    if config["tool_mode"] not in ("simulated", "real"):
        raise Invalid("tool_mode must be 'simulated' or 'real'")
    if not 0 <= config["failure_rate"] <= 1:
        raise Invalid("failure_rate must be between 0 and 1")
    run = Run(
        workspace_id=workspace_id,
        number=number,
        agent_id=agent.id,
        suite_id=suite.id,
        name=body.get("name") or f"{agent.name} run {number}",
        status="queued",
        config=config,
        created_by=actor,
    )
    db.add(run)
    await db.flush()
    rows = []
    for task, mid, vid in itertools.product(tasks, model_ids, version_ids or [None]):
        for k in range(trials_per_task):
            rows.append(
                {
                    "id": new_id(),
                    "run_id": run.id,
                    "task_id": task.id,
                    "variant": variant_key(mid, vid),
                    "model_id": mid,
                    "version_id": vid,
                    "trial_index": k,
                    "status": "queued",
                    "created_at": utcnow(),
                }
            )
    for i in range(0, len(rows), 2000):
        await db.execute(Trial.__table__.insert(), rows[i : i + 2000])
    await db.commit()
    await db.refresh(run)
    return run


async def cancel_run(db: AsyncSession, run: Run) -> None:
    if run.status in TERMINAL:
        return
    await db.execute(update(Trial).where(Trial.run_id == run.id, Trial.status == "queued").values(status="cancelled"))
    run.status = "cancelled"
    run.finished_at = utcnow()
    await db.commit()


async def process_trial(db: AsyncSession, trial_id: str) -> None:
    trial = await db.get(Trial, trial_id)
    if trial is None:
        return
    run = await db.get(Run, trial.run_id)
    task = await db.get(Task, trial.task_id)
    if run is None or task is None:
        return
    if run.status in TERMINAL:
        trial.status = "cancelled"
        await db.commit()
        return
    agent = await db.get(Agent, run.agent_id)
    version = await db.get(AgentVersion, trial.version_id) if trial.version_id else None
    cfg = run.config
    ws = run.workspace_id

    agent_client = None
    endpoint = runtime_endpoint(agent.endpoint or {})
    if agent.kind == "builtin":
        model = await get_model(db, ws, trial.model_id)
        agent_client = client_for(model)
    elif agent.kind == "openai_chat":
        ep = endpoint
        agent_client = client_from_config(
            ModelConfig(
                provider_kind="openai_chat",
                provider_name=f"agent:{agent.slug}",
                model=ep.get("model") or "agent",
                base_url=ep.get("url"),
                api_key=ep.get("api_key"),
                headers=ep.get("headers") or {},
                provider_options={"auth_style": "bearer" if ep.get("api_key") else "none", "json_mode": False},
            )
        )
    simulator, _ = await role_client(db, ws, "simulator", cfg.get("simulator_model_id"))
    judge, _ = await role_client(db, ws, "judge", cfg.get("judge_model_id"))

    tools = (version.tools if version else []) or []
    system_prompt = (version.system_prompt if version else "") or ""
    params = dict(version.params or {}) if version else {}
    spec = TrialSpec(
        agent_kind=agent.kind,
        system_prompt=system_prompt,
        tools=tools,
        params=params,
        endpoint=endpoint,
        task_input=task.input,
        scenario=task.scenario,
        max_turns=task.max_turns or 1,
        fixtures=task.fixtures or {},
        inject=task.inject or {},
        tool_mode=cfg.get("tool_mode", "simulated"),
        failure_rate=float(cfg.get("failure_rate", 0.0)),
        seed=f"{cfg.get('seed')}:{task.id}:{trial.trial_index}",
        agent_client=agent_client,
        simulator=simulator,
        trial_id=trial.id,
        task_id=task.id,
        run_id=run.id,
        auto_reply=bool(cfg.get("auto_reply", True)),
    )
    outcome = await run_trial(spec)
    try:
        ev = await evaluate(
            outcome,
            expected=task.expected or {},
            tools=tools,
            system_prompt=system_prompt,
            task_input=task.input,
            scenario=task.scenario,
            judge=judge,
            external=agent.kind == "http",
        )
    except ProviderError as exc:  # judge transport failure after retries
        outcome.error = outcome.error or f"evaluation failed: {exc}"
        ev = None
    trial.trace = outcome.trace
    trial.output = outcome.output
    trial.input_tokens = outcome.input_tokens
    trial.output_tokens = outcome.output_tokens
    trial.cost_usd = outcome.cost_usd
    trial.latency_ms = outcome.latency_ms
    trial.error = outcome.error
    if ev is not None:
        trial.passed = ev.passed
        trial.score = ev.score
        trial.checks = [c.as_dict() for c in ev.checks]
        trial.judge = ev.judge
        trial.failure_category = ev.failure_category
        trial.needs_review = ev.needs_review
        trial.eval_cost_usd = outcome.eval_cost_usd + ev.eval_cost_usd
    else:
        trial.passed = False
        trial.failure_category = "Evaluation failed"
        trial.eval_cost_usd = outcome.eval_cost_usd
    trial.status = "error" if (outcome.error and outcome.error.startswith("provider error")) else "done"
    trial.finished_at = utcnow()
    trial.lease_until = None
    await db.commit()


def outcome_from_trial(trial: Trial):
    from aether.engine.runner import TrialOutcome

    trace = [s for s in (trial.trace or []) if s.get("type") != "summary"]
    return TrialOutcome(trace=trace, output=trial.output, input_tokens=trial.input_tokens, output_tokens=trial.output_tokens, cost_usd=trial.cost_usd, latency_ms=trial.latency_ms or 0, error=trial.error)


async def run_rescore_job(db: AsyncSession, job) -> dict[str, Any]:
    """Re-evaluate stored attempts with the current evaluators (and optionally another judge) without re-running agents."""
    run = await db.get(Run, job.payload["run_id"])
    if run is None:
        raise Invalid("run was deleted")
    judge_id = job.payload.get("judge_model_id") or run.config.get("judge_model_id")
    judge, judge_model = await role_client(db, run.workspace_id, "judge", judge_id)
    agent = await db.get(Agent, run.agent_id)
    trials = (await db.execute(select(Trial).where(Trial.run_id == run.id, Trial.status == "done"))).scalars().all()
    tasks = {t.id: t for t in (await db.execute(select(Task).where(Task.id.in_({t.task_id for t in trials})))).scalars()}
    version_ids = {t.version_id for t in trials if t.version_id}
    versions = {v.id: v for v in (await db.execute(select(AgentVersion).where(AgentVersion.id.in_(version_ids)))).scalars()} if version_ids else {}
    changed = 0
    sem = asyncio.Semaphore(8)

    async def one(t: Trial) -> tuple[Trial, Any]:
        # Only LLM calls run concurrently; the session is not touched inside this coroutine.
        task = tasks[t.task_id]
        version = versions.get(t.version_id) if t.version_id else None
        async with sem:
            ev = await evaluate(
                outcome_from_trial(t),
                expected=task.expected or {},
                tools=(version.tools if version else []) or [],
                system_prompt=(version.system_prompt if version else "") or "",
                task_input=task.input,
                scenario=task.scenario,
                judge=judge,
                external=agent.kind == "http",
            )
        return t, ev

    for t, ev in await asyncio.gather(*(one(t) for t in trials)):
        if ev.passed != t.passed:
            changed += 1
        t.passed, t.score, t.checks, t.judge = ev.passed, ev.score, [c.as_dict() for c in ev.checks], ev.judge
        t.failure_category, t.needs_review = ev.failure_category, ev.needs_review
        t.eval_cost_usd = (t.eval_cost_usd or 0) + ev.eval_cost_usd
    if judge_id:
        run.config = {**run.config, "judge_model_id": judge_id}
    await db.commit()
    await finalize_run(db, run.id)
    return {"rescored": len(trials), "verdicts_changed": changed, "judge": judge_model.name if judge_model else None}


async def trial_rows(db: AsyncSession, run_id: str) -> list[dict[str, Any]]:
    q = (
        select(
            Trial.id, Trial.task_id, Trial.variant, Trial.status, Trial.passed, Trial.human_verdict, Trial.checks, Trial.latency_ms,
            Trial.cost_usd, Trial.eval_cost_usd, Trial.input_tokens, Trial.output_tokens, Trial.failure_category, Trial.error,
            Trial.needs_review, Trial.trial_index, Trial.judge, Task.category,
        )
        .join(Task, Task.id == Trial.task_id)
        .where(Trial.run_id == run_id)
    )
    return [dict(r._mapping) for r in (await db.execute(q)).all()]


async def describe_variants(db: AsyncSession, run: Run) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    agent = await db.get(Agent, run.agent_id)
    for mid, vid in itertools.product(run.config.get("model_ids") or [None], run.config.get("version_ids") or [None]):
        key = variant_key(mid, vid)
        model = await db.get(LLMModel, mid) if mid else None
        version = await db.get(AgentVersion, vid) if vid else None
        provider_name = None
        if model is not None:
            await db.refresh(model, ["provider"])
            provider_name = model.provider.name
        out[key] = {
            "key": key,
            "model_id": mid,
            "model_name": (model.display_name or model.name) if model else agent.name,
            "provider": provider_name or ("external" if agent.kind != "builtin" else None),
            "version_id": vid,
            "version": version.version if version else None,
            "version_label": version.label if version else None,
            "input_cost_per_mtok": model.input_cost_per_mtok if model else None,
            "output_cost_per_mtok": model.output_cost_per_mtok if model else None,
        }
    return out


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def recommendation(variants: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = [v for v in variants.values() if v["metrics"].get("trials")]
    if not items:
        return {"headline": "No attempts finished.", "picks": {}}

    def name(v: dict[str, Any]) -> str:
        n = v["model_name"]
        if len({x["version"] for x in items}) > 1 and v.get("version"):
            n += f" (prompt v{v['version']})"
        return n

    by_first = max(items, key=lambda v: v["metrics"]["pass_rate"] or 0)
    by_dep = max(items, key=lambda v: ((v["metrics"]["all_pass_rate"] or 0), -(v["metrics"]["cost_per_trial_usd"] or 0)))
    passing = [v for v in items if v["gate"]["passed"]]
    cheapest = min(passing, key=lambda v: v["metrics"]["cost_per_trial_usd"] or 0) if passing else None
    fastest = min(items, key=lambda v: v["metrics"]["latency_p95_ms"] or float("inf"))
    picks = {
        "best_first_try": {"variant": by_first["key"], "value": by_first["metrics"]["pass_rate"]},
        "most_dependable": {"variant": by_dep["key"], "value": by_dep["metrics"]["all_pass_rate"]},
        "fastest": {"variant": fastest["key"], "value": fastest["metrics"]["latency_p95_ms"]},
        "cheapest_passing": {"variant": cheapest["key"], "value": cheapest["metrics"]["cost_per_trial_usd"]} if cheapest else None,
    }
    k = by_dep["metrics"].get("attempts_per_task") or 1
    if len(items) == 1:
        v = items[0]
        verdict = "clears" if v["gate"]["passed"] else "does not clear"
        headline = f"{name(v)} passed {_pct(v['metrics']['pass_rate'])} of attempts and solved {_pct(v['metrics']['all_pass_rate'])} of tasks on all {k} attempts. It {verdict} the release gate."
        ship = v["key"] if v["gate"]["passed"] else None
    elif not passing:
        headline = f"No variant clears the release gate yet. {name(by_dep)} is closest, solving {_pct(by_dep['metrics']['all_pass_rate'])} of tasks on every attempt."
        ship = None
    else:
        best = max(passing, key=lambda v: ((v["metrics"]["all_pass_rate"] or 0), -(v["metrics"]["cost_per_trial_usd"] or 0)))
        ship = best["key"]
        if best["key"] == by_first["key"]:
            headline = f"Ship {name(best)}. It leads on first-attempt success and on consistency across {k} attempts."
        else:
            headline = f"Ship {name(best)}. {name(by_first)} wins more attempts, but {name(best)} is the one that wins them every time."
    return {"headline": headline, "ship": ship, "picks": picks}


async def compute_summary(db: AsyncSession, run: Run) -> dict[str, Any]:
    rows = await trial_rows(db, run.id)
    agent = await db.get(Agent, run.agent_id)
    variants = await describe_variants(db, run)
    by_variant: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_variant[r["variant"]].append(r)
    for key, v in variants.items():
        m = variant_metrics(by_variant.get(key, []))
        v["metrics"] = m
        v["gate"] = evaluate_gate(m, agent.gate)
    status_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        status_counts[r["status"]] += 1
    summary = {
        "variants": variants,
        "progress": {"total": len(rows), **status_counts},
        "totals": {
            "cost_usd": sum(r["cost_usd"] or 0 for r in rows),
            "eval_cost_usd": sum(r["eval_cost_usd"] or 0 for r in rows),
            "trials": len(rows),
            "passed": sum(1 for r in rows if effective_pass(r)),
        },
        "recommendation": recommendation(variants),
    }
    return summary


async def finalize_run(db: AsyncSession, run_id: str) -> None:
    run = await db.get(Run, run_id)
    if run is None:
        return
    summary = await compute_summary(db, run)
    rows = await trial_rows(db, run.id)
    failures = []
    for r in rows:
        if r["status"] == "done" and not effective_pass(r):
            failed = [c for c in (r["checks"] or []) if not c["passed"]]
            failures.append(
                {
                    "id": r["id"],
                    "variant": r["variant"],
                    "category": r["failure_category"],
                    "failed_checks": [f"{c['name']}: {c['detail'][:160]}" for c in failed][:3],
                    "judge": ((r["judge"] or {}).get("reasoning") or "")[:300],
                    "error": (r["error"] or "")[:200],
                }
            )
    client, _ = await role_client(db, run.workspace_id, "judge", run.config.get("judge_model_id"))
    clusters, cost = await cluster_failures(client, failures)
    variant_of = {f["id"]: f["variant"] for f in failures}
    for c in clusters:
        counts: dict[str, int] = defaultdict(int)
        for tid in c["trial_ids"]:
            counts[variant_of.get(tid, "?")] += 1
        c["by_variant"] = dict(counts)
    summary["totals"]["eval_cost_usd"] += cost
    run.summary = summary
    run.failure_clusters = clusters
    run.status = "completed" if summary["progress"].get("done", 0) + summary["progress"].get("error", 0) > 0 else "failed"
    if run.status == "failed":
        run.error = "no trials finished"
    run.finished_at = utcnow()
    await db.commit()


async def maybe_finish(db: AsyncSession, run_id: str) -> bool:
    """If no trials remain, atomically move the run to 'analyzing' and finalize. Safe under concurrency."""
    remaining = (
        await db.execute(select(func.count()).select_from(Trial).where(Trial.run_id == run_id, Trial.status.in_(("queued", "running"))))
    ).scalar()
    if remaining:
        return False
    res = await db.execute(
        update(Run).where(Run.id == run_id, Run.status.in_(("queued", "running"))).values(status="analyzing", analyzing_since=utcnow())
    )
    await db.commit()
    if res.rowcount != 1:
        return False
    try:
        await finalize_run(db, run_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("finalize failed", extra={"run_id": run_id})
        await db.rollback()
        await db.execute(update(Run).where(Run.id == run_id).values(status="failed", error=f"analysis failed: {exc}"[:2000], finished_at=utcnow()))
        await db.commit()
    return True


async def compare_runs(db: AsyncSession, base: Run, head: Run, base_variant: str | None, head_variant: str | None) -> dict[str, Any]:
    if base.suite_id != head.suite_id:
        raise Invalid("runs use different test suites; compare runs of the same suite")
    bv = base_variant or next(iter(base.summary.get("variants", {})), None)
    head_keys = list(head.summary.get("variants", {}))
    if head_variant:
        hv = head_variant
    elif base.id == head.id:
        hv = next((k for k in head_keys if k != bv), bv)
    else:
        same_model = [k for k in head_keys if bv and k.split(":")[0] == bv.split(":")[0]]
        hv = (same_model or head_keys or [None])[0]

    async def task_rates(run: Run, v: str | None) -> dict[str, tuple[int, int]]:
        rows = [r for r in await trial_rows(db, run.id) if r["variant"] == v and r["status"] in ("done", "error")]
        acc: dict[str, list[bool]] = defaultdict(list)
        for r in rows:
            acc[r["task_id"]].append(effective_pass(r))
        return {k: (sum(x), len(x)) for k, x in acc.items()}

    a, b = await task_rates(base, bv), await task_rates(head, hv)
    tasks = {t.id: t for t in (await db.execute(select(Task).where(Task.id.in_(set(a) | set(b))))).scalars()}
    regressions, improvements, unchanged = [], [], 0
    for tid in sorted(set(a) & set(b)):
        ra, rb = a[tid][0] / a[tid][1], b[tid][0] / b[tid][1]
        item = {"task_id": tid, "input": tasks[tid].input, "category": tasks[tid].category, "base": a[tid], "head": b[tid]}
        if rb < ra:
            regressions.append(item)
        elif rb > ra:
            improvements.append(item)
        else:
            unchanged += 1
    regressions.sort(key=lambda i: (i["head"][0] / i["head"][1]) - (i["base"][0] / i["base"][1]))
    improvements.sort(key=lambda i: (i["base"][0] / i["base"][1]) - (i["head"][0] / i["head"][1]))
    bm = (base.summary.get("variants", {}).get(bv) or {}).get("metrics", {})
    hm = (head.summary.get("variants", {}).get(hv) or {}).get("metrics", {})
    deltas = {
        k: (hm.get(k) - bm.get(k)) if isinstance(hm.get(k), (int, float)) and isinstance(bm.get(k), (int, float)) else None
        for k in ("pass_rate", "all_pass_rate", "argument_accuracy", "tool_selection", "recovery_rate", "latency_p95_ms", "cost_per_trial_usd")
    }
    return {
        "base": {"run_id": base.id, "number": base.number, "variant": bv, "metrics": bm},
        "head": {"run_id": head.id, "number": head.number, "variant": hv, "metrics": hm},
        "deltas": deltas,
        "regressions": regressions,
        "improvements": improvements,
        "unchanged": unchanged,
    }
