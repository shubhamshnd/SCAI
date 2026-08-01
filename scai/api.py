"""FastAPI backend for the web UI — wraps the existing scai/ package (config, db, providers,
graph) behind HTTP endpoints. The final answer streams over SSE so the frontend's streaming
message bubble (design.md Section 10.3) renders token-by-token instead of waiting on a full
response.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from scai import db
from scai.config import load_config
from scai.graph import nodes
from scai.providers.router import ProviderRouter
from scai.tools import routing

logger = logging.getLogger(__name__)

config = load_config()
router = ProviderRouter(config)

app = FastAPI(title="Supply Chain Agent API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSessionRequest(BaseModel):
    user_id: str = "web-user"


class NewMessageRequest(BaseModel):
    content: str
    # Optional map-picker payload. Merged into the session's slots so it persists across turns
    # exactly like a typed location would — pin once, and every later question is anchored.
    destination_lat: float | None = None
    destination_lon: float | None = None
    sourcing_radius_km: float | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@app.get("/api/geocode")
def geocode_place(q: str):
    """Place-name lookup for the map picker's search box.

    Proxied rather than called from the browser so the request carries the app's identifying
    User-Agent, which Nominatim's usage policy requires.
    """
    try:
        lon, lat = routing.geocode(q)
    except routing.ToolUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - upstream failure, not a client error
        raise HTTPException(status_code=502, detail=f"Geocoding failed: {exc}") from exc
    return {"query": q, "lat": lat, "lon": lon}


@app.post("/api/sessions")
def create_session(body: CreateSessionRequest):
    with db.connect(config) as conn:
        session_id = db.create_session(conn, body.user_id)
        return {"id": session_id, "user_id": body.user_id}


@app.get("/api/sessions")
def get_sessions():
    with db.connect(config) as conn:
        return db.list_sessions(conn)


@app.get("/api/sessions/{session_id}/messages")
def get_messages(session_id: UUID):
    with db.connect(config) as conn:
        return db.list_messages(conn, session_id)


@app.delete("/api/sessions/{session_id}", status_code=204)
def delete_session(session_id: UUID):
    with db.connect(config) as conn:
        if not db.delete_session(conn, session_id):
            raise HTTPException(status_code=404, detail="Unknown session_id")
    return Response(status_code=204)


_TOOL_STATUS_LABELS = {
    "pricing": "CALLING · price api",
    "routing": "ROUTING · osrm",
    "supplier_search": "SOURCING · supplier radius",
    "web_search": "SEARCHING · web",
}


def _supplier_pins(tool_results: dict) -> list[dict]:
    """Seeded suppliers flattened for the map panel. Only rows with coordinates: web-discovered
    leads have no position and would otherwise be dropped on the map at a guessed point."""
    search = tool_results.get("supplier_search") or {}
    results = (search.get("seeded_suppliers") or {}).get("results", [])
    return [
        {
            "name": s["name"],
            "city": s.get("city"),
            "state": s.get("state"),
            "lat": s["lat"],
            "lon": s["lon"],
            "distance_km": s.get("road_distance_km") or s.get("distance_km"),
            "duration_hours": s.get("road_duration_hours"),
        }
        for s in results
        if s.get("lat") is not None and s.get("lon") is not None
    ]


@app.post("/api/sessions/{session_id}/messages")
def post_message(session_id: UUID, body: NewMessageRequest):
    def stream() -> Generator[str, None, None]:
        with db.connect(config) as conn:
            try:
                slots = db.latest_slots(conn, session_id)
                for field in ("destination_lat", "destination_lon", "sourcing_radius_km"):
                    value = getattr(body, field)
                    if value is not None:
                        slots[field] = value
                db.append_message(conn, session_id, "user", body.content, slots)

                state = {"session_id": session_id, "user_message": body.content, "slots": slots}
                state = nodes.parse_intent(state, router)
                state = nodes.check_required_slots(state)

                if state["needs_clarification"]:
                    state = nodes.ask_clarifying_question(state)
                    answer = state["answer"]
                    yield _sse("token", {"text": answer})
                    db.append_message(conn, session_id, "assistant", answer, state["slots"])
                    yield _sse(
                        "done",
                        {"slots": state["slots"], "missing_fields": state["missing_fields"], "tool_results": {}},
                    )
                    return

                yield _sse("status", {"label": "RETRIEVING · knowledge base"})
                state = nodes.retrieve_and_reason(state, conn, router, config)
                yield _sse("status", {"label": f"RETRIEVED · {len(state.get('retrieved_chunks', []))} chunks"})

                state = nodes.select_tools(state)
                for tool_name in state["tool_results"].get("_selected", []):
                    yield _sse("status", {"label": _TOOL_STATUS_LABELS.get(tool_name, tool_name.upper())})
                state = nodes.call_tools(state, conn)

                messages = nodes.build_synthesis_messages(state)
                full_answer = ""
                for chunk in router.generate_stream(messages):
                    full_answer += chunk
                    yield _sse("token", {"text": chunk})

                db.append_message(conn, session_id, "assistant", full_answer, state["slots"])
                yield _sse(
                    "done",
                    {
                        "slots": state["slots"],
                        "missing_fields": [],
                        "tool_results": {k: v for k, v in state["tool_results"].items() if k != "_selected"},
                        "supplier_pins": _supplier_pins(state["tool_results"]),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - surfaced to the client, never a silent hang
                logger.exception("Error handling message for session %s", session_id)
                yield _sse("error", {"message": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
