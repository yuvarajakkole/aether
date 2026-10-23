from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from aether.api.schemas import ModelIn, ModelPatch, ProviderIn, ProviderPatch
from aether.api.serialize import model_out, provider_out
from aether.auth import Principal, get_principal
from aether.crypto import encrypt
from aether.db import get_session
from aether.models import LLMModel, Provider
from aether.providers.base import Message, ProviderError, ToolSpec
from aether.providers.registry import client_for
from aether.services.audit import record

router = APIRouter(prefix="/api/v1", tags=["providers"])


async def _provider(db: AsyncSession, p: Principal, pid: str) -> Provider:
    prov = await db.get(Provider, pid)
    if prov is None or prov.workspace_id != p.workspace.id:
        raise HTTPException(404, "provider not found")
    return prov


async def _model(db: AsyncSession, p: Principal, mid: str) -> LLMModel:
    m = (await db.execute(select(LLMModel).options(joinedload(LLMModel.provider)).where(LLMModel.id == mid))).scalar_one_or_none()
    if m is None or m.workspace_id != p.workspace.id:
        raise HTTPException(404, "model not found")
    return m


@router.get("/providers")
async def list_providers(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (await db.execute(select(Provider).where(Provider.workspace_id == p.workspace.id).order_by(Provider.name))).scalars().all()
    return [provider_out(x) for x in rows]


@router.post("/providers", status_code=201)
async def create_provider(body: ProviderIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    if body.kind not in ("mock", "anthropic") and not body.base_url:
        raise HTTPException(422, "base_url is required for this provider kind")
    prov = Provider(
        workspace_id=p.workspace.id,
        name=body.name,
        kind=body.kind,
        base_url=body.base_url,
        api_key_enc=encrypt(body.api_key),
        api_key_env=body.api_key_env,
        headers=body.headers,
        options=body.options,
    )
    db.add(prov)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f"a provider named '{body.name}' already exists") from exc
    record(db, p, "create", "provider", prov.id, name=prov.name, kind=prov.kind)
    await db.commit()
    return provider_out(prov)


@router.patch("/providers/{pid}")
async def update_provider(pid: str, body: ProviderPatch, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    prov = await _provider(db, p, pid)
    data = body.model_dump(exclude_unset=True)
    if "api_key" in data:
        prov.api_key_enc = encrypt(data.pop("api_key"))
    for k, v in data.items():
        setattr(prov, k, v)
    record(db, p, "update", "provider", prov.id, fields=sorted(body.model_dump(exclude_unset=True)))
    await db.commit()
    return provider_out(prov)


@router.delete("/providers/{pid}", status_code=204)
async def delete_provider(pid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    prov = await _provider(db, p, pid)
    record(db, p, "delete", "provider", prov.id, name=prov.name)
    await db.delete(prov)
    await db.commit()


@router.get("/models")
async def list_models(db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    rows = (
        await db.execute(select(LLMModel).options(joinedload(LLMModel.provider)).where(LLMModel.workspace_id == p.workspace.id).order_by(LLMModel.created_at))
    ).scalars().all()
    return [model_out(m) for m in rows]


@router.post("/models", status_code=201)
async def create_model(body: ModelIn, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    prov = await _provider(db, p, body.provider_id)
    m = LLMModel(workspace_id=p.workspace.id, **body.model_dump())
    db.add(m)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, f"model '{body.name}' already exists for provider '{prov.name}'") from exc
    record(db, p, "create", "model", m.id, name=m.name, provider=prov.name)
    await db.commit()
    return model_out(await _model(db, p, m.id))


@router.patch("/models/{mid}")
async def update_model(mid: str, body: ModelPatch, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    m = await _model(db, p, mid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(m, k, v)
    record(db, p, "update", "model", m.id, fields=sorted(body.model_dump(exclude_unset=True)))
    await db.commit()
    return model_out(m)


@router.delete("/models/{mid}", status_code=204)
async def delete_model(mid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    m = await _model(db, p, mid)
    record(db, p, "delete", "model", m.id, name=m.name)
    await db.delete(m)
    await db.commit()


PING_TOOL = ToolSpec(
    name="get_time",
    description="Get the current time in a city.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)


@router.post("/models/{mid}/test")
async def test_model(mid: str, db: AsyncSession = Depends(get_session), p: Principal = Depends(get_principal)):
    """Live round trip: plain reply, then (if supported) a tool call."""
    m = await _model(db, p, mid)
    client = client_for(m)
    result: dict = {"model": m.name, "provider": m.provider.name}
    try:
        r = await client.chat([Message("user", "Reply with the single word: ready")], max_tokens=200, purpose="ping")
        result.update(ok=True, reply=(r.content or "")[:200], latency_ms=r.latency_ms, input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens)
        if m.supports_tools:
            r2 = await client.chat(
                [Message("system", "Use tools when helpful."), Message("user", "What time is it in Tokyo?")], [PING_TOOL], max_tokens=400, purpose="ping"
            )
            called = [c.name for c in r2.tool_calls]
            result["tool_call"] = {"ok": "get_time" in called, "called": called, "arguments": r2.tool_calls[0].arguments if r2.tool_calls else None}
            if "get_time" not in called:
                result["ok"] = False
                result["error"] = "model did not call the tool; set supports_tools=false if it has no tool calling"
    except ProviderError as exc:
        result.update(ok=False, error=str(exc)[:600])
    except ValueError as exc:
        result.update(ok=False, error=str(exc)[:600])
    return result
