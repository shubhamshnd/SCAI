"""Table-driven tests for check_required_slots (design.md Section 8)."""

from scai.graph.nodes import check_required_slots, route_after_slot_check

CASES = [
    ({}, ["sku_code", "origin", "destination", "mode"]),
    ({"sku_code": "SKU-1"}, ["origin", "destination", "mode"]),
    ({"sku_code": "SKU-1", "origin": "Chennai"}, ["destination", "mode"]),
    ({"sku_code": "SKU-1", "origin": "Chennai", "destination": "Delhi"}, ["mode"]),
    ({"sku_code": "SKU-1", "origin": "Chennai", "destination": "Delhi", "mode": "road"}, []),
]


def test_check_required_slots_table_driven():
    for slots, expected_missing in CASES:
        state = check_required_slots({"slots": slots})
        assert state["missing_fields"] == expected_missing
        assert state["needs_clarification"] == (len(expected_missing) > 0)


def test_route_after_slot_check_goes_to_clarify_when_missing():
    state = {"needs_clarification": True}
    assert route_after_slot_check(state) == "ask_clarifying_question"


def test_route_after_slot_check_goes_to_retrieval_when_complete():
    state = {"needs_clarification": False}
    assert route_after_slot_check(state) == "retrieve_and_reason"


ROUTE_SLOTS = {"sku_code": "STL-1042", "origin": "Chennai", "destination": "Delhi"}


def test_deferral_on_mode_recommends_instead_of_reasking():
    """'what would you suggest?' must not loop back the same question."""
    state = check_required_slots({"slots": ROUTE_SLOTS, "defers_to_agent": True})
    assert state["needs_clarification"] is False
    assert state["recommend_fields"] == ["mode"]
    assert route_after_slot_check(state) == "retrieve_and_reason"


def test_deferral_still_clarifies_facts_the_agent_cannot_know():
    """Mode is decidable; a SKU code is not — deferring can't license inventing one."""
    state = check_required_slots(
        {"slots": {"origin": "Chennai", "destination": "Delhi"}, "defers_to_agent": True}
    )
    assert state["needs_clarification"] is True
    assert "sku_code" in state["missing_fields"]


def test_no_deferral_keeps_the_normal_clarify_gate():
    state = check_required_slots({"slots": ROUTE_SLOTS, "defers_to_agent": False})
    assert state["needs_clarification"] is True
