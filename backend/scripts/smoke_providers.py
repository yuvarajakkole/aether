"""Live round-trip check for provider adapters: tool call -> tool result -> final answer.

Usage: uv run python scripts/smoke_providers.py   (reads GATEWAY_* / OPENAI_API_KEY / ANTHROPIC_API_KEY from env)
"""

import asyncio
import json
import os
import sys

from aether.providers.base import Message, ModelConfig, ToolSpec, close_http_client
from aether.providers.registry import client_from_config

WEATHER = ToolSpec(
    name="get_weather",
    description="Get the current weather for a city.",
    parameters={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
)


def targets() -> list[ModelConfig]:
    out: list[ModelConfig] = []
    gw_key, gw_app = os.environ.get("GATEWAY_API_KEY"), os.environ.get("GATEWAY_APP_NAME")
    gw_openai, gw_bedrock, gw_pplx = (os.environ.get(f"GATEWAY_{k}_BASE_URL") for k in ("OPENAI", "BEDROCK", "PERPLEXITY"))
    if gw_key and gw_openai:
        h = {"x-client-app": gw_app} if gw_app else {}
        reasoning = {"no_temperature": True, "max_tokens_param": "max_completion_tokens"}
        for m, opts in [
            ("gpt-4o-mini-2024-07-18", {}),
            ("gpt-5-2025-08-07", reasoning),
            ("gpt-5.2-2025-12-11", reasoning),
        ]:
            out.append(ModelConfig("openai_chat", "gateway-openai", m, gw_openai, gw_key, h, {}, opts))
        out.append(ModelConfig("openai_responses", "gateway-responses", "gpt-5.4-pro-2026-03-05", gw_openai, gw_key, h, {}, {}))
        for m in ["us.anthropic.claude-sonnet-4-6", "us.anthropic.claude-opus-4-6-v1"]:
            out.append(ModelConfig("bedrock_converse", "gateway-bedrock", m, gw_bedrock, gw_key, h))
        out.append(ModelConfig("openai_chat", "gateway-perplexity", "sonar-reasoning-pro", gw_pplx, gw_key, h, {}, {"json_mode": False}))
    if os.environ.get("OPENAI_API_KEY"):
        out.append(ModelConfig("openai_chat", "openai", "gpt-4o-mini", "https://api.openai.com/v1", os.environ["OPENAI_API_KEY"]))
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append(ModelConfig("anthropic", "anthropic", "claude-sonnet-4-5", None, os.environ["ANTHROPIC_API_KEY"]))
    return out


async def check(cfg: ModelConfig) -> tuple[str, bool, str]:
    client = client_from_config(cfg)
    label = f"{cfg.provider_name}/{cfg.model}"
    try:
        if cfg.provider_name == "gateway-perplexity":
            r = await client.chat([Message("user", "Reply with exactly: OK")])
            return label, bool(r.content), f"text={r.content!r:.60} tokens={r.usage.input_tokens}/{r.usage.output_tokens} {r.latency_ms}ms"
        msgs = [Message("system", "You are a weather assistant. Use tools."), Message("user", "What's the weather in Paris?")]
        r1 = await client.chat(msgs, [WEATHER], max_tokens=800)
        if not r1.tool_calls:
            return label, False, f"no tool call; content={r1.content!r:.100}"
        call = r1.tool_calls[0]
        msgs.append(Message("assistant", r1.content, tool_calls=r1.tool_calls))
        msgs.append(Message("tool", json.dumps({"city": call.arguments.get("city"), "temp_c": 18, "sky": "cloudy"}), tool_call_id=call.id, name=call.name))
        r2 = await client.chat(msgs, [WEATHER], max_tokens=800)
        ok = bool(r2.content) and "18" in r2.content
        return label, ok, f"call={call.name}({call.arguments}) final={r2.content!r:.80} tokens={r1.usage.input_tokens}+{r2.usage.input_tokens}/{r2.usage.output_tokens} {r1.latency_ms}+{r2.latency_ms}ms"
    except Exception as exc:  # noqa: BLE001
        return label, False, f"{type(exc).__name__}: {str(exc)[:300]}"


async def main() -> int:
    ts = targets()
    if not ts:
        print("No provider credentials in environment.")
        return 1
    results = await asyncio.gather(*(check(t) for t in ts))
    await close_http_client()
    for label, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {label}\n      {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
