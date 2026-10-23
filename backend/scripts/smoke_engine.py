"""Run the sample hotel suite once per model against live providers: trial -> evaluation."""

import asyncio
import json
import os
import sys

from aether.engine.evaluate import evaluate
from aether.engine.runner import TrialSpec, run_trial
from aether.providers.base import ModelConfig, close_http_client
from aether.providers.registry import client_from_config
from aether.samples import hotel_concierge, sample_suite_hotel


def gw(kind, route, model, opts=None):
    """A model behind the OpenAI-compatible LLM gateway configured in .env (GATEWAY_*)."""
    base = {"azure-openai": os.environ["GATEWAY_OPENAI_BASE_URL"], "bedrock": os.environ["GATEWAY_BEDROCK_BASE_URL"]}[route]
    app = os.environ.get("GATEWAY_APP_NAME")
    return ModelConfig(kind, f"gateway-{route}", model, base, os.environ["GATEWAY_API_KEY"], {"x-client-app": app} if app else {}, {}, opts or {})


async def main() -> int:
    agent = hotel_concierge("http://localhost:8100")
    reasoning = {"no_temperature": True, "max_tokens_param": "max_completion_tokens"}
    sim = client_from_config(gw("openai_chat", "azure-openai", "gpt-4o-mini-2024-07-18"))
    judge = client_from_config(gw("openai_chat", "azure-openai", "gpt-5.2-2025-12-11", reasoning))
    agents = {
        "gpt-4o-mini": client_from_config(gw("openai_chat", "azure-openai", "gpt-4o-mini-2024-07-18")),
        "claude-sonnet-4-6": client_from_config(gw("bedrock_converse", "bedrock", "us.anthropic.claude-sonnet-4-6")),
    }
    ok = True
    for label, client in agents.items():
        for i, t in enumerate(sample_suite_hotel()):
            spec = TrialSpec(
                agent_kind="builtin", system_prompt=agent["system_prompt"], tools=agent["tools"], params={"temperature": 0.2},
                endpoint={}, task_input=t["input"], scenario=t.get("scenario"), max_turns=t.get("max_turns", 1),
                fixtures=t.get("fixtures", {}), inject=t.get("inject", {}), tool_mode="simulated", failure_rate=0.0,
                seed=f"s{i}", agent_client=client, simulator=sim, trial_id=f"t{i}", task_id=f"T{i}",
            )
            out = await run_trial(spec)
            ev = await evaluate(out, expected=t["expected"], tools=agent["tools"], system_prompt=agent["system_prompt"],
                                task_input=t["input"], scenario=t.get("scenario"), judge=judge)
            calls = [f"{c['name']}({json.dumps(c['arguments'])[:80]})" for c in out.tool_calls]
            print(f"\n[{label}] {t['category']}: {'PASS' if ev.passed else 'FAIL'} score={ev.score} turns={out.turns} "
                  f"{out.latency_ms}ms tok={out.input_tokens}/{out.output_tokens} cost=${out.cost_usd:.4f} eval=${out.eval_cost_usd + ev.eval_cost_usd:.4f}")
            print("   calls:", calls)
            print("   output:", (out.output or "")[:160].replace("\n", " "))
            for c in ev.checks:
                print(f"   - {c.name}: {'ok' if c.passed else 'FAIL'} {c.detail[:140]}")
            if ev.failure_category:
                print("   failure:", ev.failure_category)
            if out.error:
                print("   ERROR:", out.error)
                ok = False
    await close_http_client()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
