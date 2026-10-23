"""Scoring a trial: deterministic checks first, then an LLM judge for what rules cannot decide.

Expected spec (task.expected), all keys optional:
  tool_calls:          [{"name": str, "arguments": {key: matcher}}]  must all be called
  order:               "any" (default) | "strict"   expected calls must appear in this order
  forbidden_tools:     [str]                         must never be called
  max_tool_calls:      int
  output_contains:     [str]                         case-insensitive substrings
  output_not_contains: [str]
  assertions:          [str]                         natural-language checks for the judge
  judge:               bool                          force/skip the judge (default: run when assertions exist
                                                     or the agent has a system prompt with rules)
Argument matchers: literal (normalised compare), "*" (any value present),
  {"$contains": s}, {"$regex": p}, {"$in": [...]}, {"$gte": n}, {"$lte": n}, {"$absent": true},
  and "$optional": true inside any operator matcher to also accept the argument being omitted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from aether.engine.llm_json import JSONReplyError, chat_json
from aether.engine.runner import TrialOutcome
from aether.engine.tools import validate_arguments
from aether.providers.base import LLMClient, ProviderError

FAILURE_LABELS = {
    "completed": "Crashed or gave no answer",
    "tool_selection": "Wrong or missing tool",
    "forbidden_tools": "Called a tool it must not use",
    "unknown_tools": "Called a tool that does not exist",
    "arguments": "Wrong tool arguments",
    "schema": "Malformed tool arguments",
    "output": "Missing required information in answer",
    "recovery": "Did not recover from a tool error",
    "efficiency": "Looped or used too many steps",
    "judge": "Did not meet requirements",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    gating: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "gating": self.gating}


@dataclass
class Evaluation:
    passed: bool
    score: float
    checks: list[Check]
    judge: dict[str, Any] = field(default_factory=dict)
    failure_category: str | None = None
    needs_review: bool = False
    eval_cost_usd: float = 0.0


# ---------------- argument matching ----------------

def _norm(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass
        try:
            return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else s.lower()
        except ValueError:
            return s.lower()
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def match_value(expected: Any, actual: Any, present: bool) -> bool:
    if isinstance(expected, dict) and expected and all(k.startswith("$") for k in expected):
        if expected.get("$optional") and not present:
            return True
        for op, arg in expected.items():
            if op == "$optional":
                continue
            if op == "$absent":
                if bool(arg) == present:
                    return False
                continue
            if not present:
                return False
            if op == "$contains" and str(arg).lower() not in json.dumps(actual, default=str).lower():
                return False
            if op == "$regex" and not re.search(str(arg), str(actual), re.I):
                return False
            if op == "$in" and _norm(actual) not in [_norm(x) for x in arg]:
                return False
            if op in ("$gte", "$lte"):
                try:
                    a = float(actual)
                except (TypeError, ValueError):
                    return False
                if op == "$gte" and a < float(arg):
                    return False
                if op == "$lte" and a > float(arg):
                    return False
        return True
    if not present:
        return False
    if expected == "*":
        return True
    if isinstance(expected, dict) and isinstance(actual, dict):
        return all(match_value(v, actual.get(k), k in actual) for k, v in expected.items())
    return _norm(expected) == _norm(actual)


def arguments_match(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    mismatches = []
    for k, v in (expected or {}).items():
        if not match_value(v, actual.get(k), k in actual):
            mismatches.append(f"{k}: expected {json.dumps(v, default=str)}, got {json.dumps(actual.get(k), default=str) if k in actual else 'missing'}")
    return mismatches


# ---------------- tolerant text matching ----------------

MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
MONEY = re.compile(r"^\s*(?:([$€£¥₹])\s*|([A-Za-z]{3})\s+)?(-?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z]{3})?\s*$")


def _date_variants(s: str) -> list[str]:
    try:
        d = datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except ValueError:
        return []
    m = MONTHS[d.month - 1]
    return [
        d.isoformat(), f"{m} {d.day}, {d.year}", f"{m} {d.day} {d.year}", f"{d.day} {m} {d.year}", f"{m[:3]} {d.day}, {d.year}",
        f"{m[:3]} {d.day} {d.year}", f"{d.day} {m[:3]} {d.year}", f"{d.month}/{d.day}/{d.year}", f"{d.day}/{d.month}/{d.year}",
        f"{d.month:02d}/{d.day:02d}/{d.year}", f"{d.day:02d}/{d.month:02d}/{d.year}", f"{m} {d.day}", f"{m[:3]} {d.day}", f"{d.day} {m}",
    ]


def _amount(s: str) -> float | None:
    m = MONEY.match(s)
    if not m or not (m.group(1) or m.group(2) or m.group(4) or "." in m.group(3)):
        return None
    return float(m.group(3).replace(",", ""))


def text_contains(text: str, needle: str) -> bool:
    """Case-insensitive containment that accepts common renderings of ISO dates and money amounts."""
    hay = re.sub(r"\s+", " ", text.lower()).replace("**", "")
    n = needle.lower().strip()
    if n in hay:
        return True
    for v in _date_variants(needle):
        if v.lower() in hay or re.sub(r"(\d)(st|nd|rd|th)", r"\1", hay).find(v.lower()) >= 0:
            return True
    amt = _amount(needle)
    if amt is not None:
        for m in re.finditer(r"-?\d[\d,]*(?:\.\d+)?", hay):
            try:
                if abs(float(m.group(0).replace(",", "")) - amt) < 0.005:
                    return True
            except ValueError:
                continue
    return False


# ---------------- deterministic checks ----------------

def deterministic_checks(outcome: TrialOutcome, expected: dict[str, Any], tools: list[dict[str, Any]], external: bool) -> list[Check]:
    checks: list[Check] = []
    calls = outcome.tool_calls
    results = outcome.tool_results
    names = [c["name"] for c in calls]
    tool_names = {t["name"] for t in tools}

    checks.append(
        Check("completed", outcome.error is None and bool((outcome.output or "").strip()), outcome.error or ("" if outcome.output else "no final answer"))
    )

    exp_calls = expected.get("tool_calls") or []
    if exp_calls:
        missing = [e["name"] for e in exp_calls if e["name"] not in names]
        order_ok = True
        if expected.get("order") == "strict" and not missing:
            pos = -1
            for e in exp_calls:
                idx = next((i for i, n in enumerate(names) if n == e["name"] and i > pos), None)
                if idx is None:
                    order_ok = False
                    break
                pos = idx
        detail = f"missing: {', '.join(missing)}" if missing else ("" if order_ok else "tools called out of order")
        checks.append(Check("tool_selection", not missing and order_ok, detail or f"called {', '.join(names) or 'nothing'}"))

        arg_problems: list[str] = []
        for e in exp_calls:
            if not e.get("arguments"):
                continue
            candidates = [c for c in calls if c["name"] == e["name"]]
            if not candidates:
                continue
            best = min((arguments_match(e["arguments"], c.get("arguments") or {}) for c in candidates), key=len)
            if best:
                arg_problems.append(f"{e['name']}: " + "; ".join(best))
        if any(e.get("arguments") for e in exp_calls):
            checks.append(Check("arguments", not arg_problems, " | ".join(arg_problems)))

    forbidden = set(expected.get("forbidden_tools") or [])
    if forbidden:
        used = sorted(forbidden & set(names))
        checks.append(Check("forbidden_tools", not used, f"called {', '.join(used)}" if used else ""))

    if tools and not external:
        unknown = sorted({n for n in names if n not in tool_names})
        if unknown:
            checks.append(Check("unknown_tools", False, f"invented tools: {', '.join(unknown)}"))
        schema_errors = []
        for c in calls:
            tdef = next((t for t in tools if t["name"] == c["name"]), None)
            if c.get("parse_error"):
                schema_errors.append(f"{c['name']}: {c['parse_error']}")
            elif tdef:
                errs = validate_arguments(tdef.get("parameters"), c.get("arguments") or {})
                if errs:
                    schema_errors.append(f"{c['name']}: {errs[0]}")
        if calls:
            checks.append(Check("schema", not schema_errors, " | ".join(schema_errors[:3])))

    contains = expected.get("output_contains") or []
    not_contains = expected.get("output_not_contains") or []
    if contains or not_contains:
        # Facts may be communicated in any agent message of the conversation, not only the last one.
        text = "\n".join(s.get("content") or "" for s in outcome.trace if s["type"] == "assistant") or (outcome.output or "")
        miss = [s for s in contains if not text_contains(text, s)]
        bad = [s for s in not_contains if text_contains(text, s)]
        detail = "; ".join(([f"missing '{m}'" for m in miss]) + ([f"contains '{b}'" for b in bad]))
        checks.append(Check("output", not miss and not bad, detail))

    injected = [i for i, r in enumerate(results) if r.get("injected") or _is_infra_failure(r.get("result"))]
    if injected:
        first = injected[0]
        failed_tool = results[first]["name"]
        later_ok = any(r["name"] == failed_tool and not r.get("injected") and not _is_infra_failure(r.get("result")) for r in results[first + 1 :])
        acknowledged = bool(outcome.output) and bool(ACK_FAILURE.search(outcome.output or ""))
        recovered = later_ok or acknowledged
        checks.append(Check("recovery", recovered, "retried successfully" if later_ok else ("told the user about the failure" if acknowledged else f"no retry or explanation after {failed_tool} failed")))

    max_calls = expected.get("max_tool_calls")
    loop = bool(outcome.error and "repeated" in outcome.error)
    if max_calls is not None or loop:
        too_many = max_calls is not None and len(calls) > int(max_calls)
        checks.append(Check("efficiency", not too_many and not loop, f"{len(calls)} tool calls" + (" (loop)" if loop else "")))
    return checks


ACK_FAILURE = re.compile(
    r"(unable|couldn'?t|could not|cannot|can'?t|try again|temporar|unavailable|issue|problem|error|not able|"
    r"timed? ?out|timeout|failed|failing|isn'?t responding|not responding|down right now|technical)",
    re.I,
)
INFRA_CODES = {408, 429, 500, 502, 503, 504}


def _is_infra_failure(result: Any) -> bool:
    """Transient/infrastructure failures (not business errors such as 'not found')."""
    if isinstance(result, dict):
        err = result.get("error")
        if isinstance(err, dict):
            try:
                return int(err.get("code", 0)) in INFRA_CODES
            except (TypeError, ValueError):
                return False
        return False
    if isinstance(result, str):
        s = result.strip()
        return not s or s.startswith("<html") or (s.startswith("{") and not _valid_json(s))
    return False


def _valid_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


# ---------------- LLM judge ----------------

JUDGE_SYSTEM = """You are a strict, fair evaluator of an AI agent's behaviour in one test conversation.
You are given the agent's instructions (its system prompt), the user's request, optional hidden scenario,
the full transcript including tool calls and tool results, and a list of assertions.

Judge:
1. Each assertion: true only if clearly satisfied by the transcript.
2. Policy adherence: did the agent break any rule in its own instructions?
3. Grounding: did the agent state facts (prices, availability, amenities, policies) not supported by tool results or instructions?
4. Task success: did the agent do what a competent assistant should, given the request and its rules?
   Refusing or escalating is a success when the rules require it. If a tool kept failing and the agent
   honestly reported it, that is not a failure of the agent unless the assertions say otherwise.

Judge substance, not style. Do NOT fail for formatting, markdown, emojis, tone, verbosity, or rendering
values differently (e.g. "EUR 142" as "€142") unless the instructions explicitly require it. A claim is
unsupported only if it adds facts that no tool result, fixture or instruction provides. Values derived by
simple reasoning from provided data (number of nights from dates, a total from nightly price x nights,
the currency of a price) are supported.

Return JSON only:
{"passed": bool, "assertions": [{"assertion": str, "passed": bool, "reason": str}],
 "policy_violations": [str], "unsupported_claims": [str], "reasoning": str (2-4 sentences),
 "failure_label": str | null (if failed: a short cause in under 8 words, e.g. "Changed refund terms without asking"),
 "confidence": number between 0 and 1}"""


def compact_transcript(outcome: TrialOutcome, limit: int = 14000) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for s in outcome.trace:
        t = s["type"]
        if t in ("user", "assistant"):
            items.append({"role": t, "text": s.get("content")})
        elif t == "tool_call":
            items.append({"role": "tool_call", "name": s.get("name"), "arguments": s.get("arguments")})
        elif t == "tool_result":
            res = s.get("result")
            txt = res if isinstance(res, str) else json.dumps(res, default=str)
            items.append({"role": "tool_result", "name": s.get("name"), "result": txt[:1500], **({"injected_failure": True} if s.get("injected") else {})})
        elif t == "error":
            items.append({"role": "error", "text": s.get("content")})
    while len(json.dumps(items, default=str)) > limit and len(items) > 4:
        items.pop(len(items) // 2)
    return items


async def run_judge(
    judge: LLMClient, *, system_prompt: str, task_input: str, scenario: str | None, assertions: list[str], outcome: TrialOutcome
) -> tuple[dict[str, Any], float]:
    payload = {
        "agent_instructions": system_prompt[:6000],
        "user_request": task_input,
        "hidden_scenario": scenario,
        "assertions": assertions,
        "transcript": compact_transcript(outcome),
        "final_output": outcome.output,
    }
    data, responses = await chat_json(judge, JUDGE_SYSTEM, payload, purpose="judge", max_tokens=1500, temperature=0)
    cost = sum(r.cost_usd for r in responses)
    data["passed"] = bool(data.get("passed"))
    try:
        data["confidence"] = float(data.get("confidence", 0.8))
    except (TypeError, ValueError):
        data["confidence"] = 0.5
    data["model"] = responses[-1].model
    return data, cost


async def evaluate(
    outcome: TrialOutcome,
    *,
    expected: dict[str, Any],
    tools: list[dict[str, Any]],
    system_prompt: str,
    task_input: str,
    scenario: str | None,
    judge: LLMClient | None,
    external: bool = False,
) -> Evaluation:
    checks = deterministic_checks(outcome, expected or {}, tools or [], external)
    assertions = list((expected or {}).get("assertions") or [])
    want_judge = (expected or {}).get("judge")
    if want_judge is None:
        want_judge = bool(assertions) or bool(system_prompt.strip())
    judge_data: dict[str, Any] = {}
    eval_cost = 0.0
    completed = checks[0].passed
    if want_judge and judge is not None and completed:
        try:
            judge_data, eval_cost = await run_judge(
                judge, system_prompt=system_prompt, task_input=task_input, scenario=scenario, assertions=assertions, outcome=outcome
            )
            checks.append(Check("judge", judge_data["passed"], judge_data.get("failure_label") or judge_data.get("reasoning", "")[:300]))
        except (JSONReplyError, ProviderError) as exc:
            judge_data = {"error": str(exc)[:500]}
            checks.append(Check("judge", False, f"judge unavailable: {str(exc)[:200]}", gating=False))

    gating = [c for c in checks if c.gating]
    passed = all(c.passed for c in gating)
    score = sum(c.passed for c in checks) / len(checks) if checks else 0.0
    failure = None
    if not passed:
        first = next(c for c in gating if not c.passed)
        failure = (judge_data.get("failure_label") if first.name == "judge" else None) or FAILURE_LABELS.get(first.name, first.name)
    rule_checks = [c for c in gating if c.name != "judge"]
    rules_pass = all(c.passed for c in rule_checks)
    needs_review = False
    if judge_data and "passed" in judge_data:
        needs_review = judge_data.get("confidence", 1) < 0.6 or (rules_pass and not judge_data["passed"] and len(rule_checks) > 2)
    return Evaluation(passed, round(score, 4), checks, judge_data, failure, needs_review, eval_cost)
