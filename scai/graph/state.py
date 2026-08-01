"""Agent state — the state graph's structural enforcement of the clarify-before-answer rule
(design.md Section 1/2)."""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID


class Slots(TypedDict, total=False):
    sku_code: str
    origin: str
    destination: str
    mode: str
    # Optional — never gates the answer, but without it the agent cannot quote a total for the
    # actual consignment or size the shipment unit correctly (a 20 MT order is a truckload, not
    # a rake). Carried across turns like the required slots so it survives follow-up questions.
    quantity: str
    delivery_deadline: str
    # Set by the map picker (web/components/LocationPicker.tsx), not by intent parsing: a pinned
    # point and a sourcing radius. "Raigad district" is a 7,000 km2 polygon, so free text alone
    # cannot anchor a radius search — these make "which plants are near me" answerable.
    destination_lat: float
    destination_lon: float
    sourcing_radius_km: float


class AgentState(TypedDict, total=False):
    session_id: UUID
    user_message: str
    slots: Slots
    missing_fields: list[str]
    ambiguous_fields: dict[str, str]
    retrieved_chunks: list[dict[str, Any]]
    tool_results: dict[str, Any]
    answer: str
    needs_clarification: bool
    defers_to_agent: bool
    recommend_fields: list[str]
