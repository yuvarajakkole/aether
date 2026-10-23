"""OpenAI Responses API (needed for models that are not served on chat-completions)."""

from __future__ import annotations

import json
from typing import Any

from aether.providers.base import LLMClient, Message, ProviderError, ToolCall, Usage, parse_json_arguments


def to_responses_input(messages: list[Message]) -> tuple[str | None, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            instructions.append(m.content or "")
        elif m.role == "tool":
            items.append({"type": "function_call_output", "call_id": m.tool_call_id, "output": m.content or ""})
        elif m.role == "assistant":
            if m.content:
                items.append({"role": "assistant", "content": m.content})
            for c in m.tool_calls:
                items.append(
                    {
                        "type": "function_call",
                        "call_id": c.id,
                        "name": c.name,
                        "arguments": c.raw_arguments or json.dumps(c.arguments),
                    }
                )
        else:
            items.append({"role": "user", "content": m.content or ""})
    return ("\n\n".join(instructions) or None), items


class OpenAIResponsesClient(LLMClient):
    async def _chat(self, messages, tools, *, temperature, max_tokens, json_mode, purpose):
        if not self.cfg.base_url:
            raise ProviderError("provider has no base_url")
        instructions, items = to_responses_input(messages)
        body: dict[str, Any] = {"model": self.cfg.model, "input": items, "store": False}
        if instructions:
            body["instructions"] = instructions
        if tools:
            body["tools"] = [
                {"type": "function", "name": t.name, "description": t.description, "parameters": t.parameters}
                for t in tools
            ]
        if temperature is not None and not self.cfg.option("no_temperature", True):
            body["temperature"] = temperature
        if max_tokens:
            body["max_output_tokens"] = max_tokens
        if json_mode and self.cfg.option("json_mode", True):
            body["text"] = {"format": {"type": "json_object"}}
        if self.cfg.option("reasoning_effort"):
            body["reasoning"] = {"effort": self.cfg.option("reasoning_effort")}
        body.update(self.cfg.option("extra_body", {}) or {})
        url = self.cfg.base_url.rstrip("/") + self.cfg.provider_options.get("responses_path", "/responses")
        data = await self._post(url, body, self._auth_headers("bearer"))
        if data.get("error"):
            raise ProviderError(f"responses API error: {data['error']}")
        texts: list[str] = []
        calls: list[ToolCall] = []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if part.get("type") in ("output_text", "text"):
                        texts.append(part.get("text", ""))
            elif item.get("type") == "function_call":
                raw = item.get("arguments")
                args, err = parse_json_arguments(raw)
                calls.append(
                    ToolCall(id=item.get("call_id") or item.get("id"), name=item.get("name", ""), arguments=args, raw_arguments=raw, parse_error=err)
                )
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("input_tokens") or 0), output_tokens=int(u.get("output_tokens") or 0))
        return ("".join(texts) or None), calls, usage, data.get("status")
