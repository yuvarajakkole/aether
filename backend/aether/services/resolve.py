"""Resolve models/clients for a workspace, including Aether's own helper roles."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from aether.config import get_settings
from aether.models import LLMModel, Provider
from aether.providers.base import LLMClient
from aether.providers.registry import client_for

ROLES = ("judge", "simulator", "generator")


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


async def get_model(db: AsyncSession, workspace_id: str, model_id: str) -> LLMModel:
    m = (
        await db.execute(
            select(LLMModel).options(joinedload(LLMModel.provider)).where(LLMModel.id == model_id, LLMModel.workspace_id == workspace_id)
        )
    ).scalar_one_or_none()
    if m is None:
        raise NotFound(f"model {model_id} not found")
    return m


async def find_model_by_ref(db: AsyncSession, workspace_id: str, ref: str) -> LLMModel | None:
    """ref is '<provider name>/<model name>', a bare model name, or a display name."""
    q = select(LLMModel).options(joinedload(LLMModel.provider)).join(Provider).where(LLMModel.workspace_id == workspace_id)
    if "/" in ref:
        pname, mname = ref.split("/", 1)
        row = (await db.execute(q.where(Provider.name == pname, LLMModel.name == mname))).scalars().first()
        if row:
            return row
    rows = (await db.execute(q)).scalars().all()
    for m in rows:
        if ref in (m.name, m.display_name, m.id):
            return m
    return None


async def default_model_for(db: AsyncSession, workspace_id: str, role: str) -> LLMModel | None:
    """Resolution order: workspace role flag (model.options.roles) > AETHER_DEFAULT_<ROLE>_MODEL > first enabled real model > mock."""
    rows = (
        await db.execute(
            select(LLMModel).options(joinedload(LLMModel.provider)).where(LLMModel.workspace_id == workspace_id, LLMModel.enabled.is_(True))
        )
    ).scalars().all()
    flagged = [m for m in rows if role in ((m.options or {}).get("roles") or [])]
    if flagged:
        return flagged[0]
    ref = getattr(get_settings(), f"default_{role}_model", None)
    if ref:
        m = await find_model_by_ref(db, workspace_id, ref)
        if m:
            return m
    real = [m for m in rows if m.provider.kind != "mock" and m.supports_tools]
    if real:
        return real[0]
    mock = [m for m in rows if m.provider.kind == "mock"]
    return mock[0] if mock else None


async def role_client(db: AsyncSession, workspace_id: str, role: str, model_id: str | None) -> tuple[LLMClient | None, LLMModel | None]:
    m = await get_model(db, workspace_id, model_id) if model_id else await default_model_for(db, workspace_id, role)
    return (client_for(m) if m else None), m
