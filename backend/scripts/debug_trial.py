"""Run one sample task and dump the full trace + judge output (debugging aid)."""

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


async def main(idx: int, which: str) -> None:
    agent = hotel_concierge("http://localhost:8100")
    t = sample_suite_hotel()[idx]
    sim = client_from_config(gw("openai_chat", "azure-openai", "gpt-4o-mini-2024-07-18"))
    judge = client_from_config(gw("openai_chat", "azure-openai", "gpt-5.2-2025-12-11", {"no_temperature": True, "max_tokens_param": "max_completion_tokens"}))
    client = client_from_config(gw("bedrock_converse", "bedrock", "us.anthropic.claude-sonnet-4-6") if which == "claude" else gw("openai_chat", "azure-openai", "gpt-4o-mini-2024-07-18"))
    spec = TrialSpec("builtin", agent["system_prompt"], agent["tools"], {"temperature": 0.2}, {}, t["input"], t.get("scenario"), t.get("max_turns", 1),
                     t.get("fixtures", {}), t.get("inject", {}), "simulated", 0.0, "dbg", client, sim)
    out = await run_trial(spec)
    for s in out.trace:
        print(json.dumps(s, default=str)[:600])
    ev = await evaluate(out, expected=t["expected"], tools=agent["tools"], system_prompt=agent["system_prompt"], task_input=t["input"], scenario=t.get("scenario"), judge=judge)
    print("CHECKS", [c.as_dict() for c in ev.checks])
    print("JUDGE", json.dumps(ev.judge, indent=1)[:2000])
    await close_http_client()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "mini"))
