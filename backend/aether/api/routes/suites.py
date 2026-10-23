from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.api.routes.agents import load_agent
from aether.api.schemas import GenerateIn, ImportIn, SuiteIn, TaskIn, TaskPatch
from aether.api.serialize import suite_out, task_out
from aether.auth import Principal, get_principal
from aether.db import get_session
from aether.engine.generator import CATEGORIES
from aether.models import AgentVersion, Job, Suite, Task
from aether.services.audit import record
from aether.services.resolve import Invalid, NotFound, get_model
from aether.services.suites import add_tasks, normalise_import, validate_mix

router = APIRouter(prefix="/api/v1/suites", tags=["suites"])


async def _suite(db: AsyncSession, p: Principal, sid: str) -> Suite:
    s = await db.get(Suite, sid)
    if s is None or s.workspace_id != p.workspace.id:
        raise HTTPException(404, "suite not found")
    return s


async def _counts(db: AsyncSession, sid: str) -> tuple[int, dict[str, int]]:
    cats = (await db.execute(select(Task.category).where(Task.suite_id == sid, Task.enabled.is_(True)))).scalars().all()
    return len(cats), dict(Counter(cats))


@router.get("")
async def list_suites(agent_id: str | None = None, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    q = select(Suite).where(Suite.workspace_id == p.workspace.id)
    if agent_id:
        q = q.where(Suite.agent_id == agent_id)
    out = []
    for s in (await db.execute(q.order_by(Suite.created_at.desc()))).scalars():
        n, cats = await _counts(db, s.id)
        out.append(suite_out(s, n, cats))
    return out


@router.post("", status_code=201)
async def create_suite(body: SuiteIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    agent = await load_agent(db, p, body.agent_id)
    s = Suite(workspace_id=p.workspace.id, agent_id=agent.id, name=body.name, description=body.description, status="ready" if body.tasks else "draft")
    db.add(s)
    await db.flush()
    await add_tasks(db, s, [t.model_dump() for t in body.tasks])
    record(db, p, "create", "suite", s.id, name=s.name, tasks=len(body.tasks))
    await db.commit()
    n, cats = await _counts(db, s.id)
    return suite_out(s, n, cats)


@router.get("/{sid}")
async def get_suite(sid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    tasks = (await db.execute(select(Task).where(Task.suite_id == sid).order_by(Task.seq))).scalars().all()
    n, cats = await _counts(db, s.id)
    return {**suite_out(s, n, cats), "tasks": [task_out(t) for t in tasks]}


@router.delete("/{sid}", status_code=204)
async def delete_suite(sid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    record(db, p, "delete", "suite", s.id, name=s.name)
    await db.delete(s)
    await db.commit()


@router.post("/{sid}/generate", status_code=202)
async def generate(sid: str, body: GenerateIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    if s.status == "generating":
        raise HTTPException(409, "generation already in progress")
    try:
        mix = validate_mix(body.mix)
        if body.generator_model_id:
            await get_model(db, p.workspace.id, body.generator_model_id)
    except (Invalid, NotFound) as exc:
        raise HTTPException(422, str(exc)) from exc
    if body.version_id:
        v = await db.get(AgentVersion, body.version_id)
        if v is None or v.agent_id != s.agent_id:
            raise HTTPException(422, "version does not belong to this suite's agent")
    s.status = "generating"
    s.error = None
    s.generation = {"mix": mix, "instructions": body.instructions, "requested": sum(mix.values())}
    job = Job(workspace_id=p.workspace.id, kind="generate_suite", payload={"suite_id": s.id, **body.model_dump(), "mix": mix})
    db.add(job)
    await db.flush()
    record(db, p, "generate", "suite", s.id, mix=mix)
    await db.commit()
    return {"job_id": job.id, "suite": suite_out(s)}


@router.post("/{sid}/tasks", status_code=201)
async def add_task(sid: str, body: TaskIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    if body.category not in CATEGORIES:
        raise HTTPException(422, f"category must be one of {', '.join(CATEGORIES)}")
    await add_tasks(db, s, [body.model_dump()])
    s.status = "ready"
    await db.commit()
    t = (await db.execute(select(Task).where(Task.suite_id == sid).order_by(Task.seq.desc()))).scalars().first()
    return task_out(t)


@router.post("/{sid}/import")
async def import_tasks(sid: str, body: ImportIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    agent = await load_agent(db, p, s.agent_id)
    tools = agent.versions[-1].tools if agent.versions else []
    good, skipped = [], 0
    for raw in body.tasks:
        t = normalise_import(raw, tools) if isinstance(raw, dict) else None
        if t:
            good.append(t)
        else:
            skipped += 1
    n = await add_tasks(db, s, good)
    if n:
        s.status = "ready"
    record(db, p, "import", "suite", s.id, imported=n, skipped=skipped)
    await db.commit()
    return {"imported": n, "skipped": skipped}


@router.get("/{sid}/export")
async def export_tasks(sid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    s = await _suite(db, p, sid)
    tasks = (await db.execute(select(Task).where(Task.suite_id == sid).order_by(Task.seq))).scalars().all()
    keep = ("category", "input", "scenario", "max_turns", "fixtures", "expected", "inject", "tags", "enabled")
    return {"suite": s.name, "tasks": [{k: task_out(t)[k] for k in keep} for t in tasks]}


@router.patch("/{sid}/tasks/{tid}")
async def update_task(sid: str, tid: str, body: TaskPatch, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    await _suite(db, p, sid)
    t = await db.get(Task, tid)
    if t is None or t.suite_id != sid:
        raise HTTPException(404, "task not found")
    data = body.model_dump(exclude_unset=True)
    if "category" in data and data["category"] not in CATEGORIES:
        raise HTTPException(422, "unknown category")
    for k, v in data.items():
        setattr(t, k, v)
    await db.commit()
    return task_out(t)


@router.delete("/{sid}/tasks/{tid}", status_code=204)
async def delete_task(sid: str, tid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    await _suite(db, p, sid)
    t = await db.get(Task, tid)
    if t is None or t.suite_id != sid:
        raise HTTPException(404, "task not found")
    await db.delete(t)
    await db.commit()
