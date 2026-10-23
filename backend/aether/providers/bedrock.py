"""Amazon Bedrock Converse API over HTTPS with bearer auth.

Works with Bedrock API keys (bedrock-runtime.<region>.amazonaws.com) and with LLM
gateways that proxy the Converse route. SigV4 signing is intentionally not handled here.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from aether.providers.base import LLMClient, Message, ProviderError, ToolCall, Usage


def to_converse(messages: list[Message]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    system: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []

    def push(role: str, block: dict[str, Any]) -> None:
        if out and out[-1]["role"] == role:
            out[-1]["content"].append(block)
        else:
            out.append({"role": role, "content": [block]})

    for m in messages:
        if m.role == "system":
            if m.content:
                system.append({"text": m.content})
        elif m.role == "tool":
            content: list[dict[str, Any]]
            try:
                parsed = json.loads(m.content or "")
                content = [{"json": parsed}] if isinstance(parsed, dict) else [{"text": m.content or ""}]
            except json.JSONDecodeError:
                content = [{"text": m.content or "(empty)"}]
            push("user", {"toolResult": {"toolUseId": m.tool_call_id, "content": content}})
        elif m.role == "assistant":
            if m.content:
                push("assistant", {"text": m.content})
            for c in m.tool_calls:
                push("assistant", {"toolUse": {"toolUseId": c.id, "name": c.name, "input": c.arguments}})
            if not m.content and not m.tool_calls:
                push("assistant", {"text": "(no response)"})
        else:
            push("user", {"text": m.content or "(empty)"})
    return system, out


class BedrockConverseClient(LLMClient):
    async def _chat(self, messages, tools, *, temperature, max_tokens, json_mode, purpose):
        if not self.cfg.base_url:
            raise ProviderError("provider has no base_url")
        system, msgs = to_converse(messages)
        if json_mode:
            system.append({"text": "Respond with a single JSON object and nothing else."})
        body: dict[str, Any] = {"messages": msgs}
        if system:
            body["system"] = system
        inference: dict[str, Any] = {"maxTokens": max_tokens or 4096}
        if temperature is not None and not self.cfg.option("no_temperature", False):
            inference["temperature"] = temperature
        body["inferenceConfig"] = inference
        if tools:
            body["toolConfig"] = {
                "tools": [
                    {"toolSpec": {"name": t.name, "description": t.description or t.name, "inputSchema": {"json": t.parameters}}}
                    for t in tools
                ]
            }
        body.update(self.cfg.option("extra_body", {}) or {})
        url = f"{self.cfg.base_url.rstrip('/')}/model/{quote(self.cfg.model, safe='')}/converse"
        data = await self._post(url, body, self._auth_headers("bearer"))
        msg = ((data.get("output") or {}).get("message")) or {}
        texts: list[str] = []
        calls: list[ToolCall] = []
        for block in msg.get("content") or []:
            if "text" in block:
                texts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                inp = tu.get("input") or {}
                calls.append(ToolCall(id=tu.get("toolUseId"), name=tu.get("name", ""), arguments=inp, raw_arguments=json.dumps(inp)))
        u = data.get("usage") or {}
        usage = Usage(input_tokens=int(u.get("inputTokens") or 0), output_tokens=int(u.get("outputTokens") or 0))
        return ("".join(texts).strip() or None), calls, usage, data.get("stopReason")
