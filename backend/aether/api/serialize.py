from __future__ import annotations

from typing import Any

from aether.crypto import mask
from aether.models import Agent, AgentVersion, LLMModel, Provider, Run, Suite, Task, Trial
from aether.providers.registry import resolve_api_key
from aether.services.agents import public_endpoint


def provider_out(p: Provider) -> dict[str, Any]:
    try:
        key = resolve_api_key(p)
    except ValueError:
        key = None
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "base_url": p.base_url,
        "api_key_set": bool(key),
        "api_key_hint": mask(key) if p.api_key_enc else None,
        "api_key_env": p.api_key_env,
        "header_names": sorted((p.headers or {}).keys()),
        "options": p.options or {},
        "created_at": p.created_at,
    }


def model_out(m: LLMModel) -> dict[str, Any]:
    return {
        "id": m.id,
        "provider_id": m.provider_id,
        "provider_name": m.provider.name if m.provider else None,
        "provider_kind": m.provider.kind if m.provider else None,
        "name": m.name,
        "display_name": m.display_name or m.name,
        "input_cost_per_mtok": m.input_cost_per_mtok,
        "output_cost_per_mtok": m.output_cost_per_mtok,
        "rpm_limit": m.rpm_limit,
        "tpm_limit": m.tpm_limit,
        "supports_tools": m.supports_tools,
        "enabled": m.enabled,
        "options": m.options or {},
    }


def version_out(v: AgentVersion) -> dict[str, Any]:
    return {
        "id": v.id,
        "agent_id": v.agent_id,
        "version": v.version,
        "label": v.label,
        "system_prompt": v.system_prompt,
        "tools": v.tools,
        "default_model_id": v.default_model_id,
        "params": v.params,
        "notes": v.notes,
        "created_at": v.created_at,
    }


def agent_out(a: Agent, versions: list[AgentVersion] | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "id": a.id,
        "name": a.name,
        "slug": a.slug,
        "description": a.description,
        "kind": a.kind,
        "endpoint": public_endpoint(a.endpoint or {}),
        "owner": a.owner,
        "gate": a.gate or None,
        "baseline_run_id": a.baseline_run_id,
        "archived": a.archived,
        "created_at": a.created_at,
    }
    if versions is not None:
        out["versions"] = [version_out(v) for v in versions]
    if extra:
        out.update(extra)
    return out


def task_out(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "suite_id": t.suite_id,
        "seq": t.seq,
        "ref": f"T-{t.seq:03d}",
        "category": t.category,
        "input": t.input,
        "scenario": t.scenario,
        "max_turns": t.max_turns,
        "fixtures": t.fixtures,
        "expected": t.expected,
        "inject": t.inject,
        "tags": t.tags,
        "enabled": t.enabled,
    }


def suite_out(s: Suite, task_count: int | None = None, categories: dict[str, int] | None = None) -> dict[str, Any]:
    return {
        "id": s.id,
        "agent_id": s.agent_id,
        "name": s.name,
        "description": s.description,
        "status": s.status,
        "generation": s.generation,
        "error": s.error,
        "task_count": task_count,
        "categories": categories,
        "created_at": s.created_at,
    }


def run_out(r: Run, full: bool = True) -> dict[str, Any]:
    out = {
        "id": r.id,
        "number": r.number,
        "name": r.name,
        "agent_id": r.agent_id,
        "suite_id": r.suite_id,
        "status": r.status,
        "config": r.config,
        "created_by": r.created_by,
        "created_at": r.created_at,
        "started_at": r.started_at,
        "finished_at": r.finished_at,
        "error": r.error,
    }
    summary = r.summary or {}
    if full:
        out["summary"] = summary
        out["failure_clusters"] = r.failure_clusters or []
    else:
        out["headline"] = (summary.get("recommendation") or {}).get("headline")
        out["totals"] = summary.get("totals")
        out["variants"] = {
            k: {"model_name": v.get("model_name"), "version": v.get("version"), "pass_rate": v["metrics"].get("pass_rate"), "all_pass_rate": v["metrics"].get("all_pass_rate"), "gate_passed": v["gate"]["passed"]}
            for k, v in (summary.get("variants") or {}).items()
            if v.get("metrics")
        }
    return out


def trial_out(t: Trial, full: bool = False) -> dict[str, Any]:
    out = {
        "id": t.id,
        "run_id": t.run_id,
        "task_id": t.task_id,
        "variant": t.variant,
        "trial_index": t.trial_index,
        "status": t.status,
        "passed": t.passed,
        "effective_passed": (t.human_verdict == "pass") if t.human_verdict else t.passed,
        "score": t.score,
        "failure_category": t.failure_category,
        "latency_ms": t.latency_ms,
        "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens,
        "cost_usd": t.cost_usd,
        "eval_cost_usd": t.eval_cost_usd,
        "needs_review": t.needs_review,
        "human_verdict": t.human_verdict,
        "error": t.error,
    }
    if full:
        out.update(
            {
                "checks": t.checks,
                "judge": t.judge,
                "trace": t.trace,
                "output": t.output,
                "human_note": t.human_note,
                "reviewed_by": t.reviewed_by,
                "started_at": t.started_at,
                "finished_at": t.finished_at,
                "execution_attempts": t.attempts,
            }
        )
    return out
