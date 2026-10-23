"""OpenAI chat-completions wire format.

Works with OpenAI, Azure OpenAI (auth_style=api-key + query api-version), and any
OpenAI-compatible gateway or server (LLM gateways, vLLM, Ollama, LiteLLM, Perplexity...).
"""

from __future__ import annotations

import json
from typing import Any

from aether.providers.base import LLMClient, Message, ProviderError, ToolCall, ToolSpec, Usage, parse_json_arguments


def to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": c.id,
                            "type": "function",
                            "function": {"name": c.name, "arguments": c.raw_arguments or json.dumps(c.arguments)},
                        }
                        for c in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content or ""})
    return out


def to_openai_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
        for t in tools
    ]


def parse_openai_choice(data: dict[str, Any]) -> tuple[str | None, list[ToolCall], Usage, str | None]:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError(f"response has no choices: {json.dumps(data)[:300]}")
    choice = choices[0]
    msg = choice.get("message") or {}
    calls: list[ToolCall] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        raw = fn.get("arguments")
        args, err = parse_json_arguments(raw)
        calls.append(
            ToolCall(
                id=tc.get("id") or ToolCall(name="x", arguments={}).id,
                name=fn.get("name", ""),
                arguments=args,
                raw_arguments=raw if isinstance(raw, str) else json.dumps(raw),
                parse_error=err,
            )
        )
    u = data.get("usage") or {}
    usage = Usage(
        input_tokens=int(u.get("prompt_tokens") or u.get("input_tokens") or 0),
        output_tokens=int(u.get("completion_tokens") or u.get("output_tokens") or 0),
    )
    return msg.get("content"), calls, usage, choice.get("finish_reason")


class OpenAIChatClient(LLMClient):
    async def _chat(self, messages, tools, *, temperature, max_tokens, json_mode, purpose):
        if not self.cfg.base_url:
            raise ProviderError("provider has no base_url")
        body: dict[str, Any] = {"model": self.cfg.model, "messages": to_openai_messages(messages)}
        if tools:
            body["tools"] = to_openai_tools(tools)
            body["tool_choice"] = self.cfg.option("tool_choice", "auto")
        if temperature is not None and not self.cfg.option("no_temperature", False):
            body["temperature"] = temperature
        if max_tokens:
            body[self.cfg.option("max_tokens_param", "max_tokens")] = max_tokens
        if json_mode and self.cfg.option("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        if self.cfg.option("reasoning_effort"):
            body["reasoning_effort"] = self.cfg.option("reasoning_effort")
        body.update(self.cfg.option("extra_body", {}) or {})
        url = self.cfg.base_url.rstrip("/") + self.cfg.provider_options.get("chat_path", "/chat/completions")
        data = await self._post(url, body, self._auth_headers("bearer"))
        return parse_openai_choice(data)
