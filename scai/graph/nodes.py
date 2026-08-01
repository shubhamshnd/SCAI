"""Graph nodes per design.md Section 2:
parse_intent -> check_required_slots -> ask_clarifying_question | retrieve_and_reason
  -> select_tools -> call_tools -> synthesize_answer -> render_output
"""

from __future__ import annotations

import json
import logging
from datetime import date

import psycopg

from scai import db
from scai.config import Config
from scai.graph.state import AgentState
from scai.providers.base import Message
from scai.providers.router import ProviderRouter
from scai.tools import pricing, routing, suppliers, web_search

logger = logging.getLogger(__name__)

REQUIRED_FIELDS = ["sku_code", "origin", "destination", "mode"]

# Extracted and remembered across turns, but never gate the answer. Quantity in particular is
# what turns a per-tonne rate into a quotable total, so losing it between turns is what makes
# the agent drift back into generic ranges.
OPTIONAL_FIELDS = ["quantity", "delivery_deadline"]

_PARSE_INTENT_SYSTEM = """You extract structured fields from a supply chain procurement/logistics \
query. Given the user's message and any already-known slots, return ONLY a JSON object (no prose, \
no markdown fences) with these keys: sku_code, origin, destination, mode, quantity, \
delivery_deadline, defers_to_agent.

For sku_code/origin/destination/mode/quantity/delivery_deadline: use null for any field not \
mentioned or not confidently identifiable in THIS message. Never invent a value.

quantity is the consignment size with its unit exactly as the user expressed it — "20 tonnes", \
"500 kg", "2 x 20ft containers", "1500 pieces". delivery_deadline is any date or urgency the \
user states — "by 15 March", "within 10 days", "urgent".

Set defers_to_agent to true when the user is asking YOU to choose or recommend rather than \
supplying a value — e.g. "what would you suggest?", "you decide", "which is better?", \
"whatever is cheapest", "up to you". Otherwise false."""


def parse_intent(state: AgentState, router: ProviderRouter) -> AgentState:
    known_slots = state.get("slots", {})
    prompt = (
        f"Already-known slots: {json.dumps(known_slots)}\n"
        f"User message: {state['user_message']}"
    )
    response = router.generate(
        [Message(role="system", content=_PARSE_INTENT_SYSTEM), Message(role="user", content=prompt)],
        temperature=0.0,
    )
    try:
        text = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        extracted = json.loads(text)
    except (json.JSONDecodeError, AttributeError):
        logger.warning("parse_intent: model did not return valid JSON: %r", response.text)
        extracted = {}

    slots = dict(known_slots)
    for field in REQUIRED_FIELDS + OPTIONAL_FIELDS:
        value = extracted.get(field)
        if value:
            slots[field] = value

    return {**state, "slots": slots, "defers_to_agent": bool(extracted.get("defers_to_agent"))}


# Fields the agent can legitimately decide for the user by comparing options. The others name
# facts only the user knows (which material, which destination) — inventing those would be
# fabrication, so they stay hard clarify-gates no matter how the question is phrased.
#
# `origin` belongs here: sourcing is the procurement question, not a detail the buyer is
# withholding. "Which plants are near me?" used to re-ask for an origin on every turn because
# origin was a hard gate, so the one thing the agent should be best at was the one thing it
# refused to attempt. It is only decidable on deferral, and it resolves through the supplier
# radius search rather than being guessed.
AGENT_DECIDABLE_FIELDS = {"mode", "origin"}


def check_required_slots(state: AgentState) -> AgentState:
    slots = state.get("slots", {})
    missing = [f for f in REQUIRED_FIELDS if not slots.get(f)]

    if state.get("defers_to_agent") and missing and set(missing) <= AGENT_DECIDABLE_FIELDS:
        return {
            **state,
            "missing_fields": missing,
            "needs_clarification": False,
            "recommend_fields": missing,
        }

    return {**state, "missing_fields": missing, "needs_clarification": len(missing) > 0}


def route_after_slot_check(state: AgentState) -> str:
    return "ask_clarifying_question" if state.get("needs_clarification") else "retrieve_and_reason"


_FIELD_QUESTIONS = {
    # Asking for an "internal SKU code" turned away anyone who just wanted copper wire; the
    # lookup falls back to a name match (db.lookup_sku), so the question should too.
    "sku_code": "the material you need (a name like “copper wire rod” is fine, or a SKU code)",
    "origin": "the origin location — where you're sourcing from",
    "destination": "the destination location — your plant or delivery site",
    "mode": "the transport mode (road / rail / air / sea / multimodal)",
}

# What the agent offers to work out itself, per decidable field. Surfacing the offer matters:
# a buyer who does not know it can say "you suggest" will just answer the question literally,
# which is how a sourcing request turned into three rounds of the same clarification.
_DEFERRAL_OFFERS = {
    "origin": "find suppliers near you and pick one",
    "mode": "compare the modes and pick one",
}


def ask_clarifying_question(state: AgentState) -> AgentState:
    missing = state.get("missing_fields", [])
    parts = [_FIELD_QUESTIONS[f] for f in missing]
    if len(parts) == 1:
        question = f"Could you tell me {parts[0]}?"
    else:
        question = "Could you tell me " + ", ".join(parts[:-1]) + f" and {parts[-1]}?"

    offers = [_DEFERRAL_OFFERS[f] for f in missing if f in _DEFERRAL_OFFERS]
    if offers:
        joined = offers[0] if len(offers) == 1 else " and ".join(offers)
        question += f" Or say “you decide” and I'll {joined}."

    return {**state, "answer": question}


def retrieve_and_reason(state: AgentState, conn: psycopg.Connection, router: ProviderRouter, app_config: Config) -> AgentState:
    query_embedding = router.embed([state["user_message"]])[0]
    chunks = db.retrieve_chunks(
        conn,
        query_embedding,
        top_k=app_config.retrieval.top_k,
        similarity_threshold=app_config.retrieval.similarity_threshold,
    )
    return {**state, "retrieved_chunks": chunks}


# Commercial vocabulary, not just the word "price" — the earlier keyword set missed phrasings
# like "calculate along with freight charges", which is exactly when pricing data is needed most.
_PRICING_KEYWORDS = (
    "price", "cost", "rate", "cheap", "expensive", "freight", "charge", "quote", "quotation",
    "budget", "landed", "total", "estimate", "per ton", "per tonne", "per mt", "inr", "rupee",
    "₹", "rs.", "quotation", "tariff", "spend", "afford", "economical", "value",
)


_SUPPLIER_KEYWORDS = (
    "supplier", "suppliers", "vendor", "vendors", "plant", "plants", "mill", "mills",
    "manufacturer", "manufacturers", "producer", "producers", "source from", "sourcing",
    "nearby", "near me", "nearest", "around me", "procure", "who sells", "where can i buy",
)


def select_tools(state: AgentState) -> AgentState:
    text = state["user_message"].lower()
    slots = state.get("slots", {})
    wanted = []

    # A fully specified shipment is always a commercial question even when the user never says
    # "price" — the answer has to end in a landed cost either way, so gather the inputs.
    route_is_specified = all(slots.get(f) for f in ("sku_code", "origin", "destination"))
    if any(kw in text for kw in _PRICING_KEYWORDS) or state.get("recommend_fields") or route_is_specified:
        wanted.append("pricing")
    # Any query with a known origin+destination is a movement question, whether or not the user
    # used the word "distance" — the route data is what makes the answer concrete.
    if slots.get("origin") and slots.get("destination"):
        wanted.append("routing")

    # Sourcing: either the agent has been handed the origin decision, or the user is explicitly
    # hunting for vendors ("check plants nearby to me").
    if "origin" in state.get("recommend_fields", []) or any(
        kw in text for kw in _SUPPLIER_KEYWORDS
    ):
        wanted.append("supplier_search")
    # Regulatory/news questions change faster than any ingested corpus, so they go to the live web.
    if any(kw in text for kw in ("regulation", "rule", "gst", "e-way", "compliance", "ban", "duty", "tariff", "news", "latest")):
        wanted.append("web_search")

    return {**state, "tool_results": {"_selected": wanted}}


def call_tools(state: AgentState, conn: psycopg.Connection) -> AgentState:
    selected = state.get("tool_results", {}).get("_selected", [])
    results: dict = {}
    slots = state.get("slots", {})

    if "pricing" in selected:
        sku = db.lookup_sku(conn, slots.get("sku_code", ""))
        category = sku["material_category"] if sku else "default"
        material_name = sku["material_name"] if sku else None
        if sku:
            results["sku"] = sku  # HSN code drives the GST rate in the landed-cost build-up.
        try:
            results["pricing"] = pricing.get_price(
                category, slots.get("sku_code", ""), material_name=material_name
            )
        except pricing.ToolUnavailableError as exc:
            results["pricing"] = {"error": str(exc)}

        # Freight is usually the larger half of the delta between two options, so quote it from
        # market data rather than leaving the model to recall a per-tonne number.
        if slots.get("origin") and slots.get("destination"):
            modes = (
                ["road", "rail"]
                if "mode" in state.get("recommend_fields", [])
                else [slots.get("mode", "road")]
            )
            try:
                results["freight_rates"] = pricing.get_freight_rate(
                    slots["origin"], slots["destination"], modes, material_name=material_name
                )
            except pricing.ToolUnavailableError as exc:
                results["freight_rates"] = {"error": str(exc)}

    if "supplier_search" in selected:
        material = slots.get("sku_code", "")
        sku_row = db.lookup_sku(conn, material) if material else None
        # Search on the catalogue name when the user gave a code, since materials_supplied is
        # written in plain words ("copper wire rod"), not in SKU codes.
        search_term = sku_row["material_name"] if sku_row else material
        near_place = slots.get("destination") or slots.get("origin") or ""

        lat, lon = slots.get("destination_lat"), slots.get("destination_lon")
        if lat is None or lon is None:
            # No pin dropped — fall back to geocoding the destination so the radius search still
            # has a centre. A district name resolves to its centroid, which is coarse; the map
            # picker exists precisely to avoid that.
            try:
                lon, lat = routing.geocode(near_place)
            except Exception as exc:  # noqa: BLE001 - web discovery can still carry the answer
                logger.info("supplier_search: could not geocode %r: %s", near_place, exc)
                lat = lon = None

        try:
            results["supplier_search"] = suppliers.find_suppliers(
                conn,
                material=search_term,
                near_place=near_place,
                lat=lat,
                lon=lon,
                radius_km=float(slots.get("sourcing_radius_km") or suppliers.DEFAULT_RADIUS_KM),
            )
        except suppliers.ToolUnavailableError as exc:
            results["supplier_search"] = {"error": str(exc)}

    if "web_search" in selected:
        try:
            results["web_search"] = web_search.search(state["user_message"], max_results=4)
        except web_search.ToolUnavailableError as exc:
            results["web_search"] = {"error": str(exc)}

    if "routing" in selected:
        origin, destination = slots.get("origin", ""), slots.get("destination", "")
        # When the agent is choosing the mode, gather every mode's numbers so the recommendation
        # is a comparison rather than a guess.
        modes = ["road", "rail", "air", "sea"] if "mode" in state.get("recommend_fields", []) else [slots.get("mode", "")]
        comparison = {}
        for mode in modes:
            try:
                comparison[mode] = routing.get_route(origin, destination, mode)
            except routing.ToolUnavailableError as exc:
                comparison[mode] = {"error": str(exc)}
            except Exception as exc:  # noqa: BLE001 - a failed lookup shouldn't kill the whole answer
                comparison[mode] = {"error": f"lookup failed: {exc}"}

        benchmarks = db.lookup_benchmarks(conn, origin, destination)
        results["routing"] = comparison
        if benchmarks:
            results["transport_benchmarks"] = benchmarks

    return {**state, "tool_results": results}


_SYNTHESIZE_SYSTEM = """You are a senior commercial manager on an Indian supply chain desk. You \
source raw materials, you move them, and above all you quote and defend a landed price. Your \
users are procurement managers and logistics planners who need a number they can put into a \
purchase requisition and a date they can promise a site foreman.

Two things are non-negotiable in every answer about buying or moving something: it ends with a \
committed total price, and it ends with committed calendar dates. An answer that stops at "it \
depends", or at a per-tonne range with no total, or at a disclaimer about missing data, has \
failed — no matter how well reasoned the rest of it is.

## 1. Grounding: label confidence, never withhold a number
Tag every figure inline with exactly one of:
- [sourced] — from the retrieved context or a tool result. Name the source.
- [benchmark] — a prevailing Indian market rate you know from practice, not looked up this \
session. Say what it is typical for.
- [assumed] — your own working assumption. State it plainly so the user can override it.

Never present an invented figure as [sourced]. Equally, never let a missing source stop you from \
producing a number. An estimate that is labelled, decomposed and shown with its arithmetic is \
precisely what a commercial desk exists to produce; refusing to quote is not caution, it is \
non-delivery. Do not open with a data-availability notice — attach the caveat to the specific \
line item it affects and keep going.

## 2. Estimate internally by decomposition
When nothing quotes the total, build it. Never reason from a single remembered per-tonne number.
- Break the shipment into cost drivers and put a rate against each one.
- Derive rates from the primitives you do have: distance from the routing tool, per-tonne-km \
benchmarks, truck payload and per-trip rates, diesel and toll cost per km, wagon or container \
capacity, labour per tonne handled, duty and tax percentages.
- Show the arithmetic in plain text so the user can audit and re-run it: \
"2121 km x ₹2.30/tonne-km = ₹4,878/MT; x 20 MT = ₹97,560".
- Cross-check the result two ways where you can (per-tonne-km against corridor benchmarks, \
per-truck against a full-truckload rate) and reconcile any gap.
- Give each significant line a low/likely/high value, then carry all three to the total so the \
final number has a real range around a committed point estimate.

## 3. The cost sheet
Work through this checklist and include every line that applies. Say "not applicable" and why \
for any you drop — silence reads as an oversight and understates the quote.
- Ex-works / ex-mill material cost: unit rate x quantity, plus grade, brand and size premiums.
- Packing, bundling, strapping, tarpaulin.
- Loading at origin: labour, hamali, crane or forklift.
- First-mile drayage to siding, ICD, CFS or port, when the main leg is not door-to-door.
- Main-leg freight, priced in the unit the mode actually bills in — per truck, per wagon, per \
rake, per TEU, per kg chargeable weight.
- Terminal handling, siding, wharfage, CFS and documentation charges.
- Last-mile delivery to site, and unloading.
- Transit insurance, typically 0.05-0.15% of consignment value.
- Shortage, wastage and damage allowance appropriate to the material.
- Taxes: GST on goods at the HSN rate, and GST on freight (GTA at 5% without ITC or 12% with, \
rail and other modes at their own rates). State the cash outflow including GST and the net cost \
after input tax credit separately — they are different numbers and procurement cares about both.
- E-way bill, weighbridge, permits, octroi or entry tax where it survives.
- Detention and demurrage exposure: the daily rate and the realistic risk, as a named \
contingency rather than buried in the total.
- Cost of the payment terms: advance, credit period, LC charges, at a stated cost of capital.

## 4. Fit the plan to the actual quantity
Before recommending anything, check the shipment unit against the consignment size. Do not \
propose a full rake for a truckload, or part-load LTL for 500 tonnes, or air freight for bulk \
construction material. If the quantity sits awkwardly between units, say so and price both sides \
of the break-even. If quantity is unknown, assume a standard commercial lot, label it [assumed], \
quote it, and say how the total scales.

## 5. Sourcing: choose the supplier, do not hand back a directory
Finding the origin is your job, not the buyer's. When the supplier search returns candidates, \
shortlist three to five and commit to one.
- Rank on delivered cost, not proximity. The nearest plant is not automatically the cheapest \
once ex-works price, minimum lot size and freight are in. Show the delivered cost per unit for \
each shortlisted supplier so the ranking is visible.
- A price *difference between named suppliers* is [assumed] unless you actually have a quote for \
each. One market rate is a [benchmark]; spreading it into per-supplier numbers is your own \
modelling, and labelling that [benchmark] overstates what you know. Say what the spread is \
based on — producer versus converter, direct versus dealer, plant premium — and note that an \
RFQ is what settles it.
- Price freight on `road_distance_km` when it is present. The straight-line `distance_km` is a \
search filter and understates a real haul — never quote freight off it.
- Distinguish provenance. Seeded suppliers come from internal reference data: usable, but tell \
the user to verify before contracting. Web-discovered leads are unverified, have no \
coordinates, and must be labelled as leads to qualify rather than quoted as if confirmed.
- Say what would change the pick — a minimum order quantity, a certification the site needs, a \
plant with no rail siding, a supplier already on the buyer's approved vendor list.
- Name the next action per supplier: who to call for an RFQ and what to ask for.
If nothing is found in range, widen the radius and say you did; do not report an empty district \
as though no supply exists.

## 6. Get the best price
You are the user's negotiator, not a rate calculator. Include:
- Where the fat is: which one or two lines actually move the total, and what each is worth.
- Concrete levers: backhaul and return-load discounts, mini-rake versus FTL versus part load, \
direct-from-plant versus dealer, freight-inclusive versus ex-works, consolidating shipments, \
off-peak or seasonal timing, GST-efficient place of supply, annual-volume rate contracts.
- A target price to push for and the walk-away, not just the market rate.
- Rate validity: say how long the quote holds and what would break it.

## 7. Delivery dates
Use the current date supplied in the user block and give real calendar dates, never only \
durations. Break the schedule into stages — order confirmation and material lead time, dispatch, \
main-leg transit, terminal dwell, last-mile, unloading — with a date on each. Give earliest, \
expected and latest arrival. State whether you are counting working or calendar days, and call \
out anything that routinely slips: rake indent waiting, festival or monsoon effects, weekend \
loading bans, checkpost delays.

## 8. Sanity checks before answering
- Does the arithmetic foot? Re-add the lines against the total.
- Is freight a sane share of material value for this commodity? Flag it if not.
- Is every number in one consistent unit and currency?
- Have you actually stated a total and a date? If not, the answer is not finished.

## 9. Structure
Lead with the answer — the recommendation and the headline number — then the supporting detail. \
Be concrete and dense; no hedging preambles, no restating the question. Close every commercial \
answer with a section titled "Bottom line" containing: total landed cost for the exact quantity, \
cost per unit, cash outflow including GST versus net of ITC, the low-to-high range, the expected \
delivery date, and the single most valuable next action.

For a pure information question with no shipment in play — a regulation, a definition — just \
answer it well; the pricing and dating discipline applies whenever a purchase or movement is \
actually on the table.

## 10. Formatting
Reply in GitHub-flavored Markdown — headings, bold, lists, and tables for comparisons and cost \
sheets. Write arithmetic in plain text ("2121 / 200 = 11 days"), never LaTeX or math notation \
such as \\lceil or $...$, which the chat UI cannot render.

You can render inline charts. Emit a fenced code block tagged `scaichart` containing only a JSON \
object. Use them where a picture genuinely beats a table — typically a cost breakdown, an option \
comparison, and a delivery timeline. Two or three per answer is right; do not chart trivia, and \
always keep the numbers in the prose too.

```scaichart
{"type": "breakdown", "title": "Landed cost - 20 MT, Chennai to Delhi", "unit": "INR",
 "items": [{"label": "Ex-works material", "value": 940000, "tag": "sourced"},
           {"label": "Main-leg freight", "value": 97560, "tag": "benchmark"},
           {"label": "Handling + last mile", "value": 24000, "tag": "assumed"}]}
```

Types and their shapes, all using an `items` array:
- `breakdown` — cost components as a share of the total. Do not include a total item; it is \
computed and displayed for you.
- `bar` — comparing options on one measure. Set `"highlight": true` on the option you recommend.
- `timeline` — dated milestones. Each item needs an ISO `"date": "YYYY-MM-DD"`.
- `map` — supplier and site locations on a real map. Each item needs `"lat"` and `"lon"`. Use \
this whenever you present a shortlist of suppliers: it is the clearest way to show who is where \
relative to the delivery site. Mark the buyer's plant or site with `"role": "destination"` and \
each vendor with `"role": "supplier"`; set `"highlight": true` on the one you recommend. Give \
each supplier a `"distance_km"` (use the road distance) and, where you have it, \
`"duration_hours"` — both are listed beside the map and shown on the pin.

Every item takes `label` and, per type, `value`, `date`, or `lat`/`lon`. Optional on any item: \
`note` for a short caption, and `tag` set to "sourced", "benchmark" or "assumed" to carry your \
confidence labelling into the chart. Give the chart a `title` and, for value charts, a `unit`. \
Only plot a supplier on a `map` if you actually have its coordinates — web-discovered leads \
have none, so list those in prose instead of inventing a position. The JSON must be valid and \
self-contained — no comments, no trailing commas, no text outside the braces.

```scaichart
{"type": "map", "title": "Copper wire rod suppliers near Dolvi",
 "items": [{"label": "Dolvi plant (delivery site)", "lat": 18.65, "lon": 73.05, "role": "destination"},
           {"label": "Hindustan Copper - Taloja", "lat": 19.08, "lon": 73.10, "role": "supplier",
            "distance_km": 65.6, "duration_hours": 1.0, "highlight": true, "tag": "sourced"},
           {"label": "Finolex Cables - Urse", "lat": 18.72, "lon": 73.62, "role": "supplier",
            "distance_km": 95.0, "duration_hours": 1.5, "tag": "sourced"}]}
```"""

_RECOMMEND_INSTRUCTION = """The user has asked YOU to choose the following: {fields}. Do not ask \
them to pick — compare the options on landed cost, transit time and reliability using the tool \
results and your domain knowledge, then commit to one and say why. Price the recommended option \
in full and show what the runner-up would have cost, so the recommendation is defensible as \
money, not just as reasoning."""


def build_synthesis_messages(state: AgentState) -> list[Message]:
    """Shared by the graph's synthesize_answer node and the streaming API endpoint, so both
    paths ground the answer in identical context (design.md Section 5's normalization goal
    applies here too: one prompt-construction path, regardless of how the caller consumes it)."""
    context_block = "\n\n".join(
        f"[{c['source_type']}] {c['title']}: {c['chunk_text']}" for c in state.get("retrieved_chunks", [])
    ) or "(no retrieved chunks cleared the similarity threshold)"
    tools_block = json.dumps(state.get("tool_results", {}), default=str) or "{}"

    system = _SYNTHESIZE_SYSTEM
    recommend_fields = state.get("recommend_fields", [])
    if recommend_fields:
        system += "\n\n" + _RECOMMEND_INSTRUCTION.format(fields=", ".join(recommend_fields))

    slots = state.get("slots", {})
    # Delivery dates are only answerable if the model knows what day it is; without this it can
    # produce durations but never a calendar date.
    today = date.today()
    quantity_note = (
        "" if slots.get("quantity")
        else "\nQuantity was not stated — assume a standard commercial lot, label the assumption, "
        "quote a total for it anyway, and say how the total scales with volume."
    )

    geo_note = ""
    if slots.get("destination_lat") is not None and slots.get("destination_lon") is not None:
        geo_note = (
            f"\nDelivery site pinned on the map at {slots['destination_lat']}, "
            f"{slots['destination_lon']} with a sourcing radius of "
            f"{slots.get('sourcing_radius_km', 300)} km — these coordinates are exact, so prefer "
            "them over any place name when reasoning about location."
        )

    prompt = (
        f"Today's date: {today:%A, %d %B %Y} (ISO {today.isoformat()})\n"
        f"Known slots: {json.dumps(slots)}{quantity_note}{geo_note}\n\n"
        f"Retrieved context:\n{context_block}\n\n"
        f"Tool results:\n{tools_block}\n\n"
        f"User question: {state['user_message']}"
    )
    return [Message(role="system", content=system), Message(role="user", content=prompt)]


def synthesize_answer(state: AgentState, router: ProviderRouter) -> AgentState:
    response = router.generate(build_synthesis_messages(state))
    return {**state, "answer": response.text}


def render_output(state: AgentState) -> AgentState:
    return state
