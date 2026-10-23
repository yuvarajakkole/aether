from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from aether.api.schemas import AgentIn, AgentPatch, VersionIn
from aether.api.serialize import agent_out, version_out
from aether.auth import Principal, get_principal
from aether.db import get_session
from aether.models import Agent, AgentVersion, Run, Suite
from aether.services.agents import seal_endpoint, slugify
from aether.services.audit import record

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def load_agent(db: AsyncSession, p: Principal, ref: str) -> Agent:
    a = (
        await db.execute(
            select(Agent).options(selectinload(Agent.versions)).where(Agent.workspace_id == p.workspace.id, (Agent.id == ref) | (Agent.slug == ref))
        )
    ).scalar_one_or_none()
    if a is None:
        raise HTTPException(404, "agent not found")
    return a


def _check_endpoint(kind: str, endpoint: dict) -> None:
    if kind in ("http", "openai_chat") and not (endpoint or {}).get("url"):
        raise HTTPException(422, f"agents of kind '{kind}' need endpoint.url")


def _best_variant(run: Run) -> dict | None:
    variants = [v for v in (run.summary or {}).get("variants", {}).values() if v.get("metrics")]
    if not variants:
        return None
    ship = (run.summary.get("recommendation") or {}).get("ship")
    chosen = next((v for v in variants if v["key"] == ship), None) or max(variants, key=lambda v: v["metrics"].get("all_pass_rate") or 0)
    return chosen


@router.get("")
async def list_agents(include_archived: bool = False, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    q = select(Agent).options(selectinload(Agent.versions)).where(Agent.workspace_id == p.workspace.id)
    if not include_archived:
        q = q.where(Agent.archived.is_(False))
    agents = (await db.execute(q.order_by(Agent.created_at))).scalars().all()
    out = []
    for a in agents:
        runs = (
            await db.execute(select(Run).where(Run.agent_id == a.id, Run.status == "completed").order_by(Run.finished_at.desc()).limit(12))
        ).scalars().all()
        last_any = (await db.execute(select(Run).where(Run.agent_id == a.id).order_by(Run.created_at.desc()).limit(1))).scalar_one_or_none()
        trend = []
        for r in reversed(runs):
            b = _best_variant(r)
            if b:
                trend.append({"run_id": r.id, "number": r.number, "all_pass_rate": b["metrics"].get("all_pass_rate"), "pass_rate": b["metrics"].get("pass_rate")})
        latest = _best_variant(runs[0]) if runs else None
        suites = (await db.execute(select(func.count()).select_from(Suite).where(Suite.agent_id == a.id))).scalar()
        v = a.versions[-1] if a.versions else None
        out.append(
            agent_out(
                a,
                extra={
                    "latest_version": version_out(v) if v else None,
                    "version_count": len(a.versions),
                    "suite_count": suites,
                    "latest_run": {
                        "id": runs[0].id,
                        "number": runs[0].number,
                        "finished_at": runs[0].finished_at,
                        "variant": latest["key"] if latest else None,
                        "model_name": latest["model_name"] if latest else None,
                        "metrics": {k: latest["metrics"].get(k) for k in ("pass_rate", "all_pass_rate", "latency_p95_ms", "cost_per_trial_usd")} if latest else None,
                        "gate": latest["gate"] if latest else None,
                    }
                    if runs
                    else None,
                    "last_activity": {"run_id": last_any.id, "status": last_any.status, "created_at": last_any.created_at} if last_any else None,
                    "trend": trend,
                },
            )
        )
    return out


@router.post("", status_code=201)
async def create_agent(body: AgentIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    _check_endpoint(body.kind, body.endpoint)
    slug = slugify(body.slug or body.name)
    if (await db.execute(select(Agent).where(Agent.workspace_id == p.workspace.id, Agent.slug == slug))).scalar_one_or_none():
        raise HTTPException(409, f"an agent with slug '{slug}' already exists")
    a = Agent(
        workspace_id=p.workspace.id,
        name=body.name,
        slug=slug,
        description=body.description,
        kind=body.kind,
        endpoint=seal_endpoint(body.endpoint),
        owner=body.owner or p.actor,
        gate=body.gate.model_dump() if body.gate else {},
    )
    db.add(a)
    await db.flush()
    v = AgentVersion(agent_id=a.id, version=1, **(body.version or VersionIn()).model_dump(mode="json"))
    db.add(v)
    record(db, p, "create", "agent", a.id, name=a.name, kind=a.kind)
    await db.commit()
    return agent_out(await load_agent(db, p, a.id), (await load_agent(db, p, a.id)).versions)


@router.get("/{ref}")
async def get_agent(ref: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a = await load_agent(db, p, ref)
    return agent_out(a, a.versions)


@router.patch("/{ref}")
async def update_agent(ref: str, body: AgentPatch, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a = await load_agent(db, p, ref)
    data = body.model_dump(exclude_unset=True)
    if "endpoint" in data:
        merged = {**{k: v for k, v in (a.endpoint or {}).items() if k not in ("secrets_enc", "has_api_key", "header_names")}, **(data.pop("endpoint") or {})}
        _check_endpoint(a.kind, merged)
        a.endpoint = seal_endpoint(merged, a.endpoint)
    if "gate" in data:
        a.gate = data.pop("gate") or {}
    for k, v in data.items():
        setattr(a, k, v)
    record(db, p, "update", "agent", a.id, fields=sorted(body.model_dump(exclude_unset=True)))
    await db.commit()
    return agent_out(a, a.versions)


@router.delete("/{ref}", status_code=204)
async def delete_agent(ref: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a = await load_agent(db, p, ref)
    record(db, p, "delete", "agent", a.id, name=a.name)
    await db.delete(a)
    await db.commit()


@router.post("/{ref}/versions", status_code=201)
async def create_version(ref: str, body: VersionIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a = await load_agent(db, p, ref)
    n = (max((v.version for v in a.versions), default=0)) + 1
    v = AgentVersion(agent_id=a.id, version=n, **body.model_dump(mode="json"))
    db.add(v)
    record(db, p, "create", "agent_version", a.id, version=n)
    await db.commit()
    return version_out(v)


@router.post("/{ref}/baseline/{run_id}")
async def set_baseline(ref: str, run_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    a = await load_agent(db, p, ref)
    r = await db.get(Run, run_id)
    if r is None or r.agent_id != a.id or r.status != "completed":
        raise HTTPException(422, "baseline must be a completed run of this agent")
    a.baseline_run_id = r.id
    record(db, p, "set_baseline", "agent", a.id, run_id=r.id)
    await db.commit()
    return agent_out(a, a.versions)
