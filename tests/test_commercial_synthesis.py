"""Regression tests for the commercial-quoting behaviour (design.md Section 4/7).

These pin the inputs the agent needs in order to end an answer with a real total and a real
delivery date. Each case here maps to an observed failure: a freight question that never reached
the pricing tool, a consignment size lost between turns, and dates that could not be computed
because the prompt never carried the current date.
"""

from datetime import date

from scai.graph.nodes import build_synthesis_messages, parse_intent, select_tools
from scai.providers.base import GenerateResponse

FULL_SLOTS = {
    "sku_code": "STL-1042",
    "origin": "Chennai",
    "destination": "Delhi",
    "mode": "rail",
    "quantity": "20 tonnes",
}


class StubRouter:
    """Stands in for ProviderRouter — parse_intent only needs generate() to return JSON text."""

    def __init__(self, payload: str):
        self._payload = payload

    def generate(self, messages, **kwargs) -> GenerateResponse:
        return GenerateResponse(text=self._payload, provider="stub", model="stub")


# === Tool selection ==========================================================

def test_freight_phrasing_reaches_the_pricing_tool():
    """'calculate along with freight charges' matched none of the original keywords, so the
    costing question was answered with no pricing data at all."""
    state = select_tools({"user_message": "calculate along with freight charges", "slots": FULL_SLOTS})
    assert "pricing" in state["tool_results"]["_selected"]


def test_fully_specified_shipment_is_priced_without_a_price_keyword():
    state = select_tools({"user_message": "when will it arrive", "slots": FULL_SLOTS})
    assert "pricing" in state["tool_results"]["_selected"]


def test_bare_regulatory_question_does_not_trigger_pricing():
    """The pricing discipline shouldn't drag a cost lookup into a pure information question."""
    state = select_tools({"user_message": "what is an e-way bill", "slots": {}})
    assert state["tool_results"]["_selected"] == ["web_search"]


# === Slot carry-over =========================================================

def test_quantity_is_extracted_into_slots():
    router = StubRouter(
        '{"sku_code": null, "origin": "Chennai", "destination": "Delhi", "mode": null, '
        '"quantity": "20 tonnes", "delivery_deadline": null, "defers_to_agent": false}'
    )
    state = parse_intent({"user_message": "Move 20 tons from Chennai to Delhi", "slots": {}}, router)
    assert state["slots"]["quantity"] == "20 tonnes"


def test_quantity_survives_a_follow_up_that_does_not_repeat_it():
    """The consignment size is what turns a per-tonne rate into a quotable total; losing it on
    turn three is what produced rake-sized advice for a truckload order."""
    router = StubRouter(
        '{"sku_code": null, "origin": null, "destination": null, "mode": "rail", '
        '"quantity": null, "delivery_deadline": null, "defers_to_agent": false}'
    )
    state = parse_intent(
        {"user_message": "calculate along with freight charges", "slots": dict(FULL_SLOTS)}, router
    )
    assert state["slots"]["quantity"] == "20 tonnes"


def test_quantity_does_not_become_a_clarify_gate():
    from scai.graph.nodes import check_required_slots

    slots = {k: v for k, v in FULL_SLOTS.items() if k != "quantity"}
    assert check_required_slots({"slots": slots})["needs_clarification"] is False


# === Synthesis prompt ========================================================

def test_prompt_carries_todays_date_so_delivery_dates_are_computable():
    messages = build_synthesis_messages({"user_message": "when does it land?", "slots": FULL_SLOTS})
    assert date.today().isoformat() in messages[1].content


def test_missing_quantity_instructs_an_assumption_rather_than_a_question():
    slots = {k: v for k, v in FULL_SLOTS.items() if k != "quantity"}
    messages = build_synthesis_messages({"user_message": "what will it cost?", "slots": slots})
    assert "Quantity was not stated" in messages[1].content


def test_known_quantity_adds_no_assumption_instruction():
    messages = build_synthesis_messages({"user_message": "what will it cost?", "slots": FULL_SLOTS})
    assert "Quantity was not stated" not in messages[1].content


def test_system_prompt_demands_a_total_and_a_date():
    system = build_synthesis_messages({"user_message": "cost?", "slots": FULL_SLOTS})[0].content
    assert "Bottom line" in system
    assert "scaichart" in system
