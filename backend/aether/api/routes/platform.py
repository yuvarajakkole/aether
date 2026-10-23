from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aether import __version__
from aether.api.schemas import ApiKeyIn, WorkspaceIn
from aether.auth import Principal, get_principal, new_api_key
from aether.config import get_settings
from aether.crypto import hash_token
from aether.db import get_session
from aether.engine.generator import CATEGORIES
from aether.engine.metrics import DEFAULT_GATE
from aether.models import Agent, ApiKey, AuditEvent, Job, LLMModel, Provider, Run, Suite, Trial, Workspace
from aether.providers.registry import PRESETS
from aether.services.audit import record

router = APIRouter()


@router.get("/healthz", tags=["platform"])
async def healthz():
    return {"status": "ok"}


@router.get("/readyz", tags=["platform"])
async def readyz(db: AsyncSession = Depends(get_session)):
    await db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/api/v1/meta", tags=["platform"])
async def meta():
    s = get_settings()
    return {
        "app": "Aether",
        "version": __version__,
        "org_name": s.org_name,
        "auth_mode": s.auth_mode,
        "oidc": {"issuer": s.oidc_issuer, "audience": s.oidc_audience} if s.auth_mode == "oidc" else None,
        "provider_presets": PRESETS,
        "task_categories": CATEGORIES,
        "default_gate": DEFAULT_GATE,
        "gate_metrics": ["pass_rate", "all_pass_rate", "tool_selection", "argument_accuracy", "schema_validity", "recovery_rate", "policy_pass_rate", "latency_p95_ms", "cost_per_trial_usd", "errors_rate", "category:<name>:pass_rate"],
    }


@router.get("/api/v1/me", tags=["platform"])
async def me(p: Principal = Depends(get_principal)):
    return {"actor": p.actor, "via": p.via, "workspace": {"id": p.workspace.id, "name": p.workspace.name, "slug": p.workspace.slug}}


@router.get("/api/v1/workspaces", tags=["platform"])
async def list_workspaces(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (await db.execute(select(Workspace).order_by(Workspace.created_at))).scalars().all()
    return [{"id": w.id, "name": w.name, "slug": w.slug} for w in rows]


@router.post("/api/v1/workspaces", tags=["platform"], status_code=201)
async def create_workspace(body: WorkspaceIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    if (await db.execute(select(Workspace).where(Workspace.slug == body.slug))).scalar_one_or_none():
        raise HTTPException(409, "workspace slug already exists")
    w = Workspace(name=body.name, slug=body.slug)
    db.add(w)
    await db.flush()
    db.add(AuditEvent(workspace_id=w.id, actor=p.actor, action="create", entity_type="workspace", entity_id=w.id, detail={"name": w.name}))
    await db.commit()
    return {"id": w.id, "name": w.name, "slug": w.slug}


@router.get("/api/v1/api-keys", tags=["platform"])
async def list_keys(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (await db.execute(select(ApiKey).where(ApiKey.workspace_id == p.workspace.id).order_by(ApiKey.created_at.desc()))).scalars().all()
    return [{"id": k.id, "name": k.name, "prefix": k.prefix, "revoked": k.revoked, "created_at": k.created_at, "last_used_at": k.last_used_at} for k in rows]


@router.post("/api/v1/api-keys", tags=["platform"], status_code=201)
async def create_key(body: ApiKeyIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    token, prefix = new_api_key()
    k = ApiKey(workspace_id=p.workspace.id, name=body.name, prefix=prefix, token_hash=hash_token(token))
    db.add(k)
    await db.flush()
    record(db, p, "create", "api_key", k.id, name=body.name)
    await db.commit()
    return {"id": k.id, "name": k.name, "prefix": prefix, "token": token, "note": "Store this token now; it is not shown again."}


@router.delete("/api/v1/api-keys/{key_id}", tags=["platform"], status_code=204)
async def revoke_key(key_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    k = await db.get(ApiKey, key_id)
    if k is None or k.workspace_id != p.workspace.id:
        raise HTTPException(404, "API key not found")
    k.revoked = True
    record(db, p, "revoke", "api_key", k.id, name=k.name)
    await db.commit()


@router.get("/api/v1/audit", tags=["platform"])
async def audit(limit: int = 100, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (
        await db.execute(select(AuditEvent).where(AuditEvent.workspace_id == p.workspace.id).order_by(AuditEvent.created_at.desc()).limit(min(limit, 500)))
    ).scalars().all()
    return [{"id": e.id, "actor": e.actor, "action": e.action, "entity_type": e.entity_type, "entity_id": e.entity_id, "detail": e.detail, "created_at": e.created_at} for e in rows]


@router.get("/api/v1/jobs/{job_id}", tags=["platform"])
async def job(job_id: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    j = await db.get(Job, job_id)
    if j is None or j.workspace_id != p.workspace.id:
        raise HTTPException(404, "job not found")
    return {"id": j.id, "kind": j.kind, "status": j.status, "result": j.result, "error": j.error, "created_at": j.created_at, "finished_at": j.finished_at}


@router.get("/api/v1/feed", tags=["platform"])
async def feed(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """'Since you were last here': regressions, review backlog, untested models, stale suites."""
    ws = p.workspace.id
    items: list[dict] = []
    agents = {a.id: a for a in (await db.execute(select(Agent).where(Agent.workspace_id == ws, Agent.archived.is_(False)))).scalars()}
    runs = (await db.execute(select(Run).where(Run.workspace_id == ws, Run.status == "completed").order_by(Run.finished_at.desc()).limit(60))).scalars().all()
    latest: dict[str, list[Run]] = {}
    for r in runs:
        latest.setdefault(r.agent_id, []).append(r)

    def best(r: Run) -> float | None:
        vals = [v["metrics"].get("all_pass_rate") for v in (r.summary or {}).get("variants", {}).values() if v.get("metrics")]
        vals = [v for v in vals if v is not None]
        return max(vals) if vals else None

    for aid, rs in latest.items():
        if aid not in agents or not rs:
            continue
        a = best(rs[0])
        b = best(rs[1]) if len(rs) >= 2 and rs[0].suite_id == rs[1].suite_id else None
        base = {"at": rs[0].finished_at, "agent_id": aid, "run_id": rs[0].id}
        if a is not None and b is not None and a < b - 0.05:
            items.append({**base, "kind": "regression",
                          "text": f"{agents[aid].name} dropped from {b:.0%} to {a:.0%} of tasks passing every attempt (run {rs[1].number} to run {rs[0].number})."})
        elif a is not None and b is not None and a > b + 0.05:
            items.append({**base, "kind": "improvement",
                          "text": f"{agents[aid].name} improved from {b:.0%} to {a:.0%} of tasks passing every attempt (run {rs[1].number} to run {rs[0].number})."})
        else:
            headline = (rs[0].summary or {}).get("recommendation", {}).get("headline", "")
            items.append({**base, "kind": "run", "text": f"Run {rs[0].number} of {agents[aid].name} finished. {headline}".strip()})
    review = (
        await db.execute(select(func.count()).select_from(Trial).join(Run).where(Run.workspace_id == ws, Trial.needs_review.is_(True), Trial.human_verdict.is_(None)))
    ).scalar()
    if review:
        items.append({"kind": "review", "at": datetime.now(UTC), "count": review, "text": f"{review} judge verdicts need a human decision."})
    tested: set[str] = set()
    for (cfg,) in (await db.execute(select(Run.config).where(Run.workspace_id == ws))).all():
        tested.update(m for m in (cfg or {}).get("model_ids", []) if m)
    untested = [
        m
        for m in (await db.execute(select(LLMModel).join(Provider).where(LLMModel.workspace_id == ws, LLMModel.enabled.is_(True), Provider.kind != "mock"))).scalars()
        if m.id not in tested and m.supports_tools
    ]
    if untested:
        names = [m.display_name or m.name for m in untested]
        text = f"{names[0]} has not been tested against any agent yet." if len(names) == 1 else f"{len(names)} models have not been tested against any agent yet: {', '.join(names[:4])}{'…' if len(names) > 4 else ''}."
        items.append({"kind": "untested_model", "at": max(m.created_at for m in untested), "model_id": untested[0].id, "text": text})
    stale = datetime.now(UTC) - timedelta(days=30)
    for s in (await db.execute(select(Suite).where(Suite.workspace_id == ws, Suite.status == "ready"))).scalars():
        created = s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=UTC)
        if created < stale and s.agent_id in agents:
            items.append({"kind": "stale_suite", "at": s.created_at, "suite_id": s.id, "agent_id": s.agent_id, "text": f"The '{s.name}' suite for {agents[s.agent_id].name} is over 30 days old."})

    def _ts(i):
        at = i["at"]
        return at if at.tzinfo else at.replace(tzinfo=UTC)

    items.sort(key=_ts, reverse=True)
    return items[:20]
