"""Ask a model for a JSON object, tolerating fences, prose and one malformed reply."""

from __future__ import annotations

import json
import re
from typing import Any

from aether.providers.base import ChatResponse, LLMClient, Message

FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    candidates = [text.strip()]
    candidates += [m.strip() for m in FENCE.findall(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            val = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(val, dict):
            return val
    return None


class JSONReplyError(Exception):
    pass


async def chat_json(
    client: LLMClient,
    system: str,
    payload: dict[str, Any] | str,
    *,
    purpose: str,
    max_tokens: int = 4000,
    temperature: float | None = 0.2,
    retries: int = 1,
) -> tuple[dict[str, Any], list[ChatResponse]]:
    user = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, default=str)
    messages = [Message("system", system), Message("user", user)]
    responses: list[ChatResponse] = []
    for _ in range(retries + 1):
        resp = await client.chat(messages, json_mode=True, max_tokens=max_tokens, temperature=temperature, purpose=purpose)
        responses.append(resp)
        data = extract_json(resp.content)
        if data is not None:
            return data, responses
        messages = messages + [
            Message("assistant", resp.content or ""),
            Message("user", "That was not a valid JSON object. Reply again with only the JSON object."),
        ]
    raise JSONReplyError(f"model did not return JSON for {purpose}: {(responses[-1].content or '')[:200]}")
