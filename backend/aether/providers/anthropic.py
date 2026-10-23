"""Anthropic Messages API (api.anthropic.com or a compatible gateway)."""

from __future__ import annotations

import json
from typing import Any

from aether.providers.base import LLMClient, Message, ProviderError, ToolCall, Usage


def to_anthropic(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    system: list[str] = []
    out: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        if m.role == "system":
            system.append(m.content or "")
        elif m.role == "tool":
            push("user", {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content or ""})
        elif m.role == "assistant":
            if m.content:
                push("assistant", {"type": "text", "text": m.content})
            for c in m.tool_calls:
                push("assistant", {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
            if not m.content and not m.tool_calls:
                push("assistant", {"type": "text", "text": "(no response)"})
        else:
            push("user", {"type": "text", "text": m.content or ""})
    return ("\n\n".join(system) or None), out


class AnthropicClient(LLMClient):
    async def _chat(self, messages, tools, *, temperature, max_tokens, json_mode, purpose):
        base = (self.cfg.base_url or "https://api.anthropic.com").rstrip("/")
        system, msgs = to_anthropic(messages)
        if json_mode:
            system = (system or "") + "\n\nRespond with a single JSON object and nothing else."
        body: dict[str, Any] = {"model": self.cfg.model, "messages": msgs, "max_tokens": max_tokens or 4096}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools]
        if temperature is not None and not self.cfg.option("no_temperature", False):
            body["temperature"] = temperature
        body.update(self.cfg.option("extra_body", {}) or {})
        headers = self._auth_headers("x-api-key")
        headers.setdefault("anthropic-version", self.cfg.provider_options.get("anthropic_version", "2023-06-01"))
        data = await self._post(base + self.cfg.provider_options.get("messages_path", "/v1/messages"), body, headers)
        if data.get("type") == "error":
            raise ProviderError(f"anthropic error: {data.get('error')}")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                inp = block.get("input") or {}
                calls.append(ToolCall(id=block.get("id"), name=block.get("name", ""), arguments=inp, raw_arguments=json.dumps(inp)))
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0))
        return ("".join(texts).strip() or None), calls, usage, data.get("stop_reason")
