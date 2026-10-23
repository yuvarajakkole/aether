from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.api.routes.agents import load_agent
from aether.api.schemas import CIRunIn
from aether.api.serialize import run_out
from aether.auth import Principal, get_principal
from aether.db import get_session
from aether.models import Suite
from aether.services import runs as svc
from aether.services.audit import record
from aether.services.resolve import Invalid, NotFound, find_model_by_ref

router = APIRouter(prefix="/api/v1/ci", tags=["ci"])


@router.post("/runs", status_code=201)
async def ci_run(body: CIRunIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Start a run by names instead of ids; poll GET /api/v1/runs/{id}/gate for the verdict."""
    agent = await load_agent(db, p, body.agent)
    q = select(Suite).where(Suite.agent_id == agent.id, Suite.status == "ready")
    if body.suite:
        q = q.where((Suite.id == body.suite) | (Suite.name == body.suite))
    suite = (await db.execute(q.order_by(Suite.created_at.desc()))).scalars().first()
    if suite is None:
        raise HTTPException(404, "no ready suite found for this agent")
    model_ids = []
    for ref in body.models:
        m = await find_model_by_ref(db, p.workspace.id, ref)
        if m is None:
            raise HTTPException(404, f"model '{ref}' not found")
        model_ids.append(m.id)
    version_ids = []
    if body.version is not None:
        v = next((v for v in agent.versions if v.version == body.version), None)
        if v is None:
            raise HTTPException(404, f"agent has no version {body.version}")
        version_ids = [v.id]
    try:
        run = await svc.create_run(
            db,
            p.workspace.id,
            {
                "agent_id": agent.id,
                "suite_id": suite.id,
                "model_ids": model_ids,
                "version_ids": version_ids,
                "trials": body.trials,
                "tool_mode": body.tool_mode,
                "failure_rate": body.failure_rate,
                "name": body.name or f"CI: {agent.name}",
            },
            p.actor,
        )
    except NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except Invalid as exc:
        raise HTTPException(422, str(exc)) from exc
    record(db, p, "create", "run", run.id, via="ci")
    await db.commit()
    return {**run_out(run, full=False), "gate_url": f"/api/v1/runs/{run.id}/gate"}
