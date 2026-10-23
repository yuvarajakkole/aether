"""Idempotent startup seeding.

* Always: the default workspace.
* AETHER_BOOTSTRAP_FILE (JSON): providers, models and helper-role defaults for this deployment.
  Strings may reference environment variables as ${VAR} so secrets stay out of the file.
* AETHER_SEED_SAMPLES=true: an offline mock provider plus fictional sample agents and a starter suite.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.auth import DEFAULT_WORKSPACE
from aether.config import get_settings
from aether.crypto import encrypt, hash_token
from aether.models import Agent, AgentVersion, ApiKey, LLMModel, Provider, Suite, Workspace
from aether.samples import billing_support, hotel_concierge, kitchen_helper_tools, sample_suite_hotel
from aether.services.agents import seal_endpoint
from aether.services.suites import add_tasks

log = logging.getLogger(__name__)
ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def interpolate(value: Any) -> Any:
    if isinstance(value, str):
        return ENV_REF.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, list):
        return [interpolate(v) for v in value]
    if isinstance(value, dict):
        return {interpolate(k): interpolate(v) for k, v in value.items()}
    return value


async def ensure_workspace(db: AsyncSession) -> Workspace:
    ws = (await db.execute(select(Workspace).where(Workspace.slug == DEFAULT_WORKSPACE))).scalar_one_or_none()
    if ws is None:
        ws = Workspace(name=get_settings().org_name, slug=DEFAULT_WORKSPACE)
        db.add(ws)
        await db.flush()
    return ws


async def upsert_provider(db: AsyncSession, ws: Workspace, spec: dict[str, Any]) -> Provider:
    prov = (await db.execute(select(Provider).where(Provider.workspace_id == ws.id, Provider.name == spec["name"]))).scalar_one_or_none()
    if prov is None:
        prov = Provider(workspace_id=ws.id, name=spec["name"], kind=spec["kind"])
        db.add(prov)
    prov.kind = spec["kind"]
    prov.base_url = spec.get("base_url")
    if spec.get("api_key"):
        prov.api_key_enc = encrypt(spec["api_key"])
    prov.api_key_env = spec.get("api_key_env")
    prov.headers = {k: v for k, v in (spec.get("headers") or {}).items() if k and v}
    prov.options = spec.get("options") or {}
    await db.flush()
    for m in spec.get("models") or []:
        row = (await db.execute(select(LLMModel).where(LLMModel.provider_id == prov.id, LLMModel.name == m["name"]))).scalar_one_or_none()
        if row is None:
            row = LLMModel(workspace_id=ws.id, provider_id=prov.id, name=m["name"])
            db.add(row)
        for field in ("display_name", "input_cost_per_mtok", "output_cost_per_mtok", "rpm_limit", "tpm_limit", "supports_tools", "enabled", "options"):
            if field in m:
                setattr(row, field, m[field])
    await db.flush()
    return prov


async def apply_roles(db: AsyncSession, ws: Workspace, roles: dict[str, str | list[str]]) -> None:
    """Assign helper roles. Each role takes a ref or a list of refs; the first model that exists wins."""
    models = (await db.execute(select(LLMModel).where(LLMModel.workspace_id == ws.id))).scalars().all()
    providers = {p.id: p.name for p in (await db.execute(select(Provider).where(Provider.workspace_id == ws.id))).scalars()}
    for m in models:
        opts = dict(m.options or {})
        opts["roles"] = [r for r in opts.get("roles", []) if r not in roles]
        m.options = opts
    for role, refs in roles.items():
        candidates = [refs] if isinstance(refs, str) else list(refs)
        target = None
        for ref in candidates:
            pname, _, mname = ref.partition("/")
            target = next((m for m in models if providers.get(m.provider_id) == pname and m.name == mname), None)
            if target is not None:
                break
        if target is None:
            log.warning("no configured model for helper role; Aether will fall back to the first available model", extra={"role": role})
            continue
        target.options = {**target.options, "roles": sorted({*target.options.get("roles", []), role})}
    await db.flush()


async def seed_samples(db: AsyncSession, ws: Workspace) -> None:
    s = get_settings()
    await upsert_provider(
        db,
        ws,
        {
            "name": "mock",
            "kind": "mock",
            "models": [
                {"name": "mock-perfect", "display_name": "Mock (perfect)"},
                {"name": "mock-flaky", "display_name": "Mock (flaky)"},
                {"name": "mock-broken", "display_name": "Mock (broken)"},
            ],
        },
    )
    tools_url = os.environ.get("AETHER_SAMPLE_TOOLS_URL", "http://localhost:8100/tools")
    agents_url = os.environ.get("AETHER_SAMPLE_AGENTS_URL")
    specs = [hotel_concierge(tools_url), billing_support(tools_url)]
    for spec in specs:
        if (await db.execute(select(Agent).where(Agent.workspace_id == ws.id, Agent.slug == spec["slug"]))).scalar_one_or_none():
            continue
        a = Agent(workspace_id=ws.id, name=spec["name"], slug=spec["slug"], description=spec["description"], kind="builtin", owner="samples")
        db.add(a)
        await db.flush()
        db.add(AgentVersion(agent_id=a.id, version=1, label="initial", system_prompt=spec["system_prompt"], tools=spec["tools"], params={"temperature": 0.2}))
        if spec["slug"] == "hotel-concierge":
            suite = Suite(workspace_id=ws.id, agent_id=a.id, name="Starter scenarios", description="Hand-written examples covering each task category.", status="ready")
            db.add(suite)
            await db.flush()
            await add_tasks(db, suite, sample_suite_hotel())
    if agents_url:
        base = agents_url.rstrip("/")
        externals = [
            {
                "name": "Order Tracker",
                "slug": "order-tracker",
                "description": "External agent (Aether agent protocol) that tracks parcels and handles delivery issues with its own tools.",
                "kind": "http",
                "endpoint": {"url": f"{base}/agents/order-tracker", "timeout_s": 120},
                "tools": [],
                "prompt": "",
            },
            {
                "name": "Kitchen Helper",
                "slug": "kitchen-helper",
                "description": "External OpenAI-compatible chat endpoint; Aether supplies the tools and runs the tool loop.",
                "kind": "openai_chat",
                "endpoint": {"url": f"{base}/kitchen/v1", "model": "kitchen-helper", "timeout_s": 120},
                "tools": kitchen_helper_tools(),
                "prompt": "(The endpoint has its own system prompt.)",
            },
        ]
        for e in externals:
            if (await db.execute(select(Agent).where(Agent.workspace_id == ws.id, Agent.slug == e["slug"]))).scalar_one_or_none():
                continue
            a = Agent(workspace_id=ws.id, name=e["name"], slug=e["slug"], description=e["description"], kind=e["kind"], endpoint=seal_endpoint(e["endpoint"]), owner="samples")
            db.add(a)
            await db.flush()
            db.add(AgentVersion(agent_id=a.id, version=1, label="initial", system_prompt="", tools=e["tools"], params={}))
    _ = s


async def ensure_bootstrap_key(db: AsyncSession, ws: Workspace, token: str) -> None:
    """Register the first admin key for api_key/oidc deployments. Invalid keys stop startup with a clear error."""
    if not token.startswith("ak_") or len(token) < 20:
        raise ValueError("AETHER_BOOTSTRAP_API_KEY must start with 'ak_' and be at least 20 characters")
    digest = hash_token(token)
    if (await db.execute(select(ApiKey).where(ApiKey.token_hash == digest))).scalar_one_or_none() is None:
        db.add(ApiKey(workspace_id=ws.id, name="bootstrap-admin", prefix=token[:10], token_hash=digest))
        await db.flush()
        log.info("bootstrap admin API key registered", extra={"prefix": token[:10]})


async def run_bootstrap(db: AsyncSession) -> None:
    s = get_settings()
    ws = await ensure_workspace(db)
    if s.bootstrap_api_key:
        await ensure_bootstrap_key(db, ws, s.bootstrap_api_key)
    path = os.environ.get("AETHER_BOOTSTRAP_FILE")
    if path and Path(path).exists():
        cfg = interpolate(json.loads(Path(path).read_text()))
        for spec in cfg.get("providers") or []:
            if spec.get("enabled_if_env") and not os.environ.get(spec["enabled_if_env"]):
                continue
            await upsert_provider(db, ws, spec)
        if cfg.get("roles"):
            await apply_roles(db, ws, cfg["roles"])
        log.info("bootstrap file applied", extra={"path": path})
    elif path:
        log.warning("AETHER_BOOTSTRAP_FILE does not exist", extra={"path": path})
    if s.seed_samples:
        await seed_samples(db, ws)
    await db.commit()
