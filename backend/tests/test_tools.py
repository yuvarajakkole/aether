import respx

from aether.engine.tools import ToolEnv
from aether.providers.base import ModelConfig, ToolCall
from aether.providers.registry import client_from_config
from aether.samples import hotel_concierge

TOOLS = hotel_concierge("http://tools.test")["tools"]


def env(**kw):
    defaults = dict(tools=TOOLS, mode="simulated", failure_rate=0.0, seed="s1", simulator=client_from_config(ModelConfig("mock", "mock", "mock-perfect")))
    defaults.update(kw)
    return ToolEnv(**defaults)


async def test_invalid_arguments_rejected_before_execution():
    e = env()
    step = await e.execute(ToolCall(name="search_hotels", arguments={"city": "Rome"}))
    assert step["result"]["error"] == "Invalid arguments" and step["invalid_args"]
    unknown = await e.execute(ToolCall(name="teleport", arguments={}))
    assert unknown["unknown_tool"]


async def test_simulated_response():
    step = await env().execute(ToolCall(name="get_booking", arguments={"booking_id": "B-1"}))
    assert step["simulated"] and step["result"]["status"] == "ok"


async def test_failure_injection_is_deterministic_and_rate_respected():
    def outcomes(seed):
        e = env(failure_rate=0.5, seed=seed)
        res = []
        for _ in range(40):
            res.append(e._should_inject("get_booking") is not None)
            e._calls += 1
        return res

    a, b = outcomes("x"), outcomes("x")
    assert a == b
    assert 8 <= sum(a) <= 32
    assert outcomes("x") != outcomes("y")


async def test_targeted_injection_only_on_first_call():
    e = env(inject={"tool": "get_booking", "kind": "timeout", "on_calls": [0]})
    first = await e.execute(ToolCall(name="get_booking", arguments={"booking_id": "B-1"}))
    second = await e.execute(ToolCall(name="get_booking", arguments={"booking_id": "B-1"}))
    assert first["injected"] and first["result"]["error"]["code"] == 504
    assert not second.get("injected")
    assert all(not h.get("injected") for h in [x for x in e.history if x["tool"] == "get_booking"][1:])


@respx.mock
async def test_external_agent_crash_is_an_agent_failure():
    from aether.engine.runner import TrialSpec, run_trial

    respx.post("http://agent.test/aether").respond(502, text="upstream exploded")
    spec = TrialSpec("http", "", [], {}, {"url": "http://agent.test/aether"}, "hi", None, 1, {}, {}, "simulated", 0.0, "s", None, None)
    out = await run_trial(spec)
    assert out.error.startswith("agent endpoint error") and "502" in out.error


@respx.mock
async def test_real_http_tools_with_path_params_and_errors():
    respx.get("http://tools.test/hotels/bookings/B%2F1").respond(json={"booking_id": "B/1", "rate_plan": "refundable"})
    respx.post("http://tools.test/hotels/search").respond(500, json={"detail": "db down"})
    e = env(mode="real")
    ok = await e.execute(ToolCall(name="get_booking", arguments={"booking_id": "B/1"}))
    assert ok["simulated"] is False and ok["result"]["rate_plan"] == "refundable" and ok["http_status"] == 200
    bad = await e.execute(ToolCall(name="search_hotels", arguments={"city": "Rome", "check_in": "2026-01-01", "check_out": "2026-01-03", "guests": 2}))
    assert bad["result"]["error"]["code"] == 500
