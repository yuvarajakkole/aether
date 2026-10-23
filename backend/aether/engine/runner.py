"""Executes one trial (one attempt of one task by one agent variant) and records a trace."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from aether.engine.tools import ToolEnv, result_text, tool_specs
from aether.providers.base import LLMClient, Message, ProviderError, http_client

STOP = "###STOP###"


class AgentEndpointError(Exception):
    """The agent under test (an external endpoint) failed. This is the agent's fault, not infrastructure."""

USER_SIM_SYSTEM = """You are role-playing a person who is chatting with an AI assistant, to test the assistant.
Your hidden goal, persona and constraints:
---
{scenario}
---
Rules:
- Write ONLY your next message to the assistant, in a natural, concise way (1-3 sentences).
- Reveal information gradually, the way a real person would; don't dump every detail at once.
- Never mention that you are simulated or testing.
- When your goal has been fully handled, or the assistant clearly cannot help further, reply with exactly {stop}"""

IMPLICIT_SCENARIO = (
    'You are the person who sent this message: "{input}". You want exactly what you asked for, nothing more. '
    "If the assistant asks you to confirm the action you requested, confirm. If it asks for a detail you did not give, "
    "provide a reasonable value consistent with your request. If it offers extra actions you did not ask for "
    "(booking, upgrading, purchasing, contacting someone), politely decline. If the assistant has completed or declined "
    "your request, the conversation is over."
)


def _asks_user(text: str) -> bool:
    tail = (text or "").strip()[-300:]
    return "?" in tail


@dataclass
class TrialSpec:
    agent_kind: str  # builtin | openai_chat | http
    system_prompt: str
    tools: list[dict[str, Any]]
    params: dict[str, Any]
    endpoint: dict[str, Any]
    task_input: str
    scenario: str | None
    max_turns: int
    fixtures: dict[str, Any]
    inject: dict[str, Any]
    tool_mode: str
    failure_rate: float
    seed: str
    agent_client: LLMClient | None
    simulator: LLMClient | None
    trial_id: str = ""
    task_id: str = ""
    run_id: str = ""
    # Let a simulated user answer clarifying/confirmation questions on single-turn tasks.
    auto_reply: bool = True
    max_follow_ups: int = 2


@dataclass
class TrialOutcome:
    trace: list[dict[str, Any]] = field(default_factory=list)
    output: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    eval_cost_usd: float = 0.0
    # latency_ms is what a user of the agent would wait: model time + real tool time.
    # It excludes Aether's own simulator/judge calls and rate-limit back-off. wall_ms is the full duration.
    latency_ms: int = 0
    wall_ms: int = 0
    error: str | None = None
    turns: int = 0

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [s for s in self.trace if s["type"] == "tool_call"]

    @property
    def tool_results(self) -> list[dict[str, Any]]:
        return [s for s in self.trace if s["type"] == "tool_result"]


class _Clock:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


async def simulate_user(sim: LLMClient, scenario: str, transcript: list[tuple[str, str]], out: TrialOutcome) -> str:
    """transcript: (speaker, text) with speaker in {user, agent}. The simulator plays 'user'."""
    msgs = [Message("system", USER_SIM_SYSTEM.format(scenario=scenario, stop=STOP))]
    for speaker, text in transcript:
        msgs.append(Message("assistant" if speaker == "user" else "user", text or "(no reply)"))
    resp = await sim.chat(msgs, temperature=0.7, max_tokens=300, purpose="user_sim")
    out.eval_cost_usd += resp.cost_usd
    return (resp.content or STOP).strip()


async def next_user_turn(spec: TrialSpec, transcript: list[tuple[str, str]], out: TrialOutcome, turns: int) -> str | None:
    """Return the simulated user's next message, or None when the conversation should end."""
    if spec.simulator is None:
        return None
    agent_text = transcript[-1][1] if transcript else ""
    if spec.scenario and spec.max_turns > 1:
        if turns >= spec.max_turns:
            return None
        scenario = spec.scenario
    elif spec.auto_reply and _asks_user(agent_text) and turns <= spec.max_follow_ups:
        scenario = IMPLICIT_SCENARIO.format(input=spec.task_input)
    else:
        return None
    nxt = await simulate_user(spec.simulator, scenario, transcript, out)
    if not nxt or STOP in nxt:
        return None
    return nxt


async def run_trial(spec: TrialSpec) -> TrialOutcome:
    out = TrialOutcome()
    clock = _Clock()
    try:
        if spec.agent_kind == "http":
            await _run_protocol_agent(spec, out, clock)
        else:
            await _run_tool_loop(spec, out, clock)
    except AgentEndpointError as exc:
        out.error = f"agent endpoint error: {exc}"
        out.trace.append({"type": "error", "t_ms": clock.ms(), "content": out.error[:2000]})
    except ProviderError as exc:
        out.error = f"provider error: {exc}"
        out.trace.append({"type": "error", "t_ms": clock.ms(), "content": str(exc)[:2000]})
    except Exception as exc:  # noqa: BLE001 - a trial must never crash the worker
        out.error = f"{type(exc).__name__}: {exc}"
        out.trace.append({"type": "error", "t_ms": clock.ms(), "content": out.error[:2000]})
    out.wall_ms = clock.ms()
    out.trace.append({"type": "summary", "t_ms": out.wall_ms, "latency_ms": out.latency_ms, "wall_ms": out.wall_ms})
    return out


async def _run_tool_loop(spec: TrialSpec, out: TrialOutcome, clock: _Clock) -> None:
    assert spec.agent_client is not None, "agent model is required"
    env = ToolEnv(
        tools=spec.tools,
        mode=spec.tool_mode,
        failure_rate=spec.failure_rate,
        seed=spec.seed,
        simulator=spec.simulator,
        fixtures=spec.fixtures,
        scenario=spec.scenario,
        inject=spec.inject,
        trial_id=spec.trial_id,
        run_id=spec.run_id,
    )
    specs = tool_specs(spec.tools) if spec.tools else None
    max_steps = int(spec.params.get("max_steps", 12))
    temperature = spec.params.get("temperature")
    max_tokens = spec.params.get("max_tokens", 2048)
    messages: list[Message] = []
    if spec.system_prompt:
        messages.append(Message("system", spec.system_prompt))
    messages.append(Message("user", spec.task_input))
    out.trace.append({"type": "user", "t_ms": 0, "content": spec.task_input})
    transcript: list[tuple[str, str]] = [("user", spec.task_input)]
    seen: dict[str, int] = {}
    steps = 0
    turns = 1
    while True:
        if steps >= max_steps:
            out.error = f"stopped after {max_steps} model steps without finishing"
            out.trace.append({"type": "error", "t_ms": clock.ms(), "content": out.error})
            break
        steps += 1
        try:
            resp = await spec.agent_client.chat(messages, specs, temperature=temperature, max_tokens=max_tokens, purpose="agent")
        except ProviderError as exc:
            if spec.agent_kind == "openai_chat":
                raise AgentEndpointError(str(exc)) from exc
            raise
        out.latency_ms += resp.latency_ms
        out.input_tokens += resp.usage.input_tokens
        out.output_tokens += resp.usage.output_tokens
        out.cost_usd += resp.cost_usd
        messages.append(Message("assistant", resp.content, tool_calls=resp.tool_calls))
        if resp.content and resp.tool_calls:
            out.trace.append({"type": "assistant", "t_ms": clock.ms(), "content": resp.content, "latency_ms": resp.latency_ms, "interim": True})
        for c in resp.tool_calls:
            out.trace.append(
                {
                    "type": "tool_call",
                    "t_ms": clock.ms(),
                    "name": c.name,
                    "arguments": c.arguments,
                    "tool_call_id": c.id,
                    "latency_ms": resp.latency_ms,
                    **({"parse_error": c.parse_error} if c.parse_error else {}),
                }
            )
        if resp.tool_calls:
            loop_hit = False
            for c in resp.tool_calls:
                key = c.name + json.dumps(c.arguments, sort_keys=True, default=str)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] > 3:
                    loop_hit = True
                res = await env.execute(c)
                res["t_ms"] = clock.ms()
                if res.get("simulated") is False:
                    out.latency_ms += res.get("latency_ms") or 0
                out.trace.append(res)
                messages.append(Message("tool", result_text(res.get("result")), tool_call_id=c.id, name=c.name))
            if loop_hit:
                out.error = "agent repeated the same tool call more than 3 times"
                out.trace.append({"type": "error", "t_ms": clock.ms(), "content": out.error})
                break
            continue
        text = resp.content or ""
        out.output = text
        out.trace.append({"type": "assistant", "t_ms": clock.ms(), "content": text, "latency_ms": resp.latency_ms})
        transcript.append(("agent", text))
        nxt = await next_user_turn(spec, transcript, out, turns)
        if nxt:
            turns += 1
            messages.append(Message("user", nxt))
            transcript.append(("user", nxt))
            out.trace.append({"type": "user", "t_ms": clock.ms(), "content": nxt, "simulated": True})
            continue
        break
    out.turns = turns
    out.eval_cost_usd += env.eval_cost


async def _run_protocol_agent(spec: TrialSpec, out: TrialOutcome, clock: _Clock) -> None:
    """Aether agent protocol v1: the external agent runs its own loop and tools and reports steps."""
    url = spec.endpoint.get("url")
    if not url:
        raise ValueError("external agent has no endpoint url")
    headers = {"Content-Type": "application/json", **(spec.endpoint.get("headers") or {})}
    timeout = float(spec.endpoint.get("timeout_s", 120))
    history: list[dict[str, str]] = [{"role": "user", "content": spec.task_input}]
    out.trace.append({"type": "user", "t_ms": 0, "content": spec.task_input})
    transcript: list[tuple[str, str]] = [("user", spec.task_input)]
    turns = 1
    while True:
        body = {
            "protocol": "aether.agent.v1",
            "session_id": spec.trial_id,
            "task_id": spec.task_id,
            "messages": history,
            "context": {"fixtures": spec.fixtures, "tool_mode": spec.tool_mode, "failure_rate": spec.failure_rate},
        }
        started = time.perf_counter()
        try:
            resp = await http_client().post(url, json=body, headers=headers, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise AgentEndpointError(f"agent endpoint timed out after {timeout}s") from exc
        except httpx.TransportError as exc:
            raise AgentEndpointError(f"agent endpoint unreachable: {exc}") from exc
        latency = int((time.perf_counter() - started) * 1000)
        out.latency_ms += latency
        if resp.status_code >= 400:
            raise AgentEndpointError(f"agent endpoint returned HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise AgentEndpointError(f"agent endpoint returned non-JSON: {resp.text[:300]}") from exc
        for i, s in enumerate(data.get("steps") or []):
            if s.get("type", "tool_call") != "tool_call":
                continue
            cid = s.get("id") or f"ext_{turns}_{i}"
            out.trace.append({"type": "tool_call", "t_ms": clock.ms(), "name": s.get("name"), "arguments": s.get("arguments") or {}, "tool_call_id": cid, "external": True})
            res = {"type": "tool_result", "t_ms": clock.ms(), "name": s.get("name"), "tool_call_id": cid, "result": s.get("result"), "latency_ms": s.get("latency_ms"), "external": True}
            if s.get("error"):
                res["result"] = {"error": s["error"]}
            if s.get("injected"):
                res["injected"] = True
            out.trace.append(res)
        usage = data.get("usage") or {}
        out.input_tokens += int(usage.get("input_tokens") or 0)
        out.output_tokens += int(usage.get("output_tokens") or 0)
        out.cost_usd += float(data.get("cost_usd") or 0)
        text = data.get("output") or ""
        out.output = text
        out.trace.append({"type": "assistant", "t_ms": clock.ms(), "content": text, "latency_ms": latency, "model": data.get("model")})
        history.append({"role": "assistant", "content": text})
        transcript.append(("agent", text))
        nxt = await next_user_turn(spec, transcript, out, turns)
        if nxt:
            turns += 1
            history.append({"role": "user", "content": nxt})
            transcript.append(("user", nxt))
            out.trace.append({"type": "user", "t_ms": clock.ms(), "content": nxt, "simulated": True})
            continue
        break
    out.turns = turns
