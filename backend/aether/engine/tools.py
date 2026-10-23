"""Tool execution during a trial: schema validation, failure injection, simulation, real HTTP."""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from aether.engine.llm_json import JSONReplyError, chat_json
from aether.providers.base import LLMClient, ProviderError, ToolCall, ToolSpec, http_client

INJECTED_ERRORS: list[dict[str, Any]] = [
    {"error": {"code": 503, "message": "Service Unavailable"}},
    {"error": {"code": 504, "message": "Gateway Timeout: upstream did not respond in 30s"}},
    {"error": {"code": 500, "message": "Internal Server Error"}},
    {"error": {"code": 429, "message": "Too Many Requests, retry later"}},
]
MALFORMED = ['{"status": "ok", "data": [', "<html><body>502 Bad Gateway</body></html>", ""]

TOOL_SIM_SYSTEM = """You simulate a backend API for an AI agent evaluation.
Given a tool definition, the call arguments, the world state (fixtures) and earlier calls in this session,
return ONLY the JSON response body that the real API would return.

Rules:
- The arguments have ALREADY been validated against the parameter schema. Never return validation or
  "missing parameter" errors. Optional parameters that are omitted mean "leave unchanged" or "no filter".
- Return a successful response unless the fixtures or the tool description clearly imply an error
  (e.g. record not found, no availability). Never invent business rules or restrictions that are not stated in the
  fixtures or tool description; for example, a non-refundable booking can still be cancelled (with no refund) unless
  the fixtures say otherwise.
- When fixtures name a record (hotel, customer, product), return that exact name and id for it.
- Stay strictly consistent with the fixtures and with earlier responses in this session; reflect earlier writes.
- If the request refers to records that are not in the fixtures, return realistic, internally consistent data,
  or a not-found error if the fixtures make clear the record does not exist.
- Never reveal that you are a simulator. Never add commentary. Output a single JSON object.
- Keep responses compact (well under 400 words)."""


def tool_specs(tools: list[dict[str, Any]]) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=t["name"],
            description=t.get("description", ""),
            parameters=t.get("parameters") or {"type": "object", "properties": {}},
        )
        for t in tools
    ]


def validate_arguments(schema: dict[str, Any] | None, args: dict[str, Any]) -> list[str]:
    if not schema:
        return []
    try:
        validator = Draft202012Validator(schema)
    except SchemaError as exc:
        return [f"tool schema is invalid: {exc.message}"]
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in validator.iter_errors(args)]


@dataclass
class ToolEnv:
    tools: list[dict[str, Any]]
    mode: str  # simulated | real
    failure_rate: float
    seed: str
    simulator: LLMClient | None
    fixtures: dict[str, Any] = field(default_factory=dict)
    scenario: str | None = None
    inject: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    trial_id: str = ""
    run_id: str = ""
    eval_cost: float = 0.0
    eval_tokens: int = 0
    _calls: int = 0
    _per_tool: dict[str, int] = field(default_factory=dict)

    def tool_def(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self.tools if t.get("name") == name), None)

    def _rng(self, salt: str) -> random.Random:
        h = hashlib.sha256(f"{self.seed}:{salt}".encode()).hexdigest()
        return random.Random(int(h[:16], 16))

    def _should_inject(self, name: str) -> dict[str, Any] | None:
        n = self._per_tool.get(name, 0)
        spec = self.inject or {}
        if spec.get("tool") == name and n in spec.get("on_calls", [0]):
            kind = spec.get("kind", "error")
            if kind == "malformed":
                return {"malformed": MALFORMED[0]}
            if kind == "timeout":
                return {"error": {"code": 504, "message": "Gateway Timeout: upstream did not respond in 30s"}}
            return spec.get("response") or INJECTED_ERRORS[0]
        if self.failure_rate > 0:
            rng = self._rng(f"{name}:{self._calls}")
            if rng.random() < self.failure_rate:
                if rng.random() < 0.25:
                    return {"malformed": rng.choice(MALFORMED)}
                return rng.choice(INJECTED_ERRORS)
        return None

    async def execute(self, call: ToolCall) -> dict[str, Any]:
        """Run one tool call; returns a trace step (type=tool_result)."""
        started = time.perf_counter()
        step: dict[str, Any] = {"type": "tool_result", "name": call.name, "tool_call_id": call.id}
        tdef = self.tool_def(call.name)
        try:
            if tdef is None:
                step.update(result={"error": f"Unknown tool '{call.name}'"}, unknown_tool=True)
                return step
            if call.parse_error:
                step.update(result={"error": f"Invalid arguments: {call.parse_error}"}, invalid_args=[call.parse_error])
                return step
            errors = validate_arguments(tdef.get("parameters"), call.arguments)
            if errors:
                step.update(result={"error": "Invalid arguments", "details": errors[:5]}, invalid_args=errors)
                return step
            injected = self._should_inject(call.name)
            if injected is not None:
                step["injected"] = True
                if "malformed" in injected:
                    step["result"] = injected["malformed"]
                else:
                    step["result"] = injected
                return step
            use_http = self.mode == "real" and (tdef.get("http") or {}).get("url")
            if use_http:
                step["result"], step["http_status"] = await self._call_http(tdef["http"], call.arguments)
                step["simulated"] = False
            else:
                step["result"] = await self._simulate(tdef, call.arguments)
                step["simulated"] = True
            return step
        finally:
            self._calls += 1
            self._per_tool[call.name] = self._per_tool.get(call.name, 0) + 1
            step["latency_ms"] = int((time.perf_counter() - started) * 1000)
            self.history.append(
                {"tool": call.name, "arguments": call.arguments, "response": step.get("result"), **({"injected": True} if step.get("injected") else {})}
            )

    async def _simulate(self, tdef: dict[str, Any], args: dict[str, Any]) -> Any:
        if tdef.get("mock_response") is not None:
            return tdef["mock_response"]
        if self.simulator is None:
            return {"status": "ok"}
        payload = {
            "tool": tdef["name"],
            "description": tdef.get("description", ""),
            "parameters_schema": tdef.get("parameters"),
            "arguments": args,
            "fixtures": self.fixtures,
            # Injected failures are test artefacts; the simulated backend must not learn to repeat them.
            "session_history": [h for h in self.history if not h.get("injected")][-8:],
            "scenario_hint": self.scenario,
        }
        try:
            data, responses = await chat_json(self.simulator, TOOL_SIM_SYSTEM, payload, purpose="tool_sim", max_tokens=1200)
        except (JSONReplyError, ProviderError) as exc:
            return {"error": {"code": 500, "message": f"simulator failure: {str(exc)[:160]}"}, "_simulator_error": True}
        for r in responses:
            self.eval_cost += r.cost_usd
            self.eval_tokens += r.usage.input_tokens + r.usage.output_tokens
        return data

    async def _call_http(self, http: dict[str, Any], args: dict[str, Any]) -> tuple[Any, int | None]:
        method = (http.get("method") or "POST").upper()
        url = http["url"]
        remaining = dict(args)
        for key in list(remaining):
            token = "{" + key + "}"
            if token in url:
                url = url.replace(token, quote(str(remaining.pop(key)), safe=""))
        # Correlation headers let sandbox APIs isolate state per attempt and trace Aether traffic.
        headers = {"Content-Type": "application/json", "X-Aether-Trial-Id": self.trial_id, "X-Aether-Run-Id": self.run_id, **(http.get("headers") or {})}
        timeout = float(http.get("timeout_s", 30))
        try:
            if method in ("GET", "DELETE"):
                resp = await http_client().request(method, url, params=remaining, headers=headers, timeout=timeout)
            else:
                resp = await http_client().request(method, url, json=remaining, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            return {"error": {"code": 504, "message": f"timeout after {timeout}s"}}, None
        except httpx.TransportError as exc:
            return {"error": {"code": 502, "message": f"connection failed: {exc}"}}, None
        try:
            body: Any = resp.json()
        except json.JSONDecodeError:
            body = resp.text[:4000]
        if resp.status_code >= 400:
            return {"error": {"code": resp.status_code, "body": body}}, resp.status_code
        return body, resp.status_code


def result_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)
