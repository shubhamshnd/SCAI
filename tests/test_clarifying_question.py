"""ask_clarifying_question asks about exactly the missing fields, and only those (design.md
Section 7: ambiguous/missing slots get a targeted question, never a generic one)."""

from scai.graph.nodes import ask_clarifying_question


def test_single_missing_field():
    state = ask_clarifying_question({"missing_fields": ["sku_code"]})
    assert "SKU code" in state["answer"]


def test_multiple_missing_fields_joined_with_and():
    state = ask_clarifying_question({"missing_fields": ["origin", "destination"]})
    assert "origin location" in state["answer"]
    assert "destination location" in state["answer"]
    assert " and " in state["answer"]


def test_decidable_field_advertises_the_deferral():
    """A buyer who doesn't know they can say "you decide" will answer literally — which is how
    a sourcing request became three rounds of the same question."""
    state = ask_clarifying_question({"missing_fields": ["origin"]})
    assert "you decide" in state["answer"]
    assert "find suppliers near you" in state["answer"]


def test_no_deferral_offer_for_facts_only_the_buyer_knows():
    state = ask_clarifying_question({"missing_fields": ["sku_code"]})
    assert "you decide" not in state["answer"]
