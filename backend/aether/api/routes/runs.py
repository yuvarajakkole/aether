from __future__ import annotations

import csv
import io
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aether.api.schemas import ReviewIn, RunIn
from aether.api.serialize import run_out, task_out, trial_out
from aether.auth import Principal, get_principal
from aether.db import get_session
from aether.models import Job, Run, Task, Trial
from aether.services import runs as svc
from aether.services.audit import record
from aether.services.resolve import Invalid, NotFound, get_model

router = APIRouter(prefix="/api/v1", tags=["runs"])


async def load_run(db: AsyncSession, p: Principal, rid: str) -> Run:
    r = await db.get(Run, rid)
    if r is None or r.workspace_id != p.workspace.id:
        raise HTTPException(404, "run not found")
    return r


@router.post("/runs", status_code=201)
async def create_run(body: RunIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    try:
        run = await svc.create_run(db, p.workspace.id, body.model_dump(), p.actor)
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except Invalid as exc:
        raise HTTPException(422, str(exc)) from exc
    record(db, p, "create", "run", run.id, number=run.number, trials=run.config.get("trials"))
    await db.commit()
    return run_out(run)


@router.get("/runs")
async def list_runs(agent_id: str | None = None, limit: int = 50, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    q = select(Run).where(Run.workspace_id == p.workspace.id)
    if agent_id:
        q = q.where(Run.agent_id == agent_id)
    rows = (await db.execute(q.order_by(Run.created_at.desc()).limit(min(limit, 200)))).scalars().all()
    return [run_out(r, full=False) for r in rows]


@router.get("/runs/{rid}")
async def get_run(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    r = await load_run(db, p, rid)
    out = run_out(r)
    if r.status not in svc.TERMINAL:
        out["summary"] = await svc.compute_summary(db, r)
    return out


@router.post("/runs/{rid}/cancel")
async def cancel(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    r = await load_run(db, p, rid)
    await svc.cancel_run(db, r)
    record(db, p, "cancel", "run", r.id)
    await db.commit()
    return run_out(r, full=False)


@router.post("/runs/{rid}/retry-errors")
async def retry_errors(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Re-queue attempts that ended in an infrastructure error (e.g. provider outage) and reopen the run."""
    r = await load_run(db, p, rid)
    if r.status not in ("completed", "failed"):
        raise HTTPException(409, "wait for the run to finish before retrying errored attempts")
    res = await db.execute(
        update(Trial)
        .where(Trial.run_id == rid, Trial.status == "error")
        .values(status="queued", attempts=0, error=None, passed=None, checks=[], judge={}, trace=[], failure_category=None, lease_until=None, worker_id=None)
    )
    if res.rowcount:
        r.status = "running"
        r.finished_at = None
        r.error = None
    record(db, p, "retry_errors", "run", r.id, count=res.rowcount)
    await db.commit()
    return {"requeued": res.rowcount, "status": r.status}


@router.post("/runs/{rid}/rescore", status_code=202)
async def rescore(rid: str, judge_model_id: str | None = None, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Re-grade stored attempts with the current evaluators, optionally with another judge. Agents are not re-run."""
    r = await load_run(db, p, rid)
    if r.status != "completed":
        raise HTTPException(409, "only completed runs can be re-scored")
    if judge_model_id:
        try:
            await get_model(db, p.workspace.id, judge_model_id)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
    r.status = "analyzing"
    r.analyzing_since = svc.utcnow()
    job = Job(workspace_id=p.workspace.id, kind="rescore_run", payload={"run_id": r.id, "judge_model_id": judge_model_id})
    db.add(job)
    await db.flush()
    record(db, p, "rescore", "run", r.id, judge_model_id=judge_model_id)
    await db.commit()
    return {"job_id": job.id, "status": r.status}


@router.post("/runs/{rid}/reanalyze")
async def reanalyze(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Recompute metrics, gates and clusters (e.g. after human review changed verdicts)."""
    r = await load_run(db, p, rid)
    if r.status != "completed":
        raise HTTPException(409, "only completed runs can be re-analysed")
    await svc.finalize_run(db, r.id)
    await db.refresh(r)
    return run_out(r)


@router.get("/runs/{rid}/progress")
async def progress(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    r = await load_run(db, p, rid)
    rows = (await db.execute(select(Trial.variant, Trial.status, func.count()).where(Trial.run_id == rid).group_by(Trial.variant, Trial.status))).all()
    by: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total = defaultdict(int)
    for v, s, n in rows:
        by[v][s] += n
        total[s] += n
    return {"status": r.status, "total": dict(total), "variants": {k: dict(v) for k, v in by.items()}}


@router.get("/runs/{rid}/matrix")
async def matrix(rid: str, variant: str | None = None, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Task field: every task with its per-attempt outcomes for one variant."""
    r = await load_run(db, p, rid)
    variants = list((r.summary or {}).get("variants", {}).keys()) or [
        v for (v,) in (await db.execute(select(Trial.variant).where(Trial.run_id == rid).distinct())).all()
    ]
    if not variants:
        return {"variant": None, "tasks": []}
    variant = variant or variants[0]
    trials = (
        await db.execute(
            select(Trial.id, Trial.task_id, Trial.trial_index, Trial.status, Trial.passed, Trial.human_verdict, Trial.failure_category).where(
                Trial.run_id == rid, Trial.variant == variant
            )
        )
    ).all()
    tasks = {t.id: t for t in (await db.execute(select(Task).where(Task.id.in_({t.task_id for t in trials})))).scalars()}
    n = r.config.get("trials", 1)
    cells: dict[str, list] = {tid: [None] * n for tid in tasks}
    for t in trials:
        ok = (t.human_verdict == "pass") if t.human_verdict else t.passed
        state = "pass" if ok else ("fail" if t.status in ("done", "error") else t.status)
        if t.trial_index < n:
            cells[t.task_id][t.trial_index] = {"trial_id": t.id, "state": state, "failure": t.failure_category}
    out = []
    for tid, task in sorted(tasks.items(), key=lambda kv: kv[1].seq):
        out.append({"task_id": tid, "ref": f"T-{task.seq:03d}", "category": task.category, "input": task.input, "attempts": cells[tid]})
    return {"variant": variant, "variants": variants, "trials_per_task": n, "tasks": out}


@router.get("/runs/{rid}/trials")
async def list_trials(
    rid: str,
    variant: str | None = None,
    task_id: str | None = None,
    outcome: str | None = Query(default=None, pattern="^(pass|fail|error|review)$"),
    limit: int = 200,
    offset: int = 0,
    db: AsyncSession = Depends(get_session),
    p: Principal = Depends(get_principal),
):
    await load_run(db, p, rid)
    q = select(Trial).where(Trial.run_id == rid)
    if variant:
        q = q.where(Trial.variant == variant)
    if task_id:
        q = q.where(Trial.task_id == task_id)
    if outcome == "pass":
        q = q.where(Trial.passed.is_(True))
    elif outcome == "fail":
        q = q.where(Trial.passed.is_(False))
    elif outcome == "error":
        q = q.where(Trial.status == "error")
    elif outcome == "review":
        q = q.where(Trial.needs_review.is_(True), Trial.human_verdict.is_(None))
    rows = (await db.execute(q.order_by(Trial.task_id, Trial.trial_index).offset(offset).limit(min(limit, 1000)))).scalars().all()
    return [trial_out(t) for t in rows]


@router.get("/trials/{tid}")
async def get_trial(tid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    t = await db.get(Trial, tid)
    if t is None:
        raise HTTPException(404, "trial not found")
    await load_run(db, p, t.run_id)
    task = await db.get(Task, t.task_id)
    siblings = (
        await db.execute(select(Trial.id, Trial.trial_index, Trial.passed, Trial.human_verdict, Trial.status).where(Trial.run_id == t.run_id, Trial.task_id == t.task_id, Trial.variant == t.variant).order_by(Trial.trial_index))
    ).all()
    return {
        **trial_out(t, full=True),
        "task": task_out(task) if task else None,
        "attempts": [{"id": s.id, "index": s.trial_index, "passed": (s.human_verdict == "pass") if s.human_verdict else s.passed, "status": s.status} for s in siblings],
    }


@router.get("/review")
async def review_queue(limit: int = 100, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (
        await db.execute(
            select(Trial, Task, Run)
            .join(Run, Run.id == Trial.run_id)
            .join(Task, Task.id == Trial.task_id)
            .where(Run.workspace_id == p.workspace.id, Trial.needs_review.is_(True), Trial.human_verdict.is_(None))
            .order_by(Trial.finished_at.desc())
            .limit(min(limit, 500))
        )
    ).all()
    return [{**trial_out(t), "task_input": task.input, "category": task.category, "run_number": run.number, "agent_id": run.agent_id, "judge": t.judge, "checks": t.checks} for t, task, run in rows]


@router.post("/trials/{tid}/review")
async def review(tid: str, body: ReviewIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    t = await db.get(Trial, tid)
    if t is None:
        raise HTTPException(404, "trial not found")
    await load_run(db, p, t.run_id)
    t.human_verdict = body.verdict
    t.human_note = body.note
    t.reviewed_by = p.actor
    record(db, p, "review", "trial", t.id, verdict=body.verdict)
    await db.commit()
    return trial_out(t, full=True)


@router.get("/review/stats")
async def review_stats(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """How often humans agree with the judge: the judge's calibration."""
    rows = (
        await db.execute(select(Trial.passed, Trial.human_verdict).join(Run).where(Run.workspace_id == p.workspace.id, Trial.human_verdict.is_not(None)))
    ).all()
    agree = sum(1 for passed, hv in rows if (hv == "pass") == bool(passed))
    pending = (
        await db.execute(select(func.count()).select_from(Trial).join(Run).where(Run.workspace_id == p.workspace.id, Trial.needs_review.is_(True), Trial.human_verdict.is_(None)))
    ).scalar()
    return {"reviewed": len(rows), "agreement": agree / len(rows) if rows else None, "pending": pending}


@router.get("/runs/{base}/compare/{head}")
async def compare(base: str, head: str, base_variant: str | None = None, head_variant: str | None = None, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a, b = await load_run(db, p, base), await load_run(db, p, head)
    try:
        return await svc.compare_runs(db, a, b, base_variant, head_variant)
    except Invalid as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/runs/{rid}/gate")
async def gate(rid: str, variant: str | None = None, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    r = await load_run(db, p, rid)
    if r.status != "completed":
        return {"status": r.status, "passed": None, "done": r.status in svc.TERMINAL}
    variants = r.summary.get("variants", {})
    key = variant or (r.summary.get("recommendation") or {}).get("ship") or next(iter(variants), None)
    v = variants.get(key) if key else None
    if v is None:
        raise HTTPException(404, "variant not found in run")
    return {"status": r.status, "done": True, "variant": key, "model_name": v["model_name"], "passed": v["gate"]["passed"], "gate": v["gate"], "metrics": {k: v["metrics"].get(k) for k in ("pass_rate", "all_pass_rate", "latency_p95_ms", "cost_per_trial_usd")}}


@router.get("/runs/{rid}/export.csv")
async def export_csv(rid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    r = await load_run(db, p, rid)
    rows = (await db.execute(select(Trial, Task).join(Task, Task.id == Trial.task_id).where(Trial.run_id == rid).order_by(Task.seq, Trial.variant, Trial.trial_index))).all()
    variants = (r.summary or {}).get("variants", {})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["task_ref", "category", "input", "model", "prompt_version", "attempt", "status", "passed", "human_verdict", "failure", "score", "latency_ms", "input_tokens", "output_tokens", "cost_usd", "eval_cost_usd", "error"])
    for t, task in rows:
        v = variants.get(t.variant, {})
        w.writerow([f"T-{task.seq:03d}", task.category, task.input, v.get("model_name", t.variant), v.get("version"), t.trial_index + 1, t.status, t.passed, t.human_verdict, t.failure_category, t.score, t.latency_ms, t.input_tokens, t.output_tokens, f"{t.cost_usd:.6f}", f"{t.eval_cost_usd:.6f}", t.error])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="aether-run-{r.number}.csv"'})
