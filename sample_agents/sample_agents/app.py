"""Sample services for Aether (all brands fictional).

/tools/hotels/*   Northwind Travel hotel API (real-API mode for the Hotel Concierge agent)
/tools/billing/*  Acme Cloud billing API (real-API mode for the Billing Support agent)
/agents/order-tracker     external agent speaking the Aether agent protocol (runs its own tools)
/kitchen/v1/chat/completions  OpenAI-compatible endpoint; Aether supplies tools and runs the loop

State is isolated per attempt using the X-Aether-Trial-Id header that Aether sends with
real tool calls, so concurrent attempts never see each other's writes.

LLM for the external agents (any OpenAI-compatible API):
  SAMPLE_LLM_BASE_URL, SAMPLE_LLM_API_KEY, SAMPLE_LLM_MODEL, SAMPLE_LLM_HEADERS (JSON), SAMPLE_LLM_OPTIONS (JSON)
Without these, the agents fall back to simple deterministic rules.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import ssl
import time
import uuid
from collections import OrderedDict
from typing import Any

import httpx
import truststore
from fastapi import FastAPI, Header, HTTPException, Request

app = FastAPI(title="Aether sample agents", version="0.1.0")

# ---------------------------------------------------------------- seed data
HOTELS = {
    "P-PAR-01": {"property_id": "P-PAR-01", "name": "Hotel Lumière Paris", "city": "Paris", "price_per_night": 298.0, "currency": "EUR", "amenities": ["wifi", "breakfast", "air conditioning"], "pet_friendly": False, "rating": 4.6},
    "P-PAR-02": {"property_id": "P-PAR-02", "name": "Le Petit Marais", "city": "Paris", "price_per_night": 189.0, "currency": "EUR", "amenities": ["wifi", "pets allowed"], "pet_friendly": True, "rating": 4.3},
    "P-LIS-01": {"property_id": "P-LIS-01", "name": "Casa do Rio", "city": "Lisbon", "price_per_night": 142.0, "currency": "EUR", "amenities": ["wifi", "pool", "breakfast"], "pet_friendly": False, "rating": 4.5},
    "P-LIS-02": {"property_id": "P-LIS-02", "name": "Alfama Terrace", "city": "Lisbon", "price_per_night": 171.0, "currency": "EUR", "amenities": ["wifi", "rooftop bar"], "pet_friendly": True, "rating": 4.4},
    "P-ROM-01": {"property_id": "P-ROM-01", "name": "Albergo del Sole", "city": "Rome", "price_per_night": 165.0, "currency": "EUR", "amenities": ["wifi", "breakfast", "parking"], "pet_friendly": False, "rating": 4.2},
    "P-ROM-02": {"property_id": "P-ROM-02", "name": "Trastevere Loft", "city": "Rome", "price_per_night": 210.0, "currency": "EUR", "amenities": ["wifi", "kitchen"], "pet_friendly": True, "rating": 4.7},
    "P-AUS-01": {"property_id": "P-AUS-01", "name": "South Congress Inn", "city": "Austin", "price_per_night": 179.0, "currency": "USD", "amenities": ["wifi", "pets allowed", "parking"], "pet_friendly": True, "rating": 4.1},
    "P-BOS-01": {"property_id": "P-BOS-01", "name": "Harbor Inn Boston", "city": "Boston", "price_per_night": 160.0, "currency": "USD", "amenities": ["wifi", "gym"], "pet_friendly": False, "rating": 4.0},
}
BOOKINGS = {
    "B-9921": {"booking_id": "B-9921", "traveler": "Dana Lee", "property_id": "P-PAR-01", "check_in": "2026-11-12", "check_out": "2026-11-16", "rooms": 1, "rate_plan": "refundable", "status": "confirmed", "total": 1192.0, "currency": "EUR"},
    "B-7710": {"booking_id": "B-7710", "traveler": "Sam Ortiz", "property_id": "P-BOS-01", "check_in": "2026-12-01", "check_out": "2026-12-05", "rooms": 1, "rate_plan": "non_refundable", "status": "confirmed", "total": 640.0, "currency": "USD"},
    "B-4410": {"booking_id": "B-4410", "traveler": "Priya Nair", "property_id": "P-ROM-01", "check_in": "2027-03-10", "check_out": "2027-03-13", "rooms": 2, "rate_plan": "refundable", "status": "confirmed", "total": 990.0, "currency": "EUR"},
}
CUSTOMERS = {
    "dana@example.com": {"customer_id": "C-100", "name": "Dana Lee", "plan": "Team", "email": "dana@example.com"},
    "sam@example.com": {"customer_id": "C-200", "name": "Sam Ortiz", "plan": "Pro", "email": "sam@example.com"},
}
INVOICES = {
    "C-100": [
        {"invoice_id": "INV-1001", "amount": 49.0, "currency": "USD", "status": "paid", "period": "2026-09"},
        {"invoice_id": "INV-1002", "amount": 249.0, "currency": "USD", "status": "paid", "period": "2026-10"},
        {"invoice_id": "INV-1003", "amount": 49.0, "currency": "USD", "status": "open", "period": "2026-11"},
    ],
    "C-200": [{"invoice_id": "INV-2001", "amount": 99.0, "currency": "USD", "status": "paid", "period": "2026-10"}],
}
PARCELS = {
    "TRK-1001": {"tracking_id": "TRK-1001", "status": "in_transit", "eta": "2026-11-14", "last_scan": "Memphis hub", "recipient": "Dana Lee"},
    "TRK-1002": {"tracking_id": "TRK-1002", "status": "delivered", "delivered_at": "2026-11-02T14:10:00Z", "signed_by": "J. Smith", "recipient": "Sam Ortiz"},
    "TRK-1003": {"tracking_id": "TRK-1003", "status": "exception", "detail": "Package damaged at sorting facility", "recipient": "Priya Nair"},
}


class Sandbox:
    """Per-attempt copy-on-write state, bounded LRU."""

    def __init__(self, limit: int = 5000):
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.limit = limit

    def get(self, key: str | None) -> dict[str, Any]:
        key = key or "shared"
        if key not in self._data:
            self._data[key] = {"bookings": copy.deepcopy(BOOKINGS), "invoices": copy.deepcopy(INVOICES), "refunds": [], "tickets": [], "parcels": copy.deepcopy(PARCELS), "claims": []}
            if len(self._data) > self.limit:
                self._data.popitem(last=False)
        self._data.move_to_end(key)
        return self._data[key]


SANDBOX = Sandbox()


def state(trial_id: str | None) -> dict[str, Any]:
    return SANDBOX.get(trial_id)


def nights(a: str, b: str) -> int:
    from datetime import date

    try:
        return max(0, (date.fromisoformat(b) - date.fromisoformat(a)).days)
    except ValueError:
        raise HTTPException(422, "dates must be ISO YYYY-MM-DD") from None


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# ---------------------------------------------------------------- hotel API
@app.post("/tools/hotels/search")
async def search_hotels(req: Request):
    b = await req.json()
    city = str(b.get("city", "")).strip().lower()
    n = nights(b["check_in"], b["check_out"])
    if n <= 0:
        raise HTTPException(422, "check_out must be after check_in")
    out = []
    for h in HOTELS.values():
        if h["city"].lower() != city:
            continue
        if b.get("max_price") is not None and h["price_per_night"] > float(b["max_price"]):
            continue
        wanted = [a.lower() for a in b.get("amenities") or []]
        if wanted and not all(any(w in am for am in h["amenities"]) for w in wanted):
            continue
        out.append({**h, "nights": n, "total": round(h["price_per_night"] * n, 2)})
    return {"results": out, "count": len(out)}


@app.post("/tools/hotels/availability")
async def availability(req: Request):
    b = await req.json()
    h = HOTELS.get(b.get("property_id"))
    if not h:
        raise HTTPException(404, f"property {b.get('property_id')} not found")
    n = nights(b["check_in"], b["check_out"])
    available = not (h["property_id"] == "P-PAR-01" and b["check_in"] <= "2026-12-31" <= b["check_out"])
    return {"property_id": h["property_id"], "available": available, "price_per_night": h["price_per_night"], "currency": h["currency"], "nights": n}


@app.get("/tools/hotels/properties/{property_id}")
def get_property(property_id: str):
    h = HOTELS.get(property_id)
    if not h:
        raise HTTPException(404, f"property {property_id} not found")
    return {**h, "policies": {"check_in_from": "15:00", "check_out_by": "11:00", "cancellation": "Refundable rates can be cancelled free until 48h before check-in."}}


@app.post("/tools/hotels/bookings")
async def create_booking(req: Request, x_aether_trial_id: str | None = Header(default=None)):
    b = await req.json()
    h = HOTELS.get(b.get("property_id"))
    if not h:
        raise HTTPException(404, "property not found")
    n = nights(b["check_in"], b["check_out"])
    bid = "B-" + uuid.uuid4().hex[:5].upper()
    rec = {"booking_id": bid, "traveler": b.get("traveler_name"), "property_id": h["property_id"], "check_in": b["check_in"], "check_out": b["check_out"], "rooms": 1, "rate_plan": b.get("rate_plan", "refundable"), "status": "confirmed", "total": round(h["price_per_night"] * n, 2), "currency": h["currency"]}
    state(x_aether_trial_id)["bookings"][bid] = rec
    return rec


@app.get("/tools/hotels/bookings/{booking_id}")
def get_booking(booking_id: str, x_aether_trial_id: str | None = Header(default=None)):
    rec = state(x_aether_trial_id)["bookings"].get(booking_id)
    if not rec:
        raise HTTPException(404, f"booking {booking_id} not found")
    return {**rec, "hotel": HOTELS[rec["property_id"]]["name"]}


@app.post("/tools/hotels/bookings/{booking_id}/modify")
async def modify_booking(booking_id: str, req: Request, x_aether_trial_id: str | None = Header(default=None)):
    b = await req.json()
    rec = state(x_aether_trial_id)["bookings"].get(booking_id)
    if not rec:
        raise HTTPException(404, f"booking {booking_id} not found")
    if rec["status"] == "cancelled":
        raise HTTPException(409, "booking is cancelled")
    for k in ("check_in", "check_out", "rooms", "rate_plan"):
        if b.get(k) is not None:
            rec[k] = b[k]
    h = HOTELS[rec["property_id"]]
    rec["total"] = round(h["price_per_night"] * nights(rec["check_in"], rec["check_out"]) * rec["rooms"] * (0.85 if rec["rate_plan"] == "non_refundable" else 1), 2)
    return {"status": "modified", **rec}


@app.post("/tools/hotels/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, x_aether_trial_id: str | None = Header(default=None)):
    rec = state(x_aether_trial_id)["bookings"].get(booking_id)
    if not rec:
        raise HTTPException(404, f"booking {booking_id} not found")
    rec["status"] = "cancelled"
    refund = rec["total"] if rec["rate_plan"] == "refundable" else 0.0
    return {"status": "cancelled", "booking_id": booking_id, "refund_amount": refund, "currency": rec["currency"]}


# ---------------------------------------------------------------- billing API
@app.post("/tools/billing/customers/lookup")
async def lookup_customer(req: Request):
    b = await req.json()
    c = CUSTOMERS.get(str(b.get("email", "")).strip().lower())
    if not c:
        raise HTTPException(404, "no customer with that email")
    return c


@app.get("/tools/billing/customers/{customer_id}/invoices")
def list_invoices(customer_id: str, x_aether_trial_id: str | None = Header(default=None)):
    inv = state(x_aether_trial_id)["invoices"].get(customer_id)
    if inv is None:
        raise HTTPException(404, "customer not found")
    return {"customer_id": customer_id, "invoices": inv}


@app.post("/tools/billing/refunds")
async def issue_refund(req: Request, x_aether_trial_id: str | None = Header(default=None)):
    b = await req.json()
    st = state(x_aether_trial_id)
    inv = next((i for lst in st["invoices"].values() for i in lst if i["invoice_id"] == b.get("invoice_id")), None)
    if not inv:
        raise HTTPException(404, "invoice not found")
    amount = float(b.get("amount", 0))
    if inv["status"] != "paid":
        raise HTTPException(409, f"invoice is {inv['status']}; only paid invoices can be refunded")
    if amount > 100:
        raise HTTPException(403, "refunds over 100.00 require escalation to a billing specialist")
    if amount > inv["amount"]:
        raise HTTPException(422, "refund exceeds invoice amount")
    rid = "R-" + uuid.uuid4().hex[:6].upper()
    st["refunds"].append({"refund_id": rid, **b})
    return {"refund_id": rid, "invoice_id": inv["invoice_id"], "amount": amount, "currency": inv["currency"], "status": "processing"}


@app.post("/tools/billing/tickets")
async def create_ticket(req: Request, x_aether_trial_id: str | None = Header(default=None)):
    b = await req.json()
    tid = "TCK-" + uuid.uuid4().hex[:6].upper()
    state(x_aether_trial_id)["tickets"].append({"ticket_id": tid, **b})
    return {"ticket_id": tid, "status": "open", "priority": b.get("priority")}


# ---------------------------------------------------------------- LLM helper
def _llm_config() -> dict[str, Any] | None:
    base = os.environ.get("SAMPLE_LLM_BASE_URL")
    model = os.environ.get("SAMPLE_LLM_MODEL")
    if not base or not model:
        return None
    return {
        "base": base.rstrip("/"),
        "model": model,
        "key": os.environ.get("SAMPLE_LLM_API_KEY", ""),
        "headers": json.loads(os.environ.get("SAMPLE_LLM_HEADERS") or "{}"),
        "options": json.loads(os.environ.get("SAMPLE_LLM_OPTIONS") or "{}"),
    }


_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        if os.environ.get("SAMPLE_CA_BUNDLE"):
            ctx.load_verify_locations(cafile=os.environ["SAMPLE_CA_BUNDLE"])
        _client = httpx.AsyncClient(timeout=120, verify=ctx)
    return _client


async def llm_chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> dict[str, Any]:
    cfg = _llm_config()
    assert cfg is not None
    body: dict[str, Any] = {"model": cfg["model"], "messages": messages}
    if tools:
        body["tools"] = tools
    if not cfg["options"].get("no_temperature"):
        body["temperature"] = 0.2
    headers = {"Content-Type": "application/json", **cfg["headers"]}
    if cfg["key"]:
        headers["Authorization"] = f"Bearer {cfg['key']}"
    for attempt in range(4):
        try:
            r = await client().post(f"{cfg['base']}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"sample agent could not reach its LLM at {cfg['base']} ({type(exc).__name__}: {exc}). Check SAMPLE_LLM_* and AETHER_CA_BUNDLE in .env, or leave SAMPLE_LLM_BASE_URL empty to use built-in rules.") from exc
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
            await asyncio.sleep(min(8, 2**attempt))
            continue
        if r.status_code >= 400:
            raise HTTPException(502, f"upstream LLM error {r.status_code}: {r.text[:300]}")
        return r.json()
    raise HTTPException(502, "upstream LLM unavailable")


# ---------------------------------------------------------------- Order Tracker (Aether agent protocol)
ORDER_PROMPT = (
    "You are Order Tracker, the delivery assistant for Parcelwise. Use tools to answer; never guess tracking details.\n"
    "Rules: 1) Always call track_parcel before answering about a parcel. 2) Delivered parcels cannot be rescheduled. "
    "3) Open a claim only for parcels with status 'exception', or delivered parcels the customer says they did not receive. "
    "4) Never share another recipient's details beyond what the tracking record shows."
)
ORDER_TOOLS = [
    {"type": "function", "function": {"name": "track_parcel", "description": "Get the status of a parcel by tracking id.", "parameters": {"type": "object", "properties": {"tracking_id": {"type": "string"}}, "required": ["tracking_id"]}}},
    {"type": "function", "function": {"name": "reschedule_delivery", "description": "Move delivery to a new date (YYYY-MM-DD) for parcels not yet delivered.", "parameters": {"type": "object", "properties": {"tracking_id": {"type": "string"}, "date": {"type": "string"}}, "required": ["tracking_id", "date"]}}},
    {"type": "function", "function": {"name": "open_claim", "description": "Open a claim for a damaged, lost or not-received parcel.", "parameters": {"type": "object", "properties": {"tracking_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["tracking_id", "reason"]}}},
]


def run_order_tool(st: dict[str, Any], name: str, args: dict[str, Any]) -> Any:
    p = st["parcels"].get(str(args.get("tracking_id", "")).upper())
    if name == "track_parcel":
        return p or {"error": "tracking id not found"}
    if name == "reschedule_delivery":
        if not p:
            return {"error": "tracking id not found"}
        if p["status"] == "delivered":
            return {"error": "parcel already delivered; cannot reschedule"}
        p["eta"] = args.get("date")
        return {"status": "rescheduled", "tracking_id": p["tracking_id"], "eta": p["eta"]}
    if name == "open_claim":
        if not p:
            return {"error": "tracking id not found"}
        cid = "CLM-" + uuid.uuid4().hex[:5].upper()
        st["claims"].append(cid)
        return {"claim_id": cid, "tracking_id": p["tracking_id"], "status": "submitted"}
    return {"error": f"unknown tool {name}"}


@app.post("/agents/order-tracker")
async def order_tracker(req: Request):
    body = await req.json()
    if body.get("protocol") != "aether.agent.v1":
        raise HTTPException(400, "expected protocol aether.agent.v1")
    st = state(body.get("session_id"))
    history = [{"role": m["role"], "content": m["content"]} for m in body.get("messages") or []]
    steps: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    if _llm_config() is None:
        import re

        text = history[-1]["content"] if history else ""
        ids = re.findall(r"TRK-\d{4}", text.upper())
        if not ids:
            return {"output": "Could you share your tracking number (it looks like TRK-1234)?", "steps": [], "usage": usage, "model": "rules"}
        res = run_order_tool(st, "track_parcel", {"tracking_id": ids[0]})
        steps.append({"type": "tool_call", "name": "track_parcel", "arguments": {"tracking_id": ids[0]}, "result": res, "latency_ms": 1})
        return {"output": f"Parcel {ids[0]}: {json.dumps(res)}", "steps": steps, "usage": usage, "model": "rules"}
    messages: list[dict[str, Any]] = [{"role": "system", "content": ORDER_PROMPT}, *history]
    for _ in range(6):
        data = await llm_chat(messages, ORDER_TOOLS)
        u = data.get("usage") or {}
        usage["input_tokens"] += u.get("prompt_tokens", 0)
        usage["output_tokens"] += u.get("completion_tokens", 0)
        msg = data["choices"][0]["message"]
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"output": msg.get("content") or "", "steps": steps, "usage": usage, "model": data.get("model")}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for c in calls:
            started = time.perf_counter()
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            res = run_order_tool(st, c["function"]["name"], args)
            steps.append({"type": "tool_call", "id": c["id"], "name": c["function"]["name"], "arguments": args, "result": res, "latency_ms": int((time.perf_counter() - started) * 1000)})
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(res)})
    return {"output": "I wasn't able to finish that request.", "steps": steps, "usage": usage, "model": "order-tracker"}


# ---------------------------------------------------------------- Kitchen Helper (OpenAI-compatible)
KITCHEN_PROMPT = (
    "You are Kitchen Helper, a friendly cooking assistant. Use convert_units for every unit conversion and "
    "get_nutrition for nutrition facts; never estimate numbers yourself. Keep answers short and practical."
)


@app.post("/kitchen/v1/chat/completions")
async def kitchen(req: Request):
    body = await req.json()
    msgs = [m for m in body.get("messages") or [] if m.get("role") != "system"]
    messages = [{"role": "system", "content": KITCHEN_PROMPT}, *msgs]
    tools = body.get("tools")
    if _llm_config() is None:
        last = messages[-1]
        if last["role"] == "tool":
            content = f"Here you go: {last['content']}"
            return {"id": "kh", "object": "chat.completion", "model": "kitchen-helper", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        call = {"id": "call_" + uuid.uuid4().hex[:8], "type": "function", "function": {"name": "convert_units", "arguments": json.dumps({"value": 1, "from_unit": "cup", "to_unit": "ml"})}}
        return {"id": "kh", "object": "chat.completion", "model": "kitchen-helper", "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [call]}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
    data = await llm_chat(messages, tools)
    data["model"] = "kitchen-helper"
    return data
