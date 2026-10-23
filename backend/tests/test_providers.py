"""Wire-format tests for every adapter (no network)."""

import json
import time

import httpx
import pytest
import respx

from aether.providers.base import Message, ModelConfig, ProviderError, ToolCall, ToolSpec
from aether.providers.ratelimit import RateLimiter
from aether.providers.registry import client_from_config

TOOL = ToolSpec("get_weather", "weather", {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]})
CONVO = [
    Message("system", "be helpful"),
    Message("user", "weather in Paris?"),
    Message("assistant", None, tool_calls=[ToolCall(id="call_1", name="get_weather", arguments={"city": "Paris"})]),
    Message("tool", '{"temp": 18}', tool_call_id="call_1", name="get_weather"),
]


def cfg(kind, base="https://llm.example.com/v1", **kw):
    return ModelConfig(kind, "p", kw.pop("model", "m1"), base, kw.pop("key", "sk-test"), kw.pop("headers", {}), kw.pop("popts", {}), kw.pop("mopts", {}), **kw)


@respx.mock
async def test_openai_chat_request_and_tool_parse():
    route = respx.post("https://llm.example.com/v1/chat/completions").respond(
        json={
            "choices": [{"message": {"content": None, "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Rome"}'}}]}, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 5},
        }
    )
    c = client_from_config(cfg("openai_chat", headers={"x-client-app": "demo"}, input_cost_per_mtok=1.0, output_cost_per_mtok=2.0))
    r = await c.chat(CONVO, [TOOL], temperature=0.1, max_tokens=50)
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["authorization"] == "Bearer sk-test" and req.headers["x-client-app"] == "demo"
    assert body["tools"][0]["function"]["name"] == "get_weather" and body["temperature"] == 0.1 and body["max_tokens"] == 50
    assert body["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"city": "Paris"}'
    assert body["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"temp": 18}'}
    assert r.tool_calls[0].name == "get_weather" and r.tool_calls[0].arguments == {"city": "Rome"}
    assert r.usage.input_tokens == 11 and abs(r.cost_usd - (11 * 1 + 5 * 2) / 1e6) < 1e-12


@respx.mock
async def test_openai_reasoning_options_and_bad_arguments():
    route = respx.post("https://llm.example.com/v1/chat/completions").respond(
        json={"choices": [{"message": {"tool_calls": [{"id": "c", "function": {"name": "get_weather", "arguments": "{oops"}}]}}], "usage": {}}
    )
    c = client_from_config(cfg("openai_chat", mopts={"no_temperature": True, "max_tokens_param": "max_completion_tokens", "reasoning_effort": "low"}))
    r = await c.chat(CONVO, [TOOL], temperature=0.5, max_tokens=99, json_mode=True)
    body = json.loads(route.calls[0].request.content)
    assert "temperature" not in body and body["max_completion_tokens"] == 99 and body["reasoning_effort"] == "low"
    assert body["response_format"] == {"type": "json_object"}
    assert r.tool_calls[0].parse_error and r.tool_calls[0].arguments == {}


@respx.mock
async def test_azure_style_auth_and_query():
    route = respx.post("https://az.example.com/openai/deployments/d1/chat/completions").respond(json={"choices": [{"message": {"content": "hi"}}]})
    c = client_from_config(cfg("openai_chat", base="https://az.example.com/openai/deployments/d1", popts={"auth_style": "api-key", "query": {"api-version": "2024-10-21"}}))
    r = await c.chat([Message("user", "hi")])
    req = route.calls[0].request
    assert req.headers["api-key"] == "sk-test" and "authorization" not in req.headers
    assert req.url.params["api-version"] == "2024-10-21" and r.content == "hi"


@respx.mock
async def test_responses_api_roundtrip():
    route = respx.post("https://llm.example.com/v1/responses").respond(
        json={"output": [{"type": "reasoning"}, {"type": "function_call", "call_id": "fc1", "name": "get_weather", "arguments": '{"city":"Oslo"}'}], "usage": {"input_tokens": 7, "output_tokens": 3}, "status": "completed"}
    )
    c = client_from_config(cfg("openai_responses"))
    r = await c.chat(CONVO, [TOOL])
    body = json.loads(route.calls[0].request.content)
    assert body["instructions"] == "be helpful"
    types = [i.get("type", i.get("role")) for i in body["input"]]
    assert types == ["user", "function_call", "function_call_output"]
    assert body["tools"][0] == {"type": "function", "name": "get_weather", "description": "weather", "parameters": TOOL.parameters}
    assert r.tool_calls[0].id == "fc1" and r.tool_calls[0].arguments == {"city": "Oslo"} and r.usage.output_tokens == 3


@respx.mock
async def test_anthropic_messages():
    route = respx.post("https://api.anthropic.com/v1/messages").respond(
        json={"content": [{"type": "text", "text": "Calling"}, {"type": "tool_use", "id": "tu1", "name": "get_weather", "input": {"city": "Lima"}}], "usage": {"input_tokens": 20, "output_tokens": 9}, "stop_reason": "tool_use"}
    )
    c = client_from_config(cfg("anthropic", base=None, key="ant-key"))
    r = await c.chat(CONVO, [TOOL], max_tokens=100)
    req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["x-api-key"] == "ant-key" and req.headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "be helpful" and body["tools"][0]["input_schema"] == TOOL.parameters
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": '{"temp": 18}'}]}
    assert r.content == "Calling" and r.tool_calls[0].id == "tu1"


@respx.mock
async def test_bedrock_converse():
    route = respx.post("https://bedrock.example.com/model/us.anthropic.claude-x%3A0/converse").respond(
        json={"output": {"message": {"content": [{"toolUse": {"toolUseId": "t1", "name": "get_weather", "input": {"city": "Pune"}}}]}}, "usage": {"inputTokens": 30, "outputTokens": 4}, "stopReason": "tool_use"}
    )
    c = client_from_config(cfg("bedrock_converse", base="https://bedrock.example.com", model="us.anthropic.claude-x:0"))
    r = await c.chat(CONVO, [TOOL], temperature=0)
    body = json.loads(route.calls[0].request.content)
    assert body["system"] == [{"text": "be helpful"}]
    assert body["messages"][1]["content"][0]["toolUse"]["name"] == "get_weather"
    assert body["messages"][2]["content"][0]["toolResult"]["content"] == [{"json": {"temp": 18}}]
    assert body["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": TOOL.parameters}
    assert r.tool_calls[0].arguments == {"city": "Pune"} and r.usage.input_tokens == 30


@respx.mock
async def test_retries_on_429_then_succeeds(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("aether.providers.base.asyncio.sleep", fake_sleep)
    route = respx.post("https://llm.example.com/v1/chat/completions").mock(
        side_effect=[httpx.Response(429, headers={"retry-after": "2"}, json={"error": "slow down"}), httpx.Response(503), httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})]
    )
    r = await client_from_config(cfg("openai_chat")).chat([Message("user", "x")])
    assert r.content == "ok" and route.call_count == 3 and sleeps[0] == 2.0


@respx.mock
async def test_non_retryable_error_raises_once():
    route = respx.post("https://llm.example.com/v1/chat/completions").respond(400, json={"error": "bad model"})
    with pytest.raises(ProviderError) as exc:
        await client_from_config(cfg("openai_chat")).chat([Message("user", "x")])
    assert exc.value.status == 400 and route.call_count == 1


async def test_rate_limiter_blocks_when_rpm_exceeded(monkeypatch):
    lim = RateLimiter(rpm=2, tpm=None)
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    waited = []

    async def fake_sleep(s):
        waited.append(s)
        now[0] += s

    monkeypatch.setattr("aether.providers.ratelimit.asyncio.sleep", fake_sleep)
    await lim.acquire(10)
    await lim.acquire(10)
    assert not waited
    await lim.acquire(10)
    assert waited and sum(waited) >= 59


async def test_mock_provider_modes():
    perfect = client_from_config(cfg("mock", base=None, model="mock-perfect"))
    r = await perfect.chat([Message("user", "what's the weather in Paris on 2026-01-02?")], [TOOL])
    assert r.tool_calls and r.tool_calls[0].name == "get_weather"
    broken = client_from_config(cfg("mock", base=None, model="mock-broken"))
    assert not (await broken.chat([Message("user", "weather?")], [TOOL])).tool_calls
