"""Offline provider for tests, CI and key-less demos.

Model names select behaviour:
  mock-perfect  always calls the best-matching tool with well-formed arguments
  mock-flaky    like perfect, but ~30% of the time skips the tool or corrupts an argument
  mock-broken   never calls tools and answers vaguely
Internal Aether purposes (generator, judge, tool_sim, user_sim, cluster) get structurally
valid JSON so the full pipeline can run without a real model.
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from aether.providers.base import LLMClient, Message, ToolCall, ToolSpec, Usage

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
ID_LIKE = re.compile(r"\b[A-Z]{1,4}-\d{2,6}\b")
NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def _fill_args(tool: ToolSpec, text: str) -> dict[str, Any]:
    props: dict[str, Any] = (tool.parameters or {}).get("properties", {})
    required = (tool.parameters or {}).get("required", list(props))
    dates = ISO_DATE.findall(text)
    ids = ID_LIKE.findall(text)
    nums = [n for n in NUMBER.findall(text) if not any(n in d for d in dates)]
    args: dict[str, Any] = {}
    for name in required:
        spec = props.get(name, {})
        typ = spec.get("type", "string")
        if "enum" in spec:
            args[name] = spec["enum"][0]
        elif "date" in name and dates:
            args[name] = dates.pop(0)
        elif name.endswith("id") and ids:
            args[name] = ids.pop(0)
        elif typ in ("integer", "number"):
            args[name] = int(float(nums.pop(0))) if nums and typ == "integer" else (float(nums.pop(0)) if nums else 1)
        elif typ == "boolean":
            args[name] = True
        elif typ == "array":
            args[name] = []
        elif typ == "object":
            args[name] = {}
        else:
            words = [w for w in re.findall(r"[A-Z][a-z]+", text)]
            args[name] = words[0] if words else "value"
    return args


def _best_tool(tools: list[ToolSpec], text: str) -> ToolSpec:
    toks = _tokens(text)
    return max(tools, key=lambda t: len(toks & (_tokens(t.name.replace("_", " ")) | _tokens(t.description))))


def _payload(messages: list[Message]) -> dict[str, Any]:
    for m in reversed(messages):
        if m.role == "user" and m.content:
            try:
                val = json.loads(m.content)
                if isinstance(val, dict):
                    return val
            except json.JSONDecodeError:
                pass
    return {}


class MockClient(LLMClient):
    async def _chat(self, messages, tools, *, temperature, max_tokens, json_mode, purpose):
        usage = Usage(input_tokens=sum(len(m.content or "") for m in messages) // 4 + 20, output_tokens=40)
        handler = {
            "generator": self._generator,
            "judge": self._judge,
            "tool_sim": self._tool_sim,
            "user_sim": lambda _m: "###STOP###",
            "cluster": self._cluster,
        }.get(purpose or "")
        if handler:
            out = handler(messages)
            return (out if isinstance(out, str) else json.dumps(out)), [], usage, "stop"
        return self._agent(messages, tools or [], usage)

    def _agent(self, messages: list[Message], tools: list[ToolSpec], usage: Usage):
        mode = self.cfg.model
        last = messages[-1]
        user_text = next((m.content or "" for m in reversed(messages) if m.role == "user"), "")
        if mode == "mock-broken" or not tools:
            return "I'm not sure, could you clarify what you need?", [], usage, "stop"
        if last.role == "tool":
            return f"Here is what I found: {(last.content or '')[:300]}", [], usage, "stop"
        tool = _best_tool(tools, user_text)
        args = _fill_args(tool, user_text)
        if mode == "mock-flaky" and random.random() < 0.3:
            if args and random.random() < 0.5:
                k = next(iter(args))
                args[k] = "WRONG"
            else:
                return "Sure, that's all done for you.", [], usage, "stop"
        return None, [ToolCall(name=tool.name, arguments=args, raw_arguments=json.dumps(args))], usage, "tool_calls"

    def _generator(self, messages: list[Message]) -> dict[str, Any]:
        p = _payload(messages)
        tools = p.get("tools") or (p.get("agent") or {}).get("tools") or []
        mix = p.get("mix") or {"everyday": 3}
        tasks = []
        i = 0
        for cat, count in mix.items():
            for _ in range(int(count)):
                tool = tools[i % len(tools)] if tools else None
                i += 1
                if tool:
                    props = (tool.get("parameters") or {}).get("properties", {})
                    tasks.append(
                        {
                            "category": cat,
                            "input": f"[{cat}] Please use {tool['name'].replace('_', ' ')} for request {i} on 2026-11-{(i % 27) + 1:02d}.",
                            **({"scenario": f"You want help with {tool['name']}. Stop after one answer.", "max_turns": 2} if cat == "multi_turn" else {}),
                            "expected": {"tool_calls": [{"name": tool["name"], "arguments": {}}], "assertions": ["The agent addresses the request."]},
                            "fixtures": {"note": f"mock fixture for {tool['name']}", "fields": list(props)},
                        }
                    )
                else:
                    tasks.append({"category": cat, "input": f"Hello, question number {i}.", "expected": {"assertions": ["Responds politely."]}})
        return {"tasks": tasks}

    def _judge(self, messages: list[Message]) -> dict[str, Any]:
        p = _payload(messages)
        assertions = p.get("assertions") or []
        output = (p.get("final_output") or "").lower()
        vague = "not sure" in output or "clarify" in output
        results = [{"assertion": a, "passed": not vague, "reason": "mock judge"} for a in assertions]
        return {
            "passed": not vague,
            "assertions": results,
            "reasoning": "Mock judge: the agent gave a vague answer." if vague else "Mock judge: response addresses the request.",
            "failure_label": "Vague or non-committal answer" if vague else None,
        }

    def _tool_sim(self, messages: list[Message]) -> dict[str, Any]:
        p = _payload(messages)
        return {"status": "ok", "tool": p.get("tool"), "result": {"echo": p.get("arguments", {}), "simulated": True}}

    def _cluster(self, messages: list[Message]) -> dict[str, Any]:
        p = _payload(messages)
        groups: dict[str, list[str]] = {}
        for f in p.get("failures") or []:
            groups.setdefault(f.get("category") or "Other", []).append(f["id"])
        return {
            "clusters": [
                {"name": name, "description": f"{len(ids)} failures categorised as {name}.", "trial_ids": ids}
                for name, ids in groups.items()
            ]
        }
