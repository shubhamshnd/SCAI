"""Sourcing behaviour: the agent must be able to find the origin rather than demand it.

The failure these pin down: asked to "check plants nearby to me", the agent re-asked for an
origin on every turn, because origin was a hard clarify gate. Sourcing is the procurement
question — refusing to attempt it made the agent useless for the thing it should be best at.
"""

import pytest

from scai.graph.nodes import (
    AGENT_DECIDABLE_FIELDS,
    check_required_slots,
    route_after_slot_check,
    select_tools,
)

PLANT_SLOTS = {"sku_code": "copper wire rod", "destination": "Dolvi", "mode": "road"}


# === The loop ================================================================

def test_deferring_the_origin_does_not_re_ask():
    """'check plants nearby to me' must route to an answer, not back to the same question."""
    state = check_required_slots({"slots": PLANT_SLOTS, "defers_to_agent": True})
    assert state["needs_clarification"] is False
    assert state["recommend_fields"] == ["origin"]
    assert route_after_slot_check(state) == "retrieve_and_reason"


def test_origin_and_mode_can_both_be_deferred_together():
    slots = {"sku_code": "copper wire rod", "destination": "Dolvi"}
    state = check_required_slots({"slots": slots, "defers_to_agent": True})
    assert state["needs_clarification"] is False
    assert set(state["recommend_fields"]) == {"origin", "mode"}


def test_origin_is_still_gated_without_a_deferral():
    """Only deferral unlocks it — a plain question with a missing origin still asks."""
    state = check_required_slots({"slots": PLANT_SLOTS, "defers_to_agent": False})
    assert state["needs_clarification"] is True
    assert state["missing_fields"] == ["origin"]


@pytest.mark.parametrize("field", ["sku_code", "destination"])
def test_facts_only_the_buyer_knows_stay_hard_gates(field):
    """Deferring cannot license inventing the material or the delivery site."""
    assert field not in AGENT_DECIDABLE_FIELDS
    slots = {k: v for k, v in PLANT_SLOTS.items() if k != field}
    slots["origin"] = "Taloja"
    state = check_required_slots({"slots": slots, "defers_to_agent": True})
    assert state["needs_clarification"] is True
    assert field in state["missing_fields"]


# === Tool selection ==========================================================

def test_vendor_hunting_language_triggers_supplier_search():
    state = select_tools({"user_message": "check plants nearby to me", "slots": PLANT_SLOTS})
    assert "supplier_search" in state["tool_results"]["_selected"]


def test_recommending_the_origin_triggers_supplier_search():
    state = select_tools(
        {"user_message": "you decide", "slots": PLANT_SLOTS, "recommend_fields": ["origin"]}
    )
    assert "supplier_search" in state["tool_results"]["_selected"]


def test_plain_movement_question_does_not_run_supplier_search():
    slots = {**PLANT_SLOTS, "origin": "Taloja"}
    state = select_tools({"user_message": "how long will it take", "slots": slots})
    assert "supplier_search" not in state["tool_results"]["_selected"]
