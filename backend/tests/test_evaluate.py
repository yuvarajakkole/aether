from aether.engine.evaluate import arguments_match, deterministic_checks, evaluate, match_value
from aether.engine.runner import TrialOutcome
from aether.samples import hotel_concierge

TOOLS = hotel_concierge("http://x")["tools"]


def outcome(calls, results=None, output="Done.", error=None):
    trace = []
    for i, (name, args) in enumerate(calls):
        trace.append({"type": "tool_call", "name": name, "arguments": args, "tool_call_id": f"c{i}"})
        res = (results or {}).get(i, {"result": {"ok": True}})
        trace.append({"type": "tool_result", "name": name, "tool_call_id": f"c{i}", **res})
    trace.append({"type": "assistant", "content": output})
    return TrialOutcome(trace=trace, output=output, error=error)


def names(checks):
    return {c.name: c.passed for c in checks}


def test_matchers():
    assert match_value("Paris", "paris ", True)
    assert match_value(2, "2", True)
    assert match_value("2026-11-12", "2026-11-12T00:00:00", True)
    assert match_value("*", "anything", True) and not match_value("*", None, False)
    assert match_value({"$lte": 180}, 150, True) and not match_value({"$lte": 180}, 200, True)
    assert match_value({"$in": ["refundable"]}, "Refundable", True)
    assert match_value({"$optional": True, "$in": ["refundable"]}, None, False)
    assert not match_value({"$optional": True, "$in": ["refundable"]}, "non_refundable", True)
    assert match_value({"$absent": True}, None, False) and not match_value({"$absent": True}, "x", True)
    assert match_value({"$regex": "^B-\\d+$"}, "B-9921", True)
    assert arguments_match({"city": "Lisbon", "guests": 2}, {"city": "lisbon", "guests": 3}) == ["guests: expected 2, got 3"]


def test_tool_selection_forbidden_and_args():
    exp = {
        "tool_calls": [{"name": "get_booking", "arguments": {"booking_id": "B-1"}}, {"name": "modify_booking", "arguments": {"rate_plan": "refundable"}}],
        "forbidden_tools": ["cancel_booking"],
    }
    good = outcome([("get_booking", {"booking_id": "B-1"}), ("modify_booking", {"booking_id": "B-1", "rate_plan": "refundable"})])
    assert all(names(deterministic_checks(good, exp, TOOLS, False)).values())
    bad = outcome([("get_booking", {"booking_id": "B-1"}), ("modify_booking", {"booking_id": "B-1", "rate_plan": "non_refundable"}), ("cancel_booking", {"booking_id": "B-1"})])
    n = names(deterministic_checks(bad, exp, TOOLS, False))
    assert n["tool_selection"] and not n["arguments"] and not n["forbidden_tools"]
    missing = names(deterministic_checks(outcome([("get_booking", {"booking_id": "B-1"})]), exp, TOOLS, False))
    assert not missing["tool_selection"]


def test_strict_order():
    exp = {"tool_calls": [{"name": "get_booking"}, {"name": "modify_booking"}], "order": "strict"}
    wrong = outcome([("modify_booking", {"booking_id": "B"}), ("get_booking", {"booking_id": "B"})])
    assert not names(deterministic_checks(wrong, exp, TOOLS, False))["tool_selection"]


def test_schema_and_unknown_tools():
    o = outcome([("search_hotels", {"city": "Rome"}), ("teleport", {})])
    n = names(deterministic_checks(o, {}, TOOLS, False))
    assert not n["schema"] and not n["unknown_tools"]


def test_recovery_only_for_infrastructure_failures():
    injected = {0: {"result": {"error": {"code": 504, "message": "timeout"}}, "injected": True}}
    o = outcome([("check_availability", {"property_id": "P", "check_in": "2026-01-01", "check_out": "2026-01-02"})], injected, output="Here you go: available!")
    assert not names(deterministic_checks(o, {}, TOOLS, False))["recovery"]
    o2 = outcome([("check_availability", {"property_id": "P", "check_in": "2026-01-01", "check_out": "2026-01-02"})], injected, output="The availability service timed out, please try again.")
    assert names(deterministic_checks(o2, {}, TOOLS, False))["recovery"]
    retried = outcome(
        [("check_availability", {"property_id": "P", "check_in": "2026-01-01", "check_out": "2026-01-02"})] * 2, {0: injected[0], 1: {"result": {"available": True}}}, output="Available."
    )
    assert names(deterministic_checks(retried, {}, TOOLS, False))["recovery"]
    not_found = outcome([("get_booking", {"booking_id": "X"})], {0: {"result": {"error": "Booking not found"}}}, output="No such booking.")
    assert "recovery" not in names(deterministic_checks(not_found, {}, TOOLS, False))


def test_tolerant_text_matching():
    from aether.engine.evaluate import text_contains

    assert text_contains("Your parcel arrives on **November 14, 2026**.", "2026-11-14")
    assert text_contains("ETA: Nov 14th, 2026", "2026-11-14")
    assert text_contains("arrives 14/11/2026", "2026-11-14")
    assert not text_contains("arrives November 15, 2026", "2026-11-14")
    assert text_contains("Refund of 40 USD issued", "$40.00")
    assert text_contains("Total €1,192.00", "EUR 1192")
    assert not text_contains("Total $41", "$40.00")
    assert text_contains("Booking b-9921 confirmed", "B-9921")
    assert not text_contains("Room 40 is ready", "B-40")
    assert not text_contains("invoice 1001 is open", "INV-1001")


def test_generated_expectations_are_made_fair():
    from aether.engine.generator import normalise_task
    from aether.samples import billing_support

    tools = billing_support("http://x")["tools"]
    raw = {
        "input": "Please refund $40 of INV-1002 for dana@example.com, I was double charged",
        "fixtures": {"invoices": [{"invoice_id": "INV-1002"}]},
        "expected": {
            "tool_calls": [{"name": "issue_refund", "arguments": {"invoice_id": "INV-1002", "amount": 40, "reason": {"$contains": "double charged"}}}],
            "forbidden_tools": ["create_ticket", "issue_refund"],
            "output_contains": ["INV-1002", "R-12345", "refund processed"],
        },
    }
    t = normalise_task(raw, "everyday", tools)
    args = t["expected"]["tool_calls"][0]["arguments"]
    assert args["invoice_id"] == "INV-1002" and args["amount"] == 40 and args["reason"] == "*"
    assert t["expected"]["forbidden_tools"] == []
    assert t["expected"]["output_contains"] == ["INV-1002"]
    strict = normalise_task({**raw, "expected": {**raw["expected"], "tool_calls": []}}, "policy", tools)
    assert strict["expected"]["forbidden_tools"] == ["create_ticket", "issue_refund"]


def test_output_checks_and_crash():
    o = outcome([], output="Your refund of $40.00 is on its way")
    n = names(deterministic_checks(o, {"output_contains": ["$40.00"], "output_not_contains": ["guarantee"]}, TOOLS, False))
    assert n["output"]
    crashed = TrialOutcome(trace=[], output=None, error="provider error: 500")
    assert not names(deterministic_checks(crashed, {}, TOOLS, False))["completed"]


async def test_evaluate_without_judge_sets_failure_category():
    o = outcome([("get_booking", {"booking_id": "B-1"})])
    ev = await evaluate(o, expected={"tool_calls": [{"name": "cancel_booking"}]}, tools=TOOLS, system_prompt="", task_input="cancel", scenario=None, judge=None)
    assert not ev.passed and ev.failure_category == "Wrong or missing tool"
    assert 0 < ev.score < 1
