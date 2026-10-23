from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aether.engine.generator import CATEGORIES, generate_tasks, normalise_task
from aether.models import Agent, AgentVersion, Job, Suite, Task
from aether.services.resolve import Invalid, role_client

DEFAULT_MIX = {"everyday": 8, "edge_case": 6, "policy": 4, "adversarial": 3, "tool_failure": 3, "multi_turn": 2}


def validate_mix(mix: dict[str, Any]) -> dict[str, int]:
    out = {}
    for k, v in (mix or {}).items():
        if k not in CATEGORIES:
            raise Invalid(f"unknown category {k!r}; choose from {', '.join(CATEGORIES)}")
        n = int(v)
        if n < 0 or n > 500:
            raise Invalid("each category count must be between 0 and 500")
        if n:
            out[k] = n
    if not out:
        raise Invalid("choose at least one task")
    return out


async def next_seq(db: AsyncSession, suite_id: str) -> int:
    return ((await db.execute(select(func.max(Task.seq)).where(Task.suite_id == suite_id))).scalar() or 0) + 1


async def add_tasks(db: AsyncSession, suite: Suite, tasks: list[dict[str, Any]]) -> int:
    seq = await next_seq(db, suite.id)
    for t in tasks:
        db.add(
            Task(
                suite_id=suite.id,
                seq=seq,
                category=t.get("category", "everyday"),
                input=t["input"],
                scenario=t.get("scenario"),
                max_turns=int(t.get("max_turns") or 1),
                fixtures=t.get("fixtures") or {},
                expected=t.get("expected") or {},
                inject=t.get("inject") or {},
                tags=t.get("tags") or [],
            )
        )
        seq += 1
    return len(tasks)


def normalise_import(raw: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any] | None:
    cat = raw.get("category") or "everyday"
    if cat not in CATEGORIES:
        cat = "everyday"
    t = normalise_task(raw, cat, tools) if tools else None
    if t is None:
        if not str(raw.get("input") or "").strip():
            return None
        t = {
            "category": cat,
            "input": str(raw["input"]).strip(),
            "expected": raw.get("expected") or {},
            "fixtures": raw.get("fixtures") or {},
            "scenario": raw.get("scenario"),
            "max_turns": int(raw.get("max_turns") or (5 if raw.get("scenario") else 1)),
            "inject": raw.get("inject") or {},
            "tags": raw.get("tags") or [],
        }
    for key in ("output_not_contains", "order", "max_tool_calls", "judge"):
        if key in (raw.get("expected") or {}):
            t["expected"][key] = raw["expected"][key]
    return t


async def run_generation_job(db: AsyncSession, job: Job) -> dict[str, Any]:
    p = job.payload
    suite = await db.get(Suite, p["suite_id"])
    if suite is None:
        raise Invalid("suite was deleted")
    agent = await db.get(Agent, suite.agent_id)
    version = await db.get(AgentVersion, p["version_id"]) if p.get("version_id") else None
    if version is None:
        version = (await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id).order_by(AgentVersion.version.desc()))).scalars().first()
    client, model = await role_client(db, job.workspace_id, "generator", p.get("generator_model_id"))
    if client is None:
        raise Invalid("no model available to generate tasks; add a provider and model first")
    tools = (version.tools if version else []) or []
    mix = dict(p["mix"])
    if not tools:
        mix.pop("tool_failure", None)
    try:
        tasks, cost, warnings = await generate_tasks(
            client,
            agent_name=agent.name,
            description=agent.description,
            system_prompt=(version.system_prompt if version else "") or "",
            tools=tools,
            mix=mix,
            instructions=p.get("instructions"),
            sandbox_data=p.get("sandbox_data"),
        )
        n = await add_tasks(db, suite, tasks)
        suite.status = "ready" if n else "failed"
        suite.error = None if n else ("; ".join(warnings) or "no valid tasks generated")
        suite.generation = {**(suite.generation or {}), "generated": n, "cost_usd": cost, "warnings": warnings, "model": model.name if model else None}
        await db.commit()
        return {"generated": n, "cost_usd": cost, "warnings": warnings}
    except Exception as exc:
        await db.rollback()
        suite = await db.get(Suite, p["suite_id"])
        if suite is not None:
            suite.status = "failed"
            suite.error = str(exc)[:2000]
            await db.commit()
        raise
